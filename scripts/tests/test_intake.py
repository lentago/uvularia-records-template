"""Tests for the Issue-form intake door (scripts/intake.py). Stdlib only.

Why here and not core/tests: the Issue form is a *client* of the core (CLAUDE.md
invariant 7 — intake doors are clients; the core owns schemas and formats). It
lives in the vault template, so its test lives beside it. The test does lean on
the vendored core/validate.py to prove the scaffold passes validation unedited —
that is the whole "done when" of issue #7 — but it adds nothing to core/.

Two fixture issue bodies drive it: one with an attached-file URL, one without.
The network is never touched; the download is injected.

    python3 -m unittest discover -s templates/records/scripts/tests -p "test_*.py"
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]      # templates/records/scripts
VAULT_TEMPLATE = SCRIPTS_DIR.parent                    # templates/records
CORE_DIR = VAULT_TEMPLATE / "core"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CORE_DIR / "schema"))

import intake  # noqa: E402
import validate  # noqa: E402


def read_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_fetch(payload=b"%PDF-1.4 fake pdf bytes\n"):
    """A download() stand-in that writes fixed bytes and succeeds."""
    def _fetch(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return None
    return _fetch


def failing_fetch(reason="the download returned HTTP 404 (Not Found)"):
    def _fetch(url, dest):
        return reason
    return _fetch


class DetectFormTest(unittest.TestCase):
    """The workflow triggers on every issue and keys on the form's own headings,
    not a label the template cannot create (#31). is_intake_form is that gate."""

    def test_the_record_form_is_detected(self):
        self.assertTrue(intake.is_intake_form(read_fixture("with-attachment.md")))
        self.assertTrue(intake.is_intake_form(read_fixture("without-attachment.md")))

    def test_an_unrelated_issue_is_not_detected(self):
        # A plain bug report with neither required heading must be ignored, so the
        # workflow leaves it alone. This is the check's proof it can fail.
        self.assertFalse(intake.is_intake_form(
            "### Steps to reproduce\n\nClick the thing.\n\n### What I expected\n\nNot that.\n"))
        self.assertFalse(intake.is_intake_form("Just a comment, no headings at all."))

    def test_one_heading_missing_is_not_enough(self):
        # Both the record-type and the effective-date heading are required; one
        # alone (e.g. a form someone half-pasted) is not a record form.
        self.assertFalse(intake.is_intake_form("### Record type\n\nnotice\n"))
        self.assertFalse(intake.is_intake_form("### Date it takes effect\n\n2026-01-15\n"))

    def test_detect_cli_mode_returns_zero_or_one(self):
        with tempfile.TemporaryDirectory() as d:
            form = Path(d) / "form.md"
            form.write_text(read_fixture("with-attachment.md"), encoding="utf-8")
            self.assertEqual(intake.run(["--detect", "--body-file", str(form)]), 0)
            other = Path(d) / "other.md"
            other.write_text("### Bug\n\nbroken\n", encoding="utf-8")
            self.assertEqual(intake.run(["--detect", "--body-file", str(other)]), 1)


class ParseFormTest(unittest.TestCase):
    def test_parses_every_field_with_attachment(self):
        form = intake.parse_issue_form(read_fixture("with-attachment.md"))
        self.assertEqual(form["type"], "notice")
        self.assertEqual(form["effective"], "2026-01-15")
        self.assertEqual(form["title"], "Notice of the 2026 annual meeting")
        self.assertIn("annual meeting will be held", form["summary"])
        self.assertEqual(form["subjects"], "annual-meeting, budget, notice")
        self.assertIn("user-attachments", form["document"])

    def test_blank_field_becomes_empty_string(self):
        form = intake.parse_issue_form(read_fixture("without-attachment.md"))
        # GitHub's "_No response_" for the empty document box reads as empty.
        self.assertEqual(form["document"], "")
        self.assertEqual(form["type"], "minutes")

    def test_subjects_split_and_normalised(self):
        self.assertEqual(
            intake.split_subjects("Budget, Annual Meeting , budget"),
            ["budget", "annual-meeting"])

    def test_find_attachment(self):
        found = intake.find_attachment(read_fixture("with-attachment.md"))
        self.assertIsNotNone(found)
        filename, url = found
        self.assertEqual(filename, "annual-meeting-notice.pdf")
        self.assertTrue(url.startswith("https://github.com/user-attachments/assets/"))
        self.assertIsNone(intake.find_attachment(read_fixture("without-attachment.md")))

    def test_bad_date_is_rejected_with_a_plain_message(self):
        body = read_fixture("without-attachment.md").replace("2026-01-20", "Jan 20 2026")
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.build_fields(intake.parse_issue_form(body))
        self.assertIn("YYYY-MM-DD", str(ctx.exception))


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        # A real vault to scaffold into: a copy of the template (it carries the
        # vendored core/, the schema, validate.toml, and the empty record dirs).
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = self.tmp / "vault"
        shutil.copytree(VAULT_TEMPLATE, self.vault,
                        ignore=shutil.ignore_patterns(".git"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _validate(self):
        cfg = validate.load_config(self.vault)
        problems, ran, _skipped = validate.validate_vault(self.vault, cfg)
        self.assertIn("schema", ran)
        return problems

    def test_with_attachment_scaffolds_source_manifest_and_validates(self):
        result = intake.scaffold(self.vault, read_fixture("with-attachment.md"),
                                 fetch=fake_fetch())
        self.assertTrue(result["ok"])
        self.assertEqual(result["record_path"], "records/notice/2026-01-15-notice-of-the-2026-annual-meeting.md")
        self.assertTrue(result["attachment"]["downloaded"])

        record = (self.vault / result["record_path"]).read_text(encoding="utf-8")
        self.assertIn("status: draft", record)
        self.assertIn("visibility: public", record)
        self.assertIn("certainty: reported", record)
        self.assertIn("source:", record)
        self.assertIn(result["attachment"]["sha256"], record)

        # The file landed under library/ and is listed in the manifest.
        saved = self.vault / result["attachment"]["file"]
        self.assertTrue(saved.is_file())
        manifest = validate._load_manifest(self.vault)
        self.assertEqual(manifest[result["attachment"]["file"]], result["attachment"]["sha256"])

        # The whole point of #7: the scaffold validates unedited.
        self.assertEqual(self._validate(), [])

    def test_without_attachment_scaffolds_draft_note_and_validates(self):
        result = intake.scaffold(self.vault, read_fixture("without-attachment.md"),
                                 fetch=fake_fetch())
        self.assertTrue(result["ok"])
        self.assertFalse(result["attachment"]["found"])
        self.assertEqual(result["record_path"], "records/minutes/2026-01-20-board-meeting-minutes-20-january-2026.md")

        record = (self.vault / result["record_path"]).read_text(encoding="utf-8")
        self.assertIn("status: draft", record)
        self.assertNotIn("kind: pdf", record)   # no source block in the frontmatter
        self.assertIn("Add the original document by hand", record)
        # No file was attached, so the note must say exactly that — never that a
        # download failed, which would be a lie when nothing was dragged in (#31).
        self.assertIn("No file was attached", record)
        self.assertNotIn("could not be downloaded", record)

        # A draft needs no source, so it still validates clean.
        self.assertEqual(self._validate(), [])

    def test_failed_download_still_scaffolds_without_source(self):
        result = intake.scaffold(self.vault, read_fixture("with-attachment.md"),
                                 fetch=failing_fetch())
        self.assertTrue(result["ok"])          # we still produced a record
        self.assertTrue(result["attachment"]["found"])
        self.assertFalse(result["attachment"]["downloaded"])
        self.assertIn("404", result["attachment"]["reason"])

        record = (self.vault / result["record_path"]).read_text(encoding="utf-8")
        self.assertNotIn("kind: pdf", record)   # no source block in the frontmatter
        self.assertIn("Add the original document by hand", record)
        # A file WAS attached but could not be fetched, so the note says that —
        # the mirror of the no-attachment case above; the two never share wording.
        self.assertIn("could not be downloaded", record)
        self.assertNotIn("No file was attached", record)
        self.assertEqual(self._validate(), [])

    def test_scaffold_writes_only_vault_files_not_the_workflow_working_files(self):
        # The workflow's working files (the issue body, the JSON result, the
        # validator output) belong in the runner temp dir, never the checkout, so
        # they are not committed onto the intake branch (#31). intake.py must write
        # nothing but vault content: the record, and (when fetched) the library
        # file and its manifest. This proves the script half of that contract.
        before = {p.relative_to(self.vault)
                  for p in self.vault.rglob("*") if p.is_file()}
        result = intake.scaffold(self.vault, read_fixture("with-attachment.md"),
                                 fetch=fake_fetch())
        after = {p.relative_to(self.vault)
                 for p in self.vault.rglob("*") if p.is_file()}
        new = {str(p) for p in (after - before)}

        # Only the record and the downloaded library file are *new*; the manifest
        # ships with the template and is updated in place, not created.
        self.assertEqual(new, {
            result["record_path"],
            result["attachment"]["file"],
        })
        manifest = validate._load_manifest(self.vault)
        self.assertIn(result["attachment"]["file"], manifest)
        for stray in ("issue-body.md", "intake-result.json", "validate.txt"):
            self.assertNotIn(stray, new)
            self.assertFalse((self.vault / stray).exists())


if __name__ == "__main__":
    unittest.main()
