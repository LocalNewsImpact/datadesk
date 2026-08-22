"""The data explorer: grid views over the crawler corpus (SCOPE.md §2.2).

Read-only throughout — Phase 1 has no write path, and the connection
role couldn't write anyway. Filters are the ones that mattered in the
March reconciliation: dataset, status, wire state, publisher, date
range, label confidence.

Filter vocabularies (statuses, wire states, labels, scopes, geographic
skip reasons) are read from the data, cached briefly — the grid never
invents statuses the pipeline doesn't know (SCOPE.md §2.2).
"""

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import F
from django.http import Http404
from django.shortcuts import render

from accounts.decorators import admin_required, role_required
from datasets.geo import level_for_geoid, name_for_geoid
from explorer.costs import billed_costs, recorded_costs
from explorer.models import (
    Article,
    ArticleEnrichment,
    ArticleGeoid,
    ArticleOrganization,
    ArticlePerson,
    ArticlePlace,
    Dataset,
    DatasetSource,
)

PAGE_SIZE = 50
_VOCAB_CACHE_KEY = "explorer.article_filter_vocab"
_ENRICHMENT_VOCAB_CACHE_KEY = "explorer.enrichment_filter_vocab"
_VOCAB_CACHE_SECONDS = 300


def _distinct(model, field):
    """The values a column actually holds, nulls excluded, sorted."""
    return sorted(
        model.objects.filter(**{f"{field}__isnull": False})
        .values_list(field, flat=True)
        .distinct()
    )


def _filter_vocab():
    """Distinct filter values as the data defines them, or None offline."""

    def fetch():
        return {
            "datasets": list(Dataset.objects.order_by("label").values("slug", "label")),
            "statuses": sorted(
                Article.objects.values_list("status", flat=True).distinct()
            ),
            "wire_statuses": sorted(
                Article.objects.values_list("wire_check_status", flat=True).distinct()
            ),
            "labels": _distinct(Article, "primary_label"),
            "scopes": _distinct(ArticleEnrichment, "scope"),
            "geo_skip_reasons": _distinct(ArticleEnrichment, "geo_skip_reason"),
        }

    try:
        return cache.get_or_set(_VOCAB_CACHE_KEY, fetch, _VOCAB_CACHE_SECONDS)
    except DatabaseError:
        return None


# Sortable columns, with the direction each opens in. A publisher list
# reads alphabetically; a corpus reads newest first.
SORTS = {
    "date": ("Published", "publish_date", "desc"),
    "publication": ("Publisher", "candidate_link__source__host_norm", "asc"),
}


def _sort_state(params):
    """The requested sort, defaulted and bounded to the sortable columns."""
    key = params.get("sort", "date")
    if key not in SORTS:
        key = "date"
    direction = params.get("dir")
    if direction not in ("asc", "desc"):
        direction = SORTS[key][2]
    return key, direction


def _ordering(key, direction):
    field = F(SORTS[key][1])
    primary = (
        field.desc(nulls_last=True)
        if direction == "desc"
        else field.asc(nulls_last=True)
    )
    # created_at breaks ties so paging is stable across requests.
    return [primary, "-created_at"]


def sort_headers(key, direction):
    """Header descriptors: which column is active, and where a click goes."""
    headers = {}
    for name, (label, _field, default) in SORTS.items():
        active = name == key
        headers[name] = {
            "label": label,
            "active": active,
            # Clicking the active column reverses it; clicking another
            # opens it in its natural direction.
            "next_dir": (
                ("asc" if direction == "desc" else "desc") if active else default
            ),
            "indicator": ("▾" if direction == "desc" else "▴") if active else "",
        }
    return headers


def _filtered_articles(params):
    """Apply the grid filters from the query string to the corpus.

    Scope, the central-FIPS claim and the geographic skip reason live on
    article_enrichment; they join through the reverse one-to-one, and are
    annotated onto the row so the grid can show them without a query per
    article.
    """
    qs = Article.objects.select_related("candidate_link__source").annotate(
        enr_scope=F("enrichment__scope"),
        enr_point_place=F("enrichment__point_place"),
        enr_point_geoid=F("enrichment__point_geoid"),
        enr_point_level=F("enrichment__point_geoid_level"),
    )

    if slug := params.get("dataset"):
        # The canonical membership path (the crawler's own enrichment
        # queries): article → candidate_link.source_id → dataset_sources.
        member_sources = DatasetSource.objects.filter(dataset__slug=slug).values(
            "source_id"
        )
        qs = qs.filter(candidate_link__source_id__in=member_sources)
    if status := params.get("status"):
        qs = qs.filter(status=status)
    if wire := params.get("wire"):
        qs = qs.filter(wire_check_status=wire)
    if publisher := params.get("publisher"):
        # Text search over host and canonical name; hundreds of sources
        # make a dropdown unwieldy and a search box is how March worked.
        qs = qs.filter(candidate_link__source__host_norm__icontains=publisher.lower())
    if label := params.get("label"):
        qs = qs.filter(primary_label=label)
    if date_from := params.get("from"):
        qs = qs.filter(publish_date__date__gte=date_from)
    if date_to := params.get("to"):
        qs = qs.filter(publish_date__date__lte=date_to)
    if conf_min := params.get("conf_min"):
        qs = qs.filter(primary_label_confidence__gte=conf_min)
    if conf_max := params.get("conf_max"):
        qs = qs.filter(primary_label_confidence__lte=conf_max)
    if q := params.get("q"):
        qs = qs.filter(title__icontains=q)
    if scope := params.get("scope"):
        qs = qs.filter(enrichment__scope=scope)
    if geo_skip := params.get("geo_skip"):
        qs = qs.filter(enrichment__geo_skip_reason=geo_skip)
    # The central-geography claim, present or absent. "no" includes both
    # an enrichment record without a claim and no enrichment record at
    # all — from the grid's seat those read the same.
    if (fips := params.get("fips")) == "yes":
        qs = qs.filter(enrichment__point_geoid__isnull=False)
    elif fips == "no":
        qs = qs.filter(enrichment__point_geoid__isnull=True)

    return qs.order_by(*_ordering(*_sort_state(params)))


@role_required
def articles(request):
    vocab = _filter_vocab()
    params = request.GET.copy()
    params.pop("page", None)
    sort_params = params.copy()
    sort_params.pop("sort", None)
    sort_params.pop("dir", None)
    sort, direction = _sort_state(request.GET)
    context = {
        "crawler_connected": vocab is not None,
        "vocab": vocab,
        "params": params,
        "sort_params": sort_params,
        "sort": sort,
        "dir": direction,
        "headers": sort_headers(sort, direction),
    }
    if vocab is not None:
        try:
            page_number = int(request.GET.get("page", "1"))
        except ValueError:
            page_number = 1
        paginator = Paginator(_filtered_articles(request.GET), PAGE_SIZE)
        context["page"] = paginator.get_page(page_number)

    # htmx swaps just the results region; a plain GET renders the page.
    template = (
        "explorer/_articles_results.html"
        if request.headers.get("HX-Request")
        else "explorer/articles.html"
    )
    return render(request, template, context)


# The enrichment record's category dimensions, in the order the detail
# view walks them. The pipeline's vocabulary, not ours.
DIMENSIONS = ("scope", "subject", "topic", "format", "timeframe", "user_need")


def _rows(queryset):
    """Materialize a related-entity queryset, tolerating an absent table.

    The entity tables arrived with the enrichment backfield; a crawler
    database that predates it (or a test fixture that does not create
    them) yields an empty section rather than a 500.
    """
    try:
        return list(queryset)
    except DatabaseError:
        return []


def geography_rows(article_id, enrichment):
    """The story's geography of record, as one table.

    article_geoids is the source: it holds the complete set and the
    is_primary flag that separates the central claim from a mention.
    Names come from the Census gazetteer, so a code never appears without
    the place it stands for. article_places is extraction detail and
    rides along on the row it matches rather than forming a second table.
    """
    geoid_rows = _rows(
        ArticleGeoid.objects.filter(article_id=article_id).order_by("geoid")
    )
    places = _rows(ArticlePlace.objects.filter(article_id=article_id))

    detail_by_geoid = {}
    for place in places:
        if place.geoid:
            detail_by_geoid.setdefault(place.geoid, place)

    claim = enrichment.point_geoid if enrichment else None
    entries = {
        row.geoid: {
            "geoid": row.geoid,
            "level": row.geoid_level,
            "is_primary": bool(row.is_primary),
            "source": row.source,
        }
        for row in geoid_rows
    }

    # Older records predate article_geoids. Rebuild the same shape from
    # the claim and the mention list rather than showing nothing.
    if not entries and enrichment:
        if claim:
            entries[claim] = {
                "geoid": claim,
                "level": enrichment.point_geoid_level,
                "is_primary": True,
                "source": enrichment.point_method,
            }
        for geoid in enrichment.mentioned_geoids():
            entries.setdefault(
                geoid,
                {
                    "geoid": geoid,
                    "level": level_for_geoid(geoid),
                    "is_primary": False,
                    "source": "mention",
                },
            )

    # Still nothing, but extraction did code some places: use those
    # rather than showing an empty table. article_geoids stays
    # authoritative wherever it has rows, so this never adds noise to a
    # record the pipeline curated.
    if not entries:
        for place in places:
            if place.geoid:
                entries.setdefault(
                    place.geoid,
                    {
                        "geoid": place.geoid,
                        "level": place.geoid_level or level_for_geoid(place.geoid),
                        "is_primary": False,
                        "source": "place_extraction",
                    },
                )

    rows = []
    for entry in entries.values():
        # A tract or block has no gazetteer name; for the claim the
        # model's own point_place is the real answer.
        fallback = (
            enrichment.point_place if enrichment and entry["is_primary"] else None
        )
        name, kind = name_for_geoid(entry["geoid"], fallback=fallback)
        rows.append(
            {
                **entry,
                "name": name,
                "name_kind": kind,
                "detail": detail_by_geoid.get(entry["geoid"]),
            }
        )

    # The claim leads; mentions follow alphabetically.
    rows.sort(key=lambda row: (not row["is_primary"], row["name"] or "", row["geoid"]))

    # Places extraction found but could not code have no row to ride on.
    # They are named in a trailing note, not a second table.
    uncoded = sorted(
        {
            str(place)
            for place in places
            if not place.geoid and (place.full_name or place.mention_text)
        }
    )
    return rows, uncoded


@role_required
def article_detail(request, article_id):
    """The side-by-side view (SCOPE.md §2.2): stored text next to the whole
    enrichment record — every category with its confidence and rationale,
    the central-geography claim with its basis and ZIP, the mention FIPS,
    the extracted people, organizations and places, and cost.

    This is the screen an operator uses to judge whether enrichment is
    right, so the claim and the mentions are shown as what they are: two
    separate assertions. `article_enrichment.geoids` lists mentions only —
    the central claim is never repeated there — and `article_geoids` holds
    the superset with an is_primary flag.
    """
    try:
        article = (
            Article.objects.select_related("candidate_link__source")
            .filter(id=article_id)
            .first()
        )
    except DatabaseError as exc:
        # No crawler connection: nothing at this URL, honestly.
        raise Http404("Crawler database not connected") from exc
    if article is None:
        raise Http404("No such article")

    try:
        enrichment = ArticleEnrichment.objects.filter(article_id=article_id).first()
    except DatabaseError:
        enrichment = None

    dimensions = (
        [
            (
                name,
                getattr(enrichment, name),
                getattr(enrichment, f"{name}_confidence"),
                (
                    (enrichment.rationales or {}).get(name)
                    if isinstance(enrichment.rationales, dict)
                    else None
                ),
            )
            for name in DIMENSIONS
        ]
        if enrichment
        else []
    )

    # Rationales the pipeline recorded under keys that are not dimension
    # names still belong on screen; the dimension table has already shown
    # the rest.
    extra_rationales = (
        {
            key: value
            for key, value in enrichment.rationales.items()
            if key not in DIMENSIONS
        }
        if enrichment and isinstance(enrichment.rationales, dict)
        else {}
    )

    geography, uncoded = geography_rows(article_id, enrichment)

    return render(
        request,
        "explorer/article_detail.html",
        {
            "article": article,
            "enrichment": enrichment,
            "dimensions": dimensions,
            "extra_rationales": extra_rationales,
            "geography": geography,
            "uncoded_places": uncoded,
            "people": _rows(
                ArticlePerson.objects.filter(article_id=article_id).order_by(
                    "-mention_count", "name"
                )
            ),
            "organizations": _rows(
                ArticleOrganization.objects.filter(article_id=article_id).order_by(
                    "-mention_count", "name"
                )
            ),
            "stored_text": article.content or article.text or article.text_excerpt,
        },
    )


def _enrichment_vocab():
    """Distinct enrichment filter values, or None offline."""

    def fetch():
        def distinct(field):
            return sorted(
                ArticleEnrichment.objects.filter(**{f"{field}__isnull": False})
                .values_list(field, flat=True)
                .distinct()
            )

        return {
            "datasets": list(Dataset.objects.order_by("label").values("slug", "label")),
            "scopes": distinct("scope"),
            "skip_reasons": distinct("skip_reason"),
            "geo_skip_reasons": distinct("geo_skip_reason"),
            "geoid_levels": distinct("point_geoid_level"),
        }

    try:
        return cache.get_or_set(
            _ENRICHMENT_VOCAB_CACHE_KEY, fetch, _VOCAB_CACHE_SECONDS
        )
    except DatabaseError:
        return None


def _filtered_enrichment(params):
    """The enrichment grid's filters (SCOPE.md §2.2): dataset, geography
    (scope, FIPS, skip reason), confidence band."""
    qs = ArticleEnrichment.objects.select_related("article__candidate_link__source")

    if slug := params.get("dataset"):
        member_sources = DatasetSource.objects.filter(dataset__slug=slug).values(
            "source_id"
        )
        qs = qs.filter(article__candidate_link__source_id__in=member_sources)
    if scope := params.get("scope"):
        qs = qs.filter(scope=scope)
    if fips := params.get("fips"):
        # Prefix match: a state FIPS finds every place within it.
        qs = qs.filter(point_geoid__startswith=fips)
    if level := params.get("level"):
        qs = qs.filter(point_geoid_level=level)
    if skip := params.get("skip"):
        qs = qs.filter(skip_reason=skip)
    if geo_skip := params.get("geo_skip"):
        qs = qs.filter(geo_skip_reason=geo_skip)
    if params.get("no_point"):
        qs = qs.filter(point_geoid__isnull=True)
    if conf_min := params.get("conf_min"):
        qs = qs.filter(scope_confidence__gte=conf_min)
    if conf_max := params.get("conf_max"):
        qs = qs.filter(scope_confidence__lte=conf_max)

    return qs.order_by(F("enriched_at").desc(nulls_last=True))


@role_required
def enrichment(request):
    vocab = _enrichment_vocab()
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "crawler_connected": vocab is not None,
        "vocab": vocab,
        "params": params,
    }
    if vocab is not None:
        try:
            page_number = int(request.GET.get("page", "1"))
        except ValueError:
            page_number = 1
        paginator = Paginator(_filtered_enrichment(request.GET), PAGE_SIZE)
        context["page"] = paginator.get_page(page_number)

    template = (
        "explorer/_enrichment_results.html"
        if request.headers.get("HX-Request")
        else "explorer/enrichment.html"
    )
    return render(request, template, context)


@admin_required
def costs(request):
    """The cost dashboard (SCOPE.md §2.5): recorded vs billed by day,
    recorded by dataset and model, the cache discount as the headline."""
    recorded = recorded_costs()
    billed = billed_costs()

    # Join the two sides by day for the comparison table.
    by_day = {}
    if recorded:
        for row in recorded["by_day"]:
            by_day[row["day"]] = {
                "day": row["day"],
                "recorded": row["cost"],
                "articles": row["articles"],
            }
    if billed:
        for row in billed["by_day"]:
            entry = by_day.setdefault(row["day"], {"day": row["day"]})
            entry["billed"] = row["billed"]
            entry["cache_discount"] = row["cache_discount"]
    days = sorted(by_day.values(), key=lambda r: str(r["day"]), reverse=True)

    return render(
        request,
        "explorer/costs.html",
        {
            "recorded": recorded,
            "billed": billed,
            "days": days,
        },
    )
