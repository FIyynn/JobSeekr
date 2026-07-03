from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from tasks.onboarding_task import clear_task_state, run_onboarding_task


class OnboardingTaskTests(unittest.TestCase):
    def test_profile_docs_fill_core_fields_and_provenance(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "onboarding_task_state.json"
            clear_task_state(state_path)

            result = run_onboarding_task(
                {
                    "task_name": "onboarding_profile_digitization",
                    "task_id": "test-onboarding-accuracy",
                    "documents": [str(repo_root / "profiles" / "1")],
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            profile = result["result"]["profile"]
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["missing_fields"], [])
            self.assertEqual(profile["personal"]["full_name"], "Omar Saeed Al Shamsi")
            self.assertEqual(profile["personal"]["email"], "omar.alshamsi.bd@gmail.com")
            self.assertEqual(profile["personal"]["location"], "Dubai, United Arab Emirates")
            self.assertEqual(profile["field_sources"]["full_name"], "documents")
            self.assertEqual(profile["field_sources"]["email"], "documents")
            self.assertEqual(profile["field_sources"]["location"], "documents")
            self.assertGreaterEqual(profile["confidence_score"], 95)
            self.assertGreaterEqual(len(profile["skills"]), 30)
            self.assertGreaterEqual(len(profile["education"]), 1)
            self.assertGreaterEqual(len(profile["projects"]), 5)
            self.assertGreaterEqual(len(profile["preferences"]["trade_offs"]), 4)
            self.assertGreaterEqual(len(profile["scoring_preferences"]["preferred_roles"]), 1)
            self.assertIn("high_priority", profile["scoring_preferences"]["industries"])
            self.assertIn("ideal", profile["scoring_preferences"]["work_style"])
            self.assertIn("lower_if", profile["scoring_preferences"]["compensation"])
            self.assertIn("salary", profile["scoring_preferences"]["trade_offs"])
            self.assertIn("boosts", profile["scoring_profile"])
            self.assertIn("penalties", profile["scoring_profile"])
            self.assertEqual(profile["scoring_profile"]["auto_apply_threshold"], 80)
            self.assertEqual(profile["scoring_profile"]["manual_review_threshold"], 60)
            self.assertEqual(profile["experience_profile"]["level"], "entry_level")
            self.assertEqual(profile["scoring_profile"]["compensation"]["parsed"]["currency"], "AED")
            self.assertEqual(profile["scoring_profile"]["compensation"]["parsed"]["min"], 25000)
            self.assertEqual(profile["scoring_profile"]["compensation"]["parsed"]["max"], 40000)
            self.assertGreaterEqual(len(result["result"]["scoring_questions"]), 6)
            self.assertIn("minimum_monthly_salary_aed", [item["field"] for item in result["result"]["scoring_questions"]])
            self.assertFalse(result["result"]["needs_user_review"])
            self.assertGreaterEqual(profile["confidence_score"], 90)
            self.assertIn("Data Engineer", result["result"]["prompt_pack"]["search_prompt"])

    def test_missing_major_sections_reduce_confidence_and_request_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "onboarding_task_state.json"
            docs = [
                {
                    "name": "cv.md",
                    "text": "# Test User\n\n**Data Analyst**\n\n## Professional Summary\nShort summary only.\n",
                },
                {
                    "name": "Preferences.md",
                    "text": "# Job Preferences\n\n## Preferred Roles (Highest Priority)\n- Data Analyst\n\n## Hard Constraints\n- Full-time employment\n",
                },
            ]

            result = run_onboarding_task(
                {
                    "task_name": "onboarding_profile_digitization",
                    "task_id": "test-onboarding-low-confidence",
                    "documents": docs,
                    "profile": {
                        "full_name": "Test User",
                        "email": "user@example.com",
                        "location": "Dubai, United Arab Emirates",
                    },
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            profile = result["result"]["profile"]
            self.assertEqual(result["status"], "success")
            self.assertTrue(result["result"]["needs_user_review"])
            self.assertLess(profile["confidence_score"], 85)
            self.assertEqual(profile["personal"]["full_name"], "Test User")
            self.assertEqual(profile["personal"]["email"], "user@example.com")
            self.assertEqual(profile["personal"]["location"], "Dubai, United Arab Emirates")
            self.assertEqual(profile["field_sources"]["full_name"], "task_input")
            self.assertEqual(profile["field_sources"]["email"], "task_input")
            self.assertEqual(profile["field_sources"]["location"], "task_input")
            self.assertEqual(profile["education"], [])
            self.assertEqual(profile["projects"], [])
            self.assertEqual(profile["skills"], [])
            self.assertIn("preferred_roles", profile["scoring_preferences"])
            self.assertEqual(profile["scoring_preferences"]["preferred_roles"], ["Data Analyst"])
            self.assertEqual(profile["scoring_profile"]["boosts"], ["Data Analyst"])
            self.assertEqual(profile["scoring_profile"]["hard_constraints"], ["Full-time employment"])
            self.assertEqual(
                profile["scoring_profile"]["penalties"],
                ["Unpaid roles", "Commission-only roles", "Vague talent-pool postings"],
            )
            self.assertEqual(profile["experience_profile"]["level"], "unknown")
            self.assertGreaterEqual(len(result["result"]["scoring_questions"]), 6)
            self.assertIn("Incomplete profile sections", " ".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
