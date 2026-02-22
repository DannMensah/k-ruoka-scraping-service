"""Fetch GitHub Actions job logs for the latest run."""
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
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
}

# Get the latest run
resp = requests.get(
    "https://api.github.com/repos/DannMensah/k-ruoka-scraping-service/actions/runs",
    headers=headers,
    params={"per_page": 1},
    timeout=15,
)
run = resp.json()["workflow_runs"][0]
run_id = run["id"]
print(f"Run #{run['run_number']}: {run['status']}/{run['conclusion']}")
print(f"Duration: {run.get('run_started_at', '?')} -> {run.get('updated_at', '?')}")
print()

# Get jobs for this run
resp = requests.get(
    f"https://api.github.com/repos/DannMensah/k-ruoka-scraping-service/actions/runs/{run_id}/jobs",
    headers=headers,
    timeout=15,
)
for job in resp.json().get("jobs", []):
    print(f"Job: {job['name']} — {job['status']}/{job['conclusion']}")
    for step in job.get("steps", []):
        emoji = "✓" if step["conclusion"] == "success" else "✗" if step["conclusion"] == "failure" else "…"
        print(f"  {emoji} {step['name']} ({step['conclusion'] or step['status']})")

    # Get job logs
    log_resp = requests.get(
        f"https://api.github.com/repos/DannMensah/k-ruoka-scraping-service/actions/jobs/{job['id']}/logs",
        headers=headers,
        timeout=30,
    )
    if log_resp.status_code == 200:
        # Log is a zip file or raw text — show last portion
        log_text = log_resp.text
        # Find the "Run sync" step output
        lines = log_text.split("\n")
        # Show last 100 lines (most relevant)
        print("\n--- Last 100 lines of logs ---")
        for line in lines[-100:]:
            print(line)
