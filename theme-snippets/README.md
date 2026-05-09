# BetterVision Boost Theme Snippets

Standalone Liquid files for the **Boost Mega Menu** Shopify theme on
`bokaro-better-vision.myshopify.com`. These files are **NOT** part of the
Next.js inventory app — they are dropped directly into the live Shopify
theme via the theme-code editor.

The bv-app pushes the data (metafields, tags, product types) and these
snippets read that data on the storefront. Visual style follows the same
convention as `src/lib/autoGenerate.ts` `generateHTMLDescription` — `<h4>`
section headings, no inline `<style>` blocks, all visual styling lives in
the theme's CSS so the merchant can override centrally.

---

## What's in here

| File | Type | Purpose |
| ---- | ---- | ------- |
| `snippets/badge-overlay.liquid` | snippet | Top-left product-card badge driven by `product.metafields.bv.label` |
| `snippets/rx-module.liquid` | snippet | Prescription capture form for `rxable_yes` products |
| `snippets/mega-menu-image-badge.liquid` | snippet | Image + badge text in mega-menu items |
| `templates/product.preorder.liquid` | template | Pre-order alternate template (replaces ATC with "Order Now") |
| `templates/product.appointment.liquid` | template | Appointment-only product (hides price + ATC, shows enquiry form) |
| `templates/product.rx-required.liquid` | template | Add-to-cart blocked until prescription captured |
| `templates/collection.brand.liquid` | template | Brand-specific collection page |
| `templates/collection.shape.liquid` | template | Shape-based collection (Aviator, Round, ...) |
| `templates/collection.gender.liquid` | template | Gender-based collection (Men, Women, ...) |
| `templates/collection.sale.liquid` | template | Sale collection — discount sort, countdown banner |

---

## Installation

### 1. Add the snippets

In Shopify admin go to **Online Store -> Themes -> Boost Mega Menu ->
Actions -> Edit Code**. Under the `snippets/` folder click **Add a new
snippet** and paste the contents of each file in this directory's
`snippets/` subfolder. Filenames must match exactly (no `.liquid`
extension when adding — Shopify adds it).

### 2. Add the alternate templates

Under the `templates/` folder click **Add a new template -> product**
(or `collection`) and pick the suffix that matches the filename:
`preorder`, `appointment`, `rx-required` for products and `brand`,
`shape`, `gender`, `sale` for collections. Paste the file contents.

Once saved, each template appears in the product/collection admin page
under "Theme template" — pick it for the relevant product or
collection.

### 3. Wire the snippets into the existing theme

The new snippets only render when included from the existing theme
files. The merchant's theme developer (or the merchant via the
inline-code editor) needs to add the include lines below.

#### a. `snippets/badge-overlay.liquid` -> Boost product card

In the Boost theme, product cards are usually rendered by
`snippets/product-card-grid.liquid` (Boost) or
`snippets/card-product.liquid` (Dawn-style fork). Find the wrapper that
contains the product image (look for `<div class="product-card__image">`
or `<a class="product-card-link">`) and add **inside that wrapper**, as
the first child:

```liquid
{%- render 'badge-overlay', product: product -%}
```

The snippet renders nothing if the metafield is empty, so it's safe to
include unconditionally.

#### b. `snippets/rx-module.liquid` -> product page

The `rx-required` template already includes the snippet. For ordinary
product templates that should also surface the RX module (when a product
has tag `rxable_yes` but isn't using the RX-required template), add to
the product info section (`sections/product-template.liquid` or
`sections/main-product.liquid`), right after the variant picker:

```liquid
{%- render 'rx-module', product: product -%}
```

#### c. `snippets/mega-menu-image-badge.liquid` -> mega menu

The Boost mega-menu structure is in `sections/header.liquid` (or
`snippets/mega-menu.liquid` depending on theme version). Inside the
loop that renders each menu link, replace the bare `<img>` (or add
alongside it) with:

```liquid
{%- render 'mega-menu-image-badge', link: link, parent_handle: parent_handle -%}
```

`parent_handle` is the handle of the top-level menu item — pass it from
the surrounding loop so the snippet can fall back to a per-collection
default image.

---

## Required merchant configuration in Shopify admin

The bv-app pushes some of these via the existing sync; others are
metafields the merchant must define once in Shopify so the snippets
render.

### Metafield definitions (one-time setup)

Go to **Settings -> Custom data**.

#### Product metafields (namespace: `bv`)

| Key | Type | Used by | Notes |
| --- | ---- | ------- | ----- |
| `label` | Single-line text | `badge-overlay.liquid` | Round 1 mapping 10.2 — the product label is shown as a small badge, NOT as a Shopify tag. e.g. "Polarized", "New", "Bestseller". |
| `label_color` | Single-line text | `badge-overlay.liquid` | Optional hex (e.g. `#0a8754`). Defaults to dark grey if empty. |
| `preorder_ship_date` | Date or single-line text | `templates/product.preorder.liquid` | "Estimated ship date" shown next to the Order Now button. e.g. "12 May 2026" or ISO date. |

#### Collection metafields (namespace: `bv`)

| Key | Type | Used by | Notes |
| --- | ---- | ------- | ----- |
| `banner_image` | File reference (Image) | `templates/collection.brand.liquid` | Hero banner shown above the collection grid. |
| `short_description` | Multi-line text | `templates/collection.brand.liquid` | One-paragraph brand intro. |
| `face_shape_pairings` | Multi-line text | `templates/collection.shape.liquid` | One-line per face shape, e.g. "Round face: try aviator or square". |
| `editorial_image` | File reference (Image) | `templates/collection.gender.liquid` | Hero lifestyle image. |
| `editorial_tagline` | Single-line text | `templates/collection.gender.liquid` | Hero overlay copy, e.g. "Eyewear that travels with you". |
| `sale_ends` | Date and time | `templates/collection.sale.liquid` | Countdown banner end time (ISO 8601). |

#### Menu-item metafields (namespace: `bv`)

| Key | Type | Used by | Notes |
| --- | ---- | ------- | ----- |
| `menu_image` | File reference (Image) | `mega-menu-image-badge.liquid` | Image next to the mega-menu link. |
| `menu_badge` | Single-line text | `mega-menu-image-badge.liquid` | Badge text, e.g. "New", "Sale". |

Shopify's "linked" navigation menu items support metafields via the
**Navigation -> Menu items** definition once `Menus` is enabled under
Custom data.

### Tags (managed by bv-app sync)

These tags are written by `src/lib/autoGenerate.ts` `generateTags` and
the merchant **does not need to manage them manually** — they appear on
products as soon as the local fields are set:

| Tag | Source field | Used by |
| --- | ------------ | ------- |
| `rxable_yes` | Product attribute "RX-able" toggle (round 2 9.5 + U5) | `rx-module.liquid`, `product.rx-required.liquid` |
| `preorder_yes` | Product preorder toggle | `product.preorder.liquid` (optional belt-and-braces) |
| `appointment_only` | Product appointment toggle | `product.appointment.liquid` |

### Product type / vendor

The bv-app continues to sync `productType` and `vendor`. The
`collection.brand.liquid` template assumes the merchant uses Shopify
**smart collections** with a `Vendor equals <Brand>` rule (Shopify
auto-creates these for popular brands).

---

## Per-template assignment summary

To activate a template after installation, in Shopify admin:

1. **Pre-order product** — open the product page in admin -> right column
   "Theme template" -> pick `product.preorder`. Round 2 U6.
2. **Appointment-only product** — pick `product.appointment`. Used for
   in-store-only items (children's frames where staff measure first,
   high-value designer items where pricing is per-customer).
3. **RX-required product** — pick `product.rx-required` for products
   that **must** have a prescription before checkout (mostly
   prescription-only contact lenses or single-vision spectacles where
   the merchant doesn't want unverified online ordering).
4. **Brand collection** — open the collection -> "Theme template" ->
   `collection.brand`. Set the `bv.banner_image` and
   `bv.short_description` metafields.
5. **Shape collection** — pick `collection.shape`. Optional metafield
   `bv.face_shape_pairings`.
6. **Gender collection** — pick `collection.gender`. Optional
   `bv.editorial_image`, `bv.editorial_tagline`.
7. **Sale collection** — pick `collection.sale`. Optional
   `bv.sale_ends`.

---

## Visual styling

All visible classes follow Boost / Dawn-style conventions
(`section-`, `product-card__`, `button button--primary`, etc.) so the
merchant's existing theme CSS picks them up. Two extra classes are
introduced and need a small CSS block in the theme's
`assets/theme.css` (or a new `assets/bv-overrides.css`):

```css
/* BetterVision overrides */
.bv-badge-overlay {
  position: absolute;
  top: 8px;
  left: 8px;
  height: 28px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #fff;
  background: var(--bv-badge-color, #2a2a2a);
  border-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
  z-index: 2;
  pointer-events: none;
}
.bv-rx-module { margin: 28px 0; padding: 20px; border: 1px solid #e2e2e2; border-radius: 6px; }
.bv-rx-module__title { font-size: 1.1em; margin: 0 0 8px; }
.bv-rx-module__grid { display: grid; grid-template-columns: 70px repeat(4, 1fr); gap: 8px; align-items: center; }
.bv-rx-module__grid label { font-weight: 600; font-size: 0.9em; }
.bv-rx-module__grid input { width: 100%; padding: 6px 8px; border: 1px solid #d0d0d0; border-radius: 3px; }
.bv-rx-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.45); z-index: 9999; align-items: center; justify-content: center; }
.bv-rx-modal[aria-hidden="false"] { display: flex; }
.bv-rx-modal__inner { background: #fff; max-width: 520px; padding: 24px; border-radius: 6px; }
.bv-preorder-note { color: #555; font-size: 0.92em; margin: 4px 0 12px; }
.bv-appointment-card { padding: 24px; background: #f6f6f4; border-radius: 6px; margin: 16px 0; }
.bv-collection-hero { position: relative; min-height: 220px; padding: 40px 24px; background-position: center; background-size: cover; color: #fff; }
.bv-collection-hero__overlay { position: absolute; inset: 0; background: linear-gradient(180deg, rgba(0,0,0,0.05), rgba(0,0,0,0.5)); }
.bv-collection-hero__inner { position: relative; max-width: 720px; }
.bv-mega-img-badge { position: relative; display: inline-block; }
.bv-mega-img-badge__badge { position: absolute; top: 4px; right: 4px; background: #c0392b; color: #fff; font-size: 10px; padding: 2px 6px; border-radius: 3px; }
.bv-sale-countdown { background: #fff7e1; border: 1px solid #f4d27a; padding: 10px 16px; border-radius: 4px; margin-bottom: 20px; font-weight: 600; }
```

This CSS lives **once** in the theme — the snippets and templates only
emit class names, never inline `<style>`.

---

## Round 1 / Round 2 mapping references

- **Round 1, mapping 10.2** — Label is a badge, not a tag.
  -> `badge-overlay.liquid`
- **Round 2, U5** — RX module enable toggle.
  -> `rx-module.liquid`, `product.rx-required.liquid`
- **Round 2, U6** — Theme templates (preorder, appointment,
  rx-required, plus collection variants).
  -> `templates/product.*.liquid`, `templates/collection.*.liquid`
- **Round 2, 9.5** — RX-able toggle on the product form populates the
  `rxable_yes` tag. -> RX module appearance condition.

---

## Notes for the bv-app side (no code change required)

- The `bv` namespace metafields above are **read-only from the
  storefront** for these snippets. If the bv-app later wants to write
  them, use `setProductMetafields()` in `src/lib/shopify.ts` (already
  exists per CLAUDE.md). Push from a future "label" / "preorder ship
  date" admin field as a metafield with `namespace: "bv"` and the keys
  listed in the table above.
- The `rxable_yes` tag is already emitted by `generateTags()` when the
  RX-able product attribute is true, per round 2 U5 + 9.5.
