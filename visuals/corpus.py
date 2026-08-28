"""The corpus as a visual data source (SCOPE.md §2.7 v2).

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
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Substr, TruncMonth, TruncYear

from accounts.access import ALL_SCOPES
from datasets.geo import centroid, county_label
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
    "publisher_name": {
        "label": "Publisher name",
        # The name a reader would use, where `publisher` is the host it
        # publishes from. A flow from an owner to "komu.com" is a flow to
        # a domain; "KOMU 8" is the newsroom.
        "expr": F("candidate_link__source__canonical_name"),
    },
    "owner": {
        "label": "Owner",
        # Who owns the newsroom, which the directory records and the
        # corpus never offered -- so "who owns what" could not be asked
        # here at all. Recorded for a quarter of the sources; the rest
        # group as blank, which is the honest answer and not a zero.
        "expr": F("candidate_link__source__owner"),
    },
    "publisher_city": {
        "label": "Publisher city",
        "expr": F("candidate_link__source__city"),
    },
    "publisher_county": {
        "label": "Publisher county",
        "expr": F("candidate_link__source__county"),
    },
    "publisher_state": {
        "label": "Publisher state",
        # In `meta`, where the directory keeps it, rather than a column
        # of its own. Every view organised by geography needed it and
        # nothing offered it.
        #
        # Extracted as text rather than as JSON. `meta` is a `json`
        # column, not `jsonb`, and Postgres has no equality operator for
        # `json` -- so grouping by it fails outright with "could not
        # identify an equality operator for type json". `->>` gives the
        # string the group needs.
        "expr": KeyTextTransform("state", "candidate_link__source__meta"),
    },
    "publisher_type": {
        "label": "Publisher type",
        "expr": F("candidate_link__source__type"),
        "note": (
            "Digital native, print native, newspaper, audio and video "
            "broadcast. The directory's own vocabulary, which is not "
            "spelt consistently -- 'digital native' beside "
            "'audio_broadcast' -- so two spellings of one kind count as "
            "two."
        ),
    },
    "publisher_frequency": {
        "label": "How often it publishes",
        # In `meta` beside the state, and read as text for the same
        # reason: `meta` is `json` rather than `jsonb`, and Postgres has
        # no equality operator for `json`, so grouping on it fails
        # outright.
        "expr": KeyTextTransform("frequency", "candidate_link__source__meta"),
        "note": (
            "Daily, weekly, bi-weekly, monthly, or continuous. Recorded "
            "for 236 of the 1,149 publishers and blank on the rest, so a "
            "filter on it is a filter on the ones that carry it."
        ),
    },
    "author": {
        "label": "Byline",
        "expr": F("author"),
        "note": (
            "As published, so 'By Jane Doe' and 'Jane Doe' are two "
            "bylines. Thousands of values: narrow it to the largest few."
        ),
    },
    # Built with, viewed and exported freely; never published. Where a
    # story sits in our pipeline is a fact about us, and a reader meeting
    # it on a public page reads it as a fact about the journalism.
    # --- the article itself ----------------------------------------------
    #
    # A table of articles wants the article: its headline, when it ran, a
    # link, the text. None of these group into anything -- a headline is
    # unique to its story -- so choosing one turns the pivot into a
    # listing, which is what a table of them is. See run_spec.
    #
    # Not offered to a chart. A bar chart of headlines is one bar per
    # story, and a chart of body text is not a thing.
    "title": {
        "label": "Headline",
        "expr": F("title"),
        "row_level": True,
    },
    "url": {
        "label": "Link",
        "expr": F("url"),
        "row_level": True,
    },
    "published": {
        "label": "Published",
        "expr": F("publish_date"),
        "row_level": True,
        "note": "The date itself, where Month and Year group by it.",
    },
    "excerpt": {
        "label": "Body text (excerpt)",
        "expr": F("text_excerpt"),
        "row_level": True,
    },
    "body": {
        "label": "Body text (full)",
        "expr": F("text"),
        "row_level": True,
        "note": (
            "The whole article. Long in a table on screen; the reason to "
            "pick it is the CSV."
        ),
    },
    "status": {
        "label": "Article status",
        "expr": F("status"),
        "internal": True,
    },
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
    # Filterable, never an axis: which model ran is a question about a
    # slice of the corpus, not a story about local news. A chart of it says
    # something about our pipeline to a reader who came for the journalism.
    "model": {
        "label": "Enrichment model",
        "expr": F("enrichment__model"),
        "facet_only": True,
    },
    "skip_reason": {
        "label": "Skip reason",
        "expr": F("enrichment__skip_reason"),
        "facet_only": True,
    },
    "geo_skip_reason": {
        "label": "Geo skip reason",
        "expr": F("enrichment__geo_skip_reason"),
        "facet_only": True,
    },
    # Geography. Only rollups the codings support.
    "geo_state": {
        "label": "Central state",
        "expr": Substr("enrichment__point_geoid", 1, 2),
        "requires": Q(enrichment__point_geoid__isnull=False),
        "geo_level": "states",
        "note": "First two digits of the point GEOID — valid at every coding.",
    },
    "geo_county": {
        "label": "Central county",
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
        "label": "Central place (code)",
        "expr": F("enrichment__point_geoid"),
        "requires": Q(enrichment__point_geoid_level="place"),
        "geo_level": "places",
        "note": "Place-coded rows only — the corpus's most common coding.",
    },
    "point_place": {
        "label": "Central place",
        "expr": F("enrichment__point_place"),
        "note": (
            "The one place a story is set in, by name. Every story has at "
            "most one, and it is named for 8,701 of the 10,723 that have a "
            "location -- the rest are placed only to a county or a state, "
            "which have no place name to give."
        ),
    },
    # Two questions about the same geography, kept apart because they are
    # not the same question and a reader choosing between them cannot see
    # the difference from a column name.
    #
    # "County covered" was one name for both, over a text column that could
    # tell them apart in neither direction. article_geoids carries the
    # editorial distinction -- exactly one row per story is flagged primary
    # -- and covers 13,128 articles where the text column covers 7,153, a
    # strict superset with nothing of its own.
    "geo_covered": {
        "label": "Counties mentioned",
        # The place set: every county a story is about, not the one point
        # it was pinned to. A regional story has no point by design -- the
        # pipeline records `regional_uses_place_set` -- so the point
        # dimensions above cannot see its geography at all.
        #
        # Several counties per article, which is what "covered" means and
        # what makes this the one dimension the pivot cannot group in
        # SQL. It is exploded in Python, the way the story map's shaded
        # layer already is.
        "explode_join": "geoid_rows__geoid",
        "geo_level": "counties",
        "note": (
            "Every county a story names, the one it is set in and the ones "
            "it mentions in passing alike. A story naming three counties "
            "counts in all three, so these add up to more than the number "
            "of stories. Ask this to see reach; ask what a story is set in "
            "to see focus."
        ),
    },
    "point_precision": {
        "label": "Location precision",
        # The coding level the model claimed: a block is a street, a
        # state is the whole state. A map drawn without knowing which is
        # a map that reads a state-level guess as an address.
        "expr": F("enrichment__point_geoid_level"),
        "note": "Block, place, county or state — how precise the location is.",
    },
    "point_method": {
        "label": "How the location was decided",
        "expr": F("enrichment__point_method"),
        "note": (
            "The model, the publication's own city, or an assumption "
            "from where it publishes. An assumed location is not a "
            "reported one."
        ),
    },
    "point_zcta": {
        "label": "Central ZIP code area",
        "expr": F("enrichment__point_zcta"),
        "geo_level": "zcta",
        "note": "Census ZCTA, where the coding was precise enough to have one.",
    },
    # --- who is in the news ---------------------------------------------
    #
    # A story names several people and several organisations, so these join
    # one-to-many and a row multiplies across the join. That is what makes
    # them answerable at all -- a story about three officials counts in
    # every type it names -- and it is also why they can only be counted by
    # something distinct. `multi` says so, and run_spec refuses the rest.
    #
    # `role_in_story` from either table is not here: it is free text, 39,997
    # distinct values across 55,856 rows, one per row in all but name. An
    # axis of it would draw a chart of forty thousand categories. `nature`
    # is the vocabulary that field looks like it should be.
    "person_type": {
        "label": "Person type",
        "expr": F("people__person_type"),
        "multi": True,
        "requires": Q(people__person_type__isnull=False),
        "note": (
            "Who is named: an elected official, an athlete, a community "
            "member. A story counts in every type it names."
        ),
    },
    "person_nature": {
        "label": "How a person figures",
        "expr": F("people__nature"),
        "multi": True,
        "requires": Q(people__nature__isnull=False),
        "note": (
            "Source, subject, official, witness, victim -- what the person "
            "is to the story rather than who they are."
        ),
    },
    "person_public": {
        "label": "Public figure",
        "expr": F("people__public_figure"),
        "multi": True,
        "requires": Q(people__public_figure__isnull=False),
        "note": "Whether the person named holds a public role.",
    },
    "person_name": {
        "label": "Person named",
        "expr": F("people__name"),
        "multi": True,
        "requires": Q(people__name__isnull=False),
        "note": (
            "Tens of thousands of names. Usable with a limit on the "
            "left-hand side, unreadable without one."
        ),
    },
    "person_affiliation": {
        "label": "Person's affiliation",
        "expr": F("people__affiliation"),
        "multi": True,
        "requires": Q(people__affiliation__isnull=False),
        "note": "The organisation behind the name, as the story gives it.",
    },
    "org_type": {
        "label": "Organisation type",
        "expr": F("organizations__org_type"),
        "multi": True,
        "requires": Q(organizations__org_type__isnull=False),
        "note": (
            "Government, company, school, sports team. A story counts in "
            "every type it names."
        ),
    },
    "org_nature": {
        "label": "How an organisation figures",
        "expr": F("organizations__nature"),
        "multi": True,
        "requires": Q(organizations__nature__isnull=False),
        "note": "Actor, subject, source, regulator, affected.",
    },
    "org_name": {
        "label": "Organisation named",
        "expr": F("organizations__name"),
        "multi": True,
        "requires": Q(organizations__name__isnull=False),
        "note": "Thousands of names; wants a limit on the left-hand side.",
    },
    "is_news_content": {
        "label": "Is news content",
        "expr": F("enrichment__is_news_content"),
        "note": (
            "What the content gate decided. Blank where it never ran, "
            "which is not the same as 'no'."
        ),
    },
    "content_gate_reason": {
        "label": "Why the gate excluded it",
        "expr": F("enrichment__content_gate_reason"),
        "internal": True,
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
        "internal": True,
        "label": "Cost (sum, USD)",
        "agg": lambda: Sum("enrichment__cost_usd"),
        "combine": "sum",
    },
    "cost_avg": {
        "internal": True,
        "label": "Cost (average, USD)",
        "agg": lambda: Avg("enrichment__cost_usd"),
        "combine": "mean",
        "weight": lambda: Count("enrichment__cost_usd"),
    },
    "confidence_avg": {
        "internal": True,
        "label": "Scope confidence (average)",
        "agg": lambda: Avg("enrichment__scope_confidence"),
        "combine": "mean",
        "weight": lambda: Count("enrichment__scope_confidence"),
    },
    "cin_confidence_avg": {
        "internal": True,
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

#: Prefix for a dimension's annotation alias. A dimension key is a name we
#: chose and a model field is a name the crawler chose; nothing stops them
#: colliding, and Django raises rather than guessing which was meant.
DIM_PREFIX = "dim_"

# --- which articles a visual may draw ----------------------------------------
#
# An article's `status` says both how far the pipeline took it and why it
# stopped. Everything below is terminal except `paused`, which is still in
# flight: a visual drawn from it would change under the reader, so nothing
# may draw it, ever, whatever else is asked for.
#
# Beyond that there are two useful sets, and they differ by what survived
# the filters rather than by how finished they are. Obituaries, weather,
# opinion, paywall stubs and wire copy are all finished; they were diverted
# before enrichment.
IN_FLIGHT = ("paused",)

#: Reached enrichment. `enrichment_skipped` belongs here because the skip is
#: a recorded decision at that stage rather than a diversion before it.
#: These are the rows the crawler exports to BigQuery -- 15,552 in each, as
#: of 2026-08-24, which is what makes this a definition and not a guess.
ENRICHED_STATUSES = ("enriched", "enrichment_skipped")

COMPLETE = "complete"
ENRICHED = "enriched"
SUBSETS = {
    COMPLETE: (
        "Complete",
        "Everything the pipeline finished with, including obituaries, "
        "weather, opinion, paywall stubs and wire copy.",
    ),
    ENRICHED: (
        "Enriched",
        "What reached enrichment, which is what is exported to BigQuery.",
    ),
}

MAX_GROUPS = 5000
# A rolled-up dimension groups on the raw coding first, which yields more
# rows than the folded result; allow headroom before folding.
MAX_RAW_GROUPS = 50_000


def measure_label_for(key):
    return MEASURES[key]["label"]


#: The columns a geo dimension brings with it, named as the pivot names
#: everything else: by what a reader would call them.
LAT_LABEL = "Latitude"
LON_LABEL = "Longitude"

#: Levels the gazetteers here can place. A state has no centroid worth
#: plotting -- the middle of Missouri is not a place anybody reported
#: from -- so a state-grouped pivot carries no coordinates.
_CENTRED = ("counties", "places")


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
                # `extra` columns keep the alias in their own name, and
                # the rename above rewrites only the dimensions.
                level = row.get(f"{key}__with0", row.get(f"{DIM_PREFIX}{key}__with0"))
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


def qualifying_values(spec, dim_key, scopes):
    """Values of a dimension that clear the spec's group thresholds.

    "Counties with at least two publishers and 100 articles" is a HAVING
    on the group, not a filter on the rows: the threshold decides which
    groups appear at all, and the composition inside them is untouched.
    Returns None when no threshold is set.
    """
    min_articles = int(spec.get("min_articles") or 0)
    min_publishers = int(spec.get("min_publishers") or 0)
    if not (min_articles or min_publishers):
        return None
    dimension = DIMENSIONS[dim_key]
    if dimension.get("rollup"):
        raise CorpusSpecError(
            "Group thresholds do not apply to a rolled-up dimension yet — "
            "the publisher and article counts cannot be recombined safely."
        )
    qs = (
        _base_queryset(spec, scopes)
        .annotate(_dim=dimension["expr"])
        .exclude(_dim__isnull=True)
        .values("_dim")
        .annotate(
            _articles=Count("id", distinct=True),
            _publishers=Count("candidate_link__source_id", distinct=True),
        )
    )
    if min_articles:
        qs = qs.filter(_articles__gte=min_articles)
    if min_publishers:
        qs = qs.filter(_publishers__gte=min_publishers)
    return {row["_dim"] for row in qs}


#: Annotation prefix for a facet filter. Distinct from DIM_PREFIX, whose
#: aliases are the pivot's grouping columns -- colliding with one would
#: quietly replace a grouping with a filter.
ONLY_PREFIX = "_only_"


#: What a typed date is read as when it is not already ISO. The calendar
#: writes `yyyy-mm-dd` and the field says so, but it is a text box -- the
#: native date input was swapped out to stop the browser's own picker
#: appearing beside ours -- so anything can be typed into it. These are
#: US order, month first, which is the order every newsroom in this
#: corpus writes dates in.
_TYPED_DATE = ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y")


def as_iso(value, what="date"):
    """One typed date as `yyyy-mm-dd`, or a refusal naming it.

    An unparseable date used to reach the query as-is, where Django
    raised ValidationError -- not a CorpusSpecError, so it was not the
    502 the feed answers a bad spec with but an unhandled 500, and the
    builder said "500 from the feed" about a date somebody typed.
    """
    from datetime import datetime

    text = str(value or "").strip()
    if not text:
        return text
    for fmt in ("%Y-%m-%d", *_TYPED_DATE):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise CorpusSpecError(
        f"{text!r} is not a date this understands. Write the {what} date "
        "as yyyy-mm-dd, or pick it from the calendar."
    )


def _base_queryset(spec, scopes):
    """Articles narrowed to `scopes`, then by the spec's filters.

    `scopes` is the set of dataset slugs this visual may draw on, or
    `ALL_SCOPES`. It is applied *before* the spec, so a spec that names no
    dataset aggregates over what the visual is wired to rather than over
    the whole corpus — which is what it did until this argument existed,
    and which let somebody holding one dataset build a chart of every
    dataset.
    """
    qs = Article.objects.all()
    # Never in flight. A visual drawn from an article the pipeline has not
    # finished with would change under the reader, so this is not a filter
    # somebody chooses -- it is a floor under every one of them.
    qs = qs.exclude(status__in=IN_FLIGHT)
    if spec.get("subset") == ENRICHED:
        qs = qs.filter(status__in=ENRICHED_STATUSES)
    if scopes is not ALL_SCOPES:
        if not scopes:
            qs = qs.none()
        else:
            permitted = DatasetSource.objects.filter(dataset__slug__in=scopes).values(
                "source_id"
            )
            qs = qs.filter(candidate_link__source_id__in=permitted)
    if slug := spec.get("dataset"):
        members = DatasetSource.objects.filter(dataset__slug=slug).values("source_id")
        qs = qs.filter(candidate_link__source_id__in=members)
    # Where the publisher is, as opposed to where the story is about. The
    # two are different questions and a map wants both: the dots are the
    # second, and this is how you ask for the first.
    #
    # County and city, never host. A host is an identity and identity is a
    # source UUID; matching outlets on their address is what the proposal
    # queue exists to keep humans in charge of.
    # The newsrooms step writes this and, until now, nothing read it. A
    # chart built after narrowing to one county was a chart of every
    # newsroom in the dataset, and it looked right -- the step said "934
    # of 1143 kept" and the picture did not change, because the picture
    # never depended on it.
    #
    # Empty means all, which is what the step stores rather than listing
    # every publisher: a stored list of "everything" goes stale the moment
    # a newsroom is added.
    if publishers := spec.get("publishers"):
        qs = qs.filter(candidate_link__source_id__in=publishers)
    if county := spec.get("publisher_county"):
        qs = qs.filter(candidate_link__source__county__iexact=county)
    if city := spec.get("publisher_city"):
        qs = qs.filter(candidate_link__source__city__iexact=city)
    if status := spec.get("status"):
        qs = qs.filter(status=status)
    if wire := spec.get("wire"):
        qs = qs.filter(wire_check_status=wire)
    if scope := spec.get("scope"):
        qs = qs.filter(enrichment__scope=scope)
    if cin := spec.get("cin"):
        qs = qs.filter(primary_label=cin)
    if date_from := spec.get("from"):
        qs = qs.filter(publish_date__date__gte=as_iso(date_from, "from"))
    if date_to := spec.get("to"):
        qs = qs.filter(publish_date__date__lte=as_iso(date_to, "to"))
    if spec.get("enriched_only"):
        qs = qs.filter(enrichment__isnull=False)
    if spec.get("news_only"):
        qs = qs.filter(enrichment__is_news_content=True)
    if spec.get("labeled_only"):
        # "articles evaluated" — those the classifier actually labelled,
        # so a group threshold counts the same articles the chart plots.
        qs = qs.filter(primary_label__isnull=False)

    # The values the author ticked in the facet. The fields step has written
    # these since the facet existed and nothing read them, so ticking three
    # of eleven labels produced a chart of all eleven -- the step said it
    # was narrowing, the picture disagreed, and neither said so. The same
    # failure the publishers filter above had, for the same reason.
    #
    # Generic, by the dimension's own expression, so a dimension does not
    # have to be named here to be filterable. A dimension that holds several
    # values per article cannot be reached this way; `_run_exploded` narrows
    # those after it has taken them apart.
    for key, values in (spec.get("only") or {}).items():
        if key not in DIMENSIONS or not values:
            continue
        dimension = DIMENSIONS[key]
        if explodes(key):
            continue
        qs = qs.annotate(**{f"{ONLY_PREFIX}{key}": dimension["expr"]}).filter(
            **{f"{ONLY_PREFIX}{key}__in": list(values)}
        )
    # What kind of newsroom, and how often it publishes. Stored as the
    # kind rather than as the spellings it covers: a stored list of
    # spellings is a snapshot, and the first record written with a new one
    # would drop out of a filter that says it wants every radio station --
    # the same staleness that made ticking every newsroom stop meaning
    # "all of them".
    for key, wanted in (
        ("publisher_type", spec.get("publisher_kinds")),
        ("publisher_frequency", spec.get("publisher_frequencies")),
    ):
        if not wanted:
            continue
        asked = set(wanted)
        spellings = [
            value
            for value in _recorded_values(key, scopes)
            if group_of(key, value) in asked
        ]
        # A kind nobody's records spell yet matches nothing, which is what
        # it should: the alternative is a filter that quietly matches
        # everything the moment its values go missing.
        qs = qs.annotate(**{f"{ONLY_PREFIX}{key}": DIMENSIONS[key]["expr"]}).filter(
            **{f"{ONLY_PREFIX}{key}__in": spellings}
        )
    return qs


# --- what a newsroom is, and how often it publishes -------------------------
#
# The directory records both as free text and does not spell either
# consistently: 'digital native' beside 'digital_native', 'weekly' beside
# 'Weekly'. Folding case and separators here makes one kind one filter;
# it does not make the records agree, and it is not meant to. A spelling
# that has to be folded is a defect in the record, and the sources review
# queue is where that gets raised and fixed.
#
# Nothing is hidden. A value these do not recognise is offered under the
# name it was recorded with, so a vocabulary that grows is visible in the
# filter the day it grows rather than silently dropped from it.


def fold_value(value):
    """One recorded value, with case and separators taken out of it."""
    text = str(value or "").replace("_", " ").replace("-", " ").replace("/", " / ")
    return " ".join(text.split()).lower()


#: (key, what a reader sees, the recorded values it covers -- folded).
PUBLISHER_KINDS = (
    ("digital", "Digital", ("digital native", "digital")),
    ("print", "Print", ("print native", "newspaper", "print")),
    ("tv", "Television", ("video broadcast", "television", "tv")),
    ("radio", "Radio", ("audio broadcast", "radio")),
    # Ten records say only "broadcast", which is not an answer to whether
    # this is a television station or a radio one. Its own entry rather
    # than a guess into either.
    ("broadcast", "Broadcast, not said which", ("broadcast",)),
)

#: The same shape for how often a newsroom publishes.
PUBLISHER_FREQUENCIES = (
    ("daily", "Daily", ("daily",)),
    ("weekly", "Weekly", ("weekly",)),
    (
        "semiweekly",
        "More than weekly",
        (
            "bi weekly",
            "semi weekly",
            "tri weekly",
            "weekly / daily",
            "biweekly",
            "semiweekly",
        ),
    ),
    ("monthly", "Monthly", ("monthly",)),
    ("continuous", "Continuous", ("continuous",)),
)

#: Which grouping belongs to which dimension.
GROUPED_VALUES = {
    "publisher_type": PUBLISHER_KINDS,
    "publisher_frequency": PUBLISHER_FREQUENCIES,
}


def group_of(dimension, value):
    """The key a recorded value groups under, or "" for one it does not.

    A value nobody grouped is not an error and is not dropped: the caller
    offers it under its own name.
    """
    folded = fold_value(value)
    if not folded:
        return ""
    for key, _label, covered in GROUPED_VALUES.get(dimension, ()):
        if folded in covered:
            return key
    return ""


def _publisher_rows(scopes):
    """(type, frequency) for every source the given scopes can see.

    One query, held as long as the newsroom tree is: both answer the same
    question about the same records, and the filter is drawn beside the
    tree.
    """
    from django.core.cache import cache

    from explorer.models import Source

    key = _cache_key("visuals.publisher_rows", sorted(scopes) if scopes else [])
    hit = cache.get(key)
    if hit is not None:
        return hit

    members = DatasetSource.objects.all()
    if scopes:
        members = members.filter(dataset__slug__in=scopes)
    ids = set(members.values_list("source_id", flat=True))
    rows = [
        (
            (kind or "").strip(),
            ((meta or {}).get("frequency") or "").strip(),
        )
        for kind, meta in Source.objects.filter(id__in=ids).values_list("type", "meta")
    ]
    cache.set(key, rows, CORPUS_CACHE_SECONDS)
    return rows


#: Which of the pair each dimension reads.
_PUBLISHER_COLUMN = {"publisher_type": 0, "publisher_frequency": 1}


def _recorded_values(key, scopes):
    """Every spelling of one publisher attribute the sources in scope carry."""
    at = _PUBLISHER_COLUMN[key]
    return sorted({row[at] for row in _publisher_rows(scopes) if row[at]})


def publisher_facet(key, scopes, kept=()):
    """The filter for one publisher attribute: what to offer, and how many.

    Grouped values first, in the order they are declared, then anything
    recorded that no group covers -- under the name it was recorded with,
    because a value this does not recognise is a record to fix rather than
    a record to hide.
    """
    at = _PUBLISHER_COLUMN[key]
    counts, loose = {}, {}
    for row in _publisher_rows(scopes):
        value = row[at]
        if not value:
            continue
        group = group_of(key, value)
        if group:
            counts[group] = counts.get(group, 0) + 1
        else:
            loose[value] = loose.get(value, 0) + 1
    kept = set(kept or ())
    offered = [
        {"value": group, "label": label, "count": counts[group], "on": group in kept}
        for group, label, _covered in GROUPED_VALUES[key]
        if counts.get(group)
    ]
    offered += [
        {
            "value": value,
            # Named as it was recorded, and said to be: a reader choosing
            # it should know they are choosing one spelling.
            "label": f"{value} — as recorded",
            "count": count,
            "on": value in kept,
        }
        for value, count in sorted(loose.items())
    ]
    return offered


class CorpusSpecError(ValueError):
    """A pivot spec that cannot run; the message is user-facing."""


def _top_wanted(spec, choices):
    """How many of the first dimension to keep, or 0 for all of them.

    A number is a number. A string ending in "%" is a share of however
    many there turn out to be -- "the top 10%" of a hundred and eleven
    cities is eleven of them, and stays a tenth as the corpus grows,
    where a fixed twenty slowly becomes a smaller slice of a longer list.

    Older shapes are read for what they meant rather than ignored: a
    mapping asked this per dimension and its largest value is taken, so a
    saved visual keeps showing about as much as it did.
    """
    top = spec.get("top")
    if isinstance(top, dict):
        numbers = [int(v or 0) for v in top.values()]
        top = max(numbers) if numbers else 0
    if isinstance(top, str) and top.strip().endswith("%"):
        try:
            share = float(top.strip()[:-1])
        except ValueError:
            return 0
        if share <= 0:
            return 0
        # At least one: a tenth of four is not none of them.
        return max(1, round(choices * share / 100))
    try:
        return max(0, int(top or 0))
    except (TypeError, ValueError):
        return 0


def _narrow_to_the_first(rows, dim_keys, measure_key, spec):
    """Keep the largest few values of the *first* dimension, whole.

    The number names the left-hand column and nothing else. A sankey
    shows a relationship between two things, and "the top ten" is a
    statement about one of them -- the publishers, not the pairings and
    not the counties. Limiting both ends, or limiting the pairs, gives a
    number nobody can say out loud: ten of what.

    Every flow belonging to a kept value is kept with it, so a publisher
    that is shown is shown whole rather than truncated to its largest
    county. Whether the right-hand column needs gathering is the
    renderer's question, and it answers it with "Everything else".
    """
    first = dim_keys[0]
    weight = {}
    for row in rows:
        weight[row[first]] = (weight.get(row[first]) or 0) + (row[measure_key] or 0)
    wanted = _top_wanted(spec, len(weight))
    if not wanted or len(weight) <= wanted:
        return rows, 0
    ranked = sorted(weight.items(), key=lambda kv: -(kv[1] or 0))
    keep = {value for value, _ in ranked[:wanted]}
    return [row for row in rows if row[first] in keep], len(weight) - len(keep)


#: What an exploded dimension can be counted by. A sum or a mean over a
#: multi-valued dimension counts the same article once per county it
#: mentions, which is a different number wearing the same name.
EXPLODED_MEASURES = ("articles", "publishers")


#: Which family a dimension belongs to, for the picker. Ordered: the list
#: is rendered in this order and a dimension with no entry falls to the end
#: under "Other", which is a prompt to place it rather than a resting spot.
#:
#: Story geography holds both coverage questions -- where a story is set and
#: everywhere it mentions -- because they are the same analysis asked two
#: ways, and a reader choosing between them should see them side by side.
#: Where the publisher sits is a different question and lives with the
#: publisher.
GROUPS = (
    ("publisher", "Publisher"),
    ("article", "The article"),
    ("story", "The story"),
    ("time", "When"),
    ("people", "People named"),
    ("organisations", "Organisations named"),
    ("geography", "Story geography"),
    ("pipeline", "Pipeline"),
)

GROUP_OF = {
    "body": "article",
    "excerpt": "article",
    "published": "article",
    "url": "article",
    "title": "article",
    "dataset": "publisher",
    "publisher": "publisher",
    "publisher_name": "publisher",
    "owner": "publisher",
    "publisher_city": "publisher",
    "publisher_county": "publisher",
    "publisher_state": "publisher",
    "publisher_type": "publisher",
    "publisher_frequency": "publisher",
    "author": "story",
    "status": "pipeline",
    "wire": "story",
    "cin_primary": "story",
    "cin_alternate": "story",
    "month": "time",
    "year": "time",
    "scope": "story",
    "subject": "story",
    "topic": "story",
    "format": "story",
    "timeframe": "story",
    "user_need": "story",
    "model": "pipeline",
    "skip_reason": "pipeline",
    "geo_skip_reason": "pipeline",
    "person_type": "people",
    "person_nature": "people",
    "person_public": "people",
    "person_name": "people",
    "person_affiliation": "people",
    "org_type": "organisations",
    "org_nature": "organisations",
    "org_name": "organisations",
    "geo_covered": "geography",
    "geo_state": "geography",
    "geo_county": "geography",
    "geo_place": "geography",
    "point_place": "geography",
    "point_precision": "geography",
    "point_method": "geography",
    "point_zcta": "geography",
    "is_news_content": "pipeline",
    "content_gate_reason": "pipeline",
}


def explodes(key):
    """The path a multi-valued dimension's values come from, or None.

    Two sources, one question: `explode` is a text column holding a list,
    `explode_join` is the rows of a joined table. Asked here so that adding
    a source cannot leave some caller still testing for the other one --
    which is exactly what happened, and turned two geography dimensions
    into a KeyError on 'expr'.
    """
    dimension = DIMENSIONS.get(key) or {}
    return dimension.get("explode") or dimension.get("explode_join")


def _run_exploded(spec, scopes, dim_keys, measure_key, exploded):
    """The pivot, where one dimension holds several values per article.

    Grouped in Python because it has to be: the place set is a text
    column holding JSON, and no GROUP BY can reach inside it. This is the
    same explosion the story map does for its shaded layer, generalised
    to sit beside other dimensions.

    Counted as distinct things rather than summed, so an article
    mentioning a county twice is one story about it.
    """
    import json as _json

    from datasets.geo import county_of

    if measure_key not in EXPLODED_MEASURES:
        raise CorpusSpecError(
            f"{DIMENSIONS[exploded]['label']} holds several values per story, "
            f"so it can be counted by "
            f"{' or '.join(MEASURES[m]['label'].lower() for m in EXPLODED_MEASURES)}"
            f" but not by {MEASURES[measure_key]['label'].lower()}."
        )

    others = [key for key in dim_keys if key != exploded]
    alias = {key: f"{DIM_PREFIX}{key}" for key in others}
    # Two sources for the same shape of answer. `explode` reads a text
    # column holding a list; `explode_join` reads the rows of
    # article_geoids, which is where the same geography lives with the
    # editorial distinction attached -- and where far more of it lives: the
    # text column is populated for 7,153 articles and the table for 13,128,
    # a strict superset with nothing of its own.
    joined = DIMENSIONS[exploded].get("explode_join")
    path = explodes(exploded)
    qs = _base_queryset(spec, scopes).exclude(**{f"{path}__isnull": True})
    keep = DIMENSIONS[exploded].get("explode_where")
    if keep is not None:
        qs = qs.filter(keep)
    if others:
        qs = qs.annotate(**{alias[key]: DIMENSIONS[key]["expr"] for key in others})

    columns = ["id", "candidate_link__source_id", path]
    columns += [alias[key] for key in others]

    # One bucket per group, holding the ids it has seen: the measure is a
    # count of distinct things and a story reaching a county twice is one
    # story about that county.
    seen, unresolved = {}, set()
    counted = "id" if measure_key == "articles" else "candidate_link__source_id"
    # The facet, for the dimension that holds several values per article.
    # `_base_queryset` cannot narrow this one -- the values are inside a
    # text column -- so it is applied here, once they have been taken
    # apart. Narrowing the article would be the wrong answer anyway: a
    # story covering three counties still belongs in the one that was
    # ticked, and dropping the story would lose the other two as well.
    only_values = (spec.get("only") or {}).get(exploded) or []
    # Compared as the chart shows it. The facet offers "Boone, MO" because
    # "29019" is not a value anybody can tick; the code stays underneath,
    # where the centroid is looked up by it.
    wanted = {str(v) for v in only_values} or None
    shown = (
        county_label
        if DIMENSIONS[exploded].get("geo_level") == "counties"
        else (lambda v: v)
    )
    # Fetched in one go rather than streamed. A server-side cursor over
    # this join took fifty seconds against production where one fetch of
    # the same fifteen thousand rows takes five: the rows are small, and
    # the round trips are the cost.
    for row in qs.values_list(*columns):
        article, source, raw = row[0], row[1], row[2]
        rest = row[3:]
        if joined:
            # One row per geoid already; the join did the exploding.
            places = [raw] if raw else []
        else:
            try:
                places = _json.loads(raw) if raw else []
            except (TypeError, ValueError):
                continue
        counties = set()
        for place in places or ():
            county = county_of(place)
            if county is None:
                unresolved.add(str(place))
                continue
            counties.add(county)
        if wanted is not None:
            counties = {c for c in counties if shown(c) in wanted}
        for county in counties:
            key = (*rest, county)
            seen.setdefault(key, set()).add(article if counted == "id" else source)

    rows = []
    for key, ids in seen.items():
        row = dict(zip(others, key[:-1], strict=True))
        row[exploded] = key[-1]
        row[measure_key] = len(ids)
        rows.append(row)
    rows.sort(key=lambda r: -r[measure_key])
    return rows, len(unresolved)


def _run_rows(spec, scopes, dim_keys):
    """The articles themselves, one row each, not grouped.

    A headline is unique to its story, so grouping by one makes every
    story its own group -- a listing, arrived at by hashing every headline
    and every body text on the way. This asks for the rows instead.

    Newest first, because a table of stories is read from the top and the
    top is the recent end. Bounded by the same ceiling as a pivot, and it
    says when it hit it.
    """
    alias = {key: f"{DIM_PREFIX}{key}" for key in dim_keys}
    qs = _base_queryset(spec, scopes).annotate(
        **{alias[key]: DIMENSIONS[key]["expr"] for key in dim_keys}
    )
    for key in dim_keys:
        requires = DIMENSIONS[key].get("requires")
        if requires is not None:
            qs = qs.filter(requires)

    columns = [alias[key] for key in dim_keys]
    # Nulls last. Postgres puts them first on a descending sort, so a table
    # of stories would open on the ones with no date -- which reads as a
    # table of nothing.
    fetched = list(
        qs.order_by(F("publish_date").desc(nulls_last=True)).values(*columns)[
            : MAX_GROUPS + 1
        ]
    )
    truncated = len(fetched) > MAX_GROUPS
    fetched = fetched[:MAX_GROUPS]

    rows = [
        {DIMENSIONS[key]["label"]: row[alias[key]] for key in dim_keys}
        for row in fetched
    ]
    return rows, {
        "qualifying_groups": None,
        "thresholds": {"min_articles": 0, "min_publishers": 0},
        "dimensions": [
            {
                "key": k,
                "label": DIMENSIONS[k]["label"],
                "geo_level": None,
                "note": DIMENSIONS[k].get("note"),
            }
            for k in dim_keys
        ],
        "measure": None,
        "groups": len(rows),
        "truncated": truncated,
        "rows_considered": None,
        "rows_used": None,
    }


def run_spec(spec, scopes):
    """Run a pivot spec and return (rows, meta).

    One dimension gives a category table (bar, donut, map); two give the
    cross-tab that chord and arc diagrams read as from/to/value; a table
    takes as many columns as somebody ticks.
    """
    # Deduplicated, in the order they were ticked. A column ticked twice is
    # one column, and grouping by it twice would be a second identical
    # column beside the first.
    #
    # No cap on how many. Two was the cap for everything, which is a
    # chart's number -- one dimension is a category chart, two are the
    # cross-tab a chord or a sankey reads -- and a table exists to have
    # columns. A chart cannot exceed two anyway: it has only that many
    # roles to fill. The real maximum is how many dimensions there are, and
    # MAX_GROUPS bounds what comes back however many are used.
    dim_keys = list(dict.fromkeys(k for k in (spec.get("dimensions") or []) if k))
    if not dim_keys:
        raise CorpusSpecError("Pick at least one dimension to group by.")
    for key in dim_keys:
        if key not in DIMENSIONS:
            raise CorpusSpecError(f"Unknown dimension: {key}")

    # A headline, a link, a date, the text: unique to one story each, so
    # grouping by them makes every story its own group. That is a listing,
    # reached by hashing every body text on the way. Ask for the rows.
    if any(DIMENSIONS[k].get("row_level") for k in dim_keys):
        grouped = [k for k in dim_keys if not DIMENSIONS[k].get("row_level")]
        if any(explodes(k) or DIMENSIONS[k].get("multi") for k in grouped):
            raise CorpusSpecError(
                "A column that names several per story cannot sit beside the "
                "story's own headline or text. Choose one or the other."
            )
        return _run_rows(spec, scopes, dim_keys)
    for key in dim_keys:
        if key not in DIMENSIONS:
            raise CorpusSpecError(f"Unknown dimension: {key}")
    measure_key = spec.get("measure", "articles")
    if measure_key not in MEASURES:
        raise CorpusSpecError(f"Unknown measure: {measure_key}")

    # A dimension holding several values per story cannot be grouped in
    # SQL -- the place set is a text column holding JSON -- so it takes
    # its own path, and only one of them can be in a pivot: two would
    # multiply each other and count a story once per pair of counties it
    # touches.
    # A dimension that joins one-to-many multiplies the rows it groups, so
    # anything but a distinct count is counted once per person or
    # organisation named. Cost summed that way is not a bigger number, it
    # is a wrong one, and nothing about the chart would say so.
    joined = [k for k in dim_keys if DIMENSIONS[k].get("multi")]
    if joined and measure_key not in EXPLODED_MEASURES:
        raise CorpusSpecError(
            f"{DIMENSIONS[joined[0]]['label']} names several per story, so it "
            f"can be counted by "
            f"{' or '.join(MEASURES[m]['label'].lower() for m in EXPLODED_MEASURES)}"
            f" but not by {MEASURES[measure_key]['label'].lower()}."
        )

    exploding = [k for k in dim_keys if explodes(k)]
    if len(exploding) > 1:
        raise CorpusSpecError(
            "Only one dimension holding several values per story at a time."
        )
    if exploding:
        rows, unresolved = _run_exploded(
            spec, scopes, dim_keys, measure_key, exploding[0]
        )
        rows, narrowed = _narrow_to_the_first(rows, dim_keys, measure_key, spec)
        out = []
        for row in rows:
            item = {}
            for k in dim_keys:
                value = row[k]
                # A county reads as its name. "29095" is a chart nobody
                # can read, and the code is still what the centroid is
                # looked up by.
                if DIMENSIONS[k].get("geo_level") == "counties" and k == exploding[0]:
                    value = county_label(value)
                item[DIMENSIONS[k]["label"]] = value
            item[measure_label_for(measure_key)] = row[measure_key]
            lat, lon = centroid(str(row[exploding[0]] or ""))
            if lat is not None:
                item[LAT_LABEL], item[LON_LABEL] = lat, lon
            out.append(item)
        return out, {
            "qualifying_groups": None,
            "thresholds": {"min_articles": 0, "min_publishers": 0},
            "dimensions": [
                {
                    "key": k,
                    "label": DIMENSIONS[k]["label"],
                    "geo_level": DIMENSIONS[k].get("geo_level"),
                    "note": DIMENSIONS[k].get("note"),
                }
                for k in dim_keys
            ],
            "measure": {"key": measure_key, "label": measure_label_for(measure_key)},
            "groups": len(out),
            "truncated": False,
            "narrowed_away": narrowed,
            "rows_considered": None,
            "rows_used": None,
            "unresolved_places": unresolved,
        }

    qs = _base_queryset(spec, scopes)
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

    qualifying = qualifying_values(spec, dim_keys[0], scopes)

    # Prefixed, because a dimension key is free to be the name of a field.
    # `wire` was: Article.wire is a real column, so annotating an alias of
    # that name raised "conflicts with a field on the model" and the whole
    # dimension was unusable. A prefix nothing else uses cannot collide.
    alias = {key: f"{DIM_PREFIX}{key}" for key in dim_keys}
    annotations = {alias[key]: DIMENSIONS[key]["expr"] for key in dim_keys}
    # A rollup needs its companion columns (the coding level) in the group.
    extra = []
    for key in dim_keys:
        for i, path in enumerate(DIMENSIONS[key].get("with", ())):
            name = f"{alias[key]}__with{i}"
            annotations[name] = F(path)
            extra.append(name)
    qs = qs.annotate(**annotations)
    if qualifying is not None:
        qs = qs.filter(**{f"{alias[dim_keys[0]]}__in": qualifying})
    # Exclude rows with no value for a grouping dimension: an unlabeled
    # bucket in a chart reads as a category, which it is not.
    if not spec.get("keep_null"):
        for key in dim_keys:
            qs = qs.exclude(**{f"{alias[key]}__isnull": True})

    measure_label = MEASURES[measure_key]["label"]
    aggregates = {measure_key: MEASURES[measure_key]["agg"]()}
    weight_fn = MEASURES[measure_key].get("weight")
    if has_rollup and weight_fn:
        aggregates["_w"] = weight_fn()
    limit = MAX_RAW_GROUPS if has_rollup else MAX_GROUPS
    rows = list(
        qs.values(*[alias[k] for k in dim_keys], *extra)
        .annotate(**aggregates)
        .order_by(f"-{measure_key}")[: limit + 1]
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    # Back to the plain dimension names. The alias exists only so an
    # annotation cannot collide with a real column -- `wire` is both a
    # dimension and a field on Article -- and everything downstream, the
    # fold and the output, is written in terms of the dimension. Without
    # this the rows arrive keyed `dim_cin_primary` and the first read of
    # `cin_primary` raises KeyError, which is every pivot that has a
    # dimension and no rollup.
    back = {v: k for k, v in alias.items()}
    rows = [{back.get(k, k): v for k, v in row.items()} for row in rows]

    if has_rollup:
        rows = _fold(rows, dim_keys, extra, rollups, measure_key, MEASURES)
        rows.sort(key=lambda r: (r[measure_key] is None, -(r[measure_key] or 0)))
        truncated = truncated or len(rows) > MAX_GROUPS
        rows = rows[:MAX_GROUPS]

    # Keep the largest few *relationships*, where the spec asks.
    #
    # The unit is the row, not either end of it. "The top ten publisher
    # names" and "the top ten cities" are two different narrowings that
    # intersect in a way nobody can predict, and neither of them is the
    # question a flow chart asks -- which is "which pairings are the
    # biggest". A row is one pairing, ranked by the amount that pairs
    # them.
    #
    # After the fold, because a rolled-up dimension does not exist in SQL:
    # geo_county is folded from place codings in Python, so a SQL LIMIT
    # could not rank the one that most needs ranking.
    rows, narrowed = _narrow_to_the_first(rows, dim_keys, measure_key, spec)

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
            # A place says where it is. The Census gazetteer carries an
            # internal point for every county and place, so a row grouped
            # by one can carry its own coordinates -- which is what lets a
            # point map take a place rather than a latitude and a
            # longitude as two separate measures. A pivot emits one
            # measure per query, so as two measures it could not be drawn
            # at all.
            #
            # The first geo dimension wins: a second pair of coordinates
            # in one row would be two answers to "where is this".
            if DIMENSIONS[key].get("geo_level") in _CENTRED and LAT_LABEL not in item:
                lat, lon = centroid(str(value or ""))
                if lat is not None:
                    item[LAT_LABEL], item[LON_LABEL] = lat, lon
        value = row[measure_key]
        item[measure_label] = float(value) if isinstance(value, float) else value
        out.append(item)

    meta = {
        "qualifying_groups": None if qualifying is None else len(qualifying),
        # How many values of the first dimension a "top ten" left out --
        # publishers, not pairings. A chart that narrowed silently is one
        # a reader takes for the whole picture.
        "narrowed_away": narrowed,
        "thresholds": {
            "min_articles": int(spec.get("min_articles") or 0),
            "min_publishers": int(spec.get("min_publishers") or 0),
        },
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


def run_story_map(spec, scopes):
    """Return {'points': [...], 'areas': [...]} plus meta for a story map."""
    import json as _json

    from datasets.geo import to_county

    base = _base_queryset(spec, scopes)

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

    # The shaded layer, as the March map defines it: regional stories
    # only. A regional story has no central point by design (the
    # pipeline records geo_skip_reason "regional_uses_place_set"), so
    # its geography is the place set — the counties it names. This is
    # deliberately not every mention: the dots carry the centrals, the
    # shading carries the geography the dots do not claim.
    area_scope = spec.get("area_scope", "regional") or None
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
    if not points and not areas:
        meta["empty_because"] = _why_nothing_mapped(spec, scopes)
    return {"points": points, "areas": areas, "meta": meta}


def _why_nothing_mapped(spec, scopes):
    """Which filter empties the map, with the numbers behind it.

    "No mapped stories" is true and useless, and so is a guess dressed as
    a diagnosis -- "the newsrooms published nothing" is not something a
    reader should have to take on trust when they know the county has
    newspapers. So each filter is relaxed in turn and the counts are
    reported: whichever one takes the total from something to nothing is
    the one to change.
    """
    narrowed = _base_queryset(spec, scopes).count()
    if narrowed:
        return (
            f"{narrowed:,} articles match, and none carry a location the map "
            "can place. They may not be enriched yet."
        )

    # Same slice, without the newsroom filter.
    without_publishers = dict(spec)
    without_publishers.pop("publishers", None)
    ignoring_newsrooms = _base_queryset(without_publishers, scopes).count()
    chosen = len(spec.get("publishers") or ())
    if chosen and ignoring_newsrooms:
        return (
            f"None of the {chosen:,} newsrooms chosen published between "
            f"{spec.get('from') or 'the start'} and {spec.get('to') or 'now'}, "
            f"though {ignoring_newsrooms:,} articles in this data did. "
            "Check the newsrooms, or widen the dates."
        )

    # Same slice, without the dates either.
    undated = {k: v for k, v in without_publishers.items() if k not in ("from", "to")}
    ignoring_dates = _base_queryset(undated, scopes).count()
    if ignoring_dates:
        return (
            f"Nothing published between {spec.get('from') or 'the start'} and "
            f"{spec.get('to') or 'now'}. {ignoring_dates:,} articles sit "
            "outside that range."
        )
    return "No articles in the datasets this visual is wired to."


def _cache_key(prefix, *parts):
    """A short, total key for a corpus answer.

    Everything that changes the answer goes in, and `scopes` above all:
    it is what a person is allowed to read, so a key without it would
    serve one author's counts to somebody with no grant on that dataset.
    Hashed because a spec with a publisher list is longer than a cache
    key may be.
    """
    import hashlib
    import json

    blob = json.dumps(parts, sort_keys=True, default=str)
    return (
        f"{prefix}.{corpus_version()}."
        f"{hashlib.sha1(blob.encode()).hexdigest()[:24]}"
    )


#: How long a counted answer is kept. Long, because the key carries the
#: version below: a stale entry is not possible, only an unused one. These
#: numbers change when the pipeline syncs -- at most every six hours, and
#: sometimes not for months -- so counting them once and keeping them is
#: the shape of the problem, not a ten-minute guess at it.
CORPUS_CACHE_SECONDS = 7 * 24 * 3600

#: How often the *version* is re-derived. This is the only query that runs
#: on a schedule rather than on a change, so it is the one that has to be
#: cheap: a max over an unindexed column and a count of a small table.
#: Five minutes is the longest a sync can go unnoticed.
VERSION_CACHE_SECONDS = 300


def corpus_version():
    """A stamp that changes when the corpus does, and not otherwise.

    Used in every cache key here, which is what lets the answers be kept
    for a week: an entry cannot go stale, because data that has moved
    lands under a different key. The alternative -- a short expiry -- pays
    for a recount every few minutes whether or not anything changed, and
    these recounts take tens of seconds.

    Two parts. The newest article covers a sync, and the number of
    dataset memberships covers a newsroom joining or leaving a dataset,
    which changes the counts without adding an article.
    """
    from django.core.cache import cache
    from django.db.models import Max

    from explorer.models import Article, DatasetSource

    hit = cache.get("corpus.version")
    if hit is not None:
        return hit
    newest = Article.objects.aggregate(m=Max("created_at"))["m"]
    stamp = (
        f"{newest.isoformat() if newest else 'empty'}:{DatasetSource.objects.count()}"
    )
    cache.set("corpus.version", stamp, VERSION_CACHE_SECONDS)
    return stamp


def question_stamp(spec, scopes):
    """A name for the question a preview is asking.

    Everything the rows depend on goes in: the spec, the datasets the
    asker may read, and the version of the corpus underneath. Change any
    of them and the stamp changes; change nothing and it does not.

    This is what lets a preview stop re-asking between panels. Walking
    Chart -> Look -> Data -> Newsrooms asked the corpus the identical
    question four times, and the four ran at once inside one container
    until each was waiting on the other three. It is not a cache of an
    output: nothing is kept here, and what the browser holds under this
    name can only ever be the answer to the question being asked.
    """
    return _cache_key("question", spec, sorted(scopes or ()))


def _exploded_values(dim_key, spec, scopes, limit):
    """[(value, articles)] for a dimension whose column holds a list.

    The same explosion the pivot runs, counted per value and named the way
    the chart names it -- a facet must offer what a reader will see, or
    ticking one filters nothing.
    """
    import json as _json

    from datasets.geo import county_of

    counts = {}
    column = explodes(dim_key)
    keep = DIMENSIONS[dim_key].get("explode_where")
    joined = bool(DIMENSIONS[dim_key].get("explode_join"))
    counties = DIMENSIONS[dim_key].get("geo_level") == "counties"
    rows = _base_queryset(spec, scopes).exclude(**{f"{column}__isnull": True})
    if keep is not None:
        rows = rows.filter(keep)
    rows = rows.values_list("id", column)
    for article, raw in rows:
        if joined:
            places = [raw] if raw else []
        else:
            try:
                places = _json.loads(raw) if raw else []
            except (TypeError, ValueError):
                continue
        seen = set()
        for place in places or ():
            value = county_of(place) if counties else place
            if value is None:
                continue
            seen.add(county_label(value) if counties else str(value))
        for value in seen:
            counts.setdefault(value, set()).add(article)
    ranked = sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [(value, len(ids)) for value, ids in ranked[:limit]]


def internal_fields(spec):
    """The fields in `spec` that a published visual may not carry.

    Class D of the field audit: internal use only. Everything inside the
    console works on them -- build a report, look at it, take the CSV --
    and none of it may be published, because what they say is a fact about
    our pipeline that a reader meets as a fact about the journalism.

    Returns labels rather than keys: whoever is told they cannot publish
    needs the name they chose, not the column behind it.

    Narrowing counts. Filtering to the stories a gate excluded, and then
    publishing the chart, publishes the gate's opinion even though the
    reason never appears on an axis.

    Nothing is exempt, table included. A table is where these are offered
    -- it is how data is taken out, read as rows and taken as a CSV -- and
    taking data out is not publishing it. The export stays open; the
    publish button does not.
    """
    spec = spec or {}
    keys = set(spec.get("dimensions") or [])
    keys |= {value for value in (spec.get("roles") or {}).values() if value}
    keys |= set((spec.get("only") or {}).keys())

    out = []
    for key in sorted(keys):
        dimension = DIMENSIONS.get(key)
        if dimension and dimension.get("internal"):
            out.append(dimension["label"])
    for key in sorted({spec.get("measure") or ""} | keys):
        measure = MEASURES.get(key)
        if measure and measure.get("internal"):
            out.append(measure["label"])
    return out


def values_of(dim_key, spec, scopes, limit=200):
    """[(value, articles)] for a dimension, most common first.

    What the fields step offers once a variable is chosen. Counted rather
    than listed: a facet without counts is a wall of checkboxes, and the
    count is what tells somebody whether narrowing to a value leaves them
    anything to draw.

    Narrowed by the spec the author has already built, so the values on
    offer are the ones actually present in their slice -- offering a county
    with no articles in it invites a filter that empties the chart.
    """
    if dim_key not in DIMENSIONS:
        raise CorpusSpecError(f"Unknown dimension: {dim_key}")

    # Counting these took the fields step to 65 seconds: one aggregate over
    # the whole article corpus per role a chart declares, three for a chord,
    # each joined through candidate_links to sources. The columns are
    # indexed; the cost is 164,000 rows grouped and counted, three times,
    # every time somebody arrives at the step.
    from django.core.cache import cache

    key = _cache_key(
        "corpus.values",
        dim_key,
        spec,
        sorted(scopes) if scopes is not ALL_SCOPES else "*",
        limit,
    )
    hit = cache.get(key)
    if hit is not None:
        return hit

    # A dimension holding several values per story has no expression to
    # group by -- the values are inside a text column. Counting them means
    # taking them apart, which is what the pivot already does; without this
    # the facet raised KeyError on "expr" and the panel showed nothing at
    # all for the one dimension whose values a reader most wants to narrow.
    if explodes(dim_key):
        out = _exploded_values(dim_key, spec, scopes, limit)
        cache.set(key, out, CORPUS_CACHE_SECONDS)
        return out

    alias = f"{DIM_PREFIX}{dim_key}"
    qs = _base_queryset(spec, scopes).annotate(**{alias: DIMENSIONS[dim_key]["expr"]})
    rows = (
        qs.exclude(**{f"{alias}__isnull": True})
        .values(alias)
        .annotate(n=Count("id"))
        .order_by("-n")[:limit]
    )
    out = [(str(r[alias]), r["n"]) for r in rows]
    cache.set(key, out, CORPUS_CACHE_SECONDS)
    return out
