import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

t0 = time.time()
from jobspy import scrape_jobs
print("imported jobspy in", round(time.time()-t0,1), "s")

try:
    df = scrape_jobs(
        site_name=["linkedin"],
        search_term="data scientist",
        location="Dubai",
        results_wanted=8,
        hours_old=336,  # 14 days, wider for smoke test
        country_indeed="united arab emirates",
        linkedin_fetch_description=False,  # faster
    )
    print("RESULT TYPE:", type(df))
    if df is None:
        print("None returned")
    else:
        print("ROWS:", len(df))
        if len(df):
            print("COLUMNS:", list(df.columns))
            for _, r in df.head(8).iterrows():
                print(" -", r.get("title"), "@", r.get("company"), "|", r.get("location"), "|", r.get("job_url_direct") or r.get("job_url"))
except Exception as e:
    import traceback; traceback.print_exc()
    print("ERROR:", e)
print("elapsed", round(time.time()-t0,1), "s")
