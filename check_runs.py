"""Check GitHub Actions workflow runs."""
import subprocess
import requests

proc = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n",
    capture_output=True, text=True,
)
creds = dict(
    l.split("=", 1) for l in proc.stdout.strip().split("\n") if "=" in l
)
token = creds.get("password", "")

resp = requests.get(
    "https://api.github.com/repos/DannMensah/k-ruoka-scraping-service/actions/runs",
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    },
    params={"per_page": 3},
    timeout=15,
)
for run in resp.json().get("workflow_runs", []):
    num = run["run_number"]
    status = run["status"]
    conclusion = run["conclusion"] or "n/a"
    event = run["event"]
    url = run["html_url"]
    created = run["created_at"]
    print(f"  Run #{num}: {status}/{conclusion} ({event}) {created}")
    print(f"    {url}")
