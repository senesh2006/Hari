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
    try {
      const res = await fetch("/api/config");
      const cfg = await res.json();
      if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) {
        console.warn("Supabase not configured");
        return null;
      }
      _client = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);
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

window.KaprukaSupabase = {
  getSupabaseClient,
  fetchProfile,
  ensureProfile,
  updateProfile,
};
