"""
Apply-flow probe. Runs the REAL apply engine in DRY-RUN (never submits) against a
given job URL, capturing the platform detected and the outcome. Used to test
Easy Apply / Greenhouse / Workday / Lever / Ashby / custom handling.

Usage:
  python sandbox/_apply_probe.py "<url>" "Title" "Company"
  python sandbox/_apply_probe.py --linkedin-from-json   # pick a discovered LI job
"""
import sys, os, json, logging, io, time
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.env_settings import bootstrap_settings
bootstrap_settings()

from config.config import OLLAMA_MODEL, OLLAMA_VISION_MODEL, OLLAMA_BASE_URL, APPLICATION_QA, CANDIDATE_PROFILE

# Capture logs to a buffer + stdout
log_buf = io.StringIO()
handler = logging.StreamHandler(log_buf)
handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stdout)],
                    format="%(levelname)s %(name)s: %(message)s")


def run_one(url, title, company, headless=True):
    from agents.form_filler import apply_to_job
    qa = dict(APPLICATION_QA)
    qa["resume_path"] = (json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "profile_settings.json"),
                                         encoding="utf-8")).get("resume_path") or qa.get("resume_path"))
    job = {"title": title, "company": company, "job_url": url, "job_url_direct": url,
           "location": "UAE", "description": "", "positioning_angle": "investments"}
    t0 = time.time()
    print(f"\n===== PROBE: {title} @ {company} =====\nURL: {url}\n")
    try:
        apply_to_job(job, qa, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL,
                     dry_run=True, headless=headless)
    except Exception as e:
        import traceback; traceback.print_exc()
        job["apply_notes"] = f"HARNESS ERROR: {e}"
    dt = round(time.time() - t0, 1)
    print(f"\n----- OUTCOME ({dt}s) -----")
    print("applied:", job.get("applied"))
    print("apply_notes:", job.get("apply_notes"))
    print("decision:", job.get("decision"))
    return job


def linkedin_from_json():
    """Run a discovered LinkedIn job through the batch apply (persistent session)."""
    from agents.form_filler import apply_jobs_batch
    path = os.path.join(os.path.dirname(__file__), "_search_results.json")
    jobs = json.load(open(path, encoding="utf-8"))
    li = [j for j in jobs if "linkedin.com" in (j.get("job_url") or "")]
    n = int(os.getenv("LI_N", "4"))
    li = li[:n]
    if not li:
        print("No LinkedIn job in JSON"); return
    qa = dict(APPLICATION_QA)
    settings = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "profile_settings.json"), encoding="utf-8"))
    qa["resume_path"] = settings.get("resume_path") or qa.get("resume_path")
    env = settings.get("env", {})
    # Pre-set a score so the fit validator skips the slow LLM structured-profile rebuild
    for j in li:
        j["score"] = 80
        j["decision"] = "auto_apply"
    print(f"\n===== LINKEDIN EASY APPLY PROBE ({len(li)} jobs) =====")
    for j in li:
        print(f"  - {j['title'][:60]} @ {j['company']}")
    t0 = time.time()
    apply_jobs_batch(
        li, qa, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL,
        dry_run=True, headless=False,
        linkedin_email=env.get("LINKEDIN_EMAIL", ""),
        linkedin_password=env.get("LINKEDIN_PASSWORD", ""),
        vision_model=OLLAMA_VISION_MODEL,
        validate_fit=False,
    )
    dt = round(time.time() - t0, 1)
    print(f"\n----- OUTCOMES ({dt}s) -----")
    for j in li:
        print(f"  [{j.get('decision')}] applied={j.get('applied')} | {j['title'][:45]} | {j.get('apply_notes')}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--linkedin-from-json":
        linkedin_from_json()
    elif len(sys.argv) >= 2:
        url = sys.argv[1]
        title = sys.argv[2] if len(sys.argv) > 2 else "Test Role"
        company = sys.argv[3] if len(sys.argv) > 3 else "Test Co"
        headless = "--headed" not in sys.argv
        run_one(url, title, company, headless=headless)
    else:
        print(__doc__)
