// Tag-casing migration — fixes the live Shopify smart-collection rules
// to match the canonical lowercase + hyphenated tag format that the app
// emits via slugifyTagValue() in src/lib/categoryAttributes.ts.
//
// Why — round 1 audit A12: the live store has rule conditions in mixed
// or Title Case (e.g. "casecolor_Rose Gold", "gender_Women",
// "shape_Round") while new products pushed from this app emit
// lowercase + hyphens (e.g. "casecolor_rose-gold", "gender_women").
// The mismatch means new products won't land in old collections.
//
// Round 2 user decision (C6): "Both — write a migration". This module
// is Phase 1 of that — bring the COLLECTION RULE conditions into line
// with the canonical app-emitted format. Phase 2 (re-tagging existing
// products) is a separate bulk operation, documented in the README at
// the bottom of this file.
//
// Strategy — for every smart collection rule:
//   • column !== "TAG" → leave alone (TYPE/VENDOR/PRICE/etc. don't slug)
//   • column === "TAG" → split condition on first "_" into prefix + value;
//     slugify the value to lowercase + hyphens, recompose. If condition
//     has no "_" (rare — implies a non-prefixed tag), slugify the whole
//     condition.
//
// The migration is structured as build-plan + commit, so the merchant
// can review every change before it touches Shopify.

import { makeGraphQLRequest } from "@/lib/shopify";

export interface RuleEntry {
  column: string;     // TAG, TYPE, VENDOR, PRICE, COMPARE_AT_PRICE, ...
  relation: string;   // EQUALS, CONTAINS, STARTS_WITH, ...
  condition: string;
}

export interface CollectionRulesEntry {
  shopifyCollectionId: string;     // gid://shopify/Collection/...
  title: string;
  handle: string;
  productsCount: number;
  appliedDisjunctively: boolean;
  oldRules: RuleEntry[];
  newRules: RuleEntry[];
  /** Number of rule conditions that changed in this collection. */
  changedConditions: number;
}

export interface MigrationPlan {
  generatedAt: string;
  totalCollections: number;
  smartCollections: number;
  collectionsWithChanges: number;
  totalRuleChanges: number;
  /** Only collections that have at least one change. Unchanged ones are
   *  filtered out so the plan is small enough to review by hand. */
  entries: CollectionRulesEntry[];
}

export interface MigrationResult extends MigrationPlan {
  committed: boolean;
  applied: number;
  failed: Array<{
    shopifyCollectionId: string;
    title: string;
    error: string;
  }>;
}

/* ------------------------------------------------------------------
 * Slug helper — must match slugifyTagValue() in categoryAttributes.ts
 * exactly. Duplicated here to avoid the migration depending on the
 * attribute registry (keeps it standalone-runnable).
 * ------------------------------------------------------------------ */
function slugifyForRule(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Convert a single rule condition to canonical form. Returns the new
 *  condition (which may be identical to the input if no change needed). */
export function migrateCondition(rule: RuleEntry): string {
  if (rule.column !== "TAG") return rule.condition;
  const trimmed = rule.condition.trim();
  if (!trimmed) return trimmed;

  // Tags follow "<prefix>_<value>" (e.g. "casecolor_Rose Gold").
  // If no underscore, slug the whole thing.
  const sep = trimmed.indexOf("_");
  if (sep === -1) {
    return slugifyForRule(trimmed);
  }
  const prefix = trimmed.slice(0, sep);
  const value = trimmed.slice(sep + 1);
  const newPrefix = slugifyForRule(prefix);
  const newValue = slugifyForRule(value);
  if (!newPrefix) return trimmed; // pathological — leave alone
  if (!newValue) return newPrefix; // value reduced to empty — keep prefix
  return `${newPrefix}_${newValue}`;
}

/** Compute new rule set + count of changes for a collection. */
function migrateRules(rules: RuleEntry[]): { newRules: RuleEntry[]; changes: number } {
  let changes = 0;
  const newRules: RuleEntry[] = rules.map((r) => {
    const newCond = migrateCondition(r);
    if (newCond !== r.condition) changes++;
    return { column: r.column, relation: r.relation, condition: newCond };
  });
  return { newRules, changes };
}

/* ------------------------------------------------------------------
 * Plan builder — pulls every smart collection from Shopify, computes
 * the new rule set, and returns only those where something would
 * change.
 * ------------------------------------------------------------------ */
const FETCH_COLLECTIONS_QUERY = `
  query MigrationFetchCollections($cursor: String) {
    collections(first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          id
          title
          handle
          productsCount { count }
          ruleSet {
            appliedDisjunctively
            rules { column relation condition }
          }
        }
      }
    }
  }
`;

type FetchCollectionsResp = {
  collections: {
    pageInfo: { hasNextPage: boolean; endCursor: string | null };
    edges: Array<{
      node: {
        id: string;
        title: string;
        handle: string;
        productsCount: { count: number } | null;
        ruleSet: {
          appliedDisjunctively: boolean;
          rules: RuleEntry[];
        } | null;
      };
    }>;
  };
};

export async function buildMigrationPlan(): Promise<MigrationPlan> {
  const entries: CollectionRulesEntry[] = [];
  let cursor: string | null = null;
  let totalCollections = 0;
  let smartCollections = 0;

  // Paginate through the entire collection list.
  for (;;) {
    const resp: { success: boolean; data?: FetchCollectionsResp; error?: string } =
      await makeGraphQLRequest<FetchCollectionsResp>(FETCH_COLLECTIONS_QUERY, { cursor });
    const page = resp.data?.collections;
    if (!page) break;

    for (const edge of page.edges) {
      const node = edge.node;
      totalCollections++;
      // ruleSet is null for custom (manual) collections — they have no
      // tag rules to migrate.
      if (!node.ruleSet || !Array.isArray(node.ruleSet.rules)) continue;
      smartCollections++;

      const { newRules, changes } = migrateRules(node.ruleSet.rules);
      if (changes === 0) continue;

      entries.push({
        shopifyCollectionId: node.id,
        title: node.title,
        handle: node.handle,
        productsCount: node.productsCount?.count ?? 0,
        appliedDisjunctively: node.ruleSet.appliedDisjunctively,
        oldRules: node.ruleSet.rules,
        newRules,
        changedConditions: changes,
      });
    }

    if (!page.pageInfo.hasNextPage || !page.pageInfo.endCursor) break;
    cursor = page.pageInfo.endCursor;
  }

  const totalRuleChanges = entries.reduce((n, e) => n + e.changedConditions, 0);

  return {
    generatedAt: new Date().toISOString(),
    totalCollections,
    smartCollections,
    collectionsWithChanges: entries.length,
    totalRuleChanges,
    entries,
  };
}

/* ------------------------------------------------------------------
 * Commit — applies a previously-built plan via collectionUpdate.
 * Plans must be re-built right before commit (Shopify state may have
 * shifted between plan + commit).
 * ------------------------------------------------------------------ */
const UPDATE_COLLECTION_MUTATION = `
  mutation MigrationUpdateCollectionRules($input: CollectionInput!) {
    collectionUpdate(input: $input) {
      collection { id }
      userErrors { field message }
    }
  }
`;

export async function commitMigration(plan: MigrationPlan): Promise<MigrationResult> {
  const failed: Array<{ shopifyCollectionId: string; title: string; error: string }> = [];
  let applied = 0;

  type UpdateResp = {
    collectionUpdate: {
      collection: { id: string } | null;
      userErrors: Array<{ field: string[] | null; message: string }>;
    };
  };
  for (const entry of plan.entries) {
    try {
      const resp = await makeGraphQLRequest<UpdateResp>(UPDATE_COLLECTION_MUTATION, {
        input: {
          id: entry.shopifyCollectionId,
          ruleSet: {
            appliedDisjunctively: entry.appliedDisjunctively,
            rules: entry.newRules.map((r) => ({
              column: r.column,
              relation: r.relation,
              condition: r.condition,
            })),
          },
        },
      });

      const errs = resp.data?.collectionUpdate.userErrors ?? [];
      if (errs.length > 0) {
        failed.push({
          shopifyCollectionId: entry.shopifyCollectionId,
          title: entry.title,
          error: errs.map((e) => `${(e.field || []).join(".")} → ${e.message}`).join("; "),
        });
        continue;
      }
      applied++;
    } catch (e) {
      failed.push({
        shopifyCollectionId: entry.shopifyCollectionId,
        title: entry.title,
        error: e instanceof Error ? e.message : "Unknown error",
      });
    }
  }

  return {
    ...plan,
    committed: true,
    applied,
    failed,
  };
}

/* ------------------------------------------------------------------
 * Phase 2 (NOT in this file — documented for follow-up):
 *
 * After Phase 1 runs, existing products tagged with the OLD casing
 * (e.g. "gender_Women") will no longer match the migrated rules
 * ("gender_women"). Two options:
 *
 *   a) Bulk product retag — run a Shopify bulkOperationRunMutation
 *      with productUpdate to lowercase every product's tags. Touches
 *      ~4,400 products; takes 30-60 min. Owners must accept the
 *      audit-log noise.
 *
 *   b) Local-only fix — update our PUSH path so that newly-pushed
 *      products emit BOTH the new lowercase tag AND the legacy
 *      Title-Case tag. Doubles tag count per product (Shopify limit
 *      250). Avoids touching existing products. Acceptable until tag
 *      count climbs past ~120/product.
 *
 * Phase 1 lands first because it's reversible (re-running
 * commitMigration with the inverse plan would restore old casing).
 * Phase 2 is staged separately.
 * ------------------------------------------------------------------ */
