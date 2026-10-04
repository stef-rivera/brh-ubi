import unittest
from backend.catalog import announcement
from backend.practice import build_session, grade


class PracticeTests(unittest.TestCase):
    def speed(self, value=40, **extra):
        return dict(sign_id="speed_limit", sign_text=f"SPEED LIMIT {value}",
                    value=value, recognition_status="resolved", **extra)

    def test_only_resolved_catalog_signs_enter_practice(self):
        rows = [self.speed(), self.speed(35)]
        rows += [dict(sign_id="school_zone", recognition_status=status)
                 for status in ["pending", "unresolved", "expired"]]
        rows += [dict(sign_id="unknown", recognition_status="resolved")]
        self.assertEqual([item["value"] for item in build_session(rows)], [40, 35])

    def test_speed_duplicates_merge_but_distinct_limits_remain(self):
        items = build_session([self.speed(event_id="first"), self.speed(event_id="second"), self.speed(35)])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["event_id"], "first")
        self.assertEqual(items[0]["sign_text"], "SPEED LIMIT 40")
        self.assertIn("40", items[0]["meaning"])

    def test_speed_answer_requires_correct_number(self):
        item = build_session([self.speed()])[0]
        for answer in ["40", "40 mph", "forty miles per hour"]:
            self.assertEqual(grade(item, answer), "correct")
        for answer in ["35", "maximum speed", "140", "40 or 35", "forty-five"]:
            self.assertEqual(grade(item, answer), "incorrect")

    def test_legacy_rows_keep_actual_value(self):
        item = build_session([dict(sign_id="speed_limit", sign_text="SPEED LIMIT 40")])[0]
        self.assertEqual(item["value"], 40)

    def test_cues_include_noncritical_catalog_signs(self):
        self.assertEqual(announcement("speed_limit", 40), "Speed limit 40 sign detected.")
        self.assertTrue(announcement("no_left_turn"))
        self.assertEqual(announcement("unknown"), "")

    def test_bicycle_crossing_has_its_own_practice_and_cue(self):
        item = build_session([dict(sign_id="bicycle_pedestrian_crossing",
                                  recognition_status="resolved", safety_critical=True)])[0]
        self.assertEqual(item["sign_id"], "bicycle_pedestrian_crossing")
        self.assertFalse(item["verified"])
        self.assertEqual(grade(item, "pedestrians and bicycles"), "correct")
        self.assertEqual(grade(item, "a school"), "incorrect")
        self.assertIn("bicycle", announcement(item["sign_id"]).lower())

    def test_no_turn_on_red_has_cue_and_practice(self):
        item = build_session([dict(sign_id="no_turn_on_red", sign_text="NO TURN ON RED",
                                  recognition_status="resolved", safety_critical=True)])[0]
        self.assertFalse(item["verified"])
        self.assertEqual(item["sign_text"], "NO TURN ON RED")
        self.assertEqual(grade(item, "do not turn"), "correct")
        self.assertEqual(grade(item, "yes"), "incorrect")
        self.assertEqual(announcement("no_turn_on_red"), "No turn on red sign detected.")

    def test_keywords_match_words_not_substrings(self):
        item = dict(answer_keywords=["no"])
        self.assertEqual(grade(item, "I know"), "incorrect")


if __name__ == "__main__":
    unittest.main()
