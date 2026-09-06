"""
IMS 2.0 - Reports Router
=========================
Real database queries for dashboard and reports

Wave 5 split one 6,483-line module into this package. Every sub-module
registers on the SINGLE ``APIRouter`` built in ``_shared`` and they are
imported below in the original file order, so the route table -- paths,
methods, names and registration order -- is unchanged.
"""

import sys
from types import ModuleType

from ._shared import (  # noqa: F401  # re-exported: see _ReportsModule
    logging,
    re,
    APIRouter,
    Depends,
    Query,
    BaseModel,
    Field,
    Any,
    Dict,
    Optional,
    date,
    datetime,
    timedelta,
    now_ist,
    now_ist_naive,
    fy_start_year_ist,
    ist_date_str,
    ist_day_start_utc,
    ist_month_window_utc,
    ist_today,
    order_interstate_flag,
    monthrange,
    get_current_user,
    require_roles,
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_attendance_repository,
    get_audit_repository,
    get_eye_test_repository,
    get_product_repository,
    get_db,
    validate_store_access,
    order_actor_id,
    order_actor_name_map,
    _auto_reorder_disabled,
    cash_denom,
    till_service,
    logger,
    router,
    _REPORT_FINANCE_ROLES,
    _orders_in_window,
    _order_revenue,
    _order_discount,
    _GSTN_DOC_ILLEGAL,
    _OVER_CAP_ISSUE_CODE,
    _note_over_cap_serial,
    _gstr1_bill_number,
    _cdnr_note_number,
    _credit_note_date_ist,
    _order_tax,
    _item_revenue,
    _summarise_orders,
    _daily_trend,
    _category_breakdown,
    _stock_category_map,
    _row_category,
)
from .gst_base import (  # noqa: F401  # re-exported: see _ReportsModule
    _get_raw_db,
    _order_is_interstate,
    _order_taxable_and_tax,
    _b2cs_rate_lines,
    _normalise_period,
)
from .gst_itc import (  # noqa: F401  # re-exported: see _ReportsModule
    _itc_from_vendor_bills,
    _itc_transfer_from_vendor_bills,
    _transfer_outward_bills,
    _transfer_b2b_rows,
    _return_interstate_flag,
    _ledger_row_return_doc,
    _cn_foreign_store,
    _cn_bucket_rate,
)
from .overview import (  # noqa: F401  # re-exported: see _ReportsModule
    get_reports_root,
    dashboard_stats,
    inventory_report,
    sales_summary,
    daily_sales,
    sales_by_salesperson,
    sales_by_category,
)
from .inventory import (  # noqa: F401  # re-exported: see _ReportsModule
    inventory_summary,
    inventory_valuation,
    tax_code_audit,
    eye_test_report,
)
from .finance_ops import (  # noqa: F401  # re-exported: see _ReportsModule
    attendance_report,
    outstanding_report,
    gst_report,
    task_summary,
)
from .growth import (  # noqa: F401  # re-exported: see _ReportsModule
    sales_comparison,
    sales_growth,
    profit_by_category,
    profit_by_store,
    discount_analysis,
)
from .workshop import (  # noqa: F401  # re-exported: see _ReportsModule
    staff_ranking,
    pending_workshop_jobs,
    _WORKSHOP_REPORT_ROLES,
    workshop_productivity,
    daily_stock_count,
)
from .customers import (  # noqa: F401  # re-exported: see _ReportsModule
    expense_vs_revenue,
    customer_acquisition,
    brand_sellthrough,
    get_targets,
)
from .gstr1 import (  # noqa: F401  # re-exported: see _ReportsModule
    _compute_gstr1,
    gstr1_report,
    gstr1_gstn_json,
)
from .gstr3b import (  # noqa: F401  # re-exported: see _ReportsModule
    _credit_note_totals,
    _transfer_outward_totals,
    _rcm_from_vendor_bills,
    _compute_gstr3b,
    gstr3b_report,
    gstr3b_gstn_json,
)
from .promotions import (  # noqa: F401  # re-exported: see _ReportsModule
    promotions_report,
    non_moving_stock,
)
from .analytics import (  # noqa: F401  # re-exported: see _ReportsModule
    defaultdict,
    _fy_of,
    _PRICE_BANDS,
    _PRICE_BAND_NAMES,
    _price_band_of,
    _order_created_at,
    _order_net,
    _month_iter,
    footfall_audit,
    sales_price_bands,
    _LENS_ITEM_TYPES,
    sales_lens_deep_dive,
    sales_seasonality,
)
from .purchase import (  # noqa: F401  # re-exported: see _ReportsModule
    _DEFAULT_LEAD_TIME_DAYS,
    _DEFAULT_REORDER_CYCLE_DAYS,
    _DEFAULT_SAFETY_BUFFER_DAYS,
    _confidence_for,
    purchase_recommendations,
)
from .blueprint import (  # noqa: F401  # re-exported: see _ReportsModule
    _BLUEPRINT_SECTIONS,
    _BLUEPRINT_SYSTEM_PROMPT,
    _r3_assemble_inputs,
    growth_blueprint,
)
from .day_end import (  # noqa: F401  # re-exported: see _ReportsModule
    _DAY_END_CLOSE_ROLES,
    DayEndCloseBody,
    _day_end_doc_public,
    get_day_end_close,
    create_day_end_close,
)

_SUBMODULE_NAMES = (
    "_shared",
    "gst_base",
    "gst_itc",
    "overview",
    "inventory",
    "finance_ops",
    "growth",
    "workshop",
    "customers",
    "gstr1",
    "gstr3b",
    "promotions",
    "analytics",
    "purchase",
    "blueprint",
    "day_end",
)

class _ReportsModule(ModuleType):
    """Fan a ``setattr`` on this package out to the sub-modules that bind it.

    The reports module has always been patched by name -- tests swap in fake
    repositories with ``setattr(reports, "get_db", ...)`` and freeze the clock
    with ``reports.ist_today = ...``. Those globals now live in the sub-modules,
    so a set on the package has to reach every sub-module that holds the name,
    or the endpoint would keep calling the real one. Reads still resolve
    against the re-exports above.
    """

    def __setattr__(self, name, value):
        ModuleType.__setattr__(self, name, value)
        for mod in _submodules():
            if name in vars(mod):
                ModuleType.__setattr__(mod, name, value)


def _submodules():
    prefix = __name__ + "."
    return [sys.modules[prefix + n] for n in _SUBMODULE_NAMES if prefix + n in sys.modules]


sys.modules[__name__].__class__ = _ReportsModule
