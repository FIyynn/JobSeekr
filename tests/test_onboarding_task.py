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

            digitized_user = result["digitized_user"]
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["missing_fields"], [])
            self.assertEqual(digitized_user["identity"]["full_name"], "Omar Saeed Al Shamsi")
            self.assertEqual(digitized_user["contact"]["email"], "omar.alshamsi.bd@gmail.com")
            self.assertEqual(digitized_user["contact"]["location"], "Dubai, United Arab Emirates")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["full_name"], "documents")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["email"], "documents")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["location"], "documents")
            self.assertGreaterEqual(digitized_user["completeness"]["confidence_score"], 95)
            self.assertTrue(digitized_user["completeness"]["required_complete"])
            self.assertTrue(digitized_user["completeness"]["ready_for_scoring"])
            self.assertGreaterEqual(len(digitized_user["skills"]), 30)
            self.assertGreaterEqual(len(digitized_user["education"]), 1)
            self.assertGreaterEqual(len(digitized_user["projects"]), 5)
            self.assertGreaterEqual(len(digitized_user["preferences"]["trade_offs"]["salary"]), 0)
            self.assertGreaterEqual(len(digitized_user["preferences"]["roles"]), 1)
            self.assertIn("high_priority", digitized_user["preferences"]["industries"])
            self.assertIn("ideal", digitized_user["preferences"]["work_style"])
            self.assertIn("lower_if", digitized_user["preferences"]["compensation"])
            self.assertIn("salary", digitized_user["preferences"]["trade_offs"])
            self.assertEqual(digitized_user["completeness"]["notes"], [])
            self.assertNotIn("questions", result["result"])
            self.assertNotIn("scoring_questions", result["result"])
            self.assertNotIn("prompt_pack", result["result"])

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
                        "location": "Dubai, United Arab Emirates",
                    },
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            digitized_user = result["digitized_user"]
            self.assertEqual(result["status"], "partial")
            self.assertIn("email", result["missing_fields"])
            self.assertFalse(digitized_user["completeness"]["required_complete"])
            self.assertFalse(digitized_user["completeness"]["ready_for_scoring"])
            self.assertLess(digitized_user["completeness"]["confidence_score"], 85)
            self.assertEqual(digitized_user["identity"]["full_name"], "Test User")
            self.assertEqual(digitized_user["contact"]["location"], "Dubai, United Arab Emirates")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["full_name"], "task_input")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["location"], "task_input")
            self.assertEqual(digitized_user["education"], [])
            self.assertEqual(digitized_user["projects"], [])
            self.assertEqual(digitized_user["skills"], [])
            self.assertEqual(digitized_user["preferences"]["roles"], ["Data Analyst"])
            self.assertEqual(digitized_user["constraints"]["hard_no"], ["Full-time employment"])
            self.assertIn("Missing required profile fields", " ".join(result["warnings"]))
            self.assertNotIn("questions", result["result"])
            self.assertNotIn("scoring_questions", result["result"])
            self.assertNotIn("prompt_pack", result["result"])


if __name__ == "__main__":
    unittest.main()
