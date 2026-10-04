"""Tests for scripts/board.py — the plain public board on the published branch.

Each test builds a small vault and artifacts directory (what the publish job's
``_out`` holds) in a temporary directory, runs the board, and reads the page.
Held here: every state renders as a word and its colour; a missing or unreadable
``standing.json`` renders a page that says "no data" with no rows, and the script
still exits 0; text from files is escaped; a satisfying record links to its file
on GitHub; "last published" is the newest receipt's server-side time. Stdlib only,
offline, name-free.

    python3 -m unittest discover -s templates/records/scripts/tests -p "test_*.py"
"""

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import board  # noqa: E402

REPO = "Example-Org/example-records"
DIGEST = "a" * 64

ROWS = [
    {"id": "annual-report", "state": "green", "satisfied_by": "2026-01-15-annual-report",
     "deadline": "2027-01-15", "published_at": "2026-01-16T09:00:00Z", "gap": -5,
     "history": None},
    {"id": "charity-filing", "state": "amber", "satisfied_by": None,
     "deadline": "2026-10-08", "published_at": None, "gap": -4, "history": None},
    {"id": "meeting-notice", "state": "red", "satisfied_by": None,
     "deadline": "2026-09-14", "published_at": None, "gap": 1,
     "history": {"window_days": 730, "evaluated": 3, "breaches": 1}},
    {"id": "minutes-timely", "state": "no-data", "satisfied_by": None,
     "deadline": None, "published_at": None, "gap": None, "history": None},
]

CORPUS = [
    {"doc_id": "2026-01-15-annual-report", "title": "Annual report 2025", "tags": ["report"],
     "volatility": "stable", "body": "# Annual report 2025", "path": "records/report/2026-01-15-annual-report.md",
     "certainty": "verified", "archived": False},
    {"doc_id": "2026-09-01-board-minutes", "title": "Board minutes, 1 September", "tags": ["minutes"],
     "volatility": "stable", "body": "# Board minutes", "path": "records/minutes/2026-09-01-board-minutes.md",
     "certainty": "verified", "archived": False},
]


def receipt_md(published_at, digest=DIGEST):
    return (f'---\ndigest: "{digest}"\npublished_at: "{published_at}"\n'
            'run_url: "https://example.invalid/run/1"\ncommit: "abcdef1"\n'
            "records:\n  added: []\n  changed: []\n  retracted: []\n"
            "standing:\n  green: 1\n  amber: 1\n  red: 1\n  no_data: 1\n---\n\n# Publish receipt\n")


class BoardCase(unittest.TestCase):
    """A vault with index.md and an obligation, and an artifacts directory with
    a standing file, a corpus, and a receipt. Tests change what they need."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="board-"))
        self.vault = self.tmp / "vault"
        self.out = self.tmp / "_out"
        (self.vault / "obligations").mkdir(parents=True)
        (self.out / "receipts").mkdir(parents=True)
        (self.vault / "index.md").write_text("# Example Trust records\n\nHello.\n", encoding="utf-8")
        self.write_obligation("annual-report", "Annual report filed")
        self.write_standing(ROWS)
        self.write_corpus(CORPUS)
        (self.out / "receipts" / f"2026-10-03T120000Z-{DIGEST}.md").write_text(
            receipt_md("2026-10-03T12:00:00Z"), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_obligation(self, oid, title):
        (self.vault / "obligations" / f"{oid}.json").write_text(json.dumps({
            "id": oid, "title": title, "pack": "test", "record_type": "report",
            "subjects": ["reports"], "source": "test", "disclaimer_ref": "policy.disclaimer",
            "cadence": {"months": 12}}), encoding="utf-8")

    def write_standing(self, rows):
        (self.out / "standing.json").write_text(json.dumps(rows), encoding="utf-8")

    def write_corpus(self, records, digest=DIGEST):
        bundle = {"digest": digest, "published_at": "2026-10-03T12:00:00Z", "records": records}
        (self.out / f"corpus-{digest}.json").write_text(json.dumps(bundle), encoding="utf-8")

    def page(self, repo=REPO):
        """Run the CLI as the workflow does; return the page it wrote."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = board.main(["--vault", str(self.vault), "--artifacts", str(self.out),
                               "--repo", repo])
        self.assertEqual(code, 0)
        return (self.out / "index.html").read_text(encoding="utf-8")

    def body_rows(self, page):
        m = re.search(r"<tbody>(.*?)</tbody>", page, re.S)
        return re.findall(r"<tr>.*?</tr>", m.group(1), re.S) if m else []


class EveryStateIsAWordAndAColour(BoardCase):
    def test_green_amber_red_and_no_data(self):
        rows = self.body_rows(self.page())
        self.assertEqual(len(rows), 4)
        want = [("green", "#1b7a3d"), ("amber", "#8a5300"), ("red", "#b3261e"), ("no data", "#595959")]
        for row, (word, colour) in zip(rows, want):
            with self.subTest(word=word):
                self.assertIn(f'<span class="state" style="background:{colour}">{word}</span>', row)

    def test_no_data_row_is_never_shown_as_green(self):
        row = self.body_rows(self.page())[3]
        self.assertIn("minutes-timely", row)
        self.assertNotIn("green", row)

    def test_obligation_title_comes_from_obligations_else_the_id(self):
        rows = self.body_rows(self.page())
        self.assertIn('<th scope="row">Annual report filed</th>', rows[0])
        self.assertIn('<th scope="row">charity-filing</th>', rows[1])

    def test_deadline_shown(self):
        rows = self.body_rows(self.page())
        self.assertIn("<td>2027-01-15</td>", rows[0])
        self.assertIn('<td><span class="muted">none</span></td>', rows[3])


class MissingStandingSaysNoData(BoardCase):
    def assert_no_data_page(self, page):
        self.assertIn("The board has no data.", page)
        self.assertIn("nothing here is shown as met", page)
        self.assertNotIn("<tbody>", page)
        self.assertNotIn(">green<", page)

    def test_missing_file(self):
        (self.out / "standing.json").unlink()
        self.assert_no_data_page(self.page())

    def test_unreadable_or_off_schema_file(self):
        for text in ("{not json", '"a string"', "{}", "null",
                     json.dumps([{**ROWS[0], "state": "ok"}]),
                     json.dumps([{"id": "x"}]), "\udcff"):
            with self.subTest(text=text[:30]):
                (self.out / "standing.json").write_text(text, encoding="utf-8", errors="surrogateescape")
                self.assert_no_data_page(self.page())

    def test_empty_list_says_none_declared_not_green(self):
        self.write_standing([])
        page = self.page()
        self.assertIn("No posting obligations are declared", page)
        self.assertNotIn("<tbody>", page)


class BadInputNeverRaises(BoardCase):
    def test_everything_broken_still_writes_a_page(self):
        (self.vault / "index.md").write_bytes(b"\xff\xfe")
        (self.vault / "obligations" / "bad.json").write_text("{nope", encoding="utf-8")
        (self.out / "standing.json").write_text("[1, 2", encoding="utf-8")
        (self.out / f"corpus-{DIGEST}.json").write_text('{"records": 5}', encoding="utf-8")
        for name, text in (("a.md", "no frontmatter"), ("b.md", receipt_md("2026-99-99")),
                           ("c.json", "[]"), ("d.md", "---\npublished_at: [\n---\n")):
            (self.out / "receipts" / name).write_text(text, encoding="utf-8")
        (self.out / "receipts" / f"2026-10-03T120000Z-{DIGEST}.md").unlink()
        page = self.page()
        self.assertIn("The board has no data.", page)
        self.assertIn("Last published: unknown", page)
        self.assertIn("The list of records could not be read.", page)
        self.assertIn("<h1>Public records</h1>", page)

    def test_no_artifacts_directory_at_all(self):
        shutil.rmtree(self.out)
        self.assertIn("The board has no data.", self.page())


class TextIsEscaped(BoardCase):
    def test_titles_containing_markup(self):
        (self.vault / "index.md").write_text("# Parks & <b>Trails</b>\n", encoding="utf-8")
        self.write_obligation("annual-report", "Report <script>alert(1)</script>")
        self.write_corpus([{**CORPUS[0], "title": 'Budget < 5% "draft"'}])
        page = self.page()
        self.assertIn("<h1>Parks &amp; &lt;b&gt;Trails&lt;/b&gt;</h1>", page)
        self.assertIn("Report &lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("Budget &lt; 5% &quot;draft&quot;", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("<b>", page)


class RecordLinks(BoardCase):
    LINK = (f"https://github.com/{REPO}/blob/main/records/report/2026-01-15-annual-report.md")

    def test_satisfied_by_links_to_the_record_file_on_main(self):
        row = self.body_rows(self.page())[0]
        self.assertIn(f'<a href="{self.LINK}">Annual report 2025</a>', row)

    def test_recent_records_newest_first_and_linked(self):
        recent = re.search(r'<ul class="recent">(.*?)</ul>', self.page(), re.S).group(1)
        self.assertLess(recent.index("2026-09-01-board-minutes"), recent.index("2026-01-15-annual-report"))
        self.assertIn(self.LINK, recent)

    def test_without_a_repository_there_are_no_links_but_the_id_shows(self):
        page = self.page(repo="")
        self.assertNotIn("github.com", page)
        self.assertIn("<code>2026-01-15-annual-report</code>", self.body_rows(page)[0])

    def test_record_missing_from_the_corpus_is_text_not_a_guessed_link(self):
        self.write_corpus([CORPUS[1]])
        row = self.body_rows(self.page())[0]
        self.assertIn("<code>2026-01-15-annual-report</code>", row)
        self.assertNotIn("<a ", row)


class LastPublished(BoardCase):
    def test_newest_receipt_server_side_time(self):
        (self.out / "receipts" / f"2026-01-01T000000Z-{DIGEST}.md").write_text(
            receipt_md("2026-01-01T00:00:00Z"), encoding="utf-8")
        page = self.page()
        self.assertIn('Last published <time datetime="2026-10-03T12:00:00Z">3 October 2026, 12:00 UTC</time>', page)
        self.assertIn(f"https://github.com/{REPO}/blob/published/receipts/2026-10-03T120000Z-{DIGEST}.md", page)

    def test_published_branch_layout_reads_corpus_latest(self):
        (self.out / f"corpus-{DIGEST}.json").rename(self.out / "corpus-latest.json")
        self.assertIn("Board minutes, 1 September", self.page())


class _Checker(HTMLParser):
    VOID = {"meta", "br", "img", "link", "input", "hr"}

    def __init__(self):
        super().__init__()
        self.stack, self.problems, self.tags = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.problems.append(f"unbalanced </{tag}>")


class PlainAndAccessible(BoardCase):
    def test_well_formed_self_contained_page(self):
        checker = _Checker()
        checker.feed(self.page())
        self.assertEqual(checker.problems, [])
        self.assertEqual(checker.stack, [])
        tags = dict(checker.tags)
        self.assertEqual(tags["html"].get("lang"), "en")
        self.assertIn("width=device-width", self.page())
        names = {t for t, _ in checker.tags}
        self.assertFalse(names & {"script", "link", "img", "iframe"}, names)
        for tag, attrs in checker.tags:
            self.assertNotIn("src", attrs)
            if tag == "th":
                self.assertIn(attrs.get("scope"), ("col", "row"))
            href = attrs.get("href")
            if href:
                self.assertTrue(href.startswith("https://github.com/") or "/" not in href, href)

    def test_state_colours_meet_aa_contrast_with_white_text(self):
        def luminance(hex_colour):
            c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        for state, (_, colour, _) in board.STATES.items():
            with self.subTest(state=state):
                self.assertGreaterEqual(1.05 / (luminance(colour) + 0.05), 4.5)


if __name__ == "__main__":
    unittest.main()
