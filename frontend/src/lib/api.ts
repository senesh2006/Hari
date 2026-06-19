import type { Lang, Product } from "./types";

export interface SearchBody {
  messages: { role: string; content: string }[];
  allow_questions: boolean;
  suggestions: Product[];
  cart: { name: string; qty: number; price: any; currency: string }[];
  instructions: string[];
  language: Lang;
}

export interface SearchResponse {
  ok: boolean;
  needs_input?: boolean;
  questions?: string[];
  questions_local?: string[];
  answer?: string;
  answer_local?: string;
  products?: Product[];
  cart_actions?: any[];
  user_en?: string;
  error?: string;
  [k: string]: any;
}

export async function search(body: SearchBody): Promise<SearchResponse> {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export interface ToolInfo {
  name: string;
  description?: string;
  inputSchema?: { properties?: Record<string, unknown> };
  writes?: boolean;
}

let _orderTool: ToolInfo | null | undefined;

/** Discover the order-creation tool from the live MCP catalogue (cached). */
export async function loadOrderTool(): Promise<ToolInfo | null> {
  if (_orderTool !== undefined) return _orderTool;
  _orderTool = null;
  try {
    const res = await fetch("/api/tool");
    const data = await res.json();
    const tools: ToolInfo[] = (data && data.tools) || [];
    const score = (t: ToolInfo) => {
      const n = (t.name || "").toLowerCase();
      if (/(cancel|get|list|status|track|fetch|view|read|search|history)/.test(n)) return -1;
      let s = 0;
      if (/order/.test(n)) s += 2;
      if (/checkout/.test(n)) s += 3;
      if (/(create|place|new|submit|add)/.test(n)) s += 2;
      if (t.writes) s += 1;
      return s;
    };
    const best = tools.map((t) => [score(t), t] as const).filter((x) => x[0] > 0).sort((a, b) => b[0] - a[0])[0];
    if (best) _orderTool = best[1];
  } catch {
    /* fall back to the well-known name */
  }
  return _orderTool;
}

export interface Checkout {
  checkout_url: string;
  order_ref?: string;
  summary?: { grand_total?: number; delivery_fee?: number; currency?: string };
  expires_at?: string;
}

export interface InvokeResponse {
  ok: boolean;
  output?: string;
  error?: string;
  checkout?: Checkout;
  [k: string]: any;
}

export async function invokeTool(name: string, args: any): Promise<InvokeResponse> {
  const res = await fetch("/api/tool", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args }),
  });
  return res.json();
}

export async function ttsBlob(text: string, lang: Lang): Promise<Blob> {
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: String(text).slice(0, 600), lang }),
  });
  if (!res.ok) throw new Error("tts " + res.status);
  return res.blob();
}
