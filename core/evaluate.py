#!/usr/bin/env python3
"""Evaluate a vault's obligations against its records and publish receipts, and
emit ``standing.json`` — one row per obligation with a colour (green / amber /
red / no-data), the record that satisfies it, the deadline, the server-side
publish time, and the signed day-gap to the deadline.

The rules this reads live in ``obligations/`` (``obligation.schema.json``); the
publish times live in ``receipts/`` (``receipt.schema.json``); the records live
under ``records/`` as Markdown with YAML frontmatter (``record.schema.json``).
The output validates against ``schema/standing.schema.json``.

Two invariants are load-bearing here and are NOT weakened to make anything pass:

  * **Server-side time only.** A record's publish time is the ``published_at`` of
    the receipt that published it — never a file mtime, never a commit clock. A
    record with no receipt has no publish time, so it can never be reported as a
    satisfied (green) obligation.
  * **No fake green.** Missing data is ``no-data``, never green. ``no-data`` means
    "we have nothing to show here", which is not the same as "this passes".

Stdlib only; no ``pip install``, ever. A small YAML reader (flat mappings, nested
mappings, block and flow sequences) is carried here so clients need no PyYAML;
JSON inputs are accepted too. Python 3.12 in CI, but kept version-agnostic.

Run it:

    python3 core/evaluate.py <vault-root>            # prints standing.json
    python3 core/evaluate.py <vault-root> -o out.json
    python3 core/evaluate.py <vault-root> --now 2026-10-03T12:00:00Z --warn-days 7

``--now`` fixes the evaluation instant (used by the tests; defaults to now, UTC).
``--warn-days`` is the amber window: an unmet obligation whose deadline falls
within this many days is amber rather than no-data or red.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = CORE_DIR / "schema"

# Reuse the schema validator the core already ships — do not re-invent it. It is
# optional: if it cannot be imported the evaluator still runs; it only powers the
# input sanity check and the output self-check.
try:
    sys.path.insert(0, str(SCHEMA_DIR))
    from check_examples import validate as _schema_validate  # type: ignore
except Exception:  # pragma: no cover - defensive; the validator is in-tree
    _schema_validate = None


# --------------------------------------------------------------------------- #
# A tiny YAML reader — mappings, nested mappings, block/flow sequences.        #
# --------------------------------------------------------------------------- #
# Enough for obligation files and record frontmatter; no anchors, no multi-line
# scalars, no flow mappings. JSON is accepted directly elsewhere.

def _strip_comment(raw):
    """Drop a ``#`` comment that is not inside a quote. Leaves indentation."""
    out = []
    quote = None
    for i, ch in enumerate(raw):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or raw[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_scalar(text):
    text = text.strip()
    if text == "" or text in ("null", "~", "Null", "NULL"):
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if inner == "":
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _split_flow(inner):
    """Split a flow-sequence body on top-level commas, respecting quotes."""
    parts, buf, quote = [], [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p for p in parts]


def _looks_like_key(text):
    head = text.split(":", 1)[0]
    return re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", head.strip()) is not None


def _parse_block(lines, i, indent):
    """Parse one container (mapping or sequence) at column >= ``indent``.

    ``lines`` is a list of ``(indent, text)`` with blank/comment lines removed.
    Returns ``(value, next_index)``.
    """
    if i >= len(lines):
        return None, i
    cur = lines[i][0]

    if lines[i][1].startswith("- "):
        seq = []
        while i < len(lines) and lines[i][0] == cur and lines[i][1].startswith("- "):
            item = lines[i][1][2:].strip()
            if item == "":
                val, i = _parse_block(lines, i + 1, cur + 1)
                seq.append(val)
            elif ":" in item and _looks_like_key(item):
                # A mapping item: its first key is inline after "- ", any further
                # keys are indented past this marker. Re-home them one block deep.
                sub = [(cur + 2, item)]
                j = i + 1
                while j < len(lines) and lines[j][0] > cur:
                    sub.append(lines[j])
                    j += 1
                val, _ = _parse_block(sub, 0, cur + 2)
                seq.append(val)
                i = j
            else:
                seq.append(_parse_scalar(item))
                i += 1
        return seq, i

    mapping = {}
    while i < len(lines) and lines[i][0] == cur and not lines[i][1].startswith("- "):
        key, _, rest = lines[i][1].partition(":")
        key = _parse_scalar(key.strip())
        rest = rest.strip()
        if rest == "":
            # Value is the nested block (mapping or sequence) that follows.
            if i + 1 < len(lines) and lines[i + 1][0] > cur:
                val, i = _parse_block(lines, i + 1, cur + 1)
            else:
                val, i = None, i + 1
            mapping[key] = val
        else:
            mapping[key] = _parse_scalar(rest)
            i += 1
    return mapping, i


def load_yaml(text):
    """Parse the YAML subset used by obligations and record frontmatter."""
    lines = []
    for raw in text.splitlines():
        content = _strip_comment(raw)
        if content.strip() == "":
            continue
        if content.lstrip().startswith("---"):
            continue  # a stray document marker inside frontmatter
        indent = len(content) - len(content.lstrip(" "))
        lines.append((indent, content.strip()))
    if not lines:
        return None
    value, _ = _parse_block(lines, 0, 0)
    return value


# --------------------------------------------------------------------------- #
# Loading the vault.                                                          #
# --------------------------------------------------------------------------- #

def _load_structured(path):
    """Load a .json / .yaml / .yml / .md file into a Python object."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    if path.suffix == ".md":
        return _frontmatter(text, path)
    return load_yaml(text)


def _frontmatter(text, path):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: no frontmatter block (expected a leading '---')")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return load_yaml("\n".join(lines[1:idx]))
    raise ValueError(f"{path}: frontmatter block is never closed")


def _iter_files(directory, suffixes):
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in suffixes:
            yield path


def load_obligations(root):
    """Load every obligation under ``obligations/``. A file may hold one mapping
    or a list of mappings. Invalid obligations are skipped with a warning so a
    single bad rule never blanks the whole board."""
    schema = _schema("obligation") if _schema_validate else None
    obligations = []
    for path in _iter_files(root / "obligations", {".yaml", ".yml", ".json"}):
        data = _load_structured(path)
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if schema is not None:
                errors = _schema_validate(schema, item, schema)
                if errors:
                    _warn(f"skipping invalid obligation in {path.name}: {errors[0]}")
                    continue
            obligations.append(item)
    return obligations


def load_records(root):
    records = []
    for path in _iter_files(root / "records", {".md", ".json", ".yaml", ".yml"}):
        data = _load_structured(path)
        if isinstance(data, dict) and data.get("id"):
            records.append(data)
    return records


def load_receipts(root):
    """Load every receipt under ``receipts/``. A receipt is either a ``.json``
    object or a ``.md`` file whose frontmatter is that object (the human-readable
    form the publish step writes). Either way only ``published_at`` and
    ``records`` are read here, to date each record's going-public."""
    receipts = []
    for path in _iter_files(root / "receipts", {".json", ".md"}):
        try:
            data = _load_structured(path)
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("published_at"):
            receipts.append(data)
    return receipts


def publish_index(receipts):
    """Map record id -> (earliest publish datetime, its original string).

    "Earliest" because the first receipt that carries a record is when it went
    public; that is the time the lead/lag/one-off rules are measured against.
    """
    index = {}
    for receipt in receipts:
        raw = receipt.get("published_at")
        when = parse_datetime(raw)
        if when is None:
            continue
        group = receipt.get("records") or {}
        for key in ("added", "changed"):
            for record_id in group.get(key, []) or []:
                prev = index.get(record_id)
                if prev is None or when < prev[0]:
                    index[record_id] = (when, raw)
    return index


# --------------------------------------------------------------------------- #
# Time helpers.                                                               #
# --------------------------------------------------------------------------- #

def parse_datetime(value):
    """Parse an ISO date or datetime to an aware UTC datetime. Naive and
    date-only values are read as midnight UTC. Returns ``None`` on junk."""
    if not isinstance(value, str) or value.strip() == "":
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_date(value):
    if not isinstance(value, str):
        return None
    match = re.match(r"\d{4}-\d{2}-\d{2}", value.strip())
    return date.fromisoformat(match.group(0)) if match else None


def midnight_utc(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def add_months(day, months):
    """``day`` plus ``months`` calendar months, clamping the day of month."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    last = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(day.day, last))


def _day_gap(reference, deadline):
    return (reference - deadline).days


# --------------------------------------------------------------------------- #
# Matching records to an obligation.                                         #
# --------------------------------------------------------------------------- #

def _subjects_match(obligation, record):
    wanted = obligation.get("subjects") or []
    if not wanted:
        return True
    return bool(set(wanted) & set(record.get("subjects") or []))


def _candidates(obligation, records, statuses):
    out = []
    for record in records:
        if record.get("type") != obligation.get("record_type"):
            continue
        if record.get("status") not in statuses:
            continue
        if not _subjects_match(obligation, record):
            continue
        if parse_date(record.get("effective")) is None:
            continue
        out.append(record)
    return out


def _latest(records):
    """The record for the most recent event — latest ``effective``, id breaking
    ties. That is the instance the board reports the current standing of."""
    return max(records, key=lambda r: (parse_date(r["effective"]), r["id"]))


# --------------------------------------------------------------------------- #
# The four states.                                                           #
# --------------------------------------------------------------------------- #

def _row(obligation_id, state, satisfied_by, deadline, published_at, gap):
    return {
        "id": obligation_id,
        "state": state,
        "satisfied_by": satisfied_by,
        "deadline": deadline.isoformat() if isinstance(deadline, date) else deadline,
        "published_at": published_at,
        "gap": gap,
    }


def _no_data(obligation_id, deadline=None):
    return _row(obligation_id, "no-data", None, deadline, None, None)


def _forward(deadline_date, now, warn_days):
    """State for an obligation that is NOT yet satisfied, judged against a known
    future deadline. Nothing is published, so there is no server-side time to
    show — only a nudge. Returns ``(state, gap)``; gap is days of now vs deadline
    (negative = still ahead), or ``None`` when there is nothing to report yet."""
    today = now.date()
    gap = _day_gap(today, deadline_date)
    if today > deadline_date:
        return "red", gap            # the deadline passed and nothing was posted
    if (deadline_date - today).days <= warn_days:
        return "amber", gap          # due within the warning window; post it
    return "no-data", None           # not due yet and nothing posted — nothing to show


def _eval_lead(obligation, records, pubs, now, warn_days):
    spec = obligation["lead"]
    cands = _candidates(obligation, records, {"approved", "superseded"})
    if not cands:
        return _no_data(obligation["id"])
    record = _latest(cands)
    event = midnight_utc(parse_date(record["effective"]))
    offset = timedelta(hours=spec["hours"]) if "hours" in spec else timedelta(days=spec["days"])
    deadline_instant = event - offset
    deadline_date = deadline_instant.date()

    published = pubs.get(record["id"])
    if published is None:
        # Present but unpublished: no server-side time, so never green.
        state, gap = _forward(deadline_date, now, warn_days)
        return _row(obligation["id"], state, None, deadline_date, None, gap)
    when, raw = published
    gap = _day_gap(when.date(), deadline_date)
    if when <= deadline_instant:
        return _row(obligation["id"], "green", record["id"], deadline_date, raw, gap)
    return _row(obligation["id"], "red", None, deadline_date, raw, gap)  # late notice


def _eval_lag(obligation, records, pubs, now, warn_days):
    spec = obligation["lag"]
    cands = _candidates(obligation, records, {"approved", "superseded"})
    if not cands:
        return _no_data(obligation["id"])
    record = _latest(cands)
    deadline_date = parse_date(record["effective"]) + timedelta(days=spec["days"])

    published = pubs.get(record["id"])
    if published is None:
        state, gap = _forward(deadline_date, now, warn_days)
        return _row(obligation["id"], state, None, deadline_date, None, gap)
    when, raw = published
    gap = _day_gap(when.date(), deadline_date)
    if when.date() <= deadline_date:
        return _row(obligation["id"], "green", record["id"], deadline_date, raw, gap)
    return _row(obligation["id"], "red", None, deadline_date, raw, gap)  # late minutes


def _eval_cadence(obligation, records, pubs, now, warn_days):
    spec = obligation["cadence"]
    # Only an approved record that actually published can set the clock; a record
    # with no receipt has no server-side baseline, so it cannot establish cadence.
    cands = [r for r in _candidates(obligation, records, {"approved", "superseded"})
             if r["id"] in pubs]
    if not cands:
        return _no_data(obligation["id"])
    record = _latest(cands)
    when, raw = pubs[record["id"]]
    deadline_date = add_months(parse_date(record["effective"]), spec["months"])
    today = now.date()
    gap = _day_gap(today, deadline_date)
    if today > deadline_date:
        return _row(obligation["id"], "red", None, deadline_date, raw, gap)  # lapsed
    if (deadline_date - today).days <= warn_days:
        return _row(obligation["id"], "amber", record["id"], deadline_date, raw, gap)
    return _row(obligation["id"], "green", record["id"], deadline_date, raw, gap)


def _eval_one_off(obligation, records, pubs, now, warn_days):
    due = parse_date(obligation["one_off"]["due"])
    cands = _candidates(obligation, records, {"approved", "superseded"})
    published = [(pubs[r["id"]][0], pubs[r["id"]][1], r) for r in cands if r["id"] in pubs]
    if published:
        when, raw, record = min(published, key=lambda t: t[0])
        gap = _day_gap(when.date(), due)
        if when.date() <= due:
            return _row(obligation["id"], "green", record["id"], due, raw, gap)
        return _row(obligation["id"], "red", None, due, raw, gap)  # filed late
    state, gap = _forward(due, now, warn_days)
    return _row(obligation["id"], state, None, due, None, gap)


_EVALUATORS = (
    ("lead", _eval_lead),
    ("lag", _eval_lag),
    ("cadence", _eval_cadence),
    ("one_off", _eval_one_off),
)


def evaluate_obligation(obligation, records, pubs, now, warn_days):
    for key, fn in _EVALUATORS:
        if key in obligation:
            return fn(obligation, records, pubs, now, warn_days)
    # No timing kind: the schema forbids this, but never crash the board over it.
    return _no_data(obligation.get("id", "unknown"))


def evaluate(root, now=None, warn_days=7):
    """Evaluate the vault at ``root`` and return the standing rows, sorted by id."""
    root = Path(root)
    if now is None:
        now = datetime.now(timezone.utc)
    obligations = load_obligations(root)
    records = load_records(root)
    pubs = publish_index(load_receipts(root))
    rows = [evaluate_obligation(o, records, pubs, now, warn_days) for o in obligations]
    rows.sort(key=lambda r: r["id"])
    return rows


# --------------------------------------------------------------------------- #
# CLI.                                                                        #
# --------------------------------------------------------------------------- #

def _schema(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _warn(message):
    print(f"warning: {message}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate vault obligations into standing.json.")
    parser.add_argument("root", help="vault root (holds obligations/, records/, receipts/)")
    parser.add_argument("-o", "--out", help="write standing.json here (default: stdout)")
    parser.add_argument("--now", help="evaluation instant, ISO 8601 (default: now, UTC)")
    parser.add_argument("--warn-days", type=int, default=7,
                        help="amber window in days for an unmet, upcoming deadline (default: 7)")
    args = parser.parse_args(argv)

    now = None
    if args.now:
        now = parse_datetime(args.now)
        if now is None:
            parser.error(f"could not parse --now value {args.now!r}")

    rows = evaluate(args.root, now=now, warn_days=args.warn_days)

    if _schema_validate is not None:
        errors = _schema_validate(_schema("standing"), rows, _schema("standing"))
        if errors:  # a bug in us, not in the vault — fail loudly rather than ship it
            for err in errors:
                _warn(f"standing output failed its own schema: {err}")
            return 1

    text = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
