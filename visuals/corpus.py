"""The corpus as a visual data source (SCOPE.md §2.6 v2).

Charts of the research corpus need aggregation, not raw rows: "stories
per county" over 15–20k articles is a GROUP BY, and the published
snapshot should hold the few hundred aggregated rows rather than every
story — otherwise each embed downloads megabytes to draw one map.

So the pivot runs here, in Postgres, through the read-only role. A spec
names one or two dimensions, one measure, and filters; the result is a
small table the chart runtime can draw directly.

Geography is exposed only where the rollup is truthful. A place GEOID is
state(2) + place(5) and contains no county code, so place-coded rows
cannot roll up to a county — the county dimension therefore restricts
itself to county/tract/block codings and says how many rows that drops.
"""

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import Substr, TruncMonth, TruncYear

from explorer.models import Article, DatasetSource

# --- dimensions -------------------------------------------------------------
#
# expr: an ORM expression grouped on. requires: an optional filter that
# keeps only rows where the grouping is meaningful. note: shown in the UI.

DIMENSIONS = {
    "dataset": {
        "label": "Dataset",
        "expr": F("candidate_link__source__memberships__dataset__label"),
        "note": "A source in two datasets counts once per dataset.",
    },
    "publisher": {
        "label": "Publisher",
        "expr": F("candidate_link__source__host_norm"),
    },
    "publisher_city": {
        "label": "Publisher city",
        "expr": F("candidate_link__source__city"),
    },
    "publisher_county": {
        "label": "Publisher county",
        "expr": F("candidate_link__source__county"),
    },
    "status": {"label": "Article status", "expr": F("status")},
    "wire": {"label": "Wire state", "expr": F("wire_check_status")},
    # The CIN taxonomy, as the classifier records it on the article:
    # a primary need and its runner-up. Their cross-tab is the chord.
    "cin_primary": {"label": "CIN (primary)", "expr": F("primary_label")},
    "cin_alternate": {"label": "CIN (alternate)", "expr": F("alternate_label")},
    "month": {"label": "Month published", "expr": TruncMonth("publish_date")},
    "year": {"label": "Year published", "expr": TruncYear("publish_date")},
    # Enrichment dimensions — the CIN taxonomy.
    "scope": {"label": "Scope", "expr": F("enrichment__scope")},
    "subject": {"label": "Subject", "expr": F("enrichment__subject")},
    "topic": {"label": "Topic", "expr": F("enrichment__topic")},
    "format": {"label": "Format", "expr": F("enrichment__format")},
    "timeframe": {"label": "Timeframe", "expr": F("enrichment__timeframe")},
    "user_need": {
        "label": "User need (CIN)",
        "expr": F("enrichment__user_need"),
    },
    "model": {"label": "Enrichment model", "expr": F("enrichment__model")},
    "skip_reason": {"label": "Skip reason", "expr": F("enrichment__skip_reason")},
    "geo_skip_reason": {
        "label": "Geo skip reason",
        "expr": F("enrichment__geo_skip_reason"),
    },
    # Geography. Only rollups the codings support.
    "geo_state": {
        "label": "State FIPS (from point)",
        "expr": Substr("enrichment__point_geoid", 1, 2),
        "requires": Q(enrichment__point_geoid__isnull=False),
        "geo_level": "states",
        "note": "First two digits of the point GEOID — valid at every coding.",
    },
    "geo_county": {
        "label": "County FIPS (from point)",
        # Grouped in SQL by the raw point coding, then folded to counties
        # in Python: place GEOIDs carry no county code and resolve through
        # the Census place-to-county crosswalk.
        "expr": F("enrichment__point_geoid"),
        "with": ("enrichment__point_geoid_level",),
        "rollup": "county",
        "requires": Q(enrichment__point_geoid__isnull=False),
        "geo_level": "counties",
        "note": (
            "County/tract/block codings carry a county directly; place codings "
            "resolve through the Census place-to-county crosswalk (4.1% of "
            "places straddle two counties and take the first the Census "
            "lists). State-only codings have no county and are dropped."
        ),
    },
    "geo_place": {
        "label": "Place GEOID (from point)",
        "expr": F("enrichment__point_geoid"),
        "requires": Q(enrichment__point_geoid_level="place"),
        "geo_level": "places",
        "note": "Place-coded rows only — the corpus's most common coding.",
    },
    "point_place": {
        "label": "Place name (from point)",
        "expr": F("enrichment__point_place"),
    },
}

# --- measures ---------------------------------------------------------------

MEASURES = {
    "articles": {
        "label": "Articles",
        "agg": lambda: Count("id", distinct=True),
        "combine": "sum",
    },
    "cost_sum": {
        "label": "Cost (sum, USD)",
        "agg": lambda: Sum("enrichment__cost_usd"),
        "combine": "sum",
    },
    "cost_avg": {
        "label": "Cost (average, USD)",
        "agg": lambda: Avg("enrichment__cost_usd"),
        "combine": "mean",
        "weight": lambda: Count("enrichment__cost_usd"),
    },
    "confidence_avg": {
        "label": "Scope confidence (average)",
        "agg": lambda: Avg("enrichment__scope_confidence"),
        "combine": "mean",
        "weight": lambda: Count("enrichment__scope_confidence"),
    },
    "cin_confidence_avg": {
        "label": "CIN confidence (average)",
        "agg": lambda: Avg("primary_label_confidence"),
        "combine": "mean",
        "weight": lambda: Count("primary_label_confidence"),
    },
    "publishers": {
        "label": "Distinct publishers",
        "agg": lambda: Count("candidate_link__source_id", distinct=True),
        # A distinct count cannot be recombined from group totals without
        # double-counting, so it is refused alongside a rollup dimension.
        "combine": None,
    },
}

# --- filters ----------------------------------------------------------------

MAX_GROUPS = 5000
# A rolled-up dimension groups on the raw coding first, which yields more
# rows than the folded result; allow headroom before folding.
MAX_RAW_GROUPS = 50_000


def measure_label_for(key):
    return MEASURES[key]["label"]


def _fold(rows, dim_keys, extra, rollups, measure_key, measures):
    """Fold raw-coding groups into their rollup buckets.

    Sums add; means recombine weighted by the count that produced them,
    so a county's average is the average over its articles rather than
    the average of its places' averages.
    """
    from datasets.geo import to_county, to_state

    mappers = {"county": to_county, "state": to_state}
    combine = measures[measure_key].get("combine")
    folded = {}
    for row in rows:
        key_parts = []
        dropped = False
        for key in dim_keys:
            value = row[key]
            if rollups.get(key):
                level = row.get(f"{key}__with0")
                value = mappers[rollups[key]](value, level)
                if value is None:
                    dropped = True
                    break
            key_parts.append(value)
        if dropped:
            continue
        bucket = folded.setdefault(
            tuple(key_parts),
            dict(zip(dim_keys, key_parts, strict=True)) | {measure_key: 0, "_w": 0},
        )
        value = row[measure_key] or 0
        weight = row.get("_w", 1) or 0
        if combine == "mean":
            bucket[measure_key] += value * weight
            bucket["_w"] += weight
        else:
            bucket[measure_key] += value
    out = []
    for bucket in folded.values():
        if combine == "mean":
            bucket[measure_key] = (
                bucket[measure_key] / bucket["_w"] if bucket["_w"] else None
            )
        bucket.pop("_w", None)
        out.append(bucket)
    return out


def _base_queryset(spec):
    """Articles narrowed by the spec's filters."""
    qs = Article.objects.all()
    if slug := spec.get("dataset"):
        members = DatasetSource.objects.filter(dataset__slug=slug).values("source_id")
        qs = qs.filter(candidate_link__source_id__in=members)
    if status := spec.get("status"):
        qs = qs.filter(status=status)
    if wire := spec.get("wire"):
        qs = qs.filter(wire_check_status=wire)
    if scope := spec.get("scope"):
        qs = qs.filter(enrichment__scope=scope)
    if cin := spec.get("cin"):
        qs = qs.filter(primary_label=cin)
    if date_from := spec.get("from"):
        qs = qs.filter(publish_date__date__gte=date_from)
    if date_to := spec.get("to"):
        qs = qs.filter(publish_date__date__lte=date_to)
    if spec.get("enriched_only"):
        qs = qs.filter(enrichment__isnull=False)
    if spec.get("news_only"):
        qs = qs.filter(enrichment__is_news_content=True)
    return qs


class CorpusSpecError(ValueError):
    """A pivot spec that cannot run; the message is user-facing."""


def run_spec(spec):
    """Run a pivot spec and return (rows, meta).

    One dimension gives a category table (bar, donut, map); two give the
    cross-tab that chord and arc diagrams read as from/to/value.
    """
    dim_keys = [k for k in (spec.get("dimensions") or []) if k]
    if not dim_keys:
        raise CorpusSpecError("Pick at least one dimension to group by.")
    if len(dim_keys) > 2:
        raise CorpusSpecError("Group by at most two dimensions.")
    for key in dim_keys:
        if key not in DIMENSIONS:
            raise CorpusSpecError(f"Unknown dimension: {key}")
    measure_key = spec.get("measure", "articles")
    if measure_key not in MEASURES:
        raise CorpusSpecError(f"Unknown measure: {measure_key}")

    qs = _base_queryset(spec)
    total_before = None
    for key in dim_keys:
        requires = DIMENSIONS[key].get("requires")
        if requires is not None:
            if total_before is None:
                total_before = qs.count()
            qs = qs.filter(requires)

    rollups = {k: DIMENSIONS[k].get("rollup") for k in dim_keys}
    has_rollup = any(rollups.values())
    if has_rollup and MEASURES[measure_key].get("combine") is None:
        raise CorpusSpecError(
            f"{measure_label_for(measure_key)} cannot be combined across a "
            "rolled-up dimension without double counting. Group by the raw "
            "coding, or pick a different measure."
        )

    annotations = {key: DIMENSIONS[key]["expr"] for key in dim_keys}
    # A rollup needs its companion columns (the coding level) in the group.
    extra = []
    for key in dim_keys:
        for i, path in enumerate(DIMENSIONS[key].get("with", ())):
            name = f"{key}__with{i}"
            annotations[name] = F(path)
            extra.append(name)
    qs = qs.annotate(**annotations)
    # Exclude rows with no value for a grouping dimension: an unlabeled
    # bucket in a chart reads as a category, which it is not.
    if not spec.get("keep_null"):
        for key in dim_keys:
            qs = qs.exclude(**{f"{key}__isnull": True})

    measure_label = MEASURES[measure_key]["label"]
    aggregates = {measure_key: MEASURES[measure_key]["agg"]()}
    weight_fn = MEASURES[measure_key].get("weight")
    if has_rollup and weight_fn:
        aggregates["_w"] = weight_fn()
    limit = MAX_RAW_GROUPS if has_rollup else MAX_GROUPS
    rows = list(
        qs.values(*dim_keys, *extra)
        .annotate(**aggregates)
        .order_by(f"-{measure_key}")[: limit + 1]
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    if has_rollup:
        rows = _fold(rows, dim_keys, extra, rollups, measure_key, MEASURES)
        rows.sort(key=lambda r: (r[measure_key] is None, -(r[measure_key] or 0)))
        truncated = truncated or len(rows) > MAX_GROUPS
        rows = rows[:MAX_GROUPS]

    # Rename to display headers and normalise types for JSON.
    out = []
    for row in rows:
        item = {}
        for key in dim_keys:
            value = row[key]
            item[DIMENSIONS[key]["label"]] = (
                value.date().isoformat()
                if hasattr(value, "date") and not isinstance(value, str)
                else value
            )
        value = row[measure_key]
        item[measure_label] = float(value) if isinstance(value, float) else value
        out.append(item)

    meta = {
        "dimensions": [
            {
                "key": k,
                "label": DIMENSIONS[k]["label"],
                "geo_level": DIMENSIONS[k].get("geo_level"),
                "note": DIMENSIONS[k].get("note"),
            }
            for k in dim_keys
        ],
        "measure": {"key": measure_key, "label": measure_label},
        "groups": len(out),
        "truncated": truncated,
        "rows_considered": total_before,
        "rows_used": qs.count() if total_before is not None else None,
    }
    return out, meta


# --- the story map (the March Story Geography shape) ------------------------
#
# Two layers over one payload, because they answer different questions
# about the same stories:
#
#   points — each story's single claimed central location, at the
#            precision the model claimed it (place / block / county),
#            sized by how many stories share it.
#   areas  — counties shaded by how many *regional* stories mention a
#            place inside them. Regional stories deliberately have no
#            point (the pipeline records geo_skip_reason
#            "regional_uses_place_set"); their geography is the place
#            set in `geoids`, which is exactly what this layer draws.
#
# The place set is exploded in Python rather than SQL: it is a text
# column holding JSON, the corpus yields ~12k pairs, and doing it here
# keeps the sqlite test path identical to production.

STORY_MAP_LEVELS = ("place", "block", "county", "state", "tract")


def run_story_map(spec):
    """Return {'points': [...], 'areas': [...]} plus meta for a story map."""
    import json as _json

    from datasets.geo import to_county

    base = _base_queryset(spec)

    points = list(
        base.filter(enrichment__point_lat__isnull=False)
        .values(
            geoid=F("enrichment__point_geoid"),
            level=F("enrichment__point_geoid_level"),
            place=F("enrichment__point_place"),
            lat=F("enrichment__point_lat"),
            lon=F("enrichment__point_lon"),
        )
        .annotate(
            stories=Count("id", distinct=True),
            publishers=Count("candidate_link__source_id", distinct=True),
        )
        .order_by("-stories")[:MAX_GROUPS]
    )
    for row in points:
        row["lat"] = float(row["lat"]) if row["lat"] is not None else None
        row["lon"] = float(row["lon"]) if row["lon"] is not None else None

    # The shaded layer: which counties each place-set story touches.
    area_scope = spec.get("area_scope", "regional")
    area_qs = base.exclude(enrichment__geoids__isnull=True)
    if area_scope:
        area_qs = area_qs.filter(enrichment__scope=area_scope)
    by_county = {}
    considered = 0
    unresolved = set()
    for article_id, raw in area_qs.values_list("id", "enrichment__geoids")[
        :MAX_RAW_GROUPS
    ]:
        try:
            places = _json.loads(raw) if raw else []
        except (TypeError, ValueError):
            continue
        if not places:
            continue
        considered += 1
        for place in places:
            county = to_county(str(place), "place")
            if county is None:
                unresolved.add(str(place))
                continue
            # A story touching three places in one county counts once.
            by_county.setdefault(county, set()).add(article_id)
    areas = [
        {"county": county, "stories": len(ids)}
        for county, ids in sorted(by_county.items(), key=lambda kv: -len(kv[1]))
    ]

    meta = {
        "points": len(points),
        "point_stories": sum(p["stories"] for p in points),
        "areas": len(areas),
        "area_scope": area_scope,
        "area_stories": considered,
        "unresolved_places": len(unresolved),
    }
    return {"points": points, "areas": areas, "meta": meta}
