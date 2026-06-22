/* Kapruka Gift Concierge — design-system UI wired to Hari backend APIs */
const { useState, useRef, useEffect, useCallback } = React;
const {
  Button, IconButton, Badge, Bubble, SuggestionCard, ProductCard, Toast, Loader, Icon, ThemeToggle,
} = window.KaprukaDesignSystem_d6db4e;

const CHIPS = [
  { icon: "cake", tone: "blush", tx: "Birthday gift for mom", prompt: "Birthday gift for mom under Rs 5000" },
  { icon: "flower", tone: "mint", tx: "Anniversary flowers", prompt: "Anniversary flowers delivered to Colombo" },
  { icon: "heart-pulse", tone: "lilac", tx: "Get-well hamper", prompt: "Get-well hamper for a friend" },
  { icon: "gift", tone: "butter", tx: "Under Rs 3000", prompt: "A nice gift and a card under Rs 3000" },
];
const GREETING =
  "Hi there! Tell me who you're shopping for and the occasion — a birthday, an anniversary, a get-well basket, anything at all. Tap the mic to talk, or type below and I'll happily add things to your cart.";
const LANG_CODES = { en: "en-US", si: "si-LK", ta: "ta-LK" };
const LANG_NAMES = { en: "English", si: "Sinhala", ta: "Tamil" };
const UI_TEXT = {
  en: { ask: "To pick the best option, could you tell me a bit more?", skip: 'Just reply here, or say "skip".' },
  si: { ask: "හොඳම තේරීම කරන්න, ටිකක් වැඩිදුර කියන්න පුළුවන්ද?", skip: 'මෙතන පිළිතුරු දෙන්න, නැත්නම් "skip" කියන්න.' },
  ta: { ask: "சிறந்ததைத் தேர்ந்தெடுக்க, இன்னும் சற்று சொல்ல முடியுமா?", skip: 'இங்கே பதிலளிக்கவும், அல்லது "skip" எனச் சொல்லவும்.' },
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
    image: p.image || "",
    url: p.url || "",
  };
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
const greetSub = () => {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
};

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
  const [cartOpen, setCartOpen] = useState(false);
  const [checkoutView, setCheckoutView] = useState(false);
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState("Tap the mic to talk, or type below");
  const [toasts, setToasts] = useState([]);
  const [bump, setBump] = useState(false);
  const [currentLang, setCurrentLang] = useState("en");
  const [ttsOn, setTtsOn] = useState(false);
  const [budget, setBudget] = useState(null);
  const [instructions, setInstructions] = useState([]);
  const [lastSuggestions, setLastSuggestions] = useState([]);
  const [awaitingAnswers, setAwaitingAnswers] = useState(false);
  const [checkoutResult, setCheckoutResult] = useState("");
  const [placing, setPlacing] = useState(false);
  const [modal, setModal] = useState(null);
  const [profileHydrated, setProfileHydrated] = useState(false);

  const feedRef = useRef(null);
  const checkoutFormRef = useRef(null);
  const recogRef = useRef(null);
  const currentAudioRef = useRef(null);
  const guestPromptedRef = useRef(false);
  const persistTimerRef = useRef(null);

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
  }, [profile, profileHydrated]);

  useEffect(() => {
    if (!profileHydrated || isGuest) return;
    persistProfile({
      default_budget: budget,
      saved_instructions: instructions,
      default_language: currentLang,
    });
  }, [budget, instructions, currentLang, profileHydrated, isGuest, persistProfile]);

  const started = messages.some((m) => m.role === "user");
  const cartCount = cart.reduce((n, c) => n + c.qty, 0);
  const cartSubtotal = () => cart.reduce((s, c) => s + priceNum(c) * c.qty, 0);
  const uiText = (k) => (UI_TEXT[currentLang] || UI_TEXT.en)[k];

  useEffect(() => {
    const el = feedRef.current;
    if (el && el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight;
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
    if (currentLang !== "en") {
      try {
        const res = await fetch("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: String(text).slice(0, 600), lang: currentLang }),
        });
        if (!res.ok) throw new Error("tts");
        const url = URL.createObjectURL(await res.blob());
        const audio = new Audio(url);
        currentAudioRef.current = audio;
        audio.onended = () => URL.revokeObjectURL(url);
        await audio.play();
        return;
      } catch (_) {}
    }
    const synth = window.speechSynthesis;
    if (!synth) return;
    const u = new SpeechSynthesisUtterance(String(text).slice(0, 600));
    u.lang = LANG_CODES[currentLang] || "en-US";
    synth.speak(u);
  }, [ttsOn, currentLang]);

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

  const send = async (text) => {
    text = (text || "").trim();
    if (!text) return;
    const b = parseBudget(text);
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
    setMessages((m) => [...m, { id: tid, role: "bot", thinking: true }]);
    setStatus("Kapruka is thinking…");

    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextConv,
          allow_questions: !proceed,
          suggestions: lastSuggestions,
          cart: cart.map((c) => ({ name: c.name, qty: c.qty, price: c.rawPrice ?? c.price, currency: c.currency })),
          instructions,
          language: currentLang,
          access_token: accessToken || undefined,
        }),
      });
      const data = await res.json();
      setMessages((m) => m.filter((x) => x.id !== tid));

      if (data.user_en) setLastUserEnglish(data.user_en);

      if (data.ok && data.needs_input) {
        setAwaitingAnswers(true);
        const shown = (data.questions_local && data.questions_local.length) ? data.questions_local : (data.questions || []);
        const qs = shown.map((q) => `• ${q}`).join("\n");
        const full = `${uiText("ask")}\n${qs}\n\n${uiText("skip")}`;
        setMessages((m) => [...m, { id: nid(), role: "bot", text: full }]);
        setConversation((c) => [...c, { role: "assistant", content: "Clarifying questions: " + (data.questions || []).join(" ") }]);
        speak(full);
        setStatus("Tap the mic to talk, or type below");
        return;
      }

      if (data.ok) {
        if (Array.isArray(data.cart_actions) && data.cart_actions.length) await applyCartActions(data.cart_actions);
        const display = data.answer_local || data.answer || "";
        const products = Array.isArray(data.products) ? data.products.map(normProduct) : [];
        if (products.length) setLastSuggestions(data.products);
        const thought = products.length ? `Searched Kapruka · ${products.length} matches` : null;
        if (display || products.length) {
          setMessages((m) => [...m, { id: nid(), role: "bot", text: display, thought, products }]);
        } else {
          setMessages((m) => [...m, { id: nid(), role: "bot", text: "I couldn't find matching products — try giving me more detail." }]);
        }
        if (data.answer) {
          setConversation((c) => [...c, { role: "assistant", content: data.answer }]);
          if (display) speak(display);
        }
      } else {
        const err = data.error || JSON.stringify(data);
        setMessages((m) => [...m, { id: nid(), role: "bot", text: `Something went wrong: ${err}` }]);
      }
    } catch (err) {
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
    for (const c of cart) {
      const pid = c.id || idFromUrl(c.url);
      if (!pid) {
        setCheckoutResult(`Couldn't find a product ID for "${c.name}". Remove it and re-add from suggestions.`);
        return;
      }
      items.push({ product_id: pid, quantity: c.qty });
    }
    const params = {
      cart: items,
      recipient: { name: f.rname.value.trim(), phone: f.rphone.value.trim() },
      delivery: { address: f.address.value.trim(), city: f.city.value.trim(), date: f.date.value, location_type: f.location_type.value },
      sender: { name: f.sname.value.trim() },
      currency: cart[0]?.currency || "LKR",
      response_format: "json",
    };
    if (f.instructions.value.trim()) params.delivery.instructions = f.instructions.value.trim();
    if (f.gift_message.value.trim()) params.gift_message = f.gift_message.value.trim();

    setPlacing(true);
    setCheckoutResult("");
    try {
      const res = await fetch("/api/tool", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "kapruka_create_order", arguments: { params } }),
      });
      const data = await res.json();
      let order = null;
      try { order = JSON.parse(data.output); } catch (_) {}
      if (data.ok && order?.checkout_url) {
        const s = order.summary || {};
        const tot = s.grand_total != null ? fmtMoney(s.grand_total, s.currency) : "";
        setCheckoutResult(
          `Order ready!${order.order_ref ? ` Ref: ${order.order_ref}.` : ""}${tot ? ` Total: ${tot}.` : ""} Open the pay link to complete checkout.`
        );
        toast("Order created — open the pay link", "check");
      } else {
        setCheckoutResult((data.output && String(data.output)) || data.error || "Order could not be created.");
      }
    } catch (err) {
      setCheckoutResult(String(err));
    } finally {
      setPlacing(false);
    }
  };

  const openCheckout = () => {
    if (!cart.length) { toast("Your cart is empty", "shopping-cart"); return; }
    setCheckoutView(true);
    setCheckoutResult("");
    const today = new Date();
    today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
    if (checkoutFormRef.current?.date) checkoutFormRef.current.date.min = today.toISOString().slice(0, 10);
  };

  const total = cartSubtotal();
  const cur = cart[0]?.currency || "LKR";
  const overBudget = budget != null && total > budget;

  return (
    <React.Fragment>
      <header className="topbar">
        <div className="inner">
          <div className="greet">
            <span className="brandmark"><Icon name="leaf" size={20} /></span>
            <div className="greet-text">
              <span className="greet-sub">{greetSub()}</span>
              <span className="greet-title">What can I find for you?</span>
            </div>
          </div>
          <div className="top-actions">
            <button
              type="button"
              className={"account-chip" + (isGuest ? " guest" : "")}
              title={isGuest ? "Sign in for personalized picks" : displayName}
              onClick={() => { if (isGuest) onRequestSignIn(); }}
            >
              <Icon name={isGuest ? "heart" : "sparkles"} size={14} />
              <span className="name">{isGuest ? "Sign in" : displayName}</span>
            </button>
            {!isGuest && session && (
              <IconButton icon="x" title="Sign out" onClick={onSignOut} />
            )}
            <select
              className="langsel"
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
            <ThemeToggle />
            <IconButton
              icon={ttsOn ? "volume-2" : "volume-x"}
              title="Read replies aloud"
              active={ttsOn}
              onClick={() => {
                setTtsOn((v) => !v);
                if (!ttsOn) toast("Voice replies on", "volume-2");
                else stopSpeaking();
              }}
            />
            <IconButton
              icon="shopping-cart"
              title="View cart"
              badge={cartCount}
              onClick={() => { setCartOpen(true); setCheckoutView(false); }}
              style={bump ? { transform: "scale(1.08)" } : undefined}
            />
          </div>
        </div>
      </header>

      <main>
        <div className="thread">
          <div className="feed" ref={feedRef}>
            {messages.map((m) => (
              <div className="msg-in" key={m.id} style={{ display: "flex", flexDirection: "column" }}>
                {m.thought && m.role === "bot" && (
                  <div className="bot-thought">
                    <Icon name="sparkles" size={14} />
                    <span>{m.thought}</span>
                  </div>
                )}
                <Bubble role={m.role} thinking={m.thinking}>
                  {m.text}
                </Bubble>
                {m.products && (
                  <div className="grid" style={{ marginLeft: "2.4rem" }}>
                    {m.products.map((p, i) => (
                      <div className="k-rise" key={p.name + i} style={{ animationDelay: `${Math.min(i, 8) * 70}ms` }}>
                        <ProductCard
                          {...p}
                          favorite={!!fav[p.name]}
                          onFavorite={() => {
                            setFav((f) => ({ ...f, [p.name]: !f[p.name] }));
                            if (!fav[p.name]) toast("Saved to wishlist", "heart");
                          }}
                          onAdd={() => addItems([p])}
                        />
                      </div>
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
              placeholder="Tell Kapruka what you're looking for…"
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

      <div className={"scrim" + (cartOpen ? " open" : "")} onClick={() => setCartOpen(false)} />
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
                <div className="citem" key={c.name}>
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
              ))}
            </React.Fragment>
          ) : (
            <form ref={checkoutFormRef} className="formgrid" onSubmit={placeOrder}>
              <div className="full"><label>Recipient name *</label><input name="rname" required placeholder="Who receives the gift" /></div>
              <div className="full"><label>Recipient phone *</label><input name="rphone" required placeholder="07X XXX XXXX" /></div>
              <div className="full"><label>Delivery address *</label><input name="address" required /></div>
              <div><label>City *</label><input name="city" required placeholder="Negombo" defaultValue={defaultCity} /></div>
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
                <Button variant="soft" size="sm" icon="trash-2" onClick={() => { if (cart.length) { setCart([]); toast("Cart cleared", "trash-2"); } }}>Clear</Button>
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

function ConciergeShell() {
  const [booting, setBooting] = useState(true);
  const [supabase, setSupabase] = useState(null);
  const [session, setSession] = useState(null);
  const [profile, setProfile] = useState(null);
  const [isGuest, setIsGuest] = useState(false);
  const [showGate, setShowGate] = useState(false);
  const [gateMode, setGateMode] = useState("welcome");

  const loadProfileForSession = async (client, sess) => {
    const p = await window.KaprukaSupabase.ensureProfile(client, sess);
    setProfile(p);
    return p;
  };

  useEffect(() => {
    let sub;
    (async () => {
      const client = await window.KaprukaSupabase.getSupabaseClient();
      setSupabase(client);
      const guestFlag = localStorage.getItem("kapruka_guest") === "1";

      if (client) {
        const { data: { session: sess } } = await client.auth.getSession();
        setSession(sess);
        if (sess) {
          localStorage.removeItem("kapruka_guest");
          setIsGuest(false);
          await loadProfileForSession(client, sess);
        } else if (!guestFlag) {
          setShowGate(true);
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
        setShowGate(true);
      } else {
        setIsGuest(true);
      }
      setBooting(false);
    })();

    return () => {
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
    return <div className="boot-screen">Loading Kapruka…</div>;
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
        onComplete={(updated) => {
          setProfile(updated);
        }}
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
