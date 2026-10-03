# Records

This is the public records vault for **your organization** — minutes, notices,
bylaws, policies, filings, reports, and announcements, each kept as one Markdown
file with its original document alongside it. Edit the name above and this
paragraph to describe your organization.

Everything here is public. A change is made by opening a pull request; when it
merges, the records publish, the **"Is it posted?"** board updates, and a receipt
is written. Nothing is posted until the checks on the pull request pass.

## What is where

- [`records/`](records/) — one folder per record type: `minutes/`, `notice/`,
  `agenda/`, `policy/`, `bylaw/`, `filing/`, `report/`, `announcement/`, `faq/`.
  One file per record.
- [`library/`](library/) — the original documents (`files/`) with their
  checksums in [`manifest.json`](library/manifest.json), and any extracted text
  (`text/`).
- [`obligations/`](obligations/README.md) — the rules about what must be posted
  and when. The board is built from these.
- [`receipts/`](receipts/README.md) — one stamped, append-only receipt per
  publish.
- `intake/` — scratch space for drafts. **Never published**, and never linked to
  from a published page (the checks enforce both). See its own README.
- [`templates/record.md`](templates/record.md) — duplicate this to add a record.
- [`core/`](core/README.md) — the validator, the evaluator, and the bundle
  builder your checks run. Vendored; see [the README](README.md).

## Add a record

Open [`templates/record.md`](templates/record.md), duplicate it into the right
`records/<type>/` folder, fill in the frontmatter and the prose, and open a pull
request. The full walkthrough is in [`README.md`](README.md).
