/* @ds-bundle: {"format":3,"namespace":"KaprukaDesignSystem_d6db4e","components":[{"name":"Bubble","sourcePath":"components/chat/Bubble.jsx"},{"name":"SuggestionCard","sourcePath":"components/chat/SuggestionCard.jsx"},{"name":"ProductCard","sourcePath":"components/commerce/ProductCard.jsx"},{"name":"Badge","sourcePath":"components/core/Badge.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Icon","sourcePath":"components/core/Icon.jsx"},{"name":"ICON_NAMES","sourcePath":"components/core/Icon.jsx"},{"name":"IconButton","sourcePath":"components/core/IconButton.jsx"},{"name":"ThemeToggle","sourcePath":"components/core/ThemeToggle.jsx"},{"name":"Loader","sourcePath":"components/feedback/Loader.jsx"},{"name":"Toast","sourcePath":"components/feedback/Toast.jsx"}],"sourceHashes":{"components/chat/Bubble.jsx":"e2c1ac955c67","components/chat/SuggestionCard.jsx":"82b787463c33","components/commerce/ProductCard.jsx":"6dd64507fc65","components/core/Badge.jsx":"b8ef78685dfd","components/core/Button.jsx":"a2162064557a","components/core/Icon.jsx":"56c86ea50fe1","components/core/IconButton.jsx":"3c42ccd8c749","components/core/ThemeToggle.jsx":"31496e537e38","components/feedback/Loader.jsx":"dc7c56f6cbfc","components/feedback/Toast.jsx":"2401f143f44f","ui_kits/concierge/ConciergeApp.jsx":"38749ce137b1"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.KaprukaDesignSystem_d6db4e = window.KaprukaDesignSystem_d6db4e || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/core/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/** Small coral pill count badge (cart counts, notifications). Pops on mount. */
function Badge({
  children,
  className = "",
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("span", _extends({
    className: ["k-badge", className].filter(Boolean).join(" "),
    style: style
  }, rest), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Badge.jsx", error: String((e && e.message) || e) }); }

// components/core/Icon.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Icon — inline Lucide icons (ISC-licensed, MIT-compatible). The product's
 * emoji are replaced by this single, consistent line-icon set. Renders an SVG
 * that inherits `currentColor`, so it tints to whatever text color it sits in.
 *
 * Add more glyphs by pasting a Lucide icon's inner SVG into ICONS.
 */
const ICONS = {
  gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5"/>',
  "shopping-cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
  cake: '<path d="M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8"/><path d="M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1"/><path d="M2 21h20"/><path d="M7 8v3"/><path d="M12 8v3"/><path d="M17 8v3"/><path d="M7 4h.01"/><path d="M12 4h.01"/><path d="M17 4h.01"/>',
  flower: '<circle cx="12" cy="9" r="2.5"/><path d="M12 6.5C12 4.5 13.5 3 15 3s2 1.5 1.2 3"/><path d="M12 6.5C12 4.5 10.5 3 9 3S7 4.5 7.8 6"/><path d="M14.5 9c1.7-.6 3.5 0 4 1.4.5 1.4-.6 2.7-2.3 2.6"/><path d="M9.5 9c-1.7-.6-3.5 0-4 1.4-.5 1.4.6 2.7 2.3 2.6"/><path d="M12 11.5V22"/><path d="M12 22c3.5 0 6-1.4 6-4-3.5 0-6 1.4-6 4Z"/><path d="M12 22c-3.5 0-6-1.4-6-4 3.5 0 6 1.4 6 4Z"/>',
  "heart-pulse": '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/><path d="M3.22 12H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/>',
  heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
  sparkles: '<path d="M9.94 14.06A2 2 0 0 0 8.5 12.6L2.4 11a.5.5 0 0 1 0-.96L8.5 8.5A2 2 0 0 0 9.94 7L11.5.9a.5.5 0 0 1 .96 0L14 7a2 2 0 0 0 1.44 1.44L21.5 10a.5.5 0 0 1 0 .96L15.5 12.6A2 2 0 0 0 14 14.06L12.46 20a.5.5 0 0 1-.96 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
  "volume-2": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  minus: '<path d="M5 12h14"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  leaf: '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6"/>',
  package: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M3.3 7 12 12l8.7-5"/><path d="M12 22V12"/>',
  "message-circle": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  "arrow-right": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  "trash-2": '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
  star: '<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>'
};
function Icon({
  name = "gift",
  size = 20,
  strokeWidth = 2,
  color = "currentColor",
  style = {},
  ...rest
}) {
  const inner = ICONS[name] || ICONS.gift;
  return /*#__PURE__*/React.createElement("svg", _extends({
    xmlns: "http://www.w3.org/2000/svg",
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: color,
    strokeWidth: strokeWidth,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
    style: {
      display: "block",
      flex: "0 0 auto",
      ...style
    },
    dangerouslySetInnerHTML: {
      __html: inner
    }
  }, rest));
}

/** Names available in the inline Lucide set. */
const ICON_NAMES = Object.keys(ICONS);
Object.assign(__ds_scope, { Icon, ICON_NAMES });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Icon.jsx", error: String((e && e.message) || e) }); }

// components/chat/SuggestionCard.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Suggestion chip — starter prompts above the composer. A colored Lucide icon
 * tile over a short label; lifts on hover, presses with a spring. `tone` picks
 * the pastel accent for the icon tile.
 */
const TONES = {
  coral: "var(--c-coral)",
  mint: "var(--c-mint)",
  lilac: "var(--c-lilac)",
  butter: "var(--c-butter)",
  sky: "var(--c-sky)",
  blush: "var(--c-blush)"
};
function SuggestionCard({
  icon = "gift",
  tone = "coral",
  children,
  className = "",
  style = {},
  ...rest
}) {
  const c = TONES[tone] || TONES.coral;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    className: ["k-chip", className].filter(Boolean).join(" "),
    style: style
  }, rest), /*#__PURE__*/React.createElement("span", {
    className: "k-chip__ic",
    style: {
      background: `color-mix(in srgb, ${c} 22%, var(--surface))`,
      color: c
    }
  }, typeof icon === "string" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 19
  }) : icon), /*#__PURE__*/React.createElement("span", {
    style: {
      fontSize: "var(--text-sm)",
      lineHeight: 1.32,
      fontWeight: "var(--weight-medium)"
    }
  }, children));
}
Object.assign(__ds_scope, { SuggestionCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/chat/SuggestionCard.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Kapruka Button — friendly, pillowy, three weights.
 *  - "primary": pastel-coral fill, deep warm ink, soft lift on hover
 *  - "soft": neutral surface secondary
 *  - "ghost": quiet text action
 * Pass `icon`/`iconRight` as a Lucide name (string) or any node. The right
 * icon nudges on hover; the whole button lifts and presses with a spring.
 */
function Button({
  variant = "primary",
  size = "md",
  full = false,
  disabled = false,
  loading = false,
  icon = null,
  iconRight = null,
  children,
  className = "",
  style = {},
  ...rest
}) {
  const cls = ["k-btn", `k-btn--${variant}`, `k-btn--${size}`, full ? "k-btn--full" : "", className].filter(Boolean).join(" ");
  const glyph = val => typeof val === "string" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: val,
    size: size === "sm" ? 16 : 18
  }) : val;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    className: cls,
    disabled: disabled || loading,
    style: style
  }, rest), loading ? /*#__PURE__*/React.createElement("span", {
    className: "k-spinner",
    "aria-hidden": "true"
  }) : icon ? /*#__PURE__*/React.createElement("span", {
    className: "k-btn__ic k-btn__ic--left"
  }, glyph(icon)) : null, children, iconRight && !loading ? /*#__PURE__*/React.createElement("span", {
    className: "k-btn__ic k-btn__ic--right"
  }, glyph(iconRight)) : null);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/commerce/ProductCard.jsx
try { (() => {
/**
 * Gift product card — image with a heart wishlist toggle, name, price in the
 * accent ink, a 2-line blurb, and a soft "Add to cart" action. Lifts on hover
 * while the image zooms.
 */
function ProductCard({
  name = "Product",
  price = "",
  description = "",
  image = "",
  url = "",
  favorite = false,
  onFavorite = null,
  onAdd = () => {},
  style = {}
}) {
  const nameEl = url ? /*#__PURE__*/React.createElement("a", {
    href: url,
    target: "_blank",
    rel: "noopener",
    style: {
      color: "inherit",
      textDecoration: "none"
    }
  }, name) : name;
  return /*#__PURE__*/React.createElement("div", {
    className: "k-product",
    style: style
  }, image ? /*#__PURE__*/React.createElement("div", {
    className: "k-product__media"
  }, /*#__PURE__*/React.createElement("img", {
    className: "k-product__img",
    src: image,
    alt: "",
    loading: "lazy"
  }), onFavorite ? /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "k-product__fav" + (favorite ? " k-product__fav--on" : ""),
    title: favorite ? "Remove from wishlist" : "Save to wishlist",
    "aria-pressed": favorite,
    onClick: onFavorite
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "heart",
    size: 16
  })) : null) : null, /*#__PURE__*/React.createElement("div", {
    style: {
      padding: ".6rem .7rem",
      display: "flex",
      flexDirection: "column",
      gap: ".3rem",
      flex: 1
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontWeight: "var(--weight-semibold)",
      fontSize: "var(--text-sm)",
      lineHeight: 1.3,
      fontFamily: "var(--font-display)"
    }
  }, nameEl), price ? /*#__PURE__*/React.createElement("div", {
    style: {
      color: "var(--ink-accent)",
      fontWeight: "var(--weight-bold)",
      fontSize: "var(--text-sm)",
      fontFamily: "var(--font-display)"
    }
  }, price) : null, description ? /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--text-xs)",
      color: "var(--muted)",
      display: "-webkit-box",
      WebkitLineClamp: 2,
      WebkitBoxOrient: "vertical",
      overflow: "hidden"
    }
  }, description) : null, /*#__PURE__*/React.createElement(__ds_scope.Button, {
    variant: "soft",
    size: "sm",
    icon: "shopping-cart",
    full: true,
    onClick: onAdd,
    style: {
      marginTop: "auto"
    }
  }, "Add to cart")));
}
Object.assign(__ds_scope, { ProductCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/commerce/ProductCard.jsx", error: String((e && e.message) || e) }); }

// components/core/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * Round icon button for top-bar / toolbar actions. `icon` is a Lucide name
 * (string) or node; `active` fills it coral; `badge` overlays a count.
 */
function IconButton({
  icon,
  active = false,
  badge = null,
  size = 42,
  iconSize = 19,
  title,
  className = "",
  style = {},
  children,
  ...rest
}) {
  const cls = ["k-iconbtn", active ? "k-iconbtn--active" : "", className].filter(Boolean).join(" ");
  const glyph = typeof icon === "string" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: iconSize
  }) : icon || children;
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    title: title,
    "aria-label": title,
    "aria-pressed": active || undefined,
    className: cls,
    style: {
      width: size,
      height: size,
      ...style
    }
  }, rest), glyph, badge != null && badge !== 0 ? /*#__PURE__*/React.createElement(__ds_scope.Badge, {
    style: {
      position: "absolute",
      top: -5,
      right: -5
    }
  }, badge) : null);
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/IconButton.jsx", error: String((e && e.message) || e) }); }

// components/core/ThemeToggle.jsx
try { (() => {
/**
 * ThemeToggle — flips the whole system between dark (default) and light by
 * setting `data-theme` on <html>, and remembers the choice in localStorage.
 * Sun in dark mode (tap for light), moon in light mode.
 */
function ThemeToggle({
  size = 42,
  style = {}
}) {
  const get = () => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") || "dark";
  };
  const [theme, setTheme] = React.useState(get);
  React.useEffect(() => {
    try {
      const saved = localStorage.getItem("kapruka-theme");
      if (saved) {
        document.documentElement.setAttribute("data-theme", saved);
        setTheme(saved);
      }
    } catch (e) {}
  }, []);
  const toggle = () => {
    const next = get() === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("kapruka-theme", next);
    } catch (e) {}
    setTheme(next);
  };
  const isLight = theme === "light";
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "k-iconbtn",
    title: isLight ? "Switch to dark" : "Switch to light",
    "aria-label": "Toggle color theme",
    onClick: toggle,
    style: {
      width: size,
      height: size,
      ...style
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: isLight ? "moon" : "sun",
    size: 19
  }));
}
Object.assign(__ds_scope, { ThemeToggle });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/ThemeToggle.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Loader.jsx
try { (() => {
/**
 * Loader — the system's loading + "AI is working" animations.
 *  - "dots":     three bouncing coral dots (typing / sending)
 *  - "spinner":  a small spinning ring (button / inline waits)
 *  - "thinking": sparkle + shimmering gradient label (assistant is reasoning)
 */
function Loader({
  variant = "dots",
  label = "Thinking",
  style = {}
}) {
  if (variant === "spinner") {
    return /*#__PURE__*/React.createElement("span", {
      className: "k-spinner",
      role: "status",
      "aria-label": label,
      style: style
    });
  }
  if (variant === "thinking") {
    return /*#__PURE__*/React.createElement("span", {
      className: "k-think",
      role: "status",
      style: style
    }, /*#__PURE__*/React.createElement("span", {
      className: "k-think__spark"
    }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
      name: "sparkles",
      size: 16
    })), /*#__PURE__*/React.createElement("span", {
      className: "k-think__lbl"
    }, label, "\u2026"));
  }
  return /*#__PURE__*/React.createElement("span", {
    className: "k-dots",
    role: "status",
    "aria-label": label,
    style: style
  }, /*#__PURE__*/React.createElement("i", null), /*#__PURE__*/React.createElement("i", null), /*#__PURE__*/React.createElement("i", null));
}
Object.assign(__ds_scope, { Loader });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Loader.jsx", error: String((e && e.message) || e) }); }

// components/chat/Bubble.jsx
try { (() => {
/**
 * Chat message. Bot replies render as plain text beside a soft gradient
 * avatar (a leaf — the Kapruka mark); user messages are a right-aligned
 * pill bubble. Set `thinking` for the AI loading state, or `thought` for the
 * "Searched Kapruka · N matches" meta line. `thinking` floats + glows the
 * avatar via the .k-avatar--thinking animation.
 */
function Bubble({
  role = "bot",
  thought = null,
  thinking = false,
  children,
  style = {}
}) {
  const isBot = role === "bot";
  if (!isBot) {
    return /*#__PURE__*/React.createElement("div", {
      style: {
        alignSelf: "flex-end",
        maxWidth: "82%",
        ...style
      }
    }, /*#__PURE__*/React.createElement("div", {
      style: {
        padding: ".65rem 1rem",
        borderRadius: "20px",
        borderBottomRightRadius: "7px",
        background: "var(--primary-soft)",
        color: "var(--on-primary)",
        fontSize: "var(--text-base)",
        lineHeight: "var(--leading-chat)",
        whiteSpace: "pre-wrap",
        wordWrap: "break-word",
        boxShadow: "var(--shadow-sm)"
      }
    }, children));
  }
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: ".7rem",
      alignItems: "flex-start",
      maxWidth: "100%",
      ...style
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "k-avatar k-avatar--ai" + (thinking ? " k-avatar--thinking" : "")
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "leaf",
    size: 17
  })), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      color: "var(--text)",
      fontSize: "var(--text-base)",
      lineHeight: "var(--leading-chat)",
      whiteSpace: "pre-wrap",
      wordWrap: "break-word",
      paddingTop: ".25rem"
    }
  }, thought ? /*#__PURE__*/React.createElement("div", {
    style: {
      display: "inline-flex",
      alignItems: "center",
      gap: ".4rem",
      color: "var(--faint)",
      fontSize: "var(--text-sm)",
      marginBottom: ".5rem"
    }
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      color: "var(--primary)",
      display: "inline-flex"
    }
  }, /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: "sparkles",
    size: 14
  })), thought) : null, thinking ? /*#__PURE__*/React.createElement(__ds_scope.Loader, {
    variant: "thinking",
    label: "Thinking"
  }) : children));
}
Object.assign(__ds_scope, { Bubble });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/chat/Bubble.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Toast.jsx
try { (() => {
/** Transient confirmation toast — icon chip + message. Springs in on mount. */
function Toast({
  icon = "check",
  children,
  className = "",
  style = {}
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: ["k-toast", className].filter(Boolean).join(" "),
    style: style
  }, /*#__PURE__*/React.createElement("span", {
    className: "k-toast__ic"
  }, typeof icon === "string" ? /*#__PURE__*/React.createElement(__ds_scope.Icon, {
    name: icon,
    size: 16
  }) : icon), /*#__PURE__*/React.createElement("span", null, children));
}
Object.assign(__ds_scope, { Toast });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Toast.jsx", error: String((e && e.message) || e) }); }

// ui_kits/concierge/ConciergeApp.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/* Concierge UI kit  (v2) — friendly soft pastel recreation of the Kapruka
   gift assistant. Composes the design-system primitives over kit.css.
   Lucide icons (no emoji), light/dark theme toggle, and lots of smooth motion:
   staggered card entrances, AI "thinking" loaders, button & badge animations,
   a breathing mic button. Fake data only. */
const {
  useState,
  useRef,
  useEffect
} = React;
const {
  Button,
  IconButton,
  Badge,
  Bubble,
  SuggestionCard,
  ProductCard,
  Toast,
  Loader,
  Icon,
  ThemeToggle
} = window.KaprukaDesignSystem_d6db4e;

/* ---- Canned concierge knowledge (no backend) ---- */
const CHIPS = [{
  icon: "cake",
  tone: "blush",
  tx: "Birthday gift for mom",
  prompt: "Birthday gift for mom under Rs 5000"
}, {
  icon: "flower",
  tone: "mint",
  tx: "Anniversary flowers",
  prompt: "Anniversary flowers delivered to Colombo"
}, {
  icon: "heart-pulse",
  tone: "lilac",
  tx: "Get-well hamper",
  prompt: "Get-well hamper for a friend"
}, {
  icon: "gift",
  tone: "butter",
  tx: "Under Rs 3000",
  prompt: "A nice gift and a card under Rs 3000"
}];
const CATALOG = [{
  name: "Birthday Chocolate Hamper",
  price: "Rs 4,500",
  description: "Assorted pralines & truffles in a keepsake box.",
  image: "https://images.unsplash.com/photo-1549007994-cb92caebd54b?w=400&q=70"
}, {
  name: "Pastel Rose Bouquet",
  price: "Rs 3,900",
  description: "A dozen soft-pink roses, hand-tied with ribbon.",
  image: "https://images.unsplash.com/photo-1561181286-d3fee7d55364?w=400&q=70"
}, {
  name: "Spa Pamper Basket",
  price: "Rs 4,800",
  description: "Candles, bath salts & botanical soaps to unwind.",
  image: "https://images.unsplash.com/photo-1570172619644-dfd03ed5d881?w=400&q=70"
}];
const REPLY = "Lovely choice! Here are a few thoughtful options I'd recommend — tap “Add to cart”, or just tell me to add one and I'll handle it.";
const GREETING = "Hi there! Tell me who you're shopping for and the occasion — a birthday, an anniversary, a get-well basket, anything at all. Tap the mic to talk, or type below and I'll happily add things to your cart.";
let _id = 1;
const nid = () => _id++;
const rupees = cart => cart.reduce((s, c) => s + parseInt(c.price.replace(/[^\d]/g, ""), 10) * c.qty, 0);
function App() {
  const [messages, setMessages] = useState([{
    id: nid(),
    role: "bot",
    text: GREETING
  }]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState([]);
  const [fav, setFav] = useState({});
  const [cartOpen, setCartOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [status, setStatus] = useState("Tap the mic to talk, or type below");
  const [toasts, setToasts] = useState([]);
  const [bump, setBump] = useState(false);
  const feedRef = useRef(null);
  const started = messages.some(m => m.role === "user");
  const cartCount = cart.reduce((n, c) => n + c.qty, 0);
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.parentElement.scrollTop = el.parentElement.scrollHeight;
  }, [messages]);
  const toast = (msg, icon = "check") => {
    const id = nid();
    setToasts(t => [...t, {
      id,
      msg,
      icon
    }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 2400);
  };
  const addToCart = p => {
    setCart(prev => {
      const ex = prev.find(c => c.name === p.name);
      if (ex) return prev.map(c => c.name === p.name ? {
        ...c,
        qty: c.qty + 1
      } : c);
      return [...prev, {
        ...p,
        qty: 1
      }];
    });
    setBump(true);
    setTimeout(() => setBump(false), 260);
    toast(`Added “${p.name}”`, "shopping-cart");
  };
  const setQty = (name, d) => setCart(prev => prev.map(c => c.name === name ? {
    ...c,
    qty: c.qty + d
  } : c).filter(c => c.qty > 0));
  const toggleFav = name => {
    setFav(f => ({
      ...f,
      [name]: !f[name]
    }));
    if (!fav[name]) toast("Saved to wishlist", "heart");
  };
  const send = text => {
    text = (text || "").trim();
    if (!text) return;
    setQuery("");
    setListening(false);
    setMessages(m => [...m, {
      id: nid(),
      role: "user",
      text
    }]);
    setStatus("Kapruka is thinking…");
    const tid = nid();
    setMessages(m => [...m, {
      id: tid,
      role: "bot",
      thinking: true
    }]);
    setTimeout(() => {
      const picks = CATALOG.slice(0, 3);
      setMessages(m => m.filter(x => x.id !== tid).concat({
        id: nid(),
        role: "bot",
        text: REPLY,
        thought: `Searched Kapruka · ${picks.length} matches · 2s`,
        products: picks
      }));
      setStatus("Tap the mic to talk, or type below");
    }, 1500);
  };
  const micClick = () => query.trim() ? send(query) : setListening(v => !v);
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("header", {
    className: "topbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "inner"
  }, /*#__PURE__*/React.createElement("div", {
    className: "greet"
  }, /*#__PURE__*/React.createElement("span", {
    className: "brandmark"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "leaf",
    size: 20
  })), /*#__PURE__*/React.createElement("div", {
    className: "greet-text"
  }, /*#__PURE__*/React.createElement("span", {
    className: "greet-sub"
  }, "Good evening"), /*#__PURE__*/React.createElement("span", {
    className: "greet-title"
  }, "What can I find for you?"))), /*#__PURE__*/React.createElement("div", {
    className: "top-actions"
  }, /*#__PURE__*/React.createElement("select", {
    className: "langsel",
    defaultValue: "en",
    "aria-label": "Language"
  }, /*#__PURE__*/React.createElement("option", {
    value: "en"
  }, "EN"), /*#__PURE__*/React.createElement("option", {
    value: "si"
  }, "\u0DC3\u0DD2\u0D82"), /*#__PURE__*/React.createElement("option", {
    value: "ta"
  }, "\u0BA4\u0BAE\u0BBF\u0BB4\u0BCD")), /*#__PURE__*/React.createElement(ThemeToggle, null), /*#__PURE__*/React.createElement(IconButton, {
    icon: "volume-2",
    title: "Read replies aloud"
  }), /*#__PURE__*/React.createElement(IconButton, {
    icon: "shopping-cart",
    title: "View cart",
    badge: cartCount,
    onClick: () => setCartOpen(true),
    style: bump ? {
      transform: "scale(1.08)"
    } : undefined
  })))), /*#__PURE__*/React.createElement("main", null, /*#__PURE__*/React.createElement("div", {
    className: "thread"
  }, /*#__PURE__*/React.createElement("div", {
    className: "feed",
    ref: feedRef
  }, messages.map(m => /*#__PURE__*/React.createElement("div", {
    className: "msg-in",
    key: m.id,
    style: {
      display: "flex",
      flexDirection: "column"
    }
  }, /*#__PURE__*/React.createElement(Bubble, {
    role: m.role,
    thought: m.thought,
    thinking: m.thinking
  }, m.text), m.products && /*#__PURE__*/React.createElement("div", {
    className: "grid",
    style: {
      marginLeft: "2.4rem"
    }
  }, m.products.map((p, i) => /*#__PURE__*/React.createElement("div", {
    className: "k-rise",
    key: i,
    style: {
      animationDelay: `${Math.min(i, 8) * 70}ms`
    }
  }, /*#__PURE__*/React.createElement(ProductCard, _extends({}, p, {
    favorite: !!fav[p.name],
    onFavorite: () => toggleFav(p.name),
    onAdd: () => addToCart(p)
  })))))))))), /*#__PURE__*/React.createElement("footer", {
    className: "composer"
  }, /*#__PURE__*/React.createElement("div", {
    className: "composer-inner"
  }, /*#__PURE__*/React.createElement("p", {
    className: "status"
  }, listening ? "Listening…" : status), !started && /*#__PURE__*/React.createElement("div", {
    className: "cards"
  }, CHIPS.map(c => /*#__PURE__*/React.createElement(SuggestionCard, {
    key: c.prompt,
    icon: c.icon,
    tone: c.tone,
    onClick: () => send(c.prompt)
  }, c.tx))), /*#__PURE__*/React.createElement("form", {
    className: "searchbar",
    onSubmit: e => {
      e.preventDefault();
      send(query);
    }
  }, /*#__PURE__*/React.createElement("input", {
    value: query,
    onChange: e => setQuery(e.target.value),
    type: "text",
    autoComplete: "off",
    placeholder: "Tell Kapruka what you're looking for\u2026"
  }), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "mic" + (listening ? " mic--listening" : ""),
    "aria-label": query.trim() ? "Send" : "Tap to talk",
    onClick: micClick
  }, /*#__PURE__*/React.createElement(Icon, {
    name: query.trim() ? "send" : "mic",
    size: 20
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "scrim" + (cartOpen ? " open" : ""),
    onClick: () => setCartOpen(false)
  }), /*#__PURE__*/React.createElement("aside", {
    className: "drawer" + (cartOpen ? " open" : ""),
    "aria-label": "Cart"
  }, /*#__PURE__*/React.createElement("header", null, /*#__PURE__*/React.createElement(Icon, {
    name: "shopping-cart",
    size: 20
  }), /*#__PURE__*/React.createElement("h3", null, "Your cart"), /*#__PURE__*/React.createElement(IconButton, {
    icon: "x",
    title: "Close",
    style: {
      marginLeft: "auto"
    },
    onClick: () => setCartOpen(false)
  })), /*#__PURE__*/React.createElement("div", {
    className: "body"
  }, cart.length === 0 ? /*#__PURE__*/React.createElement("div", {
    className: "empty"
  }, /*#__PURE__*/React.createElement("span", {
    className: "ic"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "gift",
    size: 40,
    strokeWidth: 1.5
  })), "Your cart is empty.", /*#__PURE__*/React.createElement("br", null), "Add items from the suggestions, or just ask me to.") : cart.map(c => /*#__PURE__*/React.createElement("div", {
    className: "citem",
    key: c.name
  }, c.image ? /*#__PURE__*/React.createElement("img", {
    className: "ci-img",
    src: c.image,
    alt: ""
  }) : /*#__PURE__*/React.createElement("div", {
    className: "ci-noimg"
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "gift",
    size: 20
  })), /*#__PURE__*/React.createElement("div", {
    className: "ci-main"
  }, /*#__PURE__*/React.createElement("div", {
    className: "ci-name"
  }, c.name), /*#__PURE__*/React.createElement("div", {
    className: "ci-price"
  }, c.price)), /*#__PURE__*/React.createElement("div", {
    className: "qty"
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": "Decrease",
    onClick: () => setQty(c.name, -1)
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "minus",
    size: 14
  })), /*#__PURE__*/React.createElement("span", null, c.qty), /*#__PURE__*/React.createElement("button", {
    type: "button",
    "aria-label": "Increase",
    onClick: () => setQty(c.name, 1)
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "plus",
    size: 14
  }))), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ci-rm",
    title: "Remove",
    onClick: () => setQty(c.name, -c.qty)
  }, /*#__PURE__*/React.createElement(Icon, {
    name: "trash-2",
    size: 16
  }))))), /*#__PURE__*/React.createElement("div", {
    className: "foot"
  }, /*#__PURE__*/React.createElement("div", {
    className: "totrow"
  }, /*#__PURE__*/React.createElement("span", {
    className: "lbl"
  }, "Estimated total"), /*#__PURE__*/React.createElement("span", {
    className: "val"
  }, cart.length ? `Rs ${rupees(cart).toLocaleString()}` : "Rs 0")), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    full: true,
    iconRight: "arrow-right",
    disabled: !cart.length,
    onClick: () => cart.length && toast("Heading to checkout…", "check")
  }, "Proceed to checkout"))), /*#__PURE__*/React.createElement("div", {
    className: "toasts",
    "aria-live": "polite"
  }, toasts.map(t => /*#__PURE__*/React.createElement(Toast, {
    key: t.id,
    icon: t.icon
  }, t.msg))));
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(App, null));
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/concierge/ConciergeApp.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Bubble = __ds_scope.Bubble;

__ds_ns.SuggestionCard = __ds_scope.SuggestionCard;

__ds_ns.ProductCard = __ds_scope.ProductCard;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.ICON_NAMES = __ds_scope.ICON_NAMES;

__ds_ns.IconButton = __ds_scope.IconButton;

__ds_ns.ThemeToggle = __ds_scope.ThemeToggle;

__ds_ns.Loader = __ds_scope.Loader;

__ds_ns.Toast = __ds_scope.Toast;

})();
