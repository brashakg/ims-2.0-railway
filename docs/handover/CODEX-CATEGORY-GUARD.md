# Product category edit guard

The staff editor already locks category except for an unfinished ordered
product. A direct PUT to `/api/v1/products/{product_id}` could bypass that
restriction, changing the category, derived HSN/rate and identity of a finished
product with existing stock or sales history.

The API now asks the same `product_master.is_ordered_draft` rule before an actual
category change. Non-draft changes return 409 before product, catalogue-twin,
identity or audit writes. Alias-equivalent values such as SG/SUNGLASS and edits
that omit category remain valid. No frontend or staff workflow is changed.

The exception is deliberately the existing editor exception: provisional and
not ACTIVE. A stale provisional flag on an ACTIVE product does not unlock it.
Existing legacy review and master-product update doors are unchanged; the
master update schema does not expose category. This does not reclassify any
historical product or certify other catalogue mutation paths.

Validation:

- New ten-case regression suite: five bypass cases failed against unchanged
  source, while five alias/omitted/invalid controls passed.
- Final related run: 292 passed, 10 skipped. Includes the real ordered-draft
  finishing flow that reclassifies a frame to a sunglass, updates tax/identity,
  and releases two held pieces; barcode, catalogue owner rules, HSN/rates and
  price-edit controls also pass. Skips cover retired import doors.
- CI-pinned Pylint 3.0.3 errors/fatal check passed. Backend smoke: 1,314 routes.
- Forced frontend TypeScript and Vite build passed. The pre-existing CSS comment
  warning remains; no frontend source changed.
- All backend tests use isolated test configuration and mock data. No live
  transaction, database write, POS edit or Shopify operation was performed.

Base main: `10c6c645ebe8354958da2e1e64d91ee2c36a9791`.
Branch: `codex/catalog-category-guard`.
Require final exact-head review, required CI and CLEAN status before merge.
