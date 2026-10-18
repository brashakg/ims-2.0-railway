# Product category edit guard

The staff editor already locks category except for an unfinished ordered
product. A direct PUT to `/api/v1/products/{product_id}` could bypass that
restriction, changing the category, derived HSN/rate and identity of a finished
product with existing stock or sales history.

The API now asks the same `product_master.is_ordered_draft` rule before an actual
category change. Non-draft changes return 409 before product, catalogue-twin,
identity or audit writes. Alias-equivalent values such as SG/SUNGLASS and edits
that omit category remain valid. No frontend or staff workflow is changed.

Independent review reproduced a concurrent-completion bypass in the first
patch: a request read an ordered draft, another save finished it and released
stock, and the stale category edit still landed. Category writes now compare
the stored category, catalogue status and provisional flag atomically. A lost
race returns 409 before audit or twin writes. The same fence covers alias echoes
so they cannot overwrite a newer draft category. ProductRepository.update keeps
its existing barcode ownership, switched-on/edit stamps and cache invalidation
when using the optional conditional write.

The exception is deliberately the existing editor exception: provisional and
not ACTIVE. A stale provisional flag on an ACTIVE product does not unlock it.
Existing legacy review and master-product update doors are unchanged; the
master update schema does not expose category. This does not reclassify any
historical product or certify other catalogue mutation paths.

Validation:

- New ten-case regression suite: five bypass cases failed against unchanged
  source, while five alias/omitted/invalid controls passed.
- Final corrected run: 343 passed, 10 skipped. Includes three injected
  concurrent completion/category-edit cases and the real ordered-draft
  finishing flow that reclassifies a frame to a sunglass, updates tax/identity,
  and releases two held pieces; barcode, catalogue owner rules, HSN/rates and
  price-edit, attribution and cache controls also pass. Ten optional database-backed checks skipped
  because no local real MongoDB is available; mock-data checks passed.
- CI-pinned Pylint 3.0.3 errors/fatal check passed. Backend smoke: 1,314 routes.
- Forced frontend TypeScript and Vite build passed. The pre-existing CSS comment
  warning remains; no frontend source changed.
- All backend tests use isolated test configuration and mock data. No live
  transaction, database write, POS edit or Shopify operation was performed.

Base main: `10c6c645ebe8354958da2e1e64d91ee2c36a9791`.
Branch: `codex/catalog-category-guard`.
Require final exact-head review, required CI and CLEAN status before merge.

The first final-head E2E run found an unrelated existing probe weakness:
cash-flow was declared fixed while its screenshot still showed only Loading.
Its route inventory now waits for the existing payables card before measuring.
No cash-flow application code or layout exemption changed. This tightens the
browser check instead of retrying until a loading skeleton happens to disappear.
