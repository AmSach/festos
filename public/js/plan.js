import { S, h, s, api, fmt, toast, busy, withReason, mount, head, table, tabs, sevTag, NEED, CAT, can, canW, go, hbar, barsVs, area, scatter, render, drawer, modal } from "./core.js";

// ====================================================================== accommodation
export async function accommodation(p, q) {
  const d = await api("/accommodation");
  const m = d.metrics;
  const tab = q.tab || "plan";
  const body = h("div");
  mount(head("Accommodation", `${fmt.n(m.placed)} of ${fmt.n(m.people_needing_beds)} people housed · ${fmt.inr(m.external_cost)} external of ${fmt.inr(d.budget)} · solver: ${m.solver}`,
    S.user.role_key === "director" ? h("button", { class: "btn", onclick: () => budgetDialog(d.budget) }, "Change budget") : null,
    S.user.role_key === "director" ? h("button", { class: "btn", onclick: () => propertyDialog() }, "Contract a property") : null),
    tabs([["plan", "Plan & trade-offs"], ["floors", "Hostels & properties"], ["waiting", `Waiting (${m.unplaced})`], ["verify", "Verify accessibility"], ["rules", "Rules check"]], tab, "#/accommodation"), body);
  if (q.site) return siteDrawer(q.site);
  if (tab === "plan") {
    const cap = m.capacity, dem = m.demand, pl = m.placed_by_night;
    body.append(h("div", { class: "grid2" },
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Beds needed vs housed, night by night"),
        h("div", { class: "legend" }, h("span", {}, h("i", { style: { background: "#c9cbe6" } }), "needed"), h("span", {}, h("i", { style: { background: "#272566" } }), "housed"), h("span", {}, h("i", { style: { background: "#c81d3a" } }), "total beds")),
        barsVs({ labels: m.nights, series: [{ name: "needed", values: dem, color: "#c9cbe6" }, { name: "housed", values: pl, color: "#272566" }], line: { name: "beds", values: cap, color: "#c81d3a" }, height: 220 }),
        h("p", { class: "small" }, `Peak need ${fmt.n(Math.max(...dem))} vs ${fmt.n(Math.max(...cap))} beds. On the peak night ${fmt.n(Math.max(...pl))} beds are used (${Math.round(100 * Math.max(...pl) / Math.max(...cap))}%). The remainder is lost to rules that are never relaxed: men, women and all-gender rooms are not mixed, accessible beds are reserved, escorts get private rooms.`),
        h("h3", {}, "Who needs a bed, by housing category"),
        barsVs({ labels: m.nights, series: [["M", "#3f7cc7"], ["F", "#b34d8f"], ["X", "#e79a00"], ["S", "#5b6b8c"]].map(([c, col]) => ({ name: CAT[c], values: m.demand_by_cat[c], color: col })), height: 170 }),
        h("div", { class: "legend" }, [["M", "#3f7cc7"], ["F", "#b34d8f"], ["X", "#e79a00"], ["S", "#5b6b8c"]].map(([c, col]) => h("span", {}, h("i", { style: { background: col } }), CAT[c])))),
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Defensible alternatives"),
        h("p", { class: "small muted" }, "Same hard rules, different priorities. Every plan is re-solved, not estimated."),
        d.frontier.length ? [
          table([{ label: "Plan", f: (f) => h("b", {}, f.label) }, { label: "Housed", f: (f) => h("div", { class: "row nowrap" }, hbar(f.placed - 1800, 260, f.key === "balanced" ? "#272566" : "#9a99c9"), h("b", {}, f.placed)) },
            { label: "Waiting", key: "unplaced", right: true }, { label: "Spend", f: (f) => h("span", { class: "nowrap", style: { color: f.budget > d.budget ? "#c81d3a" : "" } }, fmt.lakh(f.external_cost)), right: true },
            { label: "Units / contingent", f: (f) => f.avg_units, right: true },
            { label: "", f: (f) => canW("alloc_override") ? h("button", { class: "btn sm", onclick: () => withReason(`Adopt “${f.label}”`, f.budget > d.budget ? "Exceeds the budget: Director only, recorded as a high-risk override." : "Replaces the current plan. People may change rooms.", async (reason) => { await busy("Re-solving…", () => api("/accommodation/adopt", { key: f.key, reason })); toast("Plan adopted", "ok"); render(); }) }, "Adopt") : "" }], d.frontier, { wrap: false })] :
          h("p", { class: "muted" }, d.frontier_running ? "Computing alternatives in the background… refresh in a minute." : "No alternatives computed yet."),
        h("div", { class: "note small", style: { marginTop: "10px" } }, frontierInsight(d.frontier)),
        h("h3", {}, "How the optimiser prices soft goals"),
        h("dl", { class: "kv small" }, Object.entries(d.weights).map(([k, v]) => [h("dt", {}, k.replace(/_inr|_/g, " ").trim()), h("dd", {}, typeof v === "number" && v > 50 ? fmt.inr(v) : v)])))));
  } else if (tab === "floors") {
    const camp = d.sites.filter((x) => x.kind === "campus");
    const ext = d.sites.filter((x) => x.kind === "external");
    const blocks = [...new Set(camp.map((x) => x.block))];
    const proposed = camp.filter((x) => x.designation === "X");
    const grid = h("div", { class: "floors" }, h("div"), blocks.map((b) => h("div", { class: "h" }, b)));
    [3, 2, 1, 0].forEach((f) => {
      grid.appendChild(h("div", { class: "fl" }, `Floor ${f}`));
      blocks.forEach((b) => {
        const x = camp.find((c) => c.block === b && c.floor === f);
        if (!x) return grid.appendChild(h("div"));
        const peak = Math.max(...x.load);
        grid.appendChild(h("div", { class: "floor " + x.designation + (x.status !== "open" ? " closed" : ""), onclick: () => siteDrawer(x.site_id), title: Object.entries(x.contingents).map(([c, n]) => `${c}: ${n}`).join(", ") },
          h("div", { class: "d" }, x.status !== "open" ? "Closed" : x.designation === "X" ? "All-gender" + (x.designation_locked ? " ✓" : " (proposed)") : x.designation === "M" ? "Men" : "Women"),
          h("div", { class: "small muted" }, `${peak}/${x.beds} peak · ${x.acc_beds} acc.`), h("div", { class: "bar" }, h("i", { style: { width: Math.min(100, 100 * peak / Math.max(1, x.beds)) + "%" } }))));
      });
    });
    body.append(h("div", { class: "grid2" },
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Campus hostels"),
        h("p", { class: "small" }, `About 700 people a night are non-binary, prefer not to say, or left gender blank. FestOS never places them in a binary block. The optimiser proposes which boys'-block floors to run as all-gender floors (max ${d.weights.max_all_gender_floors}); the Director approves.`),
        grid,
        proposed.some((x) => !x.designation_locked) && S.user.role_key === "director" ? h("button", { class: "btn primary", style: { marginTop: "12px" }, onclick: () => withReason(`Approve ${proposed.length} all-gender floors`, proposed.map((x) => x.site_id).join(", ") + " — signage and warden briefing required.", async (reason) => { await api("/accommodation/floors", { approve_all: true, reason }); toast("Approved", "ok"); render(); }) }, "Approve proposed all-gender floors") : null),
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "External properties"),
        table([{ label: "Property", f: (x) => h("a", { href: "#/accommodation?site=" + x.site_id }, x.name) }, { label: "km", key: "distance", right: true }, { label: "₹/night", key: "cost", right: true },
          { label: "Peak use", f: (x) => h("div", { class: "row" }, hbar(Math.max(...x.load), x.beds), `${Math.max(...x.load)}/${x.beds}`) },
          { label: "Transport", f: (x) => x.transport === "yes" ? "regular" : h("span", { class: "tag high" }, x.transport) },
          { label: "Step-free", f: (x) => d.verified[x.site_id] ? h("span", { class: "tag ok" }, `${d.verified[x.site_id]} verified`) : h("span", { class: "tag" }, "unverified") },
          { label: "Status", f: (x) => x.status === "open" ? "" : h("span", { class: "tag crit" }, x.status) }], ext, { wrap: false }),
        h("h3", {}, "Check-in pressure"),
        h("p", { class: "small" }, `${fmt.n(d.early_checkins.arrive_before_checkin)} housed people arrive before the 12:00 check-in. ` +
          (Object.keys(d.early_checkins.turnover_conflicts).length ? `${Object.values(d.early_checkins.turnover_conflicts).reduce((a, b) => a + b, 0)} of them take a bed vacated the same morning — they need the luggage room until cleaning is done.` : "")))));
  } else if (tab === "waiting") {
    const byreason = {};
    d.unplaced.forEach((u) => { const k = u.access_need === "WC" ? "Wheelchair — no accessible bed" : ["GF", "UNKNOWN"].includes(u.access_need) ? "Ground floor / blank need" : u.kind === "leader" ? "Tentative leader hold" : `${CAT[u.housing_cat]} pool full`; byreason[k] = (byreason[k] || 0) + 1; });
    body.append(h("div", { class: "note warn", style: { marginBottom: "12px" } }, h("b", {}, `${d.unplaced.length} people are waiting. `), Object.entries(byreason).map(([k, v]) => `${k}: ${v}`).join(" · "),
      h("div", {}, "Options: contract a property (Director), verify step-free rooms at a property, or confirm with leaders who is a day participant.")),
      table([{ label: "ID", f: (u) => h("a", { href: "#/people/" + u.pid }, u.pid) }, { label: "Name", key: "name" }, { label: "Contingent", key: "cid" }, { label: "Kind", key: "kind" },
        { label: "Housing", f: (u) => CAT[u.housing_cat] }, { label: "Need", f: (u) => u.access_need !== "NONE" ? NEED[u.access_need] : "" },
        { label: "Nights", key: "nights_label" }, { label: "Why", f: (u) => h("span", { class: "small" }, u.explain.join(" ")) }], d.unplaced, { maxh: "70vh" }));
  } else if (tab === "verify") {
    const ext = d.sites.filter((x) => x.kind === "external");
    const sel = h("select", { class: "in" }, ext.map((x) => h("option", { value: x.site_id }, x.name)));
    const n = h("input", { class: "in", type: "number", min: 0, value: 40, style: { width: "100px" } });
    body.append(h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Record verified step-free / accessible beds"),
      h("p", {}, `Wheelchair and ground-floor needs are hard rules. Campus has ${d.sites.filter((x) => x.kind === "campus").reduce((a, x) => a + x.acc_beds, 0)} accessible beds; ${m.unplaced_wheelchair} wheelchair users are waiting. When Hospitality confirms accessible rooms at a property, people with hard needs take precedence there — anyone moved out is re-housed if possible and told why.`),
      h("div", { class: "row" }, h("label", { class: "f" }, "Property", sel), h("label", { class: "f" }, "Verified accessible beds", n),
        canW("verify_property") ? h("button", { class: "btn primary", style: { alignSelf: "flex-end" }, onclick: () => withReason("Record verification", "How was this verified? (call, visit, photos)", async (reason) => {
          const r = await busy("Re-planning around hard needs…", () => api("/accommodation/verify", { site_id: sel.value, accessible_beds: +n.value, reason }));
          toast(`${r.hard_need_placed} people with accessibility needs housed; ${r.evicted} others moved (${r.evicted_rehoused} re-housed).`, "ok"); render();
        }) }, "Verify and re-plan") : null)));
  } else if (tab === "rules") {
    const hc = m.hard_constraints;
    const pr = m.preferences || {};
    body.append(h("div", { class: "grid2e" },
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Hard rules (never traded for score)"),
        rule("Gender pools respected (no one outside the binary in a binary block)", hc.gender_pool === 0),
        rule("Wheelchair users only in accessible beds", hc.accessibility === 0, `${m.unplaced_wheelchair} wait rather than be placed unsafely`),
        rule("Ground-floor / blank-need people only on floor 0 or verified step-free", hc.accessibility === 0),
        rule("Room capacity on every night, one housing category per room per night", hc.capacity === 0),
        rule("Minors never at a property without regular transport", hc.minors_limited_transport === 0, `${m.minors_off_campus} minors off campus`),
        rule("External spend within budget", hc.budget === 0, `${fmt.inr(m.external_cost)} of ${fmt.inr(m.budget)}`),
        rule("Leaders housed in a unit their group uses", m.leaders_near_group[0] === m.leaders_near_group[1], `${m.leaders_near_group[0]}/${m.leaders_near_group[1]}`),
        rule("Escorts in a private staff room, at the guest house or with their group", m.escorts_nearby[0] === m.escorts_nearby[1], `${m.escorts_nearby[0]}/${m.escorts_nearby[1]}`)),
      h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Preferences honoured (soft)"),
        Object.entries(pr).map(([k, v]) => h("div", { class: "row", style: { padding: "5px 0" } }, h("span", { style: { width: "160px" } }, k.replace("_", " ")), hbar(v.pct, 100, v.pct > 75 ? "#0f7f78" : v.pct > 45 ? "#e79a00" : "#c81d3a"), h("b", {}, v.pct + "%"), h("span", { class: "small muted" }, `${v.ok}/${v.of}`))),
        h("p", { class: "small muted", style: { marginTop: "10px" } }, `Contingents are split across ${m.avg_units} blocks/properties on average. Most splits are forced by the rules above: a mixed contingent needs a men's block, a women's block and an all-gender floor at minimum.`),
        h("p", { class: "small muted" }, `Solved with ${fmt.n(m.n_vars)} variables and ${fmt.n(m.n_rows)} constraints in ${m.solve_seconds}s.`))));
  }
}
function rule(text, ok, note) {
  return h("div", { class: "row", style: { padding: "6px 0", borderBottom: "1px solid #eef0f4" } }, h("span", { class: "tag " + (ok ? "ok" : "crit") }, ok ? "Held" : "Broken"), h("span", { class: "sp" }, text), note ? h("span", { class: "small muted" }, note) : null);
}
function frontierInsight(fr) {
  if (!fr || fr.length < 3) return "";
  const t = fr.find((f) => f.key === "tight"), b = fr.find((f) => f.key === "balanced"), o = fr.find((f) => f.key === "override");
  if (!t || !b || !o) return "";
  const per = (b.placed - t.placed) / ((b.external_cost - t.external_cost) / 100000 || 1);
  const per2 = (o.placed - b.placed) / ((o.external_cost - b.external_cost) / 100000 || 1);
  return `Below the budget each extra lakh houses about ${per.toFixed(0)} people. Above it, only about ${per2.toFixed(1)} — beds, not money, are the bottleneck. Contracting another property beats raising the budget.`;
}
async function siteDrawer(sid) {
  const d = await api("/accommodation/site/" + sid);
  const night = h("select", { class: "in" }, d.nights.map((n, i) => h("option", { value: i, selected: i === 1 ? true : null }, "Night of " + n)));
  const grid = h("div", { class: "rooms" });
  const draw = () => {
    const n = +night.value;
    grid.replaceChildren(...d.rooms.map((r) => {
      const occ = r.occupants.filter((o) => o.nights_list.includes(n));
      return h("div", { class: "room" + (r.accessible ? " acc" : "") }, h("b", {}, r.room_id), r.accessible ? h("span", { class: "tag ok", style: { marginLeft: "4px" } }, "acc") : null,
        h("div", { class: "muted" }, `${occ.length}/${r.capacity}`),
        occ.map((o) => h("div", {}, h("a", { href: "#/people/" + o.pid }, o.name.split(" ")[0]), ` ${o.cid}`, o.access_need === "WC" ? " ♿" : "", o.kind !== "participant" ? h("span", { class: "muted" }, ` (${o.kind})`) : null)));
    }));
  };
  night.onchange = draw;
  draw();
  drawer(h("div", {}, h("h1", {}, d.site.name), h("p", { class: "muted" }, `${d.site.site_id} · ${d.site.beds} beds · ${d.site.kind === "campus" ? "designation " + d.site.designation : "₹" + d.site.cost + "/night, " + d.site.distance + " km"}`),
    h("div", { class: "row" }, night), h("div", { style: { marginTop: "12px" } }, grid)), () => { if (location.hash.includes("site=")) history.back(); });
}
function budgetDialog(b) {
  const inp = h("input", { class: "in", type: "number", value: b, step: 50000 });
  withReason("Change external budget", "The plan re-solves with the new limit.", async (reason) => {
    await busy("Re-solving…", () => api("/accommodation/budget", { budget: +inp.value, reason })); toast("Budget changed and plan re-solved", "ok"); render();
  }, { extra: h("label", { class: "f" }, "Budget (₹)", inp) });
}
function propertyDialog() {
  const f = { name: h("input", { class: "in", value: "Hotel Sayaji (emergency block)" }), beds: h("input", { class: "in", type: "number", value: 90 }), cost: h("input", { class: "in", type: "number", value: 1700 }),
    distance: h("input", { class: "in", type: "number", value: 4 }), accessible_beds: h("input", { class: "in", type: "number", value: 0 }), budget_increase: h("input", { class: "in", type: "number", value: 300000 }) };
  withReason("Contract a property", "Adds emergency capacity and re-houses people who are waiting — nobody already housed moves.", async (reason) => {
    const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.type === "number" ? +v.value : v.value]));
    const r = await busy("Housing people who are waiting…", () => api("/accommodation/property", { ...body, reason }));
    toast(`${r.site_id} added: ${r.housed} people housed, ${r.still_unplaced} still waiting`, "ok"); render();
  }, { extra: h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "8px" } },
    Object.entries(f).map(([k, v]) => h("label", { class: "f" }, { name: "Name", beds: "Beds", cost: "₹ per bed-night", distance: "Distance (km)", accessible_beds: "Verified accessible beds", budget_increase: "Budget increase (₹)" }[k], v))) });
}

// ====================================================================== schedule
const EV_COLORS = ["#272566", "#3f7cc7", "#0f7f78", "#8a4fbf", "#b34d8f", "#a66d00", "#2f6fb3", "#5b6b8c", "#3a7d3a", "#9a3b3b"];
export async function schedulePage(p, q) {
  const d = await api("/schedule");
  const tab = q.tab || "grid";
  const body = h("div");
  const conf = d.conflicts;
  const nconf = conf.filter((c) => ["critical", "high"].includes(c.severity)).reduce((a, c) => a + c.count, 0);
  mount(head("Events & venues", `${d.heats.length} heats across ${d.venues.length} venues. Built by ${d.meta.iterations ? fmt.n(d.meta.iterations) + " simulated-annealing moves" : "the scheduler"}; checked by an independent validator.`,
    canW("schedule_edit") ? h("button", { class: "btn", onclick: () => withReason("Re-optimise the schedule", "Minimal change: heats are pulled toward their current slot; only infeasible ones move.", async (reason) => { const r = await busy("Re-optimising (≈8 s)…", () => api("/schedule/repair", { reason })); toast(`${r.moved_heats.length} heats moved, ${r.participants_reassigned.length} people re-seated`, "ok"); render(); }) }, "Re-optimise") : null),
    tabs([["grid", "Venue timeline"], ["conflicts", `Conflicts (${nconf})`], ["events", "Events"], ["volunteers", "Volunteers"]], tab, "#/schedule"), body);
  if (tab === "grid") {
    const days = ["2026-10-30", "2026-10-31", "2026-11-01"];
    const day = q.day || (S.clock && days.includes(S.clock.slice(0, 10)) ? S.clock.slice(0, 10) : days[0]);
    body.append(h("div", { class: "row", style: { marginBottom: "10px" } }, days.map((x) => h("a", { class: "btn sm" + (x === day ? " primary" : ""), href: `#/schedule?day=${x}` + (q.event ? `&event=${q.event}` : "") }, fmt.day(x))),
      h("span", { class: "sp" }), q.event ? h("span", {}, "Highlighting " + q.event + " ", h("a", { href: "#/schedule?day=" + day }, "clear")) : h("span", { class: "small muted" }, "Click a heat for competitors, judges, kit and a move dialog.")),
      gantt(d, day, q.event, q.venue));
  } else if (tab === "conflicts") {
    body.append(h("p", {}, "The validator re-checks the stored schedule from scratch after every change: venue windows and closures, double booking with changeover, round order, shared judging panels, judges across events, competitors (with walking time), presence on site, team integrity, equipment and volunteers."),
      table([{ label: "Severity", f: (c) => sevTag(c.severity) }, { label: "Type", f: (c) => c.kind.replace(/_/g, " ") }, { label: "Count", key: "count", right: true }, { label: "Detail", f: (c) => h("span", {}, c.message, c.kind === "participant_clash" || c.kind === "participant_absent" ? h("div", { class: "small muted" }, "e.g. ", c.refs.slice(0, 8).map((r) => [h("a", { href: "#/people/" + r }, r), " "])) : null) }], conf),
      h("div", { class: "note warn", style: { marginTop: "12px" } }, `${fmt.n(d.late_arrival_regs)} registrations belong to people who arrive at 22:00 on 31 Oct. They can only compete on 1 Nov, and 1 Nov cannot also host prelims, semis and finals for every event. The scheduler gives late arrivals a 1 Nov heat where the finals still fit; the rest are listed above as a decision for the Events lead (ask those contingents to arrive earlier).`));
  } else if (tab === "events") {
    body.append(table([
      { label: "Event", f: (e) => [h("a", { href: `#/schedule?event=${e.eid}` }, h("b", {}, e.name)), h("div", { class: "small muted" }, `${e.eid} · ${e.type} · ${e.rounds.join(" → ") || "single"} · ${e.duration} min`)] },
      { label: "Regs", key: "registrations", right: true },
      { label: "Needs", f: (e) => e.reqs.join(", ") },
      { label: "Fits in", f: (e) => e.eligible_venues.join(" ") },
      { label: "Kit per heat", f: (e) => [Object.entries(e.equipment).map(([k, v]) => h("span", { class: "tag" + (e.waived_equipment.includes(k) ? "" : "") }, `${v} ${k}` + (e.waived_equipment.includes(k) ? " (waived)" : ""))), ""] },
      { label: "Judges", f: (e) => h("span", { class: "small" }, e.judges.map((j) => j.name).join(", "), e.judges.some((j) => j.sample) ? h("span", { class: "muted" }, " (sample)") : null) },
      { label: "Flags", f: (e) => e.flags.map((f) => h("div", { class: "small", style: { color: "#8a5a00" } }, f)) },
      { label: "", f: (e) => canW("schedule_edit") && Object.keys(e.equipment).some((k) => !e.waived_equipment.includes(k)) ? h("button", { class: "btn sm", onclick: () => waiveDialog(e) }, "Waive kit…") : "" },
    ], d.events, { maxh: "75vh" }));
  } else if (tab === "volunteers") {
    const v = d.volunteer_curve;
    const ticks = [0, 1, 2].map((i) => ({ i: i * 96 + 48, label: ["30 Oct", "31 Oct", "1 Nov"][i] }));
    body.append(h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, "Volunteers needed at once"),
      area(v, { cap: d.volunteer_pool, capLabel: `assumed pool ${d.volunteer_pool}`, ticks, height: 180, width: 900 }),
      h("p", {}, `Peak ${Math.max(...v)} volunteers at the same time. core_team.csv lists only 6 people in the Volunteer role; the pool of ${d.volunteer_pool} is an assumption to confirm and roster by shift.`)));
  }
}
function gantt(d, day, hl, hv) {
  const t0 = new Date(day + "T07:30"), t1 = new Date(day + "T22:30");
  const W = 1500, pxm = W / ((t1 - t0) / 60000);
  const X = (s) => ((new Date(s.replace(" ", "T")) - t0) / 60000) * pxm;
  const evIdx = Object.fromEntries(d.events.map((e, i) => [e.eid, i]));
  const bad = new Set(d.conflicts.filter((c) => ["critical", "high"].includes(c.severity) && !["participant_clash", "equipment_shortage", "participant_absent"].includes(c.kind)).flatMap((c) => c.refs));
  const g = h("div", { class: "gantt" });
  const hours = h("div", { class: "lane", style: { width: W + "px", height: "22px" } });
  for (let hr = 8; hr <= 22; hr++) hours.appendChild(h("div", { class: "hr", style: { left: X(`${day} ${String(hr).padStart(2, "0")}:00`) + "px" } }, `${hr}:00`));
  g.appendChild(h("div", { class: "hours" }, h("div", { class: "vlab" }, "Venue"), hours));
  d.venues.forEach((v) => {
    const lane = h("div", { class: "lane", style: { width: W + "px" } });
    const win = v.windows.find((w) => w[0].startsWith(day));
    if (win) {
      lane.appendChild(h("div", { class: "off", style: { left: 0, width: Math.max(0, X(win[0])) + "px" } }));
      lane.appendChild(h("div", { class: "off", style: { left: X(win[1]) + "px", right: 0 } }));
    }
    v.blocks.forEach((b) => { if (b[0].slice(0, 10) <= day && b[1].slice(0, 10) >= day) lane.appendChild(h("div", { class: "closed", title: "Closed: " + (b[2] || ""), style: { left: Math.max(0, X(b[0])) + "px", width: Math.max(0, Math.min(W, X(b[1])) - Math.max(0, X(b[0]))) + "px" } })); });
    d.heats.filter((x) => x.vid === v.vid && x.start.startsWith(day)).forEach((x) => {
      const dim = hl && x.eid !== hl;
      lane.appendChild(h("div", { class: "hb" + (bad.has(x.hid) ? " bad" : ""), title: `${x.ev} · ${x.round} · ${fmt.hm(x.start)}–${fmt.hm(x.end)} · ${x.members} assigned`,
        style: { left: X(x.start) + "px", width: Math.max(18, X(x.end) - X(x.start)) + "px", background: EV_COLORS[evIdx[x.eid] % EV_COLORS.length], opacity: dim ? 0.18 : 1 },
        onclick: () => heatDrawer(x.hid) }, h("b", {}, x.ev), h("br"), `${x.round} · ${x.members}`));
    });
    g.appendChild(h("div", { class: "grow", style: hv === v.vid ? { background: "#fffbf2" } : null }, h("div", { class: "vlab" }, v.name, h("small", {}, `${v.capacity} seats · ${v.indoor ? "indoor" : "outdoor"}`)), lane));
  });
  if (S.clock && S.clock.startsWith(day)) g.appendChild(h("div", { class: "nowv", style: { left: 150 + X(S.clock) + "px" } }));
  return g;
}
async function heatDrawer(hid) {
  const d = await api("/schedule/heat/" + hid);
  const x = d.heat;
  const absent = d.members.filter((m) => !m.present).length;
  const vsel = h("select", { class: "in" }, (await api("/schedule")).venues.map((v) => h("option", { value: v.vid, selected: v.vid === x.vid ? true : null }, `${v.name} (${v.capacity})`)));
  const st = h("input", { class: "in", value: x.start });
  const res = h("div");
  const dry = async () => {
    const r = await api(`/schedule/heat/${hid}/move`, { vid: vsel.value, start: st.value, dry_run: true });
    res.replaceChildren(h("div", { class: "note " + (r.conflicts.some((c) => c.severity === "critical") ? "crit" : Object.values(r.delta).some((v) => v > 0) ? "warn" : "") },
      Object.keys(r.delta).length ? Object.entries(r.delta).map(([k, v]) => h("div", {}, `${k.replace(/_/g, " ")}: ${v > 0 ? "+" : ""}${v}`)) : "No new conflicts.",
      r.conflicts.map((c) => h("div", { class: "small" }, c.message))));
  };
  drawer(h("div", {},
    h("h1", {}, `${x.ev} · ${x.round}`), h("p", { class: "muted" }, `${x.hid} · ${x.venue} (${x.vcap} seats) · ${fmt.day(x.start)} ${fmt.hm(x.start)}–${fmt.hm(x.end)}`),
    d.conflicts.length ? h("div", { class: "note crit" }, d.conflicts.map((c) => h("div", {}, c.message))) : null,
    h("dl", { class: "kv" },
      h("dt", {}, "Competitors"), h("dd", {}, `${d.members.length} assigned` + (x.round === "semis" || x.round === "finals" ? ` (pool of ${x.pool} decided by results)` : "") + (absent ? ` · ${absent} not on site` : "")),
      h("dt", {}, "Judges"), h("dd", {}, d.judges.map((j) => j.name + (j.sample ? " (sample)" : "")).join(", ") || "—"),
      h("dt", {}, "Kit"), h("dd", {}, d.reservations.length ? d.reservations.map((r) => h("div", {}, `${r.qty}/${r.need} ${r.item}`, r.qty < r.need ? h("span", { class: "tag crit" }, ` short ${r.need - r.qty}`) : null, h("span", { class: "muted small" }, " from " + r.sources.map((s) => `${s[0]} (${s[1]})`).join(", ")))) : "none"),
      h("dt", {}, "Volunteers"), h("dd", {}, x.volunteers)),
    canW("schedule_edit") ? h("section", { class: "panel", style: { marginTop: "14px" } }, h("h2", { style: { marginTop: 0 } }, "Move this heat"),
      h("div", { class: "row" }, h("label", { class: "f" }, "Venue", vsel), h("label", { class: "f" }, "Start", st), h("button", { class: "btn", style: { alignSelf: "flex-end" }, onclick: dry }, "Check impact"),
        h("button", { class: "btn primary", style: { alignSelf: "flex-end" }, onclick: () => withReason("Move heat", "Competitors are re-seated automatically; anyone affected is listed for notification.", async (reason) => {
          const r = await busy("Moving and re-seating…", () => api(`/schedule/heat/${hid}/move`, { vid: vsel.value, start: st.value, reason })); toast(`Moved. ${r.reassigned} competitors re-seated.`, "ok"); render();
        }) }, "Move")), res) : null,
    h("h2", {}, "Competitors"),
    table([{ label: "ID", f: (m) => h("a", { href: "#/people/" + m.pid }, m.pid) }, { label: "Name", key: "name" }, { label: "Contingent", key: "cid" }, { label: "Team", f: (m) => m.team_id || "" },
      { label: "On site", f: (m) => m.present ? "yes" : h("span", { class: "tag crit" }, "no") }], d.members, { maxh: "45vh" })));
}
function waiveDialog(e) {
  const sel = h("select", { class: "in" }, Object.keys(e.equipment).filter((k) => !e.waived_equipment.includes(k)).map((k) => h("option", { value: k }, k)));
  withReason(`Waive a kit requirement for ${e.name}`, "Use when the event can run without it (e.g. phone cameras instead of repaired ones). Logged as an override.", async (reason) => {
    await api(`/events/${e.eid}/waive`, { item: sel.value, reason }); toast("Waived", "ok"); render();
  }, { extra: h("label", { class: "f" }, "Item", sel) });
}

// ====================================================================== logistics
export async function inventory(p, q) {
  const d = await api("/inventory");
  const tab = q.tab || "stock";
  const body = h("div");
  mount(head("Logistics", "Stock by state and place. Only 'good' stock that is not on a van counts as usable; 'needs check' must be inspected first."),
    tabs([["stock", "Stock"], ["moves", `Movement plan (${d.plan.n_moves})`], ["short", `Shortfalls (${d.plan.shortages.length})`], ["log", "Change log"]], tab, "#/inventory"), body);
  if (tab === "stock") {
    body.append(table([{ label: "Item", f: (x) => h("b", {}, x.item) }, { label: "Usable", f: (x) => h("b", { style: { color: x.available < x.peak_need ? "#c81d3a" : "" } }, x.available), right: true },
      { label: "Needs check", key: "inspect", right: true }, { label: "Under repair", key: "repair", right: true }, { label: "On van (no ETA)", key: "transit", right: true },
      { label: "Peak need at once", key: "peak_need", right: true },
      { label: "Lines", f: (x) => h("div", { class: "small" }, x.lines.map((l) => h("div", {}, `${l.line_id}: ${l.qty} @ ${l.location} — ${l.state}`,
        canW("inventory_edit") ? h("button", { class: "btn ghost sm", onclick: () => stockDialog(l) }, "change") : null))) }], d.items, { maxh: "75vh" }));
  } else if (tab === "moves") {
    body.append(h("p", {}, "Generated from the schedule: kit stays at a venue for the next heat when it can; otherwise it is pulled from the store holding the most. Each line is a pick for the logistics team."),
      table([{ label: "Deliver by", f: (m) => `${fmt.day(m.by)} ${fmt.hm(m.by)}` }, { label: "Item", key: "item" }, { label: "Qty", key: "qty", right: true }, { label: "From", key: "from" }, { label: "To", key: "to" }, { label: "For", key: "for" }], d.plan.moves));
  } else if (tab === "short") {
    body.append(table([{ label: "When", f: (m) => `${fmt.day(m.start)} ${fmt.hm(m.start)}` }, { label: "Event", f: (m) => `${m.event} (${m.hid})` }, { label: "Item", key: "item" }, { label: "Needs", key: "need", right: true }, { label: "Missing", f: (m) => h("span", { class: "tag crit" }, m.missing), right: true }, { label: "Venue", key: "venue" }], d.plan.shortages,
      { empty: "Every heat has its kit." }));
  } else {
    body.append(table([{ label: "When", f: (m) => fmt.t(m.ts) }, { label: "Line", key: "line_id" }, { label: "Item", key: "item" }, { label: "Qty", key: "qty" }, { label: "Change", f: (m) => `${m.from_state} → ${m.to_state}` + (m.from_loc !== m.to_loc ? ` · ${m.from_loc} → ${m.to_loc}` : "") }, { label: "By", key: "actor" }, { label: "Reason", key: "reason" }], d.log, { empty: "No stock changes yet." }));
  }
}
function stockDialog(l) {
  const st = h("select", { class: "in" }, ["available", "inspect", "repair", "transit"].map((x) => h("option", { value: x, selected: x === l.state ? true : null }, { available: "usable", inspect: "needs check", repair: "under repair", transit: "in transit" }[x])));
  const qty = h("input", { class: "in", type: "number", value: l.qty, min: 1, max: l.qty });
  const loc = h("select", { class: "in" }, ["", "Store Room A", "Registration Desk", "Main Stage", "Sports Complex", "LT-1", "LT-2", "Amphitheatre", "Seminar Hall", "Library Hall", "Van 1"].map((x) => h("option", { value: x }, x || "(same place)")));
  withReason(`${l.item} · ${l.line_id}`, `${l.qty} at ${l.location}, ${l.state}. Change part or all of the line.`, async (reason) => {
    await api(`/inventory/line/${l.line_id}/state`, { qty: +qty.value, state: st.value, to_loc: loc.value || null, reason }); toast("Stock updated", "ok"); render();
  }, { extra: h("div", { class: "row", style: { marginBottom: "8px" } }, h("label", { class: "f" }, "Quantity", qty), h("label", { class: "f" }, "New state", st), h("label", { class: "f" }, "Move to", loc)) });
}

// ====================================================================== merchandise
export async function merch(p, q) {
  const d = await api("/merch");
  const tab = q.tab || "desk";
  const body = h("div");
  mount(head("Merchandise", "Each entitled item is issued once per person. Second claims are blocked at any desk, with who, where and when."),
    tabs([["desk", "Issue desk"], ["stock", "Stock vs demand"], ["fair", "Fair share"], ["blocked", `Blocked claims (${d.blocked.length})`]], tab, "#/merch"), body);
  if (tab === "desk") {
    const pid = h("input", { class: "in", placeholder: "Participant ID", style: { fontSize: "18px", width: "220px" } });
    const item = h("select", { class: "in", style: { fontSize: "16px" } }, d.kit.map((k) => h("option", {}, k)));
    const sub = h("input", { type: "checkbox" });
    const out = h("div");
    const issue = async () => {
      const r = await api("/merch/issue", { pid: pid.value, item: item.value, allow_sub: sub.checked, desk: "Merch desk" });
      out.replaceChildren(h("div", { class: "note " + (r.ok ? "" : r.code === "DUPLICATE_CLAIM" ? "crit" : "warn"), style: { fontSize: "15px" } },
        r.ok ? h("b", {}, `Issued ${r.item}${r.size !== "NA" ? " " + r.size : ""} to ${r.name}. ${r.left} left in that size.`) : [h("b", {}, r.message), r.substitutes && r.substitutes.length ? h("div", {}, `Available one size up: ${r.substitutes.join(", ")} — tick “offer one size up” if the participant agrees.`) : null]));
      if (r.ok) { pid.value = ""; pid.focus(); }
    };
    pid.addEventListener("keydown", (e) => { if (e.key === "Enter") issue(); });
    body.append(h("section", { class: "panel" }, h("div", { class: "row" }, pid, item, h("label", { class: "row small" }, sub, "offer one size up (with consent)"),
      canW("merch_issue") ? h("button", { class: "btn primary", onclick: issue }, "Issue") : h("span", { class: "muted" }, "Your role can't issue.")), out),
      h("h2", {}, "Recent"), table([{ label: "When", f: (r) => fmt.t(r.ts) }, { label: "Who", f: (r) => [h("a", { href: "#/people/" + r.pid }, r.pid), " ", r.name] }, { label: "Item", f: (r) => `${r.item} ${r.size !== "NA" ? r.size : ""}` }, { label: "Desk", key: "desk" }, { label: "Staff", key: "actor" }, { label: "", f: (r) => r.voided ? h("span", { class: "tag" }, "voided") : r.note || "" }], d.recent, { empty: "Nothing issued yet." }));
  } else if (tab === "stock") {
    body.append(table([{ label: "Item", f: (l) => h("b", {}, l.item) }, { label: "Size", key: "size" }, { label: "Quantity", key: "qty", right: true }, { label: "Reserved", key: "reserved", right: true }, { label: "Issued", key: "issued", right: true },
      { label: "On hand", key: "on_hand", right: true }, { label: "Entitled", f: (l) => l.entitled ? fmt.n(l.demand) : h("span", { class: "muted small" }, "no rule"), right: true },
      { label: "Gap", f: (l) => l.gap === null ? "" : h("span", { class: "tag " + (l.gap < 0 ? "crit" : "ok") }, (l.gap > 0 ? "+" : "") + fmt.n(l.gap)), right: true },
      { label: "Runs out", f: (l) => d.stockout[`${l.item}|${l.size}`] ? h("b", { style: { color: "#c81d3a" } }, `${fmt.day(d.stockout[l.item + "|" + l.size])} ${fmt.hm(d.stockout[l.item + "|" + l.size])}`) : "" }], d.lines),
      h("p", { class: "small muted", style: { marginTop: "8px" } }, "Runs out = the arrival at which stock is exhausted if kits are handed out in arrival order. Totes and special badges have no eligibility rule in the pack and are not auto-issued."));
  } else if (tab === "fair") {
    body.append(h("p", {}, "When a size is short, each contingent gets a quota proportional to its demand (largest-remainder method) so early arrivals don't empty a size for everyone else."),
      ...Object.entries(d.fair_share).map(([size, f]) => h("section", { class: "panel" }, h("h2", { style: { marginTop: 0 } }, `T-shirt ${size}: ${f.available} for ${f.demand}`),
        h("div", { style: { columns: 4, fontSize: "13px" } }, Object.entries(f.quota).sort().map(([c, n]) => h("div", {}, h("b", {}, c), ` ${n}`))))));
  } else {
    body.append(table([{ label: "Blocked at", f: (b) => fmt.t(b.ts) }, { label: "Person", f: (b) => [h("a", { href: "#/people/" + b.pid }, b.pid), " ", b.name] }, { label: "Item", key: "item" }, { label: "Desk / staff", f: (b) => `${b.desk} · ${b.actor}` },
      { label: "Original issue", f: (b) => `${fmt.t(b.orig_ts)} at ${b.orig_desk} by ${b.orig_actor}` }], d.blocked, { empty: "No duplicate claims so far." }));
  }
}

// ====================================================================== food
export async function food(p, q) {
  const d = await api("/food");
  const tab = q.tab || "cover";
  const body = h("div");
  mount(head("Food vendors", "Coverage is solved per meal: the most restricted diets are routed to compliant counters first and can never be displaced."),
    tabs([["cover", "Diet coverage"], ["vendors", "Vendors & compliance"], ["settle", "Settlement"]], tab, "#/food"), body);
  if (tab === "cover") {
    const diets = d.priority.filter((x) => d.meals.some((m) => m.diets[x]));
    const cell = (x) => {
      if (!x) return h("td", { class: "c muted" }, "—");
      const r = x.demand ? x.served / x.demand : 1;
      const bg = x.gap === 0 ? "#dff2f0" : r > 0.7 ? "#fdf2d9" : "#fbe7ea";
      return h("td", { class: "c", style: { background: bg }, title: `${x.served}/${x.demand} served · vendors: ${x.vendors.join(", ") || "none"}` }, x.gap ? `−${x.gap}` : "✓", h("div", { class: "small muted" }, `${x.served}/${x.demand}`));
    };
    body.append(h("div", { class: "tablewrap" }, h("table", { class: "t heat" },
      h("thead", {}, h("tr", {}, h("th", {}, "Meal"), h("th", {}, "Head-count"), diets.map((x) => h("th", {}, d.labels[x] + (d.safety.includes(x) ? " ⚠" : ""))))),
      h("tbody", {}, d.meals.map((m) => h("tr", {}, h("td", {}, h("b", {}, fmt.day(m.date)), " " + m.name), h("td", { class: "right" }, fmt.n(m.headcount)), diets.map((x) => cell(m.diets[x]))))))),
      h("div", { class: "grid2e", style: { marginTop: "14px" } },
        h("div", { class: "note crit" }, h("b", {}, "Safety gaps. "), "Nut allergy: no vendor declares a nut-free kitchen. Jain: the Jain thali vendor isn't tagged Jain and the Jain-tagged vendor's certificate expired. Gluten-free: one vendor, certificate ends 31 Oct. Blank diets are fed from vegetarian counters and never treated as cleared."),
        h("div", { class: "note warn" }, h("b", {}, "Outside catering days. "), Object.entries(d.uncatered).map(([k, v]) => `${fmt.day(k.slice(0, 10))} ${k.slice(11)}: ${v}`).join(" · "), h("div", { class: "small" }, "People arrive from 29 Oct and leave up to 3 Nov; vendors are contracted 30 Oct–1 Nov."))));
  } else if (tab === "vendors") {
    body.append(table([{ label: "Vendor", f: (v) => [h("b", {}, v.name), h("div", { class: "small muted" }, `${v.vid} · ${v.menu}`)] },
      { label: "Certified diets", f: (v) => v.diets.join(", ") }, { label: "Hours", key: "hours" }, { label: "Capacity", key: "capacity", right: true },
      { label: "Hygiene certificate", f: (v) => h("span", { class: "tag " + (v.cert_state === "valid" ? "ok" : v.cert_state === "expired" ? "crit" : "high") }, `${v.cert_state} (${v.cert_renewed || v.cert_expiry})`) },
      { label: "Serves", f: (v) => `${v.compliant_meals}/9 meals` + (v.shutdown_from ? "" : "") },
      { label: "Flags", f: (v) => [v.flags.map((f) => h("div", { class: "small", style: { color: "#8a5a00" } }, f)), v.shutdown_from ? h("div", { class: "tag crit" }, `shut ${fmt.t(v.shutdown_from)}: ${v.shutdown_reason}`) : null] },
      { label: "", f: (v) => canW("vendor_edit") ? h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, !v.diets.includes("nut-free") && v.cert_state !== "expired" ? h("button", { class: "btn sm", onclick: () => withReason(`Designate ${v.name} as the allergen-controlled counter`, "Adds a verified nut-free protocol (separate prep, labelled dishes). Nut-allergy diners are routed here first.", async (reason) => { await api(`/food/vendor/${v.vid}`, { add_diet: "nut-free", reason }); toast("Designated; coverage re-solved", "ok"); render(); }) }, "Make nut-free counter") : null, v.cert_state !== "valid" ? h("button", { class: "btn sm", onclick: () => renewDialog(v) }, "Record renewal") : null, v.shutdown_from ? h("button", { class: "btn sm", onclick: () => withReason(`Reopen ${v.name}`, "", async (reason) => { await api(`/food/vendor/${v.vid}`, { reopen: true, reason }); toast("Reopened", "ok"); render(); }) }, "Reopen") : null) : "" }], d.vendors));
  } else {
    const tot = d.vendors.reduce((a, v) => a + v.payable, 0);
    body.append(h("p", {}, `Rates are missing from vendors.csv; ₹${d.vendors[0] ? d.vendors[0].rate : ""} per cover is assumed until entered. Forecast payable: ${fmt.inr(tot)}.`),
      table([{ label: "Vendor", f: (v) => `${v.name} (${v.menu})` }, { label: "Terms", key: "terms" }, { label: "Compliant meals", key: "compliant_meals", right: true },
        { label: "Planned covers", f: (v) => fmt.n(v.planned_covers), right: true }, { label: "Logged", f: (v) => v.logged_covers || "—", right: true },
        { label: "Payable", f: (v) => h("b", {}, fmt.inr(v.payable)), right: true }, { label: "Basis", f: (v) => h("span", { class: "small" }, v.settlement_note) }], d.vendors));
  }
}
function renewDialog(v) {
  const dt = h("input", { class: "in", value: "2027-10-31" });
  withReason(`Record certificate renewal — ${v.name}`, "Enter the new expiry from the renewed certificate.", async (reason) => {
    await api(`/food/vendor/${v.vid}`, { cert_renewed: dt.value, reason }); toast("Renewal recorded; coverage re-solved", "ok"); render();
  }, { extra: h("label", { class: "f" }, "New expiry (YYYY-MM-DD)", dt) });
}

// ====================================================================== transport
export async function transport() {
  const d = await api("/transport");
  mount(head("Transport", `Shuttles for people housed off campus, timed from their first and last heats. ${d.bus_capacity}-seat buses.`),
    d.risks.length ? h("div", { class: "note warn", style: { marginBottom: "14px" } }, d.risks.map((r) => h("div", {}, r.msg))) : null,
    d.sites.filter((x) => x.residents).map((x) => h("section", { class: "panel" },
      h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, x.name), h("span", { class: "muted" }, `${x.distance} km · ~${x.travel_min} min · ${x.residents} residents`), x.transport !== "yes" ? h("span", { class: "tag high" }, `transport: ${x.transport}`) : null),
      table([{ label: "Day", f: (y) => fmt.day(y.date) }, { label: "Residents", key: "residents", right: true }, { label: "Competing", key: "with_events", right: true },
        { label: "First bus leaves", f: (y) => h("b", {}, fmt.hm(y.first_departure)) }, { label: "for first heat", f: (y) => fmt.hm(y.first_event) },
        { label: "Last return", f: (y) => h("span", { class: y.late_return ? "tag high" : "" }, fmt.hm(y.last_return)) }, { label: "Trips each way", key: "trips_each_way", right: true },
        { label: "Women / minors", f: (y) => `${y.women} / ${y.minors}` }], x.days, { wrap: false }))));
}
