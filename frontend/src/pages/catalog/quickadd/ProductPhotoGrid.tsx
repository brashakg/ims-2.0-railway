// Quick Add - the product photos IN ORDER (owner 2026-10-12, mockup "Photos").
// Photo 1 is the main photo on the website and at the till, so every photo
// shows its place and photo 1 a 'Main photo' badge. A photo moves by DRAG
// (mouse) or by its Earlier / Later buttons: the tablets are touch screens,
// where HTML5 drag is unreliable, so the buttons are real 40px targets. Both
// call the one move rule (movePhoto) through onMove. This is the ONE photo
// editor: the add / edit page, review mode and clone all render it from
// InventorySection; the catalog drawer only views photos.

import { useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2, Wand2, X } from 'lucide-react';
import clsx from 'clsx';

/** Owner ruling 2026-10-12, word for word. Guidance only: nothing enforced. */
const ANGLES = ['Front', 'Angled (3/4)', 'Side', 'Back / temples', 'Folded / in case'];

export function AngleGuide() {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-700">
      <span className="font-semibold text-gray-900">Angle order for every product:</span>
      {ANGLES.map((angle, i) => (
        <span key={angle} className="rounded-full border border-gray-200 bg-white px-2.5 py-0.5">
          {`${i + 1} ${angle}`}
        </span>
      ))}
    </div>
  );
}

const MOVE_BTN =
  'inline-flex min-h-10 items-center justify-center gap-1 rounded-md border border-gray-300 bg-white text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-white';

export function ProductPhotoGrid({
  images,
  onMove,
  onRemove,
  onEdit,
  editing,
}: {
  images: string[];
  onMove: (from: number, to: number) => void;
  onRemove: (url: string) => void;
  onEdit: (url: string) => void;
  editing: Set<string>;
}) {
  // The photo being dragged and the place it hovers over (for the ring).
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const last = images.length - 1;

  return (
    <>
      <p className="mt-3 text-xs text-gray-600">
        Drag a photo to a new place, or use Earlier / Later under it. Photo 1 is the main
        photo on the website and at the till. Save the product to keep the order.
      </p>
      <ol className="mt-2 grid grid-cols-2 tablet:grid-cols-4 laptop:grid-cols-5 desktop:grid-cols-6 gap-3">
        {images.map((url, i) => {
          const isEditing = editing.has(url);
          return (
            <li
              key={url}
              draggable
              onDragStart={(e) => {
                setDragFrom(i);
                // Firefox starts no drag without data on the transfer.
                e.dataTransfer?.setData('text/plain', String(i + 1));
                if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
              }}
              onDragOver={(e) => {
                // Always accept here: a stray file dropped on a photo must not
                // make the browser open it and lose the form.
                e.preventDefault();
                if (dragFrom !== null && dragOver !== i) setDragOver(i);
              }}
              onDragLeave={() => setDragOver((v) => (v === i ? null : v))}
              onDrop={(e) => {
                e.preventDefault();
                if (dragFrom !== null) onMove(dragFrom, i);
                setDragFrom(null);
                setDragOver(null);
              }}
              onDragEnd={() => {
                setDragFrom(null);
                setDragOver(null);
              }}
              className={clsx(
                'flex flex-col gap-1.5 rounded-lg cursor-grab active:cursor-grabbing',
                dragFrom === i && 'opacity-50',
                dragOver === i && dragFrom !== i && 'ring-2 ring-bv ring-offset-2'
              )}
            >
              <div
                className={clsx(
                  'relative aspect-square overflow-hidden rounded-lg',
                  i === 0 ? 'border-2 border-bv' : 'border border-gray-200'
                )}
              >
                <img
                  src={url}
                  alt={`Photo ${i + 1}`}
                  draggable={false}
                  className="h-full w-full object-cover"
                  onError={(e) => { (e.currentTarget as HTMLImageElement).style.opacity = '0.3'; }}
                />
                <span className="absolute left-1.5 top-1.5 inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-gray-900 px-1.5 text-xs font-bold text-white">
                  {i + 1}
                </span>
                {i === 0 && (
                  <span className="absolute bottom-1.5 left-1.5 rounded-full bg-bv px-2 py-0.5 text-[11px] font-semibold text-white">
                    Main photo
                  </span>
                )}
                {/* Real 40px tap target: it is destructive. */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onRemove(url); }}
                  aria-label="Remove image"
                  title="Remove image"
                  className="absolute top-1 right-1 inline-flex min-h-10 min-w-10 items-center justify-center rounded-full bg-white/90 text-gray-600 hover:text-red-600 shadow"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  onClick={() => onMove(i, i - 1)}
                  disabled={i === 0}
                  aria-label={`Move photo ${i + 1} earlier`}
                  className={MOVE_BTN}
                >
                  <ChevronLeft className="w-4 h-4" aria-hidden="true" />
                  Earlier
                </button>
                <button
                  type="button"
                  onClick={() => onMove(i, i + 1)}
                  disabled={i === last}
                  aria-label={`Move photo ${i + 1} later`}
                  className={MOVE_BTN}
                >
                  Later
                  <ChevronRight className="w-4 h-4" aria-hidden="true" />
                </button>
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onEdit(url); }}
                disabled={isEditing}
                aria-label="Remove background"
                title="Remove background (clean up + resize)"
                className="inline-flex min-h-10 items-center justify-center gap-1 rounded-md border border-gray-200 bg-gray-50 px-2 text-[11px] font-medium text-gray-700 hover:text-bv disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {isEditing ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Wand2 className="w-3.5 h-3.5" />
                )}
                <span>{isEditing ? 'Working…' : 'Remove bg'}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </>
  );
}
