# FestOS — the control room for Aarohan

FestOS is the internal portal the Aarohan core team uses to run a three-day, 2,800-person inter-college fest. It joins participants, beds, events, venues, kit, merchandise, food and staff in one place. It makes defensible plans under conflicting constraints, and when the plan breaks it shows exactly what changed and who must be told.

**What sets it apart:** every hard rule is enforced by the optimizer *and* re-checked by an independent verifier (25/25 pass). Every disruption can be previewed on a copy of live data before it is applied. Every risky override needs a reason and lands in a tamper-evident audit log.

---

## 1. Quick Start for Judges (1-Click Run)

Requirements: **Python 3.9+** (Standard library only; zero npm/node or pip installs required).

- **Windows (1-Click):** Double-click **`START_FESTOS.bat`** (or `RUN_FESTOS.bat`). It automatically detects Python, launches the local server, and opens your browser at `http://localhost:8000`.
- **macOS / Linux:** Run `chmod +x run_festos.sh && ./run_festos.sh` in terminal.
- **Live Cloud Deployment:** Accessible 24/7 at **[https://festosarb.vercel.app](https://festosarb.vercel.app)**
- **Official GitHub Repo:** **[https://github.com/AmSach/festos](https://github.com/AmSach/festos)**

Manual launch from terminal:
```bash
python run.py              # opens http://localhost:8000
```

- The submission ships with a pre-built plan (`festos.db`), so it starts instantly. `python3 run.py --reset` rebuilds everything from `data/` (≈1 min; the cost/cohesion alternatives keep computing in the background for another minute).
- **Optional:** `pip install scipy` turns on the exact MILP accommodation optimizer (HiGHS). Without SciPy, a deterministic greedy fallback runs and every hard rule still holds. The MILP houses about 50 more people.
- Other options: `--port 9000`, `--host 0.0.0.0` to share on a LAN, `--no-browser`.
- Tests: `python3 -m unittest discover tests` (14 end-to-end tests, ≈30 s).
- Independent rule check of the current plan: `python3 -m festos.verify`.
- Rebuild the sample-plan workbook: `python3 tools/make_workbook.py`.

### Test credentials

Every account uses the password **`aarohan2026`**. The sign-in screen also has one-click role buttons. IDs come from `core_team.csv`.

| Role | Staff ID | What they see |
|---|---|---|
| Festival Director | ST08 | everything; approves overrides, budget, floor designations; audit log |
| Hospitality | ST02 | people, accommodation, transport, food; contact + accessibility |
| Registration Desk | ST01 | people, data quality, imports, duplicates, merchandise |
| Events Team | ST03 | schedule, logistics; no contact or diet |
| Logistics Head | ST04 | logistics, schedule, transport |
| Merchandise Desk | ST05 | issue desk; names and sizes only |
| Food Coordinator | ST06 | vendors, diet coverage, settlement (aggregates, no contacts) |
| Volunteer | ST07 | help desk only; phone, diet and medical fields masked |

---

## 2. What is in the box

| Screen | Job |
|---|---|
| **Control tower** | Ranked queue of everything needing a decision (severity × time-to-impact), with an owner and a link. "Mine / Critical / All" filters. What's on stage now, arrivals in the next 24 h, latest decisions. |
| **Help desk** | One lookup for a volunteer: bed, heats, needs, kit to collect, check-in. Handles the pack's heavy name repetition. |
| **People / Contingents** | A unified 360° record per person: profile (masked by role), events and heats, conditional semis/finals, bed plus *why here*, roommates, merchandise, safe vendors today, data findings, history. Break-glass reveal needs a reason. |
| **Accommodation** | Night-by-night demand vs beds, five re-solved alternatives (cost ↔ cohesion ↔ budget), hostel floor map with proposed all-gender floors, waiting list with reasons, accessibility verification, rules check, manual moves validated before commit. |
| **Events & venues** | Gantt per venue/day with a "now" line, heat drawer (competitors, judges, kit, move-with-impact-check), validator conflicts, event flags, volunteer curve. |
| **Logistics** | Stock by state and location; movement pick-list generated from the schedule; shortfalls; partial-line state changes. |
| **Merchandise** | Issue desk with duplicate-claim blocking, stock vs demand, run-out time by arrival order, fair-share quotas per contingent. |
| **Food vendors** | Meal × diet coverage heatmap (lexicographic max-flow), compliance (certificates, hours, label conflicts), settlement by payment terms. |
| **Transport** | Shuttle timing per property from first/last heats, trips, late-return safety flags. |
| **Disruptions** | 8 live-test scenarios. Each shows a blast-radius preview, commits with a reason, and can be undone. |
| **Data quality** | 2,836 → 2,212 reconciliation waterfall, duplicate review with evidence bars, findings ledger (857 findings), late-registration import. |
| **Audit / Staff / Assumptions** | Hash-chained log with verification, overrides, break-glass log, permission matrix, on-shift status, every assumption with its rationale (Director can change them). |

Sample plans generated from the pack are in **`exports/`**: `FestOS_Sample_Plans.xlsx` (summary, accommodation, schedule, heat seats, equipment plan, merchandise, food coverage, data findings, verification, assumptions) plus the same tables as CSV.

---

## 3. What we found in the data

| Finding | What FestOS does |
|---|---|
| 2,836 rows vs "about 2,400" | Waterfall: 2,834 unique people, of whom 2,831 are plannable. Peak on site at once is 2,189 and the busiest day is 2,567, so "2,400" is a brochure number, not a planning number. |
| `P_DUP01` "Taanvi Daas" = P0021; `P_DUP02` = P0119 with a dummy phone | Fellegi–Sunter entity resolution with u-probabilities **learned from the pack**. The two duplicates score 21 bits; the best of 13,019 same-name pairs scores 11. Duplicates are auto-held; merging needs a person. |
| Names repeat ~10× per contingent | Name agreement counts as weak evidence; leaders cannot be identified by name (flagged, not guessed). |
| 1,546 people need a bed on the peak night vs 1,450 beds (before 38 escorts) | Shortfall shown per night. Beyond the budget, more money barely helps; the bottleneck is beds. |
| ~700 non-binary / prefer-not-to-say / blank-gender people per night | Never placed in a binary block. The optimizer proposes which boys'-block floors become all-gender (Director approves). Blank ≠ gender: a confirmation is requested; prefer-not-to-say is never re-asked. |
| 75 wheelchair users vs 26 accessible beds | Hard rule kept. People wait rather than being placed unsafely. Hospitality verifies step-free rooms at a property and the plan re-runs with priority for hard needs. |
| 279 minors | Never at a property without regular transport; penalised off campus; counted on late shuttles. |
| Team IDs only on *solo* events; team events have no rosters | Team IDs drive grouping (members share a heat); labels flagged; rosters requested. |
| 3 arrivals after departure | Held (not deleted, not guessed) until confirmed. |
| Every camera under repair; 0 confirmed-usable tables; ID cards 1,345 for 2,834; T-shirt M 109 for 571 | Shortfalls with run-out times, waivers with reasons, fair-share quotas. |
| No nut-free vendor (411 nut-allergy), Jain coverage effectively zero, 4 expired + 4 mid-fest certificates, "Vegan bowls" tagged non-veg, breakfast vendor opens 10:00 | Safety gaps per meal; a Director/Food action designates an allergen-controlled counter; label conflicts treated pessimistically. |
| No judges, budget, rates, venue facilities or volunteer roster in the pack | Clearly-labelled assumptions (see Assumptions screen) or a labelled *sample* judge panel. |
| 379 people arrive 22:00 on 31 Oct | Only 1 Nov is open to them. The scheduler seats late arrivals where finals still fit; the rest are a visible decision. |

---

## 4. How decisions are made

**Accommodation: two-stage optimisation plus repair**
1. *Stage 1, site assignment (MILP).* Cohorts are grouped by contingent × housing category × hard need × nights × minor × early-event × kind. Integer placements, binary contingent-uses-unit variables (cohesion), binary all-gender floor designations, and integer room counts per category per night at external properties (one category per room; escorts in private rooms). Hard constraints: per-night capacity, gender pools, accessibility, minors' transport, budget, leaders and escorts near their group. Soft goals are priced in rupees (shadow prices on the Assumptions screen). Solved as LP relaxation (lower bound) → relax-and-fix → MILP with HiGHS in ~15 s.
2. *Stage 2, bed packing.* Stays are intervals of nights, so beds are reused by non-overlapping stays (interval colouring), with same-contingent rooming first.
3. *Stage 3, fragmentation repair.* The minimal-change engine re-places anyone left without a single bed for their whole stay.
4. *Frontier.* Five plans are re-solved (tight budget, balanced, max cohesion, ignore cohesion, budget override) so trade-offs are real, not estimated.

**Schedule.** Rounds are split into heats only when registrants exceed a venue's seats. A backward/forward set-cover construction is followed by simulated annealing (~10,000 moves) with exact incremental costs: hard violations, per-participant best-heat-combination cost (absence and clashes, with semis/finals weighted by the probability of advancing), equipment concurrency vs usable stock, and volunteer peaks. Seat assignment is capacity-constrained and keeps teams together. An **independent validator** re-checks everything after every change.

**Food.** Lexicographic max-flow per meal: nut-allergy → Jain → gluten-free → vegan → halal → vegetarian → unknown → non-veg. Restricted diners are never displaced.

**Disruptions.** Apply → minimal-change repair (only affected people or heats move; heats that no longer fit any venue are re-split) → state diff across every module → notification list. Preview runs the same code on an in-memory clone. Commits are snapshotted for undo.

---

## 5. Live-test playbook

| Judge injects | Click |
|---|---|
| Late contingent | Disruptions → Late contingent → choose college + hours → Preview → Apply |
| Duplicate record | Disruptions → Duplicate record (or Data quality → Import) → it is auto-held → Data quality → Duplicates → Merge |
| Hostel closure | Disruptions → Hostel closure → block/floor/property → Apply → then Accommodation → Contract a property |
| Equipment failure | Disruptions → Equipment failure, or Logistics → change a stock line |
| Venue change | Disruptions → Venue unavailable (or drag a heat via the heat drawer → Move) |
| Vendor shutdown | Disruptions → Vendor shutdown → see diet coverage change in Food |
| Duplicate merch claim | Merchandise → Issue desk → issue the same ID twice (or Help desk) |

Move the demo clock by clicking the fest-clock ribbon or **+1 h** (Director). Undo is available on the latest incident.

---

## 6. Privacy and security
- Server-side role checks on every route; field-level masking (contact, accessibility, diet, age, gender, room). Break-glass reveal needs a written reason and is shown to the Director.
- PBKDF2-SHA256 passwords; HMAC-signed 12-hour tokens; login throttling; strict security headers (CSP, no framing, no sniffing).
- Append-only audit with SHA-256 hash chain (`Audit` screen verifies it); off-shift actions are flagged; sensitive fields are redacted in audit details.
- Exports contain no phone numbers or free-text medical data.
- Synthetic data only. No external services, no CDN, no telemetry: it runs fully offline.

## 7. Known limits (honest)
- HiGHS with a time limit is not bit-for-bit deterministic; results vary by a few beds between rebuilds.
- Semis/finals competitors come from results, so their clashes are expected values until qualifiers are entered.
- Room lists for external properties are virtual (3 beds per room) until the hotels share real ones.
- Judges are a labelled sample; venue capabilities are inferred; rates, budget and volunteer pool are assumptions.
- Undo is limited to the latest incident when nothing else has changed since (we won't silently erase later work).

## 8. AI disclosure
See `AI_DISCLOSURE.md`.

## 9. File map
```
run.py                 start the portal
festos/ingest.py       import + validation ledger        festos/entity.py        duplicate detection
festos/schedule.py     heats, annealing, validator        festos/accommodation.py MILP + packing + repair
festos/food.py         diet coverage, settlement          festos/inventory.py     kit plan, merchandise
festos/transport.py    shuttles                           festos/alerts.py        control tower
festos/incidents.py    disruptions, preview/commit/undo   festos/security.py      auth, RBAC, masking, audit chain
festos/verify.py       independent rule checker           festos/api.py, server.py  HTTP layer (stdlib)
web/                   the portal (vanilla JS, no build step)
data/                  the data pack (unchanged)           exports/  sample plans
tests/                 end-to-end tests                   tools/make_workbook.py
```
