"""Kill JobHuntrr python and Playwright Chrome processes."""
import psutil

PATTERNS = (
    "ms-playwright",
    "linkedin_session",
    "jobhuntrr",
    "remote-debugging",
    "playwright",
)

JOBHUNTRR_PY = (
    "apply_from_notion",
    "orchestrator",
    "form_filler",
)


def main():
    killed_chrome = 0
    killed_py = 0

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or []).lower()

            if name == "chrome.exe" and any(p in cmd for p in PATTERNS):
                proc.kill()
                killed_chrome += 1
                print(f"  Killed Chrome PID {proc.info['pid']}")

            elif name == "python.exe" and any(p in cmd for p in JOBHUNTRR_PY):
                proc.kill()
                killed_py += 1
                print(f"  Killed Python PID {proc.info['pid']}")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    print(f"Stopped {killed_py} JobHuntrr Python and {killed_chrome} Playwright Chrome process(es).")

if __name__ == "__main__":
    main()
