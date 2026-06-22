/* Kapruka delivery city lookup via MCP kapruka_list_delivery_cities */

(function () {
  const CACHE_KEY = "kapruka_delivery_cities_v1";
  const CACHE_TTL_MS = 6 * 60 * 60 * 1000;

  let _mem = null;

  function parseOutput(output) {
    if (!output) return { cities: [], total: 0 };
    try {
      const data = JSON.parse(output);
      const cities = Array.isArray(data.cities) ? data.cities : [];
      return {
        cities: cities.map((c) => ({
          name: c.name || "",
          aliases: Array.isArray(c.aliases) ? c.aliases : [],
        })).filter((c) => c.name),
        total: data.total ?? cities.length,
      };
    } catch (_) {
      return { cities: [], total: 0 };
    }
  }

  function readCache() {
    if (_mem && Date.now() - _mem.at < CACHE_TTL_MS) return _mem.data;
    try {
      const raw = sessionStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed?.at || Date.now() - parsed.at > CACHE_TTL_MS) return null;
      _mem = parsed;
      return parsed.data;
    } catch (_) {
      return null;
    }
  }

  function writeCache(data) {
    _mem = { at: Date.now(), data };
    try {
      sessionStorage.setItem(CACHE_KEY, JSON.stringify(_mem));
    } catch (_) {}
  }

  async function fetchDeliveryCities(query, limit) {
    const params = { limit: limit || 50, response_format: "json" };
    const q = String(query || "").trim();
    if (q) params.query = q;

    const res = await fetch("/api/tool", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "kapruka_list_delivery_cities",
        arguments: { params },
      }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "Could not load delivery cities.");
    return parseOutput(data.output);
  }

  async function loadInitialCities() {
    const cached = readCache();
    if (cached?.cities?.length) return cached;

    const result = await fetchDeliveryCities("", 50);
    writeCache(result);
    return result;
  }

  async function searchDeliveryCities(query) {
    const q = String(query || "").trim();
    if (!q) return loadInitialCities();
    return fetchDeliveryCities(q, 50);
  }

  function formatCityError(raw) {
    const text = String(raw || "");
    if (!/city_not_deliverable/i.test(text)) return null;
    return (
      "That city isn't in Kapruka's delivery network. " +
      "Pick an exact city name from the list below — spelling matters " +
      "(e.g. \"Colombo 03\", not just \"Colombo\")."
    );
  }

  window.KaprukaDeliveryCities = {
    fetchDeliveryCities,
    loadInitialCities,
    searchDeliveryCities,
    formatCityError,
  };
})();
