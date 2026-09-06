"""Sales orders router package -- the POS / money door.

Wave 5 split of the 6,649-line api/routers/orders.py. The API is byte-
identical: every sub-module registers on the SAME APIRouter object (built in
_shared.py) and the ROUTE import block below runs in the original file's
order, so the route table is unchanged path-for-path and position-for-
position. FastAPI is first-registered-wins on a path clash -- the
``/{order_id}`` family would swallow ``/search``, ``/overdue/list`` and the
rest of the fixed paths if it moved up.

Everything the single-file module exposed is re-exported here, so
``from api.routers.orders import X`` keeps working for every X.
"""

import sys
import types

# Helper sub-modules register NO routes, so their position here is free; they
# are listed in the original file's order.
from . import _shared
from . import pricing
from . import models
from . import rx
from . import numbering
from . import stock
from . import workshop
from . import release

# ROUTE REGISTRATION ORDER == the original file's order. DO NOT REORDER.
from . import lists
from . import create
from . import detail
from . import admin_edit
from . import items
from . import confirm
from . import payments
from . import delivery
from . import cancel
from . import invoices
from . import bopis
from . import upi

# Re-exports: the module-level surface the single file used to have.
from ._shared import (  # noqa: F401
    APIRouter,
    HTTPException,
    Depends,
    Query,
    Header,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    Any,
    Dict,
    List,
    Optional,
    datetime,
    date,
    timedelta,
    Enum,
    math,
    uuid,
    secrets,
    logging,
    logger,
    get_current_user,
    get_order_repository,
    get_customer_repository,
    get_stock_repository,
    get_product_repository,
    get_walkin_counter_repository,
    validate_store_access,
    derive_bill_type,
    _validate_rx_power,
    _validate_rx_axis,
    _is_rx_required_line,
    cash_denom,
    _get_db,
    POS_WRITE_ROLES,
    HANDOVER_ROLES,
    _gst_rate_for_category,
    is_online_store,
    resolve_gst_rate,
    gst_pricing_mode,
    _GST_CATEGORY_TABLE,
    _normalize_gst_category,
    LOW_GST_CATEGORIES,
    _is_known_gst_category,
    _compute_per_category_gst,
    router,
    to_camel_case,
    _stamp_status_actor_names,
    order_to_frontend,
    item_to_frontend,
    payment_to_frontend,
    OrderStatus,
    VALID_TRANSITIONS,
    validate_status_transition,
    RX_HOLD_BLOCK_DETAIL,
    STOCK_HOLD_BLOCK_DETAIL,
    RX_AND_STOCK_HOLD_BLOCK_DETAIL,
    _LEGACY_STOCK_REASON_PREFIX,
    order_hold_kinds,
    order_has_active_rx_hold,
    assert_no_active_rx_hold,
)
from .pricing import (  # noqa: F401
    PaymentMethod,
    OrderItemCreate,
    PaymentCreate,
    effective_line_discount_pct,
    combined_discount_pct,
    assert_stack_within_cap,
    _enforce_line_pricing,
)
from .models import (  # noqa: F401
    SalespersonSplit,
    OrderCreate,
    OrderUpdate,
    SuperadminEditLine,
    SuperadminOrderEdit,
    SuperadminInvoiceChange,
)
from .rx import (  # noqa: F401
    _RX_EXPIRY_OVERRIDE_ROLES,
    _validate_order_line_rx,
)
from .lists import (  # noqa: F401
    list_orders,
    get_pending_deliveries,
    get_unpaid_orders,
    get_overdue_orders,
    _query_names_one_customer,
    search_orders,
    get_sales_summary,
    get_status_counts,
)
from .numbering import (  # noqa: F401
    generate_order_number,
    _emi_annual_rate,
    build_emi_schedule,
    _order_create_response,
)
from .stock import (  # noqa: F401
    _resolve_product_doc,
    _canonical_pid,
    _resolve_billable_product,
    _get_catalog_collection,
    _resolve_catalog_product_doc,
    _VIRTUAL_PID_PREFIXES,
    _NON_SERIALIZED_ITEM_TYPES,
    _LENS_RESERVED_ITEM_TYPES,
    _takes_serialized_stock,
    _assert_serialized_stock_available,
    _assert_explicit_unit_sellable,
    _lens_reservation_key,
    _legacy_lens_reservation_key,
    _mark_units_sold,
)
from .create import (  # noqa: F401
    create_order,
)
from .detail import (  # noqa: F401
    get_order,
    update_order,
)
from .admin_edit import (  # noqa: F401
    _require_superadmin,
    _write_order_edit_audit,
    _rebuilt_items_or_existing,
    superadmin_edit_order,
    superadmin_invoice_change,
)
from .items import (  # noqa: F401
    add_order_item,
    remove_order_item,
)
from .workshop import (  # noqa: F401
    _WORKSHOP_LENS_TYPES,
    _WORKSHOP_FRAME_TYPES,
    _order_item_kind,
    _order_needs_fitting,
    _ensure_workshop_job_for_order,
)
from .confirm import (  # noqa: F401
    confirm_order,
)
from .payments import (  # noqa: F401
    add_payment,
)
from .delivery import (  # noqa: F401
    mark_ready,
    HandoverDetails,
    DeliverRequest,
    deliver_order,
    DeliverWithPaymentRequest,
    deliver_with_payment,
)
from .release import (  # noqa: F401
    _is_unit_tracked,
    _release_line_units,
    _lens_line_already_committed,
    _release_lens_lines,
    _claim_order_status,
    _claim_order_for_cancel,
)
from .cancel import (  # noqa: F401
    cancel_order,
)
from .invoices import (  # noqa: F401
    _invoice_state_code,
    _customer_state_code,
    _build_invoice_gst_split,
    _assemble_invoice,
    get_invoice,
    get_invoice_pdf,
)
from .bopis import (  # noqa: F401
    BOPISLine,
    BOPISRequest,
    create_bopis_transfer,
)
from .upi import (  # noqa: F401
    get_upi_qr,
)

_SUBMODULES = (
    _shared,
    pricing,
    models,
    rx,
    lists,
    numbering,
    stock,
    create,
    detail,
    admin_edit,
    items,
    workshop,
    confirm,
    payments,
    delivery,
    release,
    cancel,
    invoices,
    bopis,
    upi,
)


class _OrdersNamespace(types.ModuleType):
    """Make ``setattr`` on this package reach the sub-module that USES the name.

    59 test files patch helpers by the package path
    ``api.routers.orders.<name>`` (``_get_db``, ``_compute_per_category_gst``,
    ``_mark_units_sold``, ``generate_order_number``, ``_assemble_invoice``,
    ...). While orders was ONE module that rebound the single global the
    handlers read. After the split each sub-module holds its own reference, so
    a patch on the package alone would silently miss them and the handler would
    reach the REAL database instead of the test's fake -- a false green on the
    money path. Forwarding the write to every sub-module that binds the name
    restores exactly the single-module behaviour, monkeypatch's undo included
    (undo is another setattr).

    tests/test_orders_package_split.py fails if this is removed.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _OrdersNamespace
