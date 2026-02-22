"""
Run health check against the running service.
Usage: python scripts/health_check.py
"""
import requests, json

BASE = "http://localhost:5000"

r = requests.get(f"{BASE}/health")
print(f"Status: {r.status_code}")
print(json.dumps(r.json(), indent=2))
