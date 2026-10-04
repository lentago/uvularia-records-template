# core/evaluate.py — turning rules into a board

`evaluate.py` reads your obligations, your records, and your publish receipts,
and writes `standing.json`: one row per obligation, each coloured **green**,
**amber**, **red**, or **no-data**. The public board and the Ask box both read
that one file, so it is the single source of truth for "are we posting what we
said we would, on time?"

**What you get:** a colour and a plain number for every rule, computed the same
way every time, with the evidence attached (which record satisfied it, and when
it actually went public). **Why bother:** the board never guesses and never
flatters — a thing is green only when a receipt proves it was posted on time.
**How long:** it runs in well under a second on a normal vault, as part of the
publish workflow. **How you know it worked:** it prints a JSON array that
validates against [`schema/standing.schema.json`](schema/standing.schema.json);
on an internal bug it exits non-zero and says what failed, rather than shipping a
wrong board.

Run it yourself:

```
python3 core/evaluate.py <vault-root>
python3 core/evaluate.py <vault-root> -o standing.json --warn-days 7
python3 core/evaluate.py <vault-root> --now 2026-10-03T12:00:00Z    # pin "now" for a dry run
```

Python 3.12, standard library only. No `pip install`, ever — it carries a small
YAML reader so your obligation files and record frontmatter need no PyYAML, and
it accepts JSON too.

## The one rule behind every colour

**Timing comes from receipts, never from file clocks.** A record's publish time
is the `published_at` of the receipt that published it — the timestamp of the
Actions run, set server-side. A file's modification time and a commit's author
date are both trivially wrong or forgeable, so neither is ever used. The
practical consequence you will feel: **a record with no receipt cannot be green.**
It may be written, committed, and perfectly correct, but until a publish stamps
it, there is nothing that proves *when* it went public, so the board will not
claim it was on time.

## The four states, and what you do about each

| State | What it means | What you do |
|---|---|---|
| **green** | A record was published, and the receipt shows it met the rule's timing. | Nothing. This is the resting state. |
| **amber** | A deadline is coming up within the warning window and the obligation is not yet met — or a recurring review is still valid but due again soon. | Post the record (or schedule the review) before the deadline. Amber is a nudge, not a failure. |
| **red** | A deadline passed without a timely posting — nothing was published in time, or what was published landed late. | Post it now and note it. A late posting is still worth making; the row stays red for the record, which is the honest result. |
| **no-data** | There is nothing to judge: no matching record, or a record with no publish receipt, and nothing is due soon. | Usually nothing yet — but check it is not a rule you have simply never set up a record for. **no-data is not a pass.** |

The last line is [invariant 5](../CLAUDE.md): a missing standing file, a missing
record, or a missing receipt is **no-data**, never a silent green. If the board
cannot prove something, it says so.

## What each row carries

Each row is defined by [`standing.schema.json`](schema/standing.schema.json):

- **`id`** — the obligation's id.
- **`state`** — `green` / `amber` / `red` / `no-data`.
- **`satisfied_by`** — the id of the record that currently satisfies the rule, or
  `null`. Set only when the obligation is actually met (green, and the still-valid
  prior review for an amber cadence); `null` for a late or missing posting.
- **`deadline`** — the date the rule is measured against (see below), or `null`
  when no deadline can be known yet.
- **`published_at`** — the server-side time the relevant record went public,
  copied verbatim from its receipt, or `null` when nothing was published.
- **`gap`** — whole days relative to the deadline. **Negative is ahead of the
  deadline; positive is overdue or late.** `null` when there is nothing to
  measure. For a posted record it is `published − deadline` (how early or late you
  posted); for an unmet, upcoming rule it is `today − deadline` (days of slack
  left, as a negative number).
- **`history`** — the rule's track record over a trailing window:
  `{window_days, evaluated, breaches}`, or `null`. See below.

## The track record, over time

A row shows the *current* standing, so one timely posting retires the red that
late ones earned — the lateness stays in the records and receipts but drops off
the live row. `history` is what a member or regulator actually asks for: not "are
you late right now" but "how often were you late?" Over a trailing window
(`--history-days`, default two years) it counts:

- **`window_days`** — the width of that window, in days.
- **`evaluated`** — how many *past* deadlines in the window had a receipt-dated
  outcome: a posting whose receipt dates it against the deadline.
- **`breaches`** — how many of those were red at the deadline (posted late, or
  for a cadence rule, the next review landed after its due date).

Two disciplines hold it honest. It is measured from receipts, **never from the
calendar alone** — a period with nothing posted has no receipt-dated outcome, so
it is simply not counted, and the board never invents a breach from "the deadline
is now past." And **a later timely posting never erases an earlier breach**: each
deadline is judged on its own. When the window holds no past, receipt-dated
deadline, `history` is `null` and the board reads "no history yet" — which is not
a zero-breach pass.

## How the deadline is computed, per rule kind

The four timing kinds are defined in
[`schema/obligation.schema.json`](schema/obligation.schema.json). The event a
`lead`/`lag` rule refers to is the matching record's `effective` date (treated as
the start of that day, UTC).

- **lead** — posted at least *N* hours or days **before** the event. Deadline =
  `event − N` (the last moment you could post and still be on time). A notice
  published after that is **red even though the record exists** — the board shows
  the late `published_at` and leaves `satisfied_by` null.

  With `weekdays_only: true`, `exclude_dates`, or both, only hours on counted
  days make up the *N*. The evaluator walks back from the event one day at a
  time. Saturdays and Sundays (with `weekdays_only`) and listed dates (with
  `exclude_dates`) are skipped, and each other day uses up 24 of the hours.
  For a 48-hour, weekdays-only rule:

  | Meeting | Deadline (start of day, UTC) | Why |
  |---|---|---|
  | Monday | the Thursday before | Sunday and Saturday are skipped; Friday and Thursday count |
  | Friday | Wednesday | no weekend in the window, so it is the same as plain 48 hours |
  | Tuesday after a Monday holiday | the Thursday before | the holiday, Sunday, and Saturday are skipped |

  So a notice posted on Friday evening for a Monday meeting is **red** under this
  rule, though it would be green under a plain 48 hours. A record carries only
  the meeting's *date*, which counts as the start of that day. That means a
  notice for a Friday meeting has to be up by the start of Wednesday: a
  Wednesday-afternoon posting is late, under a plain 48-hour rule too. Days
  are UTC calendar days, the same clock every other deadline uses. `history`
  judges each past posting the same way.
- **lag** — posted no later than *N* days **after** the event. Deadline =
  `event + N`.
- **cadence** — a fresh approved record at least every *N* months, measured from
  the last approved, *published* one. Deadline = `last review + N months` (the
  next review's due date). A matching record with no receipt cannot set the
  baseline, so a rule whose only record is unpublished is `no-data`.
- **one_off** — a single fixed `due` date. Deadline = `due`.

A rule is matched to a record by `record_type` and by a shared `subject`; among
the matches, the most recent event (latest `effective`) is the one the board
reports on.

## The amber window

`--warn-days` (default 7) is how far ahead an unmet, upcoming deadline turns
amber instead of staying quiet. It is a property of *when you run the evaluator*,
not of the rule — obligations hold only posting mechanics, never thresholds, so
the warning window is set once, by the publish workflow, for the whole board.

## Proving the board can be wrong

The tests in [`tests/`](tests/) run a fixture vault that puts every rule kind in
every state — including a notice published too late, which must come out red. A
second vault (`tests/fixtures/lead-calendar/`) judges the same postings under
plain and weekdays-only lead rules, so a Friday-evening notice for a Monday
meeting has to come out green under one rule and red under the other, and a
listed holiday has to be the thing that turns a notice late. The
expected table is written out by hand, so the evaluator cannot pass by agreeing
with itself; break the timing logic and a row stops matching. That is the same
discipline the schema examples use: a check that cannot fail proves nothing.

Run them:

```
python3 -m unittest discover -s core/tests -p "test_*.py" -v
```
