#!/usr/bin/env python3
"""Count what is waiting in the pipeline, for the telemetry events.

The operator pane's Intake and Reviewed panels show how much is waiting and
for how long, and its announcement chart shows how long a merged announcement
took to go live. Nothing in a single workflow run knows those numbers, so this
script asks GitHub, writes the answer to a small JSON file, and
``scripts/telemetry.py --snapshot FILE`` adds it to the event.

    python3 scripts/pipeline_snapshot.py intake --out intake.json
    python3 scripts/pipeline_snapshot.py reviewed --out reviewed.json \\
        --this-run "$GITHUB_RUN_ID" --this-run-conclusion success
    python3 scripts/pipeline_snapshot.py announcements --out-dir _out --vault . \\
        --out announcements.json

What each one writes (all times are Unix seconds):

  * ``intake`` — ``open``: intake items still open, and ``oldest_opened_at``:
    when the oldest was opened (``0`` when none). An intake item is an open
    "Add a record" issue, an open pull request from an ``intake/<N>`` branch,
    or both: issue #N and the pull request from ``intake/N`` are the same
    record on its way in, so they count once, from when the issue was opened.
  * ``reviewed`` — ``awaiting``: open, non-draft pull requests whose checks have
    all finished green, so the next move is a person's; ``oldest_green_at``:
    when the longest-waiting one went green (its last check finished; ``0``
    when none). A pull request with no checks at all is not counted: nothing
    has said it is green.
  * ``announcements`` — ``announcement_latency_s``: for each announcement this
    publish added or changed, the time from its pull request's merge to this
    publish's ``published_at``; the longest one. Left out when no announcement
    went live, or when none came through a pull request (a direct push has no
    server-side merge time, and a commit's own date is the author's clock).

The validate workflow passes ``--this-run``: its own check is still running
when it counts, but by then it knows how the check will end, so it counts its
own pull request with that ending rather than as "not finished".

If a count cannot be made — no token, GitHub unreachable, a page that will
not read — the file gets ``{}`` and a warning line, and the event goes out
without the field. The pane then reads "no data", which is the truth. This
script never fails the job.

Reads ``GITHUB_REPOSITORY``, ``GITHUB_TOKEN`` and (optionally)
``GITHUB_API_URL`` from the environment. Python 3.12, standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from intake import is_intake_form  # noqa: E402
from watch import SourceError, http_json  # noqa: E402

GREEN_CONCLUSIONS = {"success", "neutral", "skipped"}
INTAKE_BRANCH = re.compile(r"^intake/(\d+)$")
RUN_IN_URL = "/actions/runs/{}/"


def epoch(value):
    """An ISO-8601 time from the GitHub API as whole Unix seconds, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


# --------------------------------------------------------------------------- #
# GitHub reads. Thin and injectable, like watch.py's.                          #
# --------------------------------------------------------------------------- #

class GitHub:
    def __init__(self, repo, token, api="https://api.github.com", request=http_json):
        self.base = f"{api.rstrip('/')}/repos/{repo}"
        self.token = token
        self._request = request

    def get(self, path):
        status, data = self._request(self.base + path, token=self.token)
        if status == 404:
            raise SourceError(f"GET {path}: HTTP 404")
        return data

    def get_all(self, path, key=None):
        """Every page of a list endpoint (``key`` for wrapped lists)."""
        items, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            data = self.get(f"{path}{sep}per_page=100&page={page}") or []
            batch = data.get(key, []) if key else data
            items += batch
            if len(batch) < 100:
                return items
            page += 1


# --------------------------------------------------------------------------- #
# The three counts. Each returns the dict to write.                           #
# --------------------------------------------------------------------------- #

def intake_snapshot(gh):
    opened = {}   # key -> earliest opened_at
    for issue in gh.get_all("/issues?state=open"):
        if "pull_request" in issue or not is_intake_form(issue.get("body") or ""):
            continue
        at = epoch(issue.get("created_at"))
        if at is not None:
            opened[("issue", issue["number"])] = at
    for pr in gh.get_all("/pulls?state=open"):
        m = INTAKE_BRANCH.match((pr.get("head") or {}).get("ref") or "")
        at = epoch(pr.get("created_at"))
        if not m or at is None:
            continue
        key = ("issue", int(m.group(1)))
        opened[key] = min(opened.get(key, at), at)
    return {"open": len(opened), "oldest_opened_at": min(opened.values(), default=0)}


def green_at(check_runs, statuses, *, this_run=None, this_run_conclusion=None, now=None):
    """When this commit's checks all finished green, or None if they have not.

    ``check_runs`` are GitHub check runs, ``statuses`` commit statuses (latest
    per context). A check belonging to ``this_run`` is taken as finished with
    ``this_run_conclusion`` at ``now``: the run counting is that check.
    """
    times = []
    for run in check_runs:
        conclusion, finished = run.get("conclusion"), epoch(run.get("completed_at"))
        if this_run and RUN_IN_URL.format(this_run) in (run.get("details_url") or ""):
            conclusion, finished = this_run_conclusion, now
        if conclusion not in GREEN_CONCLUSIONS or finished is None:
            return None
        times.append(finished)
    for status in statuses:
        if status.get("state") != "success":
            return None
        updated = epoch(status.get("updated_at"))
        if updated is None:
            return None
        times.append(updated)
    return max(times) if times else None


def reviewed_snapshot(gh, *, this_run=None, this_run_conclusion=None, now=None):
    waiting = []
    for pr in gh.get_all("/pulls?state=open"):
        if pr.get("draft"):
            continue
        sha = (pr.get("head") or {}).get("sha")
        if not sha:
            continue
        runs = gh.get_all(f"/commits/{sha}/check-runs", key="check_runs")
        statuses = (gh.get(f"/commits/{sha}/status") or {}).get("statuses") or []
        at = green_at(runs, statuses, this_run=this_run,
                      this_run_conclusion=this_run_conclusion, now=now)
        if at is not None:
            waiting.append(at)
    return {"awaiting": len(waiting), "oldest_green_at": min(waiting, default=0)}


def _git_last_commit(vault, path):
    out = subprocess.run(["git", "log", "-1", "--format=%H", "--", path], cwd=vault,
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() or None


def _receipt_ids(text):
    ids = []
    for key in ("added", "changed"):
        m = re.search(rf"^\s+{key}:\s*(\[.*\])\s*$", text, re.MULTILINE)
        if m:
            ids += json.loads(m.group(1))
    return ids


def announcements_snapshot(gh, out_dir, vault, last_commit=_git_last_commit):
    """Longest merge-to-live for the announcements in this publish, or ``{}``."""
    out = Path(out_dir)
    corpora = sorted(out.glob("corpus-*.json"))
    receipts = sorted((out / "receipts").glob("*.md"))
    if not corpora or not receipts:
        return {}
    bundle = json.loads(corpora[0].read_text(encoding="utf-8"))
    published = epoch(bundle.get("published_at"))
    live = set(_receipt_ids(receipts[0].read_text(encoding="utf-8")))
    paths = [r["path"] for r in bundle.get("records", [])
             if r.get("doc_id") in live and r.get("path", "").startswith("records/announcement/")]
    if published is None or not paths:
        return {}
    latencies = []
    for path in paths:
        sha = last_commit(vault, path)
        if not sha:
            continue
        merged = [epoch(pr.get("merged_at")) for pr in gh.get(f"/commits/{sha}/pulls") or []]
        merged = [m for m in merged if m is not None]
        if merged and published >= min(merged):
            latencies.append(published - min(merged))
    return {"announcement_latency_s": max(latencies)} if latencies else {}


# --------------------------------------------------------------------------- #
# CLI.                                                                          #
# --------------------------------------------------------------------------- #

def _parser():
    p = argparse.ArgumentParser(description="Count what is waiting in the pipeline.")
    p.add_argument("what", choices=("intake", "reviewed", "announcements"))
    p.add_argument("--out", required=True, help="where to write the JSON")
    p.add_argument("--this-run", help="reviewed: this workflow run's id")
    p.add_argument("--this-run-conclusion", help="reviewed: how this run's check ends")
    p.add_argument("--out-dir", default="_out", help="announcements: the publish build output")
    p.add_argument("--vault", default=".", help="announcements: the vault checkout")
    return p


def main(argv=None, env=None, request=http_json, now=None):
    env = os.environ if env is None else env
    args = _parser().parse_args(argv)
    result = {}
    repo, token = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print(f"::warning title=telemetry::no GITHUB_REPOSITORY or GITHUB_TOKEN; "
              f"the {args.what} counts are left out of the event")
    else:
        gh = GitHub(repo, token, env.get("GITHUB_API_URL") or "https://api.github.com", request)
        try:
            if args.what == "intake":
                result = intake_snapshot(gh)
            elif args.what == "reviewed":
                now = int(datetime.now(timezone.utc).timestamp()) if now is None else now
                result = reviewed_snapshot(gh, this_run=args.this_run,
                                           this_run_conclusion=args.this_run_conclusion, now=now)
            else:
                result = announcements_snapshot(gh, args.out_dir, args.vault)
        except Exception as exc:  # noqa: BLE001 — a count never fails the job
            print(f"::warning title=telemetry::could not count {args.what} ({exc}); "
                  f"the counts are left out of the event")
            result = {}
    Path(args.out).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    print(f"pipeline snapshot: {args.what} {json.dumps(result, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
