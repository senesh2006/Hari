export interface Product {
  id?: string | null;
  name: string;
  price?: string | number | null;
  currency?: string | null;
  image?: string | null;
  url?: string | null;
  description?: string | null;
  qty?: number;
}

export interface CartItem {
  id?: string | null;
  name: string;
  price?: string | number | null;
  currency: string;
  url?: string | null;
  image?: string | null;
  qty: number;
}

export type Role = "user" | "bot";

export interface Message {
  id: number;
  role: Role;
  text?: string;        // plain text (may contain newlines)
  thought?: string;     // "Searched Kapruka · N matches · Xs"
  products?: Product[];
  thinking?: boolean;   // shimmer placeholder
  error?: boolean;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

export type Lang = "en" | "si" | "ta";
