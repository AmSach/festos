import { S, h, s, api, render, route, logout, fmt, toast, can, ROLE, go } from "./core.js";
import * as ops from "./ops.js";
import * as plan from "./plan.js";
import * as gov from "./gov.js";

const NAV = [
  ["Run the fest", [["#/", "Control tower", "control"], ["#/desk", "Help desk", "participants"], ["#/people", "People", "participants"], ["#/contingents", "Contingents", "participants"]]],
  ["Plans", [["#/accommodation", "Accommodation", "accommodation"], ["#/schedule", "Events & venues", "schedule"], ["#/inventory", "Logistics", "inventory"],
    ["#/merch", "Merchandise", "merch"], ["#/food", "Food vendors", "food"], ["#/transport", "Transport", "transport"]]],
  ["When plans change", [["#/incidents", "Disruptions", "incidents"], ["#/quality", "Data quality", "quality"]]],
  ["Accountability", [["#/audit", "Audit & overrides", "audit"], ["#/staff", "Staff & access", "staff"], ["#/assumptions", "Assumptions", "assumptions"]]],
];

// ------------------------------------------------------------ fest clock ribbon
const R0 = new Date("2026-10-29T00:00"), R1 = new Date("2026-11-04T00:00");
const pct = (d) => (100 * (new Date(d.replace(" ", "T")) - R0)) / (R1 - R0);
function ribbon() {
  const r = h("div", { class: "ribbon", title: "Fest clock — click to move the demo clock", onclick: (e) => {
    if (S.user.role_key !== "director") return;
    const rect = r.getBoundingClientRect();
    const t = new Date(R0.getTime() + ((e.clientX - rect.left) / rect.width) * (R1 - R0));
    t.setMinutes(Math.round(t.getMinutes() / 30) * 30, 0, 0);
    const iso = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
    setClock(iso);
  } });
  for (let i = 0; i < 6; i++) {
    const d = new Date(R0.getTime() + i * 86400000);
    const left = (100 * i) / 6;
    r.appendChild(h("div", { class: "day", style: { left: left + "%" } }, d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })));
    r.appendChild(h("div", { class: "night", style: { left: (100 * (i + 22 / 24)) / 6 + "%", width: (100 * (8 / 24)) / 6 + "%" } }));
  }
  r.appendChild(h("div", { class: "fest", style: { left: pct("2026-10-30 08:00") + "%", width: pct("2026-11-01 20:00") - pct("2026-10-30 08:00") + "%" }, title: "Fest window" }));
  if (S.clock) {
    const p = pct(S.clock);
    r.appendChild(h("div", { class: "nowline", style: { left: p + "%" } }));
    r.appendChild(h("div", { class: "nowlabel", style: { left: p + "%" } }, "Now · " + fmt.day(S.clock) + " " + fmt.hm(S.clock)));
  }
  return r;
}
async function setClock(iso) {
  await api("/clock", { clock: iso });
  S.clock = iso;
  toast("Fest clock moved to " + fmt.day(iso) + " " + fmt.hm(iso));
  render();
}

// ------------------------------------------------------------ search
function searchBox() {
  const inp = h("input", { placeholder: "Find a person, ID, college, event, venue…   ( / )", "aria-label": "Search" });
  const res = h("div", { class: "res hidden" });
  let sel = -1, items = [], timer;
  inp.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!inp.value.trim()) { res.classList.add("hidden"); return; }
      const d = await api("/search?q=" + encodeURIComponent(inp.value.trim()));
      items = d.results; sel = -1;
      res.innerHTML = "";
      items.forEach((it, i) => res.appendChild(h("a", { href: it.link, onclick: () => { res.classList.add("hidden"); inp.value = ""; } },
        h("span", { class: "t" }, it.type), h("b", {}, it.label), h("span", { class: "muted small" }, it.sub))));
      if (!items.length) res.appendChild(h("div", { class: "empty" }, "No match. Try a participant ID like P0119."));
      res.classList.remove("hidden");
    }, 140);
  });
  inp.addEventListener("keydown", (e) => {
    const as = [...res.querySelectorAll("a")];
    if (e.key === "ArrowDown") { sel = Math.min(as.length - 1, sel + 1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); e.preventDefault(); }
    else if (e.key === "Enter" && as[Math.max(0, sel)]) { as[Math.max(0, sel)].click(); location.hash = as[Math.max(0, sel)].getAttribute("href"); }
    else if (e.key === "Escape") { res.classList.add("hidden"); inp.blur(); }
    as.forEach((a, i) => a.classList.toggle("sel", i === sel));
  });
  inp.addEventListener("blur", () => setTimeout(() => res.classList.add("hidden"), 200));
  document.onkeydown = (e) => { if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") { e.preventDefault(); inp.focus(); } };
  return h("div", { class: "search" },
    s("svg", { width: 14, height: 14, viewBox: "0 0 24 24" }, s("circle", { cx: 10, cy: 10, r: 7, fill: "none", stroke: "#7d869a", "stroke-width": 2.5 }), s("line", { x1: 15, y1: 15, x2: 22, y2: 22, stroke: "#7d869a", "stroke-width": 2.5 })),
    inp, res);
}

// ------------------------------------------------------------ shell
export function shell(content, active) {
  const app = document.getElementById("app");
  if (!app) return;
  if (!S.user) {
    location.hash = "#/login";
    loginView();
    return;
  }
  app.innerHTML = "";
  const nav = h("nav", { "aria-label": "Main" });
  NAV.forEach(([g, items]) => {
    const vis = items.filter(([, , v]) => v === "audit" ? S.user.role_key === "director" : ["staff", "assumptions"].includes(v) ? true : can(v));
    if (!vis.length) return;
    nav.appendChild(h("div", { class: "grp" }, g));
    vis.forEach(([href, label]) => nav.appendChild(h("a", { href, class: active === href ? "on" : "" }, label,
      href === "#/" && S.critical ? h("span", { class: "pill", title: "critical alerts" }, S.critical) : null)));
  });
  const live = S.clockMode === "live";
  const clockctl = h("div", { class: "clockctl" },
    h("span", {}, live ? "Live clock" : "Demo clock"), h("b", {}, S.clock ? fmt.day(S.clock) + " · " + fmt.hm(S.clock) : ""),
    S.user.role_key === "director" ? h("button", { class: "btn sm", title: "Advance one hour", onclick: () => {
      const d = new Date(S.clock.replace(" ", "T")); d.setHours(d.getHours() + 1);
      setClock(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
    } }, "+1 h") : null);
  app.appendChild(h("div", { class: "shell" },
    h("aside", { class: "rail" },
      h("div", { class: "brand" }, 
        h("div", { class: "brand-logo" }, h("b", {}, "FestOS"), h("span", { class: "brand-live" }, "LIVE")),
        h("span", { class: "brand-sub" }, "Aarohan 2026 Control")),
      nav,
      h("div", { class: "who" }, 
        h("div", { class: "who-name" }, S.user.name), 
        h("div", { class: "who-role" }, `${S.user.role} · ${S.user.uid}`),
        h("span", { class: "small who-shift" }, `Shift ${S.user.shift} ` + (S.user.on_shift ? "· on shift" : "· off shift")),
        h("button", { class: "signout-btn", onclick: logout }, "Sign out"))),
    h("div", { class: "main" },
      h("header", { class: "topbar" }, ribbon(), h("div", { class: "bar2" }, searchBox(), h("span", { class: "sp" }), clockctl)),
      h("main", { class: "page", id: "page" }, content))));
}

// ------------------------------------------------------------ login
async function loginView() {
  const app = document.getElementById("app");
  app.innerHTML = "";
  const uid = h("input", { class: "in", placeholder: "Staff ID, e.g. ST08", autocomplete: "username" });
  const pw = h("input", { class: "in", type: "password", placeholder: "Password", autocomplete: "current-password" });
  const err = h("div", { class: "note crit hidden" });
  const doLogin = async (u, p) => {
    try {
      const d = await api("/login", { uid: u, password: p }, { noLogout: true });
      S.token = d.token; localStorage.setItem("festos_token", d.token);
      S.user = d.user; location.hash = d.user.role_key === "volunteer" ? "#/desk" : "#/"; await boot();
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
  };
  const roles = h("div", { class: "roles" });
  try {
    const d = await api("/demo-accounts");
    d.accounts.forEach((a) => roles.appendChild(h("button", { type: "button", onclick: () => doLogin(a.uid, d.password) },
      h("b", {}, ROLE[a.role_key]), h("span", {}, `${a.uid} · ${a.name}`))));
  } catch (e) { }
  app.appendChild(h("div", { class: "login" },
    h("div", { class: "l" },
      h("div", {}, h("h1", {}, "FestOS"), h("p", {}, "One control room for Aarohan: who is here, where they sleep, when they compete, what they eat, what they carry — and what breaks when the plan changes.")),
      h("p", { class: "small" }, "Internal tool for the organising team. Synthetic data only. Every change you make is logged with your staff ID.")),
    h("div", { class: "r" },
      h("h2", { style: { marginTop: 0 } }, "Sign in"),
      h("form", { onsubmit: (e) => { e.preventDefault(); doLogin(uid.value, pw.value); } }, uid, pw, err, h("button", { class: "btn primary", type: "submit" }, "Sign in")),
      h("h3", { style: { marginTop: "28px" } }, "Demo: sign in as a role"),
      h("p", { class: "muted small" }, "Each role sees different screens and fields. Password for all demo accounts: aarohan2026."),
      roles)));
}

// ------------------------------------------------------------ boot
async function boot() {
  if (!S.token) { 
    location.hash = "#/login"; 
    return loginView(); 
  }
  try {
    const d = await api("/me");
    S.user = d.user; 
    S.clock = d.clock; 
    S.clockMode = d.clock_mode;
  } catch (e) { 
    console.warn("Auth check failed:", e.message);
    return loginView(); 
  }
  render();
}

route("/login", () => loginView());
const wrap = (href, fn) => async (p, q) => {
  if (!S.user && location.hash !== "#/login") {
    location.hash = "#/login";
    return;
  }
  try {
    shell(h("div", { class: "empty", style: { padding: "40px 0" } }, 
      h("div", { class: "loading-spinner" }),
      h("div", { style: { marginTop: "12px", color: "var(--ink-3)", fontSize: "13px" } }, "Syncing festival state…")
    ), href);
    await fn(p, q);
  } catch (e) {
    console.error("View error:", e);
    const pg = document.getElementById("page");
    if (pg) {
      pg.replaceChildren(
        h("div", { class: "note crit", style: { padding: "18px 20px", borderRadius: "8px", margin: "20px 0" } },
          h("div", { style: { fontWeight: "700", fontSize: "15px", marginBottom: "4px" } }, "Unable to load view"),
          h("div", { style: { fontSize: "13.5px", marginBottom: "14px" } }, e.message || "An unexpected error occurred while communicating with the server."),
          h("div", { class: "row", style: { gap: "8px" } },
            h("button", { class: "btn primary sm", onclick: () => render() }, "Retry"),
            h("button", { class: "btn sm", onclick: () => { location.hash = "#/"; render(); } }, "Return to Control Tower")
          )
        )
      );
    }
  }
};
route("/", wrap("#/", ops.control));
route("/desk", wrap("#/desk", ops.desk));
route("/people", wrap("#/people", ops.people));
route("/people/:pid", wrap("#/people", ops.person));
route("/contingents", wrap("#/contingents", ops.contingents));
route("/contingents/:cid", wrap("#/contingents", ops.contingent));
route("/quality", wrap("#/quality", ops.quality));
route("/arrivals", wrap("#/", ops.control));
route("/accommodation", wrap("#/accommodation", plan.accommodation));
route("/schedule", wrap("#/schedule", plan.schedulePage));
route("/inventory", wrap("#/inventory", plan.inventory));
route("/merch", wrap("#/merch", plan.merch));
route("/food", wrap("#/food", plan.food));
route("/transport", wrap("#/transport", plan.transport));
route("/incidents", wrap("#/incidents", gov.incidents));
route("/audit", wrap("#/audit", gov.audit));
route("/staff", wrap("#/staff", gov.staff));
route("/assumptions", wrap("#/assumptions", gov.assumptions));

window.addEventListener("hashchange", () => { if (S.user) render(); else boot(); });
boot();
export { setClock };
