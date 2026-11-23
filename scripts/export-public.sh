#!/usr/bin/env bash
# Builds the public copy of this repository: one fresh commit, no history,
# without the private notes. Usage: scripts/export-public.sh /path/to/empty-dir
set -euo pipefail
out="${1:?usage: export-public.sh OUTPUT_DIR}"
[ ! -e "$out" ] || [ -z "$(ls -A "$out")" ] || { echo "$out is not empty"; exit 1; }
mkdir -p "$out"
cd "$(dirname "$0")/.."
git archive HEAD | tar -x -C "$out"
for p in agent docs/x-replies.md docs/show-hn.md docs/RUNBOOK.md docs/email-baseten.md docs/listings.md docs/launch-posts.md docs/upstream.md docs/opensats.md docs/nostr docs/faro.md docs/chat-design.md; do
  [ ! -e "$out/$p" ] || rm -r -- "${out:?}/$p"
done
cd "$out"
if grep -rIl --exclude=export-public.sh -E 'nsec1[0-9a-z]{20,}|PRIVATE KEY-----|@gmail\.com|bot[0-9]{8,}:|/home/[a-z]+/' . ; then
  echo "Refusing: something that looks private is in the export."; exit 1
fi
git init -q -b main
git add -A
git -c user.name=Lampo -c user.email=contact@lampo.run commit -q -m "Lampo: a public pi402 seller (Aperture + Lightmeter + Wavelength)"
echo "Public copy ready in $out ($(git ls-files | wc -l) files)"
