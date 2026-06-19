import { useEffect, useRef, useState } from "react";
import LiquidGlass from "liquid-glass-react";
import Background from "./components/Background";
import Orb, { type OrbMode } from "./components/Orb";
import { search, loadOrderTool, invokeTool, ttsBlob, type Checkout } from "./lib/api";
import { LANG_CODES, LANG_NAMES, parseBudget, uiText } from "./lib/i18n";
import type { CartItem, ChatTurn, Lang, Message, Product } from "./lib/types";
import { cartKey, cartSubtotal, fmtMoney, idFromUrl, nextId, priceNum, priceText } from "./lib/util";

const CHIPS = [
  { ic: "🎂", tx: "Birthday gift for mom", prompt: "Birthday gift for mom under Rs 5000" },
  { ic: "💐", tx: "Anniversary flowers", prompt: "Anniversary flowers delivered to Colombo" },
  { ic: "🧺", tx: "Get-well hamper", prompt: "Get-well hamper for a friend" },
  { ic: "🍫", tx: "Chocolates under Rs 3000", prompt: "Chocolates and a card under Rs 3000" },
];

const GREETING =
  "Hi there! 🎁 Tell me who you're shopping for and the occasion — a birthday, an anniversary, a get-well basket, condolence flowers, anything at all. Tap the blue button to talk to me, and I'll happily add things to your cart.";

function timeGreeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function thoughtLine(secs: number, productCount: number): string {
  const r = Math.round(secs);
  const s = secs < 1 ? "less than a second" : r + (r === 1 ? " second" : " seconds");
  return productCount > 0
    ? `Searched Kapruka · ${productCount} ${productCount === 1 ? "match" : "matches"} · ${s}`
    : `Thought for ${s}`;
}

function Lines({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => (
        <span key={i}>
          {i > 0 && <br />}
          {line}
        </span>
      ))}
    </>
  );
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([{ id: nextId(), role: "bot", text: GREETING }]);
  const [query, setQuery] = useState("");
  const [lang, setLang] = useState<Lang>("en");
  const [cart, setCart] = useState<CartItem[]>([]);
  const [instructions, setInstructions] = useState<string[]>([]);
  const [instrInput, setInstrInput] = useState("");
  const [budget, setBudget] = useState<number | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [checkoutMode, setCheckoutMode] = useState<"cart" | "form">("cart");
  const [orbMode, setOrbMode] = useState<OrbMode>("idle");
  const [status, setStatus] = useState("Tap the button to talk, or type below");
  const [ttsOn, setTtsOn] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; msg: string; icon: string }[]>([]);
  const [bumpBadge, setBumpBadge] = useState(false);
  const [coResult, setCoResult] = useState<{ kind: "ok" | "bad"; order?: Checkout; msg?: string } | null>(null);
  const [placing, setPlacing] = useState(false);
  const [modal, setModal] = useState({ open: false, title: "", msg: "", ok: "OK", cancel: "Cancel" });

  const started = messages.some((m) => m.role === "user");

  const convRef = useRef<ChatTurn[]>([]);
  const awaitingRef = useRef(false);
  const lastSuggRef = useRef<Product[]>([]);
  const cartRef = useRef(cart); cartRef.current = cart;
  const instrRef = useRef(instructions); instrRef.current = instructions;
  const langRef = useRef(lang); langRef.current = lang;
  const budgetRef = useRef(budget); budgetRef.current = budget;
  const ttsRef = useRef(ttsOn); ttsRef.current = ttsOn;
  const queryRef = useRef(query); queryRef.current = query;
  const modalResolve = useRef<((v: boolean) => void) | null>(null);
  const recogRef = useRef<any>(null);
  const listeningRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const greetSub = timeGreeting();

  const showToast = (msg: string, icon = "✓") => {
    const id = nextId();
    setToasts((t) => [...t, { id, msg, icon }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  };

  const idleStatus = () => {
    if (!listeningRef.current) { setOrbMode("idle"); setStatus("Tap the button to talk, or type below"); }
  };

  const confirmModal = (title: string, msg: string, ok: string, cancel: string) =>
    new Promise<boolean>((resolve) => { modalResolve.current = resolve; setModal({ open: true, title, msg, ok, cancel }); });
  const closeModal = (v: boolean) => { setModal((m) => ({ ...m, open: false })); modalResolve.current?.(v); modalResolve.current = null; };

  const subtotal = cartSubtotal(cart);
  const cartCount = cart.reduce((n, c) => n + c.qty, 0);
  const over = budget != null && subtotal > budget;
  const bump = () => { setBumpBadge(true); setTimeout(() => setBumpBadge(false), 240); };

  const addToCartRaw = (list: CartItem[], product: Product, qty: number) => {
    if (!product || !product.name) return;
    qty = Math.max(1, qty || 1);
    const key = cartKey(product);
    const existing = list.find((c) => cartKey(c) === key);
    if (existing) existing.qty += qty;
    else list.push({ id: product.id ?? idFromUrl(product.url), name: product.name, price: product.price, currency: product.currency || "LKR", url: product.url, image: product.image, qty });
  };

  const addItems = async (products: Product[], quiet = false) => {
    products = (products || []).filter((p) => p && p.name);
    if (!products.length) return;
    const addition = products.reduce((s, p) => s + priceNum(p) * (p.qty || 1), 0);
    const sub = cartSubtotal(cartRef.current);
    const b = budgetRef.current;
    if (b != null && sub + addition > b) {
      const label = products.length === 1 ? `“${products[0].name}”` : `these ${products.length} items`;
      const ok = await confirmModal(
        "Over your budget",
        `Adding ${label} brings your total to ${fmtMoney(sub + addition)} — ${fmtMoney(sub + addition - b)} over your ${fmtMoney(b)} budget. Add anyway, or keep within budget?`,
        "Add anyway", "Keep within budget",
      );
      if (!ok) { showToast("Skipped to stay within budget", "💸"); return; }
    }
    setCart((prev) => {
      const next = prev.map((c) => ({ ...c }));
      products.forEach((p) => addToCartRaw(next, p, p.qty || 1));
      return next;
    });
    bump();
    if (!quiet) showToast(products.length === 1 ? `Added “${products[0].name}”` : `Added ${products.length} items`, "🛒");
  };

  const applyCartActions = async (actions: any[]) => {
    if (!Array.isArray(actions)) return;
    for (const a of actions) {
      if (!a) continue;
      if (a.action === "add" && Array.isArray(a.products)) await addItems(a.products, true);
      else if (a.action === "clear") { setCart([]); showToast("Cart cleared", "🗑️"); }
      else if (a.action === "remove" && Array.isArray(a.items)) {
        setCart((prev) => {
          const next = prev.map((c) => ({ ...c }));
          (a.items as number[]).map((n) => n - 1).sort((x, y) => y - x).forEach((idx) => { if (idx >= 0 && idx < next.length) next.splice(idx, 1); });
          return next;
        });
        showToast("Removed from cart", "🛒");
      } else if (a.action === "instruction" && a.text) { setInstructions((p) => [...p, a.text]); showToast("Note saved", "📝"); }
    }
    bump();
  };

  const stopSpeaking = () => {
    try { window.speechSynthesis?.cancel(); } catch {}
    if (audioRef.current) { try { audioRef.current.pause(); } catch {} audioRef.current = null; }
  };
  const speakLocal = (text: string) => {
    const synth = window.speechSynthesis;
    if (!synth) return;
    try {
      synth.cancel();
      const u = new SpeechSynthesisUtterance(String(text).slice(0, 600));
      u.lang = LANG_CODES[langRef.current] || "en-US";
      u.onstart = () => { if (!listeningRef.current) { setOrbMode("active"); setStatus("Speaking…"); } };
      u.onend = idleStatus;
      synth.speak(u);
    } catch {}
  };
  const speakRemote = async (text: string) => {
    try {
      const blob = await ttsBlob(text, langRef.current);
      const url = URL.createObjectURL(blob);
      stopSpeaking();
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onplay = () => { if (!listeningRef.current) { setOrbMode("active"); setStatus("Speaking…"); } };
      const done = () => { URL.revokeObjectURL(url); if (audioRef.current === audio) audioRef.current = null; idleStatus(); };
      audio.onended = done; audio.onerror = done;
      await audio.play();
    } catch { speakLocal(text); }
  };
  const speak = (text: string) => {
    if (!ttsRef.current || !text) return;
    if (langRef.current !== "en") void speakRemote(text);
    else speakLocal(text);
  };

  const setLastUserEnglish = (en: string) => {
    for (let i = convRef.current.length - 1; i >= 0; i--) {
      if (convRef.current[i].role === "user") { convRef.current[i].content = en; break; }
    }
  };

  const callSearch = async (allowQuestions: boolean) => {
    setOrbMode("active"); setStatus("Thinking…");
    const tid = nextId();
    setMessages((m) => [...m, { id: tid, role: "bot", thinking: true }]);
    const t0 = performance.now();
    try {
      const data = await search({
        messages: convRef.current,
        allow_questions: allowQuestions,
        suggestions: lastSuggRef.current,
        cart: cartRef.current.map((c) => ({ name: c.name, qty: c.qty, price: c.price, currency: c.currency })),
        instructions: instrRef.current,
        language: langRef.current,
      });
      const secs = (performance.now() - t0) / 1000;
      setMessages((m) => m.filter((x) => x.id !== tid));
      if (data.user_en) setLastUserEnglish(data.user_en);

      if (data.ok && data.needs_input) {
        awaitingRef.current = true;
        const shown = (data.questions_local && data.questions_local.length ? data.questions_local : data.questions) || [];
        const text = `${uiText(langRef.current, "ask")}\n${shown.map((q) => "• " + q).join("\n")}\n\n${uiText(langRef.current, "skip")}`;
        setMessages((m) => [...m, { id: nextId(), role: "bot", text }]);
        convRef.current.push({ role: "assistant", content: "Clarifying questions: " + (data.questions || []).join(" ") });
        speak(text); idleStatus();
        return;
      }
      if (data.ok) {
        if (Array.isArray(data.cart_actions) && data.cart_actions.length) await applyCartActions(data.cart_actions);
        const display = data.answer_local || data.answer || "";
        const productCount = Array.isArray(data.products) ? data.products.length : 0;
        if (productCount) lastSuggRef.current = data.products!;
        const empty = !display && !productCount && !(data.cart_actions || []).length ? "I couldn't find matching products — try giving me more detail." : "";
        const thought = productCount > 0 || secs >= 2 ? thoughtLine(secs, productCount) : undefined;
        setMessages((m) => [...m, { id: nextId(), role: "bot", text: display || empty, thought, products: data.products }]);
        if (data.answer) { convRef.current.push({ role: "assistant", content: data.answer }); speak(display); }
        idleStatus();
      } else {
        setMessages((m) => [...m, { id: nextId(), role: "bot", error: true, text: "⚠️ Something went wrong: " + (data.error || JSON.stringify(data)) }]);
        idleStatus();
      }
    } catch (err) {
      setMessages((m) => m.filter((x) => x.id !== tid).concat({ id: nextId(), role: "bot", error: true, text: "❌ Request failed: " + String(err) }));
      idleStatus();
    }
  };

  const sendText = (text: string) => {
    text = (text || "").trim();
    if (!text) return;
    const b = parseBudget(text);
    if (b) { setBudget(b); budgetRef.current = b; }
    setQuery(""); queryRef.current = "";
    setMessages((m) => [...m, { id: nextId(), role: "user", text }]);
    convRef.current.push({ role: "user", content: text });
    const proceed = awaitingRef.current; awaitingRef.current = false;
    void callSearch(!proceed);
  };

  useEffect(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    const recog = new SR();
    recog.lang = LANG_CODES[langRef.current] || "en-US";
    recog.interimResults = true;
    recog.continuous = false;
    let finalText = "";
    recog.onstart = () => { listeningRef.current = true; finalText = ""; setOrbMode("listening"); setStatus("Listening… speak now"); };
    recog.onerror = (ev: any) => setStatus("Mic error: " + ev.error);
    recog.onresult = (ev: any) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const tr = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += tr; else interim += tr;
      }
      const val = (finalText + interim).trim();
      setQuery(val); queryRef.current = val;
    };
    recog.onend = () => {
      listeningRef.current = false; setOrbMode("idle");
      const said = queryRef.current.trim();
      if (said) sendText(said); else idleStatus();
    };
    recogRef.current = recog;
  }, []);

  useEffect(() => { if (recogRef.current) try { recogRef.current.lang = LANG_CODES[lang] || "en-US"; } catch {} }, [lang]);

  const orbActivate = () => {
    if (listeningRef.current) { recogRef.current?.stop(); return; }
    const typed = queryRef.current.trim();
    if (typed) { sendText(typed); return; }
    if (!recogRef.current) { showToast("Voice input isn't supported in this browser — please type instead.", "🎤"); return; }
    stopSpeaking();
    try { recogRef.current.start(); } catch {}
  };

  const openCheckout = () => {
    if (!cart.length) { showToast("Your cart is empty", "🛒"); return; }
    setCoResult(null); setCheckoutMode("form");
  };

  const placeOrder = async (form: HTMLFormElement) => {
    if (!cart.length) { showToast("Your cart is empty", "🛒"); return; }
    const f = form as any;
    const items: { product_id: string; quantity: number }[] = [];
    for (const c of cart) {
      const pid = c.id || idFromUrl(c.url);
      if (!pid) { setCoResult({ kind: "bad", msg: `Couldn't find a product ID for “${c.name}”. Please remove it and re-add it from the suggestions.` }); return; }
      items.push({ product_id: pid, quantity: c.qty });
    }
    const params: any = {
      cart: items,
      recipient: { name: f.rname.value.trim(), phone: f.rphone.value.trim() },
      delivery: { address: f.address.value.trim(), city: f.city.value.trim(), date: f.date.value, location_type: f.location_type.value },
      sender: { name: f.sname.value.trim() },
      currency: (cart[0] && cart[0].currency) || "LKR",
      response_format: "json",
    };
    if (f.instructions.value.trim()) params.delivery.instructions = f.instructions.value.trim();
    if (f.gift_message.value.trim()) params.gift_message = f.gift_message.value.trim();

    setPlacing(true); setCoResult(null);
    try {
      const tool = await loadOrderTool();
      const name = tool?.name || "kapruka_create_order";
      const props = tool?.inputSchema?.properties || null;
      const args = props ? (props.params ? { params } : params) : { params };
      const data = await invokeTool(name, args);
      let order: Checkout | null = data.checkout && data.checkout.checkout_url ? data.checkout : null;
      if (!order && data.output) { try { order = JSON.parse(data.output); } catch {} }
      if (data.ok && order && order.checkout_url) {
        setCoResult({ kind: "ok", order });
        showToast("Order created — open the pay link", "✅");
      } else {
        const msg = (data.output && String(data.output)) || (data.error && String(data.error)) || "Order could not be created.";
        setCoResult({ kind: "bad", msg: msg.slice(0, 400) });
      }
    } catch (err) {
      setCoResult({ kind: "bad", msg: String(err) });
    } finally {
      setPlacing(false);
    }
  };

  const openCart = () => { setCheckoutMode("cart"); setCartOpen(true); };

  return (
    <>
      <Background />

      <header className="topbar">
        <div className="inner">
          <div className="greet">
            <span className="avatar">🌴</span>
            <div className="greet-text">
              <span className="greet-sub">{greetSub}</span>
              <span className="greet-title">What can I find for you?</span>
            </div>
          </div>
          <div className="top-actions">
            <select className="langsel" value={lang} onChange={(e) => { const l = e.target.value as Lang; setLang(l); showToast("Language: " + LANG_NAMES[l], "🌐"); }} title="Language" aria-label="Language">
              <option value="en">English</option>
              <option value="si">සිංහල</option>
              <option value="ta">தமிழ்</option>
            </select>
            <button type="button" className={"iconbtn" + (ttsOn ? " on" : "")} title="Read replies aloud" aria-label="Toggle voice replies"
              onClick={() => { const v = !ttsOn; setTtsOn(v); ttsRef.current = v; showToast(v ? "Voice replies on" : "Voice replies off", "🔊"); if (!v) stopSpeaking(); }}>
              {ttsOn ? "🔊" : "🔈"}
            </button>
            <button type="button" className="iconbtn cartbtn" title="View cart" aria-label="View cart" onClick={openCart}>
              🛒{cartCount > 0 && <span className={"badge" + (bumpBadge ? " bump" : "")}>{cartCount}</span>}
            </button>
          </div>
        </div>
      </header>

      <main>
        <div className="thread">
          <div className="feed">
            {messages.map((m) => (
              <div key={m.id} className={"msg " + m.role + (m.thinking ? " thinking" : "")}>
                <div className="bubble">
                  {m.thinking ? (
                    <span className="thinking-line"><span className="think-label">Thinking…</span></span>
                  ) : (
                    <>
                      {m.thought && <div className="thought"><span className="spark">✦</span>{m.thought}</div>}
                      {m.text && <Lines text={m.text} />}
                      {m.products && m.products.length > 0 && (
                        <ProductGrid products={m.products} onAdd={(p) => addItems([p])} onAddAll={(ps) => addItems(ps)} />
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </main>

      <footer className="composer">
        <div className="composer-inner">
          <p className="status">{status}</p>
          {!started && (
            <div className="cards">
              {CHIPS.map((c) => (
                <LiquidGlass key={c.prompt} cornerRadius={16} blurAmount={0.4} displacementScale={36} elasticity={0.18} padding="0px" mode="standard" onClick={() => sendText(c.prompt)}>
                  <div className="scard scard-lg">
                    <span className="scard-ic">{c.ic}</span>
                    <span className="scard-tx">{c.tx}</span>
                  </div>
                </LiquidGlass>
              ))}
            </div>
          )}
          <form className="searchbar" onSubmit={(e) => { e.preventDefault(); sendText(queryRef.current); }}>
            <input value={query} onChange={(e) => { setQuery(e.target.value); queryRef.current = e.target.value; }}
              type="text" autoComplete="off" placeholder="Tap here to start with Kapruka…" />
            <button type="button" className="orb" aria-label="Tap to talk, or send your message" onClick={orbActivate}>
              <Orb mode={orbMode} />
            </button>
          </form>
        </div>
      </footer>

      <div className={"scrim" + (cartOpen ? " open" : "")} onClick={() => setCartOpen(false)} />
      <aside className={"drawer" + (cartOpen ? " open" : "")} aria-label="Cart">
        <header>
          <span style={{ fontSize: "1.2rem" }}>🛒</span>
          <h3>Your cart</h3>
          <button type="button" className="iconbtn" style={{ marginLeft: "auto" }} aria-label="Close" onClick={() => setCartOpen(false)}>✕</button>
        </header>
        <div className="body">
          {cart.length === 0 ? (
            <div className="empty"><span className="big">🎁</span>Your cart is empty.<br />Add items from the suggestions, or just ask me to.</div>
          ) : (
            cart.map((c, i) => (
              <div className="citem" key={cartKey(c) + i}>
                {c.image ? <img className="ci-img" src={c.image} alt="" /> : <div className="ci-noimg">🎁</div>}
                <div className="ci-main">
                  <div className="ci-name">{c.url ? <a href={c.url} target="_blank" rel="noopener">{c.name}</a> : c.name}</div>
                  {c.price ? <div className="ci-price">{fmtMoney(priceNum(c) * c.qty, c.currency)}</div> : null}
                </div>
                <div className="qty">
                  <button type="button" onClick={() => setCart((p) => p.map((x, j) => (j === i ? { ...x, qty: x.qty - 1 } : x)).filter((x) => x.qty > 0))}>−</button>
                  <span>{c.qty}</span>
                  <button type="button" onClick={() => setCart((p) => p.map((x, j) => (j === i ? { ...x, qty: x.qty + 1 } : x)))}>+</button>
                </div>
                <button type="button" className="ci-rm" title="Remove" onClick={() => setCart((p) => p.filter((_, j) => j !== i))}>✕</button>
              </div>
            ))
          )}
        </div>
        <div className="foot">
          {checkoutMode === "cart" ? (
            <div>
              <div className="budgetrow">
                <span className="lbl">Budget (LKR)</span>
                <input type="number" min={0} step={100} placeholder="none set" value={budget ?? ""}
                  onChange={(e) => { const v = parseFloat(e.target.value); setBudget(!isNaN(v) && v > 0 ? v : null); }} />
              </div>
              <div className={"totrow" + (over ? " over" : "")}><span className="lbl">Estimated total</span><span className="val">{fmtMoney(subtotal, cart[0]?.currency)}</span></div>
              {over && budget != null && <div className="budgetwarn">⚠ {fmtMoney(subtotal - budget, cart[0]?.currency)} over your {fmtMoney(budget, cart[0]?.currency)} budget.</div>}
              <label>Notes for the store — hampers, gift-wrap, custom requests</label>
              <div className="instrlist">
                {instructions.map((tx, i) => (
                  <div className="instr" key={i}><span>📝 {tx}</span><button type="button" className="ci-rm" title="Remove" onClick={() => setInstructions((p) => p.filter((_, j) => j !== i))}>✕</button></div>
                ))}
              </div>
              <textarea rows={2} placeholder="e.g. gift-wrap everything together as one hamper" value={instrInput} onChange={(e) => setInstrInput(e.target.value)} />
              <div className="row">
                <button type="button" className="btn-soft" onClick={() => { const tx = instrInput.trim(); if (!tx) return; setInstructions((p) => [...p, tx]); setInstrInput(""); showToast("Note saved", "📝"); }}>＋ Add note</button>
                <button type="button" className="btn-soft" onClick={() => { if (!cart.length) return; setCart([]); showToast("Cart cleared", "🗑️"); }}>Clear</button>
              </div>
              <button type="button" className="btn-primary" onClick={openCheckout}>Proceed to checkout →</button>
            </div>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); void placeOrder(e.currentTarget); }}>
              <div className="totrow"><span className="lbl">Order total</span><span className="val">{fmtMoney(subtotal, cart[0]?.currency)}</span></div>
              <div className="formgrid">
                <div className="full"><label>Recipient name *</label><input name="rname" required placeholder="Who receives the gift" /></div>
                <div className="full"><label>Recipient phone *</label><input name="rphone" required placeholder="07X XXX XXXX or +9477…" /></div>
                <div className="full"><label>Delivery address *</label><input name="address" required placeholder="No. 46, Pitipana Veediya" /></div>
                <div><label>City *</label><input name="city" required placeholder="Negombo" /></div>
                <div><label>Delivery date *</label><input name="date" type="date" required defaultValue={new Date().toISOString().slice(0, 10)} min={new Date().toISOString().slice(0, 10)} /></div>
                <div className="full"><label>Address type</label>
                  <select name="location_type"><option value="house">House</option><option value="apartment">Apartment</option><option value="office">Office</option><option value="other">Other</option></select>
                </div>
                <div className="full"><label>Your name (sender) *</label><input name="sname" required placeholder="From…" /></div>
                <div className="full"><label>Gift card message</label><textarea name="gift_message" rows={2} maxLength={300} placeholder="Happy birthday, Mom! 🎂" /></div>
                <div className="full"><label>Delivery instructions</label><textarea name="instructions" rows={2} maxLength={250} placeholder="Leave at the front gate, call on arrival…" defaultValue={instructions.join("; ")} /></div>
              </div>
              {coResult && (coResult.kind === "ok" && coResult.order ? (
                <div className="co-ok"><strong>✅ Order ready!</strong>
                  {coResult.order.order_ref && <div>Ref: {coResult.order.order_ref}</div>}
                  {coResult.order.summary?.grand_total != null && <div>Grand total: <strong>{fmtMoney(coResult.order.summary.grand_total, coResult.order.summary.currency)}</strong></div>}
                  <a className="pay" href={coResult.order.checkout_url} target="_blank" rel="noopener">Open secure checkout &amp; pay →</a>
                </div>
              ) : (
                <div className="co-bad"><strong>Couldn't place the order.</strong><br />{coResult?.msg}</div>
              ))}
              <button type="submit" className="btn-primary" disabled={placing}>{placing ? "Placing order…" : "Place order & get pay link"}</button>
              <button type="button" className="btn-ghost" style={{ width: "100%", marginTop: ".5rem" }} onClick={() => setCheckoutMode("cart")}>← Back to cart</button>
            </form>
          )}
        </div>
      </aside>

      <div className={"modal-scrim" + (modal.open ? " open" : "")} onClick={(e) => { if (e.target === e.currentTarget) closeModal(false); }}>
        <div className="modal" role="alertdialog" aria-modal="true">
          <h4>{modal.title}</h4>
          <p>{modal.msg}</p>
          <div className="actions">
            <button type="button" className="btn-ghost" onClick={() => closeModal(false)}>{modal.cancel}</button>
            <button type="button" className="btn-primary" style={{ marginTop: 0 }} onClick={() => closeModal(true)}>{modal.ok}</button>
          </div>
        </div>
      </div>

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id}><span className="ic">{t.icon}</span><span>{t.msg}</span></div>
        ))}
      </div>
    </>
  );
}

function ProductGrid({ products, onAdd, onAddAll }: { products: Product[]; onAdd: (p: Product) => void; onAddAll: (ps: Product[]) => void }) {
  return (
    <>
      <div className="grid">
        {products.map((p, i) => (
          <div className="card" key={(p.url || p.name || "") + i} style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}>
            {p.image && <div className="imgwrap"><img src={p.image} alt="" loading="lazy" /></div>}
            <div className="cardbody">
              <div className="pname">{p.url ? <a href={p.url} target="_blank" rel="noopener">{p.name}</a> : p.name || "Product"}</div>
              {priceText(p) && <div className="price">{priceText(p)}</div>}
              {p.description && <div className="desc">{p.description}</div>}
              <button type="button" className="btn-soft addbtn" onClick={() => onAdd(p)}>🛒 Add to cart</button>
            </div>
          </div>
        ))}
      </div>
      <div className="gridbar"><button type="button" className="btn-soft" onClick={() => onAddAll(products)}>🛒 Add all to cart</button></div>
    </>
  );
}
