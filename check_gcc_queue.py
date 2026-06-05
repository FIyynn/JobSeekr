"""List GCC Auto Apply queue and prefilter results (no browser)."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from config.env_settings import bootstrap_settings
bootstrap_settings()
from agents.job_logger import fetch_pending_apply
from agents.job_fit import prefilter_job

jobs = fetch_pending_apply(gcc_only=True)
print(f"GCC Auto Apply pending: {len(jobs)}\n")
keep, skip = 0, 0
for j in jobs:
    blocked, reason = prefilter_job(j)
    if blocked:
        skip += 1
        print(f"  [SKIP] {j['title']} @ {j['company']}")
        print(f"         {reason}")
    else:
        keep += 1
        print(f"  [KEEP] {j['title']} @ {j['company']}")
print(f"\nAfter prefilter: {keep} keep, {skip} skip")
