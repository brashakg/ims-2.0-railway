// ============================================================================
// IMS 2.0 — Handoff Response Modal
// ============================================================================
// Shown when the recipient clicks a card on their Hub. Two views:
//   1. Preview + 5 response buttons (Approved · Denied · Accepted · Received · Reshare).
//   2. Reshare sub-flow: forward the file to additional recipients.
//
// Approved/Denied/Accepted/Received call back via `onResponded` so the
// parent can chain into the dismiss modal. Reshare is its own terminal
// flow (success toast + close).

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  X,
  Loader2,
  Check,
  XCircle,
  Inbox,
  CornerDownRight,
  Forward,
  FileText,
  ImageOff,
  ArrowLeft,
  Send,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  handoffsApi,
  type EligibleRecipient,
  type HandoffResponseValue,
  type InboxItem,
} from '../../services/api/handoffs';
import RecipientPicker from './RecipientPicker';

interface HandoffResponseModalProps {
  isOpen: boolean;
  onClose: () => void;
  card: InboxItem | null;
  /** Fired after a non-reshare response completes. Parent should chain
   *  to HandoffDismissModal so the user can hide / keep / snooze. */
  onResponded?: (
    handoffId: string,
    response: Exclude<HandoffResponseValue, 'reshared'>,
  ) => void;
  /** Fired after a successful reshare completes (parent refreshes lists). */
  onReshared?: () => void;
}

type ReplyChoice = Exclude<HandoffResponseValue, 'reshared'>;

interface ReplyConfig {
  value: ReplyChoice;
  label: string;
  className: string;
  icon: React.ReactNode;
}

const REPLIES: ReplyConfig[] = [
  {
    value: 'approved',
    label: 'Approved',
    className: 'bg-green-600 hover:bg-green-700 text-white border-green-600',
    icon: <Check className="w-4 h-4" />,
  },
  {
    value: 'denied',
    label: 'Denied',
    className: 'bg-red-600 hover:bg-red-700 text-white border-red-600',
    icon: <XCircle className="w-4 h-4" />,
  },
  {
    value: 'accepted',
    label: 'Accepted',
    className: 'bg-blue-600 hover:bg-blue-700 text-white border-blue-600',
    icon: <CornerDownRight className="w-4 h-4" />,
  },
  {
    value: 'received',
    label: 'Received',
    className: 'bg-gray-600 hover:bg-gray-700 text-white border-gray-600',
    icon: <Inbox className="w-4 h-4" />,
  },
];

const COMMENT_MAX = 200;

export function HandoffResponseModal({
  isOpen,
  onClose,
  card,
  onResponded,
  onReshared,
}: HandoffResponseModalProps) {
  const toast = useToast();
  const [comment, setComment] = useState('');
  const [busyChoice, setBusyChoice] = useState<HandoffResponseValue | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [showReshare, setShowReshare] = useState(false);
  const [reshareRecipients, setReshareRecipients] = useState<EligibleRecipient[]>([]);

  // Hold the current preview blob URL on a ref so cleanup runs once,
  // even if the user clicks through multiple cards in a session.
  const previewUrlRef = useRef<string | null>(null);

  const isPdf = card?.file?.mime_type === 'application/pdf';

  // Reset state every time we open with a fresh card
  useEffect(() => {
    if (!isOpen || !card) return;
    setComment('');
    setBusyChoice(null);
    setShowReshare(false);
    setReshareRecipients([]);
    setPreviewError(null);
    setPreviewLoading(true);

    let cancelled = false;
    handoffsApi
      .downloadFileBlob(card.handoff_id)
      .then(({ blob }) => {
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        // Revoke any prior URL before swapping in the new one
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
        previewUrlRef.current = url;
        setPreviewUrl(url);
      })
      .catch((err) => {
        if (cancelled) return;
        setPreviewError(err instanceof Error ? err.message : 'Could not load preview.');
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, card]);

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (previewUrlRef.current) {
        URL.revokeObjectURL(previewUrlRef.current);
        previewUrlRef.current = null;
      }
    };
  }, []);

  const alreadyResponded = card?.my_status === 'responded';

  const expiresHuman = useMemo(() => {
    if (!card?.expires_at) return '';
    try {
      const d = new Date(card.expires_at);
      return d.toLocaleString('en-GB', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return card.expires_at;
    }
  }, [card?.expires_at]);

  if (!isOpen || !card) return null;

  const handleReply = async (choice: ReplyChoice) => {
    if (alreadyResponded || busyChoice) return;
    setBusyChoice(choice);
    try {
      await handoffsApi.respond(card.handoff_id, choice, comment);
      toast.success(`Marked as ${choice}.`);
      onResponded?.(card.handoff_id, choice);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Could not record ${choice}.`);
    } finally {
      setBusyChoice(null);
    }
  };

  const submitReshare = async () => {
    if (reshareRecipients.length === 0 || busyChoice) return;
    setBusyChoice('reshared');
    try {
      await handoffsApi.reshare(
        card.handoff_id,
        reshareRecipients.map((r) => r.user_id),
        comment.trim() || undefined,
      );
      toast.success(
        `Reshared to ${reshareRecipients.length} recipient${
          reshareRecipients.length === 1 ? '' : 's'
        }.`,
      );
      onReshared?.();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not reshare.');
    } finally {
      setBusyChoice(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[92vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-200 sticky top-0 bg-white z-10">
          <div className="min-w-0">
            <h2 className="text-base sm:text-lg font-semibold text-gray-900 truncate">
              {card.title}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              From <span className="font-medium text-gray-700">{card.uploader_name || 'Unknown'}</span>
              {expiresHuman && <> · expires {expiresHuman}</>}
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 p-2 text-gray-500 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body — flex split on desktop, stacked on mobile */}
        <div className="flex-1 overflow-hidden flex flex-col md:flex-row min-h-0">
          {/* Preview pane */}
          <div className="md:w-3/5 flex-shrink-0 bg-gray-100 flex items-center justify-center p-3 border-b md:border-b-0 md:border-r border-gray-200 min-h-[260px] md:min-h-0 overflow-hidden">
            {previewLoading && (
              <div className="flex flex-col items-center gap-2 text-gray-500">
                <Loader2 className="w-6 h-6 animate-spin" />
                <span className="text-xs">Loading preview…</span>
              </div>
            )}
            {!previewLoading && previewError && (
              <div className="flex flex-col items-center gap-2 text-gray-500 text-center px-3">
                <ImageOff className="w-7 h-7" />
                <span className="text-xs">{previewError}</span>
              </div>
            )}
            {!previewLoading && !previewError && previewUrl && (
              isPdf ? (
                <iframe
                  src={previewUrl}
                  title={card.file.filename}
                  className="w-full h-full min-h-[60vh] md:min-h-[68vh] bg-white border border-gray-200 rounded"
                />
              ) : (
                <img
                  src={previewUrl}
                  alt={card.file.filename}
                  className="max-w-full max-h-[68vh] object-contain bg-white border border-gray-200 rounded shadow-sm"
                />
              )
            )}
          </div>

          {/* Action pane */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 min-w-0">
            {/* Card meta */}
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <FileText className="w-3.5 h-3.5" />
                <span className="truncate">{card.file.filename}</span>
              </div>
              {card.description && (
                <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
                  {card.description}
                </div>
              )}
              {card.parent_handoff_id && (
                <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5 inline-flex items-center gap-1.5">
                  <Forward className="w-3 h-3" />
                  Reshared from another handoff
                </div>
              )}
            </div>

            {alreadyResponded && (
              <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
                You already replied{' '}
                <span className="font-semibold">"{card.my_response ?? 'unknown'}"</span>
                {card.my_responded_at && (
                  <>
                    {' '}
                    on{' '}
                    {(() => {
                      try {
                        return new Date(card.my_responded_at).toLocaleString('en-GB');
                      } catch {
                        return card.my_responded_at;
                      }
                    })()}
                  </>
                )}
                .
              </div>
            )}

            {!showReshare ? (
              <>
                {/* Comment */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Comment <span className="text-gray-500 font-normal">(optional)</span>
                  </label>
                  <input
                    type="text"
                    value={comment}
                    onChange={(e) => setComment(e.target.value.slice(0, COMMENT_MAX))}
                    placeholder="Add a one-line note for the sender."
                    className="input-field"
                    maxLength={COMMENT_MAX}
                    disabled={alreadyResponded}
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    {comment.length}/{COMMENT_MAX}
                  </p>
                </div>

                {/* Reply buttons */}
                <div className="grid grid-cols-2 gap-2">
                  {REPLIES.map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      onClick={() => handleReply(r.value)}
                      disabled={alreadyResponded || busyChoice !== null}
                      className={`flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${r.className}`}
                    >
                      {busyChoice === r.value ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        r.icon
                      )}
                      {r.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => setShowReshare(true)}
                    disabled={alreadyResponded || busyChoice !== null}
                    className="flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg border text-sm font-medium transition-colors col-span-2 bg-amber-500 hover:bg-amber-600 text-white border-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Forward className="w-4 h-4" />
                    Reshare to another team-mate
                  </button>
                </div>
              </>
            ) : (
              <>
                {/* Reshare sub-flow */}
                <div className="flex items-center gap-2 -mb-1">
                  <button
                    type="button"
                    onClick={() => setShowReshare(false)}
                    className="text-xs text-gray-500 hover:text-gray-700 inline-flex items-center gap-1"
                  >
                    <ArrowLeft className="w-3.5 h-3.5" />
                    Back
                  </button>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Reshare to <span className="text-red-500">*</span>
                  </label>
                  <RecipientPicker
                    selected={reshareRecipients}
                    onChange={setReshareRecipients}
                    excludeUserIds={[card.uploader_id]}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">
                    Note <span className="text-gray-500 font-normal">(optional)</span>
                  </label>
                  <input
                    type="text"
                    value={comment}
                    onChange={(e) => setComment(e.target.value.slice(0, COMMENT_MAX))}
                    placeholder="One-liner the new recipient will see."
                    className="input-field"
                    maxLength={COMMENT_MAX}
                  />
                </div>
                <p className="text-xs text-gray-500">
                  Reshares inherit the original expiry — they can't extend the deadline.
                </p>
                <button
                  type="button"
                  onClick={submitReshare}
                  disabled={reshareRecipients.length === 0 || busyChoice !== null}
                  className="btn-primary flex items-center justify-center gap-2 w-full disabled:opacity-50"
                >
                  {busyChoice === 'reshared' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                  {busyChoice === 'reshared' ? 'Resharing…' : 'Reshare'}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default HandoffResponseModal;
