// Anthropic API wrapper using fetch — no SDK dependency, so package-lock.json
// stays in sync with package.json without any extra install step.
// Uses claude-haiku-4-5 with an ephemeral-cached system prompt so bulk
// runs pay full input-token cost only on the first call.

const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const ANTHROPIC_VERSION = "2023-06-01";
const MODEL = "claude-haiku-4-5-20251001";

const SYSTEM_PROMPT = `You generate SEO metadata for a multi-category retail catalog (BetterVision — optical retail chain in India that also stocks watches). Products span Spectacles, Sunglasses, Watches, Contact Lenses, and related accessories.

Given a product's attributes, output exactly one JSON object with two fields:
- "seoTitle"       : 55-70 characters. Title Case. Includes the brand, model, and the 1-2 most selling-relevant attributes. Ends with the product type when it fits (e.g. "Eyeglasses", "Sunglasses", "Analog Watch", "Contact Lenses"). Infer the product type from the "Category" attribute — do NOT assume eyewear.
- "seoDescription" : 140-160 characters. One or two natural-sounding sentences. Mentions the brand, the main attribute or two (shape, color, material, gender, movement type for watches, lens type for contacts, etc.), and a buy-now call to action (e.g. "Shop online", "Buy now", "Order today", "Shop at BetterVision").

Rules:
- Category handling: "SPECTACLES" -> Eyeglasses. "SUNGLASSES" or "SUNGLASS" -> Sunglasses. "WATCH" or "WATCHES" -> Watch (specify Analog / Digital / Chronograph if present). "CONTACT LENSES" -> Contact Lenses. "SOLUTIONS" -> Contact Lens Solution.
- For watches: lens/frame attributes do not apply. Focus on brand + color + strap material + dial color + movement.
- For eyewear: frame shape, material, color, and lens features matter.
- Never fabricate features that are not in the input. If an attribute is unknown, skip it.
- No emojis, no ALL CAPS phrases, no marketing hyperbole ("amazing", "best-ever", etc.).
- Avoid repeating the same word back-to-back.
- Keep descriptions factual, short, and scannable.
- Output ONLY the JSON object — no markdown, no code fences, no preamble, no trailing commentary.`;

export interface SeoInput {
  brand: string | null;
  modelNo: string | null;
  title?: string | null;
  category?: string | null;
  shape?: string | null;
  frameMaterial?: string | null;
  frameType?: string | null;
  gender?: string | null;
  countryOfOrigin?: string | null;
  warranty?: string | null;
  lensMaterial?: string | null;
  polarization?: string | null;
  uvProtection?: string | null;
  productUSP?: string | null;
  variantColors?: string[];
  variantSizes?: string[];
}

export interface GeneratedSeo {
  seoTitle: string;
  seoDescription: string;
  tokensIn?: number;
  tokensOut?: number;
  cacheReadTokens?: number;
}

interface AnthropicMessageResponse {
  content: Array<{ type: string; text?: string }>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_creation_input_tokens?: number;
    cache_read_input_tokens?: number;
  };
  error?: { type: string; message: string };
}

function attributesToUserPrompt(input: SeoInput): string {
  const lines: string[] = [];
  const push = (label: string, value: string | null | undefined) => {
    if (value && value.trim()) lines.push(`${label}: ${value.trim()}`);
  };
  push("Brand", input.brand);
  push("Model", input.modelNo);
  if (input.title && input.title.trim()) push("Current Title", input.title);
  push("Category", input.category);
  push("Shape", input.shape);
  push("Frame Material", input.frameMaterial);
  push("Frame Type", input.frameType);
  push("Gender", input.gender);
  push("Country of Origin", input.countryOfOrigin);
  push("Warranty", input.warranty);
  push("Lens Material", input.lensMaterial);
  push("Polarization", input.polarization);
  push("UV Protection", input.uvProtection);
  push("Product USP", input.productUSP);
  if (input.variantColors && input.variantColors.length > 0) {
    push("Available Colors", input.variantColors.slice(0, 8).join(", "));
  }
  if (input.variantSizes && input.variantSizes.length > 0) {
    push("Sizes", input.variantSizes.slice(0, 6).join(", "));
  }
  return lines.join("\n");
}

export async function generateSeoForProduct(
  input: SeoInput
): Promise<GeneratedSeo> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error(
      "ANTHROPIC_API_KEY is not set. Add it to Railway environment variables."
    );
  }

  const userPrompt = attributesToUserPrompt(input);
  if (!userPrompt.trim()) {
    throw new Error("Cannot generate SEO: no product attributes provided");
  }

  const body = {
    model: MODEL,
    max_tokens: 350,
    system: [
      {
        type: "text",
        text: SYSTEM_PROMPT,
        cache_control: { type: "ephemeral" },
      },
    ],
    messages: [
      { role: "user", content: userPrompt },
      // Prefill assistant turn so Claude is forced to continue a JSON object.
      { role: "assistant", content: '{"seoTitle":' },
    ],
  };

  const response = await fetch(ANTHROPIC_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": ANTHROPIC_VERSION,
      "anthropic-beta": "prompt-caching-2024-07-31",
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(
      `Anthropic API HTTP ${response.status}: ${errText.slice(0, 300)}`
    );
  }

  const data = (await response.json()) as AnthropicMessageResponse;
  if (data.error) {
    throw new Error(`Anthropic API error: ${data.error.type} — ${data.error.message}`);
  }

  const textOut = (data.content || [])
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .join("");

  // Reconstruct full JSON (we prefilled `{"seoTitle":`).
  const raw = `{"seoTitle":${textOut}`.trim();
  const lastBrace = raw.lastIndexOf("}");
  const jsonText = lastBrace >= 0 ? raw.slice(0, lastBrace + 1) : raw;

  let parsed: { seoTitle?: unknown; seoDescription?: unknown };
  try {
    parsed = JSON.parse(jsonText);
  } catch {
    throw new Error(
      `Model did not return valid JSON: ${jsonText.slice(0, 300)}`
    );
  }

  if (
    typeof parsed.seoTitle !== "string" ||
    typeof parsed.seoDescription !== "string"
  ) {
    throw new Error(
      `Model JSON missing required fields. Got: ${JSON.stringify(parsed).slice(0, 300)}`
    );
  }

  return {
    seoTitle: parsed.seoTitle.trim(),
    seoDescription: parsed.seoDescription.trim(),
    tokensIn: data.usage?.input_tokens,
    tokensOut: data.usage?.output_tokens,
    cacheReadTokens: data.usage?.cache_read_input_tokens ?? 0,
  };
}
