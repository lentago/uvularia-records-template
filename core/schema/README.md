# core/schema — the record and format schemas

These five JSON Schema files (draft 2020-12) are the whole machine-readable
contract of a uvularia vault. Everything a client runs in CI validates against
them, and nothing may add a field outside them. Prose in a record is for people;
these schemas are for machines.

**What you get:** a precise definition of each document the system reads or
writes, an example that validates and an example that fails for each, and a
dependency-free checker (`check_examples.py`) that proves the examples behave as
named. **Why bother:** a bad field is caught on the pull request, before it can
reach the public site or the Ask box. **How long:** the checker runs in well
under a second. **How you know it worked:** it prints one `ok` line per example
and exits `0`; on any mismatch it prints `FAIL` with the reason and exits `1`.

Run it yourself:

```
python3 core/schema/check_examples.py
```

Python 3.12, standard library only. No `pip install`, ever — the checker carries
its own small validator for the subset of JSON Schema these files use.

A few terms used throughout. A **record** is one public document (minutes, a
notice, a policy…) kept as one Markdown file. The **frontmatter** is the YAML
block at the top of that file; the record schema validates it as a parsed
object. An **obligation** is a rule that says a record of some kind must be
posted on some schedule. The **corpus bundle** is the published snapshot every
consumer reads. **Standing** is the live green/amber/red state of each
obligation. A **receipt** is the stamped record of one publish. **Server-side
time** means the timestamp of the publishing workflow run, never an author's
commit clock.

---

## record.schema.json — one public record's frontmatter

| Field | What it means | Who sets it | What breaks if it is wrong |
|---|---|---|---|
| `id` | The permanent path slug, `YYYY-MM-DD-slug`. Stable forever. | Author (intake scaffolds it) | A changed id orphans every link and supersession chain pointing at it. |
| `title` | Human title shown on the site and in citations. | Author | An empty title leaves a blank row on the board and in the Ask box. |
| `type` | One of `minutes, notice, agenda, policy, bylaw, filing, report, announcement, faq`. | Author | A type outside the list fails the build; obligations keyed to a type would never match. |
| `status` | `draft`, `approved`, `superseded`, or `retracted`. | Author / board | Only `approved` records publish. A wrong status hides a real record or ships an unapproved one. |
| `visibility` | Always the constant `public` in v1. | Author | Anything else fails CI — this vault is public and holds only public records. |
| `effective` | ISO date (`YYYY-MM-DD`) the record takes effect or the meeting happened. | Author | A malformed date mis-sorts the timeline and mis-measures obligations. |
| `approved` | ISO date the board approved it. **Required when `status` is `approved`.** | Board secretary | An approved record with no approval date cannot prove when it became official. |
| `source` | `{kind, file, sha256}` — the original document, its path in `library/`, and its SHA-256. **Required when `status` is `approved`.** | Intake (computes the hash) | A wrong hash means the published text can no longer be tied to the original file. |
| `certainty` | `verified` or `reported` — whether the text was checked against the source. | Author / reviewer | Mislabelling `reported` as `verified` tells the Ask box to trust unchecked text. |
| `supersedes` | Optional id of the record this one replaces; builds the version chain. | Author | A wrong id breaks the chain, so readers see an old policy as current. |
| `subjects` | Topics the Ask box matches incidents and announcements against. | Author | Missing subjects mean a breach or announcement never reaches a related answer. |
| `tags` | Free-form labels for grouping and filtering. | Author | Harmless if wrong, but noisy; not machine-critical. |
| `corrections` | Append-only list of `{date, note}`; never edit history in place. | Author | Overwriting instead of appending hides that a figure ever changed. |
| `publish_at` | When an announcement goes live. **Required when `type` is `announcement`.** | Author | An announcement with no `publish_at` has no measurable merge-to-live latency. |

The two conditional rules are enforced in the schema: **`status: approved`
requires `source` and `approved`**, and **`type: announcement` requires
`publish_at`**. No property outside the table is allowed.

## obligation.schema.json — one posting rule

An obligation says which record type satisfies it and on what timing. It encodes
**posting mechanics, not legal interpretation**; the disclaimer lives in the
rules repo's `policy.yaml`.

| Field | What it means | Who sets it | What breaks if it is wrong |
|---|---|---|---|
| `id` | Stable slug for this obligation. | Pack author | A changed id loses the board row's history. |
| `title` | Plain-English statement of the rule. | Pack author | A vague title makes the board unreadable. |
| `pack` | The jurisdiction pack this belongs to (e.g. `ma`). | Pack author | A wrong pack files the rule under the wrong jurisdiction. |
| `record_type` | Which record type satisfies or triggers it. | Pack author | A type that never occurs leaves the obligation permanently unmet. |
| `subjects` | Topics a breach of this rule should surface against in the Ask box. | Pack author | Missing subjects mean a breach never reaches a related answer. |
| `source` | Citation string for what the rule encodes. | Pack author | Without a citation, no one can check the rule against the statute. |
| `disclaimer_ref` | Pointer to the not-legal-advice disclaimer in `policy.yaml`. | Pack author | A missing reference lets a mechanical rule read as legal advice. |

Exactly **one** timing kind must be present:

- **`lead`** — posted at least this far *before* the event. `{hours: N}` **or**
  `{days: N}`, not both. (A 48-hour meeting notice.) Two optional fields narrow
  which hours count toward the window:
  - **`weekdays_only: true`** — hours on Saturdays and Sundays don't count. Every
    hour of Monday to Friday counts, all 24 of them. It is not called
    `business_hours` because that name suggests 9-to-5, and the Open Meeting
    Law's "excluding Saturdays, Sundays and legal holidays" doesn't mean that.
  - **`exclude_dates: [YYYY-MM-DD, …]`** — hours on these dates don't count
    either (legal holidays). This is data, not code: a pack ships the list,
    one `holidays/<year>.json` file per year, and the rule carries the dates.
    A year missing from the list is counted as ordinary days, so the deadline
    is *later* than the law's. Add next year's holidays before it begins.
- **`lag`** — posted no later than this many days *after* the event.
  `{days: N}`. (Minutes within 30 days.)
- **`cadence`** — a fresh record of this type at least every N months, measured
  from the last approved one. `{months: N}`. (Conflict-of-interest policy
  reviewed annually.)
- **`one_off`** — a single fixed due date. `{due: YYYY-MM-DD}`. (A charity
  filing due this year.)

Zero kinds, or two, fails the build.

## bundle.schema.json — the published corpus

The snapshot every consumer reads by URL and digest. Site builds come from this,
never from a vault checkout.

| Field | What it means | What breaks if it is wrong |
|---|---|---|
| `digest` | SHA-256 of the corpus content; the version everyone pins. | A wrong digest lets consumers serve a corpus that is not the one attested. |
| `published_at` | Server-side publish time. | A client clock here would undermine every "posted at" claim. |
| `records[]` | One entry per published record. | A dropped or malformed entry silently removes a record from answers. |

Each entry is mitchella's wiki-loader shape: `doc_id`, `title`, `tags`,
`volatility` (`stable`/`live`, mitchella's vocabulary — vault records are always `stable`; `live` is reserved for entries that come from signals rather than records; it says how often the entry is expected to
change), `body`, `path`, `certainty` (`verified`/`inferred`/`unknown`, mitchella's vocabulary; the builder maps a record's `reported` to `inferred`), and `archived`.
The **`archived`** flag is present from day one so old minutes can drop out of
the prompt once a vault outgrows the corpus ceiling, without leaving the record.

## standing.schema.json — the live obligation board data

An array with one row per obligation. The public board and the Ask box both read
it, so it is the single source of compliance state.

| Field | What it means | What breaks if it is wrong |
|---|---|---|
| `id` | The obligation's id. | A mismatched id shows standing for the wrong rule. |
| `state` | `green`, `amber`, `red`, or `no-data`. | A fake `green` is the one thing this product must never ship. |
| `satisfied_by` | Id of the record that satisfies it, or `null`. | A wrong record makes the board point at the wrong evidence. |
| `deadline` | The relevant deadline date, or `null`. | A wrong deadline mis-colours the row. |
| `published_at` | When the satisfying record was published (server-side), or `null`. | A client time here misstates when the obligation was met. |
| `gap` | Days relative to the deadline, or `null`. Negative is ahead of the deadline; positive is overdue. | A wrong sign flips "early" and "late". |
| `history` | Track record over a trailing window — `{window_days, evaluated, breaches}` — or `null`. | A fabricated zero-breach row would flatter a rule the vault cannot actually vouch for. |

Every field but `history` is **required**, and all but `id` and `state` are
nullable. A row with missing data is `no-data` with explicit `null`s — never a
silently omitted field and never a fake green. (Invariant 5.) `history` is
optional for now only because it is new: see
[Compatibility between releases](#compatibility-between-releases). Today's
`evaluate.py` always writes it.

`history` is the one thing a current-state row cannot show: a timely posting
retires the red that late ones earned, so the live row forgets the lateness. It
carries `window_days` (the width of the trailing window), `evaluated` (past
deadlines in that window with a receipt-dated outcome), and `breaches` (how many
were red at their deadline). It is measured from receipts and records, never from
the current date alone, so a later timely posting never erases an earlier breach.
When the window holds no past, receipt-dated deadline, `history` is `null` — the
board says "no history yet", which is not the same as zero breaches.
A row with no `history` key at all comes from a vault whose core is older than
the field; the board reads it exactly like `null`.

## receipt.schema.json — one publish, stamped

Written append-only under `receipts/` on every publish. It is how a publish
proves what it did.

| Field | What it means | What breaks if it is wrong |
|---|---|---|
| `digest` | The corpus digest this publish produced. | A wrong digest breaks the tie between this receipt and the corpus. |
| `published_at` | Server-side time of the Actions run. | A client time here defeats the whole server-side-time invariant. |
| `run_url` | HTTPS URL of the Actions run that produced it. | A missing or non-https URL makes the publish unauditable. |
| `commit` | The commit SHA that was published. | A wrong commit means the receipt cannot be reproduced. |
| `records` | `{added, changed, retracted}` — lists of record ids. | A record deleted rather than retracted would violate the no-silent-change rule. |
| `standing` | Counts `{green, amber, red, no_data}`, none negative. | Wrong counts misreport how the obligations stood at publish. |

---

## Compatibility between releases

A vault and the things that read it update at different times. Your site, your
Ask function, and your vault each pull a new core when someone gets round to it,
so for a while a newer site reads standing written by an older core. The rule
that keeps that from breaking a build:

- **A consumer must build against the previous release's format.** A site or
  Ask runtime built from this release must accept what the last release's
  `evaluate.py` and `bundle.py` wrote.
- **A new field is optional until the next release.** When a release adds a
  field, the schema lists it under `properties` but not under `required`, and
  the producer starts writing it straight away. The release after that may make
  it required.
- **Absent is not the same as invalid.** A consumer treats a missing optional
  field as "not known yet" and shows its fallback (for `history`, "no history
  yet"). A file that breaks the schema in any other way — a wrong type, an
  unknown state, a missing required field — still stops the build.
- **Keep an example of the previous format.** `examples/<name>.previous.good.json`
  is the last release's shape. It must keep validating, so the checker catches a
  change that would turn an older vault's output into a failed build.

Today this applies to `history` on standing rows: added in this release,
optional until the next.

## Examples and the checker

Every schema has, in `examples/`:

- `<name>.good.json` — a document that must validate.
- `<name>.<variant>.good.json` — optional extra documents that must validate,
  such as `standing.previous.good.json`, the previous release's format.
- `<name>.bad.json` — a document that must **fail**, paired with a one-line
  `<name>.bad.why` saying why.

`check_examples.py` runs both. The `.bad.*` files are deliberate failing
fixtures: the checker asserts they fail and reports `ok` only when they do, which
is how we prove the check itself can fail rather than passing vacuously. A
missing example, or a good one that does not validate, or a bad one that does, is
a non-zero exit.
