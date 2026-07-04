from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tasks.detail_scoring_task import clear_task_state, read_task_state, run_detail_scoring_task


def _digitized_user() -> dict[str, object]:
    return {
        "identity": {"full_name": "Omar Saeed Al Shamsi", "headline": "Software Engineer"},
        "contact": {"email": "omar@example.com", "phone": "+971500000000", "location": "Dubai, United Arab Emirates"},
        "links": {"linkedin_url": "https://www.linkedin.com/in/example", "github_url": "", "website_url": ""},
        "summary": "Recent graduate with strong software engineering fundamentals.",
        "education": [{"title": "BSc Computer Science", "details": ["UAE university"]}],
        "experience": [{"title": "Intern", "details": ["Built Python tools"]}],
        "projects": [{"name": "Capstone", "details": ["Built a dashboard"]}],
        "skills": ["Python", "SQL", "Docker", "Linux"],
        "languages": ["English", "Arabic"],
        "certifications": ["AWS Cloud Practitioner"],
        "preferences": {
            "roles": ["Software Engineer", "Backend Engineer"],
            "industries": {"high_priority": ["Technology"], "also_interested": ["Education"]},
            "work_style": {"ideal": ["Hybrid"], "acceptable": ["On-site"]},
            "compensation": {"ideal": ["AED 18,000+"], "comfortable": ["AED 14,000+"], "lower_if": ["strong mentorship"]},
            "commute": {"preferred": ["Dubai"], "comfortable": ["Abu Dhabi"], "would_relocate": []},
            "company_size": {"preferred": ["Midsize"], "also_interested": ["Large"]},
            "trade_offs": {"salary": ["learning"], "remote_work": ["growth"], "job_title": ["ownership"], "prestige": ["brand"]},
        },
        "constraints": {"hard_no": ["Commission-only", "Unpaid"], "must_have": ["Growth"], "nice_to_haves": ["Mentorship"]},
        "source_coverage": {"field_sources": {}, "documents": []},
        "completeness": {"required_complete": True, "ready_for_scoring": True, "missing_fields": [], "notes": [], "confidence_score": 95},
    }


def _detail_rows() -> list[dict[str, object]]:
    return [
        {
            "listing": {
                "listing_id": "1001",
                "job_id": "1001",
                "title": "Software Engineer",
                "company": "Bright Tech",
                "location": "Dubai, Dubai, United Arab Emirates (On-site)",
                "link": "https://www.linkedin.com/jobs/view/1001/",
                "promoted": False,
                "easy_apply": True,
                "listed_on": "2026-07-01T00:00:00+04:00",
            },
            "detail": {
                "job_id": "1001",
                "title": "Software Engineer",
                "company": "Bright Tech",
                "location": "Dubai, Dubai, United Arab Emirates (On-site)",
                "posted_at": "2 days ago",
                "apply_activity": "100+ applicants",
                "promotion_status": "",
                "application_management": "",
                "response_insights": "",
                "listing_preferences": ["Hybrid"],
                "job_description": {"raw_text": "Entry-level software engineer role with mentorship, growth, Python, SQL and cloud tools."},
                "company_profile": {"name": "Bright Tech", "url": "/company/bright-tech/", "industry": "Technology", "size": "51-200 employees", "linkedin_employee_count": "75 on LinkedIn", "description": "Software company focused on growth and training."},
                "hiring_team": [],
            },
        },
        {
            "listing": {
                "listing_id": "1002",
                "job_id": "1002",
                "title": "Future Opportunities",
                "company": "Talent Pool",
                "location": "Remote",
                "link": "https://www.linkedin.com/jobs/view/1002/",
                "promoted": False,
                "easy_apply": False,
                "listed_on": None,
            },
            "detail": {
                "job_id": "1002",
                "title": "Future Opportunities",
                "company": "Talent Pool",
                "location": "Remote",
                "posted_at": "",
                "apply_activity": "",
                "promotion_status": "",
                "application_management": "",
                "response_insights": "",
                "listing_preferences": [],
                "job_description": {"raw_text": "Join our talent pool for future opportunities. Commission-only, open application."},
                "company_profile": {"name": "Talent Pool", "url": "/company/talent-pool/", "industry": "Staffing", "size": "", "linkedin_employee_count": "", "description": "General opportunities."},
                "hiring_team": [],
            },
        },
        {
            "listing": {
                "listing_id": "1003",
                "job_id": "1003",
                "title": "Backend Developer",
                "company": "Edu Systems",
                "location": "Abu Dhabi, Abu Dhabi Emirate, United Arab Emirates (On-site)",
                "link": "https://www.linkedin.com/jobs/view/1003/",
                "promoted": False,
                "easy_apply": False,
                "listed_on": "2026-06-30T00:00:00+04:00",
            },
            "detail": {
                "job_id": "1003",
                "title": "Backend Developer",
                "company": "Edu Systems",
                "location": "Abu Dhabi, Abu Dhabi Emirate, United Arab Emirates (On-site)",
                "posted_at": "1 week ago",
                "apply_activity": "25 applicants",
                "promotion_status": "",
                "application_management": "",
                "response_insights": "",
                "listing_preferences": ["On-site"],
                "job_description": {"raw_text": "Developer role focused on Django, APIs, Docker, and Linux with strong learning path."},
                "company_profile": {"name": "Edu Systems", "url": "/company/edu-systems/", "industry": "Education", "size": "201-500 employees", "linkedin_employee_count": "40 on LinkedIn", "description": "Education technology business."},
                "hiring_team": [],
            },
        },
    ]


class DetailScoringTaskTests(unittest.TestCase):
    def test_batches_rows_and_computes_section_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "detail_scoring_task_state.json"
            clear_task_state(state_path)

            def fake_judge(_digitized_user, batch, *, batch_index, batch_count, llm_settings):
                excluded = []
                for row in batch:
                    if row["listing_id"] == "1002":
                        excluded.append(
                            {
                                "company": row["listing"]["company"],
                                "listing_id": row["listing_id"],
                                "reason": "Vague talent-pool posting.",
                            }
                        )
                return excluded, "{\"excluded\": ...}", []

            with patch("tasks.detail_scoring_task._judge_batch_with_llm", side_effect=fake_judge):
                result = run_detail_scoring_task(
                    {
                        "task_name": "detail_listing_scoring",
                        "task_id": "test-detail-scoring",
                        "digitized_user": _digitized_user(),
                        "detail_rows": _detail_rows(),
                        "batch_size": 2,
                    },
                    state_path=state_path,
                    verbose=False,
                    step_delay_seconds=0,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["summary"]["total_rows"], 3)
            self.assertEqual(result["summary"]["batch_count"], 2)
            self.assertEqual(len(result["scored_detail_rows"]), 3)
            self.assertEqual(len(result["excluded_detail_rows"]), 1)
            self.assertEqual(len(result["kept_detail_rows"]), 2)
            self.assertEqual(result["next_stage_rows"], result["kept_detail_rows"])
            self.assertEqual(result["excluded_detail_rows"][0]["listing_id"], "1002")
            self.assertEqual(result["excluded_detail_rows"][0]["exclude_reason_code"], "detail_conflict")
            self.assertIn("sections", result["scored_detail_rows"][0])
            self.assertEqual(set(result["scored_detail_rows"][0]["sections"].keys()), {"compensation", "progression", "work_style", "relevance", "company_signal", "risks"})
            self.assertIn(result["scored_detail_rows"][0]["sections"]["work_style"]["result"], {"yes", "partial", "no"})
            self.assertTrue(all("fit_score" in row for row in result["scored_detail_rows"]))
            self.assertEqual(read_task_state(state_path)["status"], "success")
            self.assertTrue(all("llm_response" in batch and "llm_error" in batch for batch in result["batches"]))

    def test_missing_digitized_user_is_partial_but_not_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "detail_scoring_task_state.json"
            clear_task_state(state_path)

            result = run_detail_scoring_task(
                {
                    "task_name": "detail_listing_scoring",
                    "task_id": "test-detail-missing-profile",
                    "detail_rows": _detail_rows()[:1],
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
