// ============================================================================
// IMS 2.0 — Handoff Inbox Card (Hub-page section)
// ============================================================================
// "On your desk · N file{s} awaiting you" — a horizontal scrollable strip
// of cards showing the recipient's pending + kept handoffs. Renders only
// when there's at least one card visible. Click a card to open the
// response modal; submitting a non-reshare reply chains into the dismiss
// modal.

import { useCallback, useEffect, useState } from 'react';
import {
  FileText,
  Image as ImageIcon,
  Forward,
  CheckCircle2,
  XCircle,
  Inbox,
  CornerDownRight,
} from 'lucide-react';
import { useNow } from '../../hooks/useNow';
import {
  handoffsApi,
  type HandoffResponseValue,
  type InboxItem,
} from '../../services/api/handoffs';
import HandoffResponseModal from './HandoffResponseModal';
import HandoffDismissModal from './HandoffDismissModal';

// 30 s tick — fine enough for "2 m ago" / "in 1 d" without being chatty.
const TICK_MS = 30 * 1000;

// ============================================================================
// Helpers — pure presentation
// ============================================================================

function timeAgo(then: string, now: Date): string {
  let date: Date;
  try {
    date = new Date(then);
  } catch {
    return '';
  }
  const seconds = Math.max(0, Math.floor((now.getTime() - date.getTime()) / 1000));
  if (seconds < 30) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function daysUntilExpiry(expiresAt: string, now: Date): number {
  try {
    const exp = new Date(expiresAt).getTime();
    const diff = exp - now.getTime();
    return diff / (1000 * 60 * 60 * 24);
  } catch {
    return 999;
  }
}

interface ExpiryPillProps {
  expiresAt: string;
  now: Date;
}

function ExpiryPill({ expiresAt, now }: ExpiryPillProps) {
  const days = daysUntilExpiry(expiresAt, now);
  if (days <= 0) {
    return (
      <span className="inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-red-100 text-red-700">
        Expired
      </span>
    );
  }
  let cls = 'bg-emerald-100 text-emerald-700';
  if (days < 2) cls = 'bg-red-100 text-red-700';
  else if (days < 5) cls = 'bg-amber-100 text-amber-700';

  const label =
    days >= 1
      ? `${Math.floor(days)}d left`
      : `${Math.max(1, Math.round(days * 24))}h left`;

  return (
    <span className={`inline-flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded-full ${cls}`}>
      {label}
    </span>
  );
}

// Phase 6.6b lesson: light theme — readable text on coloured backgrounds.
const RESPONSE_BADGE: Record<HandoffResponseValue, { label: string; cls: string; icon: React.ReactNode }> = {
  approved: {
    label: 'Approved',
    cls: 'bg-green-100 text-green-700',
    icon: <CheckCircle2 className="w-3 h-3" />,
  },
  denied: {
    label: 'Denied',
    cls: 'bg-red-100 text-red-700',
    icon: <XCircle className="w-3 h-3" />,
  },
  accepted: {
    label: 'Accepted',
    cls: 'bg-blue-100 text-blue-700',
    icon: <CornerDownRight className="w-3 h-3" />,
  },
  received: {
    label: 'Received',
    cls: 'bg-gray-100 text-gray-700',
    icon: <Inbox className="w-3 h-3" />,
  },
  reshared: {
    label: 'Reshared',
    cls: 'bg-amber-100 text-amber-700',
    icon: <Forward className="w-3 h-3" />,
  },
};

// ============================================================================
// Component
// ============================================================================

interface HandoffInboxCardProps {
  /** Bumped by parent to force a refetch (e.g. after sending). */
  refreshKey?: number;
}

export function HandoffInboxCard({ refreshKey }: HandoffInboxCardProps) {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal state
  const [activeCard, setActiveCard] = useState<InboxItem | null>(null);
  const [dismissCard, setDismissCard] = useState<{
    handoffId: string;
    response: string;
  } | null>(null);

  const now = useNow(TICK_MS);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await handoffsApi.listInbox();
      setItems(r.handoffs || []);
      setTotal(r.total ?? r.handoffs?.length ?? 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load handoffs.');
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch, refreshKey]);

  // Hide the entire section when there's nothing to show (loading state
  // is silent — the user shouldn't see a "loading" hub block).
  if (loading) return null;
  if (error) return null;
  if (total === 0 || items.length === 0) return null;

  const handleResponded = (
    handoffId: string,
    response: Exclude<HandoffResponseValue, 'reshared'>,
  ) => {
    setDismissCard({ handoffId, response });
  };

  const handleResolved = () => {
    setDismissCard(null);
    refetch();
  };

  return (
    <>
      <section className="handoff-inbox-section" style={sectionStyle}>
        <div className="section-head">
          <span className="eyebrow">On your desk</span>
          <span className="line" />
          <span className="sub">
            {total} file{total === 1 ? '' : 's'} awaiting you
          </span>
        </div>

        <div className="handoff-inbox-strip" style={stripStyle}>
          {items.map((card) => {
            const isPdf = card.file?.mime_type === 'application/pdf';
            const responded = card.my_status === 'responded';
            const responseBadge = card.my_response ? RESPONSE_BADGE[card.my_response] : null;

            return (
              <button
                key={card.handoff_id}
                type="button"
                onClick={() => setActiveCard(card)}
                className="handoff-card"
                style={cardStyle}
              >
                <div style={cardTopRowStyle}>
                  <span style={mimeIconStyle} aria-hidden>
                    {isPdf ? (
                      <FileText className="w-5 h-5" />
                    ) : (
                      <ImageIcon className="w-5 h-5" />
                    )}
                  </span>
                  <ExpiryPill expiresAt={card.expires_at} now={now} />
                </div>

                <h4 style={titleStyle}>{card.title}</h4>

                <p style={uploaderStyle}>
                  From <span style={{ fontWeight: 600 }}>{card.uploader_name || 'Unknown'}</span>
                </p>

                <p style={timeAgoStyle}>
                  {timeAgo(card.created_at, now)}
                  {card.parent_handoff_id && (
                    <span style={resharedFlagStyle}>
                      <Forward className="w-3 h-3" /> reshared
                    </span>
                  )}
                </p>

                {card.description && (
                  <p style={descStyle}>{card.description}</p>
                )}

                <div style={ctaRowStyle}>
                  {responded && responseBadge ? (
                    <span
                      className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full ${responseBadge.cls}`}
                    >
                      {responseBadge.icon}
                      {responseBadge.label}
                    </span>
                  ) : (
                    <span className="btn sm primary" style={openBtnStyle}>
                      Open
                    </span>
                  )}
                  {card.my_kept && (
                    <span className="text-[10px] text-gray-500 ml-auto">Kept</span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </section>

      <HandoffResponseModal
        isOpen={activeCard !== null}
        card={activeCard}
        onClose={() => setActiveCard(null)}
        onResponded={(handoffId, response) => handleResponded(handoffId, response)}
        onReshared={() => {
          setActiveCard(null);
          refetch();
        }}
      />

      <HandoffDismissModal
        isOpen={dismissCard !== null}
        handoffId={dismissCard?.handoffId ?? null}
        lastResponse={dismissCard?.response ?? null}
        onClose={() => {
          // If the user dismisses the modal without picking, leave the card
          // visible (default state) — but we still need to refetch in case
          // the response itself flipped status.
          setDismissCard(null);
          refetch();
        }}
        onResolved={handleResolved}
      />
    </>
  );
}

export default HandoffInboxCard;

// ============================================================================
// Inline styles
// ============================================================================
// Hub-page section styles use design-token CSS classes (.section-head,
// .eyebrow). The strip + card visuals are local — nothing in index.css
// covers a horizontal scrollable card strip yet. Inline styles keep the
// surface area tiny without introducing a new global ruleset.

const sectionStyle: React.CSSProperties = {
  padding: '20px 24px 0 24px',
  maxWidth: 1280,
  margin: '0 auto',
};

const stripStyle: React.CSSProperties = {
  display: 'grid',
  gridAutoFlow: 'column',
  gridAutoColumns: 'minmax(260px, 280px)',
  gap: 12,
  overflowX: 'auto',
  paddingBottom: 6,
  marginTop: 12,
};

const cardStyle: React.CSSProperties = {
  textAlign: 'left',
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
  padding: 14,
  borderRadius: 10,
  border: '1px solid var(--line, #e3e3dc)',
  background: 'var(--bg-card, #fff)',
  cursor: 'pointer',
  minHeight: 156,
};

const cardTopRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 2,
};

const mimeIconStyle: React.CSSProperties = {
  width: 32,
  height: 32,
  borderRadius: 8,
  background: 'var(--bg-sunk, #f6f6f1)',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: 'var(--ink-2, #45453f)',
  flexShrink: 0,
};

const titleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 14,
  fontWeight: 600,
  color: 'var(--ink, #1f1f1d)',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
  lineHeight: 1.3,
};

const uploaderStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12,
  color: 'var(--ink-2, #45453f)',
};

const timeAgoStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 11,
  color: 'var(--ink-4, #86867d)',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
};

const resharedFlagStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 3,
  color: '#b08600',
};

const descStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12,
  color: 'var(--ink-3, #65655e)',
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
  fontStyle: 'italic',
};

const ctaRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  marginTop: 'auto',
  paddingTop: 6,
};

const openBtnStyle: React.CSSProperties = {
  // .btn.sm.primary already gives us the right size/colour via index.css.
  // Empty here on purpose — keep the legacy class authoritative.
};

// Re-export name so the parent can import a single default loader entry.
export { HandoffInboxCard as InboxCard };
