import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { hash } from "bcryptjs";

const SEED_DATA = {
  admin: {
    email: "admin@bettervision.in",
    password: process.env.SEED_ADMIN_PASSWORD || "admin123",
    name: "Admin User",
  },
  locations: [
    { name: "Pune", code: "PUN" },
    { name: "Mumbai", code: "MUM" },
    { name: "Delhi", code: "DEL" },
    { name: "Bangalore", code: "BLR" },
    { name: "Hyderabad", code: "HYD" },
  ],
  attributes: {
    // Names MUST match what the frontend looks up via getAttributeOptions()
    // e.g. getAttributeOptions('framematerial') → attr.name === 'framematerial'
    brand: [
      "Ray-Ban",
      "Prada",
      "Gucci",
      "Oakley",
      "Burberry",
      "Carrera",
      "Emporio Armani",
      "BOSS",
      "Tommy Hilfiger",
      "Vogue",
      "IDEE",
      "Fossil",
      "Calvin Klein",
      "Dolce & Gabbana",
      "Versace",
      "Cartier",
      "Mont Blanc",
      "FILA",
      "Esprit",
      "Chopard",
      "Lindberg",
      "Maui Jim",
      "Bvlgari",
      "Michael Kors",
      "Coach",
      "Kate Spade",
      "Tiffany",
    ],
    subbrand: [
      "Boss",
      "Boss Orange",
      "Emporio",
      "Giorgio",
      "Prada Linea Rossa",
      "Prada Sport",
      "RB",
      "RJ",
      "OX",
      "OO",
    ],
    shape: [
      "Aviator",
      "Rectangle",
      "Round",
      "Square",
      "Cateye",
      "Wayfarer",
      "Pilot",
      "Oval",
      "Clubmaster",
      "Geometric",
      "Hexagonal",
      "Navigator",
      "Butterfly",
      "Irregular",
    ],
    framematerial: [
      "Acetate",
      "Metal",
      "Titanium",
      "TR90",
      "Beta Titanium",
      "Stainless Steel",
      "Injected",
      "Nylon",
      "Carbon Fiber",
      "Polyamide",
      "Ultem",
      "Hexetate",
      "Optyl",
      "Memory Metal",
    ],
    templematerial: [
      "Acetate",
      "Metal",
      "Titanium",
      "TR90",
      "Beta Titanium",
      "Stainless Steel",
      "Injected",
      "Nylon",
      "Carbon Fiber",
      "Polyamide",
      "Ultem",
      "Hexetate",
      "Optyl",
      "Memory Metal",
    ],
    framecolor: [
      "Black",
      "Havana",
      "Gold",
      "Silver",
      "Brown",
      "Blue",
      "Tortoise",
      "Rose Gold",
      "Gunmetal",
      "Matte Black",
      "Crystal",
      "White",
      "Red",
      "Green",
      "Pink",
    ],
    templecolor: [
      "Black",
      "Havana",
      "Gold",
      "Silver",
      "Brown",
      "Blue",
      "Tortoise",
      "Rose Gold",
      "Gunmetal",
      "Matte Black",
      "Crystal",
      "White",
      "Red",
      "Green",
      "Pink",
    ],
    frametype: ["Full Frame", "Half Frame", "Rimless", "Supra"],
    gender: ["Men", "Women", "Unisex", "Kids"],
    countryoforigin: [
      "Made in Italy",
      "Made in India",
      "Made in Japan",
      "Made in China",
      "Made in France",
      "Made in USA",
      "Made in Germany",
    ],
    warranty: [
      "1 Year Manufacturer Warranty",
      "2 Year Manufacturer Warranty",
      "No Warranty",
    ],
    lenscolour: [
      "Black",
      "Brown",
      "Green",
      "Grey",
      "Blue",
      "Pink",
      "Yellow",
      "Mirror Silver",
      "Mirror Blue",
      "Mirror Gold",
      "Gradient Brown",
      "Gradient Grey",
    ],
    tint: ["Solid", "Gradient", "Mirror Coated", "Photochromic"],
    lensmaterial: ["CR 39", "Glass", "Polycarbonate", "Nylon", "Trivex"],
    lensUSP: [
      "Blue Protect",
      "Ultra Polar",
      "Photochromic",
      "Anti-Reflective",
      "Scratch Resistant",
    ],
    productusp: [
      "Flexible Frame",
      "Lightweight",
      "Durable",
      "Hypoallergenic",
      "Adjustable Nose Pads",
    ],
    polarization: ["Polarized", "Non Polarized"],
    uvprotection: ["100% UV Protection", "UV 400", "No UV Protection"],
  },
  discountRules: [
    { category: "SPECTACLES", discountPercentage: 10 },
    { category: "SUNGLASSES", discountPercentage: 12.5 },
    { category: "SOLUTIONS", discountPercentage: 11 },
  ],
};

export async function GET(request: NextRequest) {
  try {
    // Check if admin user already exists
    const existingAdmin = await prisma.user.findUnique({
      where: { email: SEED_DATA.admin.email },
    });

    if (existingAdmin) {
      return NextResponse.json({
        success: true,
        message: "Database already seeded",
      });
    }

    // Create admin user with hashed password
    const hashedPassword = await hash(SEED_DATA.admin.password, 12);
    await prisma.user.create({
      data: {
        email: SEED_DATA.admin.email,
        password: hashedPassword,
        name: SEED_DATA.admin.name,
        role: "ADMIN",
      },
    });

    // Locations should be synced from Shopify via the locations API endpoint
    // instead of seeding with mock data. Call POST /api/locations with
    // { action: 'sync_from_shopify' } to populate locations from your Shopify store.

    // Create attribute types and options
    for (const [attrName, options] of Object.entries(SEED_DATA.attributes)) {
      const attributeType = await prisma.attributeType.create({
        data: {
          name: attrName,
          label: attrName.replace(/([A-Z])/g, ' $1').replace(/^./, (s: string) => s.toUpperCase()).trim(),
        },
      });

      for (const option of options) {
        await prisma.attributeOption.create({
          data: {
            value: option,
            attributeTypeId: attributeType.id,
          },
        });
      }
    }

    // Create discount rules
    for (const rule of SEED_DATA.discountRules) {
      await prisma.discountRule.create({
        data: rule,
      });
    }

    return NextResponse.json({
      success: true,
      message: "Database seeded successfully",
    });
  } catch (error) {
    console.error("Seed error:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error seeding database",
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500 }
    );
  }
}
