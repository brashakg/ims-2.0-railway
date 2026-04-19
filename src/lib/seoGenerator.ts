import Anthropic from "@anthropic-ai/sdk";

// Shared system prompt. Cached via cache_control on every call so
// subsequent requests in a batch hit Anthropic's prompt cache.
const SYSTEM_PROMPT = `You generate SEO metadata for an eyewear retail catalog.

Given a product's attributes, output exactly one JSON object with two fields:
- "seoTitle"       : 55-70 characters. Title Case. Includes the brand and the most selling-relevant attributes. Ends with the product type (Spectacles / Sunglasses / Contact Lens Solution) when it fits.
- "seoDescription" : 140-160 characters. One or two natural-sounding sentences. Mentions the brand, main shape/color/material, and a buy-now call to action (e.g. "Shop online", "Buy now", "Order today").

Rules:
- Never fabricate features that are not in the input. If an attribute is unknown, skip it.
- No emojis, no ALL CAPS phrases, no marketing hyperbole ("amazing", "best-ever", etc.).
- Avoid repeating the same word back-to-back.
- Keep descriptions factual, short, and scannable.
- Output ONLY the JSON object — no markdown, no code fences, no preamble, no trailing commentary.`;

const MODEL = "claude-haiku-4-5-20251001";

let _client: Anthropic | null = null;
function getClient(): Anthropic {
  if (_client) return _client;
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) {
    throw new Error(
      "ANTHROPIC_API_KEY is not set. Add it to Railway environment variables."
    );
  }
  _client = new Anthropic({ apiKey: key });
  return _client;
}

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
  const client = getClient();
  const userPrompt = attributesToUserPrompt(input);

  if (!userPrompt.trim()) {
    throw new Error("Cannot generate SEO: no product attributes provided");
  }

  const response = await client.messages.create({
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
      {
        role: "user",
        content: userPrompt,
      },
      {
        // Prefill the assistant turn with an opening brace so Claude is
        // forced to continue a JSON object.
        role: "assistant",
        content: '{"seoTitle":',
      },
    ],
  });

  // Concatenate text blocks from the assistant's response.
  const textOut = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("");

  // Because we prefilled `{"seoTitle":`, Claude's response starts at the
  // value. Reconstruct full JSON and parse.
  const raw = `{"seoTitle":${textOut}`.trim();

  // Trim anything after the final `}` in case Claude added a stray newline.
  const lastBrace = raw.lastIndexOf("}");
  const jsonText = lastBrace >= 0 ? raw.slice(0, lastBrace + 1) : raw;

  let parsed: { seoTitle?: unknown; seoDescription?: unknown };
  try {
    parsed = JSON.parse(jsonText);
  } catch (e) {
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
    tokensIn: response.usage?.input_tokens,
    tokensOut: response.usage?.output_tokens,
    cacheReadTokens:
      (response.usage as unknown as { cache_read_input_tokens?: number })
        ?.cache_read_input_tokens ?? 0,
  };
}
