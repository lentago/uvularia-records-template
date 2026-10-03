#!/usr/bin/env bash
# Re-vendor core/ from the pinned ref in core/CORE_VERSION.
#
# What it does: downloads lentago/uvularia at the pinned ref and replaces your
# core/ with that ref's core/ (the validator, the evaluator, the bundle builder,
# and the schemas). Why bother: your CI runs this exact code, so you want it to
# be a clean copy of a known upstream ref — never a hand-edited fork. How long:
# a few seconds. How you know it worked: `git diff core/` shows the upstream
# change you expected and nothing else; `python3 core/schema/check_examples.py`
# exits 0.
#
# Update core: edit the `ref` in core/CORE_VERSION to a newer tag, run this,
# review the diff, commit. Requires git and a network connection.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pin="$here/core/CORE_VERSION"
[ -f "$pin" ] || { echo "error: $pin not found; run this from a vault made from the template" >&2; exit 1; }

repo="$(sed -n 's/^[[:space:]]*repo[[:space:]]*=[[:space:]]*//p' "$pin" | head -1)"
ref="$(sed -n 's/^[[:space:]]*ref[[:space:]]*=[[:space:]]*//p' "$pin" | head -1)"
: "${repo:?CORE_VERSION is missing a repo = line}"
: "${ref:?CORE_VERSION is missing a ref = line}"

echo "Syncing core/ from $repo @ $ref ..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git clone --quiet --depth 1 --branch "$ref" "https://github.com/$repo.git" "$tmp/src" 2>/dev/null \
  || git clone --quiet "https://github.com/$repo.git" "$tmp/src"
git -C "$tmp/src" checkout --quiet "$ref"

# Replace everything under core/ except the pin file itself.
find "$here/core" -mindepth 1 -not -name CORE_VERSION -not -path "$here/core" -delete
cp "$tmp/src/core/validate.py" "$tmp/src/core/evaluate.py" "$tmp/src/core/bundle.py" \
   "$tmp/src/core/README.md" "$tmp/src/core/evaluate.md" "$here/core/"
mkdir -p "$here/core/schema/examples"
cp "$tmp/src"/core/schema/*.schema.json "$tmp/src/core/schema/check_examples.py" \
   "$tmp/src/core/schema/README.md" "$here/core/schema/"
cp "$tmp/src"/core/schema/examples/* "$here/core/schema/examples/"

echo "Done. Review 'git diff core/' and run: python3 core/schema/check_examples.py"
