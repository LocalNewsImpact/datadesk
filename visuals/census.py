"""Census layers: ACS measures for the counties and tracts a map is drawn on.

A layered map (docs/LAYERED_MAP.md) shades counties or tracts by a Census
measure the map's author chose. The measures an author can choose from are
`VARIABLES`: twenty-eight from the ACS 5-year data profiles, each under a
plain name, each saying whether it holds at tract level. They are fetched
from the Census API by `fetch_census_layers` and kept in `CensusValue`.

The estimates are survey estimates. Each carries a 90% margin of error, and
in a small county or most tracts the margin on a small share is larger than
the share: a tract map of foreign-born residents in rural Missouri is mostly
noise. `unreliable` says which cells are (coefficient of variation over 30%,
the Census Bureau's own line), and the renderer hatches them instead of
shading them. On 2026-09-30, nine of the twenty-eight measures were
unreliable in more than a third of Missouri's tracts; those are marked
`tract=False` and offered only by county.

    CENSUS_API_KEY=... python manage.py fetch_census_layers --level county
"""

from __future__ import annotations

import json
import math
import os
import urllib.request

from django.db import transaction

from visuals.models import CensusValue

YEAR = 2024
API = "https://api.census.gov/data"
PRODUCT = "acs/acs5/profile"
LEVELS = ("county", "tract")
#: The API takes at most 50 variables in one call.
BATCH = 48
#: A 90% margin more than 30% of the estimate: not shown as a value.
UNRELIABLE_CV = 0.30
#: The API says "not applicable" and "not available" with these.
MISSING = -222222222


def _v(key, label, group, codes, kind="share", tract=True):
    return {
        "key": key,
        "label": label,
        "group": group,
        "codes": codes if isinstance(codes, list) else [codes],
        #: share: a percent exists and is what a map shows by default;
        #: value: a count, a median or a mean, mapped as it is.
        "kind": kind,
        #: Reliable enough at tract level to be offered there.
        "tract": tract,
    }


#: What a map's author can shade by. Codes are ACS 2024 5-year data-profile
#: estimates; the M, PE and PM columns beside each are fetched with it.
VARIABLES = [
    _v("total_population", "Total population", "Population", "DP05_0001E", "value"),
    _v("median_age", "Median age", "Population", "DP05_0018E", "value"),
    _v("under_18", "Under 18", "Population", "DP05_0019E"),
    _v("age_65_and_over", "65 and over", "Population", "DP05_0024E"),
    _v(
        "white_not_hispanic",
        "White alone, not Hispanic",
        "Race and ethnicity",
        "DP05_0096E",
    ),
    _v(
        "black_not_hispanic",
        "Black alone, not Hispanic",
        "Race and ethnicity",
        "DP05_0097E",
        tract=False,
    ),
    _v(
        "hispanic",
        "Hispanic or Latino",
        "Race and ethnicity",
        "DP05_0090E",
        tract=False,
    ),
    _v("foreign_born", "Foreign born", "Population", "DP02_0094E", tract=False),
    _v(
        "language_other_than_english",
        "Language other than English at home",
        "Population",
        "DP02_0114E",
        tract=False,
    ),
    _v("veterans", "Veterans", "Population", "DP02_0070E", tract=False),
    _v("households", "Households", "Households", "DP02_0001E", "value"),
    _v(
        "households_with_computer",
        "Households with a computer",
        "Households",
        "DP02_0153E",
    ),
    _v(
        "households_with_broadband",
        "Households with broadband",
        "Households",
        "DP02_0154E",
    ),
    _v(
        "high_school_or_higher",
        "High school graduate or higher",
        "Education",
        "DP02_0067E",
    ),
    _v("bachelors_or_higher", "Bachelor's degree or higher", "Education", "DP02_0068E"),
    _v(
        "median_household_income",
        "Median household income",
        "Income and work",
        "DP03_0062E",
        "value",
    ),
    _v(
        "per_capita_income",
        "Per capita income",
        "Income and work",
        "DP03_0088E",
        "value",
    ),
    _v("in_labor_force", "In labor force", "Income and work", "DP03_0002E"),
    _v(
        "unemployment_rate",
        "Unemployment rate",
        "Income and work",
        "DP03_0009E",
        tract=False,
    ),
    _v(
        "below_poverty",
        "Below poverty level",
        "Income and work",
        "DP03_0128E",
        tract=False,
    ),
    _v(
        "no_health_insurance",
        "No health insurance",
        "Income and work",
        "DP03_0099E",
        tract=False,
    ),
    _v(
        "mean_travel_time",
        "Mean travel time to work (minutes)",
        "Income and work",
        "DP03_0025E",
        "value",
    ),
    _v("housing_units", "Housing units", "Housing", "DP04_0001E", "value"),
    _v("vacant_housing", "Vacant housing units", "Housing", "DP04_0003E", tract=False),
    _v("owner_occupied", "Owner-occupied", "Housing", "DP04_0046E"),
    _v("median_home_value", "Median home value", "Housing", "DP04_0089E", "value"),
    _v("median_gross_rent", "Median gross rent", "Housing", "DP04_0134E", "value"),
    # Decades summed: the profile carries each decade on its own row.
    _v(
        "built_before_1980",
        "Housing built before 1980",
        "Housing",
        ["DP04_0022E", "DP04_0023E", "DP04_0024E", "DP04_0025E", "DP04_0026E"],
    ),
]
BY_KEY = {v["key"]: v for v in VARIABLES}


def offered(level):
    """The variables a map at this level can shade by."""
    return [v for v in VARIABLES if level == "county" or v["tract"]]


def unreliable(estimate, moe):
    """Whether a 90% margin is too wide for the estimate to be shown.

    None when there is no estimate to judge; a zero estimate is a value the
    Census stands behind (nobody counted), not an unreliable one.
    """
    if estimate is None:
        return None
    if estimate == 0 or moe is None:
        return False
    return abs(moe / 1.645 / estimate) > UNRELIABLE_CV


def columns(variable):
    """The API columns a variable needs: estimate and margin, and the
    percent pair where the measure is a share."""
    out = []
    for code in variable["codes"]:
        base = code[:-1]
        out += [base + "E", base + "M"]
        if variable["kind"] == "share":
            out += [base + "PE", base + "PM"]
    return out


def _number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return None if value <= MISSING else value


def _rss(values):
    """Margins add as the root of the sum of squares."""
    known = [v for v in values if v is not None]
    return math.sqrt(sum(v * v for v in known)) if known else None


def _sum(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _get(url):
    with urllib.request.urlopen(url, timeout=180) as resp:
        return json.load(resp)


def _geoid(row):
    return row["state"] + row["county"] + row.get("tract", "")


def fetch(level, state="29", year=YEAR, variables=None, get=None):
    """Every variable for every county or tract of a state, from the API.

    Returns rows {geoid, variable, estimate, moe, percent, percent_moe}. A
    composite variable (decades summed) is summed here, its margin taken as
    the root of the sum of squares. Batched under the API's 50-column limit.
    """
    if level not in LEVELS:
        raise ValueError(f"level is one of {', '.join(LEVELS)}")
    variables = variables or VARIABLES
    get = get or _get
    key = os.environ.get("CENSUS_API_KEY", "")
    if get is _get and not key:
        raise RuntimeError(
            "CENSUS_API_KEY is not set; the Census API refuses calls without one"
        )
    wanted = []
    for v in variables:
        wanted += [c for c in columns(v) if c not in wanted]
    geo = (
        f"for={level}:*&in=state:{state}"
        if level == "county"
        else f"for=tract:*&in=state:{state}"
    )
    cells = {}
    for i in range(0, len(wanted), BATCH):
        chunk = wanted[i : i + BATCH]
        url = f"{API}/{year}/{PRODUCT}?get={','.join(chunk)}&{geo}"
        if key:
            url += f"&key={key}"
        table = get(url)
        header = table[0]
        for values in table[1:]:
            row = dict(zip(header, values, strict=True))
            cells.setdefault(_geoid(row), {}).update(row)
    out = []
    for geoid, row in cells.items():
        for v in variables:
            bases = [c[:-1] for c in v["codes"]]
            estimate = _sum(_number(row.get(b + "E")) for b in bases)
            moe = _rss(_number(row.get(b + "M")) for b in bases)
            percent = percent_moe = None
            if v["kind"] == "share":
                percent = _sum(_number(row.get(b + "PE")) for b in bases)
                percent_moe = _rss(_number(row.get(b + "PM")) for b in bases)
            out.append(
                {
                    "geoid": geoid,
                    "variable": v["key"],
                    "estimate": estimate,
                    "moe": moe,
                    "percent": percent,
                    "percent_moe": percent_moe,
                }
            )
    return out


def store(rows, level, year=YEAR):
    """Write fetched rows, replacing what was stored for the same cells."""
    objs = [CensusValue(year=year, level=level, **r) for r in rows]
    before = CensusValue.objects.filter(
        year=year, level=level, variable__in={r["variable"] for r in rows}
    ).count()
    with transaction.atomic():
        CensusValue.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["year", "level", "geoid", "variable"],
            update_fields=["estimate", "moe", "percent", "percent_moe", "fetched_at"],
            batch_size=1000,
        )
    return {"rows": len(objs), "before": before}


def reliability(level, year=YEAR):
    """Per variable, the share of places whose stored estimate is unreliable.

    What decided the `tract` flags, re-measurable after a fetch.
    """
    out = []
    for v in VARIABLES:
        qs = CensusValue.objects.filter(year=year, level=level, variable=v["key"])
        shown = hatched = 0
        for cell in qs.values_list("estimate", "moe", "percent", "percent_moe"):
            e, m = (cell[2], cell[3]) if v["kind"] == "share" else (cell[0], cell[1])
            bad = unreliable(e, m)
            if bad is None:
                continue
            shown += 1
            hatched += bad
        out.append(
            {
                "key": v["key"],
                "label": v["label"],
                "places": shown,
                "hatched": hatched,
                "share": round(hatched / shown, 3) if shown else None,
            }
        )
    return out
