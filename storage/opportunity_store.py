"""
Unified Opportunity store — opportunities, contacts, outreach_attempts.

Shares data/jobs.db with JobStore; adds parallel tables for the engine layer.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from engine.opportunity import Opportunity, job_to_opportunity, signal_to_opportunity

logger = logging.getLogger("opportunity_store")

DB_PATH = Path(__file__).parent.parent / "data" / "jobs.db"


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class OpportunityStore:
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
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY,
                    track TEXT DEFAULT 'visible',
                    source_ref TEXT DEFAULT '',
                    source_type TEXT DEFAULT 'job',
                    company TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    location TEXT DEFAULT '',
                    job_url TEXT DEFAULT '',
                    job_url_direct TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    score INTEGER DEFAULT 0,
                    decision TEXT DEFAULT 'skip',
                    sps INTEGER,
                    ips INTEGER,
                    recommended_action TEXT DEFAULT 'monitor',
                    apply_mode TEXT DEFAULT '',
                    referral_status TEXT DEFAULT 'none',
                    outreach_level INTEGER DEFAULT 0,
                    outreach_channel TEXT DEFAULT '',
                    job_id INTEGER,
                    signal_id TEXT DEFAULT '',
                    engine_json TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    role TEXT DEFAULT '',
                    name TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    linkedin_url TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    warm_lead_score REAL,
                    open_profile INTEGER DEFAULT 0,
                    shared_group INTEGER DEFAULT 0,
                    is_connection INTEGER DEFAULT 0,
                    verified_email INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outreach_attempts (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    contact_id TEXT DEFAULT '',
                    level INTEGER DEFAULT 1,
                    channel TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    draft_message TEXT DEFAULT '',
                    human_gate_required INTEGER DEFAULT 1,
                    attempted_at TEXT DEFAULT '',
                    response_at TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    warm_lead_score REAL DEFAULT 0.0,
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT '',
                    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
                )
            """)
            try:
                conn.execute(
                    "ALTER TABLE outreach_attempts ADD COLUMN warm_lead_score REAL DEFAULT 0.0"
                )
            except sqlite3.OperationalError:
                pass
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_action ON opportunities(recommended_action)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_sps ON opportunities(sps DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_track ON opportunities(track)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opp_job_id ON opportunities(job_id)"
            )
            conn.commit()

    def upsert_opportunity(self, opp: Opportunity) -> str:
        if not opp.id:
            opp.id = str(uuid.uuid4())
        now = _now_iso()
        if not opp.created_at:
            opp.created_at = now
        opp.updated_at = now

        fields = opp.to_dict()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM opportunities WHERE id = ?", (opp.id,)
            ).fetchone()
            if not existing and opp.job_id:
                row = conn.execute(
                    "SELECT id FROM opportunities WHERE job_id = ? LIMIT 1",
                    (opp.job_id,),
                ).fetchone()
                if row:
                    opp.id = row["id"]
                    fields["id"] = opp.id

            if existing or (opp.job_id and conn.execute(
                "SELECT 1 FROM opportunities WHERE job_id = ?", (opp.job_id,)
            ).fetchone()):
                key = "id"
                key_val = opp.id
                if not existing and opp.job_id:
                    key = "job_id"
                    key_val = opp.job_id
                sets = ", ".join(f"{k} = ?" for k in fields if k != "id")
                vals = [fields[k] for k in fields if k != "id"] + [key_val]
                conn.execute(
                    f"UPDATE opportunities SET {sets} WHERE {key} = ?", vals
                )
            else:
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO opportunities ({cols}) VALUES ({placeholders})",
                    list(fields.values()),
                )
            conn.commit()
        return opp.id

    def upsert_from_job(self, job: dict) -> str:
        opp = job_to_opportunity(job)
        if job.get("opportunity_id"):
            opp.id = job["opportunity_id"]
        oid = self.upsert_opportunity(opp)
        job["opportunity_id"] = oid
        return oid

    def upsert_from_signal(self, signal: dict) -> str:
        opp = signal_to_opportunity(signal)
        oid = self.upsert_opportunity(opp)
        signal["opportunity_id"] = oid
        return oid

    def get_opportunity(self, opp_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
            ).fetchone()
            return dict(row) if row else None

    def fetch_action_queue(
        self,
        *,
        gcc_only: bool = False,
        limit: int = 200,
        exclude_ignore: bool = True,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if exclude_ignore:
            clauses.append("recommended_action NOT IN ('ignore')")
        sql = "SELECT * FROM opportunities"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(sps, 0) DESC, score DESC, updated_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params)]

        if gcc_only:
            from storage.job_store import GCC_LOCATION_KEYWORDS
            rows = [
                r for r in rows
                if any(k in (r.get("location") or "").lower() for k in GCC_LOCATION_KEYWORDS)
            ]
        return rows

    def fetch_referral_blocked(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM opportunities
                WHERE recommended_action = 'referral_first'
                  AND referral_status = 'requested'
                ORDER BY COALESCE(sps, 0) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_contact(self, contact: dict) -> str:
        cid = contact.get("id") or str(uuid.uuid4())
        now = _now_iso()
        fields = {
            "id": cid,
            "opportunity_id": contact.get("opportunity_id") or "",
            "role": contact.get("role") or "",
            "name": contact.get("name") or "",
            "title": contact.get("title") or "",
            "linkedin_url": contact.get("linkedin_url") or "",
            "email": contact.get("email") or "",
            "warm_lead_score": contact.get("warm_lead_score"),
            "open_profile": 1 if contact.get("open_profile") else 0,
            "shared_group": 1 if contact.get("shared_group") else 0,
            "is_connection": 1 if contact.get("is_connection") else 0,
            "verified_email": 1 if contact.get("verified_email") else 0,
            "notes": contact.get("notes") or "",
            "updated_at": now,
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE id = ?", (cid,)
            ).fetchone()
            if existing:
                sets = ", ".join(f"{k} = ?" for k in fields if k != "id")
                vals = [fields[k] for k in fields if k != "id"] + [cid]
                conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", vals)
            else:
                fields["created_at"] = now
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO contacts ({cols}) VALUES ({placeholders})",
                    list(fields.values()),
                )
            conn.commit()
        return cid

    def list_contacts(self, opportunity_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE opportunity_id = ? ORDER BY warm_lead_score DESC",
                (opportunity_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_outreach_attempt(self, attempt: dict) -> str:
        aid = attempt.get("id") or str(uuid.uuid4())
        now = _now_iso()
        fields = {
            "id": aid,
            "opportunity_id": attempt.get("opportunity_id") or "",
            "contact_id": attempt.get("contact_id") or "",
            "level": int(attempt.get("level") or 1),
            "channel": attempt.get("channel") or "",
            "status": attempt.get("status") or "pending",
            "draft_message": (attempt.get("draft_message") or "")[:8000],
            "human_gate_required": 1 if attempt.get("human_gate_required", True) else 0,
            "attempted_at": attempt.get("attempted_at") or "",
            "response_at": attempt.get("response_at") or "",
            "notes": attempt.get("notes") or "",
            "warm_lead_score": float(attempt.get("warm_lead_score") or 0.0),
            "updated_at": now,
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM outreach_attempts WHERE id = ?", (aid,)
            ).fetchone()
            if existing:
                sets = ", ".join(f"{k} = ?" for k in fields if k != "id")
                vals = [fields[k] for k in fields if k != "id"] + [aid]
                conn.execute(f"UPDATE outreach_attempts SET {sets} WHERE id = ?", vals)
            else:
                fields["created_at"] = now
                cols = ", ".join(fields.keys())
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO outreach_attempts ({cols}) VALUES ({placeholders})",
                    list(fields.values()),
                )
            conn.commit()
        return aid

    def list_outreach_attempts(self, opportunity_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outreach_attempts
                WHERE opportunity_id = ?
                ORDER BY level ASC, created_at ASC
                """,
                (opportunity_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_active_outreach_attempt(self, opportunity_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM outreach_attempts
                WHERE opportunity_id = ?
                  AND status IN ('pending', 'draft_ready', 'sent')
                ORDER BY level ASC
                LIMIT 1
                """,
                (opportunity_id,),
            ).fetchone()
        return dict(row) if row else None

    def fetch_all_outreach_attempts(self, limit: int = 500) -> list[dict]:
        """Fetch all outreach attempts with opportunity/contact details for GUI display."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    a.*,
                    o.company,
                    o.title,
                    o.sps,
                    o.ips,
                    o.track,
                    c.name as contact_name,
                    c.title as contact_title,
                    c.linkedin_url
                FROM outreach_attempts a
                LEFT JOIN opportunities o ON a.opportunity_id = o.id
                LEFT JOIN contacts c ON a.contact_id = c.id
                WHERE a.status IN ('pending', 'draft_ready')
                ORDER BY COALESCE(o.sps, 0) DESC, a.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


_store: Optional[OpportunityStore] = None


def get_opportunity_store(db_path: Path = None) -> OpportunityStore:
    global _store
    if _store is None or (db_path and Path(db_path) != _store.db_path):
        _store = OpportunityStore(db_path)
    return _store
