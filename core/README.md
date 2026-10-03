# core — the vault validator and obligation evaluator

Two single-file programs stand between a mistake and the public site or the Ask
box. Both are Python 3.12, **standard library only** — no `pip install`, ever.

- **`validate.py`** — checks a vault before it is published (this page).
- **`evaluate.py`** — turns records and obligation rules into live green / amber /
  red standing. See [evaluate.md](evaluate.md).
- **`schema/`** — the JSON Schemas every document validates against, and their
  own checker. See [schema/README.md](schema/README.md).

---

## validate.py — check a vault before publishing

**What it does.** You point it at a vault root and it runs seven checks; on any
problem it prints a plain-English line naming the file and what to change, and
exits non-zero so the pull request stays red. **Why bother:** a bad field, a
broken link, a resident's phone number, or a quietly deleted record is caught on
the PR, before it can reach the public site or the Ask box. **How long:** well
under a second on a few hundred records. **How you know it worked:** it prints
`All enabled checks passed.` and exits `0`; otherwise it lists each problem and
exits `1`.

Run it yourself from the vault root:

```
python3 core/validate.py .
```

or point it anywhere:

```
python3 core/validate.py path/to/vault
```

The vault layout it expects (CLAUDE.md § Artifacts): `intake/`, `records/<type>/`,
`library/files/` with a `library/manifest.json`, `obligations/`, `receipts/`, and
`index.md`.

### The seven checks

| # | Check | What it enforces |
|---|---|---|
| 1 | `schema` | Every `records/**/*.md` has frontmatter that validates against [`schema/record.schema.json`](schema/record.schema.json). |
| 2 | `approved_source` | A `status: approved` record has `source` and `approved`; its `source.file` exists under `library/`, and its sha256 matches both the frontmatter and `library/manifest.json`. |
| 3 | `visibility` | Every record is `visibility: public` — the vault is public and holds only public records. |
| 4 | `links` | Relative Markdown links in `records/` and `index.md` resolve. |
| 5 | `privacy` | A denylist (names, unit numbers) plus email and phone patterns, scanned in record bodies and any text under `library/text/`. A match fails the build and prints the file and line — **never the matched value in full**. |
| 6 | `no_delete` | No record that exists in the base git ref (default `origin/main`) has been removed from the tree. Retract or supersede instead; never delete. |
| 7 | `intake_isolation` | Nothing in `records/` or `index.md` links into `intake/`, which is never published. |

These are the invariants from `CLAUDE.md` made mechanical. They are checks, not
advice: the PR cannot merge until they pass, and none of them is weakened to make
a build green.

### Turning checks on and off

Drop a `validate.toml` at the vault root. Every check defaults to **on**; omit
the file to accept all defaults. Each check is switched independently:

```toml
[checks]
schema = true
approved_source = true
visibility = true
links = true
privacy = true
no_delete = true
intake_isolation = true

[privacy]
denylist = ["Jane Q. Resident", "Unit 4B"]   # literal strings, case-insensitive
denylist_file = "privacy-denylist.txt"        # optional: one per line
emails = true                                 # flag email-address patterns
phones = true                                 # flag phone-number patterns

[no_delete]
base_ref = "origin/main"                      # the ref to compare against
```

**Keep sensitive names out of the public repo.** The inline `denylist` lives in
`validate.toml`, which is committed. For real resident names or unit numbers, put
them in the file named by `denylist_file` and git-ignore that file — the check
reads it but it is never published. When a tripwire fires it reports the file and
line and withholds the matched value, so the message itself leaks nothing.

The `no_delete` check needs the base ref present locally. In CI, fetch it (see
the workflow below); if the ref cannot be resolved the check prints a visible
notice and skips rather than failing every PR — it never fakes a pass.

### In CI

[`.github/workflows/validate.yml`](../.github/workflows/validate.yml) runs this
validator's own tests on every pull request, with no path filter (a required
check that never triggers would deadlock every other PR). The vault template
wires `validate.py` into its own publish-gate workflow against the candidate
tree; this repo's workflow proves the validator behaves.

### Run the tests

```
python3 -m unittest core.tests.test_validate -v
```

One fixture builds a vault that passes every check; each of the others mutates a
copy to trip exactly one check, which is how we prove each check can actually
fail rather than passing vacuously — including that a privacy match never prints
the value it caught.
