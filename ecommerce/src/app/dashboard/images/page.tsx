'use client';

import { useState, useEffect } from 'react';
import { Upload, Loader2, Trash2 } from 'lucide-react';
import SearchableDropdown from '@/components/SearchableDropdown';
import Topbar from '@/components/Topbar';

interface ProductImage {
  id: string;
  url: string;
  productId: string;
  product?: {
    id: string;
    title: string;
    brand: string;
  };
  processed: boolean;
  createdAt: string;
}

interface Location {
  id: string;
  name: string;
}

export default function ImagesPage() {
  const [images, setImages] = useState<ProductImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [selectedImages, setSelectedImages] = useState<Set<string>>(new Set());

  // Filters
  const [processedFilter, setProcessedFilter] = useState('All');
  const [location, setLocation] = useState('');
  const [locations, setLocations] = useState<Location[]>([]);

  // Modal state
  const [selectedImage, setSelectedImage] = useState<ProductImage | null>(null);

  // Fetch locations
  useEffect(() => {
    const fetchLocations = async () => {
      try {
        const res = await fetch('/api/locations?excludeSynthetic=true');
        const data: Location[] = await res.json();
        setLocations(data || []);
      } catch (error) {
        console.error('Error fetching locations:', error);
      }
    };
    fetchLocations();
  }, []);

  // Fetch images
  useEffect(() => {
    const fetchImages = async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({
          ...(processedFilter !== 'All' && {
            processed: processedFilter === 'Processed' ? 'true' : 'false',
          }),
          ...(location && { location }),
        });

        const res = await fetch(`/api/images?${params}`);
        const data = await res.json();
        setImages(data || []);
      } catch (error) {
        console.error('Error fetching images:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchImages();
  }, [processedFilter, location]);

  const handleSelectImage = (id: string) => {
    const newSelected = new Set(selectedImages);
    if (newSelected.has(id)) {
      newSelected.delete(id);
    } else {
      newSelected.add(id);
    }
    setSelectedImages(newSelected);
  };

  const handleSelectAll = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.checked) {
      setSelectedImages(new Set(images.map((img) => img.id)));
    } else {
      setSelectedImages(new Set());
    }
  };

  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    setUploadLoading(true);
    try {
      for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch('/api/images', {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        if (data.id) {
          setImages((prev) => [data, ...prev]);
        }
      }
    } catch (error) {
      console.error('Error uploading image:', error);
    } finally {
      setUploadLoading(false);
    }
  };

  const handleDeleteImage = async (id: string) => {
    if (!confirm('Are you sure you want to delete this image?')) return;

    try {
      await fetch(`/api/images/${id}`, { method: 'DELETE' });
      setImages((prev) => prev.filter((img) => img.id !== id));
      setSelectedImage(null);
    } catch (error) {
      console.error('Error deleting image:', error);
    }
  };

  return (
    <>
      <Topbar
        title="Image Management"
        subtitle="Upload + browse product images"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Images' }]}
        primaryAction={null}
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        {/* Upload Section */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-blue-500 transition-colors cursor-pointer">
            <input
              type="file"
              multiple
              accept="image/*"
              onChange={handleImageUpload}
              disabled={uploadLoading}
              className="hidden"
              id="upload-input"
            />
            <label htmlFor="upload-input" className="cursor-pointer">
              {uploadLoading ? (
                <Loader2 className="w-8 h-8 animate-spin mx-auto text-blue-600 mb-2" />
              ) : (
                <Upload className="w-8 h-8 text-gray-400 mx-auto mb-2" />
              )}
              <p className="text-sm text-gray-600">
                Drag and drop images here or click to select
              </p>
            </label>
          </div>
        </div>

        {/* Filters & Bulk Actions */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="flex items-center gap-4 mb-4">
            <div className="flex-1 grid grid-cols-2 md:grid-cols-3 gap-4">
              <SearchableDropdown
                label="Processed"
                options={['All', 'Processed', 'Unprocessed']}
                value={processedFilter}
                onChange={setProcessedFilter}
              />
              <SearchableDropdown
                label="Location"
                options={['All', ...locations.map((l) => l.name)]}
                value={location ? locations.find((l) => l.id === location)?.name || '' : 'All'}
                onChange={(val) => {
                  if (val === 'All') {
                    setLocation('');
                  } else {
                    const loc = locations.find((l) => l.name === val);
                    setLocation(loc?.id || '');
                  }
                }}
              />
            </div>
          </div>

          {selectedImages.size > 0 && (
            <p className="text-sm text-gray-600">
              {selectedImages.size} image(s) selected
            </p>
          )}
        </div>

        {/* Images Grid */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {loading ? (
            <div className="col-span-full text-center py-12">
              <Loader2 className="w-8 h-8 animate-spin mx-auto text-blue-600" />
            </div>
          ) : images.length > 0 ? (
            images.map((image) => (
              <div
                key={image.id}
                className="relative group bg-white rounded-lg overflow-hidden shadow hover:shadow-lg transition-shadow"
              >
                {/* Checkbox */}
                <div className="absolute top-2 left-2 z-10">
                  <input
                    type="checkbox"
                    checked={selectedImages.has(image.id)}
                    onChange={() => handleSelectImage(image.id)}
                    className="w-4 h-4 rounded border-gray-300"
                  />
                </div>

                {/* Image */}
                <div
                  onClick={() => {
                    setSelectedImage(image);
                    ;
                  }}
                  className="relative h-48 overflow-hidden cursor-pointer bg-gray-200"
                >
                  <img
                    src={image.url}
                    alt="Product"
                    className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                  />
                  {image.processed && (
                    <div className="absolute top-2 right-2 bg-green-600 text-white px-2 py-1 rounded text-xs font-semibold">
                      Processed
                    </div>
                  )}
                </div>

                {/* Product Info */}
                <div className="p-3">
                  {image.product ? (
                    <>
                      <p className="text-xs font-semibold text-gray-900 truncate">
                        {image.product.brand}
                      </p>
                      <p className="text-xs text-gray-600 truncate">
                        {image.product.title}
                      </p>
                    </>
                  ) : (
                    <p className="text-xs text-gray-500">Unassigned</p>
                  )}
                </div>

                {/* Actions */}
                <div className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteImage(image.id);
                    }}
                    className="bg-red-600 text-white p-2 rounded-full hover:bg-red-700"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="col-span-full text-center py-12 text-gray-500">
              No images found
            </div>
          )}
        </div>
      </div>

      {/* Image Preview Modal */}
      {selectedImage && (
        <div
          className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4"
          onClick={() => setSelectedImage(null)}
        >
          <div
            className="bg-white rounded-lg shadow-lg max-w-4xl w-full max-h-96 flex gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex-1 flex items-center justify-center bg-gray-100">
              <img
                src={selectedImage.url}
                alt="Original"
                className="max-h-96 max-w-full object-contain"
              />
            </div>

            <div className="p-4 bg-gray-50 overflow-y-auto">
              <h3 className="text-sm font-semibold text-gray-900 mb-4">
                Image Details
              </h3>
              <div className="space-y-3 text-xs">
                {selectedImage.product && (
                  <>
                    <div>
                      <p className="text-gray-600">Product</p>
                      <p className="font-medium text-gray-900">
                        {selectedImage.product.title}
                      </p>
                    </div>
                    <div>
                      <p className="text-gray-600">Brand</p>
                      <p className="font-medium text-gray-900">
                        {selectedImage.product.brand}
                      </p>
                    </div>
                  </>
                )}
                <div>
                  <p className="text-gray-600">Status</p>
                  <p
                    className={`font-medium ${
                      selectedImage.processed
                        ? 'text-green-600'
                        : 'text-yellow-600'
                    }`}
                  >
                    {selectedImage.processed ? 'Processed' : 'Unprocessed'}
                  </p>
                </div>
                <div>
                  <p className="text-gray-600">Uploaded</p>
                  <p className="font-medium text-gray-900">
                    {new Date(selectedImage.createdAt).toLocaleDateString()}
                  </p>
                </div>
                <div className="pt-2 border-t border-gray-200 flex gap-2">
                  <button
                    onClick={() => handleDeleteImage(selectedImage.id)}
                    className="flex-1 bg-red-600 text-white px-3 py-2 rounded text-xs hover:bg-red-700"
                  >
                    <Trash2 className="w-3 h-3 inline mr-1" />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
