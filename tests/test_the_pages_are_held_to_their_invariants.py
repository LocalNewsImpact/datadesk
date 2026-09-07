"""Tests that hold the pages to their rules, rather than to examples.

Two failures on 2026-09-07 stopped the extraction queue loading, and no
amount of additional row-level testing would have caught either. Both
were one filter sitting in the wrong list:

- `dataset` was treated as a request for rows, so choosing one turned off
  the landing narrowing and asked for every flagged article in it. That
  was 1,956 rows for Mizzou and fine, then 60,169 once wire, weather,
  opinion and paywall had cases, and the page stopped answering.
- `since` and `until` were not remembered, so a custom range came back
  with no bounds and read as the whole corpus.

Fixtures hold a handful of rows, so no assertion about content could
fail on either. The rule can be asserted directly, and these do.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.models import DATADESK, Grant
from review import queue as q
from review.views import QUEUE_FILTER_KEYS

# --- the filter classification ------------------------------------------------


def test_every_filter_is_either_a_scope_or_a_question():
    """A filter is one or the other, and which one decides whether the
    landing narrowing survives it. Adding one without deciding fails
    here rather than in a page that stops loading."""
    classified = set(q._SCOPES) | set(q._EXPLICIT)
    unclassified = set(QUEUE_FILTER_KEYS) - classified

    assert not unclassified, (
        f"{sorted(unclassified)} is neither a scope nor a question. A scope "
        "keeps the narrowing (dataset, dates); a question lifts it (case, "
        "band, publisher)."
    )


def test_a_scope_never_lifts_the_narrowing():
    """The rule the queue exists for: it holds what there is reason to
    doubt. Choosing which corpus to work does not change that."""
    for scope in q._SCOPES:
        assert not q._asked_for_something(
            {scope: "anything"}
        ), f"{scope} lifted the narrowing; scoping is not asking"


def test_a_question_always_lifts_the_narrowing():
    for question in q._EXPLICIT:
        assert q._asked_for_something(
            {question: "anything"}
        ), f"{question} did not lift the narrowing; asking for a set is"


def test_the_bounds_of_a_range_are_scopes_and_are_remembered():
    """A custom range that is not remembered comes back with no bounds,
    which reads as the whole corpus."""
    for bound in ("since", "until"):
        assert bound in QUEUE_FILTER_KEYS
        assert bound in q._SCOPES


# --- the landing view ---------------------------------------------------------


def test_every_case_is_on_the_landing_view_or_declared_not_to_be():
    """A case in CASE_STATUS but absent from `doubtful_q` is work nobody
    is shown by default, which is the condition these cases exist to end.

    Some belong off it -- wire has no recorded doubt signal, and 47,419
    undifferentiated rows would bury the cases that do carry a reason.
    That is a decision, so it is written down in
    CASES_OFF_THE_LANDING_VIEW and this fails if a new case is neither
    narrowed in nor declared out.
    """
    import inspect

    source = inspect.getsource(q.doubtful_q)
    undeclared = [
        case
        for case in q.CASE_STATUS
        if case.upper() not in source and case not in q.CASES_OFF_THE_LANDING_VIEW
    ]

    assert not undeclared, (
        f"{undeclared} can be chosen but never appears on the landing view. "
        "Narrow it into doubtful_q, or add it to CASES_OFF_THE_LANDING_VIEW "
        "with the reason."
    )


def test_nothing_is_declared_off_the_landing_view_by_accident():
    """The declaration only covers cases that exist."""
    unknown = q.CASES_OFF_THE_LANDING_VIEW - set(q.CASE_STATUS)

    assert not unknown, f"{sorted(unknown)} is not a case"


# --- every page renders -------------------------------------------------------


def _all_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _all_patterns(entry, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry


#: Routes a smoke walk cannot call: they need an object that does not
#: exist in an empty database, or they are not GETs.
_NEEDS_AN_OBJECT = ("<", "(?P<")

#: Third-party mounts. allauth's provider routes need a SocialApp row that
#: only production has, and they are not pages this repository renders.
_NOT_OURS = ("accounts/google/", "accounts/social/", "accounts/3rdparty/")


def _named_get_routes():
    for route, pattern in _all_patterns():
        if any(marker in route for marker in _NEEDS_AN_OBJECT):
            continue
        if any(route.startswith(prefix) for prefix in _NOT_OURS):
            continue
        if not pattern.name:
            continue
        namespace = "review" if route.startswith("review/") else None
        yield route, pattern.name, namespace


@pytest.fixture
def an_admin(client, db):
    user = User.objects.create_user("smoke", email="smoke@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_page_answers(client, an_admin, crawler_schema):
    """Every objectless GET route renders for somebody allowed to see it.

    A page that 500s is how the `enrichment_attempts` grant reached
    production: the queue rendered and failed only when a decision was
    submitted, and nothing walked the routes to notice.

    5xx only. A redirect is an answer, and so is a refusal.
    """
    broken = []
    for _route, name, namespace in _named_get_routes():
        try:
            url = reverse(f"{namespace}:{name}" if namespace else name)
        except Exception:
            continue
        try:
            response = client.get(url)
        except Exception as exc:  # an unhandled exception is the worst case
            broken.append(f"{url} raised {type(exc).__name__}: {exc}")
            continue
        if response.status_code >= 500:
            broken.append(f"{url} answered {response.status_code}")

    assert not broken, "pages that do not answer:\n  " + "\n  ".join(broken)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_answers_for_every_filter_on_its_own(
    client, an_admin, crawler_schema
):
    """Each filter applied alone. The two that broke the page were both
    reached by picking one control."""
    url = reverse("review:queue")
    values = {
        "dataset": "missouri",
        "days": "custom",
        "since": "2026-03-01",
        "until": "2026-04-01",
        "case": "wire_exclusion",
        "band": "empty",
        "publisher": "herald",
        "byline": "no",
        "state": "all",
        "all": "1",
    }
    broken = [
        f"{key}={value} -> {client.get(url, {key: value}).status_code}"
        for key, value in values.items()
        if client.get(url, {key: value}).status_code >= 500
    ]

    assert not broken, "the queue does not answer for:\n  " + "\n  ".join(broken)
