// ============================================================================
// Quick Add - the product-image machinery (upload / remove / remove-bg).
// ============================================================================
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet). `images` itself
// stays in useQuickAddForm (it rides in ProductFormValues, so the reset /
// prefill / payload paths own it); everything AROUND it - the in-flight flags,
// the hidden file input, the drop target and the three handlers - lives here.
// The setter is passed in, so there is exactly one images array as before.

import { useCallback, useRef, useState } from 'react';
import { useToast } from '../../../context/ToastContext';
import { productApi } from '../../../services/api/products';

export function useProductImages(setImages: React.Dispatch<React.SetStateAction<string[]>>) {
  const toast = useToast();
  const [uploadingImages, setUploadingImages] = useState(false);
  // URLs currently being background-removed (per-image spinner + disabled btn).
  const [editingImages, setEditingImages] = useState<Set<string>>(new Set());
  const [dragActive, setDragActive] = useState(false);
  const imageInputRef = useRef<HTMLInputElement | null>(null);

  // ---- Product image upload (Part 1) ---------------------------------------
  // Upload each selected/dropped file via productApi.uploadProductImage and
  // append the returned self-hosted URL to `images`. Fail-soft: a failed upload
  // just isn't added (a toast names how many, so nothing silently vanishes).
  const uploadImageFiles = useCallback(
    async (files: File[]) => {
      // Keep files with an EMPTY reported type too (some browsers report ''
      // for HEIC/AVIF): the backend validates the real mime and its clear 400
      // detail is surfaced below — better than silently dropping the file.
      const imageFiles = files.filter((f) => f.type === '' || f.type.startsWith('image/'));
      if (imageFiles.length === 0) {
        if (files.length > 0) toast.error('Only image files can be uploaded.');
        return;
      }
      setUploadingImages(true);
      let failed = 0;
      let lastError = '';
      const uploaded: string[] = [];
      for (const file of imageFiles) {
        try {
          const res = await productApi.uploadProductImage(file);
          if (res?.url) uploaded.push(res.url);
          else failed += 1;
        } catch (err) {
          failed += 1;
          if (err instanceof Error && err.message) lastError = err.message;
        }
      }
      if (uploaded.length > 0) {
        setImages((prev) => [...prev, ...uploaded]);
      }
      if (failed > 0) {
        toast.warning(
          `${failed} image${failed > 1 ? 's' : ''} could not be uploaded${uploaded.length ? ' (the rest were added)' : ''}.${lastError ? ` ${lastError}` : ''}`
        );
      } else if (uploaded.length > 0) {
        toast.success(`${uploaded.length} image${uploaded.length > 1 ? 's' : ''} uploaded.`);
      }
      setUploadingImages(false);
    },
    // setImages is a useState setter (stable by contract) - naming it here is a
    // no-op at runtime; the rule just can't see that across the module edge.
    [setImages, toast]
  );

  const onImageInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files ? Array.from(e.target.files) : [];
      void uploadImageFiles(files);
      // Reset so the same file can be re-picked after a remove.
      e.target.value = '';
    },
    [uploadImageFiles]
  );

  const onImageDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragActive(false);
      const files = e.dataTransfer?.files ? Array.from(e.dataTransfer.files) : [];
      void uploadImageFiles(files);
    },
    [uploadImageFiles]
  );

  const removeImage = useCallback((url: string) => {
    setImages((prev) => prev.filter((u) => u !== url));
  }, [setImages]);

  // A self-hosted product image looks like /api/v1/products/image/{file_id}
  // (absolute or relative). Return the file_id for those; null for an external
  // URL (e.g. a pasted brand-site photo) — editImage RE-HOSTS those first.
  const fileIdFromImageUrl = useCallback((url: string): string | null => {
    const m = /\/api\/v1\/products\/image\/([^/?#]+)$/.exec(url);
    return m ? m[1] : null;
  }, []);

  // Run background removal + catalog-standard resize on one image and REPLACE
  // that entry (keeping array order) with the cleaned result. An EXTERNAL url
  // (external photo) is re-hosted into our store first, then edited — so the
  // Remove-background button works on every image, not only manual uploads.
  const editImage = useCallback(
    async (url: string) => {
      setEditingImages((prev) => new Set(prev).add(url));
      try {
        let fileId = fileIdFromImageUrl(url);
        if (!fileId) {
          const hosted = await productApi.rehostProductImage(url);
          fileId = hosted?.file_id || null;
          if (!fileId) {
            toast.error("Couldn't copy this image into your store, please try again.");
            return;
          }
        }
        const res = await productApi.editProductImage(fileId);
        if (res?.url) {
          setImages((prev) => prev.map((u) => (u === url ? res.url : u)));
          toast.success('Background removed.');
        } else {
          toast.error("Couldn't remove background, please try again.");
        }
      } catch (err) {
        // The shared axios interceptor rejects with a plain Error whose message
        // is already the backend `detail` string (for a 4xx). The "not set up"
        // 400 carries the "Settings -> Integrations" hint — surface that verbatim
        // so the operator knows how to enable it; otherwise a generic retry line.
        const msg = err instanceof Error ? err.message : '';
        if (msg.includes('Settings -> Integrations')) {
          toast.error(msg);
        } else {
          toast.error("Couldn't remove background, please try again.");
        }
      } finally {
        setEditingImages((prev) => {
          const next = new Set(prev);
          next.delete(url);
          return next;
        });
      }
    },
    [fileIdFromImageUrl, setImages, toast]
  );

  return {
    uploadingImages,
    editingImages,
    dragActive,
    setDragActive,
    imageInputRef,
    uploadImageFiles,
    onImageInputChange,
    onImageDrop,
    removeImage,
    editImage,
  };
}
