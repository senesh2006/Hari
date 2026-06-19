import type { Lang } from "./types";

export const LANG_CODES: Record<Lang, string> = { en: "en-US", si: "si-LK", ta: "ta-LK" };
export const LANG_NAMES: Record<Lang, string> = { en: "English", si: "Sinhala", ta: "Tamil" };

export const UI_TEXT: Record<Lang, { ask: string; skip: string }> = {
  en: { ask: "To pick the best option, could you tell me a bit more?", skip: 'Just reply here, or say "skip".' },
  si: { ask: "හොඳම තේරීම කරන්න, ටිකක් වැඩිදුර කියන්න පුළුවන්ද?", skip: 'මෙතන පිළිතුරු දෙන්න, නැත්නම් "skip" කියන්න.' },
  ta: { ask: "சிறந்ததைத் தேர்ந்தெடுக்க, இன்னும் சற்று சொல்ல முடியுமா?", skip: 'இங்கே பதிலளிக்கவும், அல்லது "skip" எனச் சொல்லவும்.' },
};

export function uiText(lang: Lang, key: "ask" | "skip"): string {
  return (UI_TEXT[lang] || UI_TEXT.en)[key];
}

// Pull a budget out of free text, e.g. "budget around 5000", "under Rs 7,500".
export function parseBudget(text: string): number | null {
  const t = String(text || "").toLowerCase();
  const m =
    t.match(/budget[^0-9]{0,20}(\d[\d,]{1,})/) ||
    t.match(/(?:rs\.?|lkr|rupees)\s*(\d[\d,]{1,})/) ||
    t.match(/(\d[\d,]{1,})\s*(?:lkr|rs\b|rupees)/) ||
    t.match(/(?:around|under|below|upto|up to|max|maximum|within)\s*(\d[\d,]{2,})/);
  if (m) {
    const n = parseFloat(m[1].replace(/,/g, ""));
    if (!isNaN(n) && n >= 100) return n;
  }
  return null;
}
