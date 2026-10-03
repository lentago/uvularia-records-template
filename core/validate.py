#!/usr/bin/env python3
"""Validate a uvularia vault before it is published. Standard library only.

Point it at a vault root (the layout in CLAUDE.md § Artifacts: ``intake/``,
``records/<type>/``, ``library/files/`` + ``library/manifest.json``,
``obligations/``, ``receipts/``, ``index.md``) and it runs the checks that stand
between a mistake and the public site or the Ask box. On any problem it prints a
plain-English line naming the file and what to change, and exits non-zero.

    python3 core/validate.py path/to/vault
    python3 core/validate.py .                 # the vault is the current dir

The eight checks, each independently switchable from ``validate.toml`` at the
vault root (see ``[checks]`` below). Every one defaults to on:

  1. schema            every records/**/*.md has frontmatter that validates
                       against core/schema/record.schema.json.
  2. approved_source   a `status: approved` record has `source` and `approved`,
                       its `source.file` exists under library/, and its sha256
                       matches both the frontmatter and library/manifest.json.
  3. visibility        every record is `visibility: public` (the vault is public).
  4. links             relative Markdown links in records/ and index.md resolve.
  5. privacy           a configurable denylist (names, unit numbers) plus email
                       and phone patterns, scanned in record bodies and any text
                       under library/text/. A match fails the build and prints
                       the file and line — never the matched value in full.
  6. no_delete         no records/**/*.md that exists in the base git ref
                       (default origin/main) has been removed from the tree:
                       retract or supersede instead, never delete.
  7. intake_isolation  nothing in records/ or index.md links into intake/.
  8. unique_obligation_ids  obligation ids are unique across all files under
                       obligations/ (JSON and YAML); the message names both
                       conflicting files so the duplicate is easy to find.

``validate.toml`` (all optional; omit the file to accept every default):

    [checks]
    schema = true
    approved_source = true
    visibility = true
    links = true
    privacy = true
    no_delete = true
    intake_isolation = true
    unique_obligation_ids = true

    [privacy]
    denylist = ["Jane Q. Resident", "Unit 4B"]   # literal strings, case-insensitive
    denylist_file = "privacy-denylist.txt"        # optional: one entry per line.
                                                  # Keep sensitive names here and
                                                  # git-ignore it, so they are not
                                                  # committed to a public repo.
    emails = true                                 # flag email-address patterns
    phones = true                                 # flag phone-number patterns

    [no_delete]
    base_ref = "origin/main"                      # the ref to compare against

Python 3.12, standard library only. It reuses the JSON Schema subset validator
from core/schema/check_examples.py; no third-party package is ever installed.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# The schema and the schema-subset validator both live next to this file.
_CORE_DIR = Path(__file__).resolve().parent
_SCHEMA_DIR = _CORE_DIR / "schema"
sys.path.insert(0, str(_SCHEMA_DIR))
from check_examples import validate as _schema_validate  # noqa: E402


# --------------------------------------------------------------------------- #
# A tiny YAML reader — only the subset record frontmatter uses.               #
# --------------------------------------------------------------------------- #
# Record frontmatter is minimal YAML: block mappings, block sequences, flow
# mappings `{k: v}`, flow sequences `[a, b]`, and quoted or bare scalars. There
# are no numbers or booleans in the record schema, so every scalar is read as a
# string; the schema then checks each against its pattern or enum. Anything the
# subset does not cover is rejected with a line number rather than guessed at.

class YamlError(Exception):
    def __init__(self, line, message):
        super().__init__(message)
        self.line = line
        self.message = message


def _indent(s):
    return len(s) - len(s.lstrip(" "))


_MAP_ENTRY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*\s*:(\s|$)")


def parse_yaml(text):
    """Parse the frontmatter subset into dicts, lists, and strings.

    Line numbers in any raised YamlError are 1-based within ``text``.
    """
    lines = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: _indent(raw)]:
            raise YamlError(lineno, "tab in indentation; use spaces")
        lines.append((lineno, raw))
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, 0)
    if idx != len(lines):
        raise YamlError(lines[idx][0], f"unexpected line: {lines[idx][1].strip()!r}")
    return value


def _parse_block(lines, idx, min_indent):
    if idx >= len(lines):
        return None, idx
    _, raw = lines[idx]
    if _indent(raw) < min_indent:
        return None, idx
    if raw.lstrip().startswith("- "):
        return _parse_seq(lines, idx)
    return _parse_map(lines, idx)


def _parse_map(lines, idx):
    indent = _indent(lines[idx][1])
    mapping = {}
    while idx < len(lines):
        lineno, raw = lines[idx]
        cur = _indent(raw)
        if cur < indent:
            break
        if cur > indent:
            raise YamlError(lineno, "unexpected indentation")
        rest = raw[cur:]
        if rest.startswith("- "):
            break
        key, sep, after = rest.partition(":")
        if not sep:
            raise YamlError(lineno, f"expected 'key: value', got {rest.strip()!r}")
        key = _unquote(key.strip(), lineno)
        after = after.strip()
        if after == "" or after.startswith("#"):
            sub, idx = _parse_block(lines, idx + 1, cur + 1)
            mapping[key] = sub
        else:
            mapping[key] = _parse_scalar(after, lineno)
            idx += 1
    return mapping, idx


def _parse_seq(lines, idx):
    indent = _indent(lines[idx][1])
    items = []
    while idx < len(lines):
        lineno, raw = lines[idx]
        cur = _indent(raw)
        if cur < indent:
            break
        if cur > indent:
            raise YamlError(lineno, "unexpected indentation in list")
        rest = raw[cur:]
        if not rest.startswith("- "):
            break
        content = rest[2:].strip()
        if content == "":
            sub, idx = _parse_block(lines, idx + 1, cur + 1)
            items.append(sub)
        elif content[0] not in "{[\"'" and _MAP_ENTRY.match(content):
            # A block mapping that begins on the dash line. Gather the entry and
            # any lines indented past the dash, dedent them, and parse as a map.
            sub_lines = [(lineno, content)]
            dedent = cur + 2
            idx += 1
            while idx < len(lines) and _indent(lines[idx][1]) > cur:
                ln, r = lines[idx]
                sub_lines.append((ln, r[dedent:] if len(r) >= dedent else r.lstrip()))
                idx += 1
            value, _ = _parse_map(sub_lines, 0)
            items.append(value)
        else:
            items.append(_parse_scalar(content, lineno))
            idx += 1
    return items, idx


def _parse_scalar(s, lineno):
    s = s.strip()
    if not s:
        return ""
    if s[0] in "{[":
        value, i = _flow_value(s, 0, lineno)
        i = _skip_ws(s, i)
        if i != len(s):
            raise YamlError(lineno, f"trailing characters after flow value: {s[i:]!r}")
        return value
    if s[0] in "\"'":
        value, i = _flow_quoted(s, 0, lineno)
        return value
    return s


def _unquote(s, lineno):
    if s and s[0] in "\"'":
        value, _ = _flow_quoted(s, 0, lineno)
        return value
    return s


def _skip_ws(s, i):
    while i < len(s) and s[i] in " \t":
        i += 1
    return i


def _flow_value(s, i, lineno):
    i = _skip_ws(s, i)
    if i >= len(s):
        raise YamlError(lineno, "unexpected end of flow value")
    c = s[i]
    if c == "{":
        return _flow_map(s, i, lineno)
    if c == "[":
        return _flow_seq(s, i, lineno)
    if c in "\"'":
        return _flow_quoted(s, i, lineno)
    j = i
    while j < len(s) and s[j] not in ",}]":
        j += 1
    return s[i:j].strip(), j


def _flow_map(s, i, lineno):
    i += 1  # past '{'
    mapping = {}
    i = _skip_ws(s, i)
    if i < len(s) and s[i] == "}":
        return mapping, i + 1
    while True:
        i = _skip_ws(s, i)
        if i < len(s) and s[i] in "\"'":
            key, i = _flow_quoted(s, i, lineno)
        else:
            j = i
            while j < len(s) and s[j] not in ":,}":
                j += 1
            key = s[i:j].strip()
            i = j
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ":":
            raise YamlError(lineno, "expected ':' in flow mapping")
        value, i = _flow_value(s, i + 1, lineno)
        mapping[key] = value
        i = _skip_ws(s, i)
        if i >= len(s):
            raise YamlError(lineno, "unterminated flow mapping (missing '}')")
        if s[i] == "}":
            return mapping, i + 1
        if s[i] != ",":
            raise YamlError(lineno, f"expected ',' or '}}' in flow mapping, got {s[i]!r}")
        i += 1


def _flow_seq(s, i, lineno):
    i += 1  # past '['
    items = []
    i = _skip_ws(s, i)
    if i < len(s) and s[i] == "]":
        return items, i + 1
    while True:
        value, i = _flow_value(s, i, lineno)
        items.append(value)
        i = _skip_ws(s, i)
        if i >= len(s):
            raise YamlError(lineno, "unterminated flow sequence (missing ']')")
        if s[i] == "]":
            return items, i + 1
        if s[i] != ",":
            raise YamlError(lineno, f"expected ',' or ']' in flow sequence, got {s[i]!r}")
        i += 1


def _flow_quoted(s, i, lineno):
    quote = s[i]
    i += 1
    buf = []
    while i < len(s):
        c = s[i]
        if c == quote:
            if quote == "'" and i + 1 < len(s) and s[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            return "".join(buf), i + 1
        if c == "\\" and quote == '"':
            nxt = s[i + 1] if i + 1 < len(s) else ""
            buf.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt))
            i += 2
            continue
        buf.append(c)
        i += 1
    raise YamlError(lineno, "unterminated quoted string")


# --------------------------------------------------------------------------- #
# Loading records off disk.                                                    #
# --------------------------------------------------------------------------- #

class RecordFile:
    def __init__(self, path, rel, frontmatter, parse_error, body, body_start):
        self.path = path            # absolute Path
        self.rel = rel              # vault-relative posix string
        self.frontmatter = frontmatter   # dict, or None if it could not be parsed
        self.parse_error = parse_error   # str message, or None
        self.body = body                 # the prose after the frontmatter
        self.body_start = body_start     # 1-based file line of the first body line


def _split_frontmatter(text):
    """Return (frontmatter_text, body_text, body_start_line, error).

    ``frontmatter_text`` is None when there is no leading ``---`` block.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, 1, "no frontmatter block (a record must open with '---')"
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            return fm, body, i + 2, None
    return None, text, 1, "frontmatter block is never closed with '---'"


def load_records(root):
    records_dir = root / "records"
    out = []
    if not records_dir.is_dir():
        return out
    for path in sorted(records_dir.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        fm_text, body, body_start, err = _split_frontmatter(text)
        frontmatter, parse_error = None, err
        if fm_text is not None:
            try:
                parsed = parse_yaml(fm_text)
                if not isinstance(parsed, dict):
                    parse_error = "frontmatter is not a mapping of fields"
                else:
                    frontmatter = parsed
            except YamlError as exc:
                # map the frontmatter-internal line to the file line (+1 for '---')
                parse_error = f"line {exc.line + 1}: {exc.message}"
        out.append(RecordFile(path, rel, frontmatter, parse_error, body, body_start))
    return out


# --------------------------------------------------------------------------- #
# Markdown links.                                                              #
# --------------------------------------------------------------------------- #

_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _iter_links(text):
    """Yield (lineno, raw_target) for every inline Markdown link/image."""
    for lineno, line in enumerate(text.split("\n"), 1):
        for match in _LINK.finditer(line):
            yield lineno, match.group(1).strip()


def _link_target_path(raw):
    """Return the local path part of a link target, or None if it is external.

    Strips an optional ``<...>`` wrapper, a trailing ``"title"``, and any
    ``#anchor`` or ``?query``. Returns None for URLs, mailto:, tel:, and pure
    anchors — nothing a file check should follow.
    """
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    # drop a title: [text](path "the title")
    if " " in target:
        target = target.split(" ", 1)[0]
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", target):  # scheme: http:, mailto:, tel:
        return None
    for sep in ("#", "?"):
        target = target.split(sep, 1)[0]
    return target or None


# --------------------------------------------------------------------------- #
# Configuration.                                                               #
# --------------------------------------------------------------------------- #

_DEFAULT_CHECKS = {
    "schema": True,
    "approved_source": True,
    "visibility": True,
    "links": True,
    "privacy": True,
    "no_delete": True,
    "intake_isolation": True,
    "unique_obligation_ids": True,
}


class Config:
    def __init__(self, root, data, base_ref_override=None):
        checks = dict(_DEFAULT_CHECKS)
        for name, value in (data.get("checks") or {}).items():
            if name in checks:
                checks[name] = bool(value)
        self.checks = checks

        privacy = data.get("privacy") or {}
        denylist = list(privacy.get("denylist") or [])
        denylist_file = privacy.get("denylist_file")
        if denylist_file:
            fpath = root / denylist_file
            if fpath.is_file():
                for raw in fpath.read_text(encoding="utf-8").split("\n"):
                    entry = raw.strip()
                    if entry and not entry.startswith("#"):
                        denylist.append(entry)
        self.denylist = denylist
        self.check_emails = bool(privacy.get("emails", True))
        self.check_phones = bool(privacy.get("phones", True))

        no_delete = data.get("no_delete") or {}
        self.base_ref = base_ref_override or no_delete.get("base_ref") or "origin/main"


def load_config(root, config_path=None, base_ref_override=None):
    path = config_path or (root / "validate.toml")
    data = {}
    if path.is_file():
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    return Config(root, data, base_ref_override=base_ref_override)


# --------------------------------------------------------------------------- #
# The checks. Each returns a list of plain-English problem strings.            #
# --------------------------------------------------------------------------- #

def check_schema(root, records, _cfg):
    schema = json.loads((_SCHEMA_DIR / "record.schema.json").read_text(encoding="utf-8"))
    problems = []
    for rec in records:
        if rec.frontmatter is None:
            problems.append(f"{rec.rel}: {rec.parse_error}")
            continue
        for err in _schema_validate(schema, rec.frontmatter, schema):
            problems.append(f"{rec.rel}: {err}")
    return problems


def check_approved_source(root, records, _cfg):
    problems = []
    manifest = _load_manifest(root)
    for rec in records:
        fm = rec.frontmatter
        if not fm or fm.get("status") != "approved":
            continue
        if not fm.get("approved"):
            problems.append(f"{rec.rel}: status is 'approved' but there is no 'approved' date; add the board approval date")
        source = fm.get("source")
        if not isinstance(source, dict):
            problems.append(f"{rec.rel}: status is 'approved' but there is no 'source'; add source.file and source.sha256")
            continue
        rel_file = source.get("file")
        declared = source.get("sha256")
        if not rel_file:
            problems.append(f"{rec.rel}: source has no 'file'; point it at the original under library/")
            continue
        fpath = root / rel_file
        if not fpath.is_file():
            problems.append(f"{rec.rel}: source.file '{rel_file}' does not exist; add the file under library/ or fix the path")
            continue
        actual = _sha256(fpath)
        if declared != actual:
            problems.append(
                f"{rec.rel}: source.sha256 does not match the bytes of '{rel_file}' "
                f"(file is {actual}); recompute it with: sha256sum {rel_file}")
        if manifest is None:
            problems.append(f"{rec.rel}: library/manifest.json is missing; it must list '{rel_file}' with its sha256")
        elif rel_file not in manifest:
            problems.append(f"{rec.rel}: '{rel_file}' is not listed in library/manifest.json; add it with its sha256")
        elif manifest[rel_file] != actual:
            problems.append(
                f"{rec.rel}: library/manifest.json records a different sha256 for '{rel_file}' "
                f"than the file has ({actual}); update the manifest")
    return problems


def check_visibility(root, records, _cfg):
    problems = []
    for rec in records:
        fm = rec.frontmatter
        if fm is None:
            continue
        if fm.get("visibility") != "public":
            problems.append(
                f"{rec.rel}: visibility is {fm.get('visibility')!r}, but this vault is public; "
                f"set 'visibility: public' or do not publish this record")
    return problems


def check_links(root, records, _cfg):
    problems = []
    targets = [(rec.rel, rec.path, rec.path.read_text(encoding="utf-8")) for rec in records]
    index = root / "index.md"
    if index.is_file():
        targets.append(("index.md", index, index.read_text(encoding="utf-8")))
    for rel, path, text in targets:
        for lineno, raw in _iter_links(text):
            local = _link_target_path(raw)
            if local is None:
                continue
            if local.startswith("/"):
                problems.append(f"{rel}:{lineno}: link '{raw}' is an absolute path; use a relative link")
                continue
            resolved = (path.parent / local).resolve()
            if not resolved.exists():
                problems.append(f"{rel}:{lineno}: link '{raw}' does not resolve; fix the path or add the file")
    return problems


def check_privacy(root, records, cfg):
    problems = []
    needles = [(entry, re.compile(re.escape(entry), re.IGNORECASE)) for entry in cfg.denylist]
    email_re = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    # A North-American-style phone number; the 3-3-4 grouping keeps ISO dates
    # (4-2-2) and sha256 hex out of the net.
    phone_re = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")

    scopes = [(rec.rel, rec.body, rec.body_start) for rec in records]
    text_dir = root / "library" / "text"
    if text_dir.is_dir():
        for path in sorted(text_dir.rglob("*")):
            if path.is_file():
                scopes.append((path.relative_to(root).as_posix(),
                               path.read_text(encoding="utf-8", errors="replace"), 1))

    for rel, text, start in scopes:
        for offset, line in enumerate(text.split("\n")):
            lineno = start + offset
            for idx, (_, rx) in enumerate(needles, 1):
                if rx.search(line):
                    problems.append(
                        f"{rel}:{lineno}: matches privacy denylist entry #{idx}; "
                        f"remove or redact it (value withheld from this message)")
            if cfg.check_emails and email_re.search(line):
                problems.append(
                    f"{rel}:{lineno}: an email-address pattern appears here (value withheld); "
                    f"remove it or add it to an allowance if it is an org address")
            if cfg.check_phones and phone_re.search(line):
                problems.append(
                    f"{rel}:{lineno}: a phone-number pattern appears here (value withheld); remove it")
    return problems


def check_no_delete(root, records, cfg):
    ref = cfg.base_ref
    try:
        prefix = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-prefix"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"no_delete: {root} is not inside a git work tree; skipping this check", file=sys.stderr)
        return []
    resolved = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True)
    if resolved.returncode != 0:
        print(f"no_delete: base ref '{ref}' is not available; skipping this check "
              f"(fetch it in CI to enable the no-deletion guard)", file=sys.stderr)
        return []
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref],
        capture_output=True, text=True, check=True).stdout.split("\n")

    scope = f"{prefix}records/"
    base_records = {
        p[len(prefix):] for p in listing
        if p.startswith(scope) and p.endswith(".md")}
    present = {rec.rel for rec in records}
    problems = []
    for rel in sorted(base_records - present):
        problems.append(
            f"{rel}: a published record present in '{ref}' is missing from the tree; "
            f"retract or supersede instead (set status: retracted), do not delete the file")
    return problems


def check_intake_isolation(root, records, _cfg):
    problems = []
    targets = [(rec.rel, rec.path, rec.path.read_text(encoding="utf-8")) for rec in records]
    index = root / "index.md"
    if index.is_file():
        targets.append(("index.md", index, index.read_text(encoding="utf-8")))
    for rel, path, text in targets:
        for lineno, raw in _iter_links(text):
            local = _link_target_path(raw)
            if local is None or local.startswith("/"):
                continue
            resolved = (path.parent / local).resolve()
            try:
                rel_to_root = resolved.relative_to(root.resolve())
            except ValueError:
                continue
            if rel_to_root.parts and rel_to_root.parts[0] == "intake":
                problems.append(
                    f"{rel}:{lineno}: links into intake/ ('{raw}'); intake is never published, "
                    f"so records and index.md must not reference it")
    return problems


def check_unique_obligation_ids(root, records, _cfg):
    obligations_dir = root / "obligations"
    if not obligations_dir.is_dir():
        return []
    seen = {}  # id -> vault-relative path of the first file that declared it
    problems = []
    for path in sorted(obligations_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".json"}:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                data = parse_yaml(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, YamlError, OSError):
            continue  # parse errors are the schema check's job
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = [data]
        else:
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            oid = item.get("id")
            if not oid or not isinstance(oid, str):
                continue
            if oid in seen:
                problems.append(
                    f"{rel}: obligation id '{oid}' is already declared in {seen[oid]}; "
                    f"each obligation id must be unique across the vault")
            else:
                seen[oid] = rel
    return problems


_CHECK_FUNCS = {
    "schema": check_schema,
    "approved_source": check_approved_source,
    "visibility": check_visibility,
    "links": check_links,
    "privacy": check_privacy,
    "no_delete": check_no_delete,
    "intake_isolation": check_intake_isolation,
    "unique_obligation_ids": check_unique_obligation_ids,
}


# --------------------------------------------------------------------------- #
# Small helpers.                                                               #
# --------------------------------------------------------------------------- #

def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(root):
    path = root / "library" / "manifest.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files") if isinstance(data, dict) and "files" in data else data
    return files if isinstance(files, dict) else {}


# --------------------------------------------------------------------------- #
# Orchestration.                                                              #
# --------------------------------------------------------------------------- #

def validate_vault(root, cfg):
    """Run every enabled check. Return (problems, ran, skipped)."""
    root = Path(root)
    records = load_records(root)
    problems = []
    ran, skipped = [], []
    for name, func in _CHECK_FUNCS.items():
        if cfg.checks.get(name):
            ran.append(name)
            problems.extend(func(root, records, cfg))
        else:
            skipped.append(name)
    return problems, ran, skipped


def run(argv=None):
    parser = argparse.ArgumentParser(description="Validate a uvularia vault before publishing.")
    parser.add_argument("root", nargs="?", default=".", help="the vault root (default: current directory)")
    parser.add_argument("--config", help="path to validate.toml (default: <root>/validate.toml)")
    parser.add_argument("--base-ref", help="git ref for the no_delete check (overrides validate.toml)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: vault root '{root}' is not a directory", file=sys.stderr)
        return 2
    config_path = Path(args.config) if args.config else None
    try:
        cfg = load_config(root, config_path=config_path, base_ref_override=args.base_ref)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(f"error: could not read config: {exc}", file=sys.stderr)
        return 2

    problems, ran, skipped = validate_vault(root, cfg)

    print(f"checks run: {', '.join(ran) or '(none)'}")
    if skipped:
        print(f"checks off: {', '.join(skipped)} (switched off in validate.toml)")
    print()
    if problems:
        for problem in problems:
            print(problem)
        print()
        noun = "problem" if len(problems) == 1 else "problems"
        print(f"{len(problems)} {noun} found. Fix the files named above; nothing is published until this passes.")
        return 1
    print("All enabled checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
