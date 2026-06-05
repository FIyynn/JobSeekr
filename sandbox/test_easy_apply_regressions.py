"""
Offline regression checks for LinkedIn Easy Apply routing helpers.

These avoid opening a browser; fake locator objects cover the small Playwright
surface used by the resume-upload guard.
"""

from __future__ import annotations

import os
import sys
import csv
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apply_jobs import (
    _csv_row_should_apply,
    _filter_to_easy_apply_temp_list,
    _is_linkedin_easy_apply,
    _normalize_job_url,
    _resolve_jobs_from_easy_apply_csvs,
    _scan_jobs_for_easy_apply,
    _write_easy_apply_temp_list,
    EASY_APPLY_CSV,
)
from agents.form_filler import (
    _linkedin_easy_apply_needs_resume_upload,
    _linkedin_is_submit_application_label,
)


class _FakeLocator:
    def __init__(self, *, visible: bool = False, count: int = 0):
        self._visible = visible
        self._count = count
        self.first = self

    def count(self):
        return self._count

    def is_visible(self, timeout=0):
        return self._visible

    def all(self):
        return [self] if self._count else []


class _FakeRoot:
    def __init__(self, *, change_resume=False, selected_resume=False, upload=False, file_visible=False):
        self.change_resume = change_resume
        self.selected_resume = selected_resume
        self.upload = upload
        self.file_visible = file_visible

    def locator(self, selector: str):
        if "Change resume" in selector:
            return _FakeLocator(visible=self.change_resume, count=int(self.change_resume))
        if "jobs-document-upload__title" in selector or "[class*='resume']" in selector:
            return _FakeLocator(visible=self.selected_resume, count=int(self.selected_resume))
        if "Upload resume" in selector or "label[for*='upload'" in selector:
            return _FakeLocator(visible=self.upload, count=int(self.upload))
        if selector == "input[type='file']":
            return _FakeLocator(visible=self.file_visible, count=int(self.file_visible))
        return _FakeLocator()


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: got={got!r} expected={expected!r}")
    return ok


def main() -> int:
    ok = True
    ok &= check(
        "method Easy Apply is kept",
        _is_linkedin_easy_apply({
            "source": "linkedin",
            "apply_method": "Easy Apply",
            "job_url": "https://www.linkedin.com/jobs/view/1",
        }),
        True,
    )
    ok &= check(
        "LinkedIn direct URL is external",
        _is_linkedin_easy_apply({
            "source": "linkedin",
            "apply_method": "Apply",
            "job_url": "https://www.linkedin.com/jobs/view/2",
            "job_url_direct": "https://boards.greenhouse.io/acme/jobs/2",
        }),
        False,
    )
    ok &= check(
        "mislabeled Workday direct URL is excluded",
        _is_linkedin_easy_apply({
            "source": "linkedin",
            "apply_method": "Easy Apply",
            "job_url": "https://www.linkedin.com/jobs/view/22",
            "job_url_direct": "https://acme.wd5.myworkdayjobs.com/job/22",
        }),
        False,
    )
    ok &= check(
        "unverified LinkedIn label is not Easy Apply queue",
        _is_linkedin_easy_apply({
            "source": "linkedin",
            "apply_method": "LinkedIn",
            "job_url": "https://www.linkedin.com/jobs/view/3",
            "job_url_direct": "",
        }),
        False,
    )
    ok &= check(
        "blank method is not Easy Apply until verified",
        _is_linkedin_easy_apply({
            "source": "linkedin",
            "apply_method": "",
            "job_url": "https://www.linkedin.com/jobs/view/4",
            "job_url_direct": "",
        }),
        False,
    )
    pending_jobs = [
        {
            "source": "linkedin",
            "apply_method": "Easy Apply",
            "job_url": "https://www.linkedin.com/jobs/view/101/",
            "job_url_direct": "",
        },
        {
            "source": "linkedin",
            "apply_method": "Easy Apply",
            "job_url": "https://www.linkedin.com/jobs/view/202",
            "job_url_direct": "https://example.wd5.myworkdayjobs.com/job/202",
        },
    ]
    allowlist = _write_easy_apply_temp_list(pending_jobs)
    ok &= check(
        "temp allowlist contains only real Easy Apply URL",
        allowlist,
        {_normalize_job_url("https://www.linkedin.com/jobs/view/101/")},
    )
    ok &= check(
        "filter only uses temp allowlist",
        len(_filter_to_easy_apply_temp_list(pending_jobs, allowlist)),
        1,
    )
    ok &= check(
        "scanner applies only Easy Apply rows",
        len(_scan_jobs_for_easy_apply(pending_jobs, allowlist)),
        1,
    )
    ok &= check(
        "selected LinkedIn resume is not replaced",
        _linkedin_easy_apply_needs_resume_upload(_FakeRoot(change_resume=True)),
        False,
    )
    ok &= check(
        "explicit upload request uploads",
        _linkedin_easy_apply_needs_resume_upload(_FakeRoot(upload=True)),
        True,
    )
    ok &= check(
        "hidden file input alone does not upload",
        _linkedin_easy_apply_needs_resume_upload(_FakeRoot(file_visible=False)),
        False,
    )
    ok &= check(
        "Submit application label accepted",
        _linkedin_is_submit_application_label("Submit application"),
        True,
    )
    ok &= check(
        "bare Submit label rejected",
        _linkedin_is_submit_application_label("Submit"),
        False,
    )
    ok &= check(
        "Next label rejected as submit",
        _linkedin_is_submit_application_label("Next"),
        False,
    )
    ok &= check(
        "pending queue filters Easy Apply rows",
        len(_scan_jobs_for_easy_apply(pending_jobs[:1], allowlist)),
        1,
    )
    ok &= check(
        "csv action apply row selected",
        _csv_row_should_apply({"action": "apply", "job_url": "https://www.linkedin.com/jobs/view/1"}),
        True,
    )
    ok &= check(
        "csv action skip row rejected",
        _csv_row_should_apply({"action": "skip", "apply_method": "Easy Apply"}),
        False,
    )
    ok &= check(
        "exported visible csv Easy Apply row selected",
        _csv_row_should_apply({
            "job_url": "https://www.linkedin.com/jobs/view/9",
            "apply_method": "Easy Apply",
            "decision": "auto_apply",
        }),
        True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "easy_apply.csv"
        job_url = "https://www.linkedin.com/jobs/view/501/"
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "row", "job_url", "title", "company", "apply_method",
                    "method_resolved", "job_url_direct", "action",
                ],
            )
            writer.writeheader()
            writer.writerow({
                "row": 1,
                "job_url": job_url,
                "title": "Analyst",
                "company": "Acme",
                "apply_method": "Easy Apply",
                "method_resolved": "Easy Apply",
                "job_url_direct": "",
                "action": "apply",
            })
        from storage.job_store import JobStore
        store = JobStore(db_path=Path(tmp) / "test_jobs.db")
        store.upsert_job({
            "job_url": job_url,
            "title": "Analyst",
            "company": "Acme",
            "apply_method": "Easy Apply",
            "decision": "auto_apply",
            "source": "linkedin",
        }, skip_if_exists=False)
        import apply_jobs as apply_jobs_module
        old_get_store = apply_jobs_module.get_store
        apply_jobs_module.get_store = lambda: store
        try:
            loaded = _resolve_jobs_from_easy_apply_csvs([csv_path])
        finally:
            apply_jobs_module.get_store = old_get_store
        ok &= check("csv loader returns Easy Apply job", len(loaded), 1)
        ok &= check(
            "csv loader keeps LinkedIn job URL",
            loaded[0].get("job_url"),
            job_url,
        )
    ok &= check(
        "placeholder csv row without db job is skipped",
        len(_resolve_jobs_from_easy_apply_csvs([EASY_APPLY_CSV])),
        0,
    )
    ok &= check(
        "default easy apply csv path is under data/",
        str(EASY_APPLY_CSV).replace("\\", "/").endswith("data/easy_apply_jobs.csv"),
        True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
