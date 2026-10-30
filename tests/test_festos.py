"""End-to-end tests (stdlib unittest; ~1 minute).   python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ["FESTOS_DB"] = os.path.join(TMP, "test.db")

from festos import config, db, entity, incidents, inventory, pipeline, security, verify  # noqa: E402

CON = None


def setUpModule():
    global CON
    CON = pipeline.build(path=os.environ["FESTOS_DB"], log=lambda *a: None, schedule_seconds=3, frontier=False)


class Ingest(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(CON.execute("SELECT COUNT(*) FROM people WHERE kind='participant'").fetchone()[0], 2836)
        self.assertEqual(CON.execute("SELECT COUNT(*) FROM registrations").fetchone()[0], 5580)

    def test_blank_gender_not_assumed(self):
        n = CON.execute("SELECT COUNT(*) FROM people WHERE kind='participant' AND gender_raw='' AND housing_cat!='X'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_blank_diet_not_cleared(self):
        n = CON.execute("SELECT COUNT(*) FROM people WHERE diet_raw='' AND kind='participant' AND diet!='UNKNOWN'").fetchone()[0]
        self.assertEqual(n, 0)

    def test_inverted_times_held(self):
        rows = CON.execute("SELECT pid, status FROM people WHERE timing_ok=0").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["status"] == "needs_confirmation" for r in rows))


class Dedup(unittest.TestCase):
    def test_exactly_the_planted_duplicates(self):
        pairs = {(r["a"], r["b"]) for r in CON.execute("SELECT a,b FROM dup_candidates")}
        self.assertEqual(pairs, {("P0021", "P_DUP01"), ("P0119", "P_DUP02")})

    def test_namesakes_not_flagged(self):
        # Raghav Joshi appears 12 times in C03; only the planted copy is a candidate
        n = CON.execute("SELECT COUNT(*) FROM dup_candidates WHERE a LIKE 'P0%' AND b LIKE 'P0%'").fetchone()[0]
        self.assertEqual(n, 0)


class Rules(unittest.TestCase):
    def test_all_hard_rules_hold(self):
        bad = [r for r in verify.check(CON) if not r["ok"]]
        self.assertEqual(bad, [], bad)


class Merch(unittest.TestCase):
    def test_duplicate_claim_blocked(self):
        mem = db.clone_to_memory(CON)
        a = inventory.issue(mem, "P0003", "T-shirt", None, "ST05", "Desk 1", "2026-10-30 09:00")
        b = inventory.issue(mem, "P0003", "T-shirt", None, "ST29", "Desk 3", "2026-10-30 09:05")
        self.assertTrue(a["ok"])
        self.assertFalse(b["ok"])
        self.assertEqual(b["code"], "DUPLICATE_CLAIM")
        self.assertEqual(mem.execute("SELECT COUNT(*) FROM merch_blocked").fetchone()[0], 1)


class Disruptions(unittest.TestCase):
    def test_preview_does_not_touch_live(self):
        before = CON.execute("SELECT COUNT(*) FROM alloc WHERE status='placed'").fetchone()[0]
        r = incidents.preview(CON, "hostel_closure", {"target": "B1", "from": "2026-10-30 18:00"}, "ST08", "2026-10-30 07:30")
        self.assertGreater(r["impact"]["modules"]["accommodation"]["lost_bed"] + r["impact"]["modules"]["accommodation"]["moved"], 0)
        after = CON.execute("SELECT COUNT(*) FROM alloc WHERE status='placed'").fetchone()[0]
        self.assertEqual(before, after)

    def test_every_type_keeps_rules(self):
        mem = db.clone_to_memory(CON)
        now = "2026-10-30 07:30"
        for typ, prm in [("late_contingent", {"cid": "C05", "delay_hours": 6}),
                         ("hostel_closure", {"target": "G2-F2", "from": "2026-10-30 18:00"}),
                         ("venue_change", {"vid": "V02", "from": "2026-10-31 14:00", "to": "2026-10-31 18:00"}),
                         ("equipment_failure", {"item": "projector", "qty": 10}),
                         ("vendor_shutdown", {"vid": "VND07", "from": now}),
                         ("duplicate_record", {"source_pid": "P0500", "contact": "98765 43210"}),
                         ("participant_cancel", {"pid": "P0011"})]:
            incidents.apply(mem, typ, prm, "ST08", now)
        bad = [r for r in verify.check(mem) if not r["ok"] and not r["rule"].startswith("Audit")]
        self.assertEqual(bad, [], bad)

    def test_duplicate_record_is_held(self):
        mem = db.clone_to_memory(CON)
        r = incidents.apply(mem, "duplicate_record", {"source_pid": "P0119", "name_variant": "Ragav Joshi", "contact": "98765 43210"}, "ST01", "2026-10-30 08:00")
        self.assertTrue(r["held"])


class Security(unittest.TestCase):
    def test_volunteer_sees_masked_fields(self):
        p = dict(CON.execute("SELECT * FROM people WHERE pid='P0004'").fetchone())
        m = security.mask_person(p, "volunteer")
        self.assertNotIn(p["contact"][-6:], m["contact"])
        self.assertIsNone(m["diet"])
        self.assertIn(m["access_need"], ("SUPPORT", "NONE"))

    def test_audit_tamper_detected(self):
        mem = db.clone_to_memory(CON)
        security.audit(mem, "ST08", "director", "test", "x", "1", {}, "r", "2026-10-30 08:00")
        self.assertTrue(security.verify_chain(mem)["ok"])
        mem.execute("UPDATE audit SET reason='edited' WHERE seq=1")
        self.assertFalse(security.verify_chain(mem)["ok"])

    def test_token_signature(self):
        t = security.make_token("ST08", "director")
        self.assertEqual(security.read_token(t)["u"], "ST08")
        self.assertIsNone(security.read_token(t[:-2] + "00"))


if __name__ == "__main__":
    unittest.main()
