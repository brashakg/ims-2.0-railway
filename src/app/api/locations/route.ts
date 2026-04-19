import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/prisma';
import { fetchShopifyLocations } from '@/lib/shopify';

export interface Location {
  id: string;
  name: string;
  code: string;
  address?: string;
  isActive: boolean;
  shopifyLocationId?: string;
}

export async function GET(request: NextRequest) {
  try {
    const searchParams = request.nextUrl.searchParams;
    const active = searchParams.get('active');

    let locations = await prisma.location.findMany({
      include: {
        _count: {
          select: {
            products: true,
            variants: true,
            users: true,
          },
        },
      },
      orderBy: [
        // Shopify-linked locations first, then mock/local-only.
        { shopifyLocationId: { sort: 'asc', nulls: 'last' } },
        { name: 'asc' },
      ],
    });

    // Filter by active status if specified
    if (active !== null) {
      const isActive = active === 'true';
      locations = locations.filter((loc) => loc.isActive === isActive);
    }

    return NextResponse.json(locations);
  } catch (error) {
    console.error('Locations fetch error:', error);
    return NextResponse.json(
      { error: 'Failed to fetch locations' },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    // Check if this is a sync request from Shopify
    if (body.action === 'sync_from_shopify') {
      return await syncLocationsFromShopify();
    }

    // Otherwise, create a new location
    const { name, code, address, isActive = true } = body;

    if (!name || !code) {
      return NextResponse.json(
        { error: 'Name and code are required' },
        { status: 400 }
      );
    }

    const location = await prisma.location.create({
      data: {
        name,
        code,
        address,
        isActive,
      },
    });

    return NextResponse.json(location, { status: 201 });
  } catch (error) {
    console.error('Location creation error:', error);
    return NextResponse.json(
      { error: 'Failed to create location' },
      { status: 500 }
    );
  }
}

async function syncLocationsFromShopify() {
  try {
    // Use the shared GraphQL helper (supports OAuth client_credentials)
    const result = await fetchShopifyLocations();

    if (!result.success || !result.locations) {
      const raw = result.error || 'Failed to fetch locations from Shopify';
      // Detect the common missing-scope case and tell the admin exactly
      // what to grant in Shopify instead of surfacing the raw GraphQL error.
      const missingScope =
        /read_locations|read_markets_home|Access denied/i.test(raw);
      return NextResponse.json(
        {
          error: missingScope
            ? 'Shopify access denied — the app is missing the `read_locations` scope. Open Shopify admin → Apps → BV Inventory-1 → API access → add read_locations and reinstall, then retry.'
            : raw,
          scopeIssue: missingScope,
          rawError: raw,
        },
        { status: missingScope ? 403 : 502 }
      );
    }

    const syncedLocations = [];

    for (const loc of result.locations) {
      const locationCode = loc.name
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, '')
        .substring(0, 10) || loc.id.split('/').pop();

      const addressStr = loc.address?.formatted?.join(', ') || null;

      const location = await prisma.location.upsert({
        where: { shopifyLocationId: loc.id },
        update: {
          name: loc.name,
          address: addressStr,
          isActive: loc.isActive ?? true,
        },
        create: {
          name: loc.name,
          code: locationCode!,
          address: addressStr,
          shopifyLocationId: loc.id,
          isActive: loc.isActive ?? true,
        },
      });

      syncedLocations.push(location);
    }

    return NextResponse.json({
      success: true,
      message: `Synced ${syncedLocations.length} locations from Shopify`,
      locations: syncedLocations,
    });
  } catch (error) {
    console.error('Shopify sync error:', error);
    return NextResponse.json(
      {
        error: 'Failed to sync locations from Shopify',
        details: error instanceof Error ? error.message : String(error),
      },
      { status: 500 }
    );
  }
}
