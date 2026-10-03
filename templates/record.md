---
# Duplicate this file into records/<type>/ and rename it to match the id below.
# The frontmatter between the --- lines is the whole machine-readable record;
# the prose underneath is for people. Every field here is defined in
# core/schema/record.schema.json — do not invent a field outside it.
# Comments (lines starting with #) are ignored by the checks, so you may keep or
# delete them.

# The permanent id: YYYY-MM-DD-slug, lowercase, words joined by hyphens. It is
# also the file name (without .md) and never changes once published.
id: 2026-01-15-example-board-minutes
# The human title shown on the site and in citations.
title: "Board meeting minutes, 15 January 2026"
# One of: minutes, notice, agenda, policy, bylaw, filing, report, announcement, faq
# It must match the folder you put this file in (records/minutes/ here).
type: minutes
# draft while you work; approved once the board has approved it. Only approved,
# public records are published. Later: superseded (replaced) or retracted.
status: approved
# Always public — this repo holds only public records and the check enforces it.
visibility: public
# ISO date (YYYY-MM-DD) the record takes effect or the meeting happened.
effective: 2026-01-15
# The date the board approved it. Required when status is approved.
approved: 2026-02-01
# The original document. Required when status is approved: put the file under
# library/files/<type>/, list it in library/manifest.json with its sha256, and
# paste the same sha256 here. Compute it with:  sha256sum <the file>
source:
  kind: pdf
  file: library/files/minutes/2026-01-15-example-board-minutes.pdf
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
# verified = the text below was checked against the source; reported = not yet.
certainty: verified
# Topics the Ask box matches questions, announcements, and obligations against.
subjects: [meetings, minutes, budget]
# Free-form labels for grouping. Not machine-critical.
tags: [board, 2026]

# --- Optional fields; delete the ones you do not use ---

# The id of the record this one replaces, to build a version chain (policies,
# bylaws). Leave out unless this supersedes something.
# supersedes: 2025-01-20-example-board-minutes

# Append-only, dated corrections. Never edit a figure in place — add a line.
# corrections:
#   - {date: 2026-02-10, note: "Treasurer's figure corrected from $12,400 to $12,040; source p.3."}

# Required only when type is announcement: when it goes live (its merge-to-live
# latency is measured from this).
# publish_at: 2026-01-15T09:00:00Z
---

# Board meeting minutes, 15 January 2026

Write the plain-English record here. The first `#` heading becomes the title in
the published corpus, so keep it meaningful.

Summarize what a reader needs: who met, what was decided, the figures that
matter. Link to other records with relative Markdown links, for example
[the 2026 budget policy](../policy/2026-01-15-example-budget-policy.md). Do not
link into `intake/` — nothing there is ever published.
