"""Wire was the largest exclusion in the corpus and the last one nobody
could check.

Measured 2026-09-07 against March 2026 Mizzou, the only cleaned window:
12,962 articles kept, 9,335 excluded as wire. Obituaries and minimal
captures were reviewable and have been reviewed in quantity; wire was
not, so a wrongly excluded article was simply absent -- a Type II error
with no other surface, which is the expensive kind, because nothing
downstream ever looks at what was removed.

The case does not doubt-rank. Several of the methods behind these
attributions are already at or above 99% precision, and a method that
good does not earn a reviewer's time -- so the axis that matters is
WHICH method decided, not how sure it was. `articles.wire` records the
syndication each row was attributed to, and working the queue one
syndication at a time is what makes precision per method fall out of the
review. That number is what says which of them can run unreviewed.
"""

import pytest

from explorer.models import Article, CandidateLink
from review import queue as q


def _article(**kw):
    source = kw.pop("source", None)
    link = CandidateLink.objects.create(
        id=kw.pop("link_id", "cl-1"),
        url=kw.pop("url", "https://a.example/one"),
        source=source,
    )
    fields = {
        "id": kw.pop("id", "a-1"),
        "candidate_link": link,
        "status": "wire",
        "wire_check_status": "complete",
    }
    fields.update(kw)
    return Article.objects.create(**fields)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_case_is_offered_at_all(crawler_schema):
    """It has to be in all three maps or the queue cannot render it."""
    assert q.CASE_STATUS[q.WIRE_EXCLUSION] == "wire"
    assert q.CASE_LABELS[q.WIRE_EXCLUSION]
    assert q.CASE_NOTES[q.WIRE_EXCLUSION]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_wire_article_reaches_the_queue(crawler_schema):
    _article(id="w1", wire=["NPR"])

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.WIRE_EXCLUSION))
        .values_list("id", flat=True)
    )

    assert ids == {"w1"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_case_does_not_narrow_to_a_doubted_subset(crawler_schema):
    """Unlike obituaries, which the detector scores. Nothing records how
    sure a wire decision was, so narrowing would be inventing a signal."""
    _article(id="w1", wire=["NPR"])
    _article(id="w2", link_id="cl-2", url="https://a.example/two", wire=None)

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.WIRE_EXCLUSION))
        .values_list("id", flat=True)
    )

    assert ids == {"w1", "w2"}, "a row with no recorded service is still excluded"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_can_be_worked_one_syndication_at_a_time(crawler_schema):
    """The axis the review is allocated along. A method at 99% precision
    should not bury one that is not."""
    _article(id="npr", wire=["NPR"])
    _article(
        id="ap",
        link_id="cl-2",
        url="https://a.example/two",
        wire=["The Associated Press"],
    )

    qs = q._apply_common(
        q.base_queryset_unscoped().filter(q._case_q(q.WIRE_EXCLUSION)),
        {"service": "NPR"},
    )

    assert set(qs.values_list("id", flat=True)) == {"npr"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_article_naming_several_syndications_answers_to_each(crawler_schema):
    """`articles.wire` is an array and rows carry more than one. Filtering
    on NPR must find "NPR, The Associated Press", or the counts per
    service are wrong in the direction that hides work."""
    _article(id="both", wire=["NPR", "The Associated Press"])

    for service in ("NPR", "The Associated Press"):
        qs = q._apply_common(
            q.base_queryset_unscoped().filter(q._case_q(q.WIRE_EXCLUSION)),
            {"service": service},
        )
        assert set(qs.values_list("id", flat=True)) == {"both"}, service
