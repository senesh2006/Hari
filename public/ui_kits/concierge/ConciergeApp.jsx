/* Kapruka Gift Concierge — design-system UI wired to Hari backend APIs */
const { useState, useRef, useEffect, useCallback } = React;
const DS = window.KaprukaDesignSystem_d6db4e || {};
const Button = DS.Button || ((p) => <button type="button" {...p}>{p.children}</button>);
const IconButton = DS.IconButton || ((p) => <button type="button" {...p} />);
const Bubble = DS.Bubble || ((p) => <div {...p}>{p.children}</div>);
const SuggestionCard = DS.SuggestionCard || ((p) => <button type="button" onClick={p.onClick}>{p.children}</button>);
const ProductCard = DS.ProductCard || ((p) => <div>{p.name}</div>);
const Toast = DS.Toast || ((p) => <div>{p.children}</div>);
const Icon = DS.Icon || (() => null);
const ThemeToggle = DS.ThemeToggle || (() => null);

const CHIPS = [
  { icon: "cake", tone: "blush", tx: "Birthday gift for mom", prompt: "Birthday gift for mom under Rs 5000" },
  { icon: "flower", tone: "mint", tx: "Anniversary flowers", prompt: "Anniversary flowers delivered to Colombo" },
  { icon: "heart-pulse", tone: "lilac", tx: "Get-well hamper", prompt: "Get-well hamper for a friend" },
  { icon: "gift", tone: "butter", tx: "Under Rs 3000", prompt: "A nice gift and a card under Rs 3000" },
];
const GREETING =
  "Hi, I'm Hari. Who are we spoiling today? Tap the mic or type below.";
const LANG_CODES = { en: "en-US", si: "si-LK", ta: "ta-LK" };
const LANG_NAMES = { en: "English", si: "Sinhala", ta: "Tamil" };
const UI_TEXT = {
  en: { ask: "", skip: 'Reply here, or say "skip" if you want me to just pick.' },
  si: { ask: "", skip: 'මෙතන පිළිතුරු දෙන්න, නැත්නම් "skip" කියන්න.' },
  ta: { ask: "", skip: 'இங்கே பதிலளிக்கவும், அல்லது "skip" எனச் சொல்லவும்.' },
};

let _id = 1;
const nid = () => _id++;
const priceNum = (p) => {
  const n = parseFloat(String(p.rawPrice ?? p.price).replace(/[^0-9.]/g, ""));
  return isNaN(n) ? 0 : n;
};
const fmtMoney = (n, cur) => (cur || "LKR") + " " + Number(n).toLocaleString();
const idFromUrl = (u) => {
  const m = String(u || "").match(/\/kid\/([^/?#]+)/);
  return m ? m[1] : null;
};
const normProduct = (p) => {
  const cur = p.currency || "LKR";
  const raw = p.price;
  const price = raw != null && raw !== "" ? `${cur} ${raw}` : "";
  return {
    id: p.id || idFromUrl(p.url),
    name: p.name || "Product",
    price,
    rawPrice: raw,
    currency: cur,
    description: p.description || "",
    image: p.image || p.image_url || "",
    url: p.url || p.link || "",
    customizable: !!p.customizable,
    customization_type: p.customization_type || null,
    group: p.group || "",
  };
};

/** Fallback: pull product objects out of raw MCP tool results when the API
 *  response omits the curated `products` array. */
const extractProductsFromResults = (results) => {
  const found = [];
  const looksLike = (d) => {
    if (!d || typeof d !== "object" || Array.isArray(d)) return false;
    const name = d.name || d.title || d.product_name || d.productName;
    if (!name) return false;
    return d.price != null || d.amount != null || d.url || d.link || d.image || d.image_url;
  };
  const walk = (obj) => {
    if (!obj || typeof obj !== "object") return;
    if (Array.isArray(obj)) { obj.forEach(walk); return; }
    if (looksLike(obj)) { found.push(normProduct(obj)); return; }
    Object.values(obj).forEach(walk);
  };
  for (const r of results || []) {
    let data = r?.output;
    if (typeof data === "string") {
      try { data = JSON.parse(data); } catch (_) { continue; }
    }
    walk(data);
  }
  const seen = new Set();
  return found.filter((p) => {
    const key = (p.url || p.name || "").toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};
/** Group products by their `group` label, preserving first-seen order. Returns
 *  [{ label, items }]. */
const groupProducts = (products) => {
  const order = [];
  const byLabel = new Map();
  for (const p of products) {
    const label = p.group || "Suggestions";
    if (!byLabel.has(label)) {
      byLabel.set(label, []);
      order.push(label);
    }
    byLabel.get(label).push(p);
  }
  return order.map((label) => ({ label, items: byLabel.get(label) }));
};
const cartKey = (p) => (p.url || "").trim().toLowerCase() || String(p.name || "").trim().toLowerCase();
const parseBudget = (text) => {
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
};

// --- Chat history persistence (localStorage) ------------------------------
const CHAT_NS = "kapruka_chat";
const chatScope = (session, isGuest) =>
  isGuest || !session?.user?.id ? "guest" : session.user.id;
const chatIndexKey = (scope) => `${CHAT_NS}:${scope}:index`;
const oneChatKey = (scope, id) => `${CHAT_NS}:${scope}:${id}`;
const loadChatIndex = (scope) => {
  try { return JSON.parse(localStorage.getItem(chatIndexKey(scope))) || []; }
  catch (_) { return []; }
};
const saveChatIndex = (scope, idx) => {
  try { localStorage.setItem(chatIndexKey(scope), JSON.stringify(idx.slice(0, 30))); }
  catch (_) {}
};
const loadChat = (scope, id) => {
  try { return JSON.parse(localStorage.getItem(oneChatKey(scope, id))); }
  catch (_) { return null; }
};
const saveChat = (scope, id, data) => {
  try { localStorage.setItem(oneChatKey(scope, id), JSON.stringify(data)); }
  catch (_) {}
};
const dropChat = (scope, id) => {
  try { localStorage.removeItem(oneChatKey(scope, id)); } catch (_) {}
};
const chatTitleFrom = (messages) => {
  const firstUser = (messages || []).find((m) => m.role === "user" && m.text);
  return (firstUser?.text || "New chat").slice(0, 48);
};
const relTime = (ts) => {
  const s = Math.max(1, Math.round((Date.now() - (ts || 0)) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
};

// A product search is the common case; skip skeleton flash for obvious
// non-product asks (greetings, cart/wishlist/orders, tracking).
const NON_PRODUCT_RE =
  /\b(hi|hii|hello|hey|thanks|thank you|what'?s? in (my )?(cart|wishlist|basket)|my (cart|wishlist|orders?)|show (me )?(my )?(cart|wishlist|orders?)|checkout|check ?out|empty (my )?cart|clear (the )?cart|track|order status|delivered yet|arrived yet)\b/i;
const looksLikeProductRequest = (text) => {
  const t = (text || "").trim();
  return t.length > 0 && !NON_PRODUCT_RE.test(t);
};

// Turns where the agent replies/asks rather than searching — show no skeletons.
const REPLY_TURN_RE =
  /\b(mad at|angry|upset|fight|make (it )?up|messed up|apolog|sorry|forgot (our|the|anniversary)|what do (you|u) think|not sure|thinking about|which (is|one|of these)|best (one|option|pick|choice)|you (choose|pick|decide)|recommend|your (pick|favou?rite|choice))\b/i;
const EXPLICIT_PRODUCTS_RE =
  /(?:\bwhat gifts?\b|\bwhat (?:do|would|can) (?:you|u)\b).{0,48}\b(?:suggest(?:ions?)?|recommend(?:ations?)?|options?|ideas?|picks?)\b|\bshow me (?:some )?(?:options?|gifts?|ideas?|suggestions?|products?)\b|\b(?:any|some) (?:suggestions?|options?|ideas?|gifts?)\b|\bwhat should i (?:get|buy|gift)\b|\bgive me (?:some )?(?:ideas?|options?|suggestions?)\b/i;
// Preference-rich categories that get a discovery question before searching.
const PREF_RICH_HINT_RE =
  /\b(flowers?|bouquet|roses?|orchids?|cake|cakes|hamper|chocolates?|ramen|noodles?|tea|coffee|wine|snacks?|dress|dresses|saree|sari|outfit|clothing|shirt|skirt|suit|jewell?ery|necklace|pendant|earrings?|bracelet|ring|watch|watches|perfume|fragrance|cologne|handbag|purse|wallet|shoes?|spa|wellness|toy|toys|plant|plants|book|books)\b/i;
// A concrete taste/style/colour means we're ready to search, not ask.
const HAS_PREF_RE =
  /\b(elegant|casual|party|formal|evening|sporty|classic|trendy|minimal|chic|cute|fancy|vintage|boho|modern|traditional|ethnic|red|blue|black|white|pink|green|gold|silver|navy|maroon|beige|purple|yellow|grey|gray|cream|brown|teal|floral|pastel|spicy|sweet|savou?ry|dark|milk|woody|fresh|seafood|cheese|chicken)\b/i;
const BUDGET_WORDS_RE =
  /\b(no budget|no limit|any budget|unlimited|mid.?range|moderate|cheap(er)?|premium|expensive|affordable)\b/i;
const OPEN_BUDGET_RE =
  /(?:\bno budget\w*(?:\s+constraints?)?|\b(?:there'?s|theres|there is) no budget\b|\b(?:don'?t|do not) have (?:a )?budget\b|\bno limit\b|\bunlimited\b|\bopen budget\b|\bwithout (?:a )?budget\b|\bbudget(?:ary)? constraints?\b)/i;

// Mirrors the backend recipient-discovery gate: a recipient is named but we know
// neither their gender, taste, occasion, nor a concrete product — so the agent
// asks "guy or girl, what are they into?" rather than presenting products.
const RECIPIENT_CUE_RE =
  /\b(mom|mother|mum|mummy|dad|father|wife|husband|girlfriend|boyfriend|partner|gf|bf|fiance|fiancee|sister|brother|daughter|son|friend|firend|freind|frnd|bestie|boss|colleague|grandma|grandpa|granny|aunt|uncle|cousin|hubby)\b/i;
const GENDER_CUE_RE =
  /\b(she|her|hers|girl|woman|lady|ladies|mom|mother|mum|wife|girlfriend|gf|sister|daughter|grandma|granny|aunt|niece|he|him|his|boy|guy|man|men|gent|dad|father|husband|boyfriend|bf|brother|son|grandpa|uncle|nephew)\b/i;
const OCCASION_CUE_RE =
  /\b(birthday|anniversary|wedding|valentine|christmas|new ?year|graduation|farewell|housewarming|baby shower|engagement|get ?well|condolence|funeral|promotion|retirement|mother'?s day|father'?s day|deepavali|diwali|avurudu)\b/i;
const TASTE_CUE_RE =
  /\b(likes?|loves?|enjoys?|into|favou?rite|fan of|prefers?|hobby|hobbies|obsessed|passionate)\b/i;

const greetSub = () => {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
};

const normNameKey = (name) =>
  String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

/** Drop numbered/bulleted product lines and corporate filler when cards render below. */
const stripCatalogFromText = (text, products) => {
  if (!text) return text;
  let out = text
    .replace(
      /\b(?:here are (?:a few |some )?(?:options|picks|ideas|gift ideas)|i(?:'ve| have) (?:found|got|pulled together) (?:some |a few )?(?:options|ideas|picks))[^.!\n]*[.:]?\s*/gi,
      ""
    )
    .replace(
      /\s*(?:let me know if (?:you(?:'d| would) like|you want)|please let me know|feel free to (?:let me know|ask))[^.!\n]*[.!]?\s*/gi,
      ""
    )
    .replace(/\s*\d+\.\s+[A-Z][^.!\n]{20,}/g, "");
  if (!products?.length) return out.replace(/\n{3,}/g, "\n\n").trim();
  const names = products.map((p) => normNameKey(p.name)).filter((n) => n.length >= 3);
  const kept = out.split("\n").filter((line) => {
    const s = line.trim();
    if (!s) return true;
    if (/^\d+\.\s/.test(s)) return false;
    if (/^[-•*]\s/.test(s) && (s.length > 40 || names.some((n) => s.toLowerCase().includes(n)))) return false;
    const norm = s.toLowerCase().replace(/[*_#[\]()]/g, " ");
    if (names.some((n) => n.length >= 8 && (norm.includes(n) || n.includes(norm))) && s.length > 30) return false;
    return true;
  });
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
};

const BotText = ({ text }) => {
  if (!text) return null;
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    const m = part.match(/^\*\*(.+)\*\*$/);
    if (m) return <strong key={i}>{m[1]}</strong>;
    return part;
  });
};

function CityPicker({ value, onChange, name, required, placeholder, inputRef }) {
  const [query, setQuery] = useState(value || "");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [cities, setCities] = useState([]);
  const [total, setTotal] = useState(null);
  const [err, setErr] = useState("");
  const wrapRef = useRef(null);
  const debounceRef = useRef(null);
  const api = window.KaprukaDeliveryCities;

  useEffect(() => {
    setQuery(value || "");
  }, [value]);

  useEffect(() => {
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const runSearch = (q) => {
    if (!api) return;
    setLoading(true);
    setErr("");
    api.searchDeliveryCities(q)
      .then((res) => {
        setCities(res.cities || []);
        setTotal(res.total ?? res.cities?.length ?? 0);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  };

  const onFocus = () => {
    setOpen(true);
    if (!cities.length) runSearch(query);
  };

  const onInput = (e) => {
    const next = e.target.value;
    setQuery(next);
    onChange?.(next);
    setOpen(true);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(next), 220);
  };

  const pick = (cityName) => {
    setQuery(cityName);
    onChange?.(cityName);
    setOpen(false);
  };

  const hint =
    total != null
      ? `${total} Kapruka delivery cities — pick the exact name from the list.`
      : "Search Kapruka delivery cities";

  return (
    <div className="city-picker" ref={wrapRef}>
      <input
        ref={inputRef}
        name={name}
        type="text"
        required={required}
        autoComplete="off"
        placeholder={placeholder || "Type to search cities…"}
        value={query}
        onFocus={onFocus}
        onChange={onInput}
      />
      <p className="city-picker-hint">{loading ? "Loading cities…" : hint}</p>
      {err && <p className="city-picker-err">{err}</p>}
      {open && cities.length > 0 && (
        <ul className="city-picker-list" role="listbox">
          {cities.map((c) => (
            <li key={c.name}>
              <button type="button" role="option" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(c.name)}>
                <span className="city-picker-name">{c.name}</span>
                {c.aliases?.length > 0 && (
                  <span className="city-picker-alias">also: {c.aliases.slice(0, 3).join(", ")}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && !loading && cities.length === 0 && query.trim() && (
        <p className="city-picker-empty">No matching cities — try a different spelling.</p>
      )}
    </div>
  );
}

const AVOID_OPTIONS = [
  { value: "chocolate", label: "Chocolate" },
  { value: "alcohol", label: "Alcohol" },
  { value: "perfume", label: "Perfume / fragrance" },
  { value: "nuts", label: "Nuts" },
  { value: "none", label: "No restrictions" },
];

const DIETARY_OPTIONS = [
  { value: "none", label: "No preference" },
  { value: "vegetarian", label: "Vegetarian" },
  { value: "no_nuts", label: "No nuts" },
  { value: "no_dairy", label: "No dairy" },
];

function SettingsPanel({
  profile,
  supabase,
  session,
  budget,
  setBudget,
  currentLang,
  setCurrentLang,
  onClose,
  onProfileUpdate,
  toast,
}) {
  const prefs = profile?.preferences || {};
  const [avoid, setAvoid] = useState(prefs.avoid_list || []);
  const [dietary, setDietary] = useState(prefs.dietary || "none");
  const [city, setCity] = useState(profile?.default_city || "");
  const [corporate, setCorporate] = useState(Boolean(prefs.corporate_gifting));
  const [busy, setBusy] = useState(false);

  const toggleAvoid = (value) => {
    if (value === "none") setAvoid(["none"]);
    else {
      const next = avoid.includes(value)
        ? avoid.filter((x) => x !== value)
        : [...avoid.filter((x) => x !== "none"), value];
      setAvoid(next);
    }
  };

  const save = async () => {
    if (!supabase || !session?.user?.id) return;
    setBusy(true);
    try {
      const preferences = {
        ...prefs,
        avoid_list: avoid.filter((x) => x !== "none"),
        dietary: dietary === "none" ? null : dietary,
        corporate_gifting: corporate,
      };
      const updated = await window.KaprukaSupabase.updateProfile(supabase, session.user.id, {
        default_budget: budget,
        default_city: city.trim() || profile?.default_city,
        default_language: currentLang,
        preferences,
      });
      if (updated) onProfileUpdate(updated);
      toast("Preferences saved", "check");
      onClose();
    } catch (e) {
      toast(e.message || "Could not save", "x");
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside className="drawer open side-drawer" aria-label="Settings">
      <header>
        <Icon name="sparkles" size={20} />
        <h3>Your gifting style</h3>
        <IconButton icon="x" title="Close" style={{ marginLeft: "auto" }} onClick={onClose} />
      </header>
      <div className="body settings-body">
        <label>Typical budget (LKR)</label>
        <input
          type="number"
          min="0"
          step="100"
          value={budget ?? ""}
          onChange={(e) => setBudget(e.target.value ? +e.target.value : null)}
        />
        <label>Default delivery city</label>
        <CityPicker value={city} onChange={setCity} placeholder="Search cities…" />
        <label>Always avoid</label>
        <div className="chip-row">
          {AVOID_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={"chip-btn" + (avoid.includes(o.value) ? " on" : "")}
              onClick={() => toggleAvoid(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label>Dietary</label>
        <select value={dietary} onChange={(e) => setDietary(e.target.value)}>
          {DIETARY_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <label className="check-row">
          <input type="checkbox" checked={corporate} onChange={(e) => setCorporate(e.target.checked)} />
          I often shop for colleagues / corporate gifts
        </label>
        {profile?.gifting_personality && (
          <p className="settings-personality">
            Personality: {window.KaprukaPersonality?.PERSONALITY_LABELS?.[profile.gifting_personality] || profile.gifting_personality}
          </p>
        )}
      </div>
      <div className="foot">
        <Button variant="primary" full disabled={busy} onClick={save}>
          {busy ? "Saving…" : "Save preferences"}
        </Button>
      </div>
    </aside>
  );
}

function RecipientsPanel({
  recipients,
  supabase,
  session,
  onClose,
  onRefresh,
  toast,
}) {
  const empty = { name: "", relationship: "", birthday: "", anniversary: "", city: "", interests: "", avoid: "", notes: "" };
  const [form, setForm] = useState(empty);
  const [editingId, setEditingId] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadForm = (r) => {
    setEditingId(r.id);
    setForm({
      name: r.name || "",
      relationship: r.relationship || "",
      birthday: r.birthday ? String(r.birthday).slice(0, 10) : "",
      anniversary: r.anniversary ? String(r.anniversary).slice(0, 10) : "",
      city: r.city || "",
      interests: (r.interests || []).join(", "),
      avoid: (r.avoid || []).join(", "),
      notes: r.notes || "",
    });
  };

  const save = async () => {
    if (!form.name.trim() || !supabase || !session?.user?.id) return;
    setBusy(true);
    try {
      const row = {
        id: editingId || undefined,
        name: form.name.trim(),
        relationship: form.relationship.trim() || null,
        birthday: form.birthday || null,
        anniversary: form.anniversary || null,
        city: form.city.trim() || null,
        interests: form.interests.split(",").map((s) => s.trim()).filter(Boolean),
        avoid: form.avoid.split(",").map((s) => s.trim()).filter(Boolean),
        notes: form.notes.trim() || null,
      };
      await window.KaprukaSupabase.upsertRecipient(supabase, session.user.id, row);
      await onRefresh();
      setForm(empty);
      setEditingId(null);
      toast("Contact saved", "heart");
    } catch (e) {
      toast(e.message || "Could not save", "x");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    if (!supabase) return;
    try {
      await window.KaprukaSupabase.deleteRecipient(supabase, id);
      await onRefresh();
      if (editingId === id) {
        setForm(empty);
        setEditingId(null);
      }
      toast("Contact removed", "trash-2");
    } catch (e) {
      toast(e.message || "Could not delete", "x");
    }
  };

  return (
    <aside className="drawer open side-drawer" aria-label="My people">
      <header>
        <Icon name="heart" size={20} />
        <h3>My people</h3>
        <IconButton icon="x" title="Close" style={{ marginLeft: "auto" }} onClick={onClose} />
      </header>
      <div className="body settings-body">
        {recipients.length === 0 && (
          <p className="muted-hint">Save gift contacts so the concierge remembers who you shop for.</p>
        )}
        <ul className="people-list">
          {recipients.map((r) => (
            <li key={r.id}>
              <button type="button" className="people-item" onClick={() => loadForm(r)}>
                <strong>{r.name}</strong>
                {r.relationship && <span>{r.relationship}</span>}
                {r.birthday && <span className="people-date">Bday {String(r.birthday).slice(0, 10)}</span>}
              </button>
              <button type="button" className="people-del" onClick={() => remove(r.id)} aria-label="Delete">
                <Icon name="trash-2" size={14} />
              </button>
            </li>
          ))}
        </ul>
        <h4 className="people-form-title">{editingId ? "Edit contact" : "Add someone"}</h4>
        <input placeholder="Name *" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <input placeholder="Relationship (mom, partner, boss…)" value={form.relationship} onChange={(e) => setForm((f) => ({ ...f, relationship: e.target.value }))} />
        <label>Birthday</label>
        <input type="date" value={form.birthday} onChange={(e) => setForm((f) => ({ ...f, birthday: e.target.value }))} />
        <label>Anniversary</label>
        <input type="date" value={form.anniversary} onChange={(e) => setForm((f) => ({ ...f, anniversary: e.target.value }))} />
        <label>Delivery city</label>
        <CityPicker value={form.city} onChange={(v) => setForm((f) => ({ ...f, city: v }))} placeholder="City…" />
        <input placeholder="Interests (comma-separated)" value={form.interests} onChange={(e) => setForm((f) => ({ ...f, interests: e.target.value }))} />
        <input placeholder="Avoid (chocolate, alcohol…)" value={form.avoid} onChange={(e) => setForm((f) => ({ ...f, avoid: e.target.value }))} />
        <textarea placeholder="Notes" rows={2} value={form.notes} onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} />
      </div>
      <div className="foot">
        <Button variant="primary" full disabled={busy || !form.name.trim()} onClick={save}>
          {busy ? "Saving…" : editingId ? "Update contact" : "Save contact"}
        </Button>
      </div>
    </aside>
  );
}

function App({
  session,
  profile,
  isGuest,
  supabase,
  onProfileUpdate,
  onSignOut,
  onRequestSignIn,
  accessToken,
}) {
  const [messages, setMessages] = useState([{ id: nid(), role: "bot", text: GREETING }]);
  const [conversation, setConversation] = useState([]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState([]);
  const [fav, setFav] = useState({});
  const [wishlist, setWishlist] = useState([]);
  const [cartOpen, setCartOpen] = useState(false);
  const [wishlistOpen, setWishlistOpen] = useState(false);
  const [checkoutView, setCheckoutView] = useState(false);
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState("Tap the mic to talk, or type below");
  const [toasts, setToasts] = useState([]);
  const [bump, setBump] = useState(false);
  const [currentLang, setCurrentLang] = useState("en");
  const [ttsOn, setTtsOn] = useState(false);
  const [assemblyAi, setAssemblyAi] = useState(false);
  const [assemblyVoice, setAssemblyVoice] = useState("ivy");
  const [budget, setBudget] = useState(null);
  const [instructions, setInstructions] = useState([]);
  const [lastSuggestions, setLastSuggestions] = useState([]);
  const [awaitingAnswers, setAwaitingAnswers] = useState(false);
  const [checkoutResult, setCheckoutResult] = useState("");
  const [checkoutCity, setCheckoutCity] = useState("");
  const [placing, setPlacing] = useState(false);
  const [modal, setModal] = useState(null);
  const [profileHydrated, setProfileHydrated] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [recipients, setRecipients] = useState([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);
  const [chatId, setChatId] = useState(() => nid());
  const [historyOpen, setHistoryOpen] = useState(false);
  const [chatList, setChatList] = useState([]);
  const [expectProducts, setExpectProducts] = useState(false);

  const restoredRef = useRef(false);
  const feedRef = useRef(null);
  const mainRef = useRef(null);
  const checkoutFormRef = useRef(null);
  const recogRef = useRef(null);
  const currentAudioRef = useRef(null);
  const guestPromptedRef = useRef(false);
  const persistTimerRef = useRef(null);
  const personalityGreetedRef = useRef(false);
  const occasionNudgedRef = useRef(false);
  const menuRef = useRef(null);
  const langRef = useRef(null);

  const defaultCity = profile?.default_city || "";
  const displayName =
    profile?.display_name ||
    session?.user?.user_metadata?.full_name ||
    (isGuest ? "Guest" : session?.user?.email?.split("@")[0]) ||
    "Guest";

  const persistProfile = useCallback(
    (patch) => {
      if (!supabase || !session?.user?.id || isGuest) return;
      clearTimeout(persistTimerRef.current);
      persistTimerRef.current = setTimeout(async () => {
        try {
          const updated = await window.KaprukaSupabase.updateProfile(
            supabase,
            session.user.id,
            patch
          );
          if (updated) onProfileUpdate(updated);
        } catch (_) {}
      }, 600);
    },
    [supabase, session, isGuest, onProfileUpdate]
  );

  useEffect(() => {
    if (!profile || profileHydrated) return;
    if (profile.default_budget != null) setBudget(Number(profile.default_budget));
    if (Array.isArray(profile.saved_instructions) && profile.saved_instructions.length) {
      setInstructions(profile.saved_instructions);
    }
    if (profile.default_language) setCurrentLang(profile.default_language);
    setProfileHydrated(true);
    const pg = window.KaprukaPersonality?.personalityGreeting?.(profile.gifting_personality);
    if (pg && !personalityGreetedRef.current) {
      personalityGreetedRef.current = true;
      setMessages((m) => {
        if (m.length !== 1 || m[0].role !== "bot") return m;
        return [{ ...m[0], text: `${m[0].text}\n\n${pg}` }];
      });
    }
  }, [profile, profileHydrated]);

  const refreshRecipients = useCallback(async () => {
    if (!supabase || !session?.user?.id || isGuest) {
      setRecipients([]);
      return;
    }
    const list = await window.KaprukaSupabase.listRecipients(supabase, session.user.id);
    setRecipients(list);
  }, [supabase, session, isGuest]);

  useEffect(() => {
    refreshRecipients();
  }, [refreshRecipients]);

  useEffect(() => {
    if (!supabase || !session?.user?.id || isGuest) return;
    window.KaprukaSupabase.listWishlist(supabase, session.user.id).then((items) => {
      const map = {};
      items.forEach((w) => { map[w.name] = true; });
      setFav(map);
      setWishlist(items || []);
    });
  }, [supabase, session, isGuest]);

  useEffect(() => {
    if (!recipients.length || occasionNudgedRef.current) return;
    const up = window.KaprukaSupabase.upcomingOccasions(recipients, 21);
    if (up.length) {
      occasionNudgedRef.current = true;
      const u = up[0];
      toast(
        `${u.name}'s ${u.type === "birthday" ? "birthday" : "anniversary"} in ${u.days} days — need gift ideas?`,
        "gift"
      );
    }
  }, [recipients]);

  useEffect(() => {
    if (!profileHydrated || isGuest) return;
    persistProfile({
      default_budget: budget,
      saved_instructions: instructions,
      default_language: currentLang,
    });
  }, [budget, instructions, currentLang, profileHydrated, isGuest, persistProfile]);

  const started = messages.some((m) => m.role === "user");
  const lastProductMsgId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].products && messages[i].products.length) return messages[i].id;
    }
    return null;
  })();
  const REFINE_CHIPS = [
    { tx: "Cheaper", icon: "trending-down", prompt: "Show me cheaper options" },
    { tx: "More premium", icon: "trending-up", prompt: "Show me something more premium" },
    { tx: "Something different", icon: "shuffle", prompt: "Something different please — not these" },
    { tx: "More like these", icon: "copy", prompt: "More like these" },
  ];
  const cartCount = cart.reduce((n, c) => n + c.qty, 0);
  const cartSubtotal = () => cart.reduce((s, c) => s + priceNum(c) * c.qty, 0);
  const uiText = (k) => (UI_TEXT[currentLang] || UI_TEXT.en)[k];
  const closeMenu = () => setMenuOpen(false);

  // --- Chat persistence: resume the last chat, keep a history list ----------
  const scope = chatScope(session, isGuest);

  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    // Always open on a fresh chat. Past chats are kept and reachable from the
    // History menu — we just load the list here, not the conversation itself.
    setChatList(loadChatIndex(scope));
  }, [scope]);

  useEffect(() => {
    if (!restoredRef.current) return;
    if (!messages.some((m) => m.role === "user")) return;
    const t = setTimeout(() => {
      saveChat(scope, chatId, {
        messages, conversation, lastSuggestions, ts: Date.now(),
      });
      const idx = loadChatIndex(scope).filter((c) => c.id !== chatId);
      idx.unshift({ id: chatId, title: chatTitleFrom(messages), ts: Date.now() });
      saveChatIndex(scope, idx);
      setChatList(idx);
    }, 500);
    return () => clearTimeout(t);
  }, [messages, conversation, lastSuggestions, chatId, scope]);

  const newChat = () => {
    setChatId(nid());
    setMessages([{ id: nid(), role: "bot", text: GREETING }]);
    setConversation([]);
    setLastSuggestions([]);
    setAwaitingAnswers(false);
    setMenuOpen(false);
    setHistoryOpen(false);
    setStatus("Tap the mic to talk, or type below");
  };

  const openChat = (id) => {
    const data = loadChat(scope, id);
    if (!data) return;
    setChatId(id);
    setMessages(data.messages && data.messages.length ? data.messages : [{ id: nid(), role: "bot", text: GREETING }]);
    setConversation(data.conversation || []);
    setLastSuggestions(data.lastSuggestions || []);
    setAwaitingAnswers(false);
    setHistoryOpen(false);
    setMenuOpen(false);
  };

  const deleteChat = (id) => {
    dropChat(scope, id);
    const idx = loadChatIndex(scope).filter((c) => c.id !== id);
    saveChatIndex(scope, idx);
    setChatList(idx);
    if (id === chatId) newChat();
  };

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) closeMenu();
    };
    const onKey = (e) => { if (e.key === "Escape") closeMenu(); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!langOpen) return;
    const onDoc = (e) => {
      if (langRef.current && !langRef.current.contains(e.target)) setLangOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") setLangOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [langOpen]);

  useEffect(() => {
    if (defaultCity) setCheckoutCity(defaultCity);
  }, [defaultCity]);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((c) => {
        setAssemblyAi(Boolean(c.assemblyAiEnabled));
        if (c.assemblyVoice) setAssemblyVoice(c.assemblyVoice);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const el = mainRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const recog = new SR();
    recog.lang = LANG_CODES[currentLang] || "en-US";
    recog.interimResults = true;
    recog.continuous = false;
    let finalText = "";
    recog.onstart = () => { finalText = ""; setListening(true); setStatus("Listening… speak now"); };
    recog.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const tr = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += tr;
        else interim += tr;
      }
      setQuery((finalText + interim).trim());
    };
    recog.onend = () => {
      setListening(false);
      const said = (finalText || "").trim();
      if (said) send(said);
      else setStatus("Tap the mic to talk, or type below");
    };
    recog.onerror = () => setListening(false);
    recogRef.current = recog;
  }, [currentLang]);

  const toast = (msg, icon = "check") => {
    const id = nid();
    setToasts((t) => [...t, { id, msg, icon }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2400);
  };

  const stopSpeaking = () => {
    if (currentAudioRef.current) {
      try { currentAudioRef.current.pause(); } catch (_) {}
      currentAudioRef.current = null;
    }
    try { window.speechSynthesis?.cancel(); } catch (_) {}
  };

  const speak = useCallback(async (text) => {
    if (!ttsOn || !text) return;
    stopSpeaking();
    const snippet = String(text).slice(0, 500);
    if (assemblyAi && currentLang === "en" && window.KaprukaAssemblySpeak) {
      try {
        await window.KaprukaAssemblySpeak.assemblySpeak(snippet, { voice: assemblyVoice });
        return;
      } catch (err) {
        console.warn("Assembly speak failed, falling back", err);
      }
    }
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: snippet, lang: currentLang }),
      });
      if (!res.ok) throw new Error("tts");
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      currentAudioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
      return;
    } catch (_) {}
    const synth = window.speechSynthesis;
    if (!synth) return;
    const u = new SpeechSynthesisUtterance(snippet);
    u.lang = LANG_CODES[currentLang] || "en-US";
    synth.speak(u);
  }, [ttsOn, currentLang, assemblyAi, assemblyVoice]);

  const confirmModal = (title, msg, okText, cancelText) =>
    new Promise((res) => setModal({ title, msg, okText, cancelText, resolve: res }));
  const closeModal = (val) => {
    if (modal?.resolve) modal.resolve(val);
    setModal(null);
  };

  const addToCartRaw = (product, qty = 1) => {
    if (!product?.name) return;
    const key = cartKey(product);
    setCart((prev) => {
      const ex = prev.find((c) => cartKey(c) === key);
      if (ex) return prev.map((c) => (cartKey(c) === key ? { ...c, qty: c.qty + qty } : c));
      return [...prev, { ...product, qty }];
    });
    setBump(true);
    setTimeout(() => setBump(false), 260);
  };

  const setItemCustom = (name, patch) =>
    setCart((prev) => prev.map((c) => (c.name === name ? { ...c, customization: { ...(c.customization || {}), ...patch } } : c)));

  const onUploadCustomPhoto = async (item, file) => {
    if (!file) return;
    if (isGuest || !supabase || !session?.user?.id) { toast("Sign in to attach a photo", "log-in"); return; }
    if (file.size > 8 * 1024 * 1024) { toast("Photo too large (max 8MB)", "x"); return; }
    toast("Uploading photo…", "upload");
    const url = await window.KaprukaSupabase.uploadCustomImage(supabase, session.user.id, file);
    if (url) { setItemCustom(item.name, { image_url: url }); toast("Photo attached", "check"); }
    else toast("Upload failed — try again", "x");
  };

  const addItems = async (products, quiet) => {
    products = (products || []).filter((p) => p?.name);
    if (!products.length) return;
    const addition = products.reduce((s, p) => s + priceNum(p) * (p.qty || 1), 0);
    const sub = cartSubtotal();
    if (budget != null && sub + addition > budget) {
      const label = products.length === 1 ? `"${products[0].name}"` : `these ${products.length} items`;
      const ok = await confirmModal(
        "Over your budget",
        `Adding ${label} brings your total to ${fmtMoney(sub + addition)} — ${fmtMoney(sub + addition - budget)} over your ${fmtMoney(budget)} budget. Add anyway, or keep within budget?`,
        "Add anyway",
        "Keep within budget"
      );
      if (!ok) { toast("Skipped to stay within budget", "wallet"); return; }
    }
    products.forEach((p) => addToCartRaw(p, p.qty || 1));
    if (!quiet) toast(products.length === 1 ? `Added "${products[0].name}"` : `Added ${products.length} items`, "shopping-cart");
  };

  const toggleWishlist = async (p) => {
    const turningOn = !fav[p.name];
    setFav((f) => ({ ...f, [p.name]: turningOn }));
    setWishlist((prev) => (turningOn
      ? (prev.some((w) => w.name === p.name) ? prev : [{ ...p, product_id: String(p.id || idFromUrl(p.url) || p.name) }, ...prev])
      : prev.filter((w) => w.name !== p.name)));
    if (turningOn) toast("Saved to wishlist", "heart");
    if (isGuest || !supabase || !session?.user?.id) return;
    try {
      const pid = p.id || idFromUrl(p.url) || p.name;
      if (turningOn) {
        await window.KaprukaSupabase.addWishlistItem(supabase, session.user.id, { ...p, id: pid });
      } else {
        await window.KaprukaSupabase.removeWishlistItem(supabase, session.user.id, pid);
      }
    } catch (_) {}
  };

  const applyCartActions = async (actions) => {
    if (!Array.isArray(actions)) return;
    for (const a of actions) {
      if (!a) continue;
      if (a.action === "add" && Array.isArray(a.products)) await addItems(a.products.map(normProduct), true);
      else if (a.action === "clear") { setCart([]); toast("Cart cleared", "trash-2"); }
      else if (a.action === "remove" && Array.isArray(a.items)) {
        setCart((prev) => {
          const idxs = a.items.map((n) => n - 1).sort((x, y) => y - x);
          const next = [...prev];
          idxs.forEach((i) => { if (i >= 0 && i < next.length) next.splice(i, 1); });
          return next;
        });
        toast("Removed from cart", "shopping-cart");
      } else if (a.action === "instruction" && a.text) {
        setInstructions((prev) => {
          const next = [...prev, a.text];
          if (!isGuest) persistProfile({ saved_instructions: next });
          return next;
        });
        toast("Note saved", "file-text");
      } else if (a.action === "customization" && a.product_name) {
        const patch = {};
        if (a.text) patch.text = a.text;
        if (a.wants_photo) patch.wants_photo = true;
        setItemCustom(a.product_name, patch);
        setCartOpen(true);
        toast("Customisation noted", "edit-3");
      }
    }
  };

  const setLastUserEnglish = (en) => {
    setConversation((c) => {
      const next = [...c];
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === "user") { next[i] = { ...next[i], content: en }; break; }
      }
      return next;
    });
  };

  // Decide whether THIS turn will actually fetch products (show skeletons) or
  // the agent will reply/ask a question (no skeletons) — mirrors the backend gates.
  const isBudgetAnswer = (text) => {
    const s = (text || "").trim();
    if (!s || s.length > 80) return false;
    return !!parseBudget(s) || BUDGET_WORDS_RE.test(s) || OPEN_BUDGET_RE.test(s) ||
      /^\s*(rs\.?|lkr|rupees)?\s*\d[\d,]*\s*$/i.test(s);
  };
  const expectProductsFor = (text) => {
    const t = (text || "").trim();
    if (EXPLICIT_PRODUCTS_RE.test(t)) return true;
    if (!looksLikeProductRequest(t)) return false;          // greetings, cart, tracking…
    if (REPLY_TURN_RE.test(t)) return false;                // repair / opinion / pick-best → reply
    // Budget reply (incl. open/no limit) → search for products next.
    if (!lastSuggestions.length && isBudgetAnswer(t)) return true;
    const budgetGiven =
      !!budget || !!parseBudget(t) || OPEN_BUDGET_RE.test(t) ||
      conversation.some((m) => m.role === "user" && (!!parseBudget(m.content) || OPEN_BUDGET_RE.test(m.content)));
    // First product turn with no budget yet → the budget question, not products.
    if (!lastSuggestions.length && !budgetGiven) return false;
    // A category named with no taste/style/colour yet, nothing on screen → discovery question.
    if (!lastSuggestions.length && PREF_RICH_HINT_RE.test(t) && !HAS_PREF_RE.test(t)) return false;
    // Recipient named but gender/taste/occasion/product all unknown → the agent asks
    // "guy or girl, what are they into?" first, so no products this turn.
    if (!lastSuggestions.length) {
      const recentUser = conversation
        .filter((m) => m.role === "user")
        .slice(-4)
        .map((m) => m.content)
        .join(" ");
      const blob = `${recentUser} ${t}`;
      if (
        RECIPIENT_CUE_RE.test(blob) &&
        !GENDER_CUE_RE.test(blob) &&
        !TASTE_CUE_RE.test(blob) &&
        !HAS_PREF_RE.test(blob) &&
        !OCCASION_CUE_RE.test(blob) &&
        !PREF_RICH_HINT_RE.test(blob)
      ) {
        return false;
      }
    }
    return true;
  };

  const send = async (text) => {
    text = (text || "").trim();
    if (!text) return;
    const b = parseBudget(text);
    const openBudget = OPEN_BUDGET_RE.test(text) && !b;
    if (b) {
      setBudget(b);
      if (!isGuest) persistProfile({ default_budget: b });
    }
    if (isGuest && !guestPromptedRef.current) {
      guestPromptedRef.current = true;
      toast("Sign in to remember your gifting style across visits", "heart");
    }
    setQuery("");
    setListening(false);
    setMessages((m) => [...m, { id: nid(), role: "user", text }]);
    const userTurn = { role: "user", content: text };
    const nextConv = [...conversation, userTurn];
    setConversation(nextConv);

    const proceed = awaitingAnswers;
    setAwaitingAnswers(false);

    const tid = nid();
    setExpectProducts(expectProductsFor(text));
    setMessages((m) => [...m, { id: tid, role: "bot", thinking: true }]);
    setStatus("Hari is thinking…");

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextConv,
          allow_questions: !proceed,
          suggestions: lastSuggestions,
          cart: cart.map((c) => ({ name: c.name, qty: c.qty, price: c.rawPrice ?? c.price, currency: c.currency, image: c.image, url: c.url, id: c.id })),
          instructions,
          budget: openBudget ? undefined : (budget ?? undefined),
          open_budget: openBudget || undefined,
          language: currentLang,
          access_token: accessToken || undefined,
        }),
      });
      let data;
      try {
        data = await res.json();
      } catch (_) {
        throw new Error(`Server error (${res.status})`);
      }
      if (!res.ok) {
        throw new Error(data?.error || `Server error (${res.status})`);
      }
      setMessages((m) => m.filter((x) => x.id !== tid));
      setExpectProducts(false);

      if (data.user_en) setLastUserEnglish(data.user_en);

      if (data.ok && data.needs_input) {
        setAwaitingAnswers(true);
        const shown = (data.questions_local && data.questions_local.length) ? data.questions_local : (data.questions || []);
        const qs = shown.length > 1 ? shown.map((q) => `• ${q}`).join("\n") : (shown[0] || "");
        const intro = (data.answer_local || data.answer || "").trim();
        const prefix = uiText("ask");
        const body = intro
          ? (prefix ? `${intro}\n\n${prefix}\n${qs}` : `${intro}\n\n${qs}`)
          : (prefix ? `${prefix}\n${qs}` : qs);
        const full = `${body}\n\n${uiText("skip")}`;
        setMessages((m) => [...m, { id: nid(), role: "bot", text: full }]);
        setConversation((c) => [...c, { role: "assistant", content: (intro ? intro + " " : "") + (data.questions || []).join(" ") }]);
        speak(full);
        setStatus("Tap the mic to talk, or type below");
        return;
      }

      if (data.ok) {
        if (Array.isArray(data.cart_actions) && data.cart_actions.length) await applyCartActions(data.cart_actions);
        let products = Array.isArray(data.products) ? data.products.map(normProduct) : [];
        if (!products.length && Array.isArray(data.results)) {
          products = extractProductsFromResults(data.results);
        }
        if (products.length) setLastSuggestions(data.products?.length ? data.products : products);
        const thought = products.length ? `Searched Kapruka · ${products.length} matches` : null;
        let display = data.answer_local || data.answer || "";
        const altQuestion = data.alternative_question_local || data.alternative_question || "";
        if (products.length) {
          display = stripCatalogFromText(display, products);
          if (!display.trim()) {
            display =
              "Pulled a few things that should work — they're right below 😊 Tell me if one jumps out.";
          }
        }
        if (display || products.length) {
          setMessages((m) => [...m, { id: nid(), role: "bot", text: display, thought, products, alternative_question: altQuestion || undefined }]);
        } else {
          setMessages((m) => [...m, { id: nid(), role: "bot", text: "I couldn't find matching products — try giving me more detail." }]);
        }
        if (data.answer) {
          setConversation((c) => [...c, { role: "assistant", content: data.answer }]);
          const speechText = altQuestion ? `${display}\n\n${altQuestion}` : display;
          if (speechText) speak(speechText);
        }
      } else {
        const err = data.error || JSON.stringify(data);
        setMessages((m) => [...m, { id: nid(), role: "bot", text: `Something went wrong: ${err}` }]);
      }
    } catch (err) {
      setExpectProducts(false);
      setMessages((m) => m.filter((x) => x.id !== tid));
      setMessages((m) => [...m, { id: nid(), role: "bot", text: `Request failed: ${String(err)}` }]);
    }
    setStatus("Tap the mic to talk, or type below");
  };

  const micClick = () => {
    if (query.trim()) { send(query); return; }
    const recog = recogRef.current;
    if (!recog) { toast("Voice input isn't supported in this browser", "mic"); return; }
    if (listening) recog.stop();
    else { stopSpeaking(); try { recog.start(); } catch (_) {} }
  };

  const setQty = (name, d) =>
    setCart((prev) =>
      prev.map((c) => (c.name === name ? { ...c, qty: c.qty + d } : c)).filter((c) => c.qty > 0)
    );

  const placeOrder = async (e) => {
    e.preventDefault();
    if (!cart.length) { toast("Your cart is empty", "shopping-cart"); return; }
    const f = checkoutFormRef.current;
    const items = [];
    const customNotes = [];
    for (const c of cart) {
      const pid = c.id || idFromUrl(c.url);
      if (!pid) {
        setCheckoutResult(`Couldn't find a product ID for "${c.name}". Remove it and re-add from suggestions.`);
        return;
      }
      const item = { product_id: pid, quantity: c.qty };
      const cu = c.customization || {};
      if (cu.text) {
        item.icing_text = cu.text;
      }
      if (cu.text || cu.image_url) {
        customNotes.push(
          `Personalisation for ${c.name}: ${[cu.text && `text="${cu.text}"`, cu.image_url && `photo=${cu.image_url}`].filter(Boolean).join(", ")}`
        );
      }
      items.push(item);
    }
    const city = (checkoutCity || f.city?.value || "").trim();
    if (!city) {
      setCheckoutResult("Pick a delivery city from Kapruka's list.");
      return;
    }
    const params = {
      cart: items,
      recipient: { name: f.rname.value.trim(), phone: f.rphone.value.trim() },
      delivery: { address: f.address.value.trim(), city, date: f.date.value, location_type: f.location_type.value },
      sender: { name: f.sname.value.trim() },
      currency: cart[0]?.currency || "LKR",
      response_format: "json",
    };
    const instrParts = [f.instructions.value.trim(), ...customNotes].filter(Boolean);
    if (instrParts.length) params.delivery.instructions = instrParts.join(" | ");
    if (f.gift_message.value.trim()) params.gift_message = f.gift_message.value.trim();

    setPlacing(true);
    setCheckoutResult("");
    const payTab = window.open("about:blank", "_blank");
    try {
      const res = await fetch("/api/tool", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "kapruka_create_order", arguments: { params } }),
      });
      const data = await res.json();
      let order = null;
      try { order = JSON.parse(data.output); } catch (_) {}
      const checkout = data.checkout || order;
      const payUrl = checkout?.checkout_url;
      if (data.ok && payUrl) {
        if (payTab) {
          payTab.location.href = payUrl;
          payTab.focus?.();
        } else {
          const opened = window.open(payUrl, "_blank", "noopener,noreferrer");
          if (!opened) window.location.assign(payUrl);
        }
        const s = checkout.summary || {};
        const tot = s.grand_total != null ? fmtMoney(s.grand_total, s.currency) : "";
        setCheckoutResult(
          `Order ready!${checkout.order_ref ? ` Ref: ${checkout.order_ref}.` : ""}${tot ? ` Total: ${tot}.` : ""} Pay link opened in a new tab — complete checkout there.`
        );
        toast("Order created — pay link opened", "check");
        if (supabase && session?.user?.id) {
          window.KaprukaSupabase.saveOrderHistory(supabase, session.user.id, {
            recipient_name: f.rname.value.trim(),
            items_summary: cart.map((c) => c.name).join(", "),
            order_ref: checkout.order_ref || null,
            grand_total: s.grand_total != null ? Number(s.grand_total) : null,
            currency: s.currency || cart[0]?.currency || "LKR",
          });
        }
      } else {
        payTab?.close();
        const raw = (data.output && String(data.output)) || data.error || "Order could not be created.";
        const cityErr = window.KaprukaDeliveryCities?.formatCityError?.(raw);
        setCheckoutResult(cityErr || raw);
      }
    } catch (err) {
      payTab?.close();
      setCheckoutResult(String(err));
    } finally {
      setPlacing(false);
    }
  };

  const openCheckout = () => {
    if (!cart.length) { toast("Your cart is empty", "shopping-cart"); return; }
    setCheckoutView(true);
    setCheckoutResult("");
    if (defaultCity && !checkoutCity) setCheckoutCity(defaultCity);
    const today = new Date();
    today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
    if (checkoutFormRef.current?.date) checkoutFormRef.current.date.min = today.toISOString().slice(0, 10);
  };

  const total = cartSubtotal();
  const cur = cart[0]?.currency || "LKR";
  const overBudget = budget != null && total > budget;

  return (
    <React.Fragment>
      <header className={"topbar" + (messages.some((m) => m.thinking) ? " is-thinking" : "") + (listening ? " is-listening" : "")}>
        <div className="inner">
          <div className="greet">
            <span className="brandmark"><Icon name="leaf" size={20} /></span>
            <div className="greet-text">
              <span className="greet-sub">{greetSub()}</span>
              <span className="greet-title">What can I find for you?</span>
            </div>
          </div>
          <div className="top-actions">
            <div className={"top-lang" + (langOpen ? " open" : "")} ref={langRef}>
              <button
                type="button"
                className="top-menu-trigger k-iconbtn"
                title="Language"
                aria-label="Language"
                aria-expanded={langOpen}
                aria-haspopup="menu"
                onClick={() => { setLangOpen((v) => !v); setMenuOpen(false); }}
              >
                <Icon name="globe" size={20} />
                <span className="top-lang-tag">{(currentLang || "en").toUpperCase()}</span>
              </button>
              {langOpen ? (
                <div className="top-lang-panel" role="menu">
                  {[["en", "English"], ["si", "සිංහල"], ["ta", "தமிழ்"]].map(([code, label]) => (
                    <button
                      key={code}
                      type="button"
                      role="menuitemradio"
                      aria-checked={currentLang === code}
                      className={"top-lang-opt" + (currentLang === code ? " on" : "")}
                      onClick={() => {
                        setCurrentLang(code);
                        if (recogRef.current) recogRef.current.lang = LANG_CODES[code] || "en-US";
                        if (!isGuest) persistProfile({ default_language: code });
                        toast(`Language: ${LANG_NAMES[code]}`, "globe");
                        setLangOpen(false);
                      }}
                    >
                      <span>{label}</span>
                      {currentLang === code ? <Icon name="check" size={16} /> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="top-cart">
              <button
                type="button"
                className="top-menu-trigger k-iconbtn"
                title="Cart"
                aria-label="Cart"
                onClick={() => { setCartOpen(true); setCheckoutView(false); setMenuOpen(false); }}
                style={bump ? { transform: "scale(1.08)" } : undefined}
              >
                <Icon name="shopping-cart" size={20} />
                {cartCount > 0 ? <span className="top-menu-badge">{cartCount}</span> : null}
              </button>
            </div>
            <div className={"top-menu" + (menuOpen ? " open" : "")} ref={menuRef}>
              <button
                type="button"
                className="top-menu-trigger k-iconbtn"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                title="Menu"
                onClick={() => setMenuOpen((v) => !v)}
              >
                <span className="top-menu-bars" aria-hidden="true">
                  <span /><span /><span />
                </span>
              </button>
              {menuOpen ? (
                <div className="top-menu-panel" role="menu">
                  <button
                    type="button"
                    className={"top-menu-account" + (isGuest ? " guest" : "")}
                    role="menuitem"
                    onClick={() => {
                      closeMenu();
                      if (isGuest) onRequestSignIn();
                    }}
                  >
                    <Icon name={isGuest ? "heart" : "sparkles"} size={16} />
                    <span className="top-menu-account-text">
                      <span className="top-menu-account-label">{isGuest ? "Sign in" : displayName}</span>
                      {!isGuest ? <span className="top-menu-account-sub">Your account</span> : null}
                    </span>
                  </button>
                  <div className="top-menu-divider" />
                  <button
                    type="button"
                    className="top-menu-item"
                    role="menuitem"
                    onClick={newChat}
                  >
                    <Icon name="plus" size={18} />
                    <span>New chat</span>
                  </button>
                  <button
                    type="button"
                    className="top-menu-item"
                    role="menuitem"
                    onClick={() => {
                      closeMenu();
                      setChatList(loadChatIndex(scope));
                      setHistoryOpen(true);
                      setPeopleOpen(false);
                      setSettingsOpen(false);
                      setCartOpen(false);
                      setWishlistOpen(false);
                    }}
                  >
                    <Icon name="clock" size={18} />
                    <span>History{chatList.length ? ` (${chatList.length})` : ""}</span>
                  </button>
                  <div className="top-menu-divider" />
                  {!isGuest && session ? (
                    <button
                      type="button"
                      className="top-menu-item"
                      role="menuitem"
                      onClick={() => {
                        closeMenu();
                        setPeopleOpen(true);
                        setSettingsOpen(false);
                        setCartOpen(false);
                        setWishlistOpen(false);
                      }}
                    >
                      <Icon name="users" size={18} />
                      <span>My people</span>
                    </button>
                  ) : null}
                  {!isGuest && session ? (
                    <button
                      type="button"
                      className="top-menu-item"
                      role="menuitem"
                      onClick={() => {
                        closeMenu();
                        setSettingsOpen(true);
                        setPeopleOpen(false);
                        setCartOpen(false);
                      }}
                    >
                      <Icon name="star" size={18} />
                      <span>Gifting preferences</span>
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="top-menu-item"
                    role="menuitem"
                    onClick={() => {
                      closeMenu();
                      setWishlistOpen(true);
                      setCartOpen(false);
                      setPeopleOpen(false);
                      setSettingsOpen(false);
                    }}
                  >
                    <Icon name="heart" size={18} />
                    <span>Wishlist{wishlist.length > 0 ? ` (${wishlist.length})` : ""}</span>
                  </button>
                  <button
                    type="button"
                    className={"top-menu-item" + (ttsOn ? " on" : "")}
                    role="menuitem"
                    onClick={() => {
                      setTtsOn((v) => !v);
                      if (!ttsOn) toast("Voice replies on", "volume-2");
                      else stopSpeaking();
                    }}
                  >
                    <Icon name="volume-2" size={18} />
                    <span>{ttsOn ? "Voice replies on" : "Voice replies off"}</span>
                  </button>
                  <div className="top-menu-divider" />
                  <div className="top-menu-row" role="none">
                    <Icon name="globe" size={18} />
                    <span>Language</span>
                    <select
                      className="top-menu-langsel"
                      value={currentLang}
                      aria-label="Language"
                      onChange={(e) => {
                        setCurrentLang(e.target.value);
                        if (recogRef.current) recogRef.current.lang = LANG_CODES[e.target.value] || "en-US";
                        toast(`Language: ${LANG_NAMES[e.target.value]}`, "globe");
                      }}
                    >
                      <option value="en">EN</option>
                      <option value="si">සිං</option>
                      <option value="ta">தமிழ்</option>
                    </select>
                  </div>
                  {ThemeToggle ? (
                    <div className="top-menu-row" role="none">
                      <Icon name="sun" size={18} />
                      <span>Theme</span>
                      <ThemeToggle size={36} />
                    </div>
                  ) : null}
                  {!isGuest && session ? (
                    <>
                      <div className="top-menu-divider" />
                      <button
                        type="button"
                        className="top-menu-item top-menu-item--danger"
                        role="menuitem"
                        onClick={() => {
                          closeMenu();
                          onSignOut();
                        }}
                      >
                        <Icon name="x" size={18} />
                        <span>Sign out</span>
                      </button>
                    </>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </header>

      <main ref={mainRef}>
        <div className="thread">
          {!started && messages.length <= 1 && (
            <div className="welcome-hero">
              <div className="welcome-mark"><Icon name="leaf" size={28} /></div>
              <h2 className="welcome-title">Find the perfect gift</h2>
              <p className="welcome-tag">Tell me who it's for and the occasion — I'll handle the rest.</p>
            </div>
          )}
          <div className="feed" ref={feedRef}>
            {messages.map((m) => (
              <div className={"msg-in msg-in--" + m.role} key={m.id} style={{ display: "flex", flexDirection: "column" }}>
                {m.thought && m.role === "bot" && (
                  <div className="bot-thought">
                    <Icon name="sparkles" size={14} />
                    <span>{m.thought}</span>
                  </div>
                )}
                <Bubble role={m.role} thinking={m.thinking}>
                  {m.role === "bot" && m.text && !m.thinking ? <BotText text={m.text} /> : m.text}
                </Bubble>
                {m.thinking && expectProducts && (
                  <div className="grid skel-grid" style={{ marginLeft: "2.4rem" }} aria-hidden="true">
                    {[0, 1, 2, 3].map((i) => (
                      <div className="skel-card" key={i}>
                        <div className="skel-img" />
                        <div className="skel-line" />
                        <div className="skel-line short" />
                      </div>
                    ))}
                  </div>
                )}
                {m.products?.length > 0 && (() => {
                  const groups = groupProducts(m.products);
                  const showHeaders = groups.length > 1;
                  let n = 0;
                  const renderCard = (p) => {
                    const i = n++;
                    return (
                      <div className="k-rise" key={p.name + i} style={{ position: "relative", animationDelay: `${Math.min(i, 8) * 70}ms` }}>
                        {p.customizable && (
                          <span style={{ position: "absolute", top: ".4rem", left: ".4rem", zIndex: 2, display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".68rem", fontWeight: 600, padding: ".2rem .45rem", borderRadius: ".5rem", background: "var(--accent, #e08aa0)", color: "#1a1a1f" }}>
                            <Icon name="sparkles" size={11} /> {p.customization_type === "photo" ? "Add your photo" : "Personalise"}
                          </span>
                        )}
                        <ProductCard
                          {...p}
                          favorite={!!fav[p.name]}
                          onFavorite={() => toggleWishlist(p)}
                          onAdd={() => addItems([p])}
                        />
                      </div>
                    );
                  };
                  return (
                    <div className="prod-wrap">
                      {groups.map((g) => (
                        <div key={g.label} className="prod-group">
                          {showHeaders && <h4 className="prod-group-label">{g.label}</h4>}
                          <div className="grid">{g.items.map(renderCard)}</div>
                        </div>
                      ))}
                    </div>
                  );
                })()}
                {m.alternative_question && (
                  <div style={{ marginTop: "1rem" }}>
                    <Bubble role="bot">
                      <BotText text={m.alternative_question} />
                    </Bubble>
                  </div>
                )}
                {m.id === lastProductMsgId && (
                  <div className="refine-chips" style={{ marginLeft: "2.4rem" }}>
                    {REFINE_CHIPS.map((c) => (
                      <button
                        type="button"
                        className="refine-chip"
                        key={c.tx}
                        onClick={() => send(c.prompt)}
                      >
                        <Icon name={c.icon} size={13} />
                        <span>{c.tx}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </main>

      <footer className="composer">
        <div className="composer-inner">
          <p className="status">{listening ? "Listening…" : status}</p>
          {!started && (
            <div className="cards">
              {CHIPS.map((c) => (
                <SuggestionCard key={c.prompt} icon={c.icon} tone={c.tone} onClick={() => send(c.prompt)}>
                  {c.tx}
                </SuggestionCard>
              ))}
            </div>
          )}
          <form className="searchbar" onSubmit={(e) => { e.preventDefault(); send(query); }}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              type="text"
              autoComplete="off"
              placeholder="Who are we spoiling today?"
            />
            <button
              type="button"
              className={"mic" + (listening ? " mic--listening" : "")}
              aria-label={query.trim() ? "Send" : "Tap to talk"}
              onClick={micClick}
            >
              <Icon name={query.trim() ? "send" : "mic"} size={20} />
            </button>
          </form>
        </div>
      </footer>

      <div className={"scrim" + (cartOpen || wishlistOpen || settingsOpen || peopleOpen || historyOpen ? " open" : "")} onClick={() => { setCartOpen(false); setWishlistOpen(false); setSettingsOpen(false); setPeopleOpen(false); setHistoryOpen(false); }} />
      <aside className={"drawer" + (cartOpen ? " open" : "")} aria-label="Cart">
        <header>
          <Icon name="shopping-cart" size={20} />
          <h3>Your cart</h3>
          <IconButton icon="x" title="Close" style={{ marginLeft: "auto" }} onClick={() => setCartOpen(false)} />
        </header>
        <div className="body">
          {!checkoutView ? (
            <React.Fragment>
              {cart.length === 0 ? (
                <div className="empty">
                  <span className="ic"><Icon name="gift" size={40} strokeWidth={1.5} /></span>
                  Your cart is empty.<br />Add items from the suggestions, or just ask me to.
                </div>
              ) : cart.map((c) => (
                <div key={c.name}>
                  <div className="citem">
                    {c.image ? <img className="ci-img" src={c.image} alt="" /> : <div className="ci-noimg"><Icon name="gift" size={20} /></div>}
                    <div className="ci-main">
                      <div className="ci-name">{c.name}</div>
                      <div className="ci-price">{fmtMoney(priceNum(c) * c.qty, c.currency)}</div>
                    </div>
                    <div className="qty">
                      <button type="button" aria-label="Decrease" onClick={() => setQty(c.name, -1)}><Icon name="minus" size={14} /></button>
                      <span>{c.qty}</span>
                      <button type="button" aria-label="Increase" onClick={() => setQty(c.name, 1)}><Icon name="plus" size={14} /></button>
                    </div>
                    <button type="button" className="ci-rm" title="Remove" onClick={() => setQty(c.name, -c.qty)}><Icon name="trash-2" size={16} /></button>
                  </div>
                  {c.customizable && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: ".5rem", alignItems: "center", margin: ".1rem 0 .6rem", paddingLeft: ".2rem" }}>
                      <span style={{ fontSize: ".72rem", opacity: .7, display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
                        <Icon name="sparkles" size={13} /> Personalise
                      </span>
                      <input
                        style={{ flex: "1 1 8rem", minWidth: "8rem", fontSize: ".82rem", padding: ".35rem .5rem", borderRadius: ".5rem", border: "1px solid var(--border, #3a3a46)", background: "var(--surface, #1c1c24)", color: "inherit" }}
                        placeholder={c.customization_type === "photo" ? "Note for the print (optional)" : "Name / message to print"}
                        value={c.customization?.text || ""}
                        maxLength={120}
                        onChange={(e) => setItemCustom(c.name, { text: e.target.value })}
                      />
                      {c.customization_type === "photo" && (
                        isGuest ? (
                          <span style={{ fontSize: ".72rem", opacity: .7 }}>Sign in to attach a photo</span>
                        ) : (
                          <label style={{ fontSize: ".76rem", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem", padding: ".35rem .55rem", borderRadius: ".5rem", border: "1px solid var(--border, #3a3a46)" }}>
                            <Icon name={c.customization?.image_url ? "check" : "image"} size={14} />
                            {c.customization?.image_url ? "Photo attached" : "Attach photo"}
                            <input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => onUploadCustomPhoto(c, e.target.files?.[0])} />
                          </label>
                        )
                      )}
                    </div>
                  )}
                </div>
              ))}
            </React.Fragment>
          ) : (
            <form ref={checkoutFormRef} className="formgrid" onSubmit={placeOrder}>
              <div className="full"><label>Recipient name *</label><input name="rname" required placeholder="Who receives the gift" /></div>
              <div className="full"><label>Recipient phone *</label><input name="rphone" required placeholder="07X XXX XXXX" /></div>
              <div className="full"><label>Delivery address *</label><input name="address" required /></div>
              <div className="full">
                <label>City *</label>
                <CityPicker
                  name="city"
                  required
                  placeholder="Search Kapruka delivery cities…"
                  value={checkoutCity}
                  onChange={setCheckoutCity}
                />
              </div>
              <div><label>Delivery date *</label><input name="date" type="date" required /></div>
              <div className="full"><label>Address type</label>
                <select name="location_type"><option value="house">House</option><option value="apartment">Apartment</option><option value="office">Office</option><option value="other">Other</option></select>
              </div>
              <div className="full"><label>Your name (sender) *</label><input name="sname" required /></div>
              <div className="full"><label>Gift card message</label><textarea name="gift_message" rows={2} maxLength={300} /></div>
              <div className="full"><label>Delivery instructions</label><textarea name="instructions" rows={2} maxLength={250} defaultValue={instructions.join("; ")} /></div>
              {checkoutResult && <div className="full co-bad" style={checkoutResult.startsWith("Order") ? { borderColor: "var(--ok)", background: "color-mix(in srgb, var(--ok) 12%, var(--surface))" } : undefined}>{checkoutResult}</div>}
            </form>
          )}
        </div>
        <div className="foot">
          {!checkoutView ? (
            <React.Fragment>
              <div className="budgetrow">
                <span className="lbl">Budget (LKR)</span>
                <input type="number" min="0" step="100" placeholder="none set" value={budget ?? ""} onChange={(e) => setBudget(e.target.value ? +e.target.value : null)} />
              </div>
              <div className={"totrow" + (overBudget ? " over" : "")}>
                <span className="lbl">Estimated total</span>
                <span className="val">{fmtMoney(total, cur)}</span>
              </div>
              {overBudget && <div className="budgetwarn">{fmtMoney(total - budget, cur)} over your {fmtMoney(budget, cur)} budget.</div>}
              <label>Notes for the store</label>
              <div className="instrlist">
                {instructions.map((tx, i) => (
                  <div className="instr" key={i}><span>{tx}</span>
                    <button type="button" className="ci-rm" onClick={() => setInstructions((prev) => prev.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
                  </div>
                ))}
              </div>
              <textarea rows={2} placeholder="e.g. gift-wrap everything as one hamper" onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  const v = e.target.value.trim();
                  if (v) {
                    setInstructions((p) => {
                      const next = [...p, v];
                      if (!isGuest) persistProfile({ saved_instructions: next });
                      return next;
                    });
                    e.target.value = "";
                    toast("Note saved", "file-text");
                  }
                }
              }} />
              <div className="row">
                <Button variant="soft" size="sm" icon="trash-2" disabled={!cart.length} onClick={() => { const had = cart.length; setCart([]); setCheckoutView(false); if (had) toast("Cart cleared", "trash-2"); }}>Clear</Button>
              </div>
              <Button variant="primary" full iconRight="arrow-right" disabled={!cart.length} onClick={openCheckout}>
                Proceed to checkout
              </Button>
            </React.Fragment>
          ) : (
            <React.Fragment>
              <div className="totrow"><span className="lbl">Order total</span><span className="val">{fmtMoney(total, cur)}</span></div>
              <Button variant="primary" full disabled={placing} onClick={() => checkoutFormRef.current?.requestSubmit()}>
                {placing ? "Placing order…" : "Place order & get pay link"}
              </Button>
              <button type="button" className="btn-ghost" onClick={() => setCheckoutView(false)}>← Back to cart</button>
            </React.Fragment>
          )}
        </div>
      </aside>

      <aside className={"drawer" + (wishlistOpen ? " open" : "")} aria-label="Wishlist">
        <header>
          <Icon name="heart" size={20} />
          <h3>Your wishlist</h3>
          <IconButton icon="x" title="Close" style={{ marginLeft: "auto" }} onClick={() => setWishlistOpen(false)} />
        </header>
        <div className="body">
          {wishlist.length === 0 ? (
            <div className="empty">
              <span className="ic"><Icon name="heart" size={40} strokeWidth={1.5} /></span>
              Nothing saved yet.<br />Tap the heart on any suggestion to keep it here.
            </div>
          ) : wishlist.map((w) => (
            <div className="citem" key={w.product_id || w.name}>
              {w.image ? <img className="ci-img" src={w.image} alt="" /> : <div className="ci-noimg"><Icon name="gift" size={20} /></div>}
              <div className="ci-main">
                <div className="ci-name">{w.name}</div>
                {w.price != null && w.price !== "" ? <div className="ci-price">{`${w.currency || "LKR"} ${w.price}`}</div> : null}
              </div>
              <button type="button" className="k-iconbtn" title="Add to cart" aria-label="Add to cart" onClick={() => { addItems([normProduct({ ...w, id: w.id || w.product_id })]); }}>
                <Icon name="shopping-cart" size={16} />
              </button>
              <button type="button" className="ci-rm" title="Remove from wishlist" onClick={() => toggleWishlist(w)}><Icon name="trash-2" size={16} /></button>
            </div>
          ))}
        </div>
      </aside>

      <aside className={"drawer" + (historyOpen ? " open" : "")} aria-label="Chat history">
        <header>
          <Icon name="clock" size={20} />
          <h3>Your chats</h3>
          <IconButton icon="x" title="Close" style={{ marginLeft: "auto" }} onClick={() => setHistoryOpen(false)} />
        </header>
        <div className="body">
          <button type="button" className="hist-new" onClick={newChat}>
            <Icon name="plus" size={16} /> New chat
          </button>
          {chatList.length === 0 ? (
            <div className="empty">
              <span className="ic"><Icon name="message-circle" size={40} strokeWidth={1.5} /></span>
              No past chats yet.<br />Your conversations will show up here.
            </div>
          ) : chatList.map((c) => (
            <div className={"citem hist-item" + (c.id === chatId ? " active" : "")} key={c.id}>
              <button type="button" className="hist-open" onClick={() => openChat(c.id)}>
                <div className="ci-main">
                  <div className="ci-name">{c.title || "Chat"}</div>
                  <div className="ci-price">{relTime(c.ts)}</div>
                </div>
              </button>
              <button type="button" className="ci-rm" title="Delete chat" onClick={() => deleteChat(c.id)}><Icon name="trash-2" size={16} /></button>
            </div>
          ))}
        </div>
      </aside>

      {settingsOpen && !isGuest && (
        <SettingsPanel
          profile={profile}
          supabase={supabase}
          session={session}
          budget={budget}
          setBudget={setBudget}
          currentLang={currentLang}
          setCurrentLang={setCurrentLang}
          onClose={() => setSettingsOpen(false)}
          onProfileUpdate={onProfileUpdate}
          toast={toast}
        />
      )}
      {peopleOpen && !isGuest && (
        <RecipientsPanel
          recipients={recipients}
          supabase={supabase}
          session={session}
          onClose={() => setPeopleOpen(false)}
          onRefresh={refreshRecipients}
          toast={toast}
        />
      )}

      {modal && (
        <div className="modal-scrim open" onClick={() => closeModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h4>{modal.title}</h4>
            <p>{modal.msg}</p>
            <div className="actions">
              <button type="button" className="btn-ghost" style={{ flex: 1, marginTop: 0 }} onClick={() => closeModal(false)}>{modal.cancelText}</button>
              <Button variant="primary" style={{ flex: 1 }} onClick={() => closeModal(true)}>{modal.okText}</Button>
            </div>
          </div>
        </div>
      )}

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <Toast key={t.id} icon={t.icon}>{t.msg}</Toast>
        ))}
      </div>
    </React.Fragment>
  );
}

function AuthGate({ supabase, initialMode, onGuest, onAuthed }) {
  const [mode, setMode] = useState(initialMode || "welcome");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const resetErr = () => setError("");

  const googleSignIn = async () => {
    if (!supabase) {
      setError("Sign-in is not configured yet. Add SUPABASE_URL and SUPABASE_ANON_KEY on Vercel, then redeploy.");
      return;
    }
    resetErr();
    setBusy(true);
    try {
      const { error: err } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo: window.location.origin },
      });
      if (err) setError(err.message);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const emailAuth = async (isSignup) => {
    if (!supabase) {
      setError("Sign-in is not configured yet. Add SUPABASE_URL and SUPABASE_ANON_KEY on Vercel, then redeploy.");
      return;
    }
    resetErr();
    setBusy(true);
    try {
      if (isSignup) {
        const { data, error: err } = await supabase.auth.signUp({
          email: email.trim(),
          password,
          options: { data: { full_name: displayName.trim() || undefined } },
        });
        if (err) throw err;
        if (data.session) onAuthed(data.session);
        else setError("Check your email to confirm your account, then log in.");
      } else {
        const { data, error: err } = await supabase.auth.signInWithPassword({
          email: email.trim(),
          password,
        });
        if (err) throw err;
        if (data.session) onAuthed(data.session);
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (mode === "welcome") {
    return (
      <div className="auth-shell">
        <div className="auth-card k-rise">
          <span className="auth-brand"><Icon name="leaf" size={28} /></span>
          <h1>Your personal gift concierge</h1>
          <p>
            Sign in so Hari remembers your gifting style, budget, and delivery
            preferences — and finds better matches every time.
          </p>
          <div className="auth-actions">
            <button type="button" className="auth-btn auth-btn--primary" onClick={() => { resetErr(); setMode("signup"); }}>
              Sign up
            </button>
            <button type="button" className="auth-btn auth-btn--soft" onClick={() => { resetErr(); setMode("login"); }}>
              Log in
            </button>
            <button type="button" className="auth-link" onClick={() => googleSignIn()} disabled={busy}>
              Continue with Google
            </button>
            <button type="button" className="auth-ghost" onClick={onGuest}>
              Continue without account
            </button>
          </div>
          {error && <p className="auth-error">{error}</p>}
        </div>
      </div>
    );
  }

  const isSignup = mode === "signup";

  return (
    <div className="auth-shell">
      <div className="auth-card k-rise">
        <button type="button" className="auth-back" onClick={() => { resetErr(); setMode("welcome"); }}>
          ← Back
        </button>
        <h1>{isSignup ? "Create your account" : "Welcome back"}</h1>
        <p>{isSignup ? "A quick quiz after signup helps us learn your gifting personality." : "Log in to pick up where you left off."}</p>
        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault();
            emailAuth(isSignup);
          }}
        >
          {isSignup && (
            <label>
              Name
              <input
                type="text"
                placeholder="Your name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                autoComplete="name"
              />
            </label>
          )}
          <label>
            Email
            <input
              type="email"
              required
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label>
            Password
            <input
              type="password"
              required
              minLength={6}
              placeholder="At least 6 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isSignup ? "new-password" : "current-password"}
            />
          </label>
          <button type="submit" className="auth-btn auth-btn--primary" disabled={busy}>
            {busy ? "Please wait…" : isSignup ? "Sign up" : "Log in"}
          </button>
        </form>
        <button type="button" className="auth-link" onClick={() => googleSignIn()} disabled={busy}>
          Continue with Google
        </button>
        <button type="button" className="auth-ghost" onClick={onGuest}>
          Continue without account
        </button>
        {error && <p className="auth-error">{error}</p>}
      </div>
    </div>
  );
}

const ONBOARDING_STEPS = [
  { key: "gift_priority", title: "What matters most in a gift?", options: [
    { value: "thoughtfulness", label: "Thoughtfulness & meaning" },
    { value: "surprise", label: "Surprise & delight" },
    { value: "practicality", label: "Practical & useful" },
    { value: "wow_factor", label: "Wow factor & premium feel" },
  ]},
  { key: "budget_band", title: "Your typical gift budget?", options: [
    { value: "under_2000", label: "Under Rs 2,000" },
    { value: "2000_5000", label: "Rs 2,000 – 5,000" },
    { value: "5000_10000", label: "Rs 5,000 – 10,000" },
    { value: "over_10000", label: "Rs 10,000+" },
  ]},
  { key: "shopping_style", title: "How do you usually shop for gifts?", options: [
    { value: "weeks_ahead", label: "Weeks ahead — I plan early" },
    { value: "few_days", label: "A few days before" },
    { value: "last_minute", label: "Last minute — I need it fast" },
  ]},
  { key: "recipient_focus", title: "Who do you shop for most often?", options: [
    { value: "family", label: "Family" },
    { value: "partner", label: "Partner" },
    { value: "colleagues", label: "Colleagues" },
    { value: "kids", label: "Kids" },
    { value: "mixed", label: "A mix of everyone" },
  ]},
  { key: "style_vibe", title: "Your gifting style vibe?", options: [
    { value: "classic", label: "Classic & elegant" },
    { value: "playful", label: "Fun & playful" },
    { value: "minimalist", label: "Minimal & modern" },
    { value: "traditional", label: "Traditional Sri Lankan" },
  ]},
  { key: "avoid_list", title: "Anything to always avoid?", type: "multi", options: AVOID_OPTIONS },
  { key: "dietary", title: "Dietary preferences?", type: "single", options: DIETARY_OPTIONS },
  { key: "default_city", title: "Default delivery city?", type: "city" },
];

function OnboardingWizard({ supabase, session, onComplete }) {
  const computePersonality = (window.KaprukaPersonality && window.KaprukaPersonality.computePersonality) || (() => ({}));
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState({});
  const [cityChoice, setCityChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const current = ONBOARDING_STEPS[step];
  const progress = ((step + 1) / ONBOARDING_STEPS.length) * 100;

  const pick = (key, value) => {
    setAnswers((a) => ({ ...a, [key]: value }));
    setTimeout(() => setStep((s) => Math.min(s + 1, ONBOARDING_STEPS.length - 1)), 180);
  };

  const toggleMulti = (key, value) => {
    setAnswers((a) => {
      const cur = Array.isArray(a[key]) ? [...a[key]] : [];
      if (value === "none") return { ...a, [key]: ["none"] };
      const next = cur.includes(value)
        ? cur.filter((x) => x !== value)
        : [...cur.filter((x) => x !== "none"), value];
      return { ...a, [key]: next };
    });
  };

  const stepReady = () => {
    if (current.type === "city") return cityChoice.trim().length >= 2;
    if (current.type === "multi") return Array.isArray(answers[current.key]) && answers[current.key].length > 0;
    if (current.type === "single") return Boolean(answers[current.key]);
    return Boolean(answers[current.key]);
  };

  const finish = async () => {
    setBusy(true);
    setError("");
    try {
      const finalAnswers = { ...answers };
      finalAnswers.default_city = cityChoice.trim() || "Colombo 03";
      const computed = computePersonality(finalAnswers);
      const patch = {
        quiz_answers: finalAnswers,
        gifting_personality: computed.gifting_personality,
        personality_scores: computed.personality_scores,
        default_budget: computed.default_budget,
        preferences: computed.preferences,
        default_city: finalAnswers.default_city,
        display_name: session.user.user_metadata?.full_name || session.user.email?.split("@")[0],
        onboarding_completed: true,
      };
      const { data, error: err } = await supabase
        .from("profiles")
        .update(patch)
        .eq("id", session.user.id)
        .select()
        .single();
      if (err) throw err;
      onComplete(data);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const canFinish = current.key === "default_city" && cityChoice.trim().length >= 2;

  return (
    <div className="auth-shell">
      <div className="auth-card wizard-card k-rise">
        <div className="wizard-progress">
          <div className="wizard-progress-bar" style={{ width: `${progress}%` }} />
        </div>
        <p className="wizard-step">Step {step + 1} of {ONBOARDING_STEPS.length}</p>
        <h1>{current.title}</h1>
        {current.type === "city" ? (
          <div className="wizard-city-picker">
            <CityPicker
              value={cityChoice}
              onChange={setCityChoice}
              placeholder="Search Kapruka delivery cities…"
            />
          </div>
        ) : current.type === "multi" ? (
          <div className="wizard-options">
            {current.options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={"wizard-opt" + ((answers[current.key] || []).includes(opt.value) ? " wizard-opt--on" : "")}
                onClick={() => toggleMulti(current.key, opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        ) : (
          <div className="wizard-options">
            {current.options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={"wizard-opt" + (answers[current.key] === opt.value ? " wizard-opt--on" : "")}
                onClick={() => pick(current.key, opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
        <div className="wizard-nav">
          {step > 0 && (
            <button type="button" className="auth-btn auth-btn--soft" onClick={() => setStep((s) => s - 1)}>Back</button>
          )}
          {step < ONBOARDING_STEPS.length - 1 && stepReady() && current.key !== "default_city" && (
            <button type="button" className="auth-btn auth-btn--primary" onClick={() => setStep((s) => s + 1)}>Next</button>
          )}
          {step === ONBOARDING_STEPS.length - 1 && (
            <button type="button" className="auth-btn auth-btn--primary" disabled={!canFinish || busy} onClick={finish}>
              {busy ? "Saving…" : "Finish & start gifting"}
            </button>
          )}
        </div>
        {error && <p className="auth-error">{error}</p>}
        <p className="wizard-hint">
          <Icon name="sparkles" size={14} /> This helps the AI tailor picks to your style.
        </p>
      </div>
    </div>
  );
}

function ConciergeShell() {
  const [booting, setBooting] = useState(true);
  const [supabase, setSupabase] = useState(null);
  const [session, setSession] = useState(null);
  const [profile, setProfile] = useState(null);
  const [isGuest, setIsGuest] = useState(false);
  const [showGate, setShowGate] = useState(false);
  const [showLanding, setShowLanding] = useState(false);
  const [gateMode, setGateMode] = useState("welcome");

  // The landing page (in an iframe) posts this when "Try Hari" is clicked.
  useEffect(() => {
    const onMsg = (e) => {
      if (e.data === "hari:enter") { setShowLanding(false); setShowGate(true); }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const loadProfileForSession = async (client, sess) => {
    const p = await window.KaprukaSupabase.ensureProfile(client, sess);
    setProfile(p);
    return p;
  };

  useEffect(() => {
    let sub;
    let alive = true;
    (async () => {
      try {
        const client = await window.KaprukaSupabase.getSupabaseClient();
        if (!alive) return;
        setSupabase(client);
        const guestFlag = localStorage.getItem("kapruka_guest") === "1";

        if (client) {
          const { data: { session: sess } } = await client.auth.getSession();
          if (!alive) return;
          setSession(sess);
          if (sess) {
            localStorage.removeItem("kapruka_guest");
            setIsGuest(false);
            await loadProfileForSession(client, sess);
          } else if (!guestFlag) {
            setShowLanding(true);
          } else {
            setIsGuest(true);
          }

          sub = client.auth.onAuthStateChange(async (_event, sess) => {
            setSession(sess);
            if (sess) {
              localStorage.removeItem("kapruka_guest");
              setIsGuest(false);
              setShowGate(false);
              await loadProfileForSession(client, sess);
            } else {
              setProfile(null);
            }
          });
        } else if (!guestFlag) {
          setShowLanding(true);
        } else {
          setIsGuest(true);
        }
      } catch (err) {
        console.warn("Boot failed", err);
        if (!localStorage.getItem("kapruka_guest")) setShowLanding(true);
        else setIsGuest(true);
      } finally {
        if (alive) setBooting(false);
      }
    })();

    return () => {
      alive = false;
      if (sub?.subscription) sub.subscription.unsubscribe();
    };
  }, []);

  const continueAsGuest = () => {
    localStorage.setItem("kapruka_guest", "1");
    setIsGuest(true);
    setShowGate(false);
  };

  const handleAuthed = async (sess) => {
    setSession(sess);
    setIsGuest(false);
    localStorage.removeItem("kapruka_guest");
    setShowGate(false);
    if (supabase && sess) await loadProfileForSession(supabase, sess);
  };

  const handleSignOut = async () => {
    if (supabase) await supabase.auth.signOut();
    setSession(null);
    setProfile(null);
    setIsGuest(false);
    localStorage.removeItem("kapruka_guest");
    setShowGate(true);
    setGateMode("welcome");
  };

  const requestSignIn = () => {
    setGateMode("login");
    setShowGate(true);
  };

  if (booting) {
    return <div className="boot-screen">Loading Hari…</div>;
  }

  if (showLanding) {
    return (
      <iframe
        title="Hari — welcome"
        src="/landing.html"
        style={{ position: "fixed", inset: 0, width: "100%", height: "100%", border: "none" }}
      />
    );
  }

  if (showGate) {
    return (
      <AuthGate
        supabase={supabase}
        initialMode={gateMode}
        onGuest={continueAsGuest}
        onAuthed={handleAuthed}
      />
    );
  }

  if (session && profile && !profile.onboarding_completed) {
    return (
      <OnboardingWizard
        supabase={supabase}
        session={session}
        onComplete={(updated) => setProfile(updated)}
      />
    );
  }

  return (
    <App
      session={session}
      profile={profile}
      isGuest={isGuest}
      supabase={supabase}
      onProfileUpdate={setProfile}
      onSignOut={handleSignOut}
      onRequestSignIn={requestSignIn}
      accessToken={session?.access_token}
    />
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<ConciergeShell />);
