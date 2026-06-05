"""Quick checks for job_fit prefilter (no browser)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from agents.job_fit import prefilter_job, is_ai_agent_only_job

G42_DESC = """
AI Agents Only – Developer Submission Required
This role is exclusively open to AI Agents. Applications from individual candidates
will not be considered. Submissions must be made by a developer representing an AI Agent.
"""

cases = [
    {
        "title": "Legal Intelligence Agent",
        "company": "G42",
        "description": G42_DESC,
        "expect_block": True,
    },
    {
        "title": "Quantitative Analyst",
        "company": "Brevan Howard",
        "description": "0-2 years Python statistics. Analyst program.",
        "expect_block": False,
    },
    {
        "title": "Senior Portfolio Manager",
        "company": "ADNOC",
        "description": "10+ years experience required.",
        "expect_block": True,
    },
]

ok = True
for c in cases:
    blocked, reason = prefilter_job(c)
    if blocked != c["expect_block"]:
        print(f"FAIL {c['title']}: blocked={blocked} reason={reason}")
        ok = False
    else:
        print(f"OK   {c['title']}: {'SKIP' if blocked else 'PASS'} — {reason or 'eligible'}")

ai, _ = is_ai_agent_only_job(cases[0])
print(f"\nG42 AI-agent detect: {ai}")
sys.exit(0 if ok and ai else 1)
