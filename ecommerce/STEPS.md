# BetterVision Inventory Management System (IMS 2.0)

A comprehensive Next.js-based inventory management application designed to replace Excel-based workflows for managing ~8,000 optical products across 5 stock locations in India. Fully integrated with Shopify for real-time product and inventory synchronization.

## 1. Project Overview

**Better Vision** (www.bettervision.in) is a premium optical store chain with multiple locations across India. This IMS 2.0 application:

- Manages inventory for approximately 8,000 optical products (spectacles, sunglasses, contact lens solutions)
- Tracks stock across 5 locations: Pune, Mumbai, Delhi, Bangalore, Hyderabad
- Auto-generates product metadata (titles, SKU, SEO fields, descriptions, tags) from dropdown selections
- Processes product images with background removal and optimization
- Synchronizes all changes to the Shopify store (bokaro-better-vision.myshopify.com) via GraphQL Admin API
- Supports role-based access control (ADMIN, DESIGN_MANAGER, CATALOG_MANAGER)
- Replaces manual Excel-based workflows with a modern, scalable web interface

## 2. Tech Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| Next.js | 14.2.35 | React framework with App Router |
| React | 18.3.1 | UI component library |
| TypeScript | 5.9.3 | Type-safe JavaScript |
| Prisma | 5.22.0 | ORM for database operations |
| SQLite | - | Lightweight relational database |
| NextAuth.js | 4.24.13 | Authentication (JWT + CredentialsProvider) |
| Tailwind CSS | 3.4.1 | Utility-first CSS framework |
| bcryptjs | 3.0.3 | Password hashing |
| Sharp | 0.34.5 | Image processing/optimization |
| Lucide React | 0.577.0 | Icon library |
| remove.bg API | - | AI-powered background removal for images |
| Shopify GraphQL Admin API | 2024-01 | Product & inventory sync |

## 3. Project Structure

```
/bv-app
├── src/
│   ├── app/
│   │   ├── api/                    # API route handlers
│   │   │   ├── auth/
│   │   │   │   └── [...nextauth]/route.ts       # NextAuth configuration
│   │   │   ├── products/route.ts                 # CRUD for products
│   │   │   ├── products/[id]/route.ts            # Single product operations
│   │   │   ├── products/[id]/stock/route.ts      # Stock quantity updates
│   │   │   ├── images/route.ts                   # Image upload & processing
│   │   │   ├── attributes/route.ts               # Dropdown attribute management
│   │   │   ├── locations/route.ts                # Stock location management
│   │   │   ├── users/route.ts                    # User management
│   │   │   ├── shopify/sync/route.ts             # Batch Shopify sync
│   │   │   ├── shopify/status/route.ts           # Shopify connection status
│   │   │   └── seed/route.ts                     # Database initialization
│   │   ├── dashboard/
│   │   │   ├── page.tsx                          # Dashboard home
│   │   │   ├── layout.tsx                        # Dashboard layout & sidebar
│   │   │   ├── products/page.tsx                 # Product list & search
│   │   │   ├── products/new/page.tsx             # Create new product
│   │   │   ├── images/page.tsx                   # Image management & editing
│   │   │   └── admin/
│   │   │       ├── users/page.tsx                # User management (ADMIN)
│   │   │       ├── locations/page.tsx            # Location management (ADMIN)
│   │   │       ├── attributes/page.tsx           # Attribute management (ADMIN)
│   │   │       └── shopify/page.tsx              # Shopify integration status (ADMIN)
│   │   ├── login/page.tsx                        # Login page
│   │   ├── layout.tsx                            # Root layout
│   │   ├── page.tsx                              # Home/landing page
│   │   └── globals.css                           # Global styles
│   ├── components/
│   │   ├── SessionProvider.tsx                   # NextAuth session provider
│   │   ├── Sidebar.tsx                           # Navigation sidebar
│   │   └── SearchableDropdown.tsx                # Reusable dropdown component
│   └── lib/
│       ├── auth.ts                               # NextAuth configuration & JWT callbacks
│       ├── prisma.ts                             # Prisma client singleton
│       ├── autoGenerate.ts                       # Auto-generation functions for product metadata
│       └── shopify.ts                            # Shopify GraphQL API client
├── prisma/
│   ├── schema.prisma                             # Database schema & Prisma models
│   └── dev.db                                    # SQLite database (development)
├── public/
│   └── uploads/                                  # Product image storage
├── .env                                          # Environment variables
├── next.config.js                                # Next.js configuration
├── tailwind.config.ts                            # Tailwind CSS configuration
├── tsconfig.json                                 # TypeScript configuration
├── package.json                                  # Dependencies & scripts
└── STEPS.md                                      # This file
```

## 4. Database Schema

### User
Stores user credentials and role assignments.
- **Fields**: id, email, password (hashed), name, role, locationId, createdAt, updatedAt
- **Roles**: ADMIN (all access), DESIGN_MANAGER (photo editing), CATALOG_MANAGER (location-specific)
- **Relations**: One-to-many with Product, one-to-one with Location

### Location
Represents physical stock locations.
- **Fields**: id, name, code (unique), address, isActive, createdAt, updatedAt
- **Values**: Pune (PUN), Mumbai (MUM), Delhi (DEL), Bangalore (BLR), Hyderabad (HYD)
- **Relations**: One-to-many with User, one-to-many with ProductLocation

### AttributeType
Defines dropdown categories (e.g., Brand, Shape, Color).
- **Fields**: id, name (unique), label, sortOrder, createdAt
- **Relations**: One-to-many with AttributeOption
- **Examples**: brand, shape, frame_material, temple_material, frame_color, gender, warranty, lens_colour, lens_material, product_category

### AttributeOption
Individual dropdown values for AttributeTypes.
- **Fields**: id, attributeTypeId, value, sortOrder, isActive, createdAt
- **Unique Constraint**: attributeTypeId + value (no duplicate values within a type)

### DiscountRule
Category-based discount percentages applied during product creation.
- **Fields**: id, category (unique), discountPercentage, createdAt
- **Examples**: SPECTACLES (10%), SUNGLASSES (12.5%), SOLUTIONS (11%)

### Product
Core product model with all optical product attributes.
- **Fields**: 40+ fields including:
  - Basic: id, category, status, shopifyProductId
  - Brand & Identity: brand, subBrand, label, productName, modelNo, colorCode, fullModelNo
  - Frame: shape, frameColor, templeColor, frameMaterial, templeMaterial, frameType
  - Measurements: frameSize, bridge, templeLength, weight
  - Demographics: gender, countryOfOrigin, warranty
  - Lens (sunglasses): lensColour, tint, lensMaterial, lensUSP, polarization, uvProtection
  - Solutions-specific: recommendedFor, instructions, ingredients, benefits, aboutProduct
  - Pricing: mrp, discountedPrice, compareAtPrice
  - Auto-generated: title, sku, seoTitle, seoDescription, pageUrl, tags, htmlDescription
  - Identifiers: gtin, upc
- **Status**: DRAFT, PUBLISHED, ARCHIVED
- **Relations**: One-to-many with ProductImage, ProductLocation, SyncLog; many-to-one with User
- **Unique**: sku field

### ProductImage
Product images with optional background removal.
- **Fields**: id, productId, url, originalUrl, position, shopifyMediaId, isProcessed, createdAt
- **Relations**: Many-to-one with Product (cascading delete)

### ProductLocation
Stock quantities at each location.
- **Fields**: id, productId, locationId, quantity, createdAt, updatedAt
- **Unique Constraint**: productId + locationId (one inventory per product per location)
- **Relations**: Many-to-one with Product and Location (cascading delete)

### SyncLog
Audit trail for Shopify synchronization.
- **Fields**: id, productId, action (CREATE, UPDATE, DELETE, STOCK_UPDATE), status (SUCCESS, FAILED, PENDING), message, createdAt
- **Relations**: Many-to-one with Product (cascading delete)

## 5. API Routes

### Authentication
- **POST /api/auth/callback/credentials** - Login via NextAuth CredentialsProvider
- **GET /api/auth/session** - Get current user session
- **GET /api/auth/signin** - Sign in page (handled by NextAuth)
- **POST /api/auth/signout** - Sign out

### Products
- **GET /api/products** - List products with pagination & filters
  - Query params: `page`, `limit`, `category`, `brand`, `status`, `location`, `search`
  - Returns: paginated array with images, locations, sync logs
- **POST /api/products** - Create product (auto-generates metadata, syncs to Shopify if PUBLISHED)
  - Body: all product fields + images array + locations array
  - Returns: created product + auto-generated field values
- **GET /api/products/[id]** - Get single product
- **PUT /api/products/[id]** - Update product
- **DELETE /api/products/[id]** - Delete product
- **PUT /api/products/[id]/stock** - Update stock quantity at a location
  - Body: `{ locationId, quantity }`
  - Syncs to Shopify if published

### Images
- **POST /api/images** - Upload product image
  - Form data: `file` (multipart/form-data)
  - Optional: remove.bg API background removal
  - Returns: original + processed URLs

### Attributes (Dropdowns)
- **GET /api/attributes** - List all attribute types with options
- **POST /api/attributes** - Create attribute type
- **GET /api/attributes/[id]** - Get attribute type with options
- **PUT /api/attributes/[id]** - Update attribute type
- **DELETE /api/attributes/[id]** - Delete attribute type

### Locations
- **GET /api/locations** - List all locations
- **POST /api/locations** - Create location
- **PUT /api/locations/[id]** - Update location
- **DELETE /api/locations/[id]** - Delete location

### Users
- **GET /api/users** - List users (ADMIN only)
- **POST /api/users** - Create user (ADMIN only)
  - Body: `{ email, password, name, role, locationId }`
- **PUT /api/users/[id]** - Update user (ADMIN only)
- **DELETE /api/users/[id]** - Delete user (ADMIN only)

### Shopify Integration
- **POST /api/shopify/sync** - Batch sync products to Shopify
  - Body: `{ productIds: string[] }`
  - Returns: success/failed/skipped counts + detailed results
- **GET /api/shopify/status** - Check Shopify connection health
  - Returns: API connectivity status, sync queue length

### Database Seeding
- **GET /api/seed** - Initialize database with defaults
  - Creates: admin user, 5 locations, attribute types/options, discount rules
  - Idempotent (safe to call multiple times)

## 6. Authentication & Authorization

### NextAuth Configuration
- **Strategy**: JWT (JSON Web Tokens)
- **Provider**: CredentialsProvider (email + password)
- **Flow**:
  1. User submits email/password on `/login`
  2. Credentials validated against hashed password in database
  3. JWT created containing: id, email, role, locationId
  4. JWT stored in HTTP-only cookie
  5. Session available in middleware/API routes via `getServerSession()`

### JWT Payload Structure
```json
{
  "id": "user_id",
  "email": "user@example.com",
  "role": "ADMIN|DESIGN_MANAGER|CATALOG_MANAGER",
  "locationId": "location_id|null",
  "iat": 1234567890,
  "exp": 1234654290
}
```

### Role-Based Access Control

| Role | Access | Use Cases |
|------|--------|-----------|
| **ADMIN** | Full system access, user management, locations, attributes, all products, all locations | System administration |
| **DESIGN_MANAGER** | Photo editing for all locations, view all products | Photo management & optimization |
| **CATALOG_MANAGER** | Product CRUD for assigned location only, stock management | Location-specific inventory |

### Authorization Checks
- API routes check `session.user.role` and `session.user.locationId`
- UI pages render conditionally based on role
- ProductLocation filtering applied for CATALOG_MANAGER users
- CATALOG_MANAGER cannot edit products from other locations

## 7. Shopify Integration

### Overview
All published products are automatically synced to the Shopify store via GraphQL Admin API. The legacy custom app "IMS 2.0" provides API access.

### Store Details
- **URL**: bokaro-better-vision.myshopify.com
- **Custom App**: IMS 2.0 (legacy)
- **API Version**: 2024-01
- **Authentication**: Access token (shpat_*) in SHOPIFY_ACCESS_TOKEN env var

### Supported Operations

**Product Creation**
- Creates new Shopify product with title, description, images, variants
- Stores Shopify product ID in `Product.shopifyProductId`
- Auto-generated fields (SEO title/description, tags) included

**Product Updates**
- Updates title, description, images, tags, SEO metadata
- Requires existing shopifyProductId

**Inventory Management**
- Updates stock quantities per location via `inventoryAdjustQuantities` mutation
- Uses inventory item ID and location ID
- Supports CORRECTION reason

**Product Deletion**
- Soft-deletes via status change (no Shopify deletion)
- Physical deletion possible but not implemented

### Sync Flow
1. User creates/publishes product in IMS
2. POST request to `/api/products` with status="PUBLISHED"
3. `createProduct()` called with auto-generated metadata
4. GraphQL mutation sent to Shopify
5. Response includes Shopify product ID (gid://shopify/Product/...)
6. Product ID stored in database
7. SyncLog entry created (SUCCESS or FAILED)

### GraphQL Mutations Used
- `productCreate` - Create product with images, variants, SEO
- `productUpdate` - Update product fields and metafields
- `inventoryAdjustQuantities` - Adjust stock by location
- `productDelete` - Archive/delete product

### Error Handling
- Shopify API errors logged in SyncLog
- Failed syncs don't block product creation
- Retry via POST /api/shopify/sync with product IDs
- Validation errors from Shopify returned in response

## 8. Image Processing Pipeline

### Upload Process
1. **Form Submission**: User uploads image via `/dashboard/images` form
2. **POST /api/images**: File received as multipart/form-data
3. **Validation**: Check MIME type starts with "image/"
4. **Storage**: 
   - Save to `/public/uploads/` directory
   - Generate random filename: `{hexHash}-{timestamp}.{ext}`
5. **Background Removal** (optional):
   - If REMOVEBG_API_KEY set, call remove.bg API
   - Send image buffer as PNG
   - Store processed image as `{hexHash}-{timestamp}-nobg.png`
6. **Response**: Return both original and processed URLs

### File Organization
- **Directory**: `/public/uploads/`
- **Naming**: 
  - Original: `{random_hex}-{timestamp}.{extension}`
  - Processed: `{random_hex}-{timestamp}-nobg.png`
- **Serving**: Static files via `/uploads/{filename}` path

### remove.bg API Integration
- **Endpoint**: `https://api.remove.bg/v1.0/removebg`
- **Auth**: X-Api-Key header with REMOVEBG_API_KEY
- **Parameters**: size=auto, type=product
- **Output**: PNG with transparent background
- **Cost**: Credits per image (requires API key)
- **Fallback**: Returns original if API unavailable

### Image in Products
- Images attached to products via ProductImage model
- Multiple images per product allowed
- Each image tracks: original URL, processed URL, position, Shopify media ID
- Images synced to Shopify during product creation

## 9. Auto-Generation Logic

All product metadata auto-generated from dropdown selections. Modifiable before publishing.

### Title Generation
**Formula**: `brand subBrand fullModelNo frameSize frameColor productName category`

**Example**: Ray-Ban Original Aviator RB3025 58 Gold Sunglasses
**Source**: Uses existing or auto-generates from components

### SKU Generation
**Formula**: `{categoryPrefix}{brandCode}{modelNo}{frameSize}`

**Example**: SGRRB34858
**Components**:
- categoryPrefix: Extracted from category dropdown (SG = Sunglasses)
- brandCode: 2-char brand abbreviation
- modelNo: Model number from input
- frameSize: Frame size with non-word chars removed

### SEO Title
**Formula**: `Buy {brand} {fullModelNo} {frameSize} {frameColor} {gender} {category} | Better Vision`

**Example**: Buy Ray-Ban RB3025 58 Gold Men Sunglasses | Better Vision
**Max Length**: ~60 chars (Shopify SEO requirement)

### SEO Description
**Formula**: `Shop authentic {brand} {productName} {shape} {frameType}. {frameColor} frame with {templeColor} temples. {frameMaterial} frame. Best discounted prices with pan-India free shipping. COD available.`

**Example**: Shop authentic Ray-Ban Aviator Square. Gold frame with Gold temples. Titanium frame. Best discounted prices with pan-India free shipping. COD available.
**Max Length**: ~160 chars (Shopify SEO requirement)

### Page URL (Slug)
**Formula**: 
1. Concat title components
2. Lowercase, remove special chars
3. Replace spaces with hyphens
4. Remove leading/trailing hyphens
5. Remove duplicate hyphens

**Example**: "ray-ban-original-aviator-58-gold"

### Tags
**Formula**: `{field}_{value}` concatenated with commas

**Example**: brand_ray-ban, shape_aviator, framecolor_gold, gender_men, category_sunglasses

**Tag Format**: 
- Prefix: lowercase field name
- Underscore separator
- Lowercase value (spaces/special chars removed)

### HTML Description
**Formula**: Styled HTML table with sections:
1. **Product Details**: Frame Color, Temple Color, Shape, Weight, Bridge, Temple Length
2. **Technical Specs**: Frame Material, Temple Material, Frame Type, Lens Material, Polarization
3. **General Info**: Brand, Model, Size, Gender, GTIN, UPC
4. **Warranty** (if present)

**Styling**: Blue section headers, striped table rows, hover effect

### Discounted Price Calculation
**Formula**: `MRP - (MRP * discountPercentage / 100)`

**Rules by Category**:
- SPECTACLES: 10% discount
- SUNGLASSES: 12.5% discount
- SOLUTIONS: 11% discount

**Example**: MRP 5000, Category SUNGLASSES → 5000 - (5000 * 0.125) = 4375

## 10. Getting Started

### Prerequisites
- Node.js 18+ and npm
- Git
- Shopify store (bokaro-better-vision.myshopify.com)
- remove.bg API key (optional, for image background removal)

### Installation Steps

**1. Clone Repository**
```bash
git clone <repository-url>
cd /sessions/zealous-nifty-hawking/bv-app
```

**2. Install Dependencies**
```bash
npm install
```
This also runs `postinstall` script: `prisma generate` (generates Prisma client)

**3. Generate Prisma Client**
```bash
npx prisma generate
```

**4. Push Schema to Database**
```bash
npm run db:push
```
Creates SQLite database and all tables from schema.prisma

**5. Seed Database** (Initialize with defaults)
```bash
npm run db:seed
```
Or via API:
```bash
curl http://localhost:3000/api/seed
```

Creates:
- Admin user: admin@bettervision.in / admin123
- 5 stock locations
- 20+ attribute types with options
- 3 discount rules

**6. Start Development Server**
```bash
npm run dev
```
Runs on `http://localhost:3000`

**7. Login**
- Navigate to `/login`
- Email: `admin@bettervision.in`
- Password: `admin123`

### Development Commands

| Command | Purpose |
|---------|---------|
| `npm run dev` | Start dev server (hot reload) |
| `npm run build` | Build for production |
| `npm run start` | Run production build |
| `npm run db:push` | Sync schema to database |
| `npm run db:seed` | Seed database with initial data |
| `npm run db:studio` | Launch Prisma Studio (GUI for database) |

### Production Deployment
```bash
npm run build
npm run start
```

Environment variables must be set on the production host (Railway, Vercel, etc.)

## 11. Environment Variables

All variables required in `.env` file:

| Variable | Example | Purpose |
|----------|---------|---------|
| **DATABASE_URL** | `file:./dev.db` | SQLite database path (use absolute path in production) |
| **NEXTAUTH_SECRET** | `change-in-production` | JWT signing secret (must be >32 chars in production) |
| **NEXTAUTH_URL** | `http://localhost:3000` | Canonical URL for auth callbacks |
| **SHOPIFY_STORE_URL** | `bokaro-better-vision.myshopify.com` | Shopify store domain |
| **SHOPIFY_ACCESS_TOKEN** | `shpat_...` | Admin API access token from custom app |
| **REMOVEBG_API_KEY** | `api_...` | remove.bg API key for background removal |

### Production Environment Notes
- **NEXTAUTH_SECRET**: Generate with `openssl rand -base64 32`
- **SHOPIFY_ACCESS_TOKEN**: Keep confidential, rotate regularly
- **DATABASE_URL**: Use absolute path or connection string for remote database
- **NEXTAUTH_URL**: Must match deployed URL exactly

## 12. Common Tasks

### Add a New Attribute Type (Dropdown)

**Via API:**
```bash
curl -X POST http://localhost:3000/api/attributes \
  -H "Content-Type: application/json" \
  -d '{
    "name": "lens_type",
    "label": "Lens Type",
    "options": ["Single Vision", "Bifocal", "Progressive"]
  }'
```

**Via Prisma Studio:**
```bash
npm run db:studio
# Then use GUI to add AttributeType and AttributeOptions
```

### Add a New Stock Location

**Via API:**
```bash
curl -X POST http://localhost:3000/api/locations \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Chennai",
    "code": "CHE",
    "address": "123 MG Road, Chennai 600001",
    "isActive": true
  }'
```

### Create a New Product

**Via API:**
```bash
curl -X POST http://localhost:3000/api/products \
  -H "Content-Type: application/json" \
  -d '{
    "brand": "Ray-Ban",
    "category": "SUNGLASSES",
    "fullModelNo": "RB3025",
    "frameSize": "58",
    "frameColor": "Gold",
    "templeColor": "Gold",
    "shape": "Aviator",
    "gender": "Men",
    "mrp": 5000,
    "status": "DRAFT",
    "locations": [{"locationId": "loc1", "quantity": 10}],
    "images": [{"url": "/uploads/image1.jpg"}]
  }'
```

### Update Product Stock

**Via API:**
```bash
curl -X PUT http://localhost:3000/api/products/{productId}/stock \
  -H "Content-Type: application/json" \
  -d '{"locationId": "loc1", "quantity": 15}'
```

### Batch Sync to Shopify

**Via API:**
```bash
curl -X POST http://localhost:3000/api/shopify/sync \
  -H "Content-Type: application/json" \
  -d '{"productIds": ["prod1", "prod2", "prod3"]}'
```

## 13. Known Issues & Notes

### SQLite Limitations
- **Max Database Size**: 2GB (sufficient for ~50,000 products)
- **Concurrent Connections**: Limited (not suitable for 100+ concurrent users)
- **No Built-in Replication**: Manual backups required
- **JSON Queries**: Limited JSON support compared to PostgreSQL
- **Full-Text Search**: Not optimized (linear scans)

**Recommendation**: For production with >50,000 products or >20 concurrent users, migrate to PostgreSQL

### Prisma 5 Specifics
- **Client Generation**: Auto-generated via postinstall script
- **Schema Changes**: Always run `npm run db:push` after modifying schema.prisma
- **Migrations**: Using "db push" mode (no migration files) suitable for development

### TypeScript Workarounds
- **Product Type Flexibility**: `Record<string, any>` used in autoGenerate.ts for dynamic field access
- **Session Extension**: NextAuth types extended via `declare module` in auth.ts
- **JSON Fields**: No native JSON type in SQLite, stored as strings

### Shopify Integration Notes
- **API Version Lock**: Using 2024-01 (verify support before upgrading)
- **Rate Limiting**: Shopify API has rate limits (~40 calls/second)
- **Media Upload**: Images added via URL only (no direct upload to Shopify)
- **Variant Handling**: Creates single default variant per product

### Performance Considerations
- **Product Listing**: Pagination recommended (default 10, max 1000)
- **Image Processing**: remove.bg API calls are slow (~5-10 seconds)
- **Shopify Sync**: Batch sync via `/api/shopify/sync` to avoid rate limits
- **Database Queries**: Add indexes for frequently filtered fields (brand, category, status)

### Security Considerations
- **Password Hashing**: bcryptjs with 12 salt rounds (>100ms per hash)
- **JWT Secret**: Change NEXTAUTH_SECRET in production
- **API Token**: Rotate SHOPIFY_ACCESS_TOKEN quarterly
- **Image Storage**: Public directory accessible without auth
- **Rate Limiting**: Implement rate limiting on API endpoints in production
- **Input Validation**: Add schema validation (zod/yup) to all API routes

## 14. Deployment

### Recommended: Railway

**Deploy to Railway:**
1. Push code to GitHub repository
2. Connect Railway to GitHub account
3. Create new project in Railway dashboard
4. Add GitHub repository
5. Set environment variables
6. Deploy branch automatically
7. Domain assigned automatically

### Pre-Deployment Checklist
- [ ] Set NEXTAUTH_SECRET to secure random value (32+ chars)
- [ ] Update NEXTAUTH_URL to production domain
- [ ] Migrate database to PostgreSQL (if expected >50k products)
- [ ] Enable HTTPS/SSL
- [ ] Test Shopify sync end-to-end
- [ ] Configure image storage
- [ ] Set up error logging
- [ ] Set up monitoring
- [ ] Create database backups

---

**Last Updated**: March 2026  
**Version**: 1.0  

For additional help, check API route comments in source code or Shopify GraphQL documentation.
