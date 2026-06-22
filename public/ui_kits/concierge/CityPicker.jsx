/* Searchable Kapruka delivery city picker */
(function () {
  const { useState, useEffect, useRef } = React;

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

  window.CityPicker = CityPicker;
})();
