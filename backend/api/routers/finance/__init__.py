"""Finance & Accounting router package.

Wave 5 split of the 6,604-line api/routers/finance.py. The API is byte-
identical: the sub-modules register their routes on the SAME two APIRouter
objects (built in _shared.py), and the import block below runs in the
original file's order, so the route table is unchanged path-for-path and
position-for-position. FastAPI is first-registered-wins on a path clash --
DO NOT SORT OR REORDER THAT BLOCK.

Everything the single-file module exposed is re-exported here, so
``from api.routers.finance import X`` keeps working for every X.
"""

import sys
import types

# ROUTE REGISTRATION ORDER == the original file's order. Do not reorder.
from . import _shared
from . import pnl
from . import gst
from . import receivables
from . import cash_flow
from . import survival
from . import itc
from . import period_lock
from . import budget
from . import gst_crosscheck
from . import tally
from . import pnl_breakdown
from . import cash_drawer_window
from . import cash_drawer
from . import cash_recon
from . import einvoice
from . import bank_statement
from . import ticker
from . import journal_entries

# Re-exports: the module-level surface the single file used to have.
from ._shared import (  # noqa: F401
    calendar,
    csv,
    io,
    logging,
    uuid,
    datetime,
    timedelta,
    date,
    timezone,
    now_ist,
    now_ist_naive,
    ist_date_str,
    ist_today,
    ist_day_start_utc,
    fy_start_year_ist,
    order_interstate_flag,
    Any,
    Optional,
    List,
    Dict,
    NamedTuple,
    APIRouter,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    File,
    Form,
    Body,
    Response,
    BaseModel,
    Field,
    get_current_user,
    validate_store_access,
    ap_engine,
    cashflow,
    itc_reconcile,
    cash_register,
    csv_safe,
    is_online_store,
    survival_cashflow,
    can_see_cost,
    SALARY_RESTRICTED_MESSAGE,
    is_payroll_shaped_expense,
    is_salary_admin,
    normalise_expense_category,
    cache,
    ticker_service,
    policy_engine,
    je_service,
    cash_denom,
    till_service,
    name_resolver,
    logger,
    router,
    ticker_router,
    _get_db,
    PAID_STATUSES,
    UNPAID_STATUSES,
    APPROVED_STATUSES,
    _REVENUE_EXPR,
    _TAX_EXPR,
    _DISCOUNT_EXPR,
    _EXCLUDED_ORDER_STATUSES,
    _REAL_ORDER_STATUS_FILTER,
    _parse_range_dt,
    _apply_created_at_range,
    _order_total,
    _item_cost,
    compute_cogs,
    compute_cogs_with_flag,
    _cost_by_product,
    _months_in_range,
    _payroll_cost,
    gst_reconciliation,
    _norm_state,
    _order_is_interstate,
    _split_output_tax,
    _store_maps,
    _store_state_map,
    _store_gstin_map,
    _customer_state_map,
    pnl_by_category,
    is_period_locked,
    check_period_locked,
    _payroll_by_store,
    _HQ_STORE_ROLES,
    _scope_store,
    _require_finance_admin,
    _iso_now,
    _store_name_map,
    _je_cal_day,
)
from .pnl import (  # noqa: F401
    get_revenue,
    COST_ONLY_PNL_FIELDS,
    PAYROLL_DERIVED_PNL_FIELDS,
    _normalise_expense_category,
    _is_payroll_shaped_expense,
    get_pnl,
)
from .gst import (  # noqa: F401
    _ITC_PENDING_STATUSES,
    _itc_eligible_bill,
    get_gst_summary,
)
from .receivables import (  # noqa: F401
    _DEFAULT_AR_CREDIT_TERMS_DAYS,
    _customer_credit_terms,
    _ar_due_date,
    _ar_days_overdue,
    get_outstanding,
    get_vendor_payments,
)
from .cash_flow import (  # noqa: F401
    get_cash_flow,
    _ap_rows,
    _ar_aging,
    _agg_sum,
    owner_dashboard,
    cash_flow_forecast,
)
from .survival import (  # noqa: F401
    _survival_policy_lists,
    _survival_month_expense_rows,
    _survival_ap_items,
    _survival_projected_income_paise,
    _build_survival_payload,
    get_survival_cashflow,
)
from .itc import (  # noqa: F401
    _primary_entity_state,
    itc_register,
    Gstr2bRow,
    Gstr2bReconcileBody,
    _book_rows_from_db,
    gstr2b_reconcile,
    _ITC_CSV_HEADERS,
    itc_export_csv,
)
from .period_lock import (  # noqa: F401
    lock_period,
    get_period_locks,
)
from .budget import (  # noqa: F401
    get_budget,
    get_reconciliation,
)
from .gst_crosscheck import (  # noqa: F401
    get_gst_reconciliation,
    _GST_CROSSCHECK_SIGNOFFS,
    GstCrossCheckSignoff,
    _gst_month_window,
    _books_and_tally_for_stores,
    _run_gst_cross_check,
    get_gst_cross_check,
    gst_cross_check_signoff,
    _jv_cgst_sgst_split,
)
from .tally import (  # noqa: F401
    _EWAY_VALUE_THRESHOLD,
    _B2B_PENDING_REMINDER_DAYS,
    _VALID_TALLY_STATUS,
    _b2b_customer_map,
    _is_b2b_customer,
    _days_since,
    _b2b_invoice_row,
    _b2b_invoices,
    _b2b_summary,
    B2BExportRequest,
    B2BMarkExportedRequest,
    B2BAttentionNoteRequest,
    _b2b_fetch_orders,
    list_b2b_invoices,
    get_b2b_invoice_tally_xml,
    export_b2b_invoices_to_tally,
    mark_b2b_invoices_exported,
    mark_b2b_invoice_done,
    set_b2b_attention_note,
    get_tally_sales_jv,
)
from .pnl_breakdown import (  # noqa: F401
    get_pnl_by_store,
    get_pnl_by_category,
    get_period_status,
)
from .cash_drawer_window import (  # noqa: F401
    _CASH_SESSIONS,
    DenominationLine,
    CashRegisterOpen,
    CashRegisterClose,
    _to_dt,
    _ist_day_face,
    _created_at_or_clauses,
    _ist_day_window,
    _naive_utc_iso_bound,
    _cash_sales_for_window,
    _CashExpenseWindow,
    _cash_expenses_for_window,
    OFF_TILL_EXPENSE_MESSAGE,
    _REFUND_EXPENSE_HINTS,
    _DOUBLE_ENTRY_EPSILON,
    _cash_refund_legs_for_window,
    _refund_double_entry_advisory,
)
from .cash_drawer import (  # noqa: F401
    open_cash_register,
    _shared_counted_paisa,
    close_cash_register,
    list_cash_register_sessions,
)
from .cash_recon import (  # noqa: F401
    _CASH_RECON_ROLES,
    _RECON_EPSILON,
    _CASH_RECON_SIGNOFFS,
    _recon_status,
    _user_name_map,
    _norm_by_mode,
    cash_reconciliation_summary,
    CashReconSignoff,
    cash_reconciliation_signoff,
)
from .einvoice import (  # noqa: F401
    _EINVOICE_ROLES,
    trigger_einvoice,
)
from .bank_statement import (  # noqa: F401
    _BS_DATE_COLS,
    _BS_DESC_COLS,
    _BS_DEBIT_COLS,
    _BS_CREDIT_COLS,
    _BS_AMOUNT_COLS,
    _BS_BALANCE_COLS,
    _parse_bank_csv,
    _auto_match_statement,
    import_bank_statement,
    list_bank_statements,
    get_bank_statement,
)
from .ticker import (  # noqa: F401
    _TICKER_CACHE_PREFIX,
    _ticker_stores_for,
    get_target_ticker,
    TickerSettingsBody,
    update_target_ticker_settings,
)
from .journal_entries import (  # noqa: F401
    _JE_MAKER_ROLES,
    _JE_CHECKER_ROLES,
    _je_require_enabled,
    _require_roles_for,
    _je_parse_entry_date,
    _je_raise,
    JeLineBody,
    JeCreateBody,
    JePinBody,
    JeRejectBody,
    CoaUpsertBody,
    create_journal_entry,
    list_journal_entries,
    get_journal_entry,
    submit_journal_entry,
    approve_journal_entry,
    reject_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    get_chart_of_accounts,
    upsert_chart_of_account,
    get_tally_journal_jv,
)

_SUBMODULES = (
    _shared,
    pnl,
    gst,
    receivables,
    cash_flow,
    survival,
    itc,
    period_lock,
    budget,
    gst_crosscheck,
    tally,
    pnl_breakdown,
    cash_drawer_window,
    cash_drawer,
    cash_recon,
    einvoice,
    bank_statement,
    ticker,
    journal_entries,
)


class _FinanceNamespace(types.ModuleType):
    """Make ``setattr`` on this package reach the sub-module that USES the name.

    39 test files (and the lazy ``from .finance import check_period_locked``
    in orders/vendors/returns/payroll/inventory) patch helpers by the package
    path ``api.routers.finance.<name>``. While finance was ONE module, that
    rebound the single global the handlers read. After the split each
    sub-module holds its own reference, so a patch on the package alone would
    silently miss them and the handler would reach the REAL database instead
    of the test's fake -- a false green on money code. Forwarding the write to
    every sub-module that binds the name restores exactly the single-module
    behaviour, monkeypatch's undo included (undo is another setattr).

    tests/test_finance_package_split.py fails if this is removed.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _FinanceNamespace
