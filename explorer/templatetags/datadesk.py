"""Template helpers for the design system.

Three small pieces of presentation logic that would otherwise be repeated
in every template, or expressed as unreadable template conditionals:
which sidebar entry is current, how confident a confidence value is, and
which filters are currently narrowing a grid.
"""

from django.template import Library
from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode

register = Library()


@register.simple_tag(takes_context=True)
def nav_active(context, url_name):
    """ "active" when the current path is at or below `url_name`.

    Prefix matching so an article detail keeps Articles marked, which is
    what a sidebar is for — showing where you are, not only what you
    clicked.
    """
    request = context.get("request")
    if request is None:
        return ""
    try:
        target = reverse(url_name)
    except NoReverseMatch:
        return ""
    if request.path == target or (target != "/" and request.path.startswith(target)):
        return "active"
    return ""


# A confidence value is easier to judge against a threshold than in the
# abstract. The bands are the ones the March review used when deciding
# which labels to re-check by hand.
HIGH_CONFIDENCE = 0.85
MID_CONFIDENCE = 0.65


@register.filter
def confidence_band(value):
    """ "high", "mid" or "low" for a confidence value; "" when absent."""
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number >= HIGH_CONFIDENCE:
        return "high"
    if number >= MID_CONFIDENCE:
        return "mid"
    return "low"


# Filter keys as an operator reads them. Keys not listed here (paging,
# sort) are not filters and never appear as a chip.
FILTER_LABELS = {
    "dataset": "dataset",
    "status": "status",
    "wire": "wire",
    "label": "CIN label",
    "publisher": "publisher",
    "scope": "geographic scope",
    "fips": "FIPS",
    "geo_skip": "geo skip",
    "skip": "skip reason",
    "level": "rung",
    "no_point": "no claim",
    "byline": "byline",
    "case": "flagged as",
    "band": "length",
    "from": "from",
    "to": "to",
    "conf_min": "conf ≥",
    "conf_max": "conf ≤",
    "q": "title",
}

_NOT_A_FILTER = {"page", "sort", "dir"}


def _filter_value_label(key, value):
    """A filter's value as a person would say it.

    The chip showed the query string's own word, so filtering the review
    queue read "Filtered by case scope_mislabel" -- which does not say
    that the scope in question is geographic. Raw enum values are the
    pipeline's vocabulary, not a reader's; every other surface here shows
    a label and keeps the raw value in a title attribute.

    The case labels live with the cases (review/queue.py) rather than
    being restated here, so renaming one renames it everywhere.
    """
    if key == "case":
        from review.queue import CASE_LABELS

        return CASE_LABELS.get(value, value)
    return value


@register.simple_tag
def active_filters(params):
    """The filters currently narrowing a grid, each with the query string
    that removes it.

    Returns a list of dicts rather than rendering, so a template can place
    the chips where it wants them.
    """
    if not params:
        return []

    # A facet may be chosen more than once -- three publishers is one
    # filter, not three. Reading a single value per key would show the last
    # one and, worse, drop the rest from every other chip's "remove me"
    # link, so taking off one filter would quietly discard the others.
    def values_of(key):
        getter = getattr(params, "getlist", None)
        raw = getter(key) if getter else [params.get(key)]
        return [v for v in raw if v]

    chips = []
    for key in dict.fromkeys(params):  # each key once, in the order given
        if key in _NOT_A_FILTER:
            continue
        values = values_of(key)
        if not values:
            continue
        remaining = [
            (other, value)
            for other in dict.fromkeys(params)
            if other != key
            for value in values_of(other)
        ]
        chips.append(
            {
                "key": key,
                "label": FILTER_LABELS.get(key, key),
                # One chip for the facet, named by how many were chosen --
                # eleven publishers do not fit on a chip and are not worth
                # eleven of them.
                "value": (
                    _filter_value_label(key, values[0])
                    if len(values) == 1
                    else f"{len(values)} selected"
                ),
                "raw": values[0] if len(values) == 1 else "",
                "without": urlencode(remaining),
            }
        )
    return chips


# Raw enum values are the pipeline's vocabulary, not a reader's. Every
# surface shows a human label and keeps the raw value in a title
# attribute, where it still helps when debugging.
STATUS_LABELS = {
    "enriched": "Enriched",
    "enrichment_skipped": "Exported unenriched",
    "labeled": "Labeled",
    "not_article": "Not an article",
    "out_of_scope": "Out of scope",
    "extracted": "Extracted",
}


@register.filter
def status_label(value):
    """An article status as a person reads it."""
    if not value:
        return "—"
    return STATUS_LABELS.get(value, value)


# wire_check_status has two passing values. 'complete' and 'local' both
# mean the check ran and found no syndication — 'local' is a legacy pass
# — so rendering the raw value produces "Wire: local", which reads as the
# opposite of what it means. 'error' and 'processing' are checks that
# never concluded, which is not the same as a local story.
WIRE_LABELS = {
    "complete": "Local",
    "local": "Local",
    "wire": "Wire",
    "error": "Check incomplete",
    "processing": "Check incomplete",
}


# Only three values mean the check concluded. Anything else — including a
# value the pipeline adds later — is a check that has not finished, which
# is a different fact from a local story and must not read as one.


@register.filter
def wire_label(value):
    """A wire-check status as a person reads it."""
    if not value:
        return "—"
    return WIRE_LABELS.get(value, "Check incomplete")


@register.filter
def wire_tone(value):
    """The tone a wire status carries: syndicated, unfinished, or fine."""
    if value == "wire":
        return "wire"
    if value in ("complete", "local"):
        return "local"
    return "incomplete"


@register.filter
def geoid_name(geoid):
    """The place a Census code stands for, or "" when unresolved.

    Wherever a FIPS code appears in the UI, its name appears with it; a
    column of bare codes is not readable.
    """
    from datasets.geo import name_for_geoid

    return name_for_geoid(geoid)[0] or ""


@register.simple_tag(takes_context=True)
def todo_count(context):
    """How much is waiting for this person, for the sidebar.

    Separate from `section_groups` so the navigation's own shape is not
    bent around a number, and read from cache rather than counted: the
    sidebar is on every page, and counting here would couple every page to
    a second database.
    """
    # The page that just counted passes the number in. Reading it back out
    # of a cache on that page would be a round trip to learn something the
    # view already knows -- and would go silently wrong wherever the cache
    # is unavailable.
    rows = context.get("todo")
    if rows:
        return sum(row["total"] for row in rows)

    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return 0
    from review import todo

    # Everywhere else: the cache the landing page filled, never a count.
    # Counting here would make every page in the console -- including ones
    # with nothing to do with review -- wait on the crawler database.
    return todo.cached_total_for(user) or 0


@register.simple_tag(takes_context=True)
def section_groups(context):
    """The navigation groups the signed-in role sees, in order.

    A group someone cannot reach is absent rather than disabled — a
    viewer sees a clean sidebar, not links to 403s. The list is
    accounts.sections, which is also what the access tests walk, so a
    section cannot appear here without its guard being checked.

    Takes the user rather than a role: roles are per dataset now, and a
    person holding several has no single role to ask about.
    """
    from accounts.sections import groups_for

    request = context.get("request")
    if request is None or not getattr(request, "user", None):
        return ()
    return groups_for(request.user)
