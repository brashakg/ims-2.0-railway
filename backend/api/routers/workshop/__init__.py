"""Workshop router package -- lens jobs from the POS hand-off to the pickup
shelf: the board and KPIs, job records, status moves, lens QC and rework,
remake / spoilage analytics, the lens-order lifecycle and ready-notify, the F2
lab-bench routing, and the vendor (lens-lab) admin endpoints. It also holds
the shared scan / QC / money gates the Orders, Labels, Shipping and
lab-routing doors import.

Wave 6 split of the 3,528-line api/routers/workshop.py. The API is byte-
identical: every sub-module registers on the SAME APIRouter object (built in
_shared.py) and the ROUTE import block below runs in the original file's
order, so the route table is unchanged path-for-path and position-for-
position. FastAPI is first-registered-wins on a path clash, so a route
sub-module must never move above one that came before it in the flat file
(/jobs/by-vendor/{vendor_id} must stay above /jobs/{job_id}).

Everything the single-file module exposed is re-exported here, so
``from api.routers.workshop import X`` keeps working for every X.
"""

import sys
import types

# Helper sub-modules register NO routes, so their position here is free; they
# are listed in the original file's order.
from . import _shared
from . import gates
from . import models
from . import helpers

# ROUTE REGISTRATION ORDER == the original file's order. DO NOT REORDER.
from . import board
from . import jobs
from . import status
from . import qc
from . import spoilage
from . import lens
from . import lab_stations
from . import vendor

# Re-exports: the module-level surface the single file used to have.
from ._shared import (  # noqa: F401
    APIRouter,
    HTTPException,
    Depends,
    Query,
    Body,
    Response,
    BaseModel,
    Field,
    Optional,
    List,
    date,
    datetime,
    timedelta,
    uuid,
    logging,
    logger,
    get_current_user,
    require_roles,
    spoilage_analytics,
    get_db,
    get_workshop_repository,
    get_order_repository,
    get_audit_repository,
    get_vendor_repository,
    validate_store_access,
    can_access_store_scoped,
    WORKSHOP_ROLES,
    _FITTING_ROLES,
    LENS_STATUS_ORDER,
    LENS_STATUS_TIMESTAMP_FIELD,
    _next_lens_status_ok,
    _DC_HARDLOCK_OVERRIDE_ROLES,
    _resolve_dc_workshop_settings,
    _check_dc_hardlock,
    ADMIN_VENDOR_STATUSES,
    VALID_JOB_TRANSITIONS,
    MAX_REWORK,
    _REWORK_OVERRIDE_ROLES,
    _QC_INPUT_STATUSES,
    _QC_INPUT_STATUS_MESSAGE,
    router,
)
from .gates import (  # noqa: F401
    SCAN_GATE_MESSAGES,
    _QC_REQUIRED_TARGETS,
    _NON_PATIENT_FACING_STATUSES,
    _is_patient_facing,
    _qc_cleared,
    qc_cleared,
    QC_HANDOVER_BLOCKED_MESSAGE,
    _HANDOVER_GATE_SKIP_STATUSES,
    _HANDOVER_NON_QC_REMEDY_STATUSES,
    _CREATION_KEYS,
    _ADMINISTRATIVE_KEYS,
    _PRISTINE_GHOST_KEYS,
    _is_pristine_ghost,
    _handover_block_detail,
    assert_linked_job_qc_cleared,
    _resolve_job_order,
    gate_job_handover_payment,
    evaluate_scan_transition_gate,
)
from .models import (  # noqa: F401
    FittingDetails,
    WorkshopJobCreate,
    WorkshopJobUpdate,
    FittingDetailsUpdate,
    WorkshopVendorPatch,
    WorkshopVendorStatusBody,
    LensStatusBody,
    QcCheckItem,
    QcChecklistBody,
    StatusBody,
    LabScanBody,
    LabStationUpsert,
)
from .helpers import (  # noqa: F401
    generate_job_number,
    _assert_job_store_access,
    _order_has_rx_required_line,
    _verify_job_prescription,
    _stamp_job_actor_names,
    job_to_frontend,
)
from .board import (  # noqa: F401
    get_workshop_root,
    get_pending_jobs,
    get_overdue_jobs,
    get_ready_jobs,
    get_technician_workload,
    get_dashboard_kpis,
)
from .jobs import (  # noqa: F401
    list_jobs_by_vendor,
    list_jobs,
    create_job,
    update_fitting_details,
    get_job,
    update_job,
)
from .status import (  # noqa: F401
    update_job_status,
    assign_job,
    start_job,
    complete_job,
)
from .qc import (  # noqa: F401
    qc_job,
    qc_checklist,
    rework_job,
)
from .spoilage import (  # noqa: F401
    _SPOILAGE_MANAGER_ROLES,
    RemakeReasonCodesBody,
    _job_in_spoilage_window,
    get_remake_reason_codes,
    put_remake_reason_codes,
    get_spoilage_analytics,
)
from .lens import (  # noqa: F401
    update_lens_status,
    _ready_whatsapp_text,
    _perform_ready_notify,
    notify_ready,
)
from .lab_stations import (  # noqa: F401
    _LAB_SCAN_ROLES,
    _STATION_CONFIG_ROLES,
    list_lab_stations,
    upsert_lab_station,
    get_station_queue,
    lab_scan,
    print_job_card,
)
from .vendor import (  # noqa: F401
    patch_job_vendor,
    post_admin_vendor_status,
)

_SUBMODULES = (
    _shared,
    gates,
    models,
    helpers,
    board,
    jobs,
    status,
    qc,
    spoilage,
    lens,
    lab_stations,
    vendor,
)


class _WorkshopNamespace(types.ModuleType):
    """Make ``setattr`` on this package reach the sub-module that USES the name.

    The workshop tests patch repositories and helpers by the package path
    ``api.routers.workshop.<name>`` (``get_workshop_repository``,
    ``get_order_repository``, ``get_audit_repository``, ``get_db``,
    ``get_vendor_repository``, ``_resolve_dc_workshop_settings``,
    ``_perform_ready_notify``, ...), via ``monkeypatch.setattr(wm, ...)``.
    While workshop was ONE module that rebound the single global the handlers
    read. After the split each sub-module holds its own reference, so a patch
    on the package alone would silently miss them and the handler would reach
    the REAL repository instead of the test's fake -- a false green on the QC,
    handover-money and IDOR code. Forwarding the write to every sub-module that
    binds the name restores exactly the single-module behaviour, monkeypatch's
    undo included (undo is another setattr).

    tests/test_workshop_package_split.py fails if this is removed.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _WorkshopNamespace
