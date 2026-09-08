"""An article exported without enrichment is in a queue that shows it.

`enrichment_skipped` is terminal AND exportable, so a row that reaches it
with no reason recorded looks finished: it exports, counts as local
coverage, and carries no scope, places or entities. Every selector
requires `status = 'labeled'`, so nothing revisits it, and every other
case here keys on a skip reason -- which it does not have. Exported,
counted, unenriched and unreviewable.

23 were found in March Missouri on 2026-09-06, written by the backfill
spike that predates the crawler's orchestrator. The code that wrote them
is gone and today's always records a reason, so this case is not for a
bug that still writes them: it is for the next thing that does.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from accounts.models import DATADESK, Grant
from explorer.models import (
    Article,
    ArticleEnrichment,
    CandidateLink,
    Dataset,
    DatasetSource,
    Source,
)
from review import queue as q

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def reviewer(client):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    client.force_login(user)
    return user


@pytest.fixture
def dataset(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    return source


def _article(source, pk, **kwargs):
    link = CandidateLink.objects.create(
        id=f"c-{pk}",
        source_id=source.id,
        dataset_id="d1",
        url=f"https://a.example/{pk}",
    )
    fields = {
        "title": f"Story {pk}",
        "status": "enrichment_skipped",
        "wire_check_status": "complete",
        "content": "A captured body of ordinary length.",
        "text": "A captured body of ordinary length.",
        "publish_date": timezone.now(),
        "created_at": timezone.now(),
        "enrichment_attempts": 0,
    }
    fields.update(kwargs)
    return Article.objects.create(id=pk, candidate_link=link, **fields)


def _ids(case):
    return set(Article.objects.filter(q._case_q(case)).values_list("id", flat=True))


def test_a_null_reason_is_selected(dataset):
    article = _article(dataset, "null")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=None)
    assert _ids(q.EXPORTED_UNENRICHED) == {"null"}


def test_an_empty_reason_is_selected(dataset):
    article = _article(dataset, "empty")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason="")
    assert _ids(q.EXPORTED_UNENRICHED) == {"empty"}


def test_no_enrichment_row_at_all_is_selected(dataset):
    """The third way a row says nothing, and the one a JOIN drops."""
    _article(dataset, "missing")
    assert _ids(q.EXPORTED_UNENRICHED) == {"missing"}


def test_a_recorded_reason_is_not_this_case(dataset):
    """Disjoint from the cases that share this status: they require a
    reason, this one requires its absence. A row cannot be in both."""
    article = _article(dataset, "stub")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason="paywall_stub")
    assert _ids(q.EXPORTED_UNENRICHED) == set()
    assert _ids(q.PAYWALL_STUB) == {"stub"}


def test_an_enriched_article_is_not_this_case(dataset):
    article = _article(dataset, "done", status="enriched")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=None)
    assert _ids(q.EXPORTED_UNENRICHED) == set()


def test_it_is_on_the_landing_view(dataset, client, reviewer):
    """Not behind a filter. These are invisible everywhere else, so a
    landing view that hid them would be the defect a second time."""
    article = _article(dataset, "null")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=None)
    body = client.get(reverse("review:queue")).content.decode()
    assert "Story null" in body


def test_the_row_says_what_is_wrong_with_it(dataset, client, reviewer):
    """With no reason on the row, the fallback that names a flag from the
    status has to know this status, or the row reads only 'flagged'."""
    article = _article(dataset, "null")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=None)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "Exported, never enriched, no reason given" in body


def test_the_sign_in_count_includes_it(dataset):
    """todo counts from the queue's own landing selector, so adding the
    case there is what puts it on somebody's list -- no second query to
    disagree with the page."""
    from review.todo import _count_extraction

    article = _article(dataset, "null")
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=None)
    assert _count_extraction(Dataset.objects.get(slug="mo")) == 1


def test_the_case_is_offered_like_the_others(dataset):
    """A case with no label or note renders as a blank filter option."""
    assert q.EXPORTED_UNENRICHED in q.CASE_STATUS
    assert q.CASE_LABELS[q.EXPORTED_UNENRICHED]
    assert q.CASE_NOTES[q.EXPORTED_UNENRICHED]
