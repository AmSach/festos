"""CSV exports (data-minimised: no phone numbers, no raw medical text)."""
from . import food, inventory, timeutil as T
from .db import jl


def write(con, name, w):
    if name == "accommodation_plan":
        w.writerow(["pid", "name", "kind", "contingent", "housing_category", "hard_need", "is_minor", "status", "site", "site_name",
                    "room", "bed", "nights", "external_cost_inr", "explanation"])
        for r in con.execute("""SELECT a.*, p.name, p.kind, p.cid, p.housing_cat, p.access_need, p.is_minor, s.name sname
                                FROM alloc a JOIN people p USING(pid) LEFT JOIN sites s USING(site_id) ORDER BY a.status, a.site_id, a.room_id, a.bed"""):
            need = r["access_need"] if r["access_need"] in ("WC", "GF", "UNKNOWN") else ""
            w.writerow([r["pid"], r["name"], r["kind"], r["cid"], r["housing_cat"], need, r["is_minor"], r["status"], r["site_id"] or "",
                        r["sname"] or "", r["room_id"] or "", r["bed"] or "", T.mask_label(r["nights"]), r["cost"] or 0,
                        " | ".join(jl(r["explain"], []))])
    elif name == "schedule":
        w.writerow(["heat", "event_id", "event", "round", "venue", "start", "end", "competitors_assigned", "planned_pool"])
        for r in con.execute("""SELECT h.*, e.name ev, v.name vn, (SELECT COUNT(*) FROM registrations r WHERE r.heat_id=h.hid) n
                                FROM heats h JOIN events e USING(eid) JOIN venues v USING(vid) ORDER BY h.start, v.vid"""):
            w.writerow([r["hid"], r["eid"], r["ev"], r["round"], r["vn"], r["start"], r["end"], r["n"], r["pool"]])
    elif name == "heat_assignments":
        w.writerow(["pid", "name", "contingent", "event_id", "team_id", "heat", "start", "venue"])
        for r in con.execute("""SELECT r.*, p.name, p.cid, h.start, v.name vn FROM registrations r JOIN people p USING(pid)
                                LEFT JOIN heats h ON h.hid=r.heat_id LEFT JOIN venues v ON v.vid=h.vid ORDER BY r.pid"""):
            w.writerow([r["pid"], r["name"], r["cid"], r["eid"], r["team_id"] or "", r["heat_id"] or "UNSEATED", r["start"] or "", r["vn"] or ""])
    elif name == "equipment_moves":
        plan = inventory.equipment_plan(con)
        w.writerow(["type", "by", "item", "qty", "from", "to", "for_or_missing"])
        for m in plan["moves"]:
            w.writerow(["move", m["by"], m["item"], m["qty"], m["from"], m["to"], m["for"]])
        for s in plan["shortages"]:
            w.writerow(["SHORTAGE", s["start"], s["item"], s["missing"], "", s["venue"], f"{s['event']} {s['hid']} needs {s['need']}"])
    elif name == "merch_plan":
        st = inventory.merch_status(con)
        w.writerow(["item", "size", "quantity", "reserved", "issued", "on_hand", "entitled_demand", "gap", "stock_out_at"])
        for l in st["lines"]:
            w.writerow([l["item"], l["size"], l["qty"], l["reserved"], l["issued"], l["on_hand"], l["demand"] or "", l["gap"] if l["gap"] is not None else "",
                        st["stockout"].get(f"{l['item']}|{l['size']}", "")])
        w.writerow([])
        w.writerow(["fair_share_size", "contingent", "quota"])
        for size, f in st["fair_share"].items():
            for c, q in sorted(f["quota"].items()):
                w.writerow([size, c, q])
    elif name == "food_coverage":
        w.writerow(["meal", "diet", "demand", "served", "gap", "eligible_vendors"])
        for m in food.coverage(con):
            for d, x in m["diets"].items():
                w.writerow([m["meal_id"], food.DIET_LABEL.get(d, d), x["demand"], x["served"], x["gap"], " ".join(x["vendors"])])
    elif name == "data_issues":
        w.writerow(["id", "entity", "entity_id", "code", "severity", "field", "message", "status", "resolution"])
        for r in con.execute("SELECT * FROM issues ORDER BY id"):
            w.writerow([r["id"], r["entity"], r["entity_id"], r["code"], r["severity"], r["field"] or "", r["message"], r["status"], r["resolution"] or ""])
    elif name == "audit_log":
        w.writerow(["seq", "ts", "actor", "role", "action", "entity", "entity_id", "reason", "off_shift", "hash"])
        for r in con.execute("SELECT * FROM audit ORDER BY seq"):
            w.writerow([r["seq"], r["ts"], r["actor"], r["role"], r["action"], r["entity"], r["entity_id"], r["reason"] or "", r["off_shift"], r["hash"]])
