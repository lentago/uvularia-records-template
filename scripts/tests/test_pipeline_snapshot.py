"""Tests for scripts/pipeline_snapshot.py — the counts behind the pane's
Intake and Reviewed panels and the announcement-latency chart (issue #59).

Offline: GitHub is an in-memory fake keyed by API path. Each count is checked
for what it includes, what it leaves out, and that a count it cannot make is
left out of the file (never written as zero).

    python3 -m unittest discover -s templates/records/scripts/tests -p "test_*.py"
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import pipeline_snapshot as snap  # noqa: E402
from watch import SourceError  # noqa: E402

REPO = "example-org/example-records"
NOW = 1_791_115_200          # 2026-10-04T12:00:00Z
FORM_BODY = "### Record type\n\nnotice\n\n### Date it takes effect\n\n2026-10-10\n"


def iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeAPI:
    """``request(url, token=...)`` over a dict of ``path -> data``. List
    endpoints are paged like GitHub's (``per_page``/``page``)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, method="GET", token=None, body=None, timeout=20):
        parts = urlsplit(url)
        path = parts.path.split(f"/repos/{REPO}", 1)[1]
        query = parse_qs(parts.query)
        self.calls.append(path)
        key = path + ("?state=open" if query.get("state") == ["open"] else "")
        if key not in self.routes:
            return 404, None
        data = self.routes[key]
        if isinstance(data, Exception):
            raise data
        if "page" in query:
            page, per = int(query["page"][0]), int(query["per_page"][0])
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list):
                        return 200, {**data, k: v[(page - 1) * per: page * per]}
            return 200, data[(page - 1) * per: page * per]
        return 200, data


def gh(routes):
    return snap.GitHub(REPO, "t", request=FakeAPI(routes))


def issue(n, created, body=FORM_BODY, pr=False):
    i = {"number": n, "created_at": iso(created), "body": body}
    if pr:
        i["pull_request"] = {}
    return i


def pull(n, created, ref, sha="s", draft=False):
    return {"number": n, "created_at": iso(created), "draft": draft,
            "head": {"ref": ref, "sha": sha}}


def check(conclusion, completed, run_id=1, status="completed"):
    return {"status": status, "conclusion": conclusion,
            "completed_at": iso(completed) if completed else None,
            "details_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/9"}


class IntakeSnapshot(unittest.TestCase):
    def test_issue_and_its_pull_request_count_once_from_the_issue(self):
        result = snap.intake_snapshot(gh({
            "/issues?state=open": [
                issue(10, NOW - 5000),                         # form, with PR below
                issue(11, NOW - 100),                          # form, no PR yet
                issue(12, NOW - 99999, body="Just a question"),  # not the form
                issue(20, NOW - 50, pr=True),                  # a PR in the issues list
            ],
            "/pulls?state=open": [
                pull(20, NOW - 50, "intake/10"),               # PR for issue 10
                pull(21, NOW - 9000, "intake/7"),              # issue 7 already closed
                pull(22, NOW - 99999, "fix-typo"),             # not intake
            ],
        }))
        self.assertEqual(result, {"open": 3, "oldest_opened_at": NOW - 9000})

    def test_nothing_open_is_zero_and_zero(self):
        result = snap.intake_snapshot(gh({"/issues?state=open": [], "/pulls?state=open": []}))
        self.assertEqual(result, {"open": 0, "oldest_opened_at": 0})

    def test_more_than_one_page_is_read(self):
        issues = [issue(n, NOW - n) for n in range(1, 151)]
        result = snap.intake_snapshot(gh({"/issues?state=open": issues,
                                          "/pulls?state=open": []}))
        self.assertEqual(result, {"open": 150, "oldest_opened_at": NOW - 150})


class GreenAt(unittest.TestCase):
    def test_all_green_is_the_last_to_finish(self):
        runs = [check("success", NOW - 300), check("skipped", NOW - 200)]
        statuses = [{"state": "success", "updated_at": iso(NOW - 100)}]
        self.assertEqual(snap.green_at(runs, statuses), NOW - 100)

    def test_any_red_or_unfinished_check_is_not_green(self):
        self.assertIsNone(snap.green_at([check("success", NOW), check("failure", NOW)], []))
        self.assertIsNone(snap.green_at([check(None, None, status="in_progress")], []))
        self.assertIsNone(snap.green_at([], [{"state": "pending", "updated_at": iso(NOW)}]))

    def test_no_checks_at_all_is_not_green(self):
        self.assertIsNone(snap.green_at([], []))

    def test_this_runs_own_check_counts_with_how_it_will_end(self):
        runs = [check("success", NOW - 300), check(None, None, run_id=77, status="in_progress")]
        self.assertEqual(snap.green_at(runs, [], this_run="77", this_run_conclusion="success",
                                       now=NOW), NOW)
        self.assertIsNone(snap.green_at(runs, [], this_run="77", this_run_conclusion="failure",
                                        now=NOW))
        self.assertIsNone(snap.green_at(runs, [], this_run="78", this_run_conclusion="success",
                                        now=NOW))


class ReviewedSnapshot(unittest.TestCase):
    def test_counts_green_non_draft_pull_requests(self):
        result = snap.reviewed_snapshot(gh({
            "/pulls?state=open": [pull(1, NOW, "a", sha="g1"), pull(2, NOW, "b", sha="g2"),
                                  pull(3, NOW, "c", sha="red"), pull(4, NOW, "d", sha="g3",
                                                                    draft=True)],
            "/commits/g1/check-runs": {"total_count": 1, "check_runs": [check("success", NOW - 60)]},
            "/commits/g1/status": {"state": "pending", "statuses": []},
            "/commits/g2/check-runs": {"total_count": 1, "check_runs": [check("success", NOW - 600)]},
            "/commits/g2/status": {"state": "success",
                                   "statuses": [{"state": "success", "updated_at": iso(NOW - 900)}]},
            "/commits/red/check-runs": {"total_count": 1, "check_runs": [check("failure", NOW)]},
            "/commits/red/status": {"state": "pending", "statuses": []},
        }))
        self.assertEqual(result, {"awaiting": 2, "oldest_green_at": NOW - 600})

    def test_nothing_waiting_is_zero_and_zero(self):
        self.assertEqual(snap.reviewed_snapshot(gh({"/pulls?state=open": []})),
                         {"awaiting": 0, "oldest_green_at": 0})


class AnnouncementsSnapshot(unittest.TestCase):
    PUBLISHED = NOW

    def _out(self, tmp, added, changed=()):
        out = Path(tmp)
        (out / "receipts").mkdir()
        digest = "c" * 64
        records = [
            {"doc_id": "2026-10-01-trail-closure", "path": "records/announcement/2026-10-01-trail-closure.md"},
            {"doc_id": "2026-10-02-fall-hours", "path": "records/announcement/2026-10-02-fall-hours.md"},
            {"doc_id": "2026-09-30-minutes", "path": "records/minutes/2026-09-30-minutes.md"},
            {"doc_id": "2026-01-01-old-notice", "path": "records/announcement/2026-01-01-old-notice.md"},
        ]
        (out / f"corpus-{digest}.json").write_text(json.dumps(
            {"digest": digest, "published_at": iso(self.PUBLISHED), "records": records}))
        (out / "receipts" / f"x-{digest}.md").write_text(
            "---\nrecords:\n"
            f"  added: {json.dumps(list(added))}\n"
            f"  changed: {json.dumps(list(changed))}\n"
            "  retracted: []\n---\n")
        return out

    def _commits(self, path_to_sha):
        return lambda vault, path: path_to_sha.get(path)

    def test_longest_merge_to_live_of_this_publishs_announcements(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._out(tmp, added=["2026-10-01-trail-closure", "2026-09-30-minutes"],
                            changed=["2026-10-02-fall-hours"])
            result = snap.announcements_snapshot(gh({
                "/commits/a1/pulls": [{"merged_at": iso(NOW - 120)}],
                "/commits/a2/pulls": [{"merged_at": None},
                                      {"merged_at": iso(NOW - 400)}],
                "/commits/m1/pulls": [{"merged_at": iso(NOW - 99999)}],
            }), out, ".", last_commit=self._commits({
                "records/announcement/2026-10-01-trail-closure.md": "a1",
                "records/announcement/2026-10-02-fall-hours.md": "a2",
                "records/minutes/2026-09-30-minutes.md": "m1",
            }))
        self.assertEqual(result, {"announcement_latency_s": 400})

    def test_no_announcement_in_this_publish_leaves_the_field_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._out(tmp, added=["2026-09-30-minutes"])
            api = FakeAPI({})
            result = snap.announcements_snapshot(snap.GitHub(REPO, "t", request=api), out, ".",
                                                 last_commit=self._commits({}))
        self.assertEqual((result, api.calls), ({}, []))

    def test_a_direct_push_has_no_merge_time_and_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._out(tmp, added=["2026-10-01-trail-closure"])
            result = snap.announcements_snapshot(gh({"/commits/a1/pulls": []}), out, ".",
                                                 last_commit=self._commits({
                "records/announcement/2026-10-01-trail-closure.md": "a1"}))
        self.assertEqual(result, {})

    def test_a_failed_build_leaves_the_field_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(snap.announcements_snapshot(gh({}), tmp, "."), {})


class Cli(unittest.TestCase):
    def _main(self, argv, env, request):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = snap.main([*argv, "--out", str(out)], env=env, request=request, now=NOW)
            return rc, json.loads(out.read_text()), buf.getvalue()

    def test_writes_the_counts(self):
        rc, data, _ = self._main(["intake"], {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t"},
                                 FakeAPI({"/issues?state=open": [issue(1, NOW - 10)],
                                          "/pulls?state=open": []}))
        self.assertEqual((rc, data), (0, {"open": 1, "oldest_opened_at": NOW - 10}))

    def test_no_token_writes_nothing_asks_nothing_and_exits_zero(self):
        api = FakeAPI({})
        rc, data, text = self._main(["reviewed"], {"GITHUB_REPOSITORY": REPO}, api)
        self.assertEqual((rc, data, api.calls), (0, {}, []))
        self.assertIn("::warning", text)

    def test_an_unreachable_github_is_left_out_not_zero(self):
        api = FakeAPI({"/issues?state=open": SourceError("GET /issues: HTTP 502")})
        rc, data, text = self._main(["intake"], {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "t"},
                                    api)
        self.assertEqual((rc, data), (0, {}))
        self.assertIn("could not count intake", text)


if __name__ == "__main__":
    unittest.main()
