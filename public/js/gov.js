import { S, h, api, fmt, toast, busy, withReason, mount, head, table, tabs, sevTag, CAT, can, canW, go, render, ROLE } from "./core.js";

const SCEN = {
  late_contingent: ["Late contingent", "A bus is delayed. Arrivals shift; beds, heats, meals and shuttles follow."],
  hostel_closure: ["Hostel closure", "A block, floor or property closes. Occupants are re-housed around everyone else."],
  equipment_failure: ["Equipment failure", "Units break. Kit plans and heats that depend on them are repaired."],
  venue_change: ["Venue unavailable", "Rain, damage or a VIP booking. Heats move with minimal disruption."],
  vendor_shutdown: ["Vendor shutdown", "A counter closes. Diet coverage is re-solved meal by meal."],
  duplicate_record: ["Duplicate record", "Someone registers twice with a typo and a dummy phone."],
  duplicate_merch_claim: ["Duplicate merch claim", "The same person asks for a second T-shirt at another desk."],
  participant_cancel: ["Participant withdraws", "A bed and a seat free up; the waitlist moves."],
};

export async function incidents(p, q) {
  const d = await api("/incidents");
  const o = d.options;
  let type = q.type || "late_contingent";
  const form = h("div");
  const out = h("div");
  const picker = h("div", { class: "scen" }, Object.entries(SCEN).map(([k, [t, sub]]) => h("button", { class: k === type ? "on" : "", onclick: () => go(`#/incidents?type=${k}`) }, h("b", {}, t), h("span", {}, sub))));
  const sel = (name, opts, val) => h("label", { class: "f" }, name, h("select", { class: "in", "data-k": name }, opts.map(([v, l]) => h("option", { value: v, selected: v === val ? true : null }, l))));
  const inp = (name, label, val, attrs = {}) => h("label", { class: "f" }, label, h("input", { class: "in", "data-k": name, value: val, ...attrs }));
  const clk = d.clock;
  const F = {
    late_contingent: () => [h("label", { class: "f" }, "Contingent", h("select", { class: "in", "data-k": "cid" }, o.contingents.map((c) => h("option", { value: c.cid, selected: c.cid === (q.cid || "C17") ? true : null }, `${c.cid} ${c.college} (${c.actual})`)))),
      inp("delay_hours", "Delay (hours)", 8, { type: "number", min: 1, max: 48 })],
    hostel_closure: () => [h("label", { class: "f" }, "What closes", h("select", { class: "in", "data-k": "target" },
      o.blocks.map((b) => h("option", { value: b }, `Whole block ${b}`)), o.sites.map((s) => h("option", { value: s.site_id, selected: s.site_id === "G1-F1" ? true : null }, `${s.site_id} · ${s.name}`)))),
      inp("from", "From", clk)],
    equipment_failure: () => [h("label", { class: "f" }, "Item", h("select", { class: "in", "data-k": "item" }, o.items.map((i) => h("option", { selected: i === "projector" ? true : null }, i)))),
      h("label", { class: "f" }, "Where", h("select", { class: "in", "data-k": "location" }, h("option", { value: "" }, "anywhere"), o.locations.map((l) => h("option", {}, l)))),
      inp("qty", "Units", 20, { type: "number", min: 1 })],
    venue_change: () => [h("label", { class: "f" }, "Venue", h("select", { class: "in", "data-k": "vid" }, o.venues.map((v) => h("option", { value: v.vid, selected: v.vid === "V04" ? true : null }, v.name)))),
      inp("from", "Unavailable from", "2026-10-31 14:00"), inp("to", "until", "2026-10-31 18:00"), inp("reason", "Reason", "heavy rain forecast")],
    vendor_shutdown: () => [h("label", { class: "f" }, "Vendor", h("select", { class: "in", "data-k": "vid" }, o.vendors.map((v) => h("option", { value: v.vid, selected: v.vid === "VND02" ? true : null }, `${v.name} · ${v.menu}`)))),
      inp("from", "From", clk), inp("reason", "Reason", "gas cylinder failure")],
    duplicate_record: () => [inp("source_pid", "Copy of participant", "P0119"), inp("name_variant", "Name as typed", "Ragav Joshi"), inp("contact", "Contact as typed", "98765 43210")],
    duplicate_merch_claim: () => [inp("pid", "Participant", "P0005"), h("label", { class: "f" }, "Item", h("select", { class: "in", "data-k": "item" }, ["T-shirt", "ID card", "lanyard"].map((x) => h("option", {}, x))))],
    participant_cancel: () => [inp("pid", "Participant", "P0010")],
  };
  const params = () => Object.fromEntries([...form.querySelectorAll("[data-k]")].map((e) => [e.dataset.k, e.type === "number" ? +e.value : e.value]).filter(([, v]) => v !== ""));
  form.append(h("div", { class: "row", style: { alignItems: "flex-end" } }, F[type](),
    h("button", { class: "btn", onclick: async () => { try { const r = await busy("Simulating on a copy of live data…", () => api("/incidents/preview", { type, params: params() })); out.replaceChildren(impactView(r, false)); } catch (e) { toast(e.message, "err"); } } }, "Preview impact"),
    canW("incident") ? h("button", { class: "btn primary", onclick: () => withReason(`Apply: ${SCEN[type][0]}`, "This changes live state for every module and notifies owners. The prior state is snapshotted so it can be undone.", async (reason) => {
      const r = await busy("Applying and repairing…", () => api("/incidents/commit", { type, params: params(), reason }));
      toast(`Applied: ${r.title}`, "ok"); out.replaceChildren(impactView(r, true)); refreshHistory();
    }, { cta: "Apply to live plan", danger: true }) }, "Apply to live plan") : null));
  const hist = h("div");
  const refreshHistory = async () => {
    const dd = await api("/incidents");
    hist.replaceChildren(table([{ label: "#", key: "id" }, { label: "When", f: (i) => fmt.t(i.ts) }, { label: "What", f: (i) => h("b", {}, i.title) }, { label: "By", key: "actor" }, { label: "Reason", key: "reason" },
      { label: "Result", f: (i) => h("span", { class: "small" }, summaryLine(i.impact && i.impact.impact)) },
      { label: "Status", f: (i) => h("span", { class: "tag " + (i.status === "committed" ? "ind" : "") }, i.status) },
      { label: "", f: (i) => i.status === "committed" && canW("incident") ? h("button", { class: "btn sm", onclick: () => withReason(`Undo #${i.id}`, "Restores the snapshot taken before this incident. Only possible while nothing else has changed since.", async (reason) => {
        await busy("Restoring…", () => api(`/incidents/${i.id}/undo`, { reason })); toast("Undone", "ok"); render();
      }) }, "Undo") : "" }], dd.incidents, { empty: "No disruptions recorded yet." }));
  };
  mount(head("Disruptions", "Preview any disruption on a copy of live data, see every downstream effect, then apply it with a reason. A change is only complete when its consequences are visible."),
    picker, h("section", { class: "panel", style: { marginTop: "14px" } }, h("h2", { style: { marginTop: 0 } }, SCEN[type][0]), h("p", { class: "muted" }, SCEN[type][1]), form), out,
    h("h2", {}, "Disruption log"), hist);
  refreshHistory();
}
function summaryLine(imp) {
  if (!imp || !imp.modules) return "";
  const m = imp.modules, b = [];
  if (m.accommodation.moved) b.push(`${m.accommodation.moved} moved rooms`);
  if (m.accommodation.lost_bed) b.push(`${m.accommodation.lost_bed} lost a bed`);
  if (m.accommodation.gained_bed) b.push(`${m.accommodation.gained_bed} housed`);
  if (m.schedule.heats_moved) b.push(`${m.schedule.heats_moved} heats moved`);
  if (m.schedule.participants_reassigned) b.push(`${m.schedule.participants_reassigned} re-seated`);
  const dg = Object.values(m.food.diet_gap_delta).reduce((a, x) => a + x, 0);
  if (dg) b.push(`diet gap ${dg > 0 ? "+" : ""}${dg}`);
  return b.join(" · ") || "no downstream change";
}
function impactView(r, committed) {
  const m = r.impact.modules;
  const card = (title, n, sub, lvl) => h("div", { class: "m " + (lvl || "") }, h("h4", {}, title), h("div", { class: "n" }, n), h("div", { class: "small muted" }, sub));
  const acc = m.accommodation, sch = m.schedule;
  const dg = m.food.diet_gap_delta;
  const dgt = Object.values(dg).reduce((a, x) => a + x, 0);
  const cd = Object.entries(sch.conflicts_delta);
  const blast = h("div", { class: "blast" },
    card("Accommodation", `${acc.moved + acc.lost_bed + acc.gained_bed} people`, `${acc.moved} moved · ${acc.lost_bed} lost a bed · ${acc.gained_bed} newly housed · cost ${acc.cost_delta >= 0 ? "+" : ""}${fmt.inr(acc.cost_delta)}`, acc.lost_bed ? "bad" : acc.moved || acc.gained_bed ? "hit" : ""),
    card("Events", `${sch.heats_moved} heats · ${sch.participants_reassigned} people`, cd.length ? cd.map(([k, v]) => `${k.replace(/_/g, " ")} ${v > 0 ? "+" : ""}${v}`).join(" · ") : "no new conflicts", cd.some(([, v]) => v > 0) ? "bad" : sch.heats_moved ? "hit" : ""),
    card("Food", dgt ? `${dgt > 0 ? "+" : ""}${dgt} uncovered` : "no change", Object.entries(dg).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(" · ") + (m.food.meals_changed ? ` · head-count changed for ${m.food.meals_changed} meals` : ""), dgt > 0 ? "bad" : m.food.meals_changed ? "hit" : ""),
    card("Logistics", m.logistics.equipment_shortages_delta ? `${m.logistics.equipment_shortages_delta > 0 ? "+" : ""}${m.logistics.equipment_shortages_delta} shortfalls` : "kit still covered", "", m.logistics.equipment_shortages_delta > 0 ? "bad" : ""),
    card("Transport", Object.keys(m.transport.residents_delta).length ? "shuttle loads changed" : "no change", Object.entries(m.transport.residents_delta).map(([k, v]) => `${k} ${v > 0 ? "+" : ""}${v}`).join(" · "), Object.keys(m.transport.residents_delta).length ? "hit" : ""),
    card("People to notify", fmt.n(r.impact.notify_count), "room changes, lost beds and re-seated competitors", r.impact.notify_count ? "hit" : ""));
  const S2 = r.summary;
  return h("section", { class: "panel", style: { marginTop: "14px", borderColor: committed ? "#272566" : "" } },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, (committed ? "Applied: " : "Preview: ") + r.title), h("span", { class: "tag " + (committed ? "ind" : "") }, committed ? "live state changed" : "nothing saved")),
    h("p", { class: "small muted" }, Object.entries(S2).map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? (v.message || JSON.stringify(v)) : v}`).join(" · ")),
    blast,
    r.impact.new_alerts.length || r.impact.cleared_alerts.length ? h("div", { class: "grid2e", style: { marginTop: "12px" } },
      h("div", {}, h("h3", {}, "New alerts"), r.impact.new_alerts.map((a) => h("div", { class: "small" }, "• " + a))),
      h("div", {}, h("h3", {}, "Cleared"), r.impact.cleared_alerts.map((a) => h("div", { class: "small" }, "• " + a)))) : null,
    r.impact.moved.length ? [h("h3", {}, "Room changes"), table([{ label: "Person", f: (x) => h("a", { href: "#/people/" + x.pid }, `${x.pid} ${x.name}`) }, { label: "From", key: "from" }, { label: "To", key: "to" }], r.impact.moved.slice(0, 60), { maxh: "260px" })] : null,
    r.impact.lost_bed.length ? h("p", { class: "small" }, h("b", {}, "Lost a bed: "), r.impact.lost_bed.slice(0, 40).map((x) => [h("a", { href: "#/people/" + x }, x), " "]), r.impact.lost_bed.length > 40 ? `and ${r.impact.lost_bed.length - 40} more` : "") : null,
    r.impact.heats_moved.length ? [h("h3", {}, "Heats moved"), table([{ label: "Heat", f: (x) => `${x.event} (${x.hid})` }, { label: "From", key: "from" }, { label: "To", key: "to" }], r.impact.heats_moved, { maxh: "260px" })] : null,
    acc.lost_bed && can("accommodation") ? h("div", { class: "note warn", style: { marginTop: "10px" } }, "Every bed is in use on the peak nights, so displaced people wait. Next step: ", h("a", { href: "#/accommodation" }, "contract a property"), " (Director) — nobody already housed will move.") : null);
}

export async function audit(p, q) {
  const d = await api("/audit");
  const tab = q.tab || "log";
  const body = h("div");
  mount(head("Audit & overrides", "Append-only and hash-chained: each entry includes the hash of the previous one, so any edit breaks the chain.",
    h("span", { class: "tag " + (d.chain.ok ? "ok" : "crit"), style: { fontSize: "13px", padding: "4px 10px" } }, d.chain.ok ? `Chain intact · ${d.chain.checked} entries · head ${d.chain.head}` : `Chain broken at #${d.chain.broken_at}`),
    h("a", { class: "btn", href: `/api/export/audit_log.csv?token=${S.token}` }, "Export CSV")),
    tabs([["log", "Log"], ["overrides", `Overrides (${d.overrides.length})`], ["glass", `Break-glass (${d.break_glass.length})`]], tab, "#/audit"), body);
  if (tab === "log") body.append(table([{ label: "#", key: "seq" }, { label: "When", f: (x) => fmt.t(x.ts) }, { label: "Who", f: (x) => `${x.actor} (${x.role})` }, { label: "Action", f: (x) => h("b", {}, x.action.replace(/_/g, " ")) },
    { label: "On", f: (x) => `${x.entity || ""} ${x.entity_id || ""}` }, { label: "Reason", key: "reason" }, { label: "", f: (x) => x.off_shift ? h("span", { class: "tag high" }, "off shift") : "" }], d.log, { maxh: "72vh" }));
  else if (tab === "overrides") body.append(table([{ label: "When", f: (x) => fmt.t(x.ts) }, { label: "Who", key: "actor" }, { label: "Kind", key: "kind" }, { label: "On", key: "entity_id" },
    { label: "Risk", f: (x) => h("span", { class: "tag " + (x.risk === "high" ? "crit" : "") }, x.risk) }, { label: "Rules bent", f: (x) => JSON.parse(x.violations || "[]").join("; ") }, { label: "Reason", key: "reason" }], d.overrides, { empty: "No overrides." }));
  else body.append(table([{ label: "When", f: (x) => fmt.t(x.ts) }, { label: "Who", key: "actor" }, { label: "Record", key: "entity_id" }, { label: "Reason", key: "reason" }], d.break_glass, { empty: "No one has revealed masked data." }));
}

export async function staff() {
  const d = await api("/staff");
  const roles = Object.keys(d.permissions);
  const views = ["control", "participants", "quality", "accommodation", "schedule", "inventory", "merch", "food", "transport", "incidents"];
  const onNow = d.staff.filter((x) => x.on_shift);
  mount(head("Staff & access", `${onNow.length} of ${d.staff.length} core-team members are on shift at ${fmt.hm(d.clock)}. Roles come from core_team.csv.`),
    h("div", { class: "grid2" },
      table([{ label: "ID", key: "uid" }, { label: "Name", key: "name" }, { label: "Role", key: "role" }, { label: "Shift", key: "shift" }, { label: "Phone", key: "phone" },
        { label: "Now", f: (x) => x.on_shift ? h("span", { class: "tag ok" }, "on shift") : h("span", { class: "muted small" }, "off") }], d.staff, { maxh: "75vh" }),
      h("div", {},
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Who can open what"),
          h("div", { class: "tablewrap" }, h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, ""), roles.map((r) => h("th", { class: "small" }, ROLE[r].split(" ")[0])))),
            h("tbody", {}, views.map((v) => h("tr", {}, h("td", {}, v), roles.map((r) => { const p = d.permissions[r].view; const ok = p === "*" || p.includes(v); return h("td", { style: { textAlign: "center", color: ok ? "#0f7f78" : "#c4c9d4" } }, ok ? "●" : "○"); }))))))),
        h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Sensitive fields"),
          h("p", { class: "small" }, "Everyone else sees a mask. Revealing requires a written reason and is visible to the Director."),
          Object.entries(d.fields).map(([f, rs]) => h("div", { class: "small", style: { padding: "3px 0" } }, h("b", {}, f), ": ", rs.map((r) => ROLE[r]).join(", "))),
          h("p", { class: "small muted", style: { marginTop: "8px" } }, "Exports contain no phone numbers or raw medical text. Passwords are PBKDF2-hashed; sessions are signed tokens that expire in 12 h; five failed logins lock an ID for a minute.")))));
}

export async function assumptions() {
  const d = await api("/assumptions");
  const cats = [...new Set(d.assumptions.map((a) => a.category))];
  const isDir = S.user.role_key === "director";
  mount(head("Assumptions", "Everything the system uses that is not in the data pack, and why. Changing one is logged; re-run the affected plan to apply it."),
    cats.map((c) => [h("h2", {}, c), table([{ label: "Setting", f: (a) => h("b", {}, a.key.replace(/_/g, " ")) },
      { label: "Value", f: (a) => h("span", { class: a.changed ? "tag high" : "" }, typeof a.value === "object" ? JSON.stringify(a.value) : String(a.value)) },
      { label: "Why", f: (a) => h("span", { class: "small" }, a.why) },
      { label: "", f: (a) => isDir && typeof a.value !== "object" ? h("button", { class: "btn sm", onclick: () => {
        const inp = h("input", { class: "in", value: a.value });
        withReason(`Change ${a.key.replace(/_/g, " ")}`, a.why, async (reason) => { const v = typeof a.default === "number" ? +inp.value : typeof a.default === "boolean" ? inp.value === "true" : inp.value; await api("/assumptions", { key: a.key, value: v, reason }); toast("Saved. Re-run the plan to apply.", "ok"); render(); }, { extra: h("label", { class: "f" }, "New value", inp) });
      } }, "Change") : "" }], d.assumptions.filter((a) => a.category === c), { wrap: false })]),
    h("h2", {}, "Venue capabilities (inferred)"),
    h("p", { class: "small" }, "venues.csv lists no facilities, so each venue's capabilities are inferred from its name and type and matched against event requirements:"),
    table([{ label: "Venue", key: "v" }, { label: "Assumed to offer", key: "c" }], Object.entries(d.venue_caps).map(([v, c]) => ({ v, c: c.join(", ") })), { wrap: false }));
}
