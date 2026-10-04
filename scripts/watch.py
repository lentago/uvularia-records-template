#!/usr/bin/env python3
"""Watch the published vault and the Ask box; tell you in a GitHub Issue.

The watch workflow runs this every 30 minutes. It reads what this vault has
published — ``standing.json`` and ``corpus-latest.json`` from your Pages URL —
and, if you have an Ask function, its ``GET /health``. Then it opens, updates,
or closes one issue per condition:

  * **stale digest** — the Ask box has been answering from an older corpus than
    the one published for more than 30 minutes;
  * **obligation amber or red** — one issue per obligation, opened when it moves
    to amber or red, updated (with a comment) if it moves between the two, and
    closed when it is green again;
  * **cap past 80 %** — today's questions have used 80 % or more of the daily cap.

    python3 scripts/watch.py                  # what the workflow runs
    python3 scripts/watch.py --dry-run        # print what it would do, touch nothing

Configuration comes from the environment the workflow passes in:

  * ``GITHUB_REPOSITORY`` and ``GITHUB_TOKEN`` — set by Actions. The token needs
    only ``issues: write``.
  * ``PUBLISHED_BASE_URL`` — optional repository variable. Defaults to this
    repository's GitHub Pages address, ``https://<owner>.github.io/<repo>``. Set
    it if you serve the ``published`` branch from your own domain.
  * ``ASK_HEALTH_URL`` — optional repository variable: your Ask function's URL
    ending in ``/health``. Unset means the two Ask checks are skipped with a
    notice; nothing fails.

**How it never opens the same thing twice.** Every issue it files carries the
label ``uvularia-watch`` and a hidden marker line naming its condition, for
example ``<!-- uvularia-watch: obligation:annual-report -->``. Before acting it
lists the open issues with that label and matches on the marker. One open issue
per condition; if a person files a duplicate by hand with the same marker, the
newer one is closed as a duplicate.

**Where "since the last run" lives.** In the open issue itself: a second hidden
line records the state it last reported (``amber`` or ``red``). There is nothing
else to remember — an obligation with no open issue was, as far as anyone has
been told, fine. Keeping it in the issue means the job needs no write access to
your code or to the ``published`` branch, which stays the append-only record of
what you published and is never touched by a timer.

**It never closes on missing data.** An issue closes only when the data says the
condition has cleared: the obligation is green, the digests match, the cap is
below 80 %. If a file can't be read, or an obligation shows ``no-data``, the
issue is left as it is. Unreachable data fails the run (GitHub emails you about
a failed scheduled run); a vault that has not published yet is a notice.

Python 3.12, standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LABEL = "uvularia-watch"
LABEL_COLOR = "d93f0b"
LABEL_DESCRIPTION = "Opened and closed by the watch workflow"
STALE_AFTER = timedelta(minutes=30)
CAP_ALERT = 0.80

MARKER_RE = re.compile(r"^<!-- uvularia-watch: (?P<key>[a-z0-9:_-]+) -->$", re.M)
STATE_RE = re.compile(r"^<!-- uvularia-watch-state: (?P<state>[a-z0-9_-]+) -->$", re.M)


# --------------------------------------------------------------------------- #
# What a condition looks like.                                                 #
# --------------------------------------------------------------------------- #

ACTIVE, CLEAR, UNKNOWN = "active", "clear", "unknown"


@dataclass
class Finding:
    """One condition's verdict this run.

    ``status`` is ACTIVE (open or update the issue), CLEAR (close it if open),
    or UNKNOWN (the data can't say — leave any issue exactly as it is).
    ``state`` is the short word stored in the issue (``amber``, ``red``, …) so a
    later run can tell the condition changed. ``note`` explains a CLEAR or
    UNKNOWN in the run log, and is the closing comment for a CLEAR.
    """

    key: str
    status: str
    title: str = ""
    body: str = ""
    state: str = ""
    note: str = ""


class SourceError(Exception):
    """A file or endpoint that should have answered could not be read."""


def _parse_time(value):
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _short(digest):
    return (digest or "")[:12] or "none"


# --------------------------------------------------------------------------- #
# The three conditions. Pure functions over parsed data, so tests need no net. #
# --------------------------------------------------------------------------- #

def stale_digest(bundle, health, now):
    """Is the Ask box serving an older corpus than the published one, for >30 min?

    The clock starts at the published bundle's own ``published_at`` (the publish
    workflow's server-side time), so this needs no memory between runs.
    """
    key = "stale-digest"
    if health is None:
        return Finding(key, UNKNOWN, note="ASK_HEALTH_URL is not set; Ask checks skipped")
    if bundle is None:
        return Finding(key, UNKNOWN, note="corpus-latest.json is not published yet")
    if not health.get("enabled"):
        return Finding(key, CLEAR, note="The Ask box is paused, so it serves no corpus "
                                        "at all; there is nothing to be stale.")
    if health.get("digest_pinned"):
        return Finding(key, CLEAR, note="The Ask box is pinned to a chosen digest on "
                                        "purpose, so it is not expected to follow the "
                                        "latest publish.")
    published, served = bundle.get("digest"), health.get("digest")
    if served and served == published:
        return Finding(key, CLEAR, note=f"The Ask box now serves the published corpus "
                                        f"(`{_short(published)}`).")
    published_at = _parse_time(bundle.get("published_at"))
    if published_at is None:
        return Finding(key, UNKNOWN, note="corpus-latest.json has no readable published_at")
    if now - published_at <= STALE_AFTER:
        return Finding(key, UNKNOWN, note="a new corpus was published under 30 minutes "
                                          "ago; the box has time to catch up")
    return Finding(
        key, ACTIVE, state="stale",
        title="Ask box is answering from an old copy of the records",
        body=(
            "**What happened.** The Ask box on your site is still answering from an "
            "older copy of your records. You published a newer one more than 30 "
            "minutes ago, and the box normally picks it up within a few minutes.\n\n"
            f"- Published corpus: `{_short(published)}` at {bundle.get('published_at')}\n"
            f"- Ask box is serving: `{_short(served)}` (rules `{health.get('rules_tag')}`)\n\n"
            "**What to do.**\n\n"
            "1. Wait one more run. A box that has been idle refreshes on its next "
            "question or health check.\n"
            "2. If this is still open after that, check the function's CloudWatch log "
            "for an error fetching `corpus-latest.json`, and that the function's "
            "`published_base_url` points at this vault's Pages address.\n"
            "3. If you pinned the box to a digest on purpose, this issue closes on its "
            "own once `/health` reports `digest_pinned: true`.\n\n"
            "This issue closes itself when the box serves the published corpus."
        ),
    )


def obligations(standing, open_keys):
    """One finding per obligation that is amber or red, or has an open issue.

    Amber and red are ACTIVE. Green CLEARs an open issue. ``no-data`` is UNKNOWN:
    missing data is not a passing obligation, so it never closes an issue. An
    obligation that has vanished from standing.json is left alone too.
    """
    prefix = "obligation:"
    rows = {row["id"]: row for row in standing}
    findings = []
    for oid, row in rows.items():
        key = prefix + oid
        state = row.get("state")
        if state in ("amber", "red"):
            findings.append(_obligation_issue(key, row))
        elif state == "green":
            if key in open_keys:
                findings.append(Finding(key, CLEAR, note=(
                    f"`{oid}` is green again on the published board"
                    + (f" (satisfied by `{row['satisfied_by']}`)."
                       if row.get("satisfied_by") else "."))))
        elif key in open_keys:
            findings.append(Finding(key, UNKNOWN, note=f"`{oid}` shows {state}; "
                                                       "leaving its issue open"))
    for key in sorted(open_keys):
        if key.startswith(prefix) and key[len(prefix):] not in rows:
            findings.append(Finding(key, UNKNOWN, note=f"`{key[len(prefix):]}` is no "
                                                       "longer in standing.json"))
    return findings


def _obligation_issue(key, row):
    oid, state = row["id"], row["state"]
    deadline = row.get("deadline") or "no deadline on file"
    if state == "red":
        title = f"Obligation {oid} is red: the deadline passed"
        what = (f"The obligation `{oid}` turned **red** on your public board. Its "
                f"deadline ({deadline}) has passed without a record posted in time, "
                "or the record was posted late.")
        todo = ("1. Post the record now if it is missing (the \"Add a record\" issue "
                "form is the quickest way). A late posting is still worth making: the "
                "board shows when it went up.\n"
                "2. If it was posted and the board is wrong, check the record's `type`, "
                "`effective` date and `status: approved` against the rule in "
                "`obligations/`.")
    else:
        title = f"Obligation {oid} is amber: due soon"
        what = (f"The obligation `{oid}` turned **amber** on your public board. Its "
                f"deadline is {deadline} and nothing that satisfies it is published yet.")
        todo = ("1. Post the record before the deadline (the \"Add a record\" issue "
                "form is the quickest way), and get it reviewed and merged — it counts "
                "only once it is published.\n"
                "2. If it is already posted, check the record's `type`, `effective` date "
                "and `status: approved` against the rule in `obligations/`.")
    gap = row.get("gap")
    gap_line = ""
    if isinstance(gap, int):
        gap_line = (f"- {gap} day(s) overdue\n" if gap > 0 else
                    f"- {-gap} day(s) to go\n" if gap < 0 else "- due today\n")
    body = (f"**What happened.** {what}\n\n"
            f"- Obligation: `{oid}`\n- State: **{state}**\n- Deadline: {deadline}\n"
            f"{gap_line}\n"
            f"**What to do.**\n\n{todo}\n\n"
            "This issue closes itself when the obligation is green again. It stays open "
            "if the board shows no data — missing data is not a pass.")
    return Finding(key, ACTIVE, title=title, body=body, state=state)


def cap(health):
    """Has today's use reached 80 % of the daily cap?"""
    key = "cap"
    if health is None:
        return Finding(key, UNKNOWN, note="ASK_HEALTH_URL is not set; Ask checks skipped")
    used, ceiling = health.get("cap_used"), health.get("cap")
    if not isinstance(used, int) or not isinstance(ceiling, int) or ceiling <= 0:
        return Finding(key, UNKNOWN, note="the Ask box could not report its cap counter")
    if used < CAP_ALERT * ceiling:
        return Finding(key, CLEAR, note=f"Today's use is back under 80 % "
                                        f"({used} of {ceiling} on {health.get('day')}).")
    pct = round(100 * used / ceiling)
    return Finding(
        key, ACTIVE, state="full" if used >= ceiling else "high",
        title="Ask box has used 80 % of today's question limit",
        body=(
            f"**What happened.** The Ask box has answered {used} of its {ceiling} "
            f"questions for {health.get('day')} (UTC) — {pct} %. When it reaches "
            f"{ceiling} it stops answering until midnight UTC and tells visitors to try "
            "tomorrow. The limit is what keeps your model bill bounded.\n\n"
            "**What to do.**\n\n"
            "1. If this is a busy day you expected (a meeting, a news story), nothing: "
            "the limit is doing its job.\n"
            "2. If it is unexpected, look at the questions in the function's CloudWatch "
            "log. Lots of the same question from nowhere is a bot: turn on the "
            "Turnstile check.\n"
            "3. To raise or lower the limit, change `daily_cap` in your rules repo's "
            "`policy.yaml` and cut a release.\n\n"
            "This issue closes itself when the day's use is under 80 % again — "
            "normally at midnight UTC."
        ),
    )


# --------------------------------------------------------------------------- #
# Reconciling findings with the open issues.                                   #
# --------------------------------------------------------------------------- #

def render_body(finding):
    return (f"{finding.body}\n\n---\n"
            "_Filed by the watch workflow (`scripts/watch.py`). Do not edit the two "
            "hidden lines below; they are how it finds this issue again._\n\n"
            f"<!-- uvularia-watch: {finding.key} -->\n"
            f"<!-- uvularia-watch-state: {finding.state or 'active'} -->\n")


def issue_key(issue):
    match = MARKER_RE.search(issue.get("body") or "")
    return match.group("key") if match else None


def issue_state(issue):
    match = STATE_RE.search(issue.get("body") or "")
    return match.group("state") if match else None


def plan(findings, open_issues):
    """Turn findings into a list of actions. Pure: no network.

    Each action is a tuple: ``("create", finding)``, ``("update", number,
    finding, changed_state_comment_or_None)``, ``("close", number, comment,
    state_reason)``.
    """
    by_key = {}
    actions = []
    for issue in sorted(open_issues, key=lambda i: i["number"]):
        key = issue_key(issue)
        if key is None:
            continue
        if key in by_key:
            actions.append(("close", issue["number"],
                            f"Duplicate of #{by_key[key]['number']}, which tracks the "
                            "same condition.", "not_planned"))
            continue
        by_key[key] = issue
    for f in findings:
        issue = by_key.get(f.key)
        if f.status == ACTIVE:
            if issue is None:
                actions.append(("create", f))
                continue
            before = issue_state(issue)
            comment = None
            if before and f.state and before != f.state:
                comment = f"Changed since the last check: **{before} → {f.state}**."
            if comment or (issue.get("body") or "") != render_body(f) \
                    or issue.get("title") != f.title:
                actions.append(("update", issue["number"], f, comment))
        elif f.status == CLEAR and issue is not None:
            actions.append(("close", issue["number"], f"Cleared: {f.note}", "completed"))
    return actions


# --------------------------------------------------------------------------- #
# GitHub and HTTP. Thin, injectable.                                           #
# --------------------------------------------------------------------------- #

def http_json(url, *, method="GET", token=None, body=None, timeout=20):
    """``(status, parsed_json_or_None)``. A 404 is returned, not raised; network
    errors and other HTTP errors raise SourceError."""
    headers = {"User-Agent": "uvularia-watch", "Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, None
        raise SourceError(f"{method} {url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SourceError(f"{method} {url}: {exc}") from exc


class GitHub:
    """The handful of Issues calls the watch needs."""

    def __init__(self, repo, token, api="https://api.github.com", request=http_json):
        self.base = f"{api.rstrip('/')}/repos/{repo}"
        self.token = token
        self._request = request

    def _call(self, method, path, body=None):
        status, data = self._request(self.base + path, method=method,
                                     token=self.token, body=body)
        if status == 404:
            raise SourceError(f"{method} {path}: HTTP 404")
        return data

    def ensure_label(self):
        status, _ = self._request(f"{self.base}/labels/{LABEL}", method="GET",
                                  token=self.token)
        if status == 404:
            self._call("POST", "/labels", {"name": LABEL, "color": LABEL_COLOR,
                                           "description": LABEL_DESCRIPTION})

    def open_issues(self):
        issues, page = [], 1
        while True:
            batch = self._call("GET", f"/issues?state=open&labels={LABEL}"
                                      f"&per_page=100&page={page}") or []
            issues += [i for i in batch if "pull_request" not in i]
            if len(batch) < 100:
                return issues
            page += 1

    def create(self, title, body):
        return self._call("POST", "/issues", {"title": title, "body": body,
                                              "labels": [LABEL]})

    def update(self, number, title, body):
        self._call("PATCH", f"/issues/{number}", {"title": title, "body": body})

    def comment(self, number, text):
        self._call("POST", f"/issues/{number}/comments", {"body": text})

    def close(self, number, reason="completed"):
        self._call("PATCH", f"/issues/{number}", {"state": "closed",
                                                  "state_reason": reason})


def apply(actions, gh, log=print):
    for action in actions:
        kind = action[0]
        if kind == "create":
            f = action[1]
            made = gh.create(f.title, render_body(f))
            log(f"opened #{(made or {}).get('number', '?')}: {f.title}")
        elif kind == "update":
            _, number, f, comment = action
            gh.update(number, f.title, render_body(f))
            if comment:
                gh.comment(number, comment)
            log(f"updated #{number}: {f.title}")
        elif kind == "close":
            _, number, comment, reason = action
            gh.comment(number, comment)
            gh.close(number, reason)
            log(f"closed #{number}: {comment}")


# --------------------------------------------------------------------------- #
# Reading the vault's published files and the box's health.                    #
# --------------------------------------------------------------------------- #

def default_base(repo):
    owner, _, name = repo.partition("/")
    host = f"https://{owner.lower()}.github.io"
    return host if name.lower() == f"{owner.lower()}.github.io" else f"{host}/{name}"


def read_sources(base, health_url, fetch=http_json, log=print):
    """``(standing, bundle, health, errors)``. Absent files are None with a
    notice; unreadable ones are None plus an entry in ``errors``."""
    errors = []

    def get(url, what):
        try:
            status, data = fetch(url)
        except SourceError as exc:
            errors.append(f"could not read {what}: {exc}")
            return None
        if status == 404:
            log(f"::notice title=watch::{what} is not published yet ({url})")
            return None
        return data

    standing = get(f"{base}/standing.json", "standing.json")
    if standing is not None and not (
            isinstance(standing, list)
            and all(isinstance(r, dict) and isinstance(r.get("id"), str) for r in standing)):
        errors.append("standing.json is not a list of obligation rows")
        standing = None
    bundle = get(f"{base}/corpus-latest.json", "corpus-latest.json")
    if bundle is not None and not (isinstance(bundle, dict) and bundle.get("digest")):
        errors.append("corpus-latest.json has no digest")
        bundle = None
    health = None
    if health_url:
        # Unlike a vault that has not published yet, a 404 here is a wrong URL.
        try:
            status, health = fetch(health_url)
        except SourceError as exc:
            errors.append(f"could not read the Ask function's /health: {exc}")
            status, health = None, None
        if status == 404:
            errors.append(f"the Ask function's /health returned 404 — check that "
                          f"ASK_HEALTH_URL ends in /health ({health_url})")
            health = None
        elif status is not None and not (isinstance(health, dict)
                                         and health.get("status") == "ok"):
            errors.append("the Ask function's /health did not report status ok")
            health = None
    else:
        log("::notice title=watch::ASK_HEALTH_URL is not set — skipping the Ask box "
            "checks (stale digest, daily cap). Set it to your function's URL ending in "
            "/health to turn them on.")
    return standing, bundle, health, errors


def run(*, base, health_url, gh, now, fetch=http_json, log=print, dry_run=False):
    """One watch pass. Returns the process exit code."""
    standing, bundle, health, errors = read_sources(base, health_url, fetch, log)
    open_issues = gh.open_issues()
    open_keys = {k for k in map(issue_key, open_issues) if k}

    findings = []
    if health_url and health is None:
        findings += [Finding("stale-digest", UNKNOWN, note="/health unreadable"),
                     Finding("cap", UNKNOWN, note="/health unreadable")]
    else:
        findings += [stale_digest(bundle, health, now), cap(health)]
    if standing is not None:
        findings += obligations(standing, open_keys)

    for f in findings:
        if f.status == UNKNOWN:
            log(f"{f.key}: no change ({f.note})")

    actions = plan(findings, open_issues)
    if dry_run:
        for a in actions:
            log(f"would {a[0]}: " + (a[1].title if a[0] == "create" else f"#{a[1]}"))
    else:
        if any(a[0] == "create" for a in actions):
            gh.ensure_label()
        apply(actions, gh, log)
    if not actions:
        log("Nothing to open, update, or close.")
    for e in errors:
        log(f"::error title=watch::{e}")
    return 1 if errors else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change; open, edit, and close nothing")
    ap.add_argument("--now", help="evaluate as of this ISO time (default: now, UTC)")
    args = ap.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not token:
        print("::error title=watch::GITHUB_REPOSITORY and GITHUB_TOKEN must be set",
              file=sys.stderr)
        return 2
    base = (os.environ.get("PUBLISHED_BASE_URL") or "").strip().rstrip("/") \
        or default_base(repo)
    health_url = (os.environ.get("ASK_HEALTH_URL") or "").strip()
    now = _parse_time(args.now) if args.now else datetime.now(timezone.utc)
    gh = GitHub(repo, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    print(f"Watching {base}" + (f" and {health_url}" if health_url else ""))
    return run(base=base, health_url=health_url, gh=gh, now=now,
               dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
