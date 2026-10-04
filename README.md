# Your records vault

This is the uvularia **records vault** template — the `<org>-records` repository.
It holds your organization's public records as plain Markdown, the rules about
what must be posted and when, and the workflows that publish a corpus, a public
**"Is it posted?"** board, an announcement feed, and a stamped receipt every time
you merge a change.

You do not need to be a programmer to run it. You add a record and open a pull
request; the checks and the publish happen on their own. Everything a check runs
is a short, dependency-free Python file you can read in [`core/`](core/README.md).

---

## 1. Use this template

**What you are about to do:** make your own copy of this vault in your GitHub
organization and turn on the two things that make it safe and public.

**Why bother:** this is the one repository that holds the *facts*. Getting its
reviewers and its published output set up once is what lets every later change be
a two-minute pull request.

**How long:** about ten minutes.

1. Click **Use this template → Create a new repository**. Name it
   `<your-org>-records`. Make it **public** — the vault holds only public records,
   and branch protection is free only on public repositories.
2. Edit [`index.md`](index.md) to name your organization.
3. Set your reviewers in [`.github/CODEOWNERS`](.github/CODEOWNERS) (replace the
   placeholder), then in **Settings → Branches** add a protection rule for `main`:
   require a pull request, and require the **validate** status check to pass.
4. Keep the obligation rules in [`obligations/`](obligations/README.md) that
   apply to you and delete the rest. The template ships one example.
5. Merge your first change (even just the edits above). The **publish** workflow
   runs and creates a `published` branch.
6. In **Settings → Pages**, set the source to the **`published`** branch, folder
   `/ (root)`. Your corpus, standing file, and feed are now served by URL:
   `https://<your-org>.github.io/<repo>/standing.json`, `.../feed.xml`, and
   `.../corpus-latest.json`.

**How you know it worked:** you have a green **validate** check on pull requests,
a `published` branch with a `corpus-*.json`, a `standing.json`, a `feed.xml`, and
a receipt under `receipts/`, and the three URLs above resolve.

---

## 2. Add your first record

There are two doors in, and both end in a pull request a reviewer merges. Use the
**issue form** if you have a browser and a document; use **Obsidian** if you are
already editing the vault.

> **Heads up — nothing goes live on its own.** Both doors create the record as a
> **draft**. A draft is saved and checked, but it is *not* published. It becomes
> public only when a reviewer sets `status: approved` and the `approved` date on
> the pull request and merges it. That is the review step by design: you can open
> intake freely without anything reaching the site or the board until a person
> says so.

### The quick way — the "Add a record" issue form

**What you are about to do:** file a short issue with your document attached, and
get a pull request back with the record already written.

**Why bother:** it is the no-git door. You do not clone anything, name any files,
or compute a checksum — you answer six fields and drag in the PDF. This is the
default way to post a record, and the one a new volunteer can do unaided.

**How long:** about a minute.

1. Go to the **Issues** tab → **New issue** → **Add a record**.
2. Pick the record **type**, give the **date it takes effect**, a **title**, a
   two-sentence **summary**, and the **subjects** (a comma list of topics). Drag
   the original PDF into the last box.
3. Submit. The **intake** workflow opens a branch `intake/<issue-number>` and a
   pull request *Intake for #N* with the record scaffolded, your file saved under
   `library/` with its checksum in [`library/manifest.json`](library/manifest.json),
   and the validator's output in the pull-request body. It comments the link back
   on your issue.

**How you know it worked:** within a minute the issue gets a comment with a pull
request link, and the pull request's **validate** check is green. If you could not
attach the file (a private link can need a sign-in the workflow does not have),
the pull request still opens and says plainly that the file must be added by hand —
it never fails silently. To fix a typo, just **edit the issue**; the same pull
request updates.

> **One-time setting — letting the workflow open the pull request.** GitHub ships
> new repositories with *Allow GitHub Actions to create and approve pull requests*
> **off**. While it is off, intake still scaffolds the record and pushes the branch
> `intake/<issue number>`, but it cannot open the pull request — so instead of a PR
> link the issue comment gives you a **one-click compare link** to open it yourself.
> To let intake open the PR on its own from now on, turn the setting on once:
> **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to
> create and approve pull requests** (an org admin may have to set it at the org
> level), then edit the issue to re-run.

### The editor way — Obsidian

**What you are about to do:** write one record and open a pull request for it.

**Why bother:** when you are already in the vault, editing the Markdown directly
is faster than a form. A record is one Markdown file with a small block of
structured fields at the top (the machine surface) and plain English underneath
(for people).

**How long:** a few minutes once the document is in hand.

1. Open this repository as a vault in [Obsidian](https://obsidian.md) (**Open
   folder as vault**). The tracked [`.obsidian/`](.obsidian/app.json) settings keep
   links relative and in Markdown form; the vault never depends on Obsidian, so a
   plain text editor works just as well.
2. Duplicate [`templates/record.md`](templates/record.md) into the folder for its
   type — a set of minutes goes in `records/minutes/`. Rename the file to the
   record's `id` (for example `2026-01-15-board-minutes.md`).
3. Fill in the frontmatter. Every field is explained inline and defined in
   [`core/schema/record.schema.json`](core/schema/record.schema.json). Put the
   original PDF under `library/files/<type>/`, add it to
   [`library/manifest.json`](library/manifest.json) with its checksum
   (`sha256sum <file>`), and paste that checksum into the record's `source`.
4. Commit on a branch and open a pull request (Obsidian Git, GitHub Desktop, or
   the web editor all work).

**How you know it worked:** the **validate** check goes green and posts a comment
showing the board. If a field is wrong, a broken link slips in, or a phone number
appears in the text, the check goes red and names the file and line to fix — before
anything is published. You can run the same checks yourself:

```
python3 core/validate.py .
python3 core/evaluate.py .
```

---

## 3. Read your first receipt

**What you are about to do:** open the proof of a publish.

**Why bother:** a receipt is how you *demonstrate* a record was posted, and when.
It carries the server-side timestamp of the publish — never an author's clock — so
"posted at" means what it says.

**How long:** a minute.

1. On the `published` branch, open [`receipts/`](receipts/README.md). There is one
   file per publish, named `<YYYY-MM-DDTHHMMSSZ>-<digest>.md` (the publish instant, so no two publishes share a file).
2. Read the frontmatter: the corpus **digest** everyone pins, the **published_at**
   time, the **run URL** you can click to the exact Actions run, the **commit**,
   what records changed, and how the obligations stood.
3. The table underneath says the same in plain English.

**How you know it worked:** the receipt's `published_at` matches the board's
timestamp and the Actions run, and the record you just added appears in the
corpus at that digest. Receipts are **append-only**: never edit or delete one.

---

## 4. Watch the pipeline (optional)

**What you are about to do:** connect this vault to a free Grafana Cloud account
you own, so every intake, review, and publish sends one short event there. Grafana
Cloud is a hosted dashboard service; its log store is called **Loki**.

**Why bother:** the public board tells the world what is posted. The events tell
*you* how the machine is running: when the last publish happened, which intake
stalled, and whether a review failed. **Skip this if** the board is all you need.
Nothing else depends on it, and with nothing set up every workflow stays green and
prints one line: `telemetry not configured`.

**How long:** about fifteen minutes the first time, most of it minting the token.

1. Sign up for the **free** tier at [grafana.com](https://grafana.com) and create
   a stack. On the stack's **Loki** tile, click **Details**. Note the **URL**
   (`https://logs-prod-NNN.grafana.net`) and the **User**, a number that is your
   Loki instance ID.
2. Make a token that can only *write* logs. Go to **Administration → Users and
   access → Cloud access policies → Create access policy**, pick your stack, and
   tick **`logs:write`** and nothing else. Then click **Add token** and copy it.
   It starts `glc_` and is shown once.
   > **Heads up:** this token sits in your GitHub secrets. Write-only means that
   > if it ever leaks, it can add log lines but never read yours.
3. In this repository, open **Settings → Secrets and variables → Actions**:
   - on the **Variables** tab, add `LOKI_PUSH_URL` = the URL from step 1;
   - on the **Secrets** tab, add `LOKI_WRITE_TOKEN` = `<instance-id>:<token>`,
     for example `123456:glc_eyJ…`;
   - optionally, add a `LOKI_CLUSTER` variable with your organization's short
     name. Without it, events are tagged with this repository's owner, lowercased.

Each workflow then ends with two telemetry steps. The first describes the run. The
second sends it with drosera's
[`loki-event`](https://github.com/lentago/drosera/tree/main/.github/actions/loki-event)
step. What goes out:

| Workflow | Event (`stage`) | What it carries |
|---|---|---|
| intake | `intake` | the issue number and what came of it: a pull request, a pushed branch, or a form that could not be read; plus `open` (intake items still open) and `oldest_opened_at` (when the oldest was opened, `0` when none) |
| validate | `reviewed` | the pull-request number, whether validation passed, and the board's green/amber/red counts; plus `awaiting` (pull requests whose checks are all green, waiting on a person) and `oldest_green_at` (when the longest-waiting one went green, `0` when none) |
| publish | `published` | the corpus digest, record counts, the board's counts, and the receipt's file name; plus `announcement_latency_s`, the longest time from an announcement's pull request merging to this publish (only when an announcement went live) |
| daily-snapshot | `intake`, `reviewed` | once a day, just the counts: `open` and `oldest_opened_at`, `awaiting` and `oldest_green_at` |

Every event also carries `at`, the time it is about in Unix seconds: the
publish time for a publish (the same server-side time as the receipt), the
run's own time for everything else. An "intake item" is an open "Add a record"
issue, its `intake/<N>` pull request, or both; the two together count once.
A pull request with no checks at all is not counted as green.

The counts come from [`scripts/pipeline_snapshot.py`](scripts/pipeline_snapshot.py),
which asks GitHub with the run's own token. It runs only when `LOKI_PUSH_URL` is
set. If GitHub won't answer, the count is left out and your dashboard reads
"no data" for it, never a made-up zero. The
[`daily-snapshot`](.github/workflows/daily-snapshot.yml) workflow exists so the
counts stay current on a week with no new issues or pull requests.

No names, emails, or document text are sent. An issue that isn't the "Add a
record" form sends nothing.

Sending is **best-effort**. If Grafana is down or the token is wrong, the step
shows a warning and the publish, review, or intake still finishes green. Nothing
you publish waits on it.

> **Heads up:** GitHub starts scheduled workflows a few minutes late when it is
> busy, and pauses them in a public repository with no activity for 60 days.
> If the daily counts stop arriving, check **Actions → daily-snapshot** and
> click **Enable workflow** if GitHub has switched it off.

**How you know it worked:** the next run's log shows
`loki-event: pushed log_source=uvularia_published …`. In Grafana, open **Explore**,
pick the Loki data source, and run `{source="uvularia"} | json`. Your event shows
up within a few seconds.

---

## 5. Get told when something needs you

**What you are about to do:** turn on a check that runs every 30 minutes and opens
a GitHub Issue when something needs a person. It closes the issue on its own when
the problem clears.

**Why bother:** the board shows the world what is posted, but nobody watches a
board all day. Issues are free and already where you work, and GitHub emails you
about each new one. **This works with no Grafana at all.** Grafana alerts are an
optional second channel, not a requirement.

**How long:** nothing to do for the board check. It is on as soon as this
repository exists. About two minutes more if you have an Ask box.

The [`watch`](.github/workflows/watch.yml) workflow runs
[`scripts/watch.py`](scripts/watch.py). It reads the `standing.json` and
`corpus-latest.json` you published and opens **one issue per condition**:

| Condition | Opens when | Closes when |
|---|---|---|
| an obligation is amber or red | it turns amber or red on the board (one issue per obligation; if it goes from amber to red, the same issue is updated and gets a comment) | it is green again |
| the Ask box serves an old copy | the box is still answering from an older corpus more than 30 minutes after a publish | the box serves the published corpus |
| the daily cap is past 80 % | today's questions reach 80 % of the cap | today's use is under 80 % again, normally at midnight UTC |

Each issue says in plain words what happened and what to do. Every issue has the
label `uvularia-watch` and two hidden lines at the bottom. Leave those lines alone:
they are how the next run finds the issue again, so the same problem is never
opened twice. The workflow uses the built-in token and can only read this
repository and write issues.

To turn on the two Ask box checks, open **Settings → Secrets and variables →
Actions → Variables** and add:

- `ASK_HEALTH_URL`: your Ask function's URL with `/health` on the end, for example
  `https://abc123.lambda-url.us-east-1.on.aws/health`. Without it, the run prints
  one notice (`ASK_HEALTH_URL is not set`), skips the two Ask checks, and still
  passes.
- `PUBLISHED_BASE_URL` (optional): only if you serve the `published` branch from
  your own domain. The default is `https://<your-org>.github.io/<repo>`.

> **Heads up — the watch never closes on missing data.** An issue closes only when
> the data shows the problem is gone. If the board shows *no data* for an
> obligation, or a file can't be read, the issue stays open. If the published
> files or the Ask box can't be reached at all, the run fails, and GitHub emails
> you about the failed run.

> **Heads up — GitHub pauses schedules on quiet repositories.** On a public
> repository with no commits for 60 days, GitHub turns scheduled workflows off and
> emails you first. Merging a record turns it back on, or you can click
> **Enable workflow** on the Actions tab.

**How you know it worked:** on the **Actions** tab, open **watch** and click **Run
workflow**. The run passes, and its log ends with `Nothing to open, update, or
close.` or names the issues it opened. To see an issue open and close, run it
while an obligation is amber. The issue appears with the `uvularia-watch` label,
and it closes with a "Cleared:" comment on the first run after the obligation is
green.

---

## What publishes, and what never does

On merge to `main`, [`publish.yml`](.github/workflows/publish.yml) builds and
appends to the `published` branch:

| Artifact | What it is |
|---|---|
| `corpus-<digest>.json` | the published records, in the Ask engine's entry shape — [schema](core/schema/bundle.schema.json) |
| `standing.json` | the live board data, one row per obligation — [schema](core/schema/standing.schema.json) |
| `feed.xml` | an Atom feed of your announcements |
| `receipts/<YYYY-MM-DDTHHMMSSZ>-<digest>.md` | the stamped, append-only receipt — [schema](core/schema/receipt.schema.json) |
| a provenance attestation | a signed statement that this corpus came from this commit |

Only **approved, public** records publish. Nothing in `intake/` is ever built —
it is scratch space, and the checks forbid a published page from even linking to
it. A published record is never deleted; it is superseded or retracted.

---

## About `core/` — vendored, pinned, not hand-edited

The checks and the publish step run the files in [`core/`](core/README.md): the
validator, the obligation evaluator, the bundle builder, and the schemas. These
are **vendored** — a copy of [lentago/uvularia](https://github.com/lentago/uvularia)'s
`core/`, carried in your repo so your CI depends on nothing but this repository
and a stock Python 3.12. There is no `pip install`, ever.

The copy is **pinned**: [`core/CORE_VERSION`](core/CORE_VERSION) records the exact
upstream ref it came from, and it is the only place you change to take a newer
core. Do not edit `core/*.py` by hand — if you did, your copy would quietly
diverge from the upstream the pin names. To update, bump the ref in `CORE_VERSION`
and run [`scripts/sync-core.sh`](scripts/sync-core.sh), which re-downloads `core/`
at that ref so your copy stays one `git diff` from upstream.

---

> Part of [uvularia](https://github.com/lentago/uvularia) by Lentago Labs.
> Firing us is a fork: this template runs in your org, on your account, for free.
