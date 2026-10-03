# intake/ — scratch space, never published

This folder is for work in progress: a draft you are not ready to show, notes
toward a record, a PDF you have not described yet. **Nothing in `intake/` is ever
built, published, or read by the Ask box.** It does not reach the corpus bundle,
the site, the feed, or the board.

That is enforced, not just promised:

- The publish step only ever reads `records/`, `library/`, `obligations/`, and
  `receipts/`. It never looks in here.
- The `intake_isolation` check fails the build if any record or `index.md` links
  into `intake/`, so a published page can never point a reader at a draft.

**Why it exists.** You want somewhere inside the vault to park a half-finished
record without it going live and without it affecting the board. When a draft is
ready, move it into the right `records/<type>/` folder, fill in its frontmatter
from [`../templates/record.md`](../templates/record.md), and open a pull request.
Moving it out of `intake/` is what makes it real.

You can keep whatever structure you like in here. It is yours.
