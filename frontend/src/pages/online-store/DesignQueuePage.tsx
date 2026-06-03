// ============================================================================
// IMS 2.0 - Online Store - Image Design Queue  (BVI Phase 4, "push-dark")
// ============================================================================
// FLAGSHIP #3 of the e-commerce (BVI) merge: the product-image DESIGN QUEUE —
// the design team's daily workflow, run entirely inside IMS. See
// docs/reference/BVI_MERGE_PLAN.md section B / Phase 4.
//
// SCOPE (Phase 4): track product/variant image records through their design
// lifecycle inside IMS Mongo `product_images`. There is NO Shopify network
// write here (no image push) — that single-writer push is Phase 5/6. So this
// screen is a pure queue board over /api/v1/online-store/images:
//   - A status filter chip row (QUEUED / IN_PROGRESS / REVIEW / APPROVED /
//     REJECTED) with per-status counts; "All" shows everything.
//   - One card per image: raw + edited thumbnails side-by-side (when present),
//     the product/variant reference, a status badge and the assignee.
//   - Per-card actions: Assign to me, Start (-> IN_PROGRESS), Attach edited URL
//     (-> REVIEW), and Approve / Reject (-> APPROVED / REJECTED) gated to
//     ADMIN / DESIGN_MANAGER (and SUPERADMIN). Catalog managers can move an
//     image up to REVIEW but not sign it off.
//
// FAIL-SOFT: the Phase-4 backend images router may not be deployed yet. Reads
// degrade to an empty board (the screen renders a friendly "backend not yet
// available" note); writes toast the backend error. Gated SUPERADMIN / ADMIN /
// CATALOG_MANAGER / DESIGN_MANAGER at the route (App.tsx). Light theme only.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Image as ImageIcon,
  Search,
  Loader2,
  RefreshCw,
  ArrowLeft,
  UserPlus,
  Play,
  Upload,
  CheckCircle2,
  XCircle,
  Trash2,
  Palette,
  Download,
  ImageOff,
  Info,
  UploadCloud,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  imagesApi,
  pushApi,
  IMAGE_DESIGN_STATUSES,
  type EcomProductImage,
  type ImageDesignStatus,
} from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  SyncChip,
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';

// ---------------------------------------------------------------------------
// Status presentation (label + on-brand light-theme colours + the legal next
// transitions). Kept here so the chip row, badges and action gating share one
// source of truth.
// ---------------------------------------------------------------------------
interface StatusMeta {
  label: string;
  // chip + badge tailwind classes (light theme only)
  chip: string;
  badge: string;
  // short helper shown under the column header / empty state
  hint: string;
}

const STATUS_META: Record<ImageDesignStatus, StatusMeta> = {
  QUEUED: {
    label: 'Queued',
    chip: 'bg-amber-100 text-amber-800 border-amber-200',
    badge: 'bg-amber-100 text-amber-800 border-amber-200',
    hint: 'Raw images awaiting a designer.',
  },
  IN_PROGRESS: {
    label: 'In progress',
    chip: 'bg-blue-100 text-blue-800 border-blue-200',
    badge: 'bg-blue-100 text-blue-800 border-blue-200',
    hint: 'A designer is editing these.',
  },
  REVIEW: {
    label: 'In review',
    chip: 'bg-purple-100 text-purple-800 border-purple-200',
    badge: 'bg-purple-100 text-purple-800 border-purple-200',
    hint: 'Edited images awaiting sign-off.',
  },
  APPROVED: {
    label: 'Approved',
    chip: 'bg-green-100 text-green-800 border-green-200',
    badge: 'bg-green-100 text-green-800 border-green-200',
    hint: 'Signed off, ready for the storefront push (a later phase).',
  },
  REJECTED: {
    label: 'Rejected',
    chip: 'bg-red-100 text-red-800 border-red-200',
    badge: 'bg-red-100 text-red-800 border-red-200',
    hint: 'Sent back for a redo.',
  },
};

// "All" sentinel for the filter chip row.
type StatusFilter = ImageDesignStatus | 'ALL';

function fmtDate(s: string | null | undefined): string {
  if (!s) return '';
  try {
    return new Date(s).toLocaleDateString('en-IN', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return '';
  }
}

// ===========================================================================
// Page
// ===========================================================================
export default function DesignQueuePage() {
  const toast = useToast();
  const { user, hasRole } = useAuth();

  // Only ADMIN / DESIGN_MANAGER (+ SUPERADMIN) may sign off (Approve/Reject).
  const canApprove = hasRole(['SUPERADMIN', 'ADMIN', 'DESIGN_MANAGER']);
  // Publishing an approved image to the storefront is integration-critical ->
  // SUPERADMIN / ADMIN only (matches the backend push router gate).
  const canPublish = hasRole(['SUPERADMIN', 'ADMIN']);
  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);

  const [images, setImages] = useState<EcomProductImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<StatusFilter>('ALL');
  const [search, setSearch] = useState('');
  // id currently being mutated (disables that card's buttons + shows a spinner)
  const [busyId, setBusyId] = useState<string | null>(null);
  // id currently being published (separate from busyId so a publish doesn't lock
  // the lifecycle buttons and vice-versa).
  const [publishingId, setPublishingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Load the whole board once; filtering by status is done client-side so
      // the chip-row counts stay live without re-fetching per chip.
      const rows = await imagesApi.list({ limit: 500 });
      setImages(rows);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Per-status counts for the chip row (over the search-filtered set so the
  // numbers match what the board shows).
  const searchFiltered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return images;
    return images.filter((img) =>
      [img.product_title, img.brand, img.model_no, img.variant_sku, img.product_id]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [images, search]);

  const counts = useMemo(() => {
    const c: Record<StatusFilter, number> = {
      ALL: searchFiltered.length,
      QUEUED: 0,
      IN_PROGRESS: 0,
      REVIEW: 0,
      APPROVED: 0,
      REJECTED: 0,
    };
    for (const img of searchFiltered) {
      const s = img.design_status;
      if (s in c) c[s] += 1;
    }
    return c;
  }, [searchFiltered]);

  const visible = useMemo(() => {
    if (filter === 'ALL') return searchFiltered;
    return searchFiltered.filter((img) => img.design_status === filter);
  }, [searchFiltered, filter]);

  // --- Mutations (optimistic-ish: re-fetch the changed record into place) ---

  const replaceRow = (updated: EcomProductImage) =>
    setImages((prev) => prev.map((x) => (x.id === updated.id ? updated : x)));

  const runMutation = async (
    id: string,
    fn: () => Promise<EcomProductImage>,
    okMsg: string,
  ) => {
    setBusyId(id);
    try {
      const updated = await fn();
      replaceRow(updated);
      toast.success(okMsg);
    } catch (e: any) {
      toast.error(
        e?.message || 'Action failed — is the Online Store backend deployed?',
      );
    } finally {
      setBusyId(null);
    }
  };

  const assignToMe = (img: EcomProductImage) => {
    const me = user?.id;
    if (!me) {
      toast.error('Could not determine your user id');
      return;
    }
    runMutation(img.id, () => imagesApi.assign(img.id, me), 'Assigned to you');
  };

  const start = (img: EcomProductImage) =>
    runMutation(img.id, () => imagesApi.setStatus(img.id, 'IN_PROGRESS'), 'Marked in progress');

  const attachEdited = (img: EcomProductImage) => {
    const url = window.prompt(
      'Paste the edited image URL (this moves the image to In review):',
      img.edited_url || '',
    );
    if (url === null) return; // cancelled
    const trimmed = url.trim();
    if (!trimmed) {
      toast.info('No URL entered');
      return;
    }
    runMutation(img.id, () => imagesApi.attachEdited(img.id, trimmed), 'Edited image attached — now in review');
  };

  const approve = (img: EcomProductImage) =>
    runMutation(img.id, () => imagesApi.setStatus(img.id, 'APPROVED'), 'Approved');

  const reject = (img: EcomProductImage) => {
    const reason = window.prompt('Reason for rejection (optional):', img.note || '');
    if (reason === null) return; // cancelled
    runMutation(
      img.id,
      () => imagesApi.setStatus(img.id, 'REJECTED', reason.trim() || undefined),
      'Rejected — sent back for a redo',
    );
  };

  const remove = async (img: EcomProductImage) => {
    if (!window.confirm('Delete this image record? This cannot be undone.')) return;
    setBusyId(img.id);
    try {
      await imagesApi.remove(img.id);
      setImages((prev) => prev.filter((x) => x.id !== img.id));
      toast.success('Image deleted');
    } catch (e: any) {
      toast.error(e?.message || 'Could not delete the image');
    } finally {
      setBusyId(null);
    }
  };

  // Publish (push) ONE approved image to Shopify. DARK by default -> a SIMULATED
  // dry-run; the returned mode (SIMULATED vs LIVE) is surfaced in the toast so a
  // dry-run is never mistaken for a live write. A non-APPROVED image returns
  // ok=false action=skip (not an HTTP error) which we surface honestly. On a LIVE
  // push refresh the board + banner so the Synced chip + counts update.
  const publishImage = async (img: EcomProductImage) => {
    setPublishingId(img.id);
    try {
      const label = `Image "${img.product_title || img.model_no || img.product_id}"`;
      const result = await pushApi.pushImage(img.id);
      if (result.ok) {
        toast.success(formatPushResult(label, result));
      } else {
        toast.warning(formatPushResult(label, result));
      }
      if (result.ok && result.mode === 'LIVE') {
        await load();
        bannerRef.current?.refresh();
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Could not publish image');
    } finally {
      setPublishingId(null);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Design queue</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Palette className="w-5 h-5" /> Image design queue
          </h1>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Move each product photo from a raw shot to a finished, approved hero image — all inside IMS.
        Approving an image marks it ready; pushing it live to the storefront is a later, owner-approved
        step, so nothing here changes the live site yet.
      </p>

      {/* Shopify publish (DARK / LIVE) banner */}
      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      {/* Status filter chip row */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <FilterChip
          active={filter === 'ALL'}
          onClick={() => setFilter('ALL')}
          label="All"
          count={counts.ALL}
          activeClass="bg-gray-900 text-white border-gray-900"
        />
        {IMAGE_DESIGN_STATUSES.map((s) => (
          <FilterChip
            key={s}
            active={filter === s}
            onClick={() => setFilter(s)}
            label={STATUS_META[s].label}
            count={counts[s]}
            activeClass={STATUS_META[s].chip}
          />
        ))}
      </div>

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by product, brand, model or SKU…"
            className="input-field w-full pl-9"
          />
        </div>
        {!canApprove && (
          <span className="inline-flex items-center gap-1.5 text-xs text-gray-500">
            <Info className="w-3.5 h-3.5" />
            Approve / reject is limited to design managers and admins.
          </span>
        )}
      </div>

      {/* Board */}
      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading the design queue…
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-center">
          <ImageIcon className="w-8 h-8 text-gray-300 mx-auto mb-2" />
          <p className="text-sm font-medium text-gray-700">
            {filter === 'ALL'
              ? search
                ? 'No images match your search.'
                : 'No images in the design queue yet.'
              : `Nothing ${STATUS_META[filter as ImageDesignStatus].label.toLowerCase()} right now.`}
          </p>
          {filter === 'ALL' && !search && (
            <p className="text-xs text-gray-500 mt-1 max-w-md mx-auto">
              Raw product photos submitted for design will appear here. If you expected images and the
              board is empty, the Online Store backend may not be deployed yet — this screen is
              fail-soft and will fill in once it is.
            </p>
          )}
          {filter !== 'ALL' && (
            <p className="text-xs text-gray-500 mt-1">{STATUS_META[filter as ImageDesignStatus].hint}</p>
          )}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((img) => (
            <ImageCard
              key={img.id}
              img={img}
              busy={busyId === img.id}
              canApprove={canApprove}
              canPublish={canPublish}
              publishing={publishingId === img.id}
              currentUserId={user?.id}
              onAssignToMe={() => assignToMe(img)}
              onStart={() => start(img)}
              onAttachEdited={() => attachEdited(img)}
              onApprove={() => approve(img)}
              onReject={() => reject(img)}
              onRemove={() => remove(img)}
              onPublish={() => publishImage(img)}
            />
          ))}
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store · Design queue · Phase 4 (stored in IMS; storefront image push is a later phase).
      </p>
    </div>
  );
}

// ===========================================================================
// Filter chip
// ===========================================================================
function FilterChip({
  active,
  onClick,
  label,
  count,
  activeClass,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  activeClass: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ' +
        (active ? activeClass : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50')
      }
    >
      {label}
      <span
        className={
          'inline-flex items-center justify-center min-w-[1.25rem] rounded-full px-1 text-[11px] ' +
          (active ? 'bg-white/25' : 'bg-gray-100 text-gray-600')
        }
      >
        {count}
      </span>
    </button>
  );
}

// ===========================================================================
// One image card
// ===========================================================================
function ImageCard({
  img,
  busy,
  canApprove,
  canPublish,
  publishing,
  currentUserId,
  onAssignToMe,
  onStart,
  onAttachEdited,
  onApprove,
  onReject,
  onRemove,
  onPublish,
}: {
  img: EcomProductImage;
  busy: boolean;
  canApprove: boolean;
  canPublish: boolean;
  publishing: boolean;
  currentUserId?: string;
  onAssignToMe: () => void;
  onStart: () => void;
  onAttachEdited: () => void;
  onApprove: () => void;
  onReject: () => void;
  onRemove: () => void;
  onPublish: () => void;
}) {
  const meta = STATUS_META[img.design_status] ?? STATUS_META.QUEUED;
  const rawUrl = img.raw_url || (img.role === 'RAW' ? img.url : null) || img.original_url || null;
  const editedUrl = img.edited_url || (img.role === 'EDITED' ? img.url : null) || null;
  const title = img.product_title || img.model_no || img.product_id || '(untitled)';
  const subtitle = [img.brand, img.category, img.variant_sku].filter(Boolean).join(' · ');
  const assignedToMe = !!currentUserId && img.assignee_id === currentUserId;

  return (
    <div className="rounded-xl border border-gray-200 bg-white flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-4 pt-3 pb-2 border-b border-gray-100 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-900 truncate" title={title}>
            {title}
          </h2>
          {subtitle && <p className="text-xs text-gray-400 truncate">{subtitle}</p>}
        </div>
        <div className="flex flex-col items-end gap-1">
          <span
            className={
              'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ' +
              meta.badge
            }
          >
            {meta.label}
          </span>
          {/* Show the Shopify sync state once an image is approved (push-eligible). */}
          {img.design_status === 'APPROVED' && (
            <SyncChip synced={!!img.shopify_media_id} pending={!!img.locally_modified} />
          )}
        </div>
      </div>

      {/* Raw + edited thumbnails side-by-side */}
      <div className="p-4 grid grid-cols-2 gap-3">
        <Thumb label="Raw" url={rawUrl} download />
        <Thumb label="Edited" url={editedUrl} accent={img.design_status === 'APPROVED'} />
      </div>

      {/* Assignee + reject note */}
      <div className="px-4 pb-2 space-y-1">
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <UserPlus className="w-3.5 h-3.5" />
          {img.assignee_name || img.assignee_id ? (
            <span>
              Assigned to{' '}
              <span className="font-medium text-gray-700">
                {img.assignee_name || img.assignee_id}
                {assignedToMe ? ' (you)' : ''}
              </span>
            </span>
          ) : (
            <span className="text-gray-400">Unassigned</span>
          )}
        </div>
        {img.design_status === 'REJECTED' && img.note && (
          <div className="flex items-start gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 rounded-md px-2 py-1">
            <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
            <span className="break-words">{img.note}</span>
          </div>
        )}
        {img.created_at && (
          <div className="text-[11px] text-gray-400">Submitted {fmtDate(img.created_at)}</div>
        )}
      </div>

      {/* Actions */}
      <div className="mt-auto border-t border-gray-100 px-3 py-2.5 flex flex-wrap items-center gap-1.5">
        {busy && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}

        {/* Assign to me — available whenever it's not already mine + not terminal-approved */}
        {!assignedToMe && img.design_status !== 'APPROVED' && (
          <ActionButton onClick={onAssignToMe} disabled={busy} icon={UserPlus} label="Assign to me" />
        )}

        {/* Start — from QUEUED or REJECTED (re-do) -> IN_PROGRESS */}
        {(img.design_status === 'QUEUED' || img.design_status === 'REJECTED') && (
          <ActionButton onClick={onStart} disabled={busy} icon={Play} label="Start" />
        )}

        {/* Attach edited URL -> REVIEW. Allowed while being worked or queued. */}
        {(img.design_status === 'IN_PROGRESS' ||
          img.design_status === 'QUEUED' ||
          img.design_status === 'REJECTED') && (
          <ActionButton onClick={onAttachEdited} disabled={busy} icon={Upload} label="Attach edited" />
        )}

        {/* Approve / Reject — only in REVIEW, only for approver roles */}
        {img.design_status === 'REVIEW' && canApprove && (
          <>
            <ActionButton
              onClick={onApprove}
              disabled={busy}
              icon={CheckCircle2}
              label="Approve"
              tone="approve"
            />
            <ActionButton onClick={onReject} disabled={busy} icon={XCircle} label="Reject" tone="reject" />
          </>
        )}

        {/* Publish — only an APPROVED image is push-eligible; SUPERADMIN/ADMIN only.
            DARK by default -> a dry-run; the toast states SIMULATED vs LIVE. The
            shared busy spinner at the row start covers the in-flight feedback. */}
        {img.design_status === 'APPROVED' && canPublish && (
          <>
            {publishing && <Loader2 className="w-4 h-4 animate-spin text-gray-400" />}
            <ActionButton
              onClick={onPublish}
              disabled={publishing}
              icon={UploadCloud}
              label="Publish"
            />
          </>
        )}

        {/* Delete — always available (record-level), pushed to the far right */}
        <button
          type="button"
          onClick={onRemove}
          disabled={busy}
          className="ml-auto p-1.5 rounded-lg hover:bg-red-50 text-gray-400 hover:text-red-600 disabled:opacity-40"
          title="Delete image record"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// One raw/edited thumbnail tile. Falls back to an "empty" placeholder when the
// URL is missing or fails to load.
function Thumb({
  label,
  url,
  download,
  accent,
}: {
  label: string;
  url: string | null;
  download?: boolean;
  accent?: boolean;
}) {
  const [broken, setBroken] = useState(false);
  const has = !!url && !broken;
  const ring = accent ? 'border-green-300' : 'border-gray-200';

  const inner = has ? (
    <>
      <img
        src={url as string}
        alt={`${label} image`}
        className="w-full h-full object-cover"
        onError={() => setBroken(true)}
      />
      {download && (
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-center justify-center">
          <Download className="w-5 h-5 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      )}
    </>
  ) : (
    <div className="w-full h-full flex flex-col items-center justify-center text-gray-300">
      <ImageOff className="w-6 h-6" />
      <span className="text-[10px] mt-1 text-gray-400">none</span>
    </div>
  );

  return (
    <div>
      <div className="text-[11px] font-medium text-gray-500 uppercase tracking-wider mb-1">{label}</div>
      {has && download ? (
        <a
          href={(url as string)}
          target="_blank"
          rel="noreferrer"
          download
          className={'relative group block aspect-square rounded-lg overflow-hidden border ' + ring}
          title={`Open / download ${label.toLowerCase()} image`}
        >
          {inner}
        </a>
      ) : (
        <div className={'relative aspect-square rounded-lg overflow-hidden border bg-gray-50 ' + ring}>
          {inner}
        </div>
      )}
    </div>
  );
}

// A compact pill action button used in the card footer.
function ActionButton({
  onClick,
  disabled,
  icon: Icon,
  label,
  tone,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: typeof UserPlus;
  label: string;
  tone?: 'approve' | 'reject';
}) {
  const toneClass =
    tone === 'approve'
      ? 'text-green-700 border-green-200 hover:bg-green-50'
      : tone === 'reject'
        ? 'text-red-600 border-red-200 hover:bg-red-50'
        : 'text-gray-700 border-gray-200 hover:bg-gray-50';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={
        'inline-flex items-center gap-1 rounded-lg border bg-white px-2 py-1 text-xs font-medium disabled:opacity-40 ' +
        toneClass
      }
    >
      <Icon className="w-3.5 h-3.5" />
      {label}
    </button>
  );
}
