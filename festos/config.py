"""Assumptions register.

Every number the system uses that is NOT in the data pack lives here, with the
reason we chose it.  The portal shows this register to every user and lets the
Festival Director change values (each change is audited and triggers a replan).
Nothing in this file is hidden from the judges.
"""

FEST_START = "2026-10-30 08:00"
FEST_END = "2026-11-01 20:00"
DEFAULT_CLOCK = "2026-10-30 07:30"   # demo clock; switchable to live time

# key: (default value, category, rationale)
ASSUMPTIONS = {
    # ---------------- accommodation ----------------
    "budget_external_inr": (2600000, "Accommodation",
        "No budget in the pack. We assume ₹26 L for external stays (≈1,600 bed-nights at the median rate). Housing everyone who fits would cost ≈₹27.7 L, so the budget binds and the plan shows how many people each extra lakh houses. Exceeding it needs a Director override with a reason."),
    "night_core_window": ("23:00-06:00", "Accommodation",
        "A person needs a bed for a night if they are on site for ≥3h of 23:00–06:00. A 22:00 departure needs no bed; a 22:00 arrival does."),
    "night_min_overlap_h": (3, "Accommodation", "See night_core_window."),
    "split_penalty_inr": (6000, "Accommodation",
        "Shadow price of splitting a contingent across one more hostel block / property. Lets cohesion be traded against rupees explicitly."),
    "floor_spread_penalty_inr": (1500, "Accommodation",
        "Extra shadow price per additional floor for contingents that asked for 'same floor'."),
    "unplaced_penalty_inr": (60000, "Accommodation",
        "Cost of leaving a person without a bed. Large so beds are used whenever budget allows; never infinite, so shortages are reported instead of the solver failing."),
    "unplaced_hardneed_penalty_inr": (120000, "Accommodation",
        "Leaving someone with an accessibility need unplaced is worse; they are placed first."),
    "early_event_km_penalty_inr": (250, "Accommodation",
        "Per km per night for people with an event starting before 09:30 that day — pulls them toward campus."),
    "near_venue_km_penalty_inr": (200, "Accommodation",
        "Per km per night for contingents that asked for 'near venue'."),
    "minor_external_penalty_inr": (3000, "Accommodation",
        "Per night for housing a 17-year-old off campus (safety coverage). Off-campus minors with 'limited' transport are forbidden."),
    "external_room_size": (3, "Accommodation",
        "External properties list beds, not rooms. We pack them into virtual rooms of 3 for roommate grouping; replace with real room lists when the property shares them."),
    "convertible_blocks": (["B1", "B2", "B3"], "Accommodation",
        "Floors in these blocks may be proposed as all-gender floors for non-binary / prefer-not-to-say / unknown-gender participants. Girls' blocks are not converted by default (they are already over-subscribed). A proposal needs Director approval."),
    "max_all_gender_floors": (6, "Accommodation",
        "At most this many hostel floors are proposed for conversion to all-gender floors (a policy change the Director approves)."),
    "designation_penalty_inr": (15000, "Accommodation",
        "Shadow price of converting one floor's designation — keeps proposals to the floors that really pay off."),
    "external_accessible_beds_verified": ({}, "Accommodation",
        "Accessible beds at external properties verified by phone/visit. Empty = unverified = nobody with a wheelchair / ground-floor need is placed there."),
    "cleaning_buffer_h": (2, "Accommodation", "Bed turnover time between occupants."),
    "standard_checkin": ("12:00", "Accommodation", "Rooms vacated that morning are ready from max(checkout+buffer, 12:00)."),

    # ---------------- events ----------------
    "semis_advance_frac": (0.30, "Events", "No results data: we assume 30% of prelim entries reach semis."),
    "finals_advance_frac": (0.10, "Events", "…and 10% reach finals (minimum 4 entries)."),
    "max_heat_size": (150, "Events", "Max competitors per sitting (also capped by venue capacity). duration_minutes is read as the length of one sitting; extra heats are created only when registrants exceed this."),
    "changeover_min": (30, "Events", "Setup + teardown between consecutive heats of different events in a venue."),
    "round_gap_min": (45, "Events", "Minimum gap between the last heat of a round and the next round (results, call-ups)."),
    "travel_buffer_min": (15, "Events", "Walking time between different venues for a participant or judge."),
    "schedule_search_seconds": (25, "Events", "Time budget for the schedule improvement search (simulated annealing)."),
    "equipment_per_heat": ({"barricade": 8, "table": 6, "projector": 1, "camera": 1, "tripod": 1},
        "Events", "Event sheets name equipment but not quantities (except cords/mics/speakers). Defaults per heat."),
    "volunteer_pool": (60, "Events",
        "The pack lists only 6 people with the Volunteer role. We assume 60 student volunteers can be rostered and flag every hour that needs more."),
    "count_needs_check_as_usable": (False, "Logistics",
        "Equipment marked 'needs check' is NOT counted as usable until someone inspects it."),

    # ---------------- food ----------------
    "meals": ([["breakfast", "07:30", "09:30"], ["lunch", "12:30", "14:30"], ["dinner", "19:30", "21:30"]],
        "Food", "Meal service windows."),
    "catering_days": (["2026-10-30", "2026-10-31", "2026-11-01"], "Food",
        "Vendors are contracted for fest days only (assumption). People on site outside these days are flagged as uncatered."),
    "halal_accepts_vegetarian": (True, "Food",
        "Many halal-observant diners accept pure-vegetarian kitchens. Toggle off to require halal-tagged vendors only."),
    "unknown_diet_default": ("vegetarian", "Food",
        "A blank dietary field is NOT clearance. Unknowns are fed from vegetarian counters (most inclusive) and kept in a confirm-with-leader queue."),
    "vendor_rate_inr": (140, "Food", "Per-cover rate. Rates are missing from vendors.csv; replace per vendor."),
    "min_guarantee_frac": (0.6, "Food", "Minimum-guarantee vendors are assumed guaranteed 60% of capacity per contracted meal."),
    "staff_meal_headcount": (48, "Food", "Core team eats too."),

    # ---------------- merch ----------------
    "kit_items": (["ID card", "lanyard", "T-shirt"], "Merchandise",
        "Every participant is entitled to one of each. Totes and special badges have no eligibility rule in the pack, so they are issued only by explicit policy."),
    "tshirt_substitution": ({"S": ["M"], "M": ["L"], "L": ["XL"], "XL": ["XXL"], "XXL": []}, "Merchandise",
        "One size up is offered only with the participant's consent at the desk."),

    # ---------------- transport ----------------
    "bus_capacity": (40, "Transport", "Seats per shuttle."),
    "late_return_hour": (22, "Transport", "Returns after this hour need a staff escort on board."),

    # ---------------- people ----------------
    "minor_age_lt": (18, "People", "Participants under 18 are minors: on-campus preferred, escort coverage tracked, contact restricted."),
    "escorts_need_beds": (True, "People", "Faculty escorts are not in participants.csv; each needs a separate bed near the group for the contingent's full stay."),
    "unmatched_leaders_need_beds": (True, "People",
        "35 of 38 leaders do not appear in participants.csv. We hold a tentative bed near their group; release it if the leader turns out to be a registered participant."),
}

# Venue capability inference (assumption — the pack lists requirements per event but no venue facilities)
VENUE_CAPS = {
    "V01": ["stage", "lights", "audio", "seating", "power", "projector"],
    "V02": ["projector", "seating", "audio", "power", "tables"],
    "V03": ["projector", "seating", "audio", "power", "tables"],
    "V04": ["stage", "lights", "audio", "seating", "open ground"],
    "V05": ["open ground"],
    "V06": ["open ground"],
    "V07": ["quiet room", "projector", "seating", "power", "tables", "audio"],
    "V08": ["power", "tables", "seating", "audio"],
    "V09": ["quiet room", "power", "tables", "seating"],
}

# Which roles exist and what they may do. Derived from core_team.csv roles.
ROLE_KEYS = {
    "Festival Director": "director",
    "Hospitality": "hospitality",
    "Registration Desk": "registration",
    "Events Team": "events",
    "Logistics Head": "logistics",
    "Merchandise Desk": "merch",
    "Food Coordinator": "food",
    "Volunteer": "volunteer",
}

PERMISSIONS = {
    #               view modules                                              write actions
    "director":     {"view": "*", "write": "*"},
    "hospitality":  {"view": ["control", "participants", "accommodation", "transport", "incidents", "quality", "food"],
                     "write": ["checkin", "alloc_override", "incident", "participant_edit", "verify_property"]},
    "registration": {"view": ["control", "participants", "quality", "merch", "incidents"],
                     "write": ["checkin", "participant_edit", "dedup", "merch_issue", "incident", "import"]},
    "events":       {"view": ["control", "participants", "schedule", "inventory", "incidents"],
                     "write": ["schedule_edit", "incident"]},
    "logistics":    {"view": ["control", "inventory", "schedule", "transport", "incidents", "merch"],
                     "write": ["inventory_edit", "incident"]},
    "merch":        {"view": ["control", "participants", "merch", "incidents"],
                     "write": ["merch_issue", "incident"]},
    "food":         {"view": ["control", "food", "incidents"],
                     "write": ["vendor_edit", "incident"]},
    "volunteer":    {"view": ["control", "participants", "volunteer"],
                     "write": ["checkin"]},
}

# Field visibility (data minimisation). Anything not listed is masked for that role.
FIELD_ACCESS = {
    "contact":       ["director", "hospitality", "registration"],
    "accessibility": ["director", "hospitality", "registration", "events"],
    "dietary":       ["director", "hospitality", "food"],
    "age":           ["director", "hospitality", "registration"],
    "gender":        ["director", "hospitality"],
    "room":          ["director", "hospitality", "registration", "volunteer", "logistics"],
}

DEMO_PASSWORD = "aarohan2026"
