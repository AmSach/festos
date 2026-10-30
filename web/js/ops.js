import { S, h, api, fmt, toast, busy, withReason, mount, head, table, tabs, sevTag, NEED, CAT, can, canW, go, hbar, barsVs, area, render, modal } from "./core.js";

// ====================================================================== control tower
export async function control(p, q) {
  const d = await api("/control");
  S.critical = (d.kpis.alerts.critical || 0);
  const k = d.kpis, t = k.board || d.board.tonight;
  const filt = q.f || (S.user.role_key === "director" ? "all" : "mine");
  const shown = d.alerts.filter((a) => filt === "all" || (filt === "mine" ? a.owner === S.user.role_key : a.severity === "critical"));
  const tonight = d.board.tonight;
  const kpis = h("div", { class: "kpis" },
    kpi(fmt.n(d.board.onsite), "on site right now"),
    kpi(`${fmt.n(tonight.placed)}`, `beds tonight (${tonight.night}) of ${fmt.n(tonight.beds)}`, ""),
    kpi(fmt.n(tonight.unplaced), `waiting for a bed tonight`, tonight.unplaced ? "bad" : "good"),
    kpi(fmt.n(k.alerts.critical || 0), "critical alerts", k.alerts.critical ? "bad" : "good"),
    kpi(`${fmt.lakh(k.ext_cost || 0)}`, `external stay spend of ${fmt.lakh(k.budget)} budget`, k.ext_cost > k.budget ? "bad" : ""),
    kpi(fmt.n(k.diet_gaps), "meal covers without a safe counter", k.diet_gaps ? "warn" : "good"));
  const queue = h("section", { class: "queue", "aria-label": "Needs attention" },
    h("div", { class: "qhead" }, h("h2", {}, "Needs attention"), h("span", { class: "muted small" }, `${shown.length} of ${d.alerts.length}, ranked by severity × how soon it hits`),
      h("span", { class: "sp" }),
      ...[["mine", "Mine"], ["critical", "Critical"], ["all", "All"]].map(([f, l]) => h("a", { class: "btn sm" + (f === filt ? " primary" : ""), href: `#/?f=${f}` }, l))),
    shown.length ? shown.map(alertRow) : h("div", { class: "empty" }, filt === "mine" ? "Nothing assigned to your role right now. Switch to All to see the whole fest." : "All clear."));
  const board = d.board;
  const nowPanel = h("section", { class: "panel" },
    h("h2", { style: { marginTop: 0 } }, "On stage now and next 3 hours"),
    board.running.length || board.upcoming.length ? h("div", { class: "nowlist" },
      board.running.map((x) => heatLine(x, true)), board.upcoming.map((x) => heatLine(x, false))) :
      h("p", { class: "muted" }, "No heats in this window. The schedule runs 30 Oct 08:00 – 1 Nov 20:00; move the clock to see it live."),
    h("h3", {}, "Arrivals in the next 24 hours"),
    arrivalsChart(board.arrivals_24h),
    h("p", { class: "small muted" }, `${fmt.n(board.checked_in)} people checked in so far.`));
  const recent = h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Latest decisions"),
    d.recent.length ? d.recent.map((r) => h("div", { class: "small", style: { padding: "5px 0", borderBottom: "1px solid #e8ebf1" } },
      h("b", {}, r.action.replace(/_/g, " ")), " ", r.entity_id ? h("span", { class: "muted" }, r.entity_id) : null,
      h("div", { class: "muted" }, `${fmt.t(r.ts)} · ${r.actor}` + (r.reason ? ` — “${r.reason}”` : "")))) : h("p", { class: "muted" }, "No changes yet."));
  mount(head("Control tower", `What needs a decision, who owns it, and how soon it bites. Clock: ${fmt.day(d.clock)} ${fmt.hm(d.clock)}.`,
    can("incidents") ? h("a", { class: "btn primary", href: "#/incidents" }, "Report a disruption") : null),
    kpis, h("div", { class: "grid2" }, queue, h("div", {}, nowPanel, recent)));
}
function kpi(v, l, cls) { return h("div", { class: "kpi " + (cls || "") }, h("div", { class: "v" }, v), h("div", { class: "l" }, l)); }
function alertRow(a) {
  const mine = a.owner === S.user.role_key;
  return h("div", { class: "alert " + a.severity },
    h("div", { class: "edge" }),
    h("div", { class: "body" },
      h("div", { class: "title" }, a.title),
      h("div", { class: "detail" }, a.detail),
      h("div", { class: "meta" }, sevTag(a.severity), h("span", {}, a.category), h("span", {}, "Owner: " + ownerName(a.owner)), mine ? h("span", { class: "mine" }, "yours") : null,
        a.why ? h("span", { title: a.why }, "Why this happens ⓘ") : null)),
    h("div", { class: "act" }, a.hours !== null ? h("span", { class: "when" }, a.hours <= 0 ? "Now" : fmt.rel(a.hours)) : h("span", { class: "when muted" }, "Open"),
      a.link ? h("a", { class: "btn sm", href: a.link }, "Open") : null));
}
const ownerName = (o) => ({ director: "Director", hospitality: "Hospitality", registration: "Registration", events: "Events", logistics: "Logistics", merch: "Merch desk", food: "Food", volunteer: "Volunteers" }[o] || o);
function heatLine(x, running) {
  return h("div", { class: "it" }, h("span", { class: "tm" }, running ? h("span", { class: "live", "aria-label": "running" }) : null, fmt.hm(x.start)),
    h("span", {}, h("b", {}, x.ev), ` ${x.round}`, h("span", { class: "muted" }, ` · ${x.venue}`)),
    h("span", { class: "muted small" }, `${x.members} competing`));
}
function arrivalsChart(arr) {
  if (!arr.length) return h("p", { class: "muted small" }, "No arrivals in the next 24 hours.");
  return barsVs({ labels: arr.map((a) => a.h.slice(11) + "h"), series: [{ name: "arrivals", values: arr.map((a) => a.n), color: "#3a3794" }], height: 150, width: 460, title: "arrivals" });
}

// ====================================================================== help desk (volunteer-friendly)
export async function desk(p, q) {
  const inp = h("input", { class: "big", placeholder: "Scan or type participant ID or name", autofocus: true, "aria-label": "Participant ID or name" });
  const out = h("div");
  const look = async () => {
    const v = inp.value.trim();
    if (!v) return;
    let pid = v.toUpperCase();
    if (!/^P[\d_]/.test(pid) && !/^[LE]-/.test(pid)) {
      const r = await api("/participants?q=" + encodeURIComponent(v) + "&limit=12");
      if (r.rows.length !== 1) {
        out.replaceChildren(h("div", { class: "card" }, h("p", {}, `${r.total} matches — names repeat a lot here, so pick by college and arrival:`),
          table([{ label: "ID", key: "pid" }, { label: "Name", key: "name" }, { label: "College", key: "college" }, { label: "Arrives", f: (x) => fmt.t(x.arrival) }],
            r.rows, { onclick: (x) => { inp.value = x.pid; look(); }, wrap: false })));
        return;
      }
      pid = r.rows[0].pid;
    }
    try { out.replaceChildren(await deskCard(pid, look)); } catch (e) { out.replaceChildren(h("div", { class: "note crit" }, e.message)); }
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") look(); });
  mount(h("div", { class: "desk" }, head("Help desk", "One lookup answers: where do I sleep, when do I compete, what do I collect."), inp, out));
  if (q.pid) { inp.value = q.pid; look(); }
}
async function deskCard(pid, refresh) {
  const d = await api("/participants/" + pid);
  const p = d.person;
  const st = p.status !== "active" ? h("div", { class: "note crit" }, `Status: ${p.status.replace("_", " ")}` + (p.merged_into ? ` → use ${p.merged_into}` : "") + ". Send to the Registration Desk.") : null;
  const next = d.registrations.filter((r) => r.start);
  const acts = h("div", { class: "acts" });
  if (canW("checkin")) acts.appendChild(h("button", { class: "btn primary", disabled: !!p.checked_in || p.status !== "active", onclick: async () => {
    try { await api(`/participants/${pid}/checkin`, {}); toast(`${p.name} checked in`, "ok"); refresh(); } catch (e) { toast(e.message, "err"); }
  } }, p.checked_in ? `Checked in ${fmt.hm(p.checked_in)}` : "Check in"));
  if (canW("merch_issue")) d.kit_pending.forEach((item) => acts.appendChild(h("button", { class: "btn", onclick: async () => {
    const r = await api("/merch/issue", { pid, item, desk: "Help desk" });
    if (r.ok) toast(`${item}${r.size !== "NA" ? " " + r.size : ""} issued to ${p.name}`, "ok"); else toast(r.message, "err");
    refresh();
  } }, `Give ${item}`)));
  return h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", {}, p.name), h("span", { class: "tag ind" }, p.pid), h("span", { class: "muted" }, p.college)),
    st,
    h("dl", { class: "kv", style: { marginTop: "10px" } },
      h("dt", {}, "Stay"), h("dd", {}, `${fmt.day(p.arrival)} ${fmt.hm(p.arrival)} → ${fmt.day(p.departure)} ${fmt.hm(p.departure)}`),
      h("dt", {}, "Bed"), h("dd", {}, d.alloc ? (d.alloc.masked ? d.alloc.site_name || "—" : d.alloc.status === "placed" ? h("b", {}, `${d.alloc.room_id} · bed ${d.alloc.bed} · ${d.alloc.site_name}`) : h("span", { class: "tag crit" }, "On waitlist — send to Hospitality")) : "No bed requested"),
      h("dt", {}, "Competes"), h("dd", {}, next.length ? next.map((r) => h("div", {}, h("b", {}, fmt.day(r.start) + " " + fmt.hm(r.start)), ` ${r.name} (${r.round}) · ${r.venue}`)) : "No heats assigned"),
      h("dt", {}, "Needs"), h("dd", {}, p.access_need && p.access_need !== "NONE" ? h("span", { class: "tag high" }, NEED[p.access_need] || p.access_need) : "—"),
      h("dt", {}, "Kit to collect"), h("dd", {}, d.kit_pending.length ? d.kit_pending.join(", ") : h("span", { class: "tag ok" }, "All collected"))),
    d.flags.length ? h("div", { class: "note warn", style: { marginTop: "10px" } }, d.flags.map((f) => h("div", {}, f))) : null,
    acts);
}

// ====================================================================== people
export async function people(p, q) {
  const qs = new URLSearchParams({ limit: 300, ...q });
  const d = await api("/participants?" + qs);
  const f = (k, opts) => h("select", { class: "in", onchange: (e) => { const nq = { ...q, [k]: e.target.value }; if (!e.target.value) delete nq[k]; go("#/people?" + new URLSearchParams(nq)); } },
    opts.map(([v, l]) => h("option", { value: v, selected: (q[k] || "") === v ? true : null }, l)));
  const search = h("input", { class: "in", placeholder: "Name or ID", value: q.q || "", onkeydown: (e) => { if (e.key === "Enter") go("#/people?" + new URLSearchParams({ ...q, q: e.target.value })); } });
  mount(head("People", `${fmt.n(d.total)} records. Fields you are not cleared for are masked; opening a record is logged.`),
    h("div", { class: "row", style: { marginBottom: "10px" } }, search,
      f("kind", [["", "Everyone"], ["participant", "Participants"], ["leader", "Leaders"], ["escort", "Escorts"]]),
      f("status", [["", "Any status"], ["active", "Active"], ["held_duplicate", "Held duplicate"], ["needs_confirmation", "Needs confirmation"], ["merged", "Merged"], ["cancelled", "Cancelled"]]),
      f("bed", [["", "Any bed status"], ["none", "Waiting for a bed"]]),
      can("accommodation") ? f("need", [["", "Any need"], ["WC", "Wheelchair"], ["GF", "Ground floor"], ["VIS", "Visual assistance"], ["WASH", "Near washroom"]]) : null,
      f("minor", [["", "All ages"], ["1", "Minors (17)"]])),
    table([
      { label: "ID", f: (r) => h("a", { href: "#/people/" + r.pid }, r.pid) },
      { label: "Name", key: "name" }, { label: "Contingent", f: (r) => h("a", { href: "#/contingents/" + r.cid }, r.cid) },
      { label: "Housing", f: (r) => CAT[r.housing_cat] || "—" },
      { label: "Need", f: (r) => r.access_need && r.access_need !== "NONE" ? h("span", { class: "tag high" }, NEED[r.access_need]) : "" },
      { label: "Arrives", f: (r) => fmt.t(r.arrival) }, { label: "Leaves", f: (r) => fmt.t(r.departure) },
      { label: "Bed", f: (r) => r.bed_status === "placed" ? (r.room_id || r.site_id) : r.bed_status === "unplaced" ? h("span", { class: "tag crit" }, "waiting") : r.accom_req === "no" ? h("span", { class: "muted" }, "day") : "—" },
      { label: "Events", key: "n_events", right: true },
      { label: "Status", f: (r) => r.status === "active" ? (r.checked_in ? h("span", { class: "tag ok" }, "checked in") : "") : h("span", { class: "tag high" }, r.status.replace("_", " ")) },
      { label: "Issues", f: (r) => r.n_issues ? h("span", { class: "tag" }, r.n_issues) : "" },
    ], d.rows, { onclick: (r) => go("#/people/" + r.pid) }));
}

export async function person({ pid }) {
  const d = await api("/participants/" + pid);
  const p = d.person;
  const masked = p.contact_masked || p.access_masked || p.diet_masked;
  const reveal = masked && can("participants") ? h("button", { class: "btn sm", onclick: () => withReason("Break-glass access",
    "Reveals masked contact, medical and dietary fields for this person. The Director is alerted and the reason is kept in the audit log.",
    async (reason) => { await api(`/participants/${pid}/reveal`, { reason }); const r = await api(`/participants/${pid}?reveal=1`); toast("Revealed — logged", "ok"); renderPerson(r); }, { cta: "Reveal and log" }) }, "Reveal masked fields") : null;
  renderPerson(d, reveal);
}
function renderPerson(d, reveal) {
  const p = d.person, pid = p.pid;
  const a = d.alloc;
  const regRows = d.registrations.map((r) => ({ ...r }));
  mount(
    head(p.name, `${p.pid} · ${p.kind} · ${p.college} (${p.cid})`, reveal,
      canW("checkin") && p.status === "active" ? h("button", { class: "btn" + (p.checked_in ? "" : " primary"), onclick: async () => {
        try { await api(`/participants/${pid}/checkin`, p.checked_in ? { undo: true } : {}); toast(p.checked_in ? "Check-in undone" : "Checked in", "ok"); render(); } catch (e) { toast(e.message, "err"); }
      } }, p.checked_in ? "Undo check-in" : "Check in") : null,
      canW("participant_edit") ? h("button", { class: "btn", onclick: () => editPerson(p) }, "Correct a field") : null),
    p.status !== "active" ? h("div", { class: "note crit", style: { marginBottom: "12px" } }, statusText(p)) : null,
    d.flags.length ? h("div", { class: "note warn", style: { marginBottom: "12px" } }, h("b", {}, "Schedule warnings"), d.flags.map((f) => h("div", {}, f))) : null,
    h("div", { class: "grid2e" },
      h("div", {},
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Profile"),
          h("dl", { class: "kv" },
            dd("Contact", p.contact || p.contact_raw || "—", p.contact_masked ? "masked for your role" : (p.contact_ok === 0 ? "invalid / placeholder" : null)),
            dd("Gender (as given)", p.gender_masked ? "masked" : (p.gender_raw || h("i", {}, "blank — not assumed"))),
            dd("Housing category", CAT[p.housing_cat] + (p.housing_basis ? "" : ""), p.housing_basis),
            dd("Age", p.age ?? (p.age_raw === null ? "masked" : "—"), p.is_minor ? "minor: safety rules apply" : null),
            dd("Accessibility", p.access_masked ? (p.access_raw || "—") : (p.access_raw || h("i", {}, "blank — held for ground floor"))),
            dd("Diet", p.diet_masked ? "masked" : (p.diet_raw || h("i", {}, "blank — not cleared; fed vegetarian until confirmed"))),
            dd("Arrives", `${fmt.day(p.arrival)} ${fmt.hm(p.arrival)}`, p.arrival_orig && p.arrival !== p.arrival_orig ? `originally ${fmt.t(p.arrival_orig)}` : null),
            dd("Leaves", `${fmt.day(p.departure)} ${fmt.hm(p.departure)}`),
            dd("T-shirt", p.tshirt || "—"),
            dd("Checked in", p.checked_in ? fmt.t(p.checked_in) : "not yet"))),
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Events"),
          regRows.length ? table([{ label: "Event", f: (r) => h("b", {}, r.name) }, { label: "Round", f: (r) => r.round || "—" },
            { label: "When", f: (r) => r.start ? `${fmt.day(r.start)} ${fmt.hm(r.start)}–${fmt.hm(r.end)}` : h("span", { class: "tag crit" }, "not seated") },
            { label: "Venue", f: (r) => r.venue || "—" }, { label: "Team", f: (r) => r.team_id || "" }], regRows, { wrap: false }) : h("p", { class: "muted" }, "Not registered for any event."),
          d.later_rounds.length ? h("p", { class: "small muted", style: { marginTop: "8px" } }, "If they advance: " + d.later_rounds.map((l) => `${l.event} ${l.round} ${fmt.day(l.start)} ${fmt.hm(l.start)} (${l.venue})`).join(" · ")) : null),
        d.diet_vendors.length ? h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Where they can eat safely today"),
          d.diet_vendors.map((v) => h("div", { class: "small" }, v.ok_today ? h("span", { class: "tag ok" }, "open") : h("span", { class: "tag crit" }, "blocked"), ` ${v.name} — ${v.menu}`, v.ok_today ? "" : h("span", { class: "muted" }, ` (${v.why})`)))) :
          (p.diet === "NUT" ? h("div", { class: "note crit" }, "Nut allergy: no vendor declares a nut-free kitchen. Route to the allergen-controlled counter once designated.") : null)),
      h("div", {},
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Bed"),
          !a ? h("p", { class: "muted" }, p.accom_req === "no" ? "Day participant — no bed requested." : "No allocation.") :
            a.masked ? h("p", {}, a.site_name || "Allocated (room masked for your role)") :
              h("div", {},
                a.status === "placed" ? h("p", { class: "big" }, h("b", {}, `${a.room_id}, bed ${a.bed}`), ` · ${a.site_name}` + (a.site_kind === "external" ? ` (${a.distance} km)` : "")) :
                  h("p", {}, h("span", { class: "tag crit" }, "Waiting for a bed")),
                h("p", { class: "small muted" }, `Nights: ${a.nights_label}` + (a.cost ? ` · ${fmt.inr(a.cost)}` : "")),
                h("h3", {}, "Why here"), h("ul", { class: "why" }, a.explain.map((x) => h("li", {}, x))),
                a.roommates && a.roommates.length ? [h("h3", {}, "Shares the room with"), h("ul", { class: "why" }, a.roommates.map((r) => h("li", {}, h("a", { href: "#/people/" + r.pid }, r.name), ` (${r.cid}) · ${r.nights}`)))] : null,
                canW("alloc_override") ? h("button", { class: "btn sm", style: { marginTop: "10px" }, onclick: () => moveDialog(p) }, "Move to another site…") : null)),
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Merchandise"),
          d.merch.length ? d.merch.map((m) => h("div", { class: "small" }, m.voided ? h("s", {}, `${m.item} ${m.size}`) : h("b", {}, `${m.item} ${m.size !== "NA" ? m.size : ""}`), ` · ${fmt.t(m.ts)} by ${m.actor} at ${m.desk}`)) : h("p", { class: "muted small" }, "Nothing issued yet."),
          d.kit_pending.length ? h("p", { class: "small" }, "Still to collect: " + d.kit_pending.join(", ")) : null,
          d.merch_blocked.length ? h("div", { class: "note warn small" }, `${d.merch_blocked.length} duplicate claim(s) blocked`) : null),
        d.issues.length ? h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Data findings"),
          d.issues.map((i) => h("div", { class: "small", style: { marginBottom: "5px" } }, sevTag(i.severity), " ", i.message, i.status !== "open" ? h("span", { class: "tag ok" }, " " + i.status) : null))) : null,
        d.duplicates.length ? h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Possible duplicate"),
          d.duplicates.map((x) => h("p", {}, `${x.a} ↔ ${x.b}: ${x.score} bits of evidence · ${x.status}. `, h("a", { href: "#/quality?tab=dups" }, "Review")))) : null,
        d.history.length ? h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "History"),
          d.history.slice(0, 12).map((x) => h("div", { class: "small muted" }, `${fmt.t(x.ts)} ${x.actor} ${x.action.replace(/_/g, " ")}` + (x.reason ? ` — ${x.reason}` : "")))) : null)));
}
const dd = (k, v, note) => [h("dt", {}, k), h("dd", {}, v, note ? h("div", { class: "small muted" }, note) : null)];
function statusText(p) {
  return { held_duplicate: "Held as a probable duplicate — not housed, seated or fed until someone confirms in Data Quality.",
    needs_confirmation: "Arrival is after departure. Nothing is planned for this person until the leader confirms the real times (Correct a field).",
    merged: `Merged into ${p.merged_into}. Use that record.`, cancelled: "Withdrew from the fest." }[p.status] || p.status;
}
function editPerson(p) {
  const fields = [["arrival", "Arrival (YYYY-MM-DD HH:MM)", p.arrival], ["departure", "Departure", p.departure], ["tshirt", "T-shirt size", p.tshirt || ""],
    ["gender_raw", "Gender as given (blank = not stated)", p.gender_raw || ""], ["access_raw", "Accessibility need", p.access_raw || ""], ["diet_raw", "Dietary need", p.diet_raw || ""],
    ["accom_req", "Accommodation requested (yes/no)", p.accom_req]];
  const inputs = {};
  const extra = h("div", { style: { display: "grid", gap: "8px", marginBottom: "8px" } }, fields.map(([k, l, v]) => {
    inputs[k] = h("input", { class: "in", value: v ?? "" });
    return h("label", { class: "f" }, l, inputs[k]);
  }));
  withReason("Correct a field", "Changes ripple: a new gender, need or stay re-houses the person; new times re-check their heats.", async (reason) => {
    const ch = {};
    for (const [k, , v] of fields) if ((inputs[k].value || "") !== (v ?? "")) ch[k] = inputs[k].value;
    const r = await busy("Applying and re-planning…", () => api(`/participants/${p.pid}/edit`, { fields: ch, reason }));
    toast("Saved. " + (r.consequences || []).join(" "), "ok"); render();
  }, { extra, cta: "Save correction" });
}
async function moveDialog(p) {
  const acc = await api("/accommodation");
  const sel = h("select", { class: "in" }, acc.sites.filter((s) => s.status === "open").map((s) => h("option", { value: s.site_id }, `${s.site_id} · ${s.name}` + (s.kind === "campus" ? ` (${s.designation === "X" ? "all-gender" : s.designation === "M" ? "men" : "women"})` : ` ₹${s.cost}`))));
  const res = h("div", { class: "small" });
  const check = async () => {
    const r = await api("/accommodation/move", { pid: p.pid, site_id: sel.value, dry_run: true });
    res.replaceChildren(r.violations.length ? h("div", { class: "note crit" }, h("b", {}, "This breaks rules:"), r.violations.map((v) => h("div", {}, `${v[0] === "high" ? "High risk" : "Low risk"}: ${v[1]}`)),
      r.violations.some((v) => v[0] === "high") ? h("div", {}, "Only the Festival Director can approve this, with a reason of 20+ characters.") : null) :
      h("div", { class: "note" }, r.room_id ? `Free bed found in ${r.room_id}. No rule is broken.` : "No single bed is free for every night of this stay at that site."));
  };
  sel.onchange = check;
  withReason(`Move ${p.name}`, "Pick a site; FestOS checks gender pool, accessibility, minors, capacity and budget before you commit.", async (reason) => {
    await api("/accommodation/move", { pid: p.pid, site_id: sel.value, reason }); toast("Moved and logged", "ok"); render();
  }, { extra: h("div", {}, h("label", { class: "f" }, "Site", sel), res), cta: "Move" });
  check();
}

// ====================================================================== contingents
export async function contingents() {
  const d = await api("/contingents");
  mount(head("Contingents", "38 colleges. Declared strength vs rows in the pack, where they sleep, and how many units (blocks / properties) each group is split across."),
    table([
      { label: "ID", f: (r) => h("a", { href: "#/contingents/" + r.cid }, r.cid) }, { label: "College", f: (r) => h("b", {}, r.college) },
      { label: "Declared", key: "declared", right: true }, { label: "Rows", f: (r) => h("span", { class: r.declared !== r.actual ? "tag high" : "" }, r.actual), right: true },
      { label: "Leader", f: (r) => [r.leader, r.leader_matches.length ? h("div", { class: "small muted" }, `${r.leader_matches.length} namesakes in roster`) : h("div", { class: "small muted" }, "not in roster")] },
      { label: "Preference", key: "preferences_raw" },
      { label: "Housed", f: (r) => `${r.stats.placed || 0}`, right: true }, { label: "Waiting", f: (r) => r.stats.unplaced ? h("span", { class: "tag crit" }, r.stats.unplaced) : "", right: true },
      { label: "Split across", f: (r) => r.units.join(", ") },
      { label: "Delay", f: (r) => r.delay_min ? h("span", { class: "tag high" }, `+${r.delay_min / 60} h`) : "" },
    ], d.rows, { onclick: (r) => go("#/contingents/" + r.cid), maxh: "78vh" }));
}
export async function contingent({ cid }) {
  const d = await api("/contingents/" + cid);
  const c = d.contingent;
  const arr = Object.entries(d.arrivals);
  mount(head(c.college, `${c.cid} · leader ${c.leader} · escort ${c.escort} · preference “${c.preferences_raw}”`,
    can("incidents") ? h("a", { class: "btn", href: `#/incidents?type=late_contingent&cid=${cid}` }, "Simulate a delay") : null),
    h("div", { class: "kpis" }, kpi(c.declared, "declared"), kpi(c.actual, "rows in the pack", c.actual !== c.declared ? "warn" : ""),
      kpi(d.people.filter((x) => x.bed === "placed").length, "housed"), kpi(d.people.filter((x) => x.bed === "unplaced").length, "waiting", "bad"),
      kpi(Object.keys(d.sites).length, "sites used"), kpi(d.people.filter((x) => x.checked_in).length, "checked in")),
    h("div", { class: "grid2" },
      table([{ label: "ID", f: (r) => h("a", { href: "#/people/" + r.pid }, r.pid) }, { label: "Name", key: "name" }, { label: "Kind", key: "kind" },
        { label: "Housing", f: (r) => CAT[r.housing_cat] }, { label: "Need", f: (r) => r.access_need && r.access_need !== "NONE" ? NEED[r.access_need] : "" },
        { label: "Arrives", f: (r) => fmt.t(r.arrival) }, { label: "Bed", f: (r) => r.bed === "placed" ? (r.room_id || r.site_id) : r.bed === "unplaced" ? h("span", { class: "tag crit" }, "waiting") : "—" },
        { label: "Status", f: (r) => r.status === "active" ? "" : h("span", { class: "tag high" }, r.status) }], d.people, { maxh: "70vh" }),
      h("div", {},
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Where they sleep"),
          Object.entries(d.sites).sort((a, b) => b[1] - a[1]).map(([s, n]) => h("div", { class: "row small" }, h("b", { style: { width: "70px" } }, s), hbar(n, Math.max(...Object.values(d.sites))), h("span", {}, n)))),
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Arrival waves"),
          arr.map(([t, n]) => h("div", { class: "small" }, h("b", {}, fmt.day(t) + " " + fmt.hm(t)), ` — ${n} people`))),
        d.issues.length ? h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Findings"),
          d.issues.slice(0, 30).map((i) => h("div", { class: "small", style: { marginBottom: "4px" } }, sevTag(i.severity), " ", i.message))) : null)));
}

// ====================================================================== data quality
export async function quality(p, q) {
  const d = await api("/quality");
  const tab = q.tab || "reconcile";
  const body = h("div");
  mount(head("Data quality", "The pack is messy by design. Nothing is silently fixed: every finding is logged, blanks stay blank, and merges need a person."),
    tabs([["reconcile", "2,836 vs 2,400"], ["dups", `Duplicates (${d.duplicates.filter((x) => x.status === "open").length} open)`], ["issues", "Findings ledger"], ["import", "Import late registrations"]], tab, "#/quality"), body);
  if (tab === "reconcile") {
    const max = Math.max(...d.waterfall.map((w) => Math.abs(w.value)));
    body.append(h("div", { class: "grid2" },
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "From raw rows to beds"),
        d.waterfall.map((w) => h("div", { class: "row", style: { padding: "4px 0", borderBottom: w.total ? "2px solid #d9dee7" : "1px solid #f0f2f6" } },
          h("span", { style: { width: "320px", fontWeight: w.total ? 700 : 400 } }, w.label),
          h("div", { style: { flex: 1 } }, hbar(Math.abs(w.value), max, w.total ? "#272566" : w.value < 0 ? "#c81d3a" : "#0f7f78")),
          h("b", { style: { width: "70px", textAlign: "right", color: w.value < 0 && !w.total ? "#c81d3a" : "" } }, (w.value > 0 && !w.total && w !== d.waterfall[0] ? "+" : "") + fmt.n(w.value))))),
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Where does “about 2,400” come from?"),
        h("p", {}, `No single field reconciles to 2,400, so we plan on records and size services on concurrency:`),
        h("dl", { class: "kv" },
          ...dd("Unique registrants", fmt.n(d.waterfall[2].value)), ...dd("Declared by leaders", fmt.n(d.declared_total)),
          ...dd("Peak on site at once", fmt.n(d.peak), fmt.t(d.peak_time)),
          ...Object.entries(d.days).flatMap(([k, v]) => dd(`On site ${fmt.day(k)}`, fmt.n(v)))),
        h("p", { class: "small muted", style: { marginTop: "10px" } }, "“2,400” sits between the peak hour and the busiest day — a sensible headline for the brochure, a dangerous one for catering and beds."),
        h("h3", {}, "Declared vs actual strength"),
        table([{ label: "Contingent", f: (r) => `${r.cid} ${r.college}` }, { label: "Declared", key: "declared", right: true }, { label: "Rows", key: "actual", right: true },
          { label: "Gap", f: (r) => (r.actual - r.declared > 0 ? "+" : "") + (r.actual - r.declared), right: true }], d.strength_mismatch, { wrap: false }))));
  } else if (tab === "dups") {
    body.append(h("p", { class: "note" }, `Names repeat heavily in this pack: we found ${fmt.n(d.namesake_pairs)} same-contingent pairs with near-identical names. A name match is therefore weak evidence — the score weighs every field by how often two different people agree on it (learned from the data). The planted duplicates score ~21 bits; the best namesake pair scores 11.`));
    d.duplicates.forEach((x) => body.append(dupCard(x)));
    if (!d.duplicates.length) body.append(h("div", { class: "empty" }, "No duplicate candidates."));
  } else if (tab === "issues") {
    const code = q.code;
    if (code) {
      const r = await api("/quality/issues?code=" + code);
      body.append(h("p", {}, h("a", { href: "#/quality?tab=issues" }, "All findings"), ` / ${code}`),
        table([{ label: "Entity", f: (x) => x.entity === "participant" ? h("a", { href: "#/people/" + x.entity_id }, x.entity_id) : `${x.entity} ${x.entity_id}` },
          { label: "Severity", f: (x) => sevTag(x.severity) }, { label: "Finding", key: "message" }, { label: "Status", f: (x) => x.status + (x.resolution ? ` — ${x.resolution}` : "") }], r.rows));
    } else {
      body.append(table([{ label: "Severity", f: (x) => sevTag(x.severity) }, { label: "Finding", f: (x) => h("a", { href: `#/quality?tab=issues&code=${x.code}` }, x.code.replace(/_/g, " ").toLowerCase()) },
        { label: "On", key: "entity" }, { label: "Count", key: "n", right: true }, { label: "Example", f: (x) => h("span", { class: "small" }, x.example) }], d.issues, { maxh: "75vh" }));
    }
  } else if (tab === "import") {
    const ta = h("textarea", { class: "in", style: { minHeight: "140px", fontFamily: "ui-monospace, monospace", fontSize: "12px" } },
      "participant_id,name,college,contingent_id,gender_as_given,age,contact,accessibility_needs,dietary_needs,arrival_datetime,departure_datetime,accommodation_requested,tshirt_size\nP2901,Kabir Sethi,IIT Bombay,C08,Male,21,+91-9822012345,None,Vegetarian,2026-10-31 07:00,2026-11-01 19:00,yes,M");
    const out = h("div");
    const run = async (commit) => {
      try {
        const r = await busy(commit ? "Importing and checking duplicates…" : "Validating…", () => api("/import/participants", { csv: ta.value, commit, reason: "late registration" }));
        out.replaceChildren(table([{ label: "ID", key: "pid" }, { label: "Name", key: "name" }, { label: "Problems", f: (x) => x.problems.length ? x.problems.join("; ") : h("span", { class: "tag ok" }, "ok") }], r.report, { wrap: false }),
          commit ? h("div", { class: "note" }, `Imported ${r.committed}. Duplicate candidates: ${r.duplicates.length}. New people were housed if a bed was free.`) : null);
        if (commit) toast("Imported", "ok");
      } catch (e) { out.replaceChildren(h("div", { class: "note crit" }, e.message)); }
    };
    body.append(h("p", {}, "Paste rows in the participants.csv format. Rows are validated first; on import each new person goes through duplicate detection and is housed around everyone else."),
      ta, h("div", { class: "row", style: { margin: "8px 0" } }, h("button", { class: "btn", onclick: () => run(false) }, "Validate"),
        canW("import") ? h("button", { class: "btn primary", onclick: () => run(true) }, "Import") : null), out);
  }
}
function dupCard(x) {
  const fields = x.evidence.fields || [];
  const maxw = Math.max(...fields.map((f) => Math.abs(f.weight)), 1);
  return h("section", { class: "panel" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, `${x.a} ↔ ${x.b}`), h("span", { class: "tag " + (x.score >= 18 ? "crit" : "high") }, `${x.score} bits`),
      h("span", { class: "tag" }, x.status), h("span", { class: "sp" }),
      x.status === "open" && canW("dedup") ? [
        h("button", { class: "btn", onclick: () => withReason("Not the same person", "Releases the held record into beds, kits and meals.", async (reason) => { await busy("Updating…", () => api(`/quality/dups/${x.id}/reject`, { reason })); toast("Kept both records", "ok"); render(); }) }, "Different people"),
        h("button", { class: "btn primary", onclick: () => withReason(`Merge ${x.b} into ${x.a}`, "Moves registrations, voids duplicate kit, frees any bed. Reversible only through a new correction.", async (reason) => { await busy("Merging…", () => api(`/quality/dups/${x.id}/merge`, { reason })); toast("Merged", "ok"); render(); }) }, "Merge")] : null),
    table([{ label: "Field", key: "field" }, { label: x.a, f: (f) => String(f.a ?? "—") }, { label: x.b, f: (f) => String(f.b ?? "—") },
      { label: "Evidence", f: (f) => h("div", { class: "row", style: { gap: "6px" } },
        h("div", { style: { width: "120px", height: "8px", background: "#e8ebf1", position: "relative", borderRadius: "4px" } },
          h("div", { style: { position: "absolute", left: f.weight >= 0 ? "50%" : (50 - 50 * Math.abs(f.weight) / maxw) + "%", width: (50 * Math.abs(f.weight) / maxw) + "%", height: "100%", background: f.weight >= 0 ? "#0f7f78" : "#c81d3a" } })),
        h("span", { class: "small" }, (f.weight > 0 ? "+" : "") + f.weight), f.note ? h("span", { class: "small muted" }, f.note) : null) }], fields, { wrap: false }));
}
