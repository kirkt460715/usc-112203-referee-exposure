"""核对审计脚本的判定口径与关键结论。"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import audit  # noqa: E402


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = audit.Records(audit.DEFAULT_RECORDS)

    def test_catalog_is_intact(self):
        self.assertEqual(self.rec.verify(), [])

    def test_score_zero_is_correct(self):
        # 已发通知在签发时点都必须达标；只有 0=正确 这一读法成立。
        for person, expected in (("R31", 0.8), ("R2", 1.0)):
            self.assertAlmostEqual(
                audit.assess(self.rec, person, "review").score, expected)

    def test_exposure_requires_attendance_and_earlier_submit(self):
        item = self.rec.items["I2"]
        answer = next(a for a in self.rec.answers if a.id == "R0-I2")
        self.assertTrue(audit.exposure_for(self.rec, answer, item))
        answer = next(a for a in self.rec.answers if a.id == "R2-I2")
        self.assertIsNone(audit.exposure_for(self.rec, answer, item))
        # 未实际举行的讲评不影响任何个人答卷
        answer = next(a for a in self.rec.answers if a.id == "R50-I7")
        self.assertIsNone(audit.exposure_for(self.rec, answer,
                                             self.rec.items["I7"]))
        answer = next(a for a in self.rec.answers if a.id == "R50-I4")
        d3 = next(d for d in self.rec.disclosures if d.id == "D3")
        self.assertFalse(d3.held)
        self.assertIsNone(audit.exposure_for(self.rec, answer,
                                             self.rec.items["I4"]))

    def test_notice_as_of_ignores_later_ledger_entry(self):
        notice = next(n for n in self.rec.notices if n.id == "N1")
        then = audit.assess(self.rec, "R31", "field", as_of=notice.as_of)
        now = audit.assess(self.rec, "R31", "field")
        self.assertEqual(then.status, "valid")
        self.assertEqual(now.status, "incomparable")

    def test_common_item_loses_comparability_globally(self):
        usable, burned = audit.common_anchor_status(self.rec, "field")
        self.assertNotIn("I5", usable)
        self.assertEqual([i for i, _ in burned], ["I5"])
        usable, burned = audit.common_anchor_status(self.rec, "review")
        self.assertNotIn("I2", usable)
        self.assertEqual([i for i, _ in burned], ["I2"])

    def test_dispositions_separate_three_outcomes(self):
        outcomes = audit.person_outcomes(self.rec)
        self.assertEqual(outcomes["R31"]["label"], "须重审")
        self.assertEqual(outcomes["R2"]["label"], "部分岗位暂缓")
        self.assertEqual(outcomes["R0"]["label"], "暂缓")
        self.assertEqual(outcomes["R2"]["rows"][0]["outcome"], "继续有效")

    def test_cycle_two_needs_backup_common_item(self):
        plan = audit.form_plan(self.rec)
        self.assertEqual(plan["pairs_active"], [])
        self.assertEqual(plan["min_activation_count"], 1)
        self.assertEqual(sorted(plan["min_activation_sets"]), [("I17",), ("I23",)])
        # 已排期未举行的 D3 也会烧掉 I21
        self.assertIn("I21", {i for i, _, _ in plan["burned"]})

    def test_report_renders(self):
        text = audit.render(self.rec)
        self.assertIn("继续有效", text)
        self.assertIn("部分岗位暂缓", text)
        self.assertIn("须重审", text)


if __name__ == "__main__":
    unittest.main()
