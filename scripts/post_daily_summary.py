"""Post this run's summary to the day's "daily summary" GitHub issue.

One issue per day per account ("Production pipeline daily summary 2026-11-19"),
one comment per vendor step, so Andy has a single place that lists what was
added, changed, skipped and held that day. Reads the text vendor_pipeline wrote
to RUN_SUMMARY_FILE. Never fails the job: a summary that cannot be posted is
printed and the run carries on.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

API = "https://api.github.com"
MAX_BODY = 60000  # GitHub caps a comment at 65,536 characters.


def main() -> int:
    path = os.environ.get("RUN_SUMMARY_FILE", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    account = os.environ.get("SUMMARY_ACCOUNT", "Production")
    if not (path and repo and token) or not Path(path).exists():
        print("daily summary: nothing to post")
        return 0
    body = Path(path).read_text(encoding="utf-8").strip()
    if not body:
        print("daily summary: empty, nothing to post")
        return 0
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n(cut short; the full list is in the run log)"
    body += f"\n\nRun: {run_url}"
    title = (f"{account} pipeline daily summary "
             f"{datetime.now(UTC).date().isoformat()}")
    headers = {"Authorization": f"token {token}",
               "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(f"{API}/repos/{repo}/issues", headers=headers,
                         params={"state": "open", "per_page": 100}, timeout=30)
        r.raise_for_status()
        number = next((i["number"] for i in r.json()
                       if i.get("title") == title and "pull_request" not in i),
                      None)
        if number is None:
            r = requests.post(f"{API}/repos/{repo}/issues", headers=headers,
                              json={"title": title, "body": body}, timeout=30)
            r.raise_for_status()
            print(f"daily summary: opened issue #{r.json()['number']}")
        else:
            r = requests.post(f"{API}/repos/{repo}/issues/{number}/comments",
                              headers=headers, json={"body": body}, timeout=30)
            r.raise_for_status()
            print(f"daily summary: added to issue #{number}")
    except requests.RequestException as exc:
        print(f"daily summary: could not post ({exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
