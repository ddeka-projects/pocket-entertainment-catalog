// Minimal DOM surface: these checks exercise real request/ownership code, not layout.
const mockNodes = new Map();
function mockNode(selector) {
  if (!mockNodes.has(selector)) mockNodes.set(selector, {
    innerHTML: "", textContent: "", value: "", hidden: false, dataset: {},
    classList: { add() {}, remove() {} },
    setAttribute() {}, removeAttribute() {}, addEventListener() {}, append() {}, remove() {},
    querySelector(s) { return mockNode(selector + " " + s); },
    querySelectorAll() { return []; },
  });
  return mockNodes.get(selector);
}
globalThis.document = {
  querySelector: mockNode,
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return mockNode("toast-" + Math.random()); },
  contains() { return false; },
  body: mockNode("body"),
};
globalThis.HTMLElement = class {};
globalThis.window = globalThis;
window.location = { search: "?profile=work" };
window.addEventListener = () => {};
window.requestAnimationFrame = () => {};
window.setInterval = () => 1;
window.clearInterval = () => {};
window.setTimeout = () => 1;
window.clearTimeout = () => {};
window.sessionStorage = window.localStorage = { getItem() { return null; }, setItem() {} };
if (typeof URLSearchParams === "undefined") {
  globalThis.URLSearchParams = class {
    constructor(values) { this.values = typeof values === "string" ? Object.fromEntries(values.replace(/^\?/, "").split("&").map(p => p.split("="))) : values; }
    get(key) { return this.values[key] || null; }
    toString() { return Object.entries(this.values).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&"); }
  };
}
const pendingRequests = [];
globalThis.fetch = (path, options) => new Promise((resolve, reject) => pendingRequests.push({ path, options, resolve, reject }));
function mockRespond(request, payload, ok = true) {
  request.resolve({ ok, status: ok ? 200 : 400, json: async () => payload });
}
async function flushMicrotasks() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function check(value, message) { if (!value) throw new Error(message); }
