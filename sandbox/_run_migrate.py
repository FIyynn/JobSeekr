"""One-off runner to capture migrate_legacy output."""
import subprocess
import sys

root = __file__.replace("sandbox\\_run_migrate.py", "").replace("sandbox/_run_migrate.py", "")

for flag in ["--dry-run", ""]:
    cmd = [sys.executable, "engine/migrate_legacy.py"] + ([flag] if flag else [])
    print("CMD:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    print("STDOUT:", r.stdout.strip())
    print("STDERR:", r.stderr.strip())
    print("EXIT:", r.returncode)
    if flag == "--dry-run" and "errors': 0" not in r.stdout and "'errors': 0" not in r.stdout:
        print("SKIP real run due to errors")
        break
