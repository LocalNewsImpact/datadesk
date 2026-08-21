#!/usr/bin/env bash
#
# Build per-state tract and place boundary files for the visual builder.
#
#   ./infra/fetch_boundaries.sh 29 42 27     # state FIPS codes
#
# Nation/state/county boundaries ship with the repo (us-atlas). Census
# tracts (~85k nationwide) and places cannot ship as one national file,
# so the runtime loads static/geo/{tracts,places}/<state-fips>.json per
# state, derived from the data's GEOID prefixes — this script builds
# those files from the Census cartographic boundary files (500k
# generalization), simplified and stripped to GEOID + NAME. Rerun with
# new FIPS codes when a dataset enters a new state; commit the output.
set -euo pipefail

YEAR="${YEAR:-2023}"
BASE="https://www2.census.gov/geo/tiger/GENZ${YEAR}/shp"
GEO="$(cd "$(dirname "$0")/.." && pwd)/static/geo"

[[ $# -gt 0 ]] || { echo "usage: $0 <state-fips> [...]" >&2; exit 1; }
command -v npx >/dev/null || { echo "needs node/npx for mapshaper" >&2; exit 1; }

mkdir -p "$GEO/tracts" "$GEO/places"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for fips in "$@"; do
  for layer in tract place; do
    plural="${layer}s"
    zip="cb_${YEAR}_${fips}_${layer}_500k.zip"
    out="$GEO/${plural}/${fips}.json"
    echo "== ${zip} -> ${out}"
    curl -sfL --retry 2 -o "$tmp/$zip" "$BASE/$zip"
    npx -y mapshaper "$tmp/$zip" \
      -simplify visvalingam 30% keep-shapes \
      -filter-fields GEOID,NAME \
      -rename-layers "$plural" \
      -o format=topojson precision=0.0001 "$out"
    wc -c "$out"
  done
done
