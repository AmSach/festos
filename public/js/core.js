// Core: DOM helper, API client, router, overlays, small SVG charts.
export const S = { token: localStorage.getItem("festos_token"), user: null, clock: null, routes: {}, cleanup: [] };

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
export const svgNS = "http://www.w3.org/2000/svg";
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const c of kids.flat()) if (c !== null && c !== undefined) el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

export async function api(path, body, opts = {}) {
  const res = await fetch("/api" + path, {
    method: body !== undefined ? "POST" : "GET",
    headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: "Bearer " + S.token } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { }
  if (res.status === 401 && !opts.noLogout) { logout(); throw new Error("Please sign in."); }
  if (!res.ok) { const e = new Error(data.error || res.statusText); e.data = data; e.status = res.status; throw e; }
  return data;
}
export function logout() {
  localStorage.removeItem("festos_token"); S.token = null; S.user = null; location.hash = "#/login"; render();
}

// ------------------------------------------------------------ formatting
export const fmt = {
  n: (x) => (x === null || x === undefined ? "—" : Number(x).toLocaleString("en-IN")),
  inr: (x) => (x === null || x === undefined ? "—" : "₹" + Number(x).toLocaleString("en-IN")),
  lakh: (x) => "₹" + (x / 100000).toFixed(1) + " L",
  t: (s) => (s ? s.slice(5, 16).replace("-", "/") : "—"),
  hm: (s) => (s ? s.slice(11, 16) : "—"),
  day: (s) => { if (!s) return "—"; const d = new Date(s.replace(" ", "T")); return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }); },
  rel: (hours) => {
    if (hours === null || hours === undefined) return "";
    if (hours <= 0) return "now";
    if (hours < 1) return `in ${Math.round(hours * 60)} min`;
    if (hours < 48) return `in ${hours.toFixed(hours < 10 ? 1 : 0)} h`;
    return `in ${Math.round(hours / 24)} days`;
  },
};
export const SEVL = { critical: "Critical", high: "High", medium: "Medium", low: "Low", info: "Info" };
export const sevTag = (sv) => h("span", { class: "tag " + ({ critical: "crit", high: "high", low: "", info: "info" }[sv] || "") }, SEVL[sv] || sv);
export const NEED = { WC: "Wheelchair", GF: "Ground floor", VIS: "Visual assistance", WASH: "Near washroom", NONE: "—", UNKNOWN: "Blank (held GF)", SUPPORT: "Support needed" };
export const CAT = { M: "Men's", F: "Women's", X: "All-gender", S: "Staff" };
export const ROLE = { director: "Festival Director", hospitality: "Hospitality", registration: "Registration Desk", events: "Events Team", logistics: "Logistics", merch: "Merchandise Desk", food: "Food Coordinator", volunteer: "Volunteer" };

// ------------------------------------------------------------ overlays
export function toast(msg, kind = "") {
  let box = document.querySelector(".toasts");
  if (!box) { box = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(box); }
  const t = h("div", { class: "toast " + kind }, msg);
  box.appendChild(t);
  setTimeout(() => t.remove(), kind === "err" ? 7000 : 4200);
}
export async function busy(label, fn) {
  const b = h("div", { class: "busy" }, h("span", {}, label));
  document.body.appendChild(b);
  try { return await fn(); } finally { b.remove(); }
}
export function drawer(content, onclose) {
  const sc = h("div", { class: "scrim", onclick: (e) => { if (e.target === sc) close(); } });
  const d = h("div", { class: "drawer", role: "dialog", "aria-modal": "true" },
    h("div", { class: "row" }, h("span", { class: "sp" }), h("button", { class: "btn sm", onclick: () => close() }, "Close")), content);
  sc.appendChild(d);
  document.body.appendChild(sc);
  const esc = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", esc);
  function close() { sc.remove(); document.removeEventListener("keydown", esc); onclose && onclose(); }
  return { close, el: d };
}
export function modal(content) {
  const sc = h("div", { class: "scrim", style: { justifyContent: "center" }, onclick: (e) => { if (e.target === sc) close(); } });
  const m = h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, content);
  sc.appendChild(m);
  document.body.appendChild(sc);
  const esc = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", esc);
  function close() { sc.remove(); document.removeEventListener("keydown", esc); }
  return { close, el: m };
}
// Ask for a reason, then run fn(reason). Shows server errors inline.
export function withReason(title, explain, fn, opts = {}) {
  const ta = h("textarea", { class: "in", placeholder: opts.placeholder || "Why is this the right call? This goes into the audit log." });
  const err = h("div", { class: "note crit hidden" });
  const go = h("button", { class: "btn " + (opts.danger ? "danger" : "primary") }, opts.cta || "Confirm");
  const m = modal([h("h2", { style: { marginTop: 0 } }, title), explain ? h("p", { class: "muted" }, explain) : null, opts.extra || null,
    h("label", { class: "f" }, "Reason (required)", ta), err,
    h("div", { class: "row", style: { marginTop: "12px" } }, h("span", { class: "sp" }), h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), go)]);
  setTimeout(() => ta.focus(), 30);
  go.onclick = async () => {
    go.disabled = true;
    try { await fn(ta.value.trim()); m.close(); }
    catch (e) { err.classList.remove("hidden"); err.textContent = e.message + (e.data && e.data.violations ? " — " + e.data.violations.map((v) => v[1]).join("; ") : ""); }
    finally { go.disabled = false; }
  };
}

// ------------------------------------------------------------ router
export function route(pattern, fn) { S.routes[pattern] = fn; }
export function parseHash() {
  const raw = location.hash.slice(1) || "/";
  const [path, qs] = raw.split("?");
  const q = Object.fromEntries(new URLSearchParams(qs || ""));
  return { path, q };
}
export function go(hash) { location.hash = hash; }
export async function render() {
  const { path, q } = parseHash();
  S.cleanup.forEach((f) => f()); S.cleanup = [];
  for (const [pat, fn] of Object.entries(S.routes)) {
    const rx = new RegExp("^" + pat.replace(/:(\w+)/g, "(?<$1>[^/]+)") + "$");
    const m = path.match(rx);
    if (m) { await fn({ ...(m.groups || {}) }, q); return; }
  }
  S.routes["/"]({}, q);
}

// ------------------------------------------------------------ charts
export function barsVs(opts) {
  // opts: labels, series [{name, values, color}], line {name, values, color}, height
  const W = opts.width || 640, H = opts.height || 200, pad = { l: 44, r: 10, t: 10, b: 26 };
  const n = opts.labels.length;
  const all = [...opts.series.flatMap((x) => x.values), ...(opts.line ? opts.line.values : [])];
  const max = Math.max(1, ...all) * 1.08;
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const bw = (W - pad.l - pad.r) / n;
  const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.title || "chart" });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    g.appendChild(s("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), stroke: "#e8ebf1" }));
    g.appendChild(s("text", { x: pad.l - 6, y: y(v) + 4, "text-anchor": "end" }, Math.round(v).toLocaleString("en-IN")));
  }
  const k = opts.series.length;
  opts.labels.forEach((lab, i) => {
    opts.series.forEach((se, j) => {
      const w = (bw * 0.7) / k, x = pad.l + i * bw + bw * 0.15 + j * w, v = se.values[i] || 0;
      const r = s("rect", { x, y: y(v), width: Math.max(1, w - 2), height: Math.max(0, y(0) - y(v)), fill: se.color, rx: 2 });
      r.appendChild(s("title", {}, `${se.name} · ${lab}: ${v.toLocaleString("en-IN")}`));
      g.appendChild(r);
    });
    g.appendChild(s("text", { x: pad.l + i * bw + bw / 2, y: H - 8, "text-anchor": "middle" }, lab));
  });
  if (opts.line) {
    const pts = opts.line.values.map((v, i) => `${pad.l + i * bw + bw * 0.08},${y(v)} ${pad.l + (i + 1) * bw - bw * 0.08},${y(v)}`).join(" ");
    g.appendChild(s("polyline", { points: pts, fill: "none", stroke: opts.line.color, "stroke-width": 2.5, "stroke-dasharray": "5 3" }));
  }
  return g;
}
export function area(values, opts = {}) {
  const W = opts.width || 640, H = opts.height || 120, pad = { l: 36, r: 8, t: 8, b: 20 };
  const max = Math.max(1, opts.cap || 0, ...values) * 1.1;
  const x = (i) => pad.l + (W - pad.l - pad.r) * (i / (values.length - 1));
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / max);
  const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  g.appendChild(s("path", { d: path + ` L${x(values.length - 1)},${y(0)} L${x(0)},${y(0)} Z`, fill: opts.fill || "rgba(58,55,148,0.18)" }));
  g.appendChild(s("path", { d: path, fill: "none", stroke: opts.color || "#3a3794", "stroke-width": 1.6 }));
  if (opts.cap) {
    g.appendChild(s("line", { x1: pad.l, x2: W - pad.r, y1: y(opts.cap), y2: y(opts.cap), stroke: "#c81d3a", "stroke-dasharray": "4 3" }));
    g.appendChild(s("text", { x: W - pad.r, y: y(opts.cap) - 4, "text-anchor": "end", fill: "#c81d3a" }, opts.capLabel || ""));
  }
  (opts.ticks || []).forEach((t) => g.appendChild(s("text", { x: x(t.i), y: H - 5, "text-anchor": "middle" }, t.label)));
  g.appendChild(s("text", { x: pad.l - 4, y: y(max / 1.1) + 4, "text-anchor": "end" }, Math.round(max / 1.1)));
  return g;
}
export function scatter(points, opts) {
  const W = opts.width || 560, H = opts.height || 240, pad = { l: 56, r: 16, t: 14, b: 36 };
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const x0 = Math.min(...xs) * 0.97, x1 = Math.max(...xs) * 1.02, y0 = Math.min(...ys) - 10, y1 = Math.max(...ys) + 10;
  const X = (v) => pad.l + (W - pad.l - pad.r) * ((v - x0) / (x1 - x0 || 1));
  const Y = (v) => pad.t + (H - pad.t - pad.b) * (1 - (v - y0) / (y1 - y0 || 1));
  const g = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
  g.appendChild(s("line", { x1: pad.l, x2: W - pad.r, y1: H - pad.b, y2: H - pad.b, stroke: "#d9dee7" }));
  g.appendChild(s("line", { x1: pad.l, x2: pad.l, y1: pad.t, y2: H - pad.b, stroke: "#d9dee7" }));
  g.appendChild(s("text", { x: W / 2, y: H - 6, "text-anchor": "middle" }, opts.xl));
  g.appendChild(s("text", { x: 12, y: H / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${H / 2})` }, opts.yl));
  [x0, (x0 + x1) / 2, x1].forEach((v) => g.appendChild(s("text", { x: X(v), y: H - pad.b + 14, "text-anchor": "middle" }, opts.xf(v))));
  [y0, (y0 + y1) / 2, y1].forEach((v) => g.appendChild(s("text", { x: pad.l - 6, y: Y(v) + 4, "text-anchor": "end" }, Math.round(v).toLocaleString("en-IN"))));
  if (opts.budget) {
    g.appendChild(s("line", { x1: X(opts.budget), x2: X(opts.budget), y1: pad.t, y2: H - pad.b, stroke: "#c81d3a", "stroke-dasharray": "4 3" }));
    g.appendChild(s("text", { x: X(opts.budget) + 4, y: pad.t + 10, fill: "#c81d3a" }, "budget"));
  }
  points.forEach((p) => {
    const c = s("circle", { cx: X(p.x), cy: Y(p.y), r: p.on ? 8 : 6, fill: p.on ? "#272566" : "#fff", stroke: p.color || "#272566", "stroke-width": 2.5 });
    c.appendChild(s("title", {}, p.label));
    g.appendChild(c);
    g.appendChild(s("text", { x: X(p.x) + 10, y: Y(p.y) - 8, fill: "#1a2233" }, p.short || ""));
  });
  return g;
}
export function hbar(value, max, color) {
  return h("div", { style: { height: "7px", background: "#e8ebf1", borderRadius: "4px", overflow: "hidden", minWidth: "60px" } },
    h("div", { style: { width: Math.min(100, (100 * value) / Math.max(1, max)) + "%", height: "100%", background: color || "#3a3794" } }));
}
export function table(cols, rows, opts = {}) {
  const t = h("table", { class: "t" },
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.right ? "right" : "" }, c.label)))),
    h("tbody", {}, rows.length ? rows.map((r) => h("tr", { class: opts.onclick ? "click" : "", onclick: opts.onclick ? () => opts.onclick(r) : null },
      cols.map((c) => h("td", { class: (c.right ? "right " : "") + (c.cls || "") }, c.f ? c.f(r) : r[c.key])))) :
      h("tr", {}, h("td", { colspan: cols.length, class: "empty" }, opts.empty || "Nothing here."))));
  return opts.wrap === false ? t : h("div", { class: "tablewrap", style: opts.maxh ? { maxHeight: opts.maxh } : null }, t);
}
export function tabs(items, current, base) {
  return h("div", { class: "tabs", role: "tablist" }, items.map(([k, label]) => h("a", { href: `${base}?tab=${k}`, class: k === current ? "on" : "", role: "tab" }, label)));
}
export function can(view) { return S.user && (S.user.view === "*" || S.user.view.includes(view)); }
export function canW(action) { return S.user && (S.user.write === "*" || S.user.write.includes(action)); }
export function mount(...kids) {
  const pg = document.getElementById("page");
  if (pg) pg.replaceChildren(...kids.flat().filter(Boolean));
}
export function head(title, sub, ...right) {
  return h("div", { class: "pagehead" }, h("div", {}, h("h1", {}, title), sub ? h("p", {}, sub) : null), h("div", { class: "row" }, ...right));
}
