"""Regression tests for LLM response normalization."""

import unittest

from llm import _to_range, _validate_plan


class TestToRange(unittest.TestCase):
    def test_list_array(self):
        self.assertEqual(_to_range([180, 220]), [180, 220])

    def test_string_encoded_array(self):
        self.assertEqual(_to_range("[180, 220]"), [180, 220])
        self.assertEqual(_to_range("  [10, 15]  "), [10, 15])

    def test_min_max_object(self):
        self.assertEqual(_to_range({"min": 180, "max": 220}), [180, 220])

    def test_float_values_coerced_to_int(self):
        self.assertEqual(_to_range([180.5, 220.9]), [180, 220])

    def test_invalid_string(self):
        self.assertIsNone(_to_range("not an array"))
        self.assertIsNone(_to_range("180, 220"))
        self.assertIsNone(_to_range("[bad, json"))

    def test_invalid_shapes(self):
        self.assertIsNone(_to_range([180]))
        self.assertIsNone(_to_range([180, 220, 300]))
        self.assertIsNone(_to_range({"min": "a", "max": 220}))


class TestValidatePlan(unittest.TestCase):
    def _estimate(self, **kwargs):
        base = {
            "type": "estimate",
            "calories": [150, 200],
            "protein_g": [5, 10],
            "carbs_g": [20, 30],
            "fat_g": [8, 12],
            "portion_note": "small bowl",
        }
        base.update(kwargs)
        return base

    def test_estimate_with_string_ranges(self):
        data = self._estimate(
            calories="[180, 220]",
            protein_g="[10, 15]",
            carbs_g="[30, 40]",
            fat_g="[8, 12]",
        )
        result = _validate_plan(data)
        self.assertIsNotNone(result)
        self.assertEqual(result["calories"], [180, 220])
        self.assertEqual(result["protein_g"], [10, 15])

    def test_estimate_with_list_ranges(self):
        result = _validate_plan(self._estimate())
        self.assertIsNotNone(result)
        self.assertEqual(result["calories"], [150, 200])

    def test_estimate_missing_keys(self):
        data = {"type": "estimate", "calories": [150, 200]}
        self.assertIsNone(_validate_plan(data))

    def test_questions_valid(self):
        data = {
            "type": "questions",
            "questions": [
                {
                    "question": "How much?",
                    "options": ["Small", "Large"],
                },
            ],
        }
        self.assertIsNotNone(_validate_plan(data))

    def test_singular_question_type(self):
        data = {
            "type": "question",
            "question": "How much?",
            "options": ["Small", "Large"],
        }
        result = _validate_plan(data)
        self.assertEqual(result["type"], "questions")
        self.assertEqual(len(result["questions"]), 1)


if __name__ == "__main__":
    unittest.main()
