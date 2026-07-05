// ============================================================================
// IMS 2.0 - Image lightbox (owner 2026-07-05: click a product thumbnail to
// view the full-size image)
// ============================================================================
// Full-screen overlay showing one image at its natural size (bounded to the
// viewport). Esc / overlay-click / the X button close; when multiple images
// are supplied, arrow keys or the prev/next buttons cycle through them.
// Purely presentational — mount it with the image list + start index, unmount
// via onClose.

import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';

interface ImageLightboxProps {
  /** Absolute image URLs. */
  images: string[];
  /** Index to open at (clamped). */
  startIndex?: number;
  /** Accessible label / caption (e.g. the product name). */
  alt?: string;
  onClose: () => void;
}

export function ImageLightbox({ images, startIndex = 0, alt, onClose }: ImageLightboxProps) {
  const valid = images.filter((u) => typeof u === 'string' && u.trim() !== '');
  const [index, setIndex] = useState(() =>
    Math.min(Math.max(startIndex, 0), Math.max(valid.length - 1, 0)),
  );

  const prev = useCallback(
    () => setIndex((i) => (i - 1 + valid.length) % valid.length),
    [valid.length],
  );
  const next = useCallback(
    () => setIndex((i) => (i + 1) % valid.length),
    [valid.length],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowLeft' && valid.length > 1) prev();
      else if (e.key === 'ArrowRight' && valid.length > 1) next();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, prev, next, valid.length]);

  if (valid.length === 0) return null;

  return (
    <div
      className="fixed inset-0 z-[80] bg-black/80 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={alt ? `Image: ${alt}` : 'Product image'}
      onClick={onClose}
    >
      <button
        type="button"
        className="absolute top-4 right-4 text-white/80 hover:text-white p-2"
        aria-label="Close image"
        onClick={onClose}
      >
        <X className="w-7 h-7" />
      </button>

      {valid.length > 1 && (
        <button
          type="button"
          className="absolute left-3 top-1/2 -translate-y-1/2 text-white/70 hover:text-white p-2"
          aria-label="Previous image"
          onClick={(e) => {
            e.stopPropagation();
            prev();
          }}
        >
          <ChevronLeft className="w-9 h-9" />
        </button>
      )}

      {/* stopPropagation so clicking the image itself doesn't close */}
      <div className="flex flex-col items-center gap-2" onClick={(e) => e.stopPropagation()}>
        <img
          src={valid[index]}
          alt={alt || 'Product image'}
          className="max-w-[92vw] max-h-[86vh] object-contain rounded-lg bg-white/5"
        />
        <div className="text-white/80 text-sm flex items-center gap-3">
          {alt && <span className="truncate max-w-[60vw]">{alt}</span>}
          {valid.length > 1 && (
            <span className="text-white/60">
              {index + 1} / {valid.length}
            </span>
          )}
        </div>
      </div>

      {valid.length > 1 && (
        <button
          type="button"
          className="absolute right-3 top-1/2 -translate-y-1/2 text-white/70 hover:text-white p-2"
          aria-label="Next image"
          onClick={(e) => {
            e.stopPropagation();
            next();
          }}
        >
          <ChevronRight className="w-9 h-9" />
        </button>
      )}
    </div>
  );
}

export default ImageLightbox;
