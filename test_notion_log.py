"""One-off test: verify Notion database access and log a sample job."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from config.env_settings import bootstrap_settings
bootstrap_settings()

import requests
from agents.notion_logger import _headers, log_job

token = os.getenv("NOTION_TOKEN", "")
db_id = os.getenv("NOTION_DATABASE_ID", "")

if not token or not db_id:
    print("ERROR: NOTION_TOKEN and NOTION_DATABASE_ID must be set in Profile Settings")
    sys.exit(1)

print("Token set:", bool(token))
print("DB ID:", db_id)

resp = requests.get(
    f"https://api.notion.com/v1/databases/{db_id}",
    headers=_headers(token),
    timeout=30,
)
print("Database check:", resp.status_code)
if resp.status_code != 200:
    print("Error:", resp.text[:500])
    sys.exit(1)

title = resp.json().get("title", [{}])
name = title[0].get("plain_text", "Unknown") if title else "Unknown"
print("Database title:", name)

test_job = {
    "company": "JobHunter Test Co",
    "title": "Notion Logging Test Role",
    "location": "Abu Dhabi, UAE",
    "score": 99,
    "decision": "manual_review",
    "positioning_angle": "quant",
    "source": "linkedin",
    "apply_method": "test",
    "date_posted": "2026-05-25",
    "discovered_at": datetime.utcnow().isoformat(),
    "job_url": f"https://example.com/jobhuntrr-test-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
    "fit_reason": "Test entry from JobHunter to verify Notion logging works.",
    "skip_reason": "",
    "applied": False,
}

page_id = log_job(token, db_id, test_job, skip_if_exists=False)
if page_id:
    print("SUCCESS: logged test job")
    print("Page ID:", page_id)
    print("Check your Notion database for 'JobHunter Test Co'")
else:
    print("FAILED: could not log test job")
    sys.exit(1)
