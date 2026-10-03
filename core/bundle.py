#!/usr/bin/env python3
"""Build the published artifacts of a uvularia vault: the corpus bundle, the
Atom feed, and the publish receipt — then (via ``evaluate.py``) the standing
file. One command, run by the vault template's publish workflow and by the
tests, so the build that CI runs is the build you can run by hand.

    python3 core/bundle.py build \\
        --vault . \\
        --out _publish \\
        --published-at 2026-10-03T14:03:11Z \\
        --run-url https://github.com/you/you-records/actions/runs/123 \\
        --commit 0a1b2c3 \\
        --prior-receipts _published/receipts \\
        --prev-bundle _published/corpus-latest.json

**What it writes** into ``--out`` (all four, every run):

  * ``corpus-<digest>.json``  — the bundle every consumer reads by URL and
    digest (``schema/bundle.schema.json``). One entry per **approved, public**
    record, in mitchella's wiki-loader shape: ``doc_id``, ``title`` (the H1),
    ``tags`` (frontmatter tags plus the parent folder), ``volatility`` (always
    ``stable`` — vault records are facts, not live signals), ``body``, ``path``,
    ``certainty`` (``verified`` stays verified; ``reported`` maps to
    ``inferred``; missing to ``unknown``), and ``archived``.
  * ``standing.json``         — the live board data (``schema/standing.schema.json``),
    produced by ``evaluate.py`` so there is exactly one evaluator.
  * ``feed.xml``              — an Atom feed of ``announcement`` records, newest
    ``publish_at`` first.
  * ``receipts/<YYYY-MM-DDTHHMMSSZ>-<digest>.md`` — the append-only receipt
    (``schema/receipt.schema.json`` in its frontmatter). ``published_at`` is the
    server-side time you pass in, never an author's clock; ``run_url`` is the
    Actions run.

**Server-side time.** ``--published-at`` is the publish workflow's own clock.
The build does not read a commit time or a file mtime for it. (Invariant 4.)

**Never fake green.** Standing comes straight from ``evaluate.py``; a record with
no receipt has no publish time and so can never be reported green. (Invariant 5.)

Every artifact is validated against its schema before it is written; a build that
cannot produce a schema-valid artifact fails loudly rather than shipping it.

Python 3.12, standard library only. Reuses the in-tree schema validator and the
obligation evaluator — it does not re-implement either.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

_CORE_DIR = Path(__file__).resolve().parent
_SCHEMA_DIR = _CORE_DIR / "schema"
sys.path.insert(0, str(_CORE_DIR))
sys.path.insert(0, str(_SCHEMA_DIR))

import evaluate as _evaluate  # noqa: E402  (sibling module in core/)
from check_examples import validate as _schema_validate  # noqa: E402


# --------------------------------------------------------------------------- #
# Loading records with their prose body (the evaluator only needs frontmatter;  #
# the bundle needs the H1 and the text, so we read a little more here).         #
# --------------------------------------------------------------------------- #

def _split_frontmatter(text, path):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: no frontmatter block (a record must open with '---')")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise ValueError(f"{path}: frontmatter block is never closed with '---'")


def _h1(body):
    """The first ``# `` heading in the body, or None."""
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def load_records(vault):
    """Every record under ``records/`` as ``(frontmatter, body, rel_path)``.

    Frontmatter is parsed with the evaluator's YAML reader so there is one parser
    for records across the core. Records whose frontmatter will not parse are
    skipped here; ``validate.py`` is what fails the build on a bad record.
    """
    out = []
    records_dir = vault / "records"
    if not records_dir.is_dir():
        return out
    for path in sorted(records_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            fm_text, body = _split_frontmatter(text, path)
            fm = _evaluate.load_yaml(fm_text)
        except ValueError:
            continue
        if isinstance(fm, dict) and fm.get("id"):
            out.append((fm, body, path.relative_to(vault).as_posix()))
    return out


# --------------------------------------------------------------------------- #
# The corpus bundle (mitchella's wiki-loader entry shape).                      #
# --------------------------------------------------------------------------- #

_CERTAINTY = {"verified": "verified", "reported": "inferred"}


def _entry(fm, body, rel_path):
    # The evaluator's YAML reader is typed (a bare `2026` is an int); bundle tags
    # are strings, so coerce. validate.py reads the same frontmatter as strings.
    tags = [str(t) for t in (fm.get("tags") or [])]
    parent = rel_path.split("/")[-2] if "/" in rel_path else ""
    if parent and parent not in tags:
        tags.append(parent)      # folder is the record type: a free, reliable tag
    return {
        "doc_id": fm["id"],
        "title": _h1(body) or fm.get("title") or fm["id"],
        "tags": tags,
        "volatility": "stable",
        "body": body.strip(),
        "path": rel_path,
        "certainty": _CERTAINTY.get(fm.get("certainty"), "unknown"),
        # The record schema does not (yet) carry an `archived` field, so this is
        # False today; read it defensively so the flag works the day it is added.
        "archived": bool(fm.get("archived", False)),
    }


def build_entries(records):
    """Bundle entries for every approved, public record, sorted by id."""
    entries = []
    for fm, body, rel_path in records:
        if fm.get("status") != "approved" or fm.get("visibility") != "public":
            continue
        entries.append(_entry(fm, body, rel_path))
    entries.sort(key=lambda e: e["doc_id"])
    return entries


def digest_of(entries):
    """SHA-256 over the corpus content, independent of publish time, so the same
    records always produce the same digest."""
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# What changed since the previous publish.                                      #
# --------------------------------------------------------------------------- #

def diff_records(entries, prev_bundle):
    """``{added, changed, retracted}`` of record ids versus the previous bundle.

    ``retracted`` is any id that left the published corpus — a record set to
    ``retracted`` or ``superseded`` drops out of the approved set and lands here.
    """
    prev = {e["doc_id"]: e for e in (prev_bundle or {}).get("records", [])}
    now = {e["doc_id"]: e for e in entries}
    added = sorted(i for i in now if i not in prev)
    retracted = sorted(i for i in prev if i not in now)
    changed = sorted(i for i in now if i in prev and now[i]["body"] != prev[i]["body"])
    return {"added": added, "changed": changed, "retracted": retracted}


# --------------------------------------------------------------------------- #
# The receipt (append-only, human-readable, with the machine surface up top).   #
# --------------------------------------------------------------------------- #

def _fm_scalar(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fm_id_list(ids):
    return "[" + ", ".join(_fm_scalar(i) for i in ids) + "]"


def receipt_markdown(receipt):
    """Serialize a receipt dict to a frontmatter block the evaluator's YAML
    reader round-trips, followed by a short human summary."""
    r = receipt
    s = r["standing"]
    changed = r["records"]
    fm = [
        "---",
        f'digest: {_fm_scalar(r["digest"])}',
        f'published_at: {_fm_scalar(r["published_at"])}',
        f'run_url: {_fm_scalar(r["run_url"])}',
        f'commit: {_fm_scalar(r["commit"])}',
        "records:",
        f'  added: {_fm_id_list(changed["added"])}',
        f'  changed: {_fm_id_list(changed["changed"])}',
        f'  retracted: {_fm_id_list(changed["retracted"])}',
        "standing:",
        f'  green: {s["green"]}',
        f'  amber: {s["amber"]}',
        f'  red: {s["red"]}',
        f'  no_data: {s["no_data"]}',
        "---",
        "",
        f'# Publish receipt — {r["published_at"]}',
        "",
        f'Corpus digest `{r["digest"]}`, published from commit `{r["commit"]}`.',
        f'Produced by the Actions run at {r["run_url"]}.',
        "",
        "| Records | ids |",
        "|---|---|",
        f'| added | {", ".join(changed["added"]) or "—"} |',
        f'| changed | {", ".join(changed["changed"]) or "—"} |',
        f'| retracted | {", ".join(changed["retracted"]) or "—"} |',
        "",
        "| Obligations | count |",
        "|---|---|",
        f'| green | {s["green"]} |',
        f'| amber | {s["amber"]} |',
        f'| red | {s["red"]} |',
        f'| no-data | {s["no_data"]} |',
        "",
        "> Server-side timestamp from the publish workflow, not an author's clock.",
        "> This file is append-only: never edit or delete a receipt.",
        "",
    ]
    return "\n".join(fm)


def receipt_name(published_at, digest):
    """``<YYYY-MM-DDTHHMMSSZ>-<digest>.md`` — one file per publish, never reused.

    The name carries the publish instant, not just the day: two publishes with an
    unchanged corpus share a digest, and a day-plus-digest name would make the
    second overwrite the first and erase the record provenance it carried."""
    stamp = re.sub(r"[^0-9TZ]", "", published_at.replace("+00:00", "Z"))
    if not stamp.endswith("Z"):
        stamp += "Z"
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:15]}Z-{digest}.md"


# --------------------------------------------------------------------------- #
# The Atom feed of announcements.                                               #
# --------------------------------------------------------------------------- #

def _rfc3339(value):
    dt = _evaluate.parse_datetime(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_feed(records, digest, published_at):
    """An Atom feed of ``announcement`` records, newest ``publish_at`` first."""
    anns = []
    for fm, body, rel_path in records:
        if fm.get("type") != "announcement" or fm.get("status") != "approved":
            continue
        if not fm.get("publish_at"):
            continue
        anns.append((fm, body, rel_path))
    anns.sort(key=lambda t: _evaluate.parse_datetime(t[0]["publish_at"]) or datetime.min.replace(tzinfo=timezone.utc),
              reverse=True)

    feed_updated = _rfc3339(anns[0][0]["publish_at"]) if anns else _rfc3339(published_at)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f'  <id>urn:uvularia:corpus:{digest}</id>',
        '  <title>Announcements</title>',
        f'  <updated>{feed_updated}</updated>',
    ]
    for fm, body, _rel in anns:
        title = _h1(body) or fm.get("title") or fm["id"]
        summary = " ".join(body.strip().split())[:500]
        lines += [
            '  <entry>',
            f'    <id>urn:uvularia:record:{escape(fm["id"])}</id>',
            f'    <title>{escape(title)}</title>',
            f'    <updated>{_rfc3339(fm["publish_at"])}</updated>',
            f'    <content type="text">{escape(summary)}</content>',
            '  </entry>',
        ]
    lines.append('</feed>')
    return "\n".join(lines) + "\n"


def _feed_is_valid(xml_text):
    """Well-formed XML with the Atom elements the board and readers rely on.

    Atom has no JSON Schema; this is the feed's schema check — parse it, and
    assert the required feed- and entry-level elements are present."""
    from xml.dom import minidom
    try:
        dom = minidom.parseString(xml_text)
    except Exception as exc:  # noqa: BLE001 - any parse error is a failure
        return [f"feed.xml is not well-formed XML: {exc}"]
    errors = []
    root = dom.documentElement
    if root.tagName != "feed":
        errors.append(f"feed.xml root is <{root.tagName}>, expected <feed>")
    for tag in ("id", "title", "updated"):
        if not root.getElementsByTagName(tag):
            errors.append(f"feed.xml is missing a feed-level <{tag}>")
    for entry in root.getElementsByTagName("entry"):
        for tag in ("id", "title", "updated"):
            if not entry.getElementsByTagName(tag):
                errors.append(f"an <entry> is missing <{tag}>")
    return errors


# --------------------------------------------------------------------------- #
# Orchestration.                                                                #
# --------------------------------------------------------------------------- #

def _schema(name):
    return json.loads((_SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _require_valid(name, instance):
    schema = _schema(name)
    errors = _schema_validate(schema, instance, schema)
    if errors:
        raise SystemExit(f"error: built {name} does not match its schema:\n  - " + "\n  - ".join(errors))


def build(vault, out_dir, published_at, run_url, commit,
          prior_receipts=None, prev_bundle_path=None, warn_days=7):
    vault = Path(vault)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "receipts").mkdir(parents=True, exist_ok=True)

    if _evaluate.parse_datetime(published_at) is None:
        raise SystemExit(f"error: --published-at {published_at!r} is not an ISO 8601 time")

    records = load_records(vault)
    entries = build_entries(records)
    digest = digest_of(entries)

    prev_bundle = None
    if prev_bundle_path and Path(prev_bundle_path).is_file():
        prev_bundle = json.loads(Path(prev_bundle_path).read_text(encoding="utf-8"))
    changed = diff_records(entries, prev_bundle)

    bundle = {"digest": digest, "published_at": published_at, "records": entries}
    _require_valid("bundle", bundle)

    # The receipt for this run must be visible to the evaluator so that records
    # published *now* carry this run's server-side time. Stage prior receipts and
    # this new one into the vault's receipts/ (the ephemeral CI checkout), run the
    # one evaluator, then fill the receipt's standing counts from its result.
    receipts_dir = vault / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    if prior_receipts and Path(prior_receipts).is_dir():
        for src in sorted(Path(prior_receipts).glob("*.md")):
            (receipts_dir / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    receipt = {
        "digest": digest,
        "published_at": published_at,
        "run_url": run_url,
        "commit": commit,
        "records": changed,
        "standing": {"green": 0, "amber": 0, "red": 0, "no_data": 0},
    }
    name = receipt_name(published_at, digest)
    (receipts_dir / name).write_text(receipt_markdown(receipt), encoding="utf-8")

    now = _evaluate.parse_datetime(published_at)
    rows = _evaluate.evaluate(vault, now=now, warn_days=warn_days)
    _require_valid("standing", rows)

    counts = {"green": 0, "amber": 0, "red": 0, "no_data": 0}
    for row in rows:
        key = "no_data" if row["state"] == "no-data" else row["state"]
        counts[key] += 1
    receipt["standing"] = counts
    _require_valid("receipt", receipt)

    receipt_md = receipt_markdown(receipt)
    (receipts_dir / name).write_text(receipt_md, encoding="utf-8")

    feed_xml = build_feed(records, digest, published_at)
    feed_errors = _feed_is_valid(feed_xml)
    if feed_errors:
        raise SystemExit("error: built feed.xml is not valid:\n  - " + "\n  - ".join(feed_errors))

    # Write the artifacts a publish appends to the published branch.
    corpus_name = f"corpus-{digest}.json"
    (out_dir / corpus_name).write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "standing.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "feed.xml").write_text(feed_xml, encoding="utf-8")
    (out_dir / "receipts" / name).write_text(receipt_md, encoding="utf-8")

    return {
        "digest": digest,
        "corpus": corpus_name,
        "standing": "standing.json",
        "feed": "feed.xml",
        "receipt": f"receipts/{name}",
        "counts": counts,
        "records": changed,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a uvularia vault's published artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build corpus, standing, feed, and receipt")
    b.add_argument("--vault", default=".", help="vault root (default: current directory)")
    b.add_argument("--out", required=True, help="directory to write the artifacts into")
    b.add_argument("--published-at", required=True, help="server-side publish time (ISO 8601, from the workflow)")
    b.add_argument("--run-url", required=True, help="HTTPS URL of the Actions run")
    b.add_argument("--commit", required=True, help="the commit SHA being published")
    b.add_argument("--prior-receipts", help="directory of earlier receipts (the published branch's receipts/)")
    b.add_argument("--prev-bundle", help="the previous corpus-<digest>.json, for the changed/retracted diff")
    b.add_argument("--warn-days", type=int, default=7, help="amber window passed to evaluate.py (default: 7)")
    args = parser.parse_args(argv)

    result = build(
        vault=args.vault, out_dir=args.out, published_at=args.published_at,
        run_url=args.run_url, commit=args.commit,
        prior_receipts=args.prior_receipts, prev_bundle_path=args.prev_bundle,
        warn_days=args.warn_days)

    print(f"digest     {result['digest']}")
    print(f"corpus     {result['corpus']}")
    print(f"standing   {result['standing']}  "
          f"(green {result['counts']['green']}, amber {result['counts']['amber']}, "
          f"red {result['counts']['red']}, no-data {result['counts']['no_data']})")
    print(f"feed       {result['feed']}")
    print(f"receipt    {result['receipt']}")
    added, chg, ret = result["records"]["added"], result["records"]["changed"], result["records"]["retracted"]
    print(f"records    added {len(added)}, changed {len(chg)}, retracted {len(ret)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
