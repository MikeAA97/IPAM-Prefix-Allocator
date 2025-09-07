/* auth.js
 * Injects X-API-Key into requests.
 * Include this BEFORE dashboard.js, then call window.api(url, opts).
 */

(() => {
  const HEADER = "X-API-Key";
  const REQ_ID = "X-Request-Id";
  const STORAGE = "ipam_api_key";

  function newReqId() {
    return (crypto?.randomUUID?.()) ||
           (Date.now().toString(36) + Math.random().toString(36).slice(2,10));
  }
  function get() {
    return localStorage.getItem(STORAGE) || null;
  }

  function set(k) {
    if (!k) return clear();
    localStorage.setItem(STORAGE, k);
    return k;
  }

  function clear() {
    localStorage.removeItem(STORAGE);
  }

  async function ensure() {
    let k = get();
    if (!k) {
      k = window.prompt("Enter API key");
      if (!k) throw new Error("API key required");
      set(k);
    }
    return k;
  }

  async function api(input, init = {}) {
    const key = await ensure(); // your existing function that gets the API key
    const headers = new Headers(init.headers || {});
    if (!headers.has("X-API-Key")) headers.set("X-API-Key", key);
    if (!headers.has(REQ_ID)) headers.set(REQ_ID, newReqId());
    return fetch(input, { ...init, headers });
  }


  // exports
  window.Auth = { get, set, clear, ensure, HEADER, STORAGE };
  window.api = api;
})();
