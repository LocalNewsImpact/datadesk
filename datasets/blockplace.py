"""Block-to-place assignment, fetched per state and then read locally.

WHY THIS EXISTS. A block GEOID encodes state, county, tract and block
group and never the city -- a place is a separate geography that cuts
across tracts, so the digits cannot be parsed into one. Everything else
in `datasets.geo` walks UP a code by slicing it; this is the one hop that
needs a table.

It is what lets a story coded to a block be counted under its city.
Without it a block-coded story falls back to its county, which on a map
of cities means it is simply absent.

    290190011064015  ->  2915670  Columbia, MO
    291833119081051  ->  2940043  Lake St. Louis, MO

MEASURED, Missouri 2020: 253,633 blocks from a 4.3 MB download, of which
119,139 are in a place and 134,493 -- 53% -- are unincorporated and in
none. That second number is the reason "no city" has to be a stored
answer rather than a missing row.
"""

from __future__ import annotations

import csv
import io
import zipfile
from urllib.request import urlopen

from django.db import transaction

#: The Census publishes one archive per state. 2020 is the vintage the
#: rest of this repository's gazetteers are cut from, so blocks, places
#: and counties all describe the same decade.
VINTAGE = "2020"
_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/baf{vintage}/"
    "BlockAssign_ST{fips}_{usps}.zip"
)
#: The component that maps blocks to incorporated places and CDPs. The
#: same archive carries school districts, voting districts and
#: legislative districts, which are not this question.
_MEMBER = "BlockAssign_ST{fips}_{usps}_INCPLACE_CDP.txt"

#: Rows per INSERT. Missouri is a quarter of a million rows and a single
#: bulk_create of that size is a large statement and a large amount of
#: memory held at once.
BATCH = 5000


def url_for(state_fips: str, usps: str, vintage: str = VINTAGE) -> str:
    return _URL.format(vintage=vintage, fips=state_fips, usps=usps.upper())


def is_loaded(state_fips: str) -> bool:
    """Whether this state's crosswalk has been fetched.

    An empty result and an unfetched state are different answers, and
    only one of them should send us back to census.gov.
    """
    from datasets.models import BlockPlaceLoad

    return BlockPlaceLoad.objects.filter(state_fips=state_fips).exists()


def parse(archive_bytes: bytes, state_fips: str, usps: str):
    """(block_geoid, place_geoid) for every block in the archive.

    `place_geoid` is "" where PLACEFP is blank -- unincorporated, and the
    majority of any state. The file gives a 5-digit place code; a place
    GEOID is the state FIPS in front of it.
    """
    member = _MEMBER.format(fips=state_fips, usps=usps.upper())
    with (
        zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive,
        archive.open(member) as handle,
    ):
        text = io.TextIOWrapper(handle, encoding="utf-8")
        for row in csv.DictReader(text, delimiter="|"):
            block = (row.get("BLOCKID") or "").strip()
            if not block:
                continue
            placefp = (row.get("PLACEFP") or "").strip()
            yield block, (f"{state_fips}{placefp}" if placefp else "")


def load_state(state_fips: str, usps: str, *, fetch=None, vintage: str = VINTAGE):
    """Fetch and store one state's crosswalk. Returns its BlockPlaceLoad.

    Idempotent: a state already loaded is returned untouched rather than
    re-downloaded, which is what makes this safe to call from a request
    path that cannot know whether it is the first.
    """
    from datasets.models import BlockPlace, BlockPlaceLoad

    existing = BlockPlaceLoad.objects.filter(state_fips=state_fips).first()
    if existing is not None:
        return existing

    source = url_for(state_fips, usps, vintage)
    getter = fetch or (lambda u: urlopen(u, timeout=120).read())  # noqa: S310
    rows = list(parse(getter(source), state_fips, usps))

    with transaction.atomic():
        # Replace rather than merge: a re-load of a state means its
        # vintage changed, and half-old half-new assignments would put
        # one story in two cities.
        BlockPlace.objects.filter(state_fips=state_fips).delete()
        BlockPlace.objects.bulk_create(
            (
                BlockPlace(block_geoid=block, place_geoid=place, state_fips=state_fips)
                for block, place in rows
            ),
            batch_size=BATCH,
        )
        return BlockPlaceLoad.objects.create(
            state_fips=state_fips,
            usps=usps.upper(),
            blocks=len(rows),
            in_a_place=sum(1 for _, place in rows if place),
            vintage=vintage,
            source_url=source,
        )


def place_for_block(block_geoid: str) -> str | None:
    """The place GEOID a block sits in, or None.

    None means three different things and the caller usually wants them
    to behave the same: not a block, the state is not loaded, or the
    block is unincorporated. `is_loaded` separates the second when it
    matters -- deciding whether to go and fetch.
    """
    from datasets.models import BlockPlace

    code = str(block_geoid or "").strip()
    if len(code) != 15:
        return None
    row = (
        BlockPlace.objects.filter(block_geoid=code)
        .values_list("place_geoid", flat=True)
        .first()
    )
    return row or None


def places_for_blocks(block_geoids) -> dict[str, str]:
    """{block_geoid: place_geoid} for the blocks that are in one.

    ONE QUERY, NOT ONE PER ROW. The caller is relabelling a result set,
    and `place_for_block` in a loop is the N+1 that makes a map page slow
    for the sake of a handful of block-coded points.

    Blocks that are unincorporated, or whose state is not loaded, are
    simply absent from the result -- the caller falls back either way.
    """
    from datasets.models import BlockPlace

    codes = {str(g or "").strip() for g in block_geoids}
    codes = {c for c in codes if len(c) == 15}
    if not codes:
        return {}
    return {
        block: place
        for block, place in BlockPlace.objects.filter(
            block_geoid__in=codes
        ).values_list("block_geoid", "place_geoid")
        if place
    }


def city_geoid(geoid: str, level: str | None = None, *, blocks=None) -> str | None:
    """The place GEOID a coded point should be counted under, or None.

    THE AGGREGATION KEY FOR A MAP OF CITIES. A story coded to a place is
    already there; one coded to a block is in a city the block knows and
    the code does not; one coded to a county or a state is in no city at
    all and must not be invented into one.

    `blocks` is the result of `places_for_blocks`, so a caller ranging
    over rows does the lookup once.
    """
    code = str(geoid or "").strip()
    if not code:
        return None
    if level == "place" or (level is None and len(code) == 7):
        return code
    if level == "block" or (level is None and len(code) == 15):
        if blocks is not None:
            return blocks.get(code)
        return place_for_block(code)
    # County and state are coarser than a city. There is no honest way
    # down, and guessing one would put stories in a city they were never
    # coded to.
    return None


def ladder(geoid: str, level: str | None = None, *, blocks=None) -> dict:
    """Every rung a coding can reach, as codes and as names.

    NAME EVERY LEVEL YOU CAN, NOT JUST THE FINEST. A row that carries only
    its own label can be shown but not aggregated: a story coded to a
    block cannot be counted by county, and one coded to a place cannot be
    counted by state, even though both facts are derivable. Filling the
    ladder once, here, is what lets a map group by whichever rung it
    wants.

    Which rungs are reachable is not symmetric, and the asymmetry is the
    whole reason this is a function rather than three slices:

        from a BLOCK   state and county come from the digits;
                       city needs the block-to-place crosswalk
        from a PLACE   state comes from the digits; city IS the code;
                       COUNTY needs the place-to-county crosswalk,
                       because a place can straddle county lines
        from a COUNTY  state and county from the digits; no city
        from a STATE   state only

    `blocks` is a prepared `places_for_blocks` result, so a caller ranging
    over rows pays one query rather than one per row.
    """
    from datasets.geo import county_label, state_label, to_county, to_state
    from datasets.places import place_label

    code = str(geoid or "").strip()
    state = to_state(code)
    county = to_county(code, level)
    city = city_geoid(code, level, blocks=blocks)
    return {
        "state_geoid": state,
        "state": state_label(state) if state else None,
        "county_geoid": county,
        # county_label echoes the code back when it cannot name it, which
        # would put a FIPS in a column of names.
        "county": (
            county_label(county) if county and county_label(county) != county else None
        ),
        "city_geoid": city,
        "city": place_label(city) if city else None,
    }
