"""Exports in the standardized deliverable shape (SCOPE.md §2.4):
UTF-8 BOM CSVs, one logical row per physical line, article UUID as the
join key."""

import csv

from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse


def _enrichment(article):
    """The article's enrichment row, or None for an article never enriched."""
    try:
        return article.enrichment
    except ObjectDoesNotExist:
        return None


def _enriched(field):
    """A column read from `article_enrichment`; blank where there is none."""

    def read(article):
        value = getattr(_enrichment(article), field, None)
        return "" if value is None else value

    return read


def _person(person):
    detail = ", ".join(part for part in (person.title, person.affiliation) if part)
    return f"{person.name} ({detail})" if detail else person.name


def _by_mentions(rows):
    return sorted(rows, key=lambda row: (-(row.mention_count or 0), row.name or ""))


def _people(article):
    return "; ".join(_person(p) for p in _by_mentions(article.people.all()))


def _organizations(article):
    return "; ".join(o.name for o in _by_mentions(article.organizations.all()))


def _places(article):
    names = []
    for place in article.places.all():
        name = place.full_name or place.mention_text
        if name and name not in names:
            names.append(name)
    return "; ".join(names)


def _geoids(article):
    enrichment = _enrichment(article)
    return "; ".join(enrichment.mentioned_geoids()) if enrichment else ""


#: Columns that read a related list, and the prefetch each one needs. A
#: column not chosen costs no query.
_PREFETCH = {"people": "people", "organizations": "organizations", "places": "places"}

# What an export may carry. The UUID key is always first, uninvited.
EXPORT_COLUMNS = {
    "url": lambda a: a.url or "",
    "title": lambda a: a.title or "",
    "author": lambda a: a.author or "",
    "publish_date": lambda a: (
        a.publish_date.date().isoformat() if a.publish_date else ""
    ),
    "publisher": lambda a: (
        a.candidate_link.source.host_norm if a.candidate_link.source else ""
    ),
    "status": lambda a: a.status,
    "wire_check_status": lambda a: a.wire_check_status,
    "primary_label": lambda a: a.primary_label or "",
    "primary_label_confidence": lambda a: (
        "" if a.primary_label_confidence is None else a.primary_label_confidence
    ),
    # The secondary CIN label: the classifier's second-highest category.
    "alternate_label": lambda a: a.alternate_label or "",
    "alternate_label_confidence": lambda a: (
        "" if a.alternate_label_confidence is None else a.alternate_label_confidence
    ),
    # AI enrichment, from `article_enrichment`. Scope is the geographic
    # classification: where the story's impact falls.
    "scope": _enriched("scope"),
    "scope_confidence": _enriched("scope_confidence"),
    "subject": _enriched("subject"),
    "subject_confidence": _enriched("subject_confidence"),
    "topic": _enriched("topic"),
    "topic_confidence": _enriched("topic_confidence"),
    "format": _enriched("format"),
    "format_confidence": _enriched("format_confidence"),
    "timeframe": _enriched("timeframe"),
    "timeframe_confidence": _enriched("timeframe_confidence"),
    "user_need": _enriched("user_need"),
    "user_need_confidence": _enriched("user_need_confidence"),
    "is_news_content": _enriched("is_news_content"),
    "enrichment_skip_reason": _enriched("skip_reason"),
    # Central geography: the one place the story is about.
    "point_place": _enriched("point_place"),
    "point_geoid": _enriched("point_geoid"),
    "point_geoid_level": _enriched("point_geoid_level"),
    "point_lat": _enriched("point_lat"),
    "point_lon": _enriched("point_lon"),
    "point_method": _enriched("point_method"),
    # Every geography the story mentions.
    "places": _places,
    "geoids": _geoids,
    # People and organizations, most-mentioned first, "; "-separated.
    "people": _people,
    "organizations": _organizations,
    # The export column keeps its name; what it carries is the cleaned body.
    "content": lambda a: a.text or "",
}


def _one_line(value):
    """One logical row per physical line: embedded newlines become the
    literal two characters backslash-n, reversibly."""
    if isinstance(value, str):
        return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return value


def csv_response(queryset, columns, filename):
    """Stream the queryset as the standard deliverable CSV."""
    columns = [c for c in columns if c in EXPORT_COLUMNS]
    # Related rows in bulk, not one query per article per column.
    queryset = queryset.select_related("candidate_link__source", "enrichment")
    prefetch = [_PREFETCH[c] for c in columns if c in _PREFETCH]
    if prefetch:
        queryset = queryset.prefetch_related(*prefetch)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # utf-8-sig: Excel needs the BOM to read UTF-8 as UTF-8.
    response.write("\ufeff")
    writer = csv.writer(response, lineterminator="\n")
    writer.writerow(["article_uuid", *columns])
    for article in queryset.iterator(chunk_size=500):
        writer.writerow(
            [article.id, *(_one_line(EXPORT_COLUMNS[c](article)) for c in columns)]
        )
    return response
