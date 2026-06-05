"""
Notion Bootstrap
Run this ONCE to create the JobHunter database in your Notion workspace.
It will print the database ID — save it in Profile Settings (profile_settings.json).

Usage:
  python setup_notion.py --page-id YOUR_NOTION_PAGE_ID

How to get your page ID:
  1. Open the Notion page where you want the database
  2. Click Share > Copy link
  3. The page ID is the last 32-char string in the URL
     e.g. https://notion.so/My-Workspace/abc123...def456
     page ID = abc123...def456 (without dashes in the URL)
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(__file__))
from config.env_settings import bootstrap_settings, update_env_keys
bootstrap_settings()

from agents.notion_logger import create_database


def main():
    parser = argparse.ArgumentParser(description="Create JobHunter Notion database")
    parser.add_argument(
        "--page-id", required=True,
        help="Notion page ID where the database will be created"
    )
    parser.add_argument(
        "--title", default="JobHunter Tracker",
        help="Database title"
    )
    args = parser.parse_args()

    token = os.getenv("NOTION_TOKEN", "")
    if not token:
        print("ERROR: NOTION_TOKEN not set — add it in GUI Profile Settings")
        print("Get it from: https://www.notion.so/my-integrations")
        sys.exit(1)

    print(f"Creating '{args.title}' in page: {args.page_id}")

    try:
        db_id = create_database(
            token=token,
            parent_page_id=args.page_id,
            title=args.title,
        )
        print(f"\n✓ Database created!")
        print(f"\nNotion database ID:")
        print(f"  NOTION_DATABASE_ID={db_id}")
        save = input("\nSave to profile_settings.json now? [Y/n]: ").strip().lower()
        if save != "n":
            update_env_keys({"NOTION_DATABASE_ID": db_id})
            print("  Saved to data/profile_settings.json")
    except Exception as e:
        print(f"\n✗ Failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Make sure your integration is connected to the page")
        print("  2. Go to the page → Share → Connections → Add your integration")
        sys.exit(1)


if __name__ == "__main__":
    main()
