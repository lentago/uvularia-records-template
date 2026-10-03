# obligations/ — the rules the board is built from

An **obligation** is a rule about records: a record of some type must be posted
on some schedule. The publish step evaluates every rule here against your records
and their receipts, and the public **"Is it posted?"** board is one row per rule,
green / amber / red / no-data. The full field reference is in
[`../core/schema/obligation.schema.json`](../core/schema/obligation.schema.json)
and [`../core/evaluate.md`](../core/evaluate.md).

Each rule names which record `type` satisfies it and exactly **one** timing kind:

| Kind | Means | Example |
|---|---|---|
| `lead` | posted at least this far *before* the event | a 48-hour meeting notice |
| `lag` | posted no later than this many days *after* the event | minutes within 30 days |
| `cadence` | a fresh record at least every N months | a policy reviewed annually |
| `one_off` | a single fixed due date | a filing due this year |

Obligations encode **posting mechanics, not legal interpretation.** The
not-legal-advice disclaimer lives in your Ask-rules repo's `policy.yaml`; each
rule points at it with `disclaimer_ref`.

## Import a rule from a jurisdiction pack

uvularia ships obligation packs — ready-made rules with their statutory
citations — under
[`obligations/packs/`](https://github.com/lentago/uvularia/tree/main/obligations/packs)
(Massachusetts first). To use one, **copy its file into this folder** and adjust
`subjects` to match the records it should watch. Copying, rather than referencing
a remote file, keeps your vault self-contained: your CI needs nothing but this
repo.

This folder already contains one imported example,
[`ma-oml-minutes-timely.yaml`](ma-oml-minutes-timely.yaml), copied from the
Massachusetts pack's `ma-oml-minutes-timely` rule. It says minutes are posted
within 30 days of a meeting. Until you add a `minutes` record it shows **no-data**
on the board — honestly, not a green.

**Keep the rules that apply to you and delete the rest.** A rule you leave here
that does not apply to your organization will put a row on your public board that
you then have to explain. If a rule's day count differs from your governing
documents, change it here and note why in the plain-English description your
board secretary maintains.
