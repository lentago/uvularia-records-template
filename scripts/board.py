#!/usr/bin/env python3
"""Write the vault's public "Is it posted?" board as one plain HTML page.

The publish workflow runs this right after it builds the corpus, the standing
file, and the receipt. It reads those artifacts and writes ``index.html``, which
the workflow lays onto the ``published`` branch next to ``standing.json``. With
GitHub Pages serving that branch, the vault alone shows a public board at
``https://<owner>.github.io/<repo>/``. The site template is an optional extra.

    python3 scripts/board.py --artifacts _out                 # what publish runs
    python3 scripts/board.py --artifacts _out --out board.html

What goes on the page:

  * the vault's title, from the first ``# `` heading of ``index.md``;
  * "last published", from the newest receipt's ``published_at``, the
    server-side time the publish workflow stamped (never an author's clock);
  * one row per obligation in ``standing.json``: its title, its state as a word
    on a colour, its deadline, and the record that satisfies it, linked to the
    record file on GitHub;
  * the most recent records in the corpus.

**Never a fake green.** A missing or unreadable ``standing.json``, or one that
does not match ``core/schema/standing.schema.json``, gives a page that says in
words that the board has no data, with no rows. A ``no-data`` row says "no
data". On any bad input the script still writes a page and exits 0; it never
raises.

The page has no JavaScript and no external files. Its CSS is inline, it fits a
phone screen, and every state is a word as well as a colour.

The repository it links to (``owner/name``) comes from ``GITHUB_REPOSITORY``,
which Actions sets. Without it the page still renders, without links. Standard
library only, plus the vendored ``core/`` this vault already carries.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

VAULT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VAULT / "core"))
sys.path.insert(0, str(VAULT / "core" / "schema"))

import evaluate  # noqa: E402  (vendored core/: the one YAML reader and date parser)
from check_examples import validate as schema_validate  # noqa: E402

RECENT_LIMIT = 10

# The word and the colour for each state in standing.schema.json. White text on
# each background is at least 4.5:1 (WCAG AA), so the word is readable on its own.
STATES = {
    "green": ("green", "#1b7a3d", "posted on time"),
    "amber": ("amber", "#8a5300", "due soon and not posted yet"),
    "red": ("red", "#b3261e", "the deadline passed without a timely posting"),
    "no-data": ("no data", "#595959", "nothing to judge yet; this is not a pass"),
}


# --------------------------------------------------------------------------- #
# Reading the artifacts. Each reader returns None when its input is missing or  #
# unusable; the page then says so. None of them raise on bad input.             #
# --------------------------------------------------------------------------- #

_READ_ERRORS = (OSError, UnicodeDecodeError, ValueError)   # JSONDecodeError is a ValueError


def _schema(name):
    return json.loads((VAULT / "core" / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


def _matches(name, instance):
    schema = _schema(name)
    return not schema_validate(schema, instance, schema)


def read_title(vault):
    """The first ``# `` heading of ``index.md``, or a plain default."""
    try:
        text = (Path(vault) / "index.md").read_text(encoding="utf-8")
    except _READ_ERRORS:
        return "Public records"
    for line in text.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return "Public records"


def read_standing(artifacts):
    """The standing rows, or None if the file is missing, unreadable, or off-schema."""
    try:
        rows = json.loads((Path(artifacts) / "standing.json").read_text(encoding="utf-8"))
    except _READ_ERRORS:
        return None
    return rows if _matches("standing", rows) else None


def _when(value):
    """An aware UTC datetime, or None. The core's parser can raise on an
    impossible date such as 2026-99-99; here that is just unreadable."""
    try:
        return evaluate.parse_datetime(value)
    except ValueError:
        return None


def read_receipt(artifacts):
    """``(file name, receipt, publish time)`` for the receipt with the latest
    ``published_at``, or None. Receipts are ``.md`` with frontmatter (or
    ``.json`` from older vaults); either is read with the core's one loader."""
    newest = None
    for path in sorted((Path(artifacts) / "receipts").glob("*")):
        if path.suffix not in (".md", ".json"):
            continue
        try:
            receipt = evaluate._load_structured(path)
        except _READ_ERRORS:
            continue
        when = _when(receipt.get("published_at")) if isinstance(receipt, dict) else None
        if when is not None and (newest is None or when > newest[2]):
            newest = (path.name, receipt, when)
    return newest


def read_corpus(artifacts, receipt):
    """The corpus records, or None. ``corpus-latest.json`` when it is there (the
    published branch); otherwise the ``corpus-<digest>.json`` the newest receipt
    names (the publish job's own output directory)."""
    names = ["corpus-latest.json"]
    if receipt and re.fullmatch(r"[a-f0-9]{64}", str(receipt.get("digest"))):
        names.append(f"corpus-{receipt['digest']}.json")
    for name in names:
        try:
            bundle = json.loads((Path(artifacts) / name).read_text(encoding="utf-8"))
        except _READ_ERRORS:
            continue
        if _matches("bundle", bundle):
            return bundle["records"]
    return None


def read_obligation_titles(vault):
    """``{obligation id: title}`` from ``obligations/``; empty if unreadable."""
    try:
        return {o["id"]: o["title"] for o in evaluate.load_obligations(Path(vault))}
    except (*_READ_ERRORS, KeyError, TypeError):
        return {}


# --------------------------------------------------------------------------- #
# A tiny HTML builder. Text is escaped unless it is already an Html fragment,  #
# so nothing read from a file can inject markup.                               #
# --------------------------------------------------------------------------- #

class Html(str):
    """A fragment that is already safe HTML."""


def esc(value):
    return value if isinstance(value, Html) else Html(html.escape(str(value)))


def el(tag, *children, **attrs):
    """``<tag attrs>children</tag>``. ``class_`` stands in for ``class``."""
    attr_text = "".join(f' {k.rstrip("_").replace("_", "-")}="{html.escape(str(v))}"'
                        for k, v in attrs.items() if v is not None)
    return Html(f"<{tag}{attr_text}>{''.join(esc(c) for c in children)}</{tag}>")


def join(*parts):
    return Html("".join(esc(p) for p in parts))


# --------------------------------------------------------------------------- #
# The page.                                                                    #
# --------------------------------------------------------------------------- #

def github_url(repo, ref, path):
    """``https://github.com/<repo>/blob/<ref>/<path>``, or None without a repo."""
    if not repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return None
    return f"https://github.com/{repo}/blob/{ref}/{quote(path)}"


def state_badge(state):
    word, colour, _ = STATES.get(state, STATES["no-data"])
    return el("span", word, class_="state", style=f"background:{colour}")


def record_link(record_id, corpus_by_id, repo):
    """The satisfying record, linked to its file on ``main`` when it is in the
    corpus and the repository is known; its id as text otherwise."""
    if not record_id:
        return el("span", "none", class_="muted")
    entry = corpus_by_id.get(record_id)
    url = github_url(repo, "main", entry["path"]) if entry else None
    text = entry["title"] if entry else record_id
    return el("a", text, href=url) if url else el("code", record_id)


def board_section(rows, titles, corpus_by_id, repo):
    if rows is None:
        return el("section",
                  el("h2", "Is it posted?"),
                  el("p", state_badge("no-data"), " The board has no data. ",
                     "The obligation standings for this vault could not be read, ",
                     "so nothing here is shown as met.", class_="notice"))
    if not rows:
        return el("section",
                  el("h2", "Is it posted?"),
                  el("p", "No posting obligations are declared in this vault yet.",
                     class_="notice"))
    body = [el("tr",
               el("th", titles.get(r["id"]) or r["id"], scope="row"),
               el("td", state_badge(r["state"])),
               el("td", r["deadline"] or el("span", "none", class_="muted")),
               el("td", record_link(r["satisfied_by"], corpus_by_id, repo)))
            for r in rows]
    legend = [el("li", state_badge(s), " ", meaning) for s, (_, _, meaning) in STATES.items()]
    return el("section",
              el("h2", "Is it posted?"),
              el("div",
                 el("table",
                    el("caption", "One row per posting obligation."),
                    el("thead", el("tr", *(el("th", h, scope="col") for h in
                                           ("Obligation", "State", "Deadline", "Satisfied by")))),
                    el("tbody", *body)),
                 class_="scroll"),
              el("ul", *legend, class_="legend"))


def recent_section(corpus, repo):
    if corpus is None:
        return el("section", el("h2", "Recent records"),
                  el("p", "The list of records could not be read.", class_="notice"))
    if not corpus:
        return el("section", el("h2", "Recent records"),
                  el("p", "Nothing has been published yet.", class_="notice"))
    # Record ids start with their date, so the newest sort last.
    newest = sorted(corpus, key=lambda e: e["doc_id"], reverse=True)[:RECENT_LIMIT]
    items = []
    for e in newest:
        url = github_url(repo, "main", e["path"])
        title = el("a", e["title"], href=url) if url else esc(e["title"])
        items.append(el("li", el("span", e["doc_id"][:10], class_="muted"), " ", title))
    return el("section", el("h2", "Recent records"), el("ul", *items, class_="recent"))


def published_line(receipt, repo):
    if receipt is None:
        return el("p", "Last published: unknown. No publish receipt could be read.")
    name, data, when = receipt
    stamp = data["published_at"]
    words = f"{when.day} {when:%B %Y, %H:%M} UTC"
    line = join("Last published ", el("time", words, datetime=stamp), ".")
    url = github_url(repo, "published", f"receipts/{name}")
    if url:
        line = join(line, " ", el("a", "Read the receipt", href=url), ".")
    return el("p", line)


CSS = """
:root { color-scheme: light; }
body { margin: 0 auto; max-width: 48rem; padding: 1rem; font: 1rem/1.5 system-ui,
  -apple-system, "Segoe UI", sans-serif; color: #1f2328; background: #ffffff; }
h1 { font-size: 1.6rem; margin: 0.5rem 0; }
h2 { font-size: 1.25rem; margin-top: 2rem; }
a { color: #0b57d0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
caption { text-align: left; padding-bottom: 0.5rem; color: #595959; }
th, td { text-align: left; vertical-align: top; padding: 0.5rem; border-bottom: 1px solid #d0d7de; }
thead th { font-size: 0.9rem; }
tbody th { font-weight: 600; }
.state { display: inline-block; padding: 0.1em 0.6em; border-radius: 999px; color: #ffffff;
  font-size: 0.85rem; font-weight: 700; white-space: nowrap; }
.muted { color: #595959; }
.notice { padding: 0.75rem; border: 1px solid #d0d7de; border-radius: 0.5rem; }
.legend, .recent { padding-left: 0; list-style: none; }
.legend li, .recent li { margin: 0.4rem 0; }
footer { margin-top: 2rem; font-size: 0.9rem; color: #595959; }
"""


def render_page(title, rows, titles, corpus, receipt, repo):
    corpus_by_id = {e["doc_id"]: e for e in corpus or []}
    footer = el("footer",
                "Times are the server-side publish times in UTC. The data behind this page: ",
                el("a", "standing.json", href="standing.json"), ", ",
                el("a", "feed.xml", href="feed.xml"), ", ",
                el("a", "corpus-latest.json", href="corpus-latest.json"), ".")
    return ("<!DOCTYPE html>\n" + el(
        "html",
        el("head",
           Html('<meta charset="utf-8">'),
           Html('<meta name="viewport" content="width=device-width, initial-scale=1">'),
           el("title", f"{title}: Is it posted?"),
           el("style", Html(CSS))),
        el("body",
           el("main",
              el("h1", title),
              published_line(receipt, repo),
              board_section(rows, titles, corpus_by_id, repo),
              recent_section(corpus, repo)),
           footer),
        lang="en") + "\n")


def build(vault, artifacts, repo):
    receipt = read_receipt(artifacts)
    return render_page(
        title=read_title(vault),
        rows=read_standing(artifacts),
        titles=read_obligation_titles(vault),
        corpus=read_corpus(artifacts, receipt[1] if receipt else None),
        receipt=receipt,
        repo=repo)


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the vault's public board as index.html.")
    p.add_argument("--vault", default=".", help="vault root, for index.md and obligations/ (default: .)")
    p.add_argument("--artifacts", default="_out",
                   help="directory holding standing.json, the corpus, and receipts/ (default: _out)")
    p.add_argument("--out", help="where to write the page (default: <artifacts>/index.html)")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                   help="owner/name for record links (default: $GITHUB_REPOSITORY)")
    a = p.parse_args(argv)
    out = Path(a.out or Path(a.artifacts) / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(a.vault, a.artifacts, a.repo), encoding="utf-8")
    print(f"board: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
