// ============================================================================
// IMS 2.0 - E-commerce Storefront
// ============================================================================
// Public-facing online store for customers

import { useState, useEffect } from 'react';
import {
  Search,
  ShoppingCart,
  Star,
  Heart,
  Eye,
  Package,
  Truck,
  Shield,
  Phone,
  Mail,
  MapPin,
  Loader2,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';

interface Product {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  description: string;
  mrp: number;
  offerPrice: number;
  discount: number;
  rating: number;
  reviewCount: number;
  images: string[];
  inStock: boolean;
  stockQuantity: number;
  specifications: Record<string, string>;
}

type SortOption = 'featured' | 'price-low' | 'price-high' | 'newest' | 'popular';

export function StorefrontPage() {
  const toast = useToast();

  const [products, setProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [sortBy, setSortBy] = useState<SortOption>('featured');
  const [cartCount, setCartCount] = useState(0);
  const [wishlistItems, setWishlistItems] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadProducts();
  }, []);

  const loadProducts = async () => {
    setIsLoading(true);
    try {
      // Mock data - in production, fetch from API
      await new Promise(resolve => setTimeout(resolve, 1000));

      const mockProducts: Product[] = [
        {
          id: '1',
          sku: 'FR-RB-001',
          name: 'Ray-Ban Aviator Classic Gold Frame',
          brand: 'Ray-Ban',
          category: 'Sunglasses',
          description: 'Iconic aviator sunglasses with gold metal frame and green classic lenses.',
          mrp: 12000,
          offerPrice: 9600,
          discount: 20,
          rating: 4.8,
          reviewCount: 245,
          images: ['/placeholder-sunglasses.jpg'],
          inStock: true,
          stockQuantity: 15,
          specifications: {
            'Frame Material': 'Metal',
            'Lens Material': 'Glass',
            'UV Protection': '100%',
            'Frame Color': 'Gold',
            'Lens Color': 'Green',
          },
        },
        {
          id: '2',
          sku: 'CL-ACU-023',
          name: 'Acuvue Oasys Monthly Contact Lenses (6 Pack)',
          brand: 'Acuvue',
          category: 'Contact Lenses',
          description: 'Monthly disposable silicone hydrogel contact lenses with superior comfort.',
          mrp: 2500,
          offerPrice: 2200,
          discount: 12,
          rating: 4.7,
          reviewCount: 182,
          images: ['/placeholder-cl.jpg'],
          inStock: true,
          stockQuantity: 50,
          specifications: {
            'Pack Size': '6 lenses',
            'Replacement Schedule': 'Monthly',
            'Material': 'Silicone Hydrogel',
            'Water Content': '38%',
            'Oxygen Transmissibility': 'High',
          },
        },
        {
          id: '3',
          sku: 'FR-OAK-112',
          name: 'Oakley Holbrook Matte Black Sunglasses',
          brand: 'Oakley',
          category: 'Sunglasses',
          description: 'Modern classic design with Prizm lens technology for enhanced color and contrast.',
          mrp: 15000,
          offerPrice: 13500,
          discount: 10,
          rating: 4.9,
          reviewCount: 328,
          images: ['/placeholder-sunglasses.jpg'],
          inStock: true,
          stockQuantity: 8,
          specifications: {
            'Frame Material': 'O Matter',
            'Lens Technology': 'Prizm',
            'UV Protection': '100%',
            'Frame Color': 'Matte Black',
            'Lens Color': 'Grey',
          },
        },
        {
          id: '4',
          sku: 'ACC-OPT-045',
          name: 'Premium Lens Cleaning Kit with Microfiber Cloth',
          brand: 'Opticare',
          category: 'Accessories',
          description: 'Complete lens care solution with cleaning spray, microfiber cloth, and storage case.',
          mrp: 500,
          offerPrice: 399,
          discount: 20,
          rating: 4.5,
          reviewCount: 89,
          images: ['/placeholder-accessory.jpg'],
          inStock: true,
          stockQuantity: 100,
          specifications: {
            'Contents': 'Spray (100ml), Cloth, Case',
            'Safe For': 'All lens types',
            'Anti-fog': 'Yes',
            'Alcohol-free': 'Yes',
          },
        },
      ];

      setProducts(mockProducts);
    } catch (error: any) {
      toast.error('Failed to load products');
    } finally {
      setIsLoading(false);
    }
  };

  const toggleWishlist = (productId: string) => {
    const newWishlist = new Set(wishlistItems);
    if (newWishlist.has(productId)) {
      newWishlist.delete(productId);
      toast.success('Removed from wishlist');
    } else {
      newWishlist.add(productId);
      toast.success('Added to wishlist');
    }
    setWishlistItems(newWishlist);
  };

  const addToCart = (product: Product) => {
    setCartCount(prev => prev + 1);
    toast.success(`${product.name} added to cart!`);
  };

  const getFilteredProducts = () => {
    let filtered = products;

    // Filter by category
    if (selectedCategory !== 'all') {
      filtered = filtered.filter(p => p.category === selectedCategory);
    }

    // Filter by search
    if (searchQuery) {
      filtered = filtered.filter(
        p =>
          p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
          p.brand.toLowerCase().includes(searchQuery.toLowerCase())
      );
    }

    // Sort
    switch (sortBy) {
      case 'price-low':
        filtered.sort((a, b) => a.offerPrice - b.offerPrice);
        break;
      case 'price-high':
        filtered.sort((a, b) => b.offerPrice - a.offerPrice);
        break;
      case 'popular':
        filtered.sort((a, b) => b.reviewCount - a.reviewCount);
        break;
      case 'newest':
        // Already in order
        break;
      default:
        // Featured
        filtered.sort((a, b) => b.rating - a.rating);
    }

    return filtered;
  };

  const filteredProducts = getFilteredProducts();
  const categories = ['all', ...Array.from(new Set(products.map(p => p.category)))];

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            {/* Logo */}
            <div className="flex items-center gap-2">
              <Eye className="w-8 h-8 text-purple-600" />
              <div>
                <h1 className="text-2xl font-bold text-gray-900">Better Vision Optics</h1>
                <p className="text-xs text-gray-500">Premium Eyewear & Eyecare</p>
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-4">
              <button className="relative p-2 hover:bg-gray-100 rounded-lg transition-colors">
                <Heart className="w-6 h-6 text-gray-600" />
                {wishlistItems.size > 0 && (
                  <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">
                    {wishlistItems.size}
                  </span>
                )}
              </button>
              <button className="relative p-2 hover:bg-gray-100 rounded-lg transition-colors">
                <ShoppingCart className="w-6 h-6 text-gray-600" />
                {cartCount > 0 && (
                  <span className="absolute -top-1 -right-1 w-5 h-5 bg-purple-600 text-white text-xs rounded-full flex items-center justify-center">
                    {cartCount}
                  </span>
                )}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Hero Banner */}
      <div className="bg-gradient-to-r from-purple-600 to-blue-600 text-white py-12">
        <div className="max-w-7xl mx-auto px-4">
          <h2 className="text-4xl font-bold mb-2">Winter Sale - Up to 30% Off!</h2>
          <p className="text-xl mb-6">Premium eyewear from top brands at unbeatable prices</p>
          <div className="flex gap-4">
            <div className="flex items-center gap-2">
              <Truck className="w-5 h-5" />
              <span>Free Delivery</span>
            </div>
            <div className="flex items-center gap-2">
              <Shield className="w-5 h-5" />
              <span>100% Authentic</span>
            </div>
            <div className="flex items-center gap-2">
              <Package className="w-5 h-5" />
              <span>Easy Returns</span>
            </div>
          </div>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="max-w-7xl mx-auto px-4 py-6">
        <div className="bg-white rounded-lg shadow-sm p-4 mb-6">
          <div className="flex flex-col tablet:flex-row gap-4">
            {/* Search */}
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search for sunglasses, contact lenses, frames..."
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              />
            </div>

            {/* Category Filter */}
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
              className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
            >
              {categories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat === 'all' ? 'All Categories' : cat}
                </option>
              ))}
            </select>

            {/* Sort */}
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortOption)}
              className="px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
            >
              <option value="featured">Featured</option>
              <option value="popular">Most Popular</option>
              <option value="newest">Newest First</option>
              <option value="price-low">Price: Low to High</option>
              <option value="price-high">Price: High to Low</option>
            </select>
          </div>
        </div>

        {/* Products Grid */}
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-12 h-12 animate-spin text-purple-600" />
          </div>
        ) : filteredProducts.length === 0 ? (
          <div className="text-center py-20 text-gray-500">
            <Package className="w-16 h-16 mx-auto mb-4 opacity-50" />
            <p className="text-xl font-medium">No products found</p>
            <p>Try adjusting your search or filters</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 tablet:grid-cols-3 laptop:grid-cols-4 gap-6">
            {filteredProducts.map((product) => (
              <div
                key={product.id}
                className="bg-white rounded-lg shadow-sm hover:shadow-md transition-shadow overflow-hidden group"
              >
                {/* Product Image */}
                <div className="relative aspect-square bg-gray-100">
                  <img
                    src={product.images[0]}
                    alt={product.name}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      (e.target as HTMLImageElement).src =
                        'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="400"%3E%3Crect fill="%23f0f0f0" width="400" height="400"/%3E%3Ctext fill="%239ca3af" font-family="sans-serif" font-size="24" x="50%25" y="50%25" text-anchor="middle" dominant-baseline="middle"%3ENo Image%3C/text%3E%3C/svg%3E';
                    }}
                  />
                  {/* Discount Badge */}
                  {product.discount > 0 && (
                    <div className="absolute top-2 right-2 bg-red-500 text-white px-2 py-1 rounded-md text-sm font-bold">
                      {product.discount}% OFF
                    </div>
                  )}
                  {/* Wishlist Button */}
                  <button
                    onClick={() => toggleWishlist(product.id)}
                    className="absolute top-2 left-2 p-2 bg-white rounded-full shadow-md hover:bg-gray-50 transition-colors"
                  >
                    <Heart
                      className={`w-5 h-5 ${
                        wishlistItems.has(product.id)
                          ? 'fill-red-500 text-red-500'
                          : 'text-gray-600'
                      }`}
                    />
                  </button>
                  {/* Stock Badge */}
                  {!product.inStock && (
                    <div className="absolute inset-0 bg-black bg-opacity-50 flex items-center justify-center">
                      <span className="bg-white text-gray-900 px-4 py-2 rounded-lg font-medium">
                        Out of Stock
                      </span>
                    </div>
                  )}
                </div>

                {/* Product Info */}
                <div className="p-4">
                  <p className="text-sm text-gray-500 mb-1">{product.brand}</p>
                  <h3 className="font-medium text-gray-900 mb-2 line-clamp-2 min-h-[2.5rem]">
                    {product.name}
                  </h3>

                  {/* Rating */}
                  <div className="flex items-center gap-1 mb-2">
                    <Star className="w-4 h-4 fill-yellow-400 text-yellow-400" />
                    <span className="text-sm font-medium text-gray-900">{product.rating}</span>
                    <span className="text-sm text-gray-500">({product.reviewCount})</span>
                  </div>

                  {/* Price */}
                  <div className="flex items-baseline gap-2 mb-3">
                    <span className="text-2xl font-bold text-gray-900">
                      ₹{product.offerPrice.toLocaleString('en-IN')}
                    </span>
                    {product.discount > 0 && (
                      <span className="text-sm text-gray-500 line-through">
                        ₹{product.mrp.toLocaleString('en-IN')}
                      </span>
                    )}
                  </div>

                  {/* Actions */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => addToCart(product)}
                      disabled={!product.inStock}
                      className="flex-1 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white py-2 px-4 rounded-lg font-medium transition-colors flex items-center justify-center gap-2"
                    >
                      <ShoppingCart className="w-4 h-4" />
                      Add to Cart
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <footer className="bg-gray-900 text-white mt-20">
        <div className="max-w-7xl mx-auto px-4 py-12">
          <div className="grid grid-cols-1 tablet:grid-cols-3 gap-8">
            {/* About */}
            <div>
              <h3 className="text-lg font-bold mb-4">Better Vision Optics</h3>
              <p className="text-gray-400 text-sm mb-4">
                Your trusted partner for premium eyewear and eyecare solutions. Quality products
                from top brands at competitive prices.
              </p>
            </div>

            {/* Quick Links */}
            <div>
              <h3 className="text-lg font-bold mb-4">Quick Links</h3>
              <ul className="space-y-2 text-sm text-gray-400">
                <li>
                  <a href="#" className="hover:text-white transition-colors">
                    About Us
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-white transition-colors">
                    Contact Us
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-white transition-colors">
                    Shipping Policy
                  </a>
                </li>
                <li>
                  <a href="#" className="hover:text-white transition-colors">
                    Return Policy
                  </a>
                </li>
              </ul>
            </div>

            {/* Contact */}
            <div>
              <h3 className="text-lg font-bold mb-4">Contact Us</h3>
              <ul className="space-y-3 text-sm text-gray-400">
                <li className="flex items-center gap-2">
                  <Phone className="w-4 h-4" />
                  <span>+91 98765 43210</span>
                </li>
                <li className="flex items-center gap-2">
                  <Mail className="w-4 h-4" />
                  <span>support@bettervision.com</span>
                </li>
                <li className="flex items-start gap-2">
                  <MapPin className="w-4 h-4 mt-0.5" />
                  <span>123 Vision Street, Mumbai, Maharashtra 400001</span>
                </li>
              </ul>
            </div>
          </div>

          <div className="border-t border-gray-800 mt-8 pt-8 text-center text-sm text-gray-400">
            <p>&copy; 2025 Better Vision Optics. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
