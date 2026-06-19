import type { CartItem, Product } from "./types";

export function priceNum(p: { price?: string | number | null }): number {
  const n = parseFloat(String(p.price ?? "").replace(/[^0-9.]/g, ""));
  return isNaN(n) ? 0 : n;
}

export function fmtMoney(n: number, cur?: string | null): string {
  return (cur || "LKR") + " " + Number(n).toLocaleString();
}

export function idFromUrl(u?: string | null): string | null {
  const m = String(u || "").match(/\/kid\/([^/?#]+)/);
  return m ? m[1] : null;
}

export function cartKey(p: { url?: string | null; name?: string | null }): string {
  return (p.url || "").trim().toLowerCase() || String(p.name || "").trim().toLowerCase();
}

export function priceText(p: Product): string {
  if (p.price === null || p.price === undefined || p.price === "") return "";
  return (p.currency ? p.currency + " " : "") + p.price;
}

export function cartSubtotal(cart: CartItem[]): number {
  return cart.reduce((s, c) => s + priceNum(c) * c.qty, 0);
}

let _seq = 0;
export const nextId = () => ++_seq;
