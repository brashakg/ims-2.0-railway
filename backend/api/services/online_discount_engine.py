"""
IMS 2.0 - Online Discount-Rule Engine  (rebuild of BVI `DiscountRule`; DARK, ONLINE-only)
=========================================================================================
Reproduces, inside IMS, the automatic storefront-discount engine that BVI ran in
``ecommerce/src/lib/autoGenerate.ts`` (``calculateDiscountedPrice`` +
``findMatchingDiscountRule``) and stored in the Postgres ``DiscountRule`` model.

WHAT IT DOES
------------
Given a catalog product (its category / brand / sub-brand / MRP / cost) and the
owner-editable ``ecom_discount_rules`` collection, it decides the ONLINE
storefront price:

  * winning rule = the MOST SPECIFIC active match
        (category + brand + sub_brand)  >  (category + brand)  >  (category)
    tie-broken by ``priority`` (higher wins), then by discount % (higher wins).
  * online_offer     = round(MRP * (1 - pct/100))              [BVI parity]
  * online_compare_at = MRP  (the strike-through / "was" price)
  * a product/variant carrying an EXPLICIT manual online offer OVERRIDES the rule
    (owner ruling: rules are the DEFAULT; a hand-set online price wins).

CLAMPS (money invariants -- REUSED, never re-invented)
------------------------------------------------------
  * offer <= MRP always (the same invariant pricing_caps enforces at the DB door).
  * NEVER below cost: if a rule/manual price would sell below cost_price, it is
    capped AT cost and FLAGGED (``COST_CLAMPED``) -- we never silently sell at a
    loss. If cost itself exceeds MRP (bad data) we cap at MRP and flag
    ``COST_ABOVE_MRP`` (cannot protect margin without breaching the MRP ceiling).
  * ADVISORY only: after computing, the result is run through
    ``pricing_caps.evaluate_offer_price`` and, if the online discount would exceed
    the IN-STORE discount cap (category / luxury-brand), the flag
    ``EXCEEDS_INSTORE_CAP`` is attached. It is NOT enforced (the owner formula for
    the online price is cost-floor + MRP-ceiling only), but it makes the risk
    VISIBLE. See the FLAG note in the PR description.

SCOPE = ONLINE / STOREFRONT ONLY  (owner ruling, non-negotiable)
----------------------------------------------------------------
This engine NEVER touches in-store POS pricing or the in-store discount caps. It
writes ONLY the online-lineage price fields:

  * catalog_variants.discounted_price     -- the online SELLING price (what the
    Shopify push reads FIRST for a variant; see shopify_push._resolve_variant_pricing).
  * catalog_variants.compare_at_price     -- the online MRP / strike-through.
  * catalog_products.ecom.online_offer_price / .online_compare_at_price -- the
    product-level record + the no-variant fallback the push reads.

The in-store price lives on the ``products`` SPINE (offer_price) and its mirror
``catalog_products.offer_price`` / ``pricing.offer_price`` -- this engine NEVER
writes those. ``catalog_variants`` is the online variant tier only (no POS/billing
reader), so writing discounted_price / compare_at_price is storefront-only.

DARK
----
This module only COMPUTES + STORES online prices on IMS Mongo docs. Whether those
prices ever reach Shopify is gated entirely by the existing Phase-5 write-gates in
shopify_push.py (IMS_SHOPIFY_WRITES + SHOPIFY_DISPATCH_MODE + creds). Nothing here
makes a network call.

FAIL-SOFT
---------
``recompute_online_price`` / ``recompute_all`` NEVER raise -- a recompute must
never block a product save. A bad product returns ``{"ok": False, ...}`` and the
save proceeds untouched. No emojis (Windows cp1252).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from .pricing_caps import evaluate_offer_price

try:  # resolve_category makes rule<->product category matching alias-proof.
    from .product_master import resolve_category
except Exception:  # noqa: BLE001 -- keep the engine importable in any checkout
    def resolve_category(value: Any) -> Optional[str]:  # type: ignore
        s = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        return s or None

logger = logging.getLogger("ims.online_discount_engine")

# The owner-editable rule store (migrated from BVI Postgres DiscountRule).
RULES_COLLECTION = "ecom_discount_rules"

# Field names -- match EXACTLY what shopify_push reads so the push needs no change
# for variant-carrying products (the common case: all eyewear).
VARIANT_OFFER_FIELD = "discounted_price"       # online SELLING price (push reads 1st)
VARIANT_COMPARE_FIELD = "compare_at_price"     # online MRP / strike-through
ECOM_OFFER_FIELD = "online_offer_price"        # product-level online SELLING
ECOM_COMPARE_FIELD = "online_compare_at_price"  # product-level online MRP
# Owner override INPUT (read, never written by the engine). A positive value here
# wins over the rule. Lives on the ecom sub-doc (product) or top-level (variant).
MANUAL_OFFER_FIELD = "manual_online_offer_price"


# ===========================================================================
# Small numeric helpers (fail-soft; never raise)
# ===========================================================================


def _round2(value: Any) -> float:
    """Round to 2 decimals HALF-UP (money rounding; matches BVI's Math.round on
    paise and the push's ``f"{price:.2f}"`` formatting). Fail-soft -> 0.0."""
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:  # noqa: BLE001
        return 0.0


def _pos_or_none(value: Any) -> Optional[float]:
    """A positive float, else None (a non-positive / unparseable price is 'absent',
    never a real 0 that could zero out a storefront price)."""
    try:
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _first_pos(*values: Any) -> Optional[float]:
    for v in values:
        f = _pos_or_none(v)
        if f is not None:
            return f
    return None


def _norm_txt(value: Any) -> str:
    """Lower + strip (the same case/space folding BVI's findMatchingDiscountRule
    used for brand / sub-brand equality)."""
    return str(value or "").strip().lower()


# ===========================================================================
# Rule resolution -- BVI specificity, tie-broken by priority
# ===========================================================================


def _rule_active(rule: Dict[str, Any]) -> bool:
    """A rule with no ``active`` field is treated as active (BVI's DiscountRule had
    NO active column -- every rule was live). An explicit False disables it."""
    return bool(rule.get("active", True))


def _rule_pct(rule: Dict[str, Any]) -> float:
    """Discount percentage off MRP (accepts the IMS snake_case + the BVI camelCase
    key so a not-yet-migrated row still resolves)."""
    return (
        _pos_or_none(rule.get("discount_percentage"))
        or _pos_or_none(rule.get("discountPercentage"))
        or 0.0
    )


def _rule_priority(rule: Dict[str, Any]) -> float:
    try:
        return float(rule.get("priority") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rule_category(rule: Dict[str, Any]) -> str:
    rc = resolve_category(rule.get("category"))
    return rc if rc is not None else _norm_txt(rule.get("category"))


def _rule_sub_brand(rule: Dict[str, Any]) -> str:
    # Accept both the IMS key and the BVI camelCase key.
    return _norm_txt(rule.get("sub_brand") or rule.get("subBrand"))


def _best_by_priority(rules: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the winner among same-specificity matches: highest priority, then
    highest discount % (deterministic). Returns None for an empty list."""
    if not rules:
        return None
    return sorted(
        rules,
        key=lambda r: (_rule_priority(r), _rule_pct(r)),
        reverse=True,
    )[0]


def find_matching_rule(
    rules: List[Dict[str, Any]],
    category: Any,
    brand: Any = None,
    sub_brand: Any = None,
) -> Optional[Dict[str, Any]]:
    """The most specific ACTIVE rule for (category, brand, sub_brand), mirroring
    BVI findMatchingDiscountRule specificity:

        (category + brand + sub_brand)  >  (category + brand)  >  (category)

    Within one specificity level, ``priority`` (higher wins) then discount %
    breaks ties. A brand-level rule must carry NO sub_brand; a category-level rule
    must carry NO brand AND no sub_brand (a rule only matches at its OWN level --
    exactly BVI's behaviour). Category comparison is alias-proof (resolve_category
    on both sides), brand/sub-brand equality is case-insensitive. Returns None when
    nothing matches (caller then leaves MRP unchanged -- no discount)."""
    cat = resolve_category(category)
    if cat is None:
        cat = _norm_txt(category)
    br = _norm_txt(brand)
    sb = _norm_txt(sub_brand)

    cat_matches = [
        r for r in (rules or []) if _rule_active(r) and _rule_category(r) == cat
    ]
    if not cat_matches:
        return None

    # 1) category + brand + sub_brand (most specific)
    if br and sb:
        lvl1 = [
            r
            for r in cat_matches
            if _norm_txt(r.get("brand")) == br and _rule_sub_brand(r) == sb
        ]
        best = _best_by_priority(lvl1)
        if best is not None:
            return best

    # 2) category + brand (rule carries no sub_brand)
    if br:
        lvl2 = [
            r
            for r in cat_matches
            if _norm_txt(r.get("brand")) == br and not _rule_sub_brand(r)
        ]
        best = _best_by_priority(lvl2)
        if best is not None:
            return best

    # 3) category only (rule carries no brand and no sub_brand)
    lvl3 = [
        r for r in cat_matches if not _norm_txt(r.get("brand")) and not _rule_sub_brand(r)
    ]
    return _best_by_priority(lvl3)


# ===========================================================================
# Price computation -- PURE, tested. offer<=MRP + never-below-cost clamps.
# ===========================================================================


def compute_online_price(
    mrp: Any,
    *,
    discount_pct: float = 0.0,
    cost_price: Any = None,
    manual_offer: Any = None,
    discount_category: Any = None,
    brand: Any = None,
) -> Dict[str, Any]:
    """Resolve the ONLINE (offer, compare_at) for one item. PURE + deterministic.

    Precedence: a positive ``manual_offer`` WINS over the rule; otherwise the rule
    ``discount_pct`` applies; otherwise no discount (offer == MRP). The result is
    then clamped:  offer <= MRP  AND  offer >= cost_price (never below cost).

    Returns:
        {
          "offer": float,          # online selling price (>= cost, <= MRP)
          "compare_at": float,     # == MRP (strike-through)
          "pct": float,            # EFFECTIVE discount % after clamps
          "requested_pct": float,  # the % the rule/manual price implied pre-clamp
          "source": "manual"|"rule"|"none",
          "flags": [str, ...],     # COST_CLAMPED / COST_ABOVE_MRP / OFFER_ABOVE_MRP
                                   #   / EXCEEDS_INSTORE_CAP / INVALID_MRP
          "clamped": bool,
        }
    Never raises.
    """
    flags: List[str] = []
    mrp_f = _pos_or_none(mrp)
    if mrp_f is None:
        return {
            "offer": 0.0,
            "compare_at": 0.0,
            "pct": 0.0,
            "requested_pct": 0.0,
            "source": "none",
            "flags": ["INVALID_MRP"],
            "clamped": False,
        }

    cost_f = _pos_or_none(cost_price)
    manual_f = _pos_or_none(manual_offer)

    if manual_f is not None:
        raw = manual_f
        source = "manual"
        requested_pct = _round2((mrp_f - manual_f) / mrp_f * 100.0)
    elif discount_pct and float(discount_pct) > 0:
        pct = float(discount_pct)
        raw = _round2(mrp_f * (1.0 - pct / 100.0))
        source = "rule"
        requested_pct = _round2(pct)
    else:
        raw = mrp_f
        source = "none"
        requested_pct = 0.0

    offer = raw
    clamped = False

    # Invariant 1: offer <= MRP (the DB-level MRP >= offer rule; pricing_caps).
    if offer > mrp_f:
        offer = mrp_f
        clamped = True
        flags.append("OFFER_ABOVE_MRP")

    # Invariant 2: never below cost. Cap at cost and FLAG (don't sell at a loss).
    if cost_f is not None and offer < cost_f:
        if cost_f <= mrp_f:
            offer = cost_f
            flags.append("COST_CLAMPED")
        else:
            # cost > MRP: bad data. offer<=MRP is the hard invariant, so cap at
            # MRP and flag that margin CANNOT be protected here (do not invent).
            offer = mrp_f
            flags.append("COST_ABOVE_MRP")
        clamped = True

    offer = _round2(offer)
    compare_at = _round2(mrp_f)
    eff_pct = _round2((mrp_f - offer) / mrp_f * 100.0) if mrp_f > 0 else 0.0

    # ADVISORY (REUSE the in-store cap invariant to SURFACE, not enforce, an
    # online discount that exceeds the in-store cap -- esp. luxury 2-5% MAP).
    try:
        verdict = evaluate_offer_price(mrp_f, offer, discount_category, brand)
        if not verdict.get("ok") and verdict.get("reason") == "CAP_EXCEEDED":
            flags.append("EXCEEDS_INSTORE_CAP")
    except Exception:  # noqa: BLE001 -- advisory only, never break the compute
        pass

    return {
        "offer": offer,
        "compare_at": compare_at,
        "pct": eff_pct,
        "requested_pct": requested_pct,
        "source": source,
        "flags": flags,
        "clamped": clamped,
    }


# ===========================================================================
# Product-field readers (tolerate BOTH catalog shapes: legacy `pricing.*` and
# the PM/BVI top-level shape)
# ===========================================================================


def _product_mrp(product: Dict[str, Any]) -> Optional[float]:
    pricing = product.get("pricing") or {}
    return _first_pos(product.get("mrp"), pricing.get("mrp"))


def _product_cost(product: Dict[str, Any]) -> Optional[float]:
    pricing = product.get("pricing") or {}
    return _first_pos(product.get("cost_price"), pricing.get("cost_price"))


def _product_discount_category(product: Dict[str, Any]) -> Optional[str]:
    pricing = product.get("pricing") or {}
    return product.get("discount_category") or pricing.get("discount_category")


def _product_brand(product: Dict[str, Any]) -> Optional[str]:
    attrs = product.get("attributes") or {}
    return product.get("brand") or attrs.get("brand_name") or attrs.get("brand")


def _product_sub_brand(product: Dict[str, Any]) -> Optional[str]:
    attrs = product.get("attributes") or {}
    return (
        attrs.get("subbrand")
        or attrs.get("sub_brand")
        or product.get("sub_brand")
        or product.get("subBrand")
    )


def _product_manual_offer(product: Dict[str, Any]) -> Optional[float]:
    ecom = product.get("ecom") or {}
    return _pos_or_none(ecom.get(MANUAL_OFFER_FIELD)) or _pos_or_none(
        product.get(MANUAL_OFFER_FIELD)
    )


def _variant_manual_offer(variant: Dict[str, Any]) -> Optional[float]:
    return _pos_or_none(variant.get(MANUAL_OFFER_FIELD))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# recompute -- compute + (optionally) persist. FAIL-SOFT, never raises.
# ===========================================================================


def _load_rules(db) -> List[Dict[str, Any]]:
    """All rules from ``ecom_discount_rules`` (active filter applied at match
    time). Fail-soft -> []."""
    if db is None:
        return []
    try:
        rows = list(db[RULES_COLLECTION].find({}))
        for r in rows:
            if isinstance(r, dict):
                r.pop("_id", None)
        return rows
    except Exception:  # noqa: BLE001
        return []


def _load_variants(db, product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """catalog_variants for this product (by parent_product_id, then parent_sku).
    Fail-soft -> []."""
    if db is None:
        return []
    pid = product.get("id") or product.get("product_id")
    try:
        coll = db["catalog_variants"]
        rows: List[Dict[str, Any]] = []
        if pid:
            rows = list(coll.find({"parent_product_id": pid}))
        if not rows and product.get("sku"):
            rows = list(coll.find({"parent_sku": product.get("sku")}))
        for r in rows:
            if isinstance(r, dict):
                r.pop("_id", None)
        return rows
    except Exception:  # noqa: BLE001
        return []


def _persist_product_ecom(db, product_id: str, ecom_updates: Dict[str, Any]) -> None:
    """Read-merge-write the catalog_products ``ecom`` sub-doc (preserving siblings
    like shopify_product_id / handle / seo). Mirrors shopify_push._writeback_product
    so it also works on the in-memory MockCollection. Fail-soft."""
    try:
        coll = db["catalog_products"]
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return
        ecom = dict(doc.get("ecom") or {})
        ecom.update(ecom_updates)
        coll.update_one({"id": product_id}, {"$set": {"ecom": ecom}})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ODE] product ecom persist failed %s: %s", product_id, exc)


def _persist_variant(db, sku: str, updates: Dict[str, Any]) -> None:
    """$set the online price fields on a catalog_variants doc (keyed on its unique
    ``sku``). Fail-soft -- a variant write never blocks the rest."""
    try:
        db["catalog_variants"].update_one({"sku": sku}, {"$set": updates})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ODE] variant persist failed %s: %s", sku, exc)


def recompute_online_price(
    product: Dict[str, Any],
    *,
    db=None,
    variants: Optional[List[Dict[str, Any]]] = None,
    rules: Optional[List[Dict[str, Any]]] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Compute + store the ONLINE price for one product (and its variants).

    Called ADDITIVELY on catalog create/update (fail-soft: it must NEVER block a
    save) and by ``recompute_all`` when a rule changes. Mutates the passed
    ``product`` dict's ``ecom`` in place (so the caller sees the result even with
    no DB) and, when ``persist`` + ``db``, writes:
      * catalog_products.ecom.online_offer_price / .online_compare_at_price (+meta)
      * each catalog_variants.discounted_price / .compare_at_price (+meta)
    NEVER touches offer_price (in-store). NEVER raises.

    Returns:
        {"ok": bool, "product_id", "rule_id", "source", "flags",
         "product": <compute result>, "variants": [<per-variant summary>...]}
    """
    try:
        pid = product.get("id") or product.get("product_id")
        mrp = _product_mrp(product)
        if mrp is None:
            # No usable MRP -> nothing to price online. Not an error (a draft may
            # legitimately have no MRP yet); leave everything untouched.
            return {
                "ok": True,
                "product_id": pid,
                "rule_id": None,
                "source": "none",
                "flags": ["NO_MRP"],
                "product": None,
                "variants": [],
            }

        category = product.get("category")
        brand = _product_brand(product)
        sub_brand = _product_sub_brand(product)
        cost = _product_cost(product)
        discount_category = _product_discount_category(product)

        if rules is None:
            rules = _load_rules(db)

        rule = find_matching_rule(rules, category, brand, sub_brand)
        rule_id = None
        pct = 0.0
        if rule is not None:
            rule_id = rule.get("id") or rule.get("rule_id") or rule.get("bvi_rule_id")
            pct = _rule_pct(rule)

        # ---- Product level (record + no-variant fallback the push reads) ----
        product_manual = _product_manual_offer(product)
        prod_res = compute_online_price(
            mrp,
            discount_pct=pct,
            cost_price=cost,
            manual_offer=product_manual,
            discount_category=discount_category,
            brand=brand,
        )
        computed_at = _now_iso()
        ecom_updates = {
            ECOM_OFFER_FIELD: prod_res["offer"],
            ECOM_COMPARE_FIELD: prod_res["compare_at"],
            "online_discount_pct": prod_res["pct"],
            "online_discount_rule_id": rule_id,
            "online_price_source": prod_res["source"],
            "online_price_flags": prod_res["flags"],
            "online_price_computed_at": computed_at,
        }
        # Mutate in place so a DB-less caller (unit test / pre-persist door) sees it.
        ecom = dict(product.get("ecom") or {})
        ecom.update(ecom_updates)
        product["ecom"] = ecom

        if persist and db is not None and pid:
            _persist_product_ecom(db, pid, ecom_updates)

        # ---- Variant level (the primary lever: the push reads these FIRST) ----
        if variants is None:
            variants = _load_variants(db, product)
        variant_summaries: List[Dict[str, Any]] = []
        all_flags = set(prod_res["flags"])
        for v in variants or []:
            v_mrp = _first_pos(v.get("mrp")) or mrp  # variant MRP, else product MRP
            v_manual = _variant_manual_offer(v)
            v_res = compute_online_price(
                v_mrp,
                discount_pct=pct,
                cost_price=cost,  # variant carries no cost; use the product cost
                manual_offer=v_manual,
                discount_category=discount_category,
                brand=brand,
            )
            v[VARIANT_OFFER_FIELD] = v_res["offer"]
            v[VARIANT_COMPARE_FIELD] = v_res["compare_at"]
            v["online_price_meta"] = {
                "source": v_res["source"],
                "rule_id": rule_id,
                "pct": v_res["pct"],
                "flags": v_res["flags"],
                "computed_at": computed_at,
            }
            all_flags.update(v_res["flags"])
            sku = v.get("sku")
            if persist and db is not None and sku:
                _persist_variant(
                    db,
                    sku,
                    {
                        VARIANT_OFFER_FIELD: v_res["offer"],
                        VARIANT_COMPARE_FIELD: v_res["compare_at"],
                        "online_price_meta": v["online_price_meta"],
                    },
                )
            variant_summaries.append(
                {
                    "sku": sku,
                    "offer": v_res["offer"],
                    "compare_at": v_res["compare_at"],
                    "source": v_res["source"],
                    "flags": v_res["flags"],
                }
            )

        return {
            "ok": True,
            "product_id": pid,
            "rule_id": rule_id,
            "source": prod_res["source"],
            "flags": sorted(all_flags),
            "product": prod_res,
            "variants": variant_summaries,
        }
    except Exception as exc:  # noqa: BLE001 -- recompute must NEVER block a save
        logger.warning(
            "[ODE] recompute_online_price failed for %s: %s",
            product.get("id") if isinstance(product, dict) else "?",
            exc,
        )
        return {
            "ok": False,
            "product_id": (product or {}).get("id") if isinstance(product, dict) else None,
            "error": str(exc),
            "product": None,
            "variants": [],
        }


def recompute_all(
    db,
    rule_filter: Optional[Dict[str, Any]] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Recompute online prices across catalog_products (used when a rule changes).

    Loads the rule set ONCE, then recomputes every matching product + its variants.
    Fail-soft PER product (one bad doc never aborts the run). ``rule_filter`` is a
    Mongo query narrowing which catalog_products to touch (e.g. by category/brand
    of the rule that changed) so a single-rule edit need not sweep all ~4.4k docs.

    Returns {"ok", "products", "variants", "errors", "rules"}."""
    if db is None:
        return {"ok": False, "products": 0, "variants": 0, "errors": 0, "error": "no db"}
    rules = _load_rules(db)
    try:
        cursor = db["catalog_products"].find(rule_filter or {})
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "products": 0, "variants": 0, "errors": 0, "error": str(exc)}

    products = 0
    variants = 0
    errors = 0
    for doc in cursor:
        if isinstance(doc, dict):
            doc.pop("_id", None)
        res = recompute_online_price(doc, db=db, rules=rules, persist=True)
        if res.get("ok"):
            products += 1
            variants += len(res.get("variants") or [])
        else:
            errors += 1
        if limit and products >= int(limit):
            break
    return {
        "ok": True,
        "products": products,
        "variants": variants,
        "errors": errors,
        "rules": len(rules),
    }
