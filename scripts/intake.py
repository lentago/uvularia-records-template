#!/usr/bin/env python3
"""Turn an "Add a record" issue into a scaffolded vault record. Stdlib only.

This is the engine behind the Issue-form intake door. Someone with only a
browser files the "Add a record" issue (see
``.github/ISSUE_TEMPLATE/add-record.yml``); the intake workflow
(``.github/workflows/intake.yml``) checks the repo out, hands this script the
issue body, and this script does the file work a tech director can read line by
line:

  1. parse the issue form body into fields;
  2. build a record id (``YYYY-MM-DD-slug``) and its path under records/<type>/;
  3. scaffold that record from ``templates/record.md`` with the frontmatter
     filled in — always ``status: draft``, ``visibility: public``,
     ``certainty: reported``;
  4. if the form carried an attached file, download it into
     ``library/files/<type>/``, checksum it, add it to ``library/manifest.json``,
     and point the record's ``source`` at it;
  5. write a small JSON result the workflow turns into the pull-request body and
     the issue comment.

Nothing here talks to git or GitHub's API — the workflow owns the branch, the
pull request, and the comment. This script only reads the body it is given and
writes files into the vault. That keeps the step that touches a person's
document small, dependency-free, and auditable.

    python3 scripts/intake.py --vault . --issue 42 \
        --body-file body.md --result-file result.json

The record is scaffolded as a **draft**, so it validates on its own even with no
source attached: ``core/schema/record.schema.json`` only requires ``source`` and
``approved`` once ``status`` is ``approved``. A reviewer promotes it to approved
(and fills ``approved``) on the pull request — never this script.

Python 3.12, standard library only. No third-party package is ever installed.
"""

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The nine record types, in the schema's enum order. Kept in step with
# core/schema/record.schema.json and the dropdown in the issue form; a type the
# schema does not know is rejected here with a plain message rather than written
# into a record that would only fail validation later.
RECORD_TYPES = [
    "minutes", "notice", "agenda", "policy", "bylaw",
    "filing", "report", "announcement", "faq",
]

# The issue form renders each field as a `### <label>` heading followed by the
# answer. These are the labels from add-record.yml, mapped to the keys we use.
FIELD_LABELS = {
    "Record type": "type",
    "Date it takes effect": "effective",
    "Title": "title",
    "Two-sentence summary": "summary",
    "Subjects": "subjects",
    "The original document": "document",
}

# GitHub writes this into a field the filer left blank.
NO_RESPONSE = "_No response_"

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A file attached to an issue renders as a Markdown link or image. Newer uploads
# live under github.com/user-attachments/assets/<uuid>; older ones under
# user-images.githubusercontent.com or github.com/<owner>/<repo>/files/<id>/name.
# We capture the link text (which carries the real filename, hence the
# extension) and the URL.
ATTACHMENT_RE = re.compile(
    r"!?\[([^\]]*)\]\((https?://(?:github\.com/user-attachments/assets/"
    r"|user-images\.githubusercontent\.com/"
    r"|github\.com/[^/]+/[^/]+/(?:files|assets)/)[^)\s]+)\)"
)


class IntakeError(Exception):
    """A problem with the form that stops us scaffolding a valid record."""


# --------------------------------------------------------------------------- #
# Parsing the issue form body.                                                 #
# --------------------------------------------------------------------------- #

def parse_issue_form(body):
    """Split an "Add a record" issue body into its fields.

    Returns a dict keyed by the short keys in ``FIELD_LABELS``. A field the
    filer left blank (GitHub's ``_No response_``) comes back as an empty string.
    Unknown headings are ignored, so added help text never breaks parsing.
    """
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    fields = {}
    current = None
    chunk = []
    for line in body.split("\n"):
        heading = line.strip()
        if heading.startswith("### "):
            if current is not None:
                fields[current] = "\n".join(chunk).strip()
            label = heading[4:].strip()
            current = FIELD_LABELS.get(label)
            chunk = []
            continue
        if current is not None:
            chunk.append(line)
    if current is not None:
        fields[current] = "\n".join(chunk).strip()

    out = {}
    for key in FIELD_LABELS.values():
        value = fields.get(key, "")
        out[key] = "" if value == NO_RESPONSE else value
    return out


def slugify(title):
    """A stable, lowercase slug from a title, matching the record id pattern."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug


def split_subjects(raw):
    """A comma list into a clean, de-duplicated list of subject slugs."""
    seen = []
    for part in raw.split(","):
        subject = re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-")
        if subject and subject not in seen:
            seen.append(subject)
    return seen


def find_attachment(body):
    """The first attached file in the body, as (filename, url), or None."""
    match = ATTACHMENT_RE.search(body)
    if not match:
        return None
    text, url = match.group(1).strip(), match.group(2).strip()
    return text or None, url


def _extension(filename, url):
    """A safe lowercase extension (with dot) from the link text, else .pdf."""
    for candidate in (filename or "", url):
        suffix = Path(candidate.split("?")[0]).suffix.lower()
        if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            return suffix
    return ".pdf"


# --------------------------------------------------------------------------- #
# Building the record.                                                         #
# --------------------------------------------------------------------------- #

def build_fields(form):
    """Validate the parsed form and compute the record's identity.

    Raises IntakeError with a plain-English message a filer can act on. The
    workflow turns that message into an issue comment; it never scaffolds a
    record the validator would only reject.
    """
    rtype = form.get("type", "").strip()
    if rtype not in RECORD_TYPES:
        raise IntakeError(
            f"Record type must be one of: {', '.join(RECORD_TYPES)}. "
            f"Got {rtype!r}. Edit the issue and pick a type from the dropdown.")

    effective = form.get("effective", "").strip()
    if not ISO_DATE.match(effective):
        raise IntakeError(
            f"The date it takes effect must be YYYY-MM-DD (for example "
            f"2026-01-15). Got {effective!r}. Edit the issue to fix the date.")

    title = " ".join(form.get("title", "").split())
    if not title:
        raise IntakeError("The title is empty. Edit the issue and add a title.")

    slug = slugify(title)
    if not slug:
        raise IntakeError(
            "The title has no letters or numbers to make a file name from. "
            "Edit the issue and give it a plain title.")

    record_id = f"{effective}-{slug}"
    return {
        "type": rtype,
        "effective": effective,
        "title": title,
        "id": record_id,
        "rel_path": f"records/{rtype}/{record_id}.md",
        "summary": form.get("summary", "").strip(),
        "subjects": split_subjects(form.get("subjects", "")),
    }


def _yaml_list(items):
    """A YAML flow sequence the vault's frontmatter parser accepts."""
    return "[" + ", ".join(items) + "]"


def _yaml_str(value):
    """Quote a scalar so it survives the frontmatter parser intact."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_record(fields, source=None):
    """The scaffolded record file: filled frontmatter plus a prose stub.

    ``source`` is a dict ``{"file": ..., "sha256": ...}`` when a file was
    downloaded, otherwise None. A draft needs no source to validate, so when the
    attachment could not be fetched the record is written without one and the
    prose carries a visible note to add the file by hand.
    """
    fm = [
        "---",
        "# Scaffolded by the intake workflow from an \"Add a record\" issue.",
        "# It is a DRAFT: it does not publish until a reviewer sets",
        "# 'status: approved' and the 'approved' date on the pull request.",
        "# Every field is defined in core/schema/record.schema.json.",
        f"id: {fields['id']}",
        f"title: {_yaml_str(fields['title'])}",
        f"type: {fields['type']}",
        "status: draft",
        "visibility: public",
        f"effective: {fields['effective']}",
        "certainty: reported",
    ]
    if source:
        fm += [
            "source:",
            "  kind: pdf",
            f"  file: {source['file']}",
            f"  sha256: {_yaml_str(source['sha256'])}",
        ]
    fm += [
        f"subjects: {_yaml_list(fields['subjects'])}",
        f"tags: {_yaml_list([fields['effective'][:4]])}",
        "---",
    ]

    summary = fields["summary"] or "_Add a plain-English summary of this record._"
    body = ["", f"# {fields['title']}", "", summary, ""]
    if not source:
        body += [
            "> **Add the original document by hand.** The file attached to the "
            "issue could not be downloaded automatically, so this record has no "
            "`source` yet. Put the original under "
            f"`library/files/{fields['type']}/`, add it to "
            "`library/manifest.json` with its sha256 (`sha256sum <file>`), and "
            "add a `source:` block to the frontmatter above. A reviewer must do "
            "this before approving the record.",
            "",
        ]
    return "\n".join(fm + body) + "\n"


# --------------------------------------------------------------------------- #
# The attached file.                                                           #
# --------------------------------------------------------------------------- #

def download(url, dest, timeout=30):
    """Fetch ``url`` into ``dest``. Return None on success, else a reason.

    Private-repo attachment URLs can require a browser session the workflow does
    not have; any failure returns a human reason instead of raising, so the
    workflow can still open the pull request and say the file needs adding by
    hand (never a silent miss).
    """
    request = urllib.request.Request(url, headers={"User-Agent": "uvularia-intake"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        return f"the download returned HTTP {exc.code} ({exc.reason})"
    except urllib.error.URLError as exc:
        return f"the download failed ({exc.reason})"
    except (TimeoutError, OSError) as exc:
        return f"the download failed ({exc})"
    if not data:
        return "the downloaded file was empty"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return None


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def update_manifest(vault, rel_file, digest):
    """Add or update one file's checksum in library/manifest.json, in place."""
    path = vault / "library" / "manifest.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"files": {}}
    if not isinstance(data, dict) or "files" not in data or not isinstance(data["files"], dict):
        data = {"files": {}}
    data["files"][rel_file] = digest
    data["files"] = dict(sorted(data["files"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Orchestration — pure enough to unit-test without a network or a repo.        #
# --------------------------------------------------------------------------- #

def scaffold(vault, body, fetch=download):
    """Do the whole job against a vault directory. Return a result dict.

    ``fetch`` is injectable so the tests can run without a network. It takes
    ``(url, dest)`` and returns None on success or a string reason on failure.
    """
    vault = Path(vault)
    form = parse_issue_form(body)
    fields = build_fields(form)   # raises IntakeError on a form we cannot use

    attachment = find_attachment(body)
    result = {
        "ok": True,
        "id": fields["id"],
        "type": fields["type"],
        "title": fields["title"],
        "record_path": fields["rel_path"],
        "subjects": fields["subjects"],
        "attachment": {"found": attachment is not None},
    }

    source = None
    if attachment is not None:
        filename, url = attachment
        rel_file = f"library/files/{fields['type']}/{fields['id']}{_extension(filename, url)}"
        dest = vault / rel_file
        result["attachment"].update({"url": url, "file": rel_file})
        reason = fetch(url, dest)
        if reason is None:
            digest = sha256_of(dest)
            update_manifest(vault, rel_file, digest)
            source = {"file": rel_file, "sha256": digest}
            result["attachment"].update({"downloaded": True, "sha256": digest})
        else:
            result["attachment"].update({"downloaded": False, "reason": reason})

    record_path = vault / fields["rel_path"]
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(render_record(fields, source=source), encoding="utf-8")
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description="Scaffold a vault record from an 'Add a record' issue.")
    parser.add_argument("--vault", default=".", help="the vault root (default: current directory)")
    parser.add_argument("--issue", required=True, help="the issue number (for messages)")
    parser.add_argument("--body-file", required=True, help="a file holding the issue body")
    parser.add_argument("--result-file", help="where to write the JSON result (default: stdout)")
    args = parser.parse_args(argv)

    body = Path(args.body_file).read_text(encoding="utf-8")
    try:
        result = scaffold(args.vault, body)
    except IntakeError as exc:
        result = {"ok": False, "issue": args.issue, "error": str(exc)}
    else:
        result["issue"] = args.issue

    text = json.dumps(result, indent=2)
    if args.result_file:
        Path(args.result_file).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(run())
