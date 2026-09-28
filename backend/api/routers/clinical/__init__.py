"""Clinical router package -- the eye-test queue, exams, SOAP notes, the Rx
print card and redos, the handover to the floor, abuse detection, lens-power
combos and the manufacturability check.

Wave 6 split of the 3,158-line api/routers/clinical.py. The API is byte-
identical: every sub-module registers on the SAME APIRouter object (built in
_shared.py) and the ROUTE import block below runs in the original file's
order, so the route table is unchanged path-for-path and position-for-
position. FastAPI is first-registered-wins on a path clash, so a route
sub-module must never move above one that came before it in the flat file.

Everything the single-file module exposed is re-exported here, so
``from api.routers.clinical import X`` keeps working for every X.
"""

import sys
import types

# Helper sub-modules register NO routes, so their position here is free; they
# are listed in the original file's order.
from . import _shared
from . import models
from . import helpers

# ROUTE REGISTRATION ORDER == the original file's order. DO NOT REORDER.
from . import eye_queue
from . import eye_tests
from . import handover
from . import patient_history
from . import stats
from . import soap
from . import rx_print
from . import abuse
from . import lens_combos
from . import manufacturability

# Re-exports: the module-level surface the single file used to have.
from ._shared import (  # noqa: F401
    APIRouter,
    HTTPException,
    Depends,
    Query,
    Path,
    HTMLResponse,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    List,
    Optional,
    datetime,
    date,
    timezone,
    ist_date_str,
    ist_today,
    _html_escape,
    uuid,
    get_current_user,
    require_roles,
    require_rx_read,
    get_db,
    get_eye_test_queue_repository,
    get_eye_test_repository,
    get_order_repository,
    get_prescription_repository,
    get_customer_repository,
    get_store_repository,
    get_audit_repository,
    get_handoff_repository,
    get_user_repository,
    validate_store_access,
    can_access_store_scoped,
    user_store_scope,
    _abuse,
    _conversion,
    first_present_rx_value,
    is_absent_rx_value,
    horizon_start_iso_date,
    later_iso_bound,
    router,
    _audit_clinical,
    _CLINICAL_ROLES,
    _QUEUE_ADD_ROLES,
    _COMBO_READ_ROLES,
    _REDO_ROLES,
    _ABUSE_VIEW_ROLES,
    _CONVERSION_VIEW_ROLES,
    _CONVERSION_REVENUE_ROLES,
    _HQ_ROLES,
    _conversion_can_see_revenue,
    _VALID_QUEUE_STATUSES,
    _store_scope_or_404,
    _filter_tests_by_store_scope,
)
from .models import (  # noqa: F401
    QueueItemCreate,
    ClinicalFindings,
    SoapDxCode,
    SoapNote,
    ExamEyeReading,
    AutoRefEyeReading,
    ExamRefraction,
    AutoRefExam,
    SlitLampEyeExam,
    SlitLampExam,
    EyeTestData,
    StatusUpdate,
    RedoCreate,
    ProductRecommendation,
    SendToFloorInput,
)
from .helpers import (  # noqa: F401
    format_axis_value,
    format_rx_value,
    _validate_eye_test_rx,
    _validate_eye_test_payload,
    _validate_keratometry,
    _validate_exam_block,
    _exam_blocks_for_storage,
    _exam_header_for_storage,
    _axis_for_storage,
    _power_for_storage,
    _eye_for_rx_storage,
    _to_camel_case,
    _convert_to_camel,
    _get_empty_queue,
    _get_empty_tests,
)
from .eye_queue import (  # noqa: F401
    get_clinical_root,
    get_queue,
    add_to_queue,
    update_queue_status,
    remove_from_queue,
    start_test,
    get_queue_stats,
)
from .eye_tests import (  # noqa: F401
    _resolve_test_date_range,
    get_tests,
    get_test,
    complete_test,
    amend_eye_test,
)
from .handover import (  # noqa: F401
    _clinical_handover_enabled,
    send_test_to_floor,
    _notify_handover_recipients,
)
from .patient_history import (  # noqa: F401
    get_patient_tests,
    get_customer_tests,
)
from .stats import (  # noqa: F401
    get_optometrist_stats,
    get_conversion_dashboard,
)
from .soap import (  # noqa: F401
    get_soap_note,
    save_soap_note,
)
from .rx_print import (  # noqa: F401
    _eye_block,
    _rx_date,
    _build_rx_card_html,
    print_prescription,
    create_prescription_redo,
    list_prescription_redos,
)
from .abuse import (  # noqa: F401
    _rx_has_redo,
    _looks_like_id,
    _opto_label,
    _patient_label,
    _build_abuse_alerts,
    get_abuse_detection,
)
from .lens_combos import (  # noqa: F401
    LensPowerComboCreate,
    _get_lens_power_combos_col,
    list_lens_power_combos,
    create_lens_power_combo,
    delete_lens_power_combo,
)
from .manufacturability import (  # noqa: F401
    ManufacturabilityRequest,
    _parse_float_safe,
    _check_power_in_range,
    manufacturability_check,
)

_SUBMODULES = (
    _shared,
    models,
    helpers,
    eye_queue,
    eye_tests,
    handover,
    patient_history,
    stats,
    soap,
    rx_print,
    abuse,
    lens_combos,
    manufacturability,
)


class _ClinicalNamespace(types.ModuleType):
    """Make ``setattr`` on this package reach the sub-module that USES the name.

    The clinical tests patch helpers by the package path
    ``api.routers.clinical.<name>`` (``get_eye_test_repository``,
    ``get_prescription_repository``, ``get_audit_repository``, ``get_db``,
    ``_clinical_handover_enabled``, ``_get_lens_power_combos_col``, ...), via
    both ``monkeypatch.setattr(clinical, ...)`` and
    ``unittest.mock.patch("api.routers.clinical.<name>")``. While clinical was
    ONE module that rebound the single global the handlers read. After the
    split each sub-module holds its own reference, so a patch on the package
    alone would silently miss them and the handler would reach the REAL
    repository instead of the test's fake -- a false green on eye-test, Rx and
    IDOR code. Forwarding the write to every sub-module that binds the name
    restores exactly the single-module behaviour, monkeypatch's undo included
    (undo is another setattr).

    tests/test_clinical_package_split.py fails if this is removed.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _ClinicalNamespace
