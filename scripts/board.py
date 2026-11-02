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
  * "evaluated", the ``evaluated_at`` on the standing rows: when the board was
    last judged, by a publish or by the scheduled re-evaluate workflow;
  * one row per obligation in ``standing.json``: its title, with the rule's
    ``scope_note`` under it when it has one (what the rule does not assert, so a
    green row is not read as legal standing), its state as a word on a colour,
    its deadline, and the record that satisfies it, linked to the record file on
    GitHub;
  * the most recent records in the corpus.

**Never a fake green.** A missing or unreadable ``standing.json``, or one that
does not match ``core/schema/standing.schema.json``, gives a page that says in
words that the board has no data, with no rows. A ``no-data`` row says "no
data". On any bad input the script still writes a page and exits 0; it never
raises.

**Never a stale green.** A row is true only as of when it was evaluated, and
each row says until when it stays current (``valid_until``: the next UTC midnight
if it would change then, otherwise a day after it was evaluated). A row past that
instant when the page is written shows as "unknown", with the colour it last had
in brackets. A page can also go stale after it is written, if the schedule stops,
so the page carries one small inline script that makes the same check against
the reader's clock, row by row. The script only ever hides colours; it never
shows one the page did not already show. Without JavaScript the page still reads
correctly and says when it was evaluated.

The page has no external files. Its CSS and its one script are inline, it fits a
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
from datetime import datetime, timedelta, timezone
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

# Not a standing state: what a row shows once it is past its valid_until (or,
# for an older core, a day after the board was evaluated). It hides the last
# colour rather than vouch for it.
UNKNOWN = ("unknown", "#595959", "may have changed since the board was last re-checked; this is not a pass")
FRESH = timedelta(hours=evaluate.FRESH_HOURS)


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


def read_bundle(artifacts, receipt):
    """The corpus bundle, or None. ``corpus-latest.json`` when it is there (the
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
            return bundle
    return None


def read_corpus(artifacts, receipt):
    """The corpus records, or None (see ``read_bundle``)."""
    bundle = read_bundle(artifacts, receipt)
    return bundle["records"] if bundle else None


def evaluated_instant(rows, bundle, receipt):
    """When the board was last evaluated, or None if nothing says.

    The rows' own ``evaluated_at`` (the oldest, if they ever differ). A vault
    whose core predates the field evaluated only when it published, so its rows
    are dated by the bundle's ``published_at``, then by the newest receipt's."""
    if rows:
        stamps = [_when(r.get("evaluated_at")) for r in rows]
        if all(stamps):
            return min(stamps)
    if bundle:
        when = _when(bundle.get("published_at"))
        if when:
            return when
    return receipt[2] if receipt else None


def row_lapse(row, evaluated):
    """When ``row`` stops being current, or None if nothing says.

    Its own ``valid_until``; for a row from a core that predates the field, a day
    after the board was evaluated (``evaluated_instant``)."""
    lapse = _when(row.get("valid_until"))
    if lapse:
        return lapse
    return evaluated + FRESH if evaluated else None


def is_stale(lapse, now):
    """True when the row's lapse has passed, or nobody knows when it lapses."""
    return lapse is None or now > lapse


def read_obligation_titles(vault):
    """``{obligation id: title}`` from ``obligations/``; empty if unreadable."""
    try:
        return {o["id"]: o["title"] for o in evaluate.load_obligations(Path(vault))}
    except (*_READ_ERRORS, KeyError, TypeError):
        return {}


def read_scope_notes(vault):
    """``{obligation id: scope_note}`` for the rules that carry one; empty if
    unreadable. A missing note is simply not shown."""
    try:
        return {o["id"]: o["scope_note"] for o in evaluate.load_obligations(Path(vault))
                if isinstance(o.get("scope_note"), str) and o["scope_note"].strip()}
    except (*_READ_ERRORS, KeyError, TypeError, AttributeError):
        return {}


def obligation_cell(oid, titles, notes):
    """The row heading: the rule's title, and its scope note under it."""
    note = notes.get(oid)
    title = titles.get(oid) or oid
    if not note:
        return el("th", title, scope="row")
    return el("th", title, el("span", note, class_="scope muted"), scope="row")


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
    word, colour, _ = UNKNOWN if state == "unknown" else STATES.get(state, STATES["no-data"])
    return el("span", word, class_="state", style=f"background:{colour}")


def _words(when):
    return f"{when.day} {when:%B %Y, %H:%M} UTC"


def state_cell(state, lapse, now):
    """The row's state, and the "unknown" it turns into once the row lapses.

    Both are in the page; ``hidden`` picks one. A row already lapsed when the page
    is written shows only "unknown". A current row shows its colour, and the
    inline script swaps it for "unknown" if the reader opens the page after
    ``data-valid-until``."""
    stale = is_stale(lapse, now)
    word = STATES.get(state, STATES["no-data"])[0]
    live = el("span", state_badge(state), class_="live", hidden="hidden" if stale else None)
    gone = el("span", state_badge("unknown"), " ", el("span", f"(was {word})", class_="muted"),
              class_="stale", hidden=None if stale else "hidden")
    return el("td", live, gone, class_="row-state",
              data_valid_until=evaluate.format_instant(lapse) if lapse else "")


def record_link(record_id, corpus_by_id, repo):
    """The satisfying record, linked to its file on ``main`` when it is in the
    corpus and the repository is known; its id as text otherwise."""
    if not record_id:
        return el("span", "none", class_="muted")
    entry = corpus_by_id.get(record_id)
    url = github_url(repo, "main", entry["path"]) if entry else None
    text = entry["title"] if entry else record_id
    return el("a", text, href=url) if url else el("code", record_id)


def board_section(rows, titles, corpus_by_id, repo, evaluated=None, now=None, notes=None):
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
    now = now or datetime.now(timezone.utc)
    lapses = [row_lapse(r, evaluated) for r in rows]
    body = [el("tr",
               obligation_cell(r["id"], titles, notes or {}),
               state_cell(r["state"], lapse, now),
               el("td", r["deadline"] or el("span", "none", class_="muted")),
               el("td", record_link(r["satisfied_by"], corpus_by_id, repo)))
            for r, lapse in zip(rows, lapses)]
    legend = [el("li", state_badge(s), " ", meaning) for s, (_, _, meaning) in STATES.items()]
    legend.append(el("li", state_badge("unknown"), " ", UNKNOWN[2]))
    since = _words(evaluated) if evaluated else "an unknown time"
    any_stale = any(is_stale(lapse, now) for lapse in lapses)
    warning = el("p", state_badge("unknown"),
                 " A row marked unknown may have changed since the board was last "
                 f"re-checked, at {since}. The colour in brackets is what it showed then. "
                 "Nothing marked unknown is shown as met.",
                 class_="notice stale", hidden=None if any_stale else "hidden")
    return el("section",
              el("h2", "Is it posted?"),
              warning,
              el("div",
                 el("table",
                    el("caption", "One row per posting obligation."),
                    el("thead", el("tr", *(el("th", h, scope="col") for h in
                                           ("Obligation", "State", "Deadline", "Satisfied by")))),
                    el("tbody", *body)),
                 class_="scroll"),
              el("ul", *legend, class_="legend"),
              id="board")


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
    line = join("Last published ", el("time", _words(when), datetime=stamp), ".")
    url = github_url(repo, "published", f"receipts/{name}")
    if url:
        line = join(line, " ", el("a", "Read the receipt", href=url), ".")
    return el("p", line)


def evaluated_line(evaluated):
    if evaluated is None:
        return el("p", "Evaluated: unknown. Nothing says when this board was last checked.")
    stamp = evaluate.format_instant(evaluated)
    return el("p", "Evaluated ", el("time", _words(evaluated), datetime=stamp),
              ". Each row is true as of then. The board is re-checked four times a day, "
              "and on every publish.")


# Runs once, on load. Each row past its data-valid-until by the reader's clock (or
# with no readable instant) has its colour hidden and "unknown" shown, and the
# notice appears. It never does the reverse.
SCRIPT = """
(function () {
  var board = document.getElementById("board");
  if (!board) return;
  var cells = board.querySelectorAll("[data-valid-until]"), lapsed = false;
  for (var i = 0; i < cells.length; i++) {
    var until = Date.parse(cells[i].getAttribute("data-valid-until") || "");
    if (!isNaN(until) && Date.now() <= until) continue;
    lapsed = true;
    cells[i].querySelector(".live").hidden = true;
    cells[i].querySelector(".stale").hidden = false;
  }
  if (lapsed) board.querySelector(".notice.stale").hidden = false;
})();
"""


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
.scope { display: block; font-weight: 400; font-size: 0.85rem; margin-top: 0.2rem; }
.state { display: inline-block; padding: 0.1em 0.6em; border-radius: 999px; color: #ffffff;
  font-size: 0.85rem; font-weight: 700; white-space: nowrap; }
.muted { color: #595959; }
.notice { padding: 0.75rem; border: 1px solid #d0d7de; border-radius: 0.5rem; }
.legend, .recent { padding-left: 0; list-style: none; }
[hidden] { display: none !important; }
.legend li, .recent li { margin: 0.4rem 0; }
footer { margin-top: 2rem; font-size: 0.9rem; color: #595959; }
"""


def render_page(title, rows, titles, corpus, receipt, repo, evaluated=None, now=None, notes=None):
    corpus_by_id = {e["doc_id"]: e for e in corpus or []}
    footer = el("footer",
                "Times are server-side times in UTC. The data behind this page: ",
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
              evaluated_line(evaluated),
              board_section(rows, titles, corpus_by_id, repo, evaluated, now, notes),
              recent_section(corpus, repo)),
           footer,
           el("script", Html(SCRIPT))),
        lang="en") + "\n")


def build(vault, artifacts, repo, now=None):
    receipt = read_receipt(artifacts)
    bundle = read_bundle(artifacts, receipt[1] if receipt else None)
    rows = read_standing(artifacts)
    return render_page(
        title=read_title(vault),
        rows=rows,
        titles=read_obligation_titles(vault),
        notes=read_scope_notes(vault),
        corpus=bundle["records"] if bundle else None,
        receipt=receipt,
        repo=repo,
        evaluated=evaluated_instant(rows, bundle, receipt),
        now=now)


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the vault's public board as index.html.")
    p.add_argument("--vault", default=".", help="vault root, for index.md and obligations/ (default: .)")
    p.add_argument("--artifacts", default="_out",
                   help="directory holding standing.json, the corpus, and receipts/ (default: _out)")
    p.add_argument("--out", help="where to write the page (default: <artifacts>/index.html)")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                   help="owner/name for record links (default: $GITHUB_REPOSITORY)")
    p.add_argument("--now", help="judge freshness as of this ISO time (default: now, UTC)")
    a = p.parse_args(argv)
    now = _when(a.now) if a.now else None
    if a.now and now is None:
        p.error(f"could not parse --now value {a.now!r}")
    out = Path(a.out or Path(a.artifacts) / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(a.vault, a.artifacts, a.repo, now=now), encoding="utf-8")
    print(f"board: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
