# receipts/ — one stamped receipt per publish, append-only

Every time the publish workflow runs, it writes one receipt here named
`YYYY-MM-DD-<digest>.md`. A receipt is the proof of a single publish: it records

- the **corpus digest** that publish produced — the exact version every consumer
  pins;
- the **server-side timestamp** of the Actions run that published it — the "posted
  at" time, taken from the workflow's own clock, never from an author's commit;
- the **run URL**, so anyone can open the run that did it;
- the **commit** that was published;
- **what changed** — which records were added, changed, or retracted;
- **how the obligations stood** at that moment — the green / amber / red / no-data
  counts.

The machine-readable version of all of that is the frontmatter at the top of each
receipt (it matches [`../core/schema/receipt.schema.json`](../core/schema/receipt.schema.json));
the table underneath it is the same thing for people.

**Append-only — this is a rule, not a convention.** A receipt is a historical
fact: it says what was true at a past publish. Never edit one and never delete
one. The record you published may later be superseded or retracted, but the
receipt that it *was* published stays exactly as written. Corrections are made by
publishing again, which writes a new receipt; the old one remains.

These files are produced by the workflow. You do not write them by hand.
