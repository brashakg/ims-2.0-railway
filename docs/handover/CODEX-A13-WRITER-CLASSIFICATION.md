# A13 writer classification

Source inventory was pinned to `5f3db215` (application source equivalent to the A01 dependency `68f01425`). This is an expression classification, not a count of business features or persisted rows. No history was rewritten.

The direct AST inventory found 300 naive-clock ISO expressions: 243 live event expressions are corrected, 52 are excluded below, and five are handled explicitly (four established IST helpers plus the receipt lease clock). Indirect assignments and helper callers were inspected separately; 42 selected expressions in 20 files are listed below. Follow-up checks additionally covered HR manual/self-service values, cash capture, marketing snooze deadlines, Shopify historical-source stamps, lab progression and repository status history.

## Direct expressions

| File | Classification | Count | Functions / reason |
| --- | --- | ---: | --- |
| `backend/agents/implementations/megaphone.py` | utc-event | 3 | _build_reactivation_cohorts, _scan_personal_triggers, _score_nba_daily |
| `backend/api/routers/admin_extras.py` | utc-event | 1 | _now_iso |
| `backend/api/routers/analytics.py` | exclude | 3 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/analytics_v2.py` | utc-event | 2 | create_eye_camp, update_eye_camp |
| `backend/api/routers/auth.py` | utc-event | 1 | change_password |
| `backend/api/routers/budgets.py` | utc-event | 1 | upsert_budget |
| `backend/api/routers/campaigns.py` | utc-event | 1 | _now_iso |
| `backend/api/routers/catalog.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/catalog.py` | utc-event | 10 | adjust_product_inventory, bulk_sync_products_to_shopify, create_catalog_product, promote_catalog_product, sync_product_to_shopify, update_catalog_product |
| `backend/api/routers/cl_po.py` | utc-event | 1 | generate_cl_po |
| `backend/api/routers/clinical/eye_queue.py` | utc-event | 1 | add_to_queue |
| `backend/api/routers/clinical/eye_tests.py` | utc-event | 2 | amend_eye_test, complete_test |
| `backend/api/routers/clinical/lens_combos.py` | utc-event | 1 | create_lens_power_combo |
| `backend/api/routers/clinical/soap.py` | utc-event | 1 | save_soap_note |
| `backend/api/routers/crm.py` | exclude | 3 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/crm.py` | utc-event | 3 | complete_nba_card, dismiss_nba_card, log_reactivation_outcome |
| `backend/api/routers/customers.py` | utc-event | 7 | _append_consent_event, _email_validation_stamp, approve_tag_suggestion, create_customer, reject_tag_suggestion, suggest_customer_tag, validate_customer_emails |
| `backend/api/routers/dashboard_widgets.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/display_fixtures.py` | exclude | 1 | Unused helper; no live call site found. Actual fixture persistence uses BSON. |
| `backend/api/routers/expenses.py` | utc-event | 13 | approve_advance, approve_expense, create_expense, disburse_advance, mark_entered, reject_expense, request_advance, send_to_accountant, settle_advance, submit_expense, update_expense_caps, upload_bill |
| `backend/api/routers/finance/_shared.py` | utc-event | 1 | _iso_now |
| `backend/api/routers/finance/bank_statement.py` | utc-event | 1 | import_bank_statement |
| `backend/api/routers/finance/period_lock.py` | utc-event | 1 | lock_period |
| `backend/api/routers/follow_ups.py` | utc-event | 5 | auto_generate_follow_ups, complete_follow_up, create_follow_up |
| `backend/api/routers/hr.py` | exclude | 1 | No-repository response fallback. |
| `backend/api/routers/hr.py` | utc-event | 11 | apply_leave, approve_leave, approve_leave_remote, approve_weekoff_swap, check_out_by_id, create_shift, create_weekoff_swap, edit_attendance, mark_attendance, reject_leave, reject_weekoff_swap |
| `backend/api/routers/hr_self_service.py` | utc-event | 1 | cancel_my_leave |
| `backend/api/routers/inventory/accountability.py` | utc-event | 1 | assign_accountability |
| `backend/api/routers/inventory/opening_stock.py` | utc-event | 1 | _write_opening_stock_batch |
| `backend/api/routers/inventory/stock_count.py` | utc-event | 1 | _upsert_count_item |
| `backend/api/routers/jarvis.py` | exclude | 13 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/jarvis.py` | utc-event | 1 | _cmd_create_task |
| `backend/api/routers/labels.py` | exclude | 2 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/loyalty.py` | utc-event | 3 | create_reward, update_reward |
| `backend/api/routers/marketing.py` | utc-event | 13 | create_walkin, record_whatsapp_consent, redeem_referral, send_nps_survey, send_referral_invite, submit_nps_response, update_consent_text |
| `backend/api/routers/orders/admin_edit.py` | utc-event | 4 | _write_order_edit_audit, superadmin_edit_order, superadmin_invoice_change |
| `backend/api/routers/orders/bopis.py` | utc-event | 1 | create_bopis_transfer |
| `backend/api/routers/orders/create.py` | utc-event | 3 | create_order |
| `backend/api/routers/orders/delivery.py` | utc-event | 1 | deliver_order |
| `backend/api/routers/orders/invoices.py` | exclude | 1 | Invoice calendar-date fallback, separate from event timestamp. |
| `backend/api/routers/orders/payments.py` | utc-event | 1 | add_payment |
| `backend/api/routers/orders/release.py` | utc-event | 1 | _claim_order_for_cancel |
| `backend/api/routers/payroll.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/payroll.py` | utc-event | 16 | approve_payroll, bulk_upsert_salary_config, calculate_salary, create_salary_config, get_payslip, lock_payroll, record_salary_advance, run_payroll, seed_pt_slabs, settle_salary_advance, update_salary_config, upsert_pt_slab |
| `backend/api/routers/prescriptions.py` | utc-event | 2 | update_prescription, upload_prescription_photo |
| `backend/api/routers/products.py` | utc-event | 1 | update_product |
| `backend/api/routers/promotions.py` | utc-event | 1 | _now_iso |
| `backend/api/routers/purchase_invoices.py` | utc-event | 10 | _apply_invoice_mrp, _apply_valuation_trueup, _claim_grn_units, _stamp_dcs_matched, allocate_invoice_landed_costs, approve_invoice_exception, create_purchase_invoice, set_invoice_landed_costs, update_purchase_config |
| `backend/api/routers/reminders.py` | utc-event | 1 | _now_iso |
| `backend/api/routers/reports/blueprint.py` | exclude | 3 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/reports/customers.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/reports/purchase.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/returns.py` | utc-event | 5 | _audit_stock_transition, _reactivate_original_unit, create_return, retry_restock |
| `backend/api/routers/settings.py` | utc-event | 10 | _save_feature_toggles, set_discount_rule, update_approval_workflows, update_discount_rules, update_integration, update_marketplace_channels, update_notification_providers, update_preferences, update_system_settings, update_tds_rates |
| `backend/api/routers/shipping.py` | utc-event | 2 | book_shipment, track_shipment |
| `backend/api/routers/stores.py` | utc-event | 1 | delete_store |
| `backend/api/routers/transfers.py` | utc-event | 36 | _audit_stock_move, _book_mirror_purchase, _create_receive_mismatch_task, _rehome, approve_transfer, bulk_approve_transfers, cancel_transfer, complete_picking, complete_transfer, create_shiprocket_shipment_for_transfer, create_transfer, receive_transfer, ship_transfer, start_picking, update_transfer |
| `backend/api/routers/users.py` | utc-event | 1 | reset_password |
| `backend/api/routers/vendor_portal.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/vendor_returns.py` | utc-event | 6 | create_vendor_return, price_vendor_return, update_return_status |
| `backend/api/routers/vendors/ap_bills.py` | utc-event | 1 | create_vendor_bill |
| `backend/api/routers/vendors/ap_payments.py` | utc-event | 2 | create_debit_note, create_vendor_payment |
| `backend/api/routers/vendors/grn_accept.py` | utc-event | 3 | _accept_grn_claimed, refresh_po_received |
| `backend/api/routers/vendors/grn_accept_lock.py` | paired-manual | 1 | Deliberate IST history or atomic lock reader needs paired change. |
| `backend/api/routers/vendors/grn_create.py` | utc-event | 1 | _create_grn_impl |
| `backend/api/routers/vendors/grn_void.py` | utc-event | 3 | drop_over_order, escalate_grn, void_grn |
| `backend/api/routers/vendors/master.py` | utc-event | 2 | create_vendor, update_vendor |
| `backend/api/routers/vendors/numbering.py` | utc-event | 1 | _grn_stock_audit |
| `backend/api/routers/vendors/po_detail.py` | utc-event | 1 | send_po |
| `backend/api/routers/vendors/purchase_orders.py` | utc-event | 1 | create_po |
| `backend/api/routers/vendors/variance.py` | utc-event | 1 | dismiss_po_variance |
| `backend/api/routers/vouchers.py` | utc-event | 5 | cancel_voucher, mint_voucher, redeem_voucher_atomic |
| `backend/api/routers/workshop/board.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/routers/workshop/jobs.py` | utc-event | 1 | create_job |
| `backend/api/routers/workshop/spoilage.py` | exclude | 1 | Response metadata; no persisted event written by this expression. |
| `backend/api/services/family_wallet.py` | paired-manual | 1 | Deliberate IST history or atomic lock reader needs paired change. |
| `backend/api/services/lapsed_reactivation.py` | utc-event | 1 | build_cohort_doc |
| `backend/api/services/money_guard.py` | paired-manual | 1 | Deliberate IST history or atomic lock reader needs paired change. |
| `backend/api/services/nba_call_list.py` | utc-event | 1 | build_nba_doc |
| `backend/api/services/non_adapt.py` | utc-event | 1 | _now_iso |
| `backend/api/services/notification_service.py` | utc-event | 1 | queue_notification_row |
| `backend/api/services/nps_trigger.py` | utc-event | 2 | trigger_nps_on_delivery |
| `backend/api/services/order_superadmin_edit.py` | utc-event | 2 | build_credit_note_doc, build_revised_invoice_doc |
| `backend/api/services/petty_cash_service.py` | paired-manual | 1 | Deliberate IST history or atomic lock reader needs paired change. |
| `backend/api/services/petty_cash_settlement_service.py` | paired-manual | 1 | Deliberate IST history or atomic lock reader needs paired change. |
| `backend/api/services/product_master.py` | utc-event | 5 | _sync_status_dict, create_product, revive_discarded_draft, soft_delete_product, update_product |
| `backend/api/services/serial_tracking.py` | utc-event | 3 | capture_serial, mark_serial_sold, transition_serial |
| `backend/api/services/store_credit_ledger.py` | utc-event | 1 | make_entry |
| `backend/api/services/upi_qr.py` | utc-event | 1 | _do_reconcile |
| `backend/api/services/vip_triggers.py` | utc-event | 4 | build_vip_update, claim_fire, create_trigger, update_trigger |
| `backend/database/repositories/clinical_repository.py` | utc-event | 5 | amend_test, complete_test, create_test, update_status |
| `backend/database/repositories/order_repository.py` | utc-event | 1 | update_status |
| `backend/database/repositories/product_repository.py` | utc-event | 2 | claim_for_transfer, release_sold_units_for_order |
| `backend/database/seed_data.py` | exclude | 1 | Seed/example data is not a live writer. |
| `backend/database/seed_data_OLD.py` | exclude | 16 | Seed/example data is not a live writer. |

## Indirect expressions

| File | Functions | Encoding / preserved siblings |
| --- | --- | --- |
| `backend/api/routers/clinical/eye_tests.py` | complete_test | utc_event_iso |
| `backend/api/routers/clinical/rx_print.py` | create_prescription_redo | utc_event_iso |
| `backend/api/routers/inventory/serials.py` | create_serial, update_serial | utc_event_iso |
| `backend/api/routers/inventory/stock_count.py` | start_stock_count | utc_event_iso |
| `backend/api/routers/inventory/stock_count_items.py` | complete_stock_count | utc_event_iso |
| `backend/api/routers/inventory/stock_count_reconcile.py` | finish_stuck_stock_count_reconcile, reconcile_stock_count | utc_event_iso |
| `backend/api/routers/labels.py` | scan_advance | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/online_store_orders.py` | clear_rx_hold | utc_event_iso |
| `backend/api/routers/orders/release.py` | _claim_order_status | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/reports/day_end.py` | create_day_end_close | utc_event_iso |
| `backend/api/routers/vendor_portal.py` | post_vendor_status | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/vendors/master.py` | create_vendor_sku_alias | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/vendors/purchase_orders.py` | create_pos_from_forecast | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/workshop/jobs.py` | update_fitting_details | utc_now_iso; local order_date/order_time defaults unchanged |
| `backend/api/routers/workshop/lab_stations.py` | print_job_card | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/workshop/lens.py` | _perform_ready_notify, update_lens_status | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/workshop/qc.py` | qc_checklist, rework_job | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/api/routers/workshop/vendor.py` | post_admin_vendor_status | utc_event_iso; shared new BSON sample is naive UTC |
| `backend/database/repositories/workshop_repository.py` | add_qc_result, update_status | utc_event_iso; shared new BSON sample is naive UTC |

## Explicit exclusions and compatibility boundaries

- BSON timestamps remain datetime values. Only shared clocks that also produce changed event strings use a single new UTC sample, retaining naive BSON values. Unrelated BSON-only writers, including BaseRepository timestamps and task archive dates, retain their existing server-UTC assumption; this is not a BSON clock migration.
- Financial document dates, bill/due dates, attendance date keys, manual prescription dates, expected delivery and lens-receive dates retain their existing calendar interpretation. Workshop fitting date/time defaults and notification-ID date prefixes retain their original local-calendar behavior.
- Response-only metadata, export generation stamps, unused helpers and seed-only values do not become live-event fixes merely because they contain isoformat. The independent scan also checked backend/ai response-only subagents and found no persisted writer there.
- Existing aware ISO writers remain unchanged unless their paired reader requires compatibility. Deliberate legacy IST money/attendance fields emit +05:30; ordinary new event strings emit +00:00. Old naive UTC event history is parsed as UTC only in documented event readers, and old naive IST attendance is parsed as IST.
- Shopify new history-import payment/refund strings retain the source instant. Existing historical records, source IDs, supplier-note dates, tax periods and idempotency identities are not rewritten.
- Drawer string queries support legacy UTC and the new canonical +00:00 values, with inclusive endpoints; they do not claim to query arbitrary-offset historical strings.
- Pre-existing GSTR-3B credit-note last-microsecond exclusion is outside A13: `_credit_note_totals` uses a strict upper comparison against an inclusive month-end helper. Both old naive and new explicit UTC values behave identically. GSTR-1 uses its separate half-open window. This remains a separately reported arithmetic/date-window defect.
