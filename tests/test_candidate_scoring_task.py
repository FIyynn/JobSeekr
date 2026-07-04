from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tasks.candidate_scoring_task import clear_task_state, read_task_state, run_candidate_scoring_task


def _digitized_user() -> dict[str, object]:
    return {
        "identity": {"full_name": "Omar Saeed Al Shamsi", "headline": "Data / BI"},
        "contact": {"email": "omar@example.com", "phone": "+971500000000", "location": "Dubai, United Arab Emirates"},
        "links": {"linkedin_url": "https://www.linkedin.com/in/example", "github_url": "", "website_url": ""},
        "summary": "Recent graduate with data and analytics focus.",
        "education": [{"title": "BSc Data Analytics", "details": ["UAE university"]}],
        "experience": [],
        "projects": [{"name": "Capstone", "details": ["Built a dashboard"]}],
        "skills": ["Python", "SQL", "Power BI", "Excel"],
        "languages": ["English", "Arabic"],
        "certifications": ["Google Data Analytics"],
        "preferences": {
            "roles": ["Data Analyst", "Business Intelligence Analyst"],
            "industries": {"high_priority": ["Technology"], "also_interested": ["Retail"]},
            "work_style": {"ideal": ["On-site"], "acceptable": ["Hybrid"]},
            "compensation": {"ideal": ["AED 12,000+"], "comfortable": ["AED 10,000+"], "lower_if": ["strong brand"]},
            "commute": {"preferred": ["Dubai"], "comfortable": ["Abu Dhabi"], "would_relocate": []},
            "company_size": {"preferred": ["Midsize"], "also_interested": ["Large"]},
            "trade_offs": {"salary": ["learning"], "remote_work": ["growth"], "job_title": ["ownership"], "prestige": ["brand"]},
        },
        "constraints": {"hard_no": ["Commission-only"], "must_have": ["Growth"], "nice_to_haves": ["Mentorship"]},
        "source_coverage": {"field_sources": {}, "documents": []},
        "completeness": {"required_complete": True, "ready_for_scoring": True, "missing_fields": [], "notes": [], "confidence_score": 95},
    }


def _candidates() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(35):
        rows.append(
            {
                "job_id": f"job-{index:02d}",
                "title": f"Data Analyst {index}",
                "company": "GMG",
                "location": "Dubai, Dubai, United Arab Emirates (On-site)",
                "link": f"https://www.linkedin.com/jobs/view/{index}/",
                "promoted": False,
                "easy_apply": False,
                "listed_on": "2026-07-03T00:59:13+04:00",
            }
        )
    rows[2]["title"] = "Senior Data Analyst"
    rows[7]["title"] = "Future Opportunities"
    rows[7]["company"] = "Talent Pool"
    rows[12]["location"] = "New York, United States"
    rows[20]["title"] = "Business Intelligence Analyst"
    rows[20]["easy_apply"] = True
    return rows


class CandidateScoringTaskTests(unittest.TestCase):
    def test_batches_rows_and_contract_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "candidate_scoring_task_state.json"
            clear_task_state(state_path)

            def fake_judge(_digitized_user, batch, *, batch_index, batch_count, llm_settings):
                if batch_index == 1:
                    excluded = [
                        {
                            "company": batch[2]["company"],
                            "listing_id": batch[2]["listing_id"],
                            "reason": "Hard constraint conflict.",
                        },
                        {
                            "company": batch[7]["company"],
                            "listing_id": batch[7]["listing_id"],
                            "reason": "Hard constraint conflict.",
                        },
                        {
                            "company": batch[12]["company"],
                            "listing_id": batch[12]["listing_id"],
                            "reason": "Hard constraint conflict.",
                        },
                    ]
                else:
                    excluded = [
                        {
                            "company": batch[0]["company"],
                            "listing_id": batch[0]["listing_id"],
                            "reason": "Hard constraint conflict.",
                        }
                    ]
                return excluded, "{\"excluded\": ...}", []

            with patch("tasks.candidate_scoring_task._judge_batch_with_llm", side_effect=fake_judge):
                result = run_candidate_scoring_task(
                    {
                        "task_name": "candidate_listing_scoring",
                        "task_id": "test-candidate-scoring",
                        "digitized_user": _digitized_user(),
                        "candidates": _candidates(),
                    },
                    state_path=state_path,
                    verbose=False,
                    step_delay_seconds=0,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["total_candidates"], 35)
            self.assertEqual(result["summary"]["batch_count"], 3)
            self.assertEqual(len(result["scored_candidates"]), 35)
            self.assertEqual(len(result["kept_candidates"]) + len(result["excluded_candidates"]), 35)
            self.assertEqual(result["next_stage_candidates"], result["kept_candidates"])
            self.assertEqual(result["result"]["digitized_user"]["identity"]["full_name"], "Omar Saeed Al Shamsi")

            first = result["scored_candidates"][2]
            self.assertEqual(first["decision"], "exclude")
            self.assertEqual(first["exclude_reason_code"], "constraint_conflict")
            self.assertTrue(first["exclude_reason_text"])

            second = result["scored_candidates"][7]
            self.assertEqual(second["decision"], "exclude")
            self.assertEqual(second["exclude_reason_code"], "constraint_conflict")

            third = result["scored_candidates"][12]
            self.assertEqual(third["decision"], "exclude")
            self.assertEqual(third["exclude_reason_code"], "constraint_conflict")

            batch_sizes = [batch["candidate_count"] for batch in result["batches"]]
            self.assertEqual(batch_sizes, [15, 15, 5])
            self.assertEqual(result["result"]["summary"]["batch_count"], 3)
            self.assertIn("reason_histogram", result["result"]["summary"])
            self.assertTrue(all("decision" in row and "score" in row and "exclude_reason_code" in row for row in result["scored_candidates"]))
            self.assertEqual(read_task_state(state_path)["status"], "success")

    def test_missing_digitized_user_is_partial_but_not_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "candidate_scoring_task_state.json"
            clear_task_state(state_path)

            result = run_candidate_scoring_task(
                {
                    "task_name": "candidate_listing_scoring",
                    "task_id": "test-candidate-missing-profile",
                    "candidates": _candidates()[:3],
                },
                state_path=state_path,
                verbose=False,
                step_delay_seconds=0,
            )

            self.assertEqual(result["status"], "partial")
            self.assertIn("digitized_user", result["missing_fields"])
            self.assertEqual(result["result"]["digitized_user"], {})
            self.assertEqual(read_task_state(state_path)["status"], "partial")


if __name__ == "__main__":
    unittest.main()
