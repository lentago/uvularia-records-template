"""Tests for scripts/watch.py — the scheduled "tell me in an issue" pass.

The condition logic runs over fixture standings, bundles, and /health replies;
the issue side runs against an in-memory GitHub that speaks the same REST calls.
Held here: each condition opens exactly one issue, a second run opens nothing
new, a cleared condition closes its issue, missing data never closes one, and an
absent ASK_HEALTH_URL is a notice, not a failure. Stdlib only, offline.

    python3 -m unittest discover -s templates/records/scripts/tests -p "test_*.py"
"""

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
CORE_SCHEMA = SCRIPTS_DIR.parent / "core" / "schema"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "watch"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CORE_SCHEMA))

import watch  # noqa: E402
from check_examples import validate  # noqa: E402

BASE = "https://example.github.io/example-records"
HEALTH = "https://fn.example/health"
REPO = "example/example-records"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)   # 60 min after the publish


def fx(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeGitHub:
    """Just enough of the Issues REST API, in memory, behind watch.GitHub."""

    def __init__(self):
        self.issues = {}
        self.comments = {}
        self.labels = set()
        self.next = 1
        self.calls = []

    def request(self, url, *, method="GET", token=None, body=None, timeout=20):
        assert token == "t", "every GitHub call carries the token"
        parsed = urlparse(url)
        path = parsed.path.split(f"/repos/{REPO}", 1)[1]
        self.calls.append((method, path))
        if method == "GET" and path == f"/labels/{watch.LABEL}":
            return (200, {"name": watch.LABEL}) if watch.LABEL in self.labels else (404, None)
        if method == "POST" and path == "/labels":
            self.labels.add(body["name"])
            return 201, body
        if method == "GET" and path == "/issues":
            q = parse_qs(parsed.query)
            page, per = int(q["page"][0]), int(q["per_page"][0])
            hits = [i for i in sorted(self.issues.values(), key=lambda i: i["number"])
                    if i["state"] == "open" and q["labels"][0] in i["labels"]]
            return 200, [dict(i) for i in hits[(page - 1) * per: page * per]]
        if method == "POST" and path == "/issues":
            n = self.next
            self.next += 1
            for label in body.get("labels", []):
                assert label in self.labels, "the label exists before it is used"
            self.issues[n] = {"number": n, "title": body["title"], "body": body["body"],
                              "labels": list(body.get("labels", [])), "state": "open"}
            return 201, {"number": n}
        m = re.fullmatch(r"/issues/(\d+)(/comments)?", path)
        if m:
            n = int(m.group(1))
            if m.group(2):
                self.comments.setdefault(n, []).append(body["body"])
                return 201, {}
            if method == "PATCH":
                self.issues[n].update(body)
                return 200, self.issues[n]
        raise AssertionError(f"unexpected call {method} {path}")

    # Conveniences for assertions.
    def open(self):
        return [i for i in self.issues.values() if i["state"] == "open"]

    def open_keys(self):
        return sorted(k for k in map(watch.issue_key, self.open()) if k)


def make_fetch(standing=None, bundle="corpus-latest.json", health=None, broken=()):
    """A fetch over fixture names. None means 404; a URL in ``broken`` raises."""
    table = {f"{BASE}/standing.json": standing, f"{BASE}/corpus-latest.json": bundle,
             HEALTH: health}

    def fetch(url, **kw):
        if url in broken:
            raise watch.SourceError(f"GET {url}: timed out")
        name = table.get(url)
        return (404, None) if name is None else (200, fx(name))
    return fetch


def run(gh, *, health_url=HEALTH, now=NOW, dry_run=False, **sources):
    logs = []
    code = watch.run(base=BASE, health_url=health_url,
                     gh=watch.GitHub(REPO, "t", request=gh.request), now=now,
                     fetch=make_fetch(**sources), log=logs.append, dry_run=dry_run)
    return code, logs


# --------------------------------------------------------------------------- #
# The fixtures are real shapes: standings validate against core's schema.      #
# --------------------------------------------------------------------------- #

class FixturesAreRealTest(unittest.TestCase):
    def test_standing_fixtures_validate_against_the_schema(self):
        schema = json.loads((CORE_SCHEMA / "standing.schema.json").read_text())
        for path in sorted(FIXTURES.glob("standing-*.json")):
            with self.subTest(path.name):
                self.assertEqual(validate(schema, fx(path.name), schema), [])

    def test_bundle_fixture_carries_digest_and_published_at(self):
        schema = json.loads((CORE_SCHEMA / "bundle.schema.json").read_text())
        self.assertEqual(validate(schema, fx("corpus-latest.json"), schema), [])


# --------------------------------------------------------------------------- #
# Condition logic, pure.                                                       #
# --------------------------------------------------------------------------- #

class StaleDigestTest(unittest.TestCase):
    bundle = fx("corpus-latest.json")

    def test_mismatch_over_thirty_minutes_is_active(self):
        f = watch.stale_digest(self.bundle, fx("health-stale.json"), NOW)
        self.assertEqual(f.status, watch.ACTIVE)
        self.assertIn("What to do", f.body)

    def test_mismatch_under_thirty_minutes_waits(self):
        soon = datetime(2026, 10, 4, 11, 20, tzinfo=timezone.utc)
        f = watch.stale_digest(self.bundle, fx("health-stale.json"), soon)
        self.assertEqual(f.status, watch.UNKNOWN)

    def test_exactly_thirty_minutes_still_waits(self):
        edge = datetime(2026, 10, 4, 11, 30, tzinfo=timezone.utc)
        self.assertEqual(watch.stale_digest(self.bundle, fx("health-stale.json"),
                                            edge).status, watch.UNKNOWN)

    def test_matching_digest_clears(self):
        f = watch.stale_digest(self.bundle, fx("health-current.json"), NOW)
        self.assertEqual(f.status, watch.CLEAR)

    def test_paused_or_pinned_box_is_not_stale(self):
        for name in ("health-paused.json", "health-pinned.json"):
            with self.subTest(name):
                self.assertEqual(watch.stale_digest(self.bundle, fx(name), NOW).status,
                                 watch.CLEAR)

    def test_no_health_or_no_bundle_is_unknown(self):
        self.assertEqual(watch.stale_digest(self.bundle, None, NOW).status, watch.UNKNOWN)
        self.assertEqual(watch.stale_digest(None, fx("health-stale.json"), NOW).status,
                         watch.UNKNOWN)


class CapTest(unittest.TestCase):
    def test_eighty_percent_is_active(self):
        self.assertEqual(watch.cap(fx("health-cap-80.json")).status, watch.ACTIVE)

    def test_just_under_eighty_clears(self):
        self.assertEqual(watch.cap(fx("health-cap-79.json")).status, watch.CLEAR)

    def test_unreadable_counter_is_unknown_not_clear(self):
        self.assertEqual(watch.cap(fx("health-cap-unknown.json")).status, watch.UNKNOWN)

    def test_full_and_high_are_different_states(self):
        full = dict(fx("health-cap-80.json"), cap_used=100)
        self.assertEqual(watch.cap(full).state, "full")
        self.assertEqual(watch.cap(fx("health-cap-80.json")).state, "high")


class ObligationTest(unittest.TestCase):
    def test_amber_and_red_are_active(self):
        for name, state in (("standing-one-amber.json", "amber"),
                            ("standing-one-red.json", "red")):
            with self.subTest(name):
                (f,) = watch.obligations(fx(name), set())
                self.assertEqual((f.key, f.status, f.state),
                                 ("obligation:annual-report", watch.ACTIVE, state))

    def test_green_with_no_issue_produces_nothing(self):
        self.assertEqual(watch.obligations(fx("standing-all-green.json"), set()), [])

    def test_no_data_never_clears(self):
        (f,) = watch.obligations(fx("standing-no-data.json"), {"obligation:annual-report"})
        self.assertEqual(f.status, watch.UNKNOWN)

    def test_vanished_obligation_is_left_alone(self):
        (f,) = watch.obligations([], {"obligation:retired-rule"})
        self.assertEqual(f.status, watch.UNKNOWN)


# --------------------------------------------------------------------------- #
# End to end against the in-memory GitHub: open, dedupe, update, close.        #
# --------------------------------------------------------------------------- #

class ReconcileTest(unittest.TestCase):
    def test_each_condition_opens_one_issue_and_a_rerun_opens_nothing(self):
        gh = FakeGitHub()
        sources = dict(standing="standing-one-amber.json", health="health-stale.json")
        code, _ = run(gh, **sources)
        self.assertEqual(code, 0)
        self.assertEqual(gh.open_keys(), ["obligation:annual-report", "stale-digest"])
        for issue in gh.open():
            self.assertEqual(issue["labels"], [watch.LABEL])
            self.assertIn("What happened", issue["body"])
            self.assertIn("What to do", issue["body"])

        before = len(gh.issues)
        writes_before = [c for c in gh.calls if c[0] != "GET"]
        code, logs = run(gh, **sources)
        self.assertEqual(code, 0)
        self.assertEqual(len(gh.issues), before, "nothing is opened twice")
        self.assertEqual([c for c in gh.calls if c[0] != "GET"], writes_before,
                         "an unchanged condition is not even edited")
        self.assertIn("Nothing to open, update, or close.", logs)

    def test_clearing_closes_the_issue_with_a_comment(self):
        gh = FakeGitHub()
        run(gh, standing="standing-one-amber.json", health="health-stale.json")
        run(gh, standing="standing-all-green.json", health="health-current.json")
        self.assertEqual(gh.open(), [])
        for n, issue in gh.issues.items():
            self.assertEqual(issue["state_reason"], "completed")
            self.assertTrue(gh.comments[n][-1].startswith("Cleared:"))

    def test_amber_to_red_updates_the_same_issue_and_says_so(self):
        gh = FakeGitHub()
        run(gh, standing="standing-one-amber.json", health="health-current.json")
        run(gh, standing="standing-one-red.json", health="health-current.json")
        (issue,) = gh.open()
        self.assertEqual(len(gh.issues), 1)
        self.assertIn("is red", issue["title"])
        self.assertEqual(watch.issue_state(issue), "red")
        self.assertIn("amber → red", gh.comments[issue["number"]][-1])

    def test_no_data_and_unreadable_sources_never_close(self):
        gh = FakeGitHub()
        run(gh, standing="standing-one-red.json", health="health-cap-80.json")
        self.assertEqual(gh.open_keys(), ["cap", "obligation:annual-report"])

        run(gh, standing="standing-no-data.json", health="health-cap-unknown.json")
        self.assertEqual(gh.open_keys(), ["cap", "obligation:annual-report"])

        code, logs = run(gh, health=None, standing=None, bundle=None,
                         broken={f"{BASE}/standing.json", HEALTH})
        self.assertEqual(code, 1, "unreadable data fails the run")
        self.assertEqual(gh.open_keys(), ["cap", "obligation:annual-report"])
        self.assertTrue(any(line.startswith("::error") for line in logs))

    def test_cap_reopens_as_a_new_issue_the_next_day(self):
        gh = FakeGitHub()
        run(gh, standing="standing-all-green.json", health="health-cap-80.json")
        run(gh, standing="standing-all-green.json", health="health-cap-79.json")
        run(gh, standing="standing-all-green.json", health="health-cap-80.json")
        self.assertEqual(len(gh.issues), 2)
        self.assertEqual(gh.open_keys(), ["cap"])

    def test_absent_health_url_skips_with_a_notice(self):
        gh = FakeGitHub()
        code, logs = run(gh, health_url="", standing="standing-one-amber.json")
        self.assertEqual(code, 0)
        self.assertEqual(gh.open_keys(), ["obligation:annual-report"])
        self.assertTrue(any("::notice" in line and "ASK_HEALTH_URL" in line for line in logs))

    def test_absent_health_url_leaves_an_existing_ask_issue_alone(self):
        gh = FakeGitHub()
        run(gh, standing="standing-all-green.json", health="health-stale.json")
        run(gh, health_url="", standing="standing-all-green.json")
        self.assertEqual(gh.open_keys(), ["stale-digest"])

    def test_health_404_is_an_error_not_a_skip(self):
        gh = FakeGitHub()
        code, logs = run(gh, standing="standing-all-green.json", health=None)
        self.assertEqual(code, 1)
        self.assertTrue(any("ends in /health" in line for line in logs))

    def test_unpublished_vault_is_a_notice(self):
        gh = FakeGitHub()
        code, logs = run(gh, health_url="", standing=None, bundle=None)
        self.assertEqual(code, 0)
        self.assertEqual(gh.issues, {})
        self.assertTrue(any("not published yet" in line for line in logs))

    def test_a_hand_made_duplicate_is_closed(self):
        gh = FakeGitHub()
        run(gh, standing="standing-one-amber.json", health_url="")
        (first,) = gh.open()
        gh.issues[99] = dict(first, number=99)
        run(gh, standing="standing-one-amber.json", health_url="")
        self.assertEqual([i["number"] for i in gh.open()], [first["number"]])
        self.assertEqual(gh.issues[99]["state_reason"], "not_planned")

    def test_issues_without_the_marker_are_ignored(self):
        gh = FakeGitHub()
        gh.labels.add(watch.LABEL)
        gh.issues[1] = {"number": 1, "title": "a note", "body": "hand written",
                        "labels": [watch.LABEL], "state": "open"}
        gh.next = 2
        run(gh, standing="standing-all-green.json", health="health-current.json")
        self.assertEqual(gh.issues[1]["state"], "open")

    def test_dry_run_writes_nothing(self):
        gh = FakeGitHub()
        _, logs = run(gh, standing="standing-one-red.json", health="health-stale.json",
                      dry_run=True)
        self.assertEqual([c for c in gh.calls if c[0] != "GET"], [])
        self.assertEqual(sum(line.startswith("would create") for line in logs), 2)

    def test_more_than_one_page_of_open_issues_is_read(self):
        gh = FakeGitHub()
        gh.labels.add(watch.LABEL)
        for n in range(1, 151):
            gh.issues[n] = {"number": n, "title": "x", "body": "unrelated",
                            "labels": [watch.LABEL], "state": "open"}
        gh.next = 151
        run(gh, standing="standing-one-amber.json", health_url="")
        run(gh, standing="standing-one-amber.json", health_url="")
        self.assertEqual(gh.open_keys().count("obligation:annual-report"), 1)


class DefaultBaseTest(unittest.TestCase):
    def test_project_pages_address(self):
        self.assertEqual(watch.default_base("Example-Org/example-records"),
                         "https://example-org.github.io/example-records")

    def test_user_site_repository(self):
        self.assertEqual(watch.default_base("example/example.github.io"),
                         "https://example.github.io")


if __name__ == "__main__":
    unittest.main()
