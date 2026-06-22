/* Supabase client bootstrap for Kapruka concierge */

let _client = null;
let _bootPromise = null;

async function getSupabaseClient() {
  if (_client) return _client;
  if (_bootPromise) return _bootPromise;

  _bootPromise = (async () => {
    if (!window.supabase?.createClient) {
      console.warn("Supabase JS not loaded");
      return null;
    }
    const { createClient } = window.supabase;
    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();
      if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) {
        console.warn("Supabase not configured");
        return null;
      }
      _client = createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);
      return _client;
    } catch (err) {
      console.warn("Failed to init Supabase", err);
      return null;
    }
  })();

  return _bootPromise;
}

async function fetchProfile(client, userId) {
  if (!client || !userId) return null;
  const { data, error } = await client
    .from("profiles")
    .select("*")
    .eq("id", userId)
    .maybeSingle();
  if (error) {
    console.warn("Profile fetch failed", error.message);
    return null;
  }
  return data;
}

async function ensureProfile(client, session) {
  if (!client || !session?.user) return null;
  let profile = await fetchProfile(client, session.user.id);
  if (profile) return profile;

  const meta = session.user.user_metadata || {};
  const display =
    meta.full_name || meta.name || (session.user.email || "").split("@")[0] || "Guest";

  const { data, error } = await client
    .from("profiles")
    .upsert({ id: session.user.id, display_name: display })
    .select()
    .single();

  if (error) {
    console.warn("Profile create failed", error.message);
    return null;
  }
  return data;
}

async function updateProfile(client, userId, patch) {
  if (!client || !userId) return null;
  const { data, error } = await client
    .from("profiles")
    .update(patch)
    .eq("id", userId)
    .select()
    .single();
  if (error) throw new Error(error.message);
  return data;
}

async function listRecipients(client, userId) {
  if (!client || !userId) return [];
  const { data, error } = await client
    .from("recipients")
    .select("*")
    .eq("user_id", userId)
    .order("updated_at", { ascending: false });
  if (error) {
    console.warn("Recipients fetch failed", error.message);
    return [];
  }
  return data || [];
}

async function upsertRecipient(client, userId, row) {
  if (!client || !userId) return null;
  const payload = { ...row, user_id: userId };
  if (payload.id) {
    const { data, error } = await client
      .from("recipients")
      .update(payload)
      .eq("id", payload.id)
      .select()
      .single();
    if (error) throw new Error(error.message);
    return data;
  }
  const { data, error } = await client
    .from("recipients")
    .insert(payload)
    .select()
    .single();
  if (error) throw new Error(error.message);
  return data;
}

async function deleteRecipient(client, id) {
  if (!client || !id) return;
  const { error } = await client.from("recipients").delete().eq("id", id);
  if (error) throw new Error(error.message);
}

async function listWishlist(client, userId) {
  if (!client || !userId) return [];
  const { data, error } = await client
    .from("wishlist_items")
    .select("*")
    .eq("user_id", userId)
    .order("created_at", { ascending: false });
  if (error) {
    console.warn("Wishlist fetch failed", error.message);
    return [];
  }
  return data || [];
}

async function addWishlistItem(client, userId, product) {
  if (!client || !userId || !product?.name) return null;
  const pid = product.id || product.product_id || product.name;
  const row = {
    user_id: userId,
    product_id: String(pid),
    name: product.name,
    url: product.url || null,
    image: product.image || null,
    price: product.price || product.rawPrice || null,
    currency: product.currency || "LKR",
  };
  const { data, error } = await client
    .from("wishlist_items")
    .upsert(row, { onConflict: "user_id,product_id" })
    .select()
    .single();
  if (error) throw new Error(error.message);
  return data;
}

async function removeWishlistItem(client, userId, productId) {
  if (!client || !userId || !productId) return;
  const { error } = await client
    .from("wishlist_items")
    .delete()
    .eq("user_id", userId)
    .eq("product_id", String(productId));
  if (error) throw new Error(error.message);
}

async function saveOrderHistory(client, userId, order) {
  if (!client || !userId) return null;
  const { data, error } = await client
    .from("order_history")
    .insert({ ...order, user_id: userId })
    .select()
    .single();
  if (error) {
    console.warn("Order history save failed", error.message);
    return null;
  }
  return data;
}

function daysUntil(dateStr) {
  if (!dateStr) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const parts = String(dateStr).slice(0, 10).split("-");
  if (parts.length !== 3) return null;
  let d = new Date(+parts[0], +parts[1] - 1, +parts[2]);
  d.setHours(0, 0, 0, 0);
  if (d < today) d = new Date(+parts[0] + 1, +parts[1] - 1, +parts[2]);
  return Math.round((d - today) / (24 * 60 * 60 * 1000));
}

function upcomingOccasions(recipients, withinDays = 21) {
  const out = [];
  (recipients || []).forEach((r) => {
    const name = r.name || "Someone";
    ["birthday", "anniversary"].forEach((field) => {
      const days = daysUntil(r[field]);
      if (days != null && days >= 0 && days <= withinDays) {
        out.push({
          name,
          type: field,
          days,
          recipient: r,
        });
      }
    });
  });
  return out.sort((a, b) => a.days - b.days);
}

window.KaprukaSupabase = {
  getSupabaseClient,
  fetchProfile,
  ensureProfile,
  updateProfile,
  listRecipients,
  upsertRecipient,
  deleteRecipient,
  listWishlist,
  addWishlistItem,
  removeWishlistItem,
  saveOrderHistory,
  upcomingOccasions,
  daysUntil,
};
