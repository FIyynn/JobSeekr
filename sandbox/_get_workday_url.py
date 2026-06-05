import requests, json
# Workday CXS public jobs endpoint for a few tenants. Returns externalPath we can deep-link.
TENANTS = [
    ("nvidia", "wd5", "NVIDIAExternalCareerSite"),
    ("gehealthcare", "wd5", "External"),
    ("redhat", "wd5", "Jobs"),
]
HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
           "User-Agent": "Mozilla/5.0"}
for tenant, wd, site in TENANTS:
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    try:
        r = requests.post(api, json={"limit": 5, "offset": 0, "searchText": "analyst"},
                          headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"{tenant}: HTTP {r.status_code}")
            continue
        data = r.json()
        jobs = data.get("jobPostings", [])
        print(f"\n{tenant}: {len(jobs)} postings")
        for j in jobs[:3]:
            path = j.get("externalPath", "")
            url = f"{base}/en-US/{site}{path}"
            print("TITLE:", j.get("title"))
            print("URL:", url)
        if jobs:
            break
    except Exception as e:
        print(f"{tenant}: ERROR {e}")
