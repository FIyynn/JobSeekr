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
            self.assertTrue(digitized_user["eligibility"]["right_to_work"])
            self.assertTrue(digitized_user["eligibility"]["driving_license"]["uae_license"])
            self.assertTrue(digitized_user["eligibility"]["availability"]["immediately"])
            self.assertCountEqual(digitized_user["eligibility"]["work_arrangement"]["acceptable"], ["Hybrid", "On-site", "Remote"])
            self.assertEqual(digitized_user["seniority"]["level"], "entry_level")
            self.assertTrue(digitized_user["seniority"]["recent_graduate"])
            self.assertEqual(digitized_user["application_policy"]["auto_apply"], False)
            self.assertEqual(digitized_user["application_policy"]["default_action"], "shortlist_for_review")
            self.assertEqual(digitized_user["application_policy"]["notes"], [])
            self.assertGreaterEqual(len(digitized_user["preferences"]["trade_offs"]["salary"]), 0)
            self.assertGreaterEqual(len(digitized_user["preferences"]["roles"]), 1)
            self.assertTrue(all("entry" not in role.casefold() for role in digitized_user["preferences"]["roles"]))
            self.assertIn("high_priority", digitized_user["preferences"]["industries"])
            self.assertIn("ideal", digitized_user["preferences"]["work_style"])
            self.assertIn("lower_if", digitized_user["preferences"]["compensation"])
            self.assertIn("ranges", digitized_user["preferences"]["compensation"])
            self.assertIn("salary", digitized_user["preferences"]["trade_offs"])
            self.assertIn("hard_yes", digitized_user["constraints"])
            self.assertIn("hard_no", digitized_user["constraints"])
            self.assertIn("notes", digitized_user["constraints"])
            self.assertIn("Full-time employment", digitized_user["constraints"]["hard_yes"])
            self.assertTrue(any("unpaid" in item.casefold() for item in digitized_user["constraints"]["hard_no"]))
            self.assertTrue(any("vague talent-pool" in item.casefold() for item in digitized_user["constraints"]["hard_no"]))
            self.assertTrue(any("no clear standard employment setup" in item.casefold() for item in digitized_user["constraints"]["notes"]))
            self.assertTrue(any("role structure is unclear" in item.casefold() for item in digitized_user["constraints"]["notes"]))
            self.assertTrue(any("slightly lower floor" in item.casefold() for item in digitized_user["preferences"]["compensation"]["notes"]))
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
            self.assertEqual(digitized_user["constraints"]["hard_yes"], ["Full-time employment"])
            self.assertIn("hard_no", digitized_user["constraints"])
            self.assertIn("eligibility", digitized_user)
            self.assertIn("application_policy", digitized_user)
            self.assertIn("Missing required profile fields", " ".join(result["warnings"]))
            self.assertNotIn("questions", result["result"])
            self.assertNotIn("scoring_questions", result["result"])
            self.assertNotIn("prompt_pack", result["result"])

    def test_explicit_overrides_win_for_new_handoff_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "onboarding_task_state.json"
            docs = [
                {
                    "name": "cv.md",
                    "text": "# Override User\n\n## Professional Summary\nShort summary.\n\n## Additional Information\n- Open to Remote opportunities\n",
                }
            ]

            result = run_onboarding_task(
                {
                    "task_name": "onboarding_profile_digitization",
                    "task_id": "test-onboarding-overrides",
                    "documents": docs,
                    "profile": {
                        "full_name": "Override User",
                        "email": "override@example.com",
                        "location": "Abu Dhabi, United Arab Emirates",
                        "eligibility": {
                            "right_to_work": {"generic_right_to_work": False, "canonical": "No right-to-work", "aliases": ["No right-to-work"]},
                            "driving_license": {"uae_license": False, "notes": []},
                            "availability": {"immediately": False, "notes": []},
                            "work_arrangement": {"ideal": ["Hybrid"], "acceptable": ["Remote"], "notes": ["From task input"]},
                        },
                        "seniority": {
                            "level": "experienced",
                            "recent_graduate": False,
                            "years_min": 3,
                            "years_max": 5,
                            "evidence": ["task input override"],
                        },
                        "application_policy": {
                            "auto_apply": True,
                            "default_action": "auto_apply",
                            "notes": ["Task input override"],
                        },
                    },
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            digitized_user = result["digitized_user"]
            self.assertEqual(result["status"], "success")
            self.assertFalse(digitized_user["eligibility"]["right_to_work"]["generic_right_to_work"])
            self.assertEqual(digitized_user["eligibility"]["right_to_work"]["canonical"], "No right-to-work")
            self.assertFalse(digitized_user["eligibility"]["driving_license"]["uae_license"])
            self.assertFalse(digitized_user["eligibility"]["availability"]["immediately"])
            self.assertEqual(digitized_user["eligibility"]["work_arrangement"]["ideal"], ["Hybrid"])
            self.assertEqual(digitized_user["eligibility"]["work_arrangement"]["acceptable"], ["Remote"])
            self.assertEqual(digitized_user["seniority"]["level"], "experienced")
            self.assertFalse(digitized_user["seniority"]["recent_graduate"])
            self.assertEqual(digitized_user["application_policy"]["auto_apply"], True)
            self.assertEqual(digitized_user["application_policy"]["default_action"], "auto_apply")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["eligibility.right_to_work"], "task_input")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["seniority.level"], "task_input")
            self.assertEqual(digitized_user["source_coverage"]["field_sources"]["application_policy.default_action"], "task_input")

    def test_formal_talent_programs_stay_valid_when_source_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "onboarding_task_state.json"
            docs = [
                {
                    "name": "cv.md",
                    "text": "# Synthetic User\n\n## Additional Information\n- UAE National\n- Available Immediately\n- Open to Hybrid opportunities\n\n## Professional Summary\nRecent graduate looking for a junior data role.\n",
                },
                {
                    "name": "Preferences.md",
                    "text": "# Job Preferences\n\n## Preferred Roles (Highest Priority)\n- Data Analyst\n- Treat this as an entry-level / recent-graduate profile\n\n## Team Preferences\n- Do not auto-apply by default; shortlist for review first\n\n## Hard Constraints\n- Formal graduate or UAE National talent programs are acceptable\n- Always skip unpaid, commission-only, or vague talent-pool roles\n",
                },
            ]

            result = run_onboarding_task(
                {
                    "task_name": "onboarding_profile_digitization",
                    "task_id": "test-onboarding-talent-program",
                    "documents": docs,
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            digitized_user = result["digitized_user"]
            self.assertEqual(result["status"], "partial")
            self.assertIn("email", result["missing_fields"])
            self.assertIn("location", result["missing_fields"])
            self.assertIn("formal graduate / UAE National talent programs", digitized_user["constraints"]["hard_yes"])
            self.assertEqual(digitized_user["constraints"]["notes"], [])
            self.assertTrue(any("unpaid" in item.casefold() for item in digitized_user["constraints"]["hard_no"]))
            self.assertTrue(any("talent-pool" in item.casefold() for item in digitized_user["constraints"]["hard_no"]))
            self.assertEqual(digitized_user["application_policy"]["notes"], [])


if __name__ == "__main__":
    unittest.main()
