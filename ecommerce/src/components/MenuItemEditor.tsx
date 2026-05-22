"use client";

import { useEffect, useState } from "react";
import {
  Loader2,
  Search,
  AlertCircle,
  CheckCircle,
  Save,
  ExternalLink,
  X,
  Trash2,
} from "lucide-react";

// ─── Types ─────────────────────────────────────────────

export interface MenuItemRecord {
  id: string;
  shopifyItemId: string | null;
  parentId: string | null;
  position: number;
  title: string;
  itemType: string;
  url: string | null;
  resourceId: string | null;
  tagsFilter: string | null;
  iconUrl: string | null;
  bannerUrl: string | null;
  badgeText: string | null;
  badgeColor: string | null;
  pinnedToTop: boolean;
}

interface MenuItemEditorProps {
  menuId: string;
  item: MenuItemRecord | null;
  /** Re-fetch the menu tree after a save so the parent stays in sync. */
  onSaved: () => void;
  onDeleted: () => void;
}

const ITEM_TYPES: Array<{ value: string; label: string; needsResource: boolean }> = [
  { value: "COLLECTION", label: "Collection", needsResource: true },
  { value: "COLLECTIONS", label: "All Collections", needsResource: false },
  { value: "PRODUCT", label: "Product", needsResource: true },
  { value: "PAGE", label: "Page", needsResource: true },
  { value: "BLOG", label: "Blog", needsResource: true },
  { value: "ARTICLE", label: "Article", needsResource: true },
  { value: "FRONTPAGE", label: "Home", needsResource: false },
  { value: "CATALOG", label: "Catalog", needsResource: false },
  { value: "SEARCH", label: "Search", needsResource: false },
  { value: "HTTP", label: "External link (URL)", needsResource: false },
  { value: "SHOP_POLICY", label: "Shop policy", needsResource: true },
  { value: "METAOBJECT", label: "Metaobject", needsResource: true },
];

// Hard-coded page resources per the M5/M11 implication note. The
// autoCollectionHandlesFor / Shopify page list is unstable enough
// that we keep the most-used pages here so the user can pick one
// without an extra API. New pages can be added inline once the
// pages picker hits the backlog.
const KNOWN_PAGES: Array<{ handle: string; title: string }> = [
  { handle: "warranty", title: "Warranty" },
  { handle: "contact-us", title: "Contact Us" },
  { handle: "faq-frequently-asked-questions", title: "FAQ — Frequently Asked Questions" },
  { handle: "terms-conditions", title: "Terms & Conditions" },
  { handle: "privacy-policy", title: "Privacy Policy" },
  { handle: "shipping-policy", title: "Shipping Policy" },
  { handle: "refund-policy", title: "Refund Policy" },
  { handle: "about-us", title: "About Us" },
  { handle: "store-locator", title: "Store Locator" },
  { handle: "book-eye-test", title: "Book an Eye Test" },
];

// ─── Picker for COLLECTION / PRODUCT — debounced fetch ─

interface PickerProps {
  resourceType: "COLLECTION" | "PRODUCT";
  value: string;
  onChange: (resourceId: string, label: string) => void;
}

interface PickerHit {
  id: string;
  shopifyId: string | null;
  label: string;
  sub: string | null;
}

function ResourcePicker({ resourceType, value, onChange }: PickerProps) {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<PickerHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (search.length === 0 && results.length > 0) return;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const path =
          resourceType === "COLLECTION"
            ? `/api/collections?limit=20&search=${encodeURIComponent(search)}`
            : `/api/products?limit=20&search=${encodeURIComponent(search)}`;
        const res = await fetch(path);
        const json = await res.json();
        if (!json.success) {
          setResults([]);
          return;
        }
        const hits: PickerHit[] = (json.data || []).map((d: any) => {
          if (resourceType === "COLLECTION") {
            return {
              id: d.id,
              shopifyId: d.shopifyCollectionId || null,
              label: d.title,
              sub: d.handle || null,
            };
          }
          return {
            id: d.id,
            shopifyId: d.shopifyProductId || null,
            label: d.title || d.brand + " " + (d.modelNo || ""),
            sub: d.sku || d.brand || null,
          };
        });
        setResults(hits);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [search, resourceType, open, results.length]);

  return (
    <div className="relative">
      <div className="flex gap-1">
        <input
          type="text"
          readOnly
          value={value}
          placeholder={`Pick a ${resourceType.toLowerCase()}...`}
          className="flex-1 px-2 py-1 text-xs font-mono bg-slate-50 border border-slate-300 rounded"
        />
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="px-2 py-1 text-xs border border-slate-300 rounded text-slate-700 hover:bg-slate-100"
        >
          {open ? "Close" : "Browse"}
        </button>
      </div>
      {open && (
        <div className="absolute z-10 mt-1 w-full bg-white border border-slate-300 rounded shadow-lg max-h-72 overflow-y-auto">
          <div className="p-2 border-b border-slate-200 sticky top-0 bg-white">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search ${resourceType.toLowerCase()}s...`}
                className="w-full pl-7 pr-2 py-1 text-xs border border-slate-300 rounded"
                autoFocus
              />
            </div>
          </div>
          {loading && (
            <div className="p-3 text-xs text-slate-500 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" />
              Loading...
            </div>
          )}
          {!loading && results.length === 0 && (
            <div className="p-3 text-xs text-slate-500">No results</div>
          )}
          {!loading &&
            results.map((r) => (
              <button
                key={r.id}
                type="button"
                onClick={() => {
                  if (!r.shopifyId) {
                    alert(
                      `${r.label} hasn't been pushed to Shopify yet — pick another resource or push it first.`
                    );
                    return;
                  }
                  onChange(r.shopifyId, r.label);
                  setOpen(false);
                }}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100 border-t border-slate-100"
              >
                <div className="font-medium text-slate-900 truncate">
                  {r.label}
                </div>
                {r.sub && (
                  <div className="text-slate-500 text-[10px] font-mono truncate">
                    {r.sub}
                  </div>
                )}
                {!r.shopifyId && (
                  <div className="text-orange-600 text-[10px]">
                    not on Shopify
                  </div>
                )}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

// ─── Main editor panel ─────────────────────────────────

export default function MenuItemEditor({
  menuId,
  item,
  onSaved,
  onDeleted,
}: MenuItemEditorProps) {
  const [draft, setDraft] = useState<MenuItemRecord | null>(item);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setDraft(item);
    setError(null);
    setSaved(false);
  }, [item]);

  if (!draft) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500 p-6">
        Select an item from the tree to edit it, or click "Add item" above.
      </div>
    );
  }

  const typeMeta = ITEM_TYPES.find((t) => t.value === draft.itemType);
  const needsResource = typeMeta?.needsResource ?? false;

  const update = <K extends keyof MenuItemRecord>(
    key: K,
    value: MenuItemRecord[K]
  ) => {
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/menus/${menuId}/items/${draft.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: draft.title,
          itemType: draft.itemType,
          url: draft.url,
          resourceId: draft.resourceId,
          tagsFilter: draft.tagsFilter,
          iconUrl: draft.iconUrl,
          bannerUrl: draft.bannerUrl,
          badgeText: draft.badgeText,
          badgeColor: draft.badgeColor,
          pinnedToTop: draft.pinnedToTop,
        }),
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error || "Failed to save");
        return;
      }
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete "${draft.title}" and all its children?`)) return;
    try {
      const res = await fetch(`/api/menus/${menuId}/items/${draft.id}`, {
        method: "DELETE",
      });
      const json = await res.json();
      if (!json.success) {
        setError(json.error || "Failed to delete");
        return;
      }
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-900">Edit menu item</h3>
        <button
          type="button"
          onClick={handleDelete}
          className="inline-flex items-center gap-1 text-xs text-red-600 hover:underline"
        >
          <Trash2 className="w-3 h-3" />
          Delete
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-800 flex items-center gap-1">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}
      {saved && !error && (
        <div className="bg-emerald-50 border border-emerald-200 rounded p-2 text-xs text-emerald-800 flex items-center gap-1">
          <CheckCircle className="w-3.5 h-3.5" />
          Saved. Push to Shopify when ready.
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-slate-600 mb-1">
          Title
        </label>
        <input
          type="text"
          value={draft.title}
          onChange={(e) => update("title", e.target.value)}
          className="w-full px-2 py-1 border border-slate-300 rounded text-sm"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-600 mb-1">
          Type
        </label>
        <select
          value={draft.itemType}
          onChange={(e) => {
            update("itemType", e.target.value);
            // Clear resourceId/url when the type changes since they
            // belong to the previous type.
            if (e.target.value !== "HTTP") update("url", null);
            const newMeta = ITEM_TYPES.find((t) => t.value === e.target.value);
            if (!newMeta?.needsResource) update("resourceId", null);
          }}
          className="w-full px-2 py-1 border border-slate-300 rounded text-sm"
        >
          {ITEM_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      {/* HTTP type → free-form URL */}
      {draft.itemType === "HTTP" && (
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            URL
          </label>
          <input
            type="url"
            value={draft.url || ""}
            onChange={(e) => update("url", e.target.value || null)}
            placeholder="https://..."
            className="w-full px-2 py-1 border border-slate-300 rounded text-sm font-mono"
          />
        </div>
      )}

      {/* COLLECTION / PRODUCT → resource picker */}
      {(draft.itemType === "COLLECTION" || draft.itemType === "PRODUCT") && (
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            {draft.itemType === "COLLECTION" ? "Collection" : "Product"}
          </label>
          <ResourcePicker
            resourceType={draft.itemType}
            value={draft.resourceId || ""}
            onChange={(rid, label) => {
              update("resourceId", rid);
              if (!draft.title || draft.title === "(untitled)") {
                update("title", label);
              }
            }}
          />
          {draft.resourceId && (
            <button
              type="button"
              onClick={() => update("resourceId", null)}
              className="text-[11px] text-slate-500 mt-1 inline-flex items-center gap-1 hover:text-slate-800"
            >
              <X className="w-3 h-3" /> Clear
            </button>
          )}
        </div>
      )}

      {/* PAGE → drop-down of known handles */}
      {draft.itemType === "PAGE" && (
        <div>
          <label className="block text-xs font-medium text-slate-600 mb-1">
            Page
          </label>
          <select
            value={draft.resourceId || ""}
            onChange={(e) => {
              const handle = e.target.value;
              update("resourceId", handle ? `gid://shopify/Page/${handle}` : null);
              const known = KNOWN_PAGES.find((p) => p.handle === handle);
              if (known && (!draft.title || draft.title === "(untitled)")) {
                update("title", known.title);
              }
            }}
            className="w-full px-2 py-1 border border-slate-300 rounded text-sm"
          >
            <option value="">Select a page...</option>
            {KNOWN_PAGES.map((p) => (
              <option key={p.handle} value={p.handle}>
                {p.title}
              </option>
            ))}
          </select>
          <p className="text-[11px] text-slate-500 mt-1">
            Shopify resolves the handle to the live page on push. If your store
            has a page not in this list, paste the handle into the URL field
            directly.
          </p>
          <input
            type="text"
            value={draft.resourceId || ""}
            onChange={(e) => update("resourceId", e.target.value || null)}
            placeholder="gid://shopify/Page/your-page-handle"
            className="w-full mt-1 px-2 py-1 border border-slate-300 rounded text-xs font-mono"
          />
        </div>
      )}

      {needsResource &&
        draft.itemType !== "COLLECTION" &&
        draft.itemType !== "PRODUCT" &&
        draft.itemType !== "PAGE" && (
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              Resource ID (Shopify GID)
            </label>
            <input
              type="text"
              value={draft.resourceId || ""}
              onChange={(e) => update("resourceId", e.target.value || null)}
              placeholder="gid://shopify/..."
              className="w-full px-2 py-1 border border-slate-300 rounded text-xs font-mono"
            />
          </div>
        )}

      <div>
        <label className="block text-xs font-medium text-slate-600 mb-1">
          Tags filter (comma-separated)
        </label>
        <input
          type="text"
          value={draft.tagsFilter || ""}
          onChange={(e) => update("tagsFilter", e.target.value || null)}
          placeholder="brand_rayban,gender_men"
          className="w-full px-2 py-1 border border-slate-300 rounded text-xs font-mono"
        />
        <p className="text-[11px] text-slate-500 mt-1">
          Storefront filters this collection by tags when set. Leave empty for
          unfiltered.
        </p>
      </div>

      <div className="border-t border-slate-200 pt-3 mt-3">
        <h4 className="text-xs font-semibold text-slate-700 mb-2">
          Mega menu (optional)
        </h4>

        <div className="space-y-2">
          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">
              Icon URL (small thumbnail)
            </label>
            <input
              type="url"
              value={draft.iconUrl || ""}
              onChange={(e) => update("iconUrl", e.target.value || null)}
              placeholder="https://cdn.shopify.com/..."
              className="w-full px-2 py-1 border border-slate-300 rounded text-xs"
            />
            {draft.iconUrl && (
              <div className="mt-1 inline-block">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={draft.iconUrl}
                  alt="icon preview"
                  className="w-10 h-10 rounded border border-slate-200 object-cover"
                />
              </div>
            )}
          </div>

          <div>
            <label className="block text-[11px] font-medium text-slate-600 mb-1">
              Banner URL (mega-menu card)
            </label>
            <input
              type="url"
              value={draft.bannerUrl || ""}
              onChange={(e) => update("bannerUrl", e.target.value || null)}
              placeholder="https://cdn.shopify.com/..."
              className="w-full px-2 py-1 border border-slate-300 rounded text-xs"
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">
                Badge text
              </label>
              <input
                type="text"
                value={draft.badgeText || ""}
                onChange={(e) => update("badgeText", e.target.value || null)}
                placeholder="NEW / SALE"
                maxLength={12}
                className="w-full px-2 py-1 border border-slate-300 rounded text-xs"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-slate-600 mb-1">
                Badge color
              </label>
              <input
                type="color"
                value={draft.badgeColor || "#dc2626"}
                onChange={(e) => update("badgeColor", e.target.value)}
                className="w-full h-7 border border-slate-300 rounded"
              />
            </div>
          </div>
        </div>
      </div>

      <div className="border-t border-slate-200 pt-3">
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input
            type="checkbox"
            checked={draft.pinnedToTop}
            onChange={(e) => update("pinnedToTop", e.target.checked)}
          />
          Pin to top within parent (round 2 M11 — top categories)
        </label>
      </div>

      <div className="flex items-center justify-between gap-2 pt-2 border-t border-slate-200">
        <a
          href={
            draft.itemType === "HTTP" && draft.url
              ? draft.url
              : draft.resourceId
                ? `https://bokaro-better-vision.myshopify.com/admin/${draft.resourceId.replace(
                    "gid://shopify/",
                    ""
                  )}`
                : "#"
          }
          target="_blank"
          rel="noreferrer"
          className="text-[11px] text-slate-500 inline-flex items-center gap-1 hover:text-slate-800"
        >
          <ExternalLink className="w-3 h-3" />
          Open resource
        </a>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Save className="w-3.5 h-3.5" />
          )}
          Save
        </button>
      </div>
    </div>
  );
}
