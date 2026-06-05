"""Reset JobHunter state: clear seen URLs and Notion database rows."""

import json
import os
import sys
from pathlib import Path

import requests
sys.path.insert(0, os.path.dirname(__file__))
from config.env_settings import bootstrap_settings
bootstrap_settings()

from agents.notion_logger import _headers

SEEN_URLS_FILE = Path("data/seen_urls.json")
SEEN_URLS_FILE.parent.mkdir(exist_ok=True)
SEEN_URLS_FILE.write_text("[]")
print("Cleared seen URLs")

token = os.getenv("NOTION_TOKEN", "")
db_id = os.getenv("NOTION_DATABASE_ID", "")
if not token or not db_id:
    print("Notion not configured - skipped database clear")
    sys.exit(0)

archived = 0
cursor = None
while True:
    payload = {"page_size": 100}
    if cursor:
        payload["start_cursor"] = cursor

    resp = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=_headers(token),
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        print("Failed to query Notion:", resp.status_code, resp.text[:300])
        sys.exit(1)

    data = resp.json()
    for page in data.get("results", []):
        page_id = page["id"]
        del_resp = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_headers(token),
            json={"archived": True},
            timeout=30,
        )
        if del_resp.status_code == 200:
            archived += 1

    if not data.get("has_more"):
        break
    cursor = data.get("next_cursor")

print(f"Archived {archived} Notion rows")
