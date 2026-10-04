"""Tests for scripts/telemetry.py — the workflows' "describe this stage" step.

Unset configuration must mean one notice line, no event, and exit 0; set
configuration must yield a well-formed event per stage. Stdlib only, offline.

    python3 -m unittest discover -s templates/records/scripts/tests -p "test_*.py"
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import telemetry  # noqa: E402

CONFIGURED = {"LOKI_PUSH_URL": "https://logs-prod-000.grafana.net", "LOKI_TOKEN_SET": "true",
              "GITHUB_REPOSITORY_OWNER": "Example-Org", "GITHUB_RUN_ID": "77", "GITHUB_SHA": "abc"}

RECEIPT = """---
digest: "{d}"
published_at: "2026-10-04T12:00:00Z"
run_url: "https://github.com/example/records/actions/runs/77"
commit: "abcdef0"
records:
  added: ["2026-01-15-board-minutes", "2026-02-01-notice"]
  changed: []
  retracted: ["2025-12-01-old"]
standing:
  green: 1
  amber: 0
  red: 1
  no_data: 0
---
"""


def run(argv, env):
    """Run main(); return (rc, printed text, outputs dict)."""
    tmp = Path(tempfile.mkdtemp())
    try:
        out_file = tmp / "out"
        out_file.write_text("")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = telemetry.main(argv, env={**env, "GITHUB_OUTPUT": str(out_file)})
        outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
        return rc, buf.getvalue(), outputs
    finally:
        shutil.rmtree(tmp)


class NotConfigured(unittest.TestCase):
    def test_unset_url_is_one_notice_and_no_event(self):
        for stage in telemetry.STAGES:
            rc, text, out = run([stage], {"GITHUB_REPOSITORY_OWNER": "x"})
            self.assertEqual(rc, 0)
            self.assertEqual(out["enabled"], "false")
            self.assertEqual(text.strip().count("\n"), 0, "exactly one line")
            self.assertIn("telemetry not configured", text)

    def test_url_without_token_sends_nothing(self):
        env = dict(CONFIGURED, LOKI_TOKEN_SET="false")
        rc, text, out = run(["rules_released", "--tag", "rules-v2"], env)
        self.assertEqual((rc, out["enabled"]), (0, "false"))
        self.assertIn("LOKI_WRITE_TOKEN", text)

    def test_a_broken_builder_warns_and_exits_zero(self):
        orig = telemetry.BUILDERS["rules_released"]
        telemetry.BUILDERS["rules_released"] = lambda a: 1 / 0
        try:
            rc, text, out = run(["rules_released"], CONFIGURED)
        finally:
            telemetry.BUILDERS["rules_released"] = orig
        self.assertEqual((rc, out["enabled"]), (0, "false"))
        self.assertIn("::warning", text)


class Payloads(unittest.TestCase):
    def payload(self, argv):
        rc, _, out = run(argv, CONFIGURED)
        self.assertEqual((rc, out["enabled"]), (0, "true"))
        self.assertEqual(out["cluster"], "example-org")
        return json.loads(out["payload"])

    def test_published(self):
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "receipts").mkdir()
            (out / f"corpus-{digest}.json").write_text(json.dumps(
                {"digest": digest, "published_at": "2026-10-04T12:00:00Z", "records": [{}, {}, {}]}))
            (out / "standing.json").write_text(json.dumps(
                [{"state": "green"}, {"state": "red"}, {"state": "no-data"}]))
            (out / "receipts" / f"2026-10-04T120000Z-{digest}.md").write_text(RECEIPT.format(d=digest))
            p = self.payload(["published", "--out-dir", tmp, "--job-status", "success"])
        self.assertEqual(p["digest"], digest)
        self.assertEqual(p["records"], {"total": 3, "added": 2, "changed": 0, "retracted": 1})
        self.assertEqual(p["standing"], {"green": 1, "amber": 0, "red": 1, "no_data": 1, "total": 3})
        self.assertEqual(p["receipt"], f"2026-10-04T120000Z-{digest}.md")
        self.assertEqual((p["job_status"], p["run_id"]), ("success", "77"))

    def test_failed_publish_reports_no_data_not_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.payload(["published", "--out-dir", tmp, "--job-status", "failure"])
        self.assertIsNone(p["standing"])
        self.assertIsNone(p["digest"])
        self.assertEqual(p["job_status"], "failure")

    def test_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "standing.json"
            path.write_text(json.dumps([{"state": "amber"}]))
            p = self.payload(["reviewed", "--standing", str(path), "--validate", "failure", "--pr", "9"])
        self.assertEqual((p["pr"], p["validate"]), (9, "failure"))
        self.assertEqual(p["standing"]["amber"], 1)

    def test_intake_outcomes(self):
        base = ["intake", "--issue", "42", "--is-form", "true"]
        self.assertEqual(self.payload(base + ["--scaffold", "failure"])["outcome"], "form_unreadable")
        p = self.payload(base + ["--scaffold", "success", "--branch-exists", "false",
                                 "--pr-result", "opened", "--pr-number", "43"])
        self.assertEqual((p["issue"], p["outcome"], p["pr"]), (42, "pr_opened", 43))
        p = self.payload(base + ["--scaffold", "success", "--branch-exists", "false",
                                 "--pr-result", "blocked"])
        self.assertEqual((p["outcome"], p["pr"]), ("branch_pushed", None))
        p = self.payload(base + ["--scaffold", "success", "--branch-exists", "true"])
        self.assertEqual(p["outcome"], "already_open")

    def test_an_ordinary_issue_is_not_an_event(self):
        rc, text, out = run(["intake", "--issue", "5", "--is-form", "false"], CONFIGURED)
        self.assertEqual((rc, out["enabled"]), (0, "false"))

    def test_evals_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Path(tmp) / "s.json"
            s.write_text(json.dumps({"mode": "dry", "total": 10, "passed": 9, "failed": 1}))
            p = self.payload(["evals", "--summary", str(s), "--corpus", "present"])
        self.assertEqual((p["total"], p["passed"], p["failed"]), (10, 9, 1))
        self.assertEqual(self.payload(["rules_released", "--tag", "rules-v3"])["tag"], "rules-v3")

    def test_cluster_slug(self):
        self.assertEqual(telemetry.cluster_slug("", "Example_Org"), "example_org")
        self.assertEqual(telemetry.cluster_slug("My Org!", "x"), "my-org")
        self.assertEqual(telemetry.cluster_slug("", ""), "unknown")


if __name__ == "__main__":
    unittest.main()
