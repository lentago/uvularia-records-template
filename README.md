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
   file per publish, named `YYYY-MM-DD-<digest>.md`.
2. Read the frontmatter: the corpus **digest** everyone pins, the **published_at**
   time, the **run URL** you can click to the exact Actions run, the **commit**,
   what records changed, and how the obligations stood.
3. The table underneath says the same in plain English.

**How you know it worked:** the receipt's `published_at` matches the board's
timestamp and the Actions run, and the record you just added appears in the
corpus at that digest. Receipts are **append-only**: never edit or delete one.

---

## What publishes, and what never does

On merge to `main`, [`publish.yml`](.github/workflows/publish.yml) builds and
appends to the `published` branch:

| Artifact | What it is |
|---|---|
| `corpus-<digest>.json` | the published records, in the Ask engine's entry shape — [schema](core/schema/bundle.schema.json) |
| `standing.json` | the live board data, one row per obligation — [schema](core/schema/standing.schema.json) |
| `feed.xml` | an Atom feed of your announcements |
| `receipts/YYYY-MM-DD-<digest>.md` | the stamped, append-only receipt — [schema](core/schema/receipt.schema.json) |
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
