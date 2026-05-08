"use client";

// Edit Product redesign — new shell. Renders behind ?next=1 from
// /dashboard/products/edit/[id]. The existing page is untouched and
// stays as the fallback until we flag-flip per migration step 6.
//
// What's wired in this revision (Migration steps 1-3 + foundation of 4):
//   ✓ Polaris-aligned layout: TopBar + SectionNav + 200/1fr/320 grid + right rail
//   ✓ Auto-save (1.2s debounce → PUT /api/products/[id]) with SaveIndicator
//   ✓ Keyboard contract: ⌘S (save), ⌘↵ (publish), ⌘K (palette), P (preview), Esc
//   ✓ getIssues() drives top-bar counter, right-rail panel, publish gate
//   ✓ Per-category nav via CAT_SPECS — sections appear/disappear by category
//   ✓ ChipGroup for product type, scroll-spy active state
//   ✓ Storefront preview modal (lightweight)
//   ✓ Right rail: Storefront preview · Auto-derived (click-to-copy) · Tags · Issues · Activity placeholder
//
// Not yet wired (deferred):
//   • Inventory table redesign (still uses location number inputs)
//   • Image roles grid (still flat thumbnails)
//   • CommandPalette extensions (uses existing global ⌘K)
//   • AI describe button
//   • react-hook-form (kept controlled state for now to minimise churn)

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Upload, X, AlertCircle, ChevronRight } from "lucide-react";
import SearchableDropdown from "@/components/SearchableDropdown";
import { CATEGORIES as CATEGORY_DEFS } from "@/lib/categories";
import { isAttrApplicable, attributesForCategory } from "@/lib/categoryAttributes";
import {
  generateTitle,
  generateSKU,
  generateSEOTitle,
  generateSEODescription,
  generatePageUrl,
  generateTags,
} from "@/lib/autoGenerate";
import { CAT_SPECS, navForCategory } from "@/lib/products/categorySpecs";
import { getIssues, blockingIssues, type Issue } from "@/lib/products/validation";
import {
  TopBar,
  SectionNav,
  Section,
  Field,
  Row,
  ChipGroup,
  TextInput,
  CurrencyInput,
  RailGroup,
  RailRow,
  EP_TOKENS_CSS,
  useScrollSpy,
  type SaveState,
  type NavItem,
} from "./primitives";

// Match the existing edit page's form shape so the PUT contract doesn't change.
interface FormData {
  category: string;
  brand: string;
  subBrand: string;
  label: string;
  productName: string;
  modelNo: string;
  shape: string;
  frameMaterial: string;
  templeMaterial: string;
  frameType: string;
  lensColour: string;
  tint: string;
  lensMaterial: string;
  lensUSP: string;
  polarization: string;
  uvProtection: string;
  // Reading / safety / contact / watch — extended per category.
  power: string;
  impactRating: string;
  baseCurve: string;
  movement: string;
  recommendedFor: string;
  instructions: string;
  ingredients: string;
  benefits: string;
  aboutProduct: string;
  gender: string;
  countryOfOrigin: string;
  warranty: string;
  productUSP: string;
  mrp: string;
  gtin: string;
  upc: string;
  images: string[];
  stockByLocation: Record<string, number>;
}

const EMPTY_FORM: FormData = {
  category: "",
  brand: "",
  subBrand: "",
  label: "",
  productName: "",
  modelNo: "",
  shape: "",
  frameMaterial: "",
  templeMaterial: "",
  frameType: "",
  lensColour: "",
  tint: "",
  lensMaterial: "",
  lensUSP: "",
  polarization: "",
  uvProtection: "",
  power: "",
  impactRating: "",
  baseCurve: "",
  movement: "",
  recommendedFor: "",
  instructions: "",
  ingredients: "",
  benefits: "",
  aboutProduct: "",
  gender: "",
  countryOfOrigin: "",
  warranty: "",
  productUSP: "",
  mrp: "",
  gtin: "",
  upc: "",
  images: [],
  stockByLocation: {},
};

interface AttributeType {
  id: string;
  name: string;
  options: Array<{ id: string; value: string }>;
}

interface Location {
  id: string;
  name: string;
}

const CATEGORY_ICONS: Record<string, string> = {
  SPECTACLES: "👓",
  SUNGLASSES: "🕶️",
  CLIP_ON_FRAMES: "🧷",
  READING_GLASSES: "📖",
  COMPUTER_GLASSES: "💻",
  SAFETY_GLASSES: "⚠️",
  CONTACT_LENSES: "👁️",
  SMARTGLASSES: "🤖",
  WATCHES: "⌚",
  SMARTWATCHES: "⏱️",
  ACCESSORIES: "🧰",
};

export default function EditProductV2({ productId }: { productId: string }) {
  const router = useRouter();

  // ---------- Data ----------
  const [fetching, setFetching] = useState(true);
  const [productStatus, setProductStatus] = useState<string>("DRAFT");
  const [shopifyProductId, setShopifyProductId] = useState<string | null>(null);
  const [attributes, setAttributes] = useState<AttributeType[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [extraAttrs, setExtraAttrs] = useState<Record<string, string>>({});
  const [formData, setFormData] = useState<FormData>(EMPTY_FORM);

  // ---------- Save state ----------
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [savedAt, setSavedAt] = useState<number | undefined>(undefined);
  const [saveError, setSaveError] = useState<string | undefined>(undefined);
  const dirtyRef = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initialLoadRef = useRef(true);

  // ---------- UI state ----------
  const [activeSec, setActiveSec] = useState("identity");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);

  // ---------- Initial fetch ----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [pRes, aRes, lRes] = await Promise.all([
          fetch(`/api/products/${productId}`),
          fetch("/api/attributes"),
          fetch("/api/locations"),
        ]);
        if (cancelled) return;
        const pJson = await pRes.json();
        const aJson = await aRes.json();
        const lJson = await lRes.json();

        const p = pJson.data || pJson;
        if (p) {
          setProductStatus((p.status as string) || "DRAFT");
          setShopifyProductId((p.shopifyProductId as string) || null);
          setFormData({
            category: p.category || "",
            brand: p.brand || "",
            subBrand: p.subBrand || "",
            label: p.label || "",
            productName: p.productName || "",
            modelNo: p.modelNo || p.fullModelNo || "",
            shape: p.shape || "",
            frameMaterial: p.frameMaterial || "",
            templeMaterial: p.templeMaterial || "",
            frameType: p.frameType || "",
            lensColour: p.lensColour || "",
            tint: p.tint || "",
            lensMaterial: p.lensMaterial || "",
            lensUSP: p.lensUSP || "",
            polarization: p.polarization || "",
            uvProtection: p.uvProtection || "",
            power: p.power || "",
            impactRating: p.impactRating || "",
            baseCurve: p.baseCurve || "",
            movement: p.movement || "",
            recommendedFor: p.recommendedFor || "",
            instructions: p.instructions || "",
            ingredients: p.ingredients || "",
            benefits: p.benefits || "",
            aboutProduct: p.aboutProduct || "",
            gender: p.gender || "",
            countryOfOrigin: p.countryOfOrigin || "",
            warranty: p.warranty || "",
            productUSP: p.productUSP || "",
            mrp: p.mrp ? String(p.mrp) : "",
            gtin: p.gtin || "",
            upc: p.upc || "",
            images: (p.images || []).map((img: { url?: string } | string) =>
              typeof img === "string" ? img : img?.url || ""
            ).filter(Boolean),
            stockByLocation: Object.fromEntries(
              (p.locations || []).map((pl: { locationId: string; quantity: number }) => [pl.locationId, pl.quantity])
            ),
          });
        }
        setAttributes(Array.isArray(aJson) ? aJson : aJson.data || []);
        setLocations(Array.isArray(lJson) ? lJson : lJson.data || []);
      } catch (e) {
        console.error("Failed to load product", e);
      } finally {
        if (!cancelled) setFetching(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productId]);

  // ---------- Auto-save (debounced 1.2s on dirty) ----------
  const saveDraft = useCallback(
    async (status?: "DRAFT" | "PUBLISHED") => {
      setSaveState("saving");
      setSaveError(undefined);
      try {
        const payload: Record<string, unknown> = {
          category: formData.category,
          brand: formData.brand,
          subBrand: formData.subBrand,
          fullModelNo: formData.modelNo,
          productName: formData.productName,
          modelNo: formData.modelNo,
          label: formData.label,
          shape: formData.shape,
          frameMaterial: formData.frameMaterial,
          templeMaterial: formData.templeMaterial,
          frameType: formData.frameType,
          lensMaterial: formData.lensMaterial,
          lensUSP: formData.lensUSP,
          lensColour: formData.lensColour,
          tint: formData.tint,
          polarization: formData.polarization,
          uvProtection: formData.uvProtection,
          warranty: formData.warranty,
          gender: formData.gender,
          countryOfOrigin: formData.countryOfOrigin,
          productUSP: formData.productUSP,
          gtin: formData.gtin,
          upc: formData.upc,
          mrp: parseFloat(formData.mrp || "0") || 0,
          status: status || productStatus,
          images: formData.images.map((url) => ({ url })),
          locations: Object.entries(formData.stockByLocation).map(([locationId, quantity]) => ({
            locationId,
            quantity,
          })),
        };
        // Flatten dynamic category attrs so generateTags picks them up.
        for (const [k, v] of Object.entries(extraAttrs)) {
          if (v !== undefined && v !== null && String(v).trim() !== "") {
            payload[k] = v;
          }
        }
        const res = await fetch(`/api/products/${productId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const j = await res.json().catch(() => ({} as Record<string, unknown>));
        if (!res.ok) {
          const msg = (j && (j as { message?: string }).message) || `HTTP ${res.status}`;
          throw new Error(msg);
        }
        if (status) setProductStatus(status);
        setSaveState("saved");
        setSavedAt(Date.now());
        dirtyRef.current = false;
        // Soft notice if the local save worked but Shopify rejected.
        const shopifyError = (j as { shopifyError?: string }).shopifyError;
        if (shopifyError) {
          setSaveError(`Shopify: ${shopifyError}`);
          setSaveState("error");
        }
      } catch (e) {
        setSaveState("error");
        setSaveError(e instanceof Error ? e.message : "Save failed");
      }
    },
    [formData, extraAttrs, productId, productStatus]
  );

  // Watch form for changes → debounce save.
  useEffect(() => {
    if (initialLoadRef.current) {
      // Skip the first render after data loads.
      if (!fetching) initialLoadRef.current = false;
      return;
    }
    dirtyRef.current = true;
    setSaveState("dirty");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveDraft();
    }, 1200);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData, extraAttrs]);

  // ---------- Issues ----------
  const issues: Issue[] = useMemo(
    () => getIssues({ ...formData, images: formData.images }),
    [formData]
  );
  const blocking = useMemo(() => blockingIssues(issues), [issues]);
  const issuesBySection = useMemo(() => {
    const m: Record<string, number> = {};
    for (const i of issues) m[i.section] = (m[i.section] || 0) + 1;
    return m;
  }, [issues]);

  // ---------- Derived ----------
  const derivedTitle = useMemo(() => generateTitle(formData), [formData]);
  const derivedSku = useMemo(() => generateSKU(formData), [formData]);
  const derivedSeoTitle = useMemo(() => generateSEOTitle(formData), [formData]);
  const derivedSeoDesc = useMemo(() => generateSEODescription(formData), [formData]);
  const derivedHandle = useMemo(() => generatePageUrl(formData), [formData]);
  const derivedTags = useMemo(() => generateTags({ ...formData, ...extraAttrs }), [formData, extraAttrs]);

  // ---------- Nav ----------
  const navSpec = useMemo(() => navForCategory(formData.category), [formData.category]);
  const navItems: NavItem[] = useMemo(
    () =>
      navSpec.map((s) => ({
        id: s.id,
        label: s.label,
        hint: s.hint,
        issues: issuesBySection[s.id] || 0,
      })),
    [navSpec, issuesBySection]
  );

  // Scroll-spy
  useScrollSpy(navSpec.map((s) => s.id));
  useEffect(() => {
    const onSpy = (e: Event) => setActiveSec((e as CustomEvent<string>).detail);
    window.addEventListener("ep-scroll-spy", onSpy);
    return () => window.removeEventListener("ep-scroll-spy", onSpy);
  }, []);

  const jumpTo = useCallback((id: string) => {
    const el = document.getElementById(`sec-${id}`);
    if (el) {
      const top = el.getBoundingClientRect().top + window.scrollY - 80;
      window.scrollTo({ top, behavior: "smooth" });
      setActiveSec(id);
    }
  }, []);

  // ---------- Keyboard contract ----------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const inField = isInField(e.target);
      const cmd = e.metaKey || e.ctrlKey;
      // ⌘S — save draft
      if (cmd && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        saveDraft();
        return;
      }
      // ⌘↵ — publish (only if no blocking issues)
      if (cmd && e.key === "Enter") {
        e.preventDefault();
        if (blocking.length === 0) saveDraft("PUBLISHED");
        return;
      }
      // ⌘K — handled by global CommandPalette already (sidebar wires it)
      if (cmd && (e.key === "k" || e.key === "K")) {
        // let the global handler take it; we just mark intent
        return;
      }
      // P — preview (no input focused)
      if (!cmd && !inField && (e.key === "p" || e.key === "P")) {
        e.preventDefault();
        setPreviewOpen(true);
        return;
      }
      // Esc — close preview
      if (e.key === "Escape" && previewOpen) {
        setPreviewOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveDraft, blocking.length, previewOpen]);

  // ---------- Image upload (NB: same response-shape bug as old page —
  // fix is gated on Q1 of the audit; not changing here. ---------
  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    setUploadingImage(true);
    try {
      const uploaded: string[] = [];
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        const res = await fetch("/api/images", { method: "POST", body: fd });
        const j = await res.json();
        // Tolerate both response shapes (current bug + post-fix shape).
        const url =
          (j?.data?.urls?.original as string | undefined) ||
          (j?.data?.url as string | undefined) ||
          (j?.url as string | undefined);
        if (url) uploaded.push(url);
      }
      if (uploaded.length > 0) {
        setFormData((prev) => ({ ...prev, images: [...prev.images, ...uploaded] }));
      }
    } finally {
      setUploadingImage(false);
    }
  };

  // ---------- Helpers ----------
  const updateField = <K extends keyof FormData>(key: K, value: FormData[K]) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const getOptions = (typeName: string): string[] => {
    const target = typeName.toLowerCase().replace(/[^a-z0-9]/g, "");
    const a = attributes.find((x) => x.name.toLowerCase().replace(/[^a-z0-9]/g, "") === target);
    return a?.options.map((o) => o.value) || [];
  };

  if (fetching) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500">
        <Loader2 className="w-6 h-6 animate-spin mr-2" />
        Loading product…
      </div>
    );
  }

  // ---------- Render ----------
  const sectionsToRender = navSpec.filter((s) => s.id !== "identity"); // Identity always rendered first

  return (
    <div className="ep-shell" style={{ minHeight: "100vh" }}>
      <style dangerouslySetInnerHTML={{ __html: EP_TOKENS_CSS }} />

      <TopBar
        productTitle={derivedTitle || formData.productName || formData.modelNo || "Untitled"}
        productSku={derivedSku}
        status={productStatus}
        saveState={saveState}
        savedAt={savedAt}
        saveError={saveError}
        issuesCount={issues.length}
        onIssuesClick={() => {
          if (issues[0]) jumpTo(issues[0].section);
        }}
        onPreview={() => setPreviewOpen(true)}
        onCommandPalette={() => {
          // Trigger the global CommandPalette via its keyboard shortcut.
          window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true, bubbles: true }));
        }}
        onSaveDraft={() => saveDraft("DRAFT")}
        onPublish={() => {
          if (blocking.length === 0) saveDraft("PUBLISHED");
        }}
        publishDisabled={blocking.length > 0}
        onBack={() => router.push("/dashboard/products")}
      />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "200px minmax(0, 1fr) 320px",
          gap: 16,
          maxWidth: 1480,
          margin: "0 auto",
          padding: "12px 16px 80px",
        }}
        className="ep-grid"
      >
        {/* LEFT — Section nav */}
        <aside>
          <SectionNav items={navItems} activeId={activeSec} onJump={jumpTo} />
        </aside>

        {/* MIDDLE — Form */}
        <main>
          {/* Identity */}
          <Section
            id="identity"
            title="Identity"
            subtitle="Pick the product type, then fill brand, sub-brand, and model."
          >
            <div style={{ marginBottom: 14 }}>
              <Field label="Product type" required>
                <ChipGroup
                  options={CATEGORY_DEFS.map((c) => ({
                    label: c.label,
                    value: c.key,
                    icon: CATEGORY_ICONS[c.key],
                  }))}
                  value={formData.category}
                  onChange={(v) => updateField("category", v)}
                />
              </Field>
            </div>
            <Row cols={2}>
              <Field label="Brand" required>
                <SearchableDropdown
                  label=""
                  options={getOptions("brand")}
                  value={formData.brand}
                  onChange={(v) => updateField("brand", v)}
                />
              </Field>
              <Field label="Sub-Brand">
                <SearchableDropdown
                  label=""
                  options={getOptions("subbrand")}
                  value={formData.subBrand}
                  onChange={(v) => updateField("subBrand", v)}
                />
              </Field>
              <Field label="Model No" required>
                <TextInput
                  value={formData.modelNo}
                  onChange={(e) => updateField("modelNo", e.target.value)}
                  placeholder="e.g. RB3025"
                />
              </Field>
              <Field label="Label">
                <TextInput
                  value={formData.label}
                  onChange={(e) => updateField("label", e.target.value)}
                  placeholder="e.g. Classic"
                />
              </Field>
              <Field label="Product Name" hint="Optional override; auto-built from brand + model if blank.">
                <TextInput
                  value={formData.productName}
                  onChange={(e) => updateField("productName", e.target.value)}
                />
              </Field>
              <Field label="GTIN / UPC" hint="Product-level barcode (variants have their own).">
                <TextInput
                  value={formData.gtin}
                  onChange={(e) => updateField("gtin", e.target.value)}
                  placeholder="13-digit GTIN"
                />
              </Field>
            </Row>
          </Section>

          {/* Frame */}
          {sectionsToRender.some((s) => s.id === "frame") && (
            <Section id="frame" title="Frame" subtitle="Shape, materials, frame type.">
              <Row cols={2}>
                {isAttrApplicable("shape", formData.category) && (
                  <Field label="Shape">
                    <SearchableDropdown
                      label=""
                      options={getOptions("shape")}
                      value={formData.shape}
                      onChange={(v) => updateField("shape", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("frameMaterial", formData.category) && (
                  <Field label="Frame Material">
                    <SearchableDropdown
                      label=""
                      options={getOptions("framematerial")}
                      value={formData.frameMaterial}
                      onChange={(v) => updateField("frameMaterial", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("templeMaterial", formData.category) && (
                  <Field label="Temple Material">
                    <SearchableDropdown
                      label=""
                      options={getOptions("templematerial")}
                      value={formData.templeMaterial}
                      onChange={(v) => updateField("templeMaterial", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("frameType", formData.category) && (
                  <Field label="Frame Type">
                    <SearchableDropdown
                      label=""
                      options={getOptions("frametype")}
                      value={formData.frameType}
                      onChange={(v) => updateField("frameType", v)}
                    />
                  </Field>
                )}
              </Row>
            </Section>
          )}

          {/* Lens */}
          {sectionsToRender.some((s) => s.id === "lens") && (
            <Section
              id="lens"
              title="Lens"
              subtitle={
                navSpec.find((s) => s.id === "lens")?.hint ||
                "Lens material, colour, treatments."
              }
            >
              <Row cols={2}>
                {isAttrApplicable("lensMaterial", formData.category) && (
                  <Field label="Lens Material">
                    <SearchableDropdown
                      label=""
                      options={getOptions("lensmaterial")}
                      value={formData.lensMaterial}
                      onChange={(v) => updateField("lensMaterial", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("lensUSP", formData.category) && (
                  <Field label="Lens USP">
                    <SearchableDropdown
                      label=""
                      options={getOptions("lensUSP")}
                      value={formData.lensUSP}
                      onChange={(v) => updateField("lensUSP", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("polarization", formData.category) && (
                  <Field label="Polarization">
                    <SearchableDropdown
                      label=""
                      options={getOptions("polarization")}
                      value={formData.polarization}
                      onChange={(v) => updateField("polarization", v)}
                    />
                  </Field>
                )}
                {isAttrApplicable("uvProtection", formData.category) && (
                  <Field
                    label="UV Protection"
                    required={formData.category === "SUNGLASSES"}
                  >
                    <SearchableDropdown
                      label=""
                      options={getOptions("uvprotection")}
                      value={formData.uvProtection}
                      onChange={(v) => updateField("uvProtection", v)}
                    />
                  </Field>
                )}
              </Row>
            </Section>
          )}

          {/* Power — Reading glasses */}
          {sectionsToRender.some((s) => s.id === "power") && (
            <Section id="power" title="Power" subtitle="Diopter range / increments for reading glasses.">
              <Row cols={2}>
                <Field label="Power range" required>
                  <TextInput
                    value={formData.power}
                    onChange={(e) => updateField("power", e.target.value)}
                    placeholder="e.g. +1.0 to +3.0"
                  />
                </Field>
              </Row>
            </Section>
          )}

          {/* Safety certification */}
          {sectionsToRender.some((s) => s.id === "safety") && (
            <Section id="safety" title="Safety certification" subtitle="ANSI / EN166 / IS 5983, impact rating, side shields.">
              <Row cols={2}>
                <Field label="Impact rating" required>
                  <TextInput
                    value={formData.impactRating}
                    onChange={(e) => updateField("impactRating", e.target.value)}
                    placeholder="e.g. ANSI Z87.1+"
                  />
                </Field>
              </Row>
            </Section>
          )}

          {/* Contact lens spec */}
          {sectionsToRender.some((s) => s.id === "contact") && (
            <Section id="contact" title="Lens spec" subtitle="Base curve, diameter, power, replacement.">
              <Row cols={2}>
                <Field label="Base curve" required>
                  <TextInput
                    value={formData.baseCurve}
                    onChange={(e) => updateField("baseCurve", e.target.value)}
                    placeholder="e.g. 8.6"
                  />
                </Field>
              </Row>
            </Section>
          )}

          {sectionsToRender.some((s) => s.id === "packSize") && (
            <Section id="packSize" title="Pack size" subtitle="Lenses per box.">
              <Row cols={2}>
                <Field label="Pack size">
                  <TextInput placeholder="e.g. 30 lenses" />
                </Field>
              </Row>
            </Section>
          )}

          {/* Watch specs */}
          {sectionsToRender.some((s) => s.id === "watch") && (
            <Section id="watch" title="Watch specs" subtitle="Movement, case, water resistance.">
              <Row cols={2}>
                <Field label="Movement" required>
                  <TextInput
                    value={formData.movement}
                    onChange={(e) => updateField("movement", e.target.value)}
                    placeholder="Quartz / Automatic / Digital / Hybrid"
                  />
                </Field>
              </Row>
            </Section>
          )}

          {sectionsToRender.some((s) => s.id === "smart") && (
            <Section id="smart" title="Smart features" subtitle="Battery, connectivity, sensors.">
              <p style={{ color: "var(--ep-text-3)", fontSize: 12 }}>
                Smart-feature attributes (battery, connectivity, sensors, OS) aren&apos;t modelled yet — flagged in the
                category mapping. Open the mapping HTML to mark requirements.
              </p>
            </Section>
          )}

          {sectionsToRender.some((s) => s.id === "strap") && (
            <Section id="strap" title="Strap" subtitle="Material, color, length.">
              <Row cols={2}>
                <Field label="Strap material">
                  <TextInput placeholder="Leather / Metal / Silicone / Nylon" />
                </Field>
              </Row>
            </Section>
          )}

          {/* Details — universal-ish for the middle band */}
          {sectionsToRender.some((s) => s.id === "details") && (
            <Section id="details" title="Details" subtitle="Demographics, origin, warranty.">
              <Row cols={2}>
                <Field label="Gender">
                  <SearchableDropdown
                    label=""
                    options={getOptions("gender")}
                    value={formData.gender}
                    onChange={(v) => updateField("gender", v)}
                  />
                </Field>
                <Field label="Country of Origin">
                  <SearchableDropdown
                    label=""
                    options={getOptions("countryoforigin")}
                    value={formData.countryOfOrigin}
                    onChange={(v) => updateField("countryOfOrigin", v)}
                  />
                </Field>
                <Field label="Warranty">
                  <SearchableDropdown
                    label=""
                    options={getOptions("warranty")}
                    value={formData.warranty}
                    onChange={(v) => updateField("warranty", v)}
                  />
                </Field>
                <Field label="Product USP">
                  <SearchableDropdown
                    label=""
                    options={getOptions("productusp")}
                    value={formData.productUSP}
                    onChange={(v) => updateField("productUSP", v)}
                  />
                </Field>
              </Row>

              {/* Dynamic category attributes (everything not hard-coded). */}
              <DynamicCategoryAttrs
                category={formData.category}
                attributes={attributes}
                values={extraAttrs}
                onChange={(k, v) => setExtraAttrs((p) => ({ ...p, [k]: v }))}
              />
            </Section>
          )}

          {/* Pricing */}
          <Section
            id="pricing"
            title="Pricing"
            subtitle="MRP and discount strategy. Selling price is auto-derived from category discount rule."
          >
            <Row cols={2}>
              <Field label="MRP" required hint="Compare-at price on Shopify.">
                <CurrencyInput
                  value={formData.mrp}
                  onChange={(v) => updateField("mrp", v)}
                  placeholder="0"
                />
              </Field>
            </Row>
          </Section>

          {/* Inventory */}
          <Section
            id="inventory"
            title="Inventory"
            subtitle="Stock by location. Saved on blur."
            badge={
              <span style={{ fontSize: 11, color: "var(--ep-text-3)" }}>
                Total: {Object.values(formData.stockByLocation).reduce((a, b) => a + (Number(b) || 0), 0)}
              </span>
            }
          >
            {locations.length === 0 ? (
              <p style={{ color: "var(--ep-text-3)", fontSize: 12 }}>No locations configured.</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--ep-text-3)", fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
                    <th style={{ padding: "8px 12px" }}>Location</th>
                    <th style={{ padding: "8px 12px", width: 140 }}>Quantity</th>
                  </tr>
                </thead>
                <tbody>
                  {locations.map((loc) => (
                    <tr key={loc.id} style={{ borderTop: "1px solid var(--ep-border-subdued)" }}>
                      <td style={{ padding: "10px 12px", color: "var(--ep-text)" }}>{loc.name}</td>
                      <td style={{ padding: "8px 12px" }}>
                        <TextInput
                          type="number"
                          min="0"
                          value={formData.stockByLocation[loc.id] ?? 0}
                          onChange={(e) =>
                            setFormData((p) => ({
                              ...p,
                              stockByLocation: { ...p.stockByLocation, [loc.id]: parseInt(e.target.value) || 0 },
                            }))
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          {/* Media */}
          <Section id="media" title="Images">
            <div
              style={{
                border: "2px dashed var(--ep-border)",
                borderRadius: 10,
                padding: 24,
                textAlign: "center",
                cursor: "pointer",
                background: "var(--ep-surface-2)",
              }}
            >
              <input
                type="file"
                multiple
                accept="image/*"
                onChange={handleImageUpload}
                disabled={uploadingImage}
                className="hidden"
                id="ep-image-input"
              />
              <label htmlFor="ep-image-input" style={{ cursor: "pointer", display: "block" }}>
                {uploadingImage ? (
                  <Loader2 className="w-6 h-6 animate-spin mx-auto" style={{ color: "var(--ep-action)" }} />
                ) : (
                  <Upload className="w-6 h-6 mx-auto" style={{ color: "var(--ep-text-3)" }} />
                )}
                <p style={{ marginTop: 8, fontSize: 13, color: "var(--ep-text-2)" }}>
                  Drag and drop images, or click to select
                </p>
              </label>
            </div>
            {formData.images.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginTop: 16 }}>
                {formData.images.map((url, idx) => (
                  <div key={idx} style={{ position: "relative" }}>
                    <img src={url} alt={`Product ${idx + 1}`} style={{ width: "100%", height: 96, objectFit: "cover", borderRadius: 8, border: "1px solid var(--ep-border)" }} />
                    <button
                      onClick={() => setFormData((p) => ({ ...p, images: p.images.filter((_, i) => i !== idx) }))}
                      style={{
                        position: "absolute",
                        top: 4,
                        right: 4,
                        width: 24,
                        height: 24,
                        borderRadius: 999,
                        background: "var(--ep-critical)",
                        color: "white",
                        border: 0,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Publish */}
          <Section id="publish" title="Publish">
            <p style={{ fontSize: 13, color: "var(--ep-text-2)", marginBottom: 12 }}>
              {blocking.length === 0
                ? "All checks pass — you can publish to Shopify."
                : `${blocking.length} blocking issue${blocking.length === 1 ? "" : "s"} above. Resolve, then publish.`}
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => saveDraft("DRAFT")}
                style={{
                  height: 32,
                  padding: "0 14px",
                  border: "1px solid var(--ep-border)",
                  background: "var(--ep-surface)",
                  color: "var(--ep-text)",
                  borderRadius: 7,
                  fontSize: 13,
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                Save draft
              </button>
              <button
                onClick={() => blocking.length === 0 && saveDraft("PUBLISHED")}
                disabled={blocking.length > 0}
                style={{
                  height: 32,
                  padding: "0 16px",
                  background: "var(--ep-action)",
                  color: "var(--ep-action-text)",
                  border: 0,
                  borderRadius: 7,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: blocking.length > 0 ? "not-allowed" : "pointer",
                  opacity: blocking.length > 0 ? 0.5 : 1,
                }}
              >
                Publish to Shopify
              </button>
            </div>
          </Section>
        </main>

        {/* RIGHT — Live summary rail */}
        <aside style={{ position: "sticky", top: 53 + 12, alignSelf: "flex-start", maxHeight: "calc(100vh - 80px)", overflowY: "auto" }}>
          {/* Storefront preview thumb */}
          <RailGroup title="Storefront preview">
            <div style={{ padding: "10px 14px" }}>
              {formData.images[0] ? (
                <img src={formData.images[0]} alt="" style={{ width: "100%", height: 140, objectFit: "cover", borderRadius: 6, border: "1px solid var(--ep-border)" }} />
              ) : (
                <div style={{ height: 140, background: "var(--ep-surface-3)", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ep-text-3)", fontSize: 12 }}>
                  No image yet
                </div>
              )}
              <p style={{ marginTop: 8, fontSize: 12.5, fontWeight: 600, color: "var(--ep-text)" }}>
                {derivedTitle || "Untitled"}
              </p>
              {formData.mrp && (
                <p style={{ marginTop: 2, fontSize: 12, color: "var(--ep-success-text)", fontWeight: 600 }}>
                  ₹{formData.mrp}
                </p>
              )}
              <button
                onClick={() => setPreviewOpen(true)}
                style={{
                  marginTop: 10,
                  width: "100%",
                  height: 28,
                  border: "1px solid var(--ep-border)",
                  background: "var(--ep-surface)",
                  color: "var(--ep-text-2)",
                  borderRadius: 6,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Open full preview · P
              </button>
            </div>
          </RailGroup>

          {/* Auto-derived */}
          <RailGroup title="Auto-derived">
            <RailRow label="SKU" value={derivedSku || "—"} copyable={derivedSku} />
            <RailRow label="URL handle" value={derivedHandle || "—"} copyable={derivedHandle} />
            <RailRow label="SEO title" value={derivedSeoTitle || "—"} copyable={derivedSeoTitle} />
            <RailRow label="SEO desc" value={truncate(derivedSeoDesc, 60) || "—"} copyable={derivedSeoDesc} />
          </RailGroup>

          {/* Tags */}
          <RailGroup title="Shopify tags" badge={<span style={{ fontSize: 11, color: "var(--ep-text-3)" }}>{derivedTags.split(",").filter(Boolean).length}</span>}>
            <div style={{ padding: "8px 14px", display: "flex", flexWrap: "wrap", gap: 4 }}>
              {derivedTags.split(",").filter(Boolean).slice(0, 30).map((t) => (
                <span key={t} style={{ padding: "2px 7px", background: "var(--ep-info-bg)", color: "var(--ep-info-text)", borderRadius: 4, fontSize: 11 }}>
                  {t.trim()}
                </span>
              ))}
              {derivedTags.split(",").filter(Boolean).length > 30 && (
                <span style={{ fontSize: 11, color: "var(--ep-text-3)" }}>+{derivedTags.split(",").filter(Boolean).length - 30} more</span>
              )}
            </div>
          </RailGroup>

          {/* Issues */}
          <RailGroup
            title="Pre-publish issues"
            badge={
              issues.length === 0 ? (
                <span style={{ fontSize: 11, color: "var(--ep-success-text)", fontWeight: 600 }}>All clear</span>
              ) : (
                <span style={{ fontSize: 11, color: "var(--ep-critical-text)", fontWeight: 600 }}>{issues.length}</span>
              )
            }
          >
            {issues.length === 0 ? (
              <p style={{ padding: "8px 14px", fontSize: 12, color: "var(--ep-text-3)" }}>Nothing blocking publish.</p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {issues.map((i, idx) => (
                  <li key={idx}>
                    <button
                      onClick={() => jumpTo(i.section)}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "8px 14px",
                        border: 0,
                        background: "transparent",
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "flex-start",
                        gap: 8,
                        borderTop: "1px solid var(--ep-border-subdued)",
                      }}
                    >
                      <AlertCircle className="w-3.5 h-3.5 mt-0.5" style={{ color: i.level === "warning" ? "var(--ep-warning-text)" : "var(--ep-critical-text)", flexShrink: 0 }} />
                      <span style={{ fontSize: 12, color: "var(--ep-text)", flex: 1 }}>{i.msg}</span>
                      <ChevronRight className="w-3 h-3 mt-1" style={{ color: "var(--ep-text-3)", flexShrink: 0 }} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </RailGroup>

          {/* Activity placeholder */}
          <RailGroup title="Activity">
            <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--ep-text-3)" }}>
              Activity feed will surface here once /api/products/:id/activity is wired.
            </div>
          </RailGroup>

          {shopifyProductId && (
            <RailGroup title="Shopify">
              <RailRow label="Product ID" value={shopifyProductId.split("/").pop() || ""} copyable={shopifyProductId} />
            </RailGroup>
          )}
        </aside>
      </div>

      {previewOpen && (
        <PreviewModal
          title={derivedTitle}
          image={formData.images[0]}
          mrp={formData.mrp}
          seoDesc={derivedSeoDesc}
          onClose={() => setPreviewOpen(false)}
        />
      )}
    </div>
  );
}

/* ============================================================
 * Dynamic category attributes (anything in categoryAttributes.ts not
 * hard-coded into the form above)
 * ============================================================ */
const HARDCODED = new Set([
  "brand", "subBrand", "modelNo", "label", "productName",
  "shape", "frameMaterial", "templeMaterial", "frameType",
  "lensMaterial", "lensColour", "tint", "polarization", "uvProtection", "lensUSP",
  "gender", "countryOfOrigin", "warranty",
  "mrp", "gtin", "upc", "productUSP",
  "description", "tags", "sku",
  "colorCode", "colorName", "frameColor", "templeColor", "frameSize",
  "bridgeSize", "lensSize", "templeLength", "weightGrams",
  "quantity",
]);

function DynamicCategoryAttrs({
  category,
  attributes,
  values,
  onChange,
}: {
  category: string;
  attributes: AttributeType[];
  values: Record<string, string>;
  onChange: (k: string, v: string) => void;
}) {
  if (!category) return null;
  const dynamic = attributesForCategory(category, "product").filter(
    (m) => !m.autoPopulate && !HARDCODED.has(m.key)
  );
  if (dynamic.length === 0) return null;

  const getOptions = (typeName: string): string[] => {
    const target = typeName.toLowerCase().replace(/[^a-z0-9]/g, "");
    const a = attributes.find((x) => x.name.toLowerCase().replace(/[^a-z0-9]/g, "") === target);
    return a?.options.map((o) => o.value) || [];
  };

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--ep-border-subdued)" }}>
      <p style={{ fontSize: 11, fontWeight: 600, color: "var(--ep-text-3)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8 }}>
        Category attributes
      </p>
      <Row cols={2}>
        {dynamic.map((meta) => {
          const typeName = meta.attributeTypeName || meta.key.toLowerCase();
          const options = getOptions(typeName);
          const v = values[meta.key] ?? "";
          if (options.length > 0) {
            return (
              <Field key={meta.key} label={meta.label}>
                <SearchableDropdown
                  label=""
                  options={options}
                  value={v}
                  onChange={(val) => onChange(meta.key, val)}
                />
              </Field>
            );
          }
          return (
            <Field key={meta.key} label={meta.label}>
              <TextInput value={v} onChange={(e) => onChange(meta.key, e.target.value)} />
            </Field>
          );
        })}
      </Row>
    </div>
  );
}

/* ============================================================
 * PreviewModal
 * ============================================================ */
function PreviewModal({
  title,
  image,
  mrp,
  seoDesc,
  onClose,
}: {
  title: string;
  image?: string;
  mrp: string;
  seoDesc: string;
  onClose: () => void;
}) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15,23,42,0.55)",
        zIndex: 100,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--ep-surface)",
          borderRadius: 12,
          maxWidth: 480,
          width: "100%",
          padding: 20,
          boxShadow: "0 24px 64px rgba(0,0,0,0.25)",
        }}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 style={{ fontSize: 14, fontWeight: 600, margin: 0, color: "var(--ep-text)" }}>Storefront preview</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ width: 28, height: 28, border: 0, background: "transparent", cursor: "pointer", color: "var(--ep-text-2)" }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        {image && <img src={image} alt="" style={{ width: "100%", height: 240, objectFit: "cover", borderRadius: 8 }} />}
        <h4 style={{ fontSize: 16, fontWeight: 600, marginTop: 12, marginBottom: 4, color: "var(--ep-text)" }}>{title || "Untitled"}</h4>
        {mrp && <p style={{ fontSize: 14, color: "var(--ep-success-text)", fontWeight: 600, margin: "0 0 8px" }}>₹{mrp}</p>}
        <p style={{ fontSize: 12, color: "var(--ep-text-2)", lineHeight: 1.5, margin: 0 }}>{seoDesc || "(no SEO description yet)"}</p>
      </div>
    </div>
  );
}

/* ============================================================
 * Helpers
 * ============================================================ */
function isInField(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

function truncate(s: string | undefined | null, n: number): string {
  if (!s) return "";
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
