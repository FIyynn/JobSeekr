"""
Local SQLite job tracker — same fields as Notion, no cloud required.
Database: data/jobs.db
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("job_store")

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"

DECISION_DISPLAY = {
    "discovered": "Pending Score",
    "auto_apply": "Auto Apply",
    "manual_review": "Manual Review",
    "skip": "Skipped",
    "applied": "Applied",
    "closed": "Closed",
    "excluded": "Excluded",
}

DISPLAY_TO_DECISION = {v: k for k, v in DECISION_DISPLAY.items()}
DISPLAY_TO_DECISION["Applied"] = "applied"

GCC_LOCATION_KEYWORDS = (
    "uae", "united arab emirates", "dubai", "abu dhabi", "difc", "adgm",
    "qatar", "doha", "saudi", "riyadh", "bahrain", "kuwait", "oman",
    "gcc", "emirate", "sharjah", "ajman",
)


def dedupe_jobs(jobs: list[dict]) -> list[dict]:
    """Return one row per listing URL, with a title/company fallback."""
    seen: set[str] = set()
    out = []
    for job in jobs:
        key = (job.get("job_url") or "").strip().lower()
        if not key:
            key = f"{job.get('title', '')}|{job.get('company', '')}".lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def filter_gcc_jobs(jobs: list[dict]) -> list[dict]:
    """Keep listings whose location mentions the configured GCC region."""
    return [
        job for job in jobs
        if any(
            keyword in (job.get("location") or "").lower()
            for keyword in GCC_LOCATION_KEYWORDS
        )
    ]


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release the file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class JobStore:
    def __init__(self, db_path: Path = None):
        self.db_path = Path(db_path or DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    location TEXT DEFAULT '',
                    score INTEGER DEFAULT 0,
                    decision TEXT DEFAULT 'skip',
                    decision_display TEXT DEFAULT 'Skipped',
                    positioning_angle TEXT DEFAULT '',
                    source TEXT DEFAULT 'linkedin',
                    apply_method TEXT DEFAULT '',
                    date_posted TEXT DEFAULT '',
                    discovered_at TEXT DEFAULT '',
                    job_url TEXT UNIQUE,
                    job_url_direct TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    fit_reason TEXT DEFAULT '',
                    skip_reason TEXT DEFAULT '',
                    applied INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    apply_notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_decision ON jobs(decision)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_applied ON jobs(applied)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC)"
            )
            self._migrate_columns(conn)
            self._create_submission_invariant_triggers(conn)
            conn.commit()

    def _migrate_columns(self, conn: sqlite3.Connection):
        """Add columns introduced after initial schema without breaking existing DBs."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "outside_target_industry" not in existing:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN outside_target_industry INTEGER DEFAULT 0"
            )
        if "outside_target_reason" not in existing:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN outside_target_reason TEXT DEFAULT ''"
            )
        new_cols = {
            "job_profile_json": "TEXT DEFAULT ''",
            "salary_snippet": "TEXT DEFAULT ''",
            "min_monthly_aed": "INTEGER",
            "max_monthly_aed": "INTEGER",
            "salary_below_minimum": "INTEGER DEFAULT 0",
            "matches_stated_targets": "INTEGER DEFAULT 1",
            "suggested_alternate": "INTEGER DEFAULT 0",
            "alternate_suggestion_reason": "TEXT DEFAULT ''",
            "submission_status": "TEXT DEFAULT ''",
            "submission_confirmed_at": "TEXT DEFAULT ''",
            "confirmation_url": "TEXT DEFAULT ''",
            "confirmation_text": "TEXT DEFAULT ''",
            "confirmation_checks": "INTEGER DEFAULT 0",
            "last_confirmation_check_at": "TEXT DEFAULT ''",
            "apply_attempts": "INTEGER DEFAULT 0",
            "last_apply_attempt_at": "TEXT DEFAULT ''",
            "sps": "INTEGER",
            "ips": "INTEGER",
            "apply_mode": "TEXT DEFAULT ''",
            "engine_action": "TEXT DEFAULT ''",
            "engine_json": "TEXT DEFAULT ''",
        }
        for col, typedef in new_cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
        if "notion_page_id" not in existing:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN notion_page_id TEXT DEFAULT ''"
            )
        self._repair_legacy_applied_rows(conn)
        self._repair_missing_skip_reasons(conn)
        self._repair_legacy_apply_methods(conn)

    @staticmethod
    def _repair_legacy_applied_rows(conn: sqlite3.Connection):
        """Stop legacy click-only outcomes from being treated as confirmed submissions."""
        rows = conn.execute(
            """
            SELECT id, apply_notes
            FROM jobs
            WHERE applied = 1
              AND COALESCE(submission_status, '') NOT IN ('confirmed', 'manual_confirmed')
            """
        ).fetchall()
        for row in rows:
            notes = row["apply_notes"] or ""
            low = notes.lower()
            if "already submitted (detected on linkedin job page)" in low:
                conn.execute(
                    """
                    UPDATE jobs
                    SET decision = 'applied',
                        decision_display = 'Applied',
                        submission_status = 'confirmed',
                        confirmation_text = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    ("LinkedIn job page showed an already-applied status", _now_iso(), row["id"]),
                )
                continue
            quarantine_note = notes or "Legacy applied row had no submission confirmation evidence"
            conn.execute(
                """
                UPDATE jobs
                SET applied = 0,
                    decision = 'manual_review',
                    decision_display = 'Manual Review',
                    submission_status = 'legacy_unverified',
                    apply_notes = ?,
                    notes = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (quarantine_note, quarantine_note, _now_iso(), row["id"]),
            )

    @staticmethod
    def _create_submission_invariant_triggers(conn: sqlite3.Connection):
        """Enforce the terminal-state invariant even for stale or external writers."""
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_jobs_applied_insert_requires_confirmation
            BEFORE INSERT ON jobs
            WHEN NEW.applied = 1
              AND COALESCE(NEW.submission_status, '') NOT IN ('confirmed', 'manual_confirmed')
            BEGIN
                SELECT RAISE(ABORT, 'applied jobs require confirmed submission evidence');
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_jobs_applied_update_requires_confirmation
            BEFORE UPDATE OF applied, submission_status ON jobs
            WHEN NEW.applied = 1
              AND COALESCE(NEW.submission_status, '') NOT IN ('confirmed', 'manual_confirmed')
            BEGIN
                SELECT RAISE(ABORT, 'applied jobs require confirmed submission evidence');
            END
            """
        )

    @staticmethod
    def _repair_legacy_apply_methods(conn: sqlite3.Connection):
        """Normalize only rows with a known external URL; never guess Easy Apply."""
        rows = conn.execute(
            "SELECT id, apply_method, job_url_direct FROM jobs"
        ).fetchall()
        for row in rows:
            stored = (row["apply_method"] or "").strip()
            direct = (row["job_url_direct"] or "").strip()
            if direct and stored.lower() in ("linkedin", "linked in", ""):
                conn.execute(
                    "UPDATE jobs SET apply_method = ? WHERE id = ?",
                    ("Apply", row["id"]),
                )

    @staticmethod
    def _repair_missing_skip_reasons(conn: sqlite3.Connection):
        """Backfill explanations for rows created before skip reasons were enforced."""
        conn.execute(
            """
            UPDATE jobs
            SET skip_reason = CASE
                    WHEN TRIM(COALESCE(fit_reason, '')) <> ''
                        THEN 'Skipped after scoring. ' || fit_reason
                    ELSE 'Skipped by an earlier scoring run without a recorded reason'
                END,
                updated_at = ?
            WHERE decision = 'skip'
              AND TRIM(COALESCE(skip_reason, '')) = ''
            """,
            (_now_iso(),),
        )

    def _row_to_job(self, row: sqlite3.Row) -> dict:
        if row is None:
            return {}
        d = dict(row)
        job = {
            "id": d["id"],
            "job_id": d["id"],
            "company": d["company"] or "",
            "title": d["title"] or "",
            "location": d["location"] or "",
            "score": d["score"] or 0,
            "decision": d["decision"] or "skip",
            "decision_display": (
                DECISION_DISPLAY.get("discovered")
                if d["decision"] == "discovered" and (d["score"] or 0) == 0
                else d["decision_display"] or DECISION_DISPLAY.get(d["decision"], "Skipped")
            ),
            "positioning_angle": d["positioning_angle"] or "investments",
            "source": d["source"] or "",
            "apply_method": d["apply_method"] or "",
            "date_posted": d["date_posted"] or "",
            "discovered_at": d["discovered_at"] or "",
            "job_url": d["job_url"] or "",
            "job_url_direct": d["job_url_direct"] or "",
            "description": d["description"] or "",
            "fit_reason": d["fit_reason"] or "",
            "skip_reason": d["skip_reason"] or "",
            "applied": bool(d["applied"]),
            "notes": d["notes"] or "",
            "apply_notes": d["apply_notes"] or "",
            "outside_target_industry": bool(d.get("outside_target_industry")),
            "outside_target_reason": d.get("outside_target_reason") or "",
            "job_profile_json": d.get("job_profile_json") or "",
            "salary_snippet": d.get("salary_snippet") or "",
            "min_monthly_aed": d.get("min_monthly_aed"),
            "max_monthly_aed": d.get("max_monthly_aed"),
            "salary_below_minimum": bool(d.get("salary_below_minimum")),
            "matches_stated_targets": bool(d.get("matches_stated_targets", 1)),
            "suggested_alternate": bool(d.get("suggested_alternate")),
            "alternate_suggestion_reason": d.get("alternate_suggestion_reason") or "",
            "notion_page_id": d.get("notion_page_id") or "",
            "submission_status": d.get("submission_status") or "",
            "submission_confirmed_at": d.get("submission_confirmed_at") or "",
            "confirmation_url": d.get("confirmation_url") or "",
            "confirmation_text": d.get("confirmation_text") or "",
            "confirmation_checks": int(d.get("confirmation_checks") or 0),
            "last_confirmation_check_at": d.get("last_confirmation_check_at") or "",
            "apply_attempts": int(d.get("apply_attempts") or 0),
            "last_apply_attempt_at": d.get("last_apply_attempt_at") or "",
            "sps": d.get("sps"),
            "ips": d.get("ips"),
            "apply_mode": d.get("apply_mode") or "",
            "engine_action": d.get("engine_action") or "",
            "engine_json": d.get("engine_json") or "",
            "created_at": d["created_at"] or "",
            "updated_at": d["updated_at"] or "",
        }
        return job

    def job_exists(self, job_url: str) -> bool:
        if not job_url:
            return False
        with self._connect() as conn:
            r = conn.execute(
                "SELECT 1 FROM jobs WHERE job_url = ? LIMIT 1", (job_url,)
            ).fetchone()
            return r is not None

    def upsert_job(self, job: dict, skip_if_exists: bool = True) -> Optional[int]:
        """Insert or update job by job_url. Returns job id or None if skipped dedup."""
        url = (job.get("job_url") or "").strip()
        if skip_if_exists and url and self.job_exists(url):
            logger.debug(f"Already in DB: {job.get('title')} @ {job.get('company')}")
            return None

        decision = job.get("decision", "skip")
        display = DECISION_DISPLAY.get(decision, job.get("decision_display", "Skipped"))
        now = _now_iso()
        discovered = job.get("discovered_at") or now

        fields = {
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "score": int(job.get("score") or 0),
            "decision": decision,
            "decision_display": display,
            "positioning_angle": (job.get("positioning_angle") or "").strip(),
            "source": job.get("source", "linkedin"),
            "apply_method": job.get("apply_method", ""),
            "date_posted": str(job.get("date_posted", "")),
            "discovered_at": discovered,
            "job_url": url,
            "job_url_direct": job.get("job_url_direct", ""),
            "description": (job.get("description") or "")[:8000],
            "fit_reason": job.get("fit_reason", ""),
            "skip_reason": job.get("skip_reason", ""),
            "applied": 1 if job.get("applied") else 0,
            "notes": job.get("notes", "") or job.get("apply_notes", ""),
            "apply_notes": job.get("apply_notes", ""),
            "outside_target_industry": 1 if job.get("outside_target_industry") else 0,
            "outside_target_reason": job.get("outside_target_reason", "") or "",
            "job_profile_json": (job.get("job_profile_json") or "")[:12000],
            "salary_snippet": job.get("salary_snippet", "") or "",
            "min_monthly_aed": job.get("min_monthly_aed"),
            "max_monthly_aed": job.get("max_monthly_aed"),
            "salary_below_minimum": 1 if job.get("salary_below_minimum") else 0,
            "matches_stated_targets": 0 if job.get("matches_stated_targets") is False else 1,
            "suggested_alternate": 1 if job.get("suggested_alternate") else 0,
            "alternate_suggestion_reason": job.get("alternate_suggestion_reason", "") or "",
            "notion_page_id": job.get("notion_page_id", "") or "",
            "submission_status": job.get("submission_status", "") or "",
            "submission_confirmed_at": job.get("submission_confirmed_at", "") or "",
            "confirmation_url": job.get("confirmation_url", "") or "",
            "confirmation_text": job.get("confirmation_text", "") or "",
            "confirmation_checks": int(job.get("confirmation_checks") or 0),
            "last_confirmation_check_at": job.get("last_confirmation_check_at", "") or "",
            "apply_attempts": int(job.get("apply_attempts") or 0),
            "last_apply_attempt_at": job.get("last_apply_attempt_at", "") or "",
            "sps": int(job["sps"]) if job.get("sps") is not None else None,
            "ips": int(job["ips"]) if job.get("ips") is not None else None,
            "apply_mode": job.get("apply_mode") or "",
            "engine_action": job.get("engine_action") or "",
            "engine_json": (job.get("engine_json") or "")[:16000],
            "updated_at": now,
        }
        if fields["applied"] and fields["submission_status"] not in (
            "confirmed", "manual_confirmed"
        ):
            raise ValueError("applied jobs require confirmed submission evidence")

        with self._connect() as conn:
            if url:
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE job_url = ?", (url,)
                ).fetchone()
            else:
                existing = None

            if existing:
                # A confirmed submission is terminal. Rediscovery and rescoring may
                # improve metadata, but they must never put the job back in a queue.
                if (
                    existing["applied"]
                    and existing["submission_status"] in ("confirmed", "manual_confirmed")
                    and not fields["applied"]
                ):
                    fields.update({
                        "decision": "applied",
                        "decision_display": "Applied",
                        "applied": 1,
                        "notes": existing["notes"] or "",
                        "apply_notes": existing["apply_notes"] or "",
                        "submission_status": existing["submission_status"] or "confirmed",
                        "submission_confirmed_at": existing["submission_confirmed_at"] or "",
                        "confirmation_url": existing["confirmation_url"] or "",
                        "confirmation_text": existing["confirmation_text"] or "",
                        "confirmation_checks": existing["confirmation_checks"] or 0,
                        "last_confirmation_check_at": existing["last_confirmation_check_at"] or "",
                        "apply_attempts": existing["apply_attempts"] or 0,
                        "last_apply_attempt_at": existing["last_apply_attempt_at"] or "",
                    })
                sets = ", ".join(f"{k} = ?" for k in fields)
                vals = list(fields.values()) + [existing["id"]]
                conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
                conn.commit()
                return existing["id"]

            fields["created_at"] = now
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" * len(fields))
            cur = conn.execute(
                f"INSERT INTO jobs ({cols}) VALUES ({placeholders})",
                list(fields.values()),
            )
            conn.commit()
            jid = cur.lastrowid
            logger.info(
                f"  [OK] Saved [{fields['score']}/100 {decision}]: "
                f"{fields['title']} @ {fields['company']}"
            )
            return jid

    def log_jobs_batch(self, jobs: list[dict], update_existing: bool = False) -> dict:
        """Log jobs, optionally replacing rows previously saved during discovery."""
        logged, dedup, failed = 0, 0, 0
        for job in jobs:
            rid = self.upsert_job(job, skip_if_exists=not update_existing)
            if rid is None:
                dedup += 1
            elif rid:
                logged += 1
            else:
                failed += 1
        return {"logged": logged, "dedup_skipped": dedup, "failed": failed}

    def list_jobs(
        self,
        decision: str = None,
        applied: bool = None,
        gcc_only: bool = False,
        search: str = "",
        limit: int = 500,
        order_by: str = "score DESC, discovered_at DESC",
        include_closed: bool = False,
    ) -> list[dict]:
        clauses, params = [], []
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        elif not include_closed:
            clauses.append("decision <> 'closed'")
        if applied is not None:
            clauses.append("applied = ?")
            params.append(1 if applied else 0)
        if search:
            clauses.append(
                "(title LIKE ? OR company LIKE ? OR location LIKE ? OR notes LIKE ?)"
            )
            q = f"%{search}%"
            params.extend([q, q, q, q])

        sql = "SELECT * FROM jobs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_by} LIMIT ?"
        params.append(limit)

        rows = []
        with self._connect() as conn:
            for row in conn.execute(sql, params):
                job = self._row_to_job(row)
                if gcc_only and not self._is_gcc(job.get("location", "")):
                    continue
                rows.append(job)
        return rows

    def fetch_pending_apply(self, gcc_only: bool = False) -> list[dict]:
        """Same as Notion: Auto Apply + not applied."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET decision = 'manual_review',
                    decision_display = 'Manual Review',
                    apply_notes = CASE
                        WHEN TRIM(COALESCE(apply_notes, '')) = ''
                            THEN 'Automatic apply stopped after three unsuccessful live attempts'
                        ELSE apply_notes
                    END,
                    updated_at = ?
                WHERE decision = 'auto_apply'
                  AND applied = 0
                  AND COALESCE(apply_attempts, 0) >= 3
                """,
                (_now_iso(),),
            )
            conn.commit()
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE decision = 'auto_apply'
                  AND applied = 0
                  AND COALESCE(apply_attempts, 0) < 3
                ORDER BY score DESC, discovered_at DESC
                """
            ).fetchall()
        jobs = [self._row_to_job(row) for row in rows]
        if gcc_only:
            jobs = [j for j in jobs if self._is_gcc(j.get("location", ""))]
        return dedupe_jobs(jobs)

    def fetch_confirmation_pending(self, max_checks: int = 3) -> list[dict]:
        """Return uncertain click outcomes for evidence-only reconciliation."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE applied = 0
                  AND submission_status = 'confirmation_pending'
                  AND COALESCE(confirmation_checks, 0) < ?
                ORDER BY updated_at ASC
                """,
                (max_checks,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, job_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    def get_job_by_url(self, job_url: str) -> Optional[dict]:
        url = (job_url or "").strip()
        if not url:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_url = ? LIMIT 1", (url,)
            ).fetchone()
            return self._row_to_job(row) if row else None

    def update_job(self, job_id: int, **fields) -> bool:
        allowed = {
            "score", "decision", "decision_display", "fit_reason", "skip_reason",
            "applied", "notes", "apply_notes", "positioning_angle", "description",
            "apply_method", "job_url_direct",
            "outside_target_industry", "outside_target_reason",
            "job_profile_json", "salary_snippet", "min_monthly_aed", "max_monthly_aed",
            "salary_below_minimum", "matches_stated_targets", "suggested_alternate",
            "alternate_suggestion_reason",
            "notion_page_id",
            "submission_status", "submission_confirmed_at",
            "confirmation_url", "confirmation_text",
            "confirmation_checks", "last_confirmation_check_at",
            "apply_attempts", "last_apply_attempt_at",
            "sps", "ips", "apply_mode", "engine_action", "engine_json",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if updates.get("applied"):
            status = updates.get("submission_status", "")
            if status not in ("confirmed", "manual_confirmed"):
                raise ValueError("applied jobs require confirmed submission evidence")
        for flag in ("outside_target_industry", "salary_below_minimum", "suggested_alternate"):
            if flag in updates:
                updates[flag] = 1 if updates[flag] else 0
        if "matches_stated_targets" in updates:
            updates["matches_stated_targets"] = 0 if updates["matches_stated_targets"] is False else 1
        if "decision" in updates and "decision_display" not in updates:
            updates["decision_display"] = DECISION_DISPLAY.get(
                updates["decision"], updates["decision"]
            )
        if "applied" in updates:
            updates["applied"] = 1 if updates["applied"] else 0
            if updates["applied"]:
                updates["decision"] = "applied"
                updates["decision_display"] = "Applied"
        if not updates:
            return False
        updates["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
            conn.commit()
        return True

    def mark_applied(
        self,
        job_id: int,
        notes: str = "",
        applied: bool = True,
        *,
        submission_status: str = "",
        submission_confirmed_at: str = "",
        confirmation_url: str = "",
        confirmation_text: str = "",
        confirmation_checks: int = 0,
        last_confirmation_check_at: str = "",
        apply_attempts: int = 0,
        last_apply_attempt_at: str = "",
    ) -> bool:
        if applied:
            submission_status = submission_status or "manual_confirmed"
            submission_confirmed_at = submission_confirmed_at or _now_iso()
        return self.update_job(
            job_id,
            applied=applied,
            notes=notes,
            apply_notes=notes,
            submission_status=submission_status,
            submission_confirmed_at=submission_confirmed_at,
            confirmation_url=confirmation_url,
            confirmation_text=confirmation_text,
            confirmation_checks=confirmation_checks,
            last_confirmation_check_at=last_confirmation_check_at,
            apply_attempts=apply_attempts,
            last_apply_attempt_at=last_apply_attempt_at,
        )

    def delete_job(self, job_id: int) -> bool:
        """Permanently delete a job by id. Returns True on success."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            return cur.rowcount > 0

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            by_dec = dict(conn.execute(
                "SELECT decision, COUNT(*) FROM jobs GROUP BY decision"
            ).fetchall())
            pending = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE decision = 'auto_apply' AND applied = 0"
            ).fetchone()[0]
            pending_score = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE decision = 'discovered'"
            ).fetchone()[0]
            applied_n = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE applied = 1"
            ).fetchone()[0]
        return {
            "total": total,
            "by_decision": by_dec,
            "pending_apply": pending,
            "pending_score": pending_score,
            "applied": applied_n,
        }

    def revision(self) -> tuple[int, str]:
        """Cheap fingerprint used by the GUI to notice writes from other processes."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM jobs"
            ).fetchone()
        return int(row[0]), str(row[1])

    @staticmethod
    def _is_gcc(location: str) -> bool:
        loc = (location or "").lower()
        return any(kw in loc for kw in GCC_LOCATION_KEYWORDS)
