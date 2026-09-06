"""A wall the rule caught is not reported as a wall the model judged.

The enrichment gate now settles the clearest paywall stubs with a free
rule -- a subscribe phrase in a body under 900 characters -- before it
spends a model call (MizzouNewsCrawler src/enrichment/orchestrator.py,
PAYWALL_RULE_SKIP_REASON). Those articles never reach the model, so if
this queue did not know the rule's reason they would leave the review
queue altogether, exactly when the rule most needs judging.

They are the same finding and the same status, so they belong in the
same case. They are not the same evidence -- a phrase and a length,
against a model's judgement -- so they are flagged in different words.
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
from review import queue as review_queue

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

RULE = "paywall_stub_rule"


@pytest.fixture
def reviewer(client):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    client.force_login(user)
    return user


@pytest.fixture
def stub(crawler_schema):
    """One article the rule diverted before enrichment."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="b.example", host_norm="b.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(
        id="c1", source_id=source.id, url="https://b.example/stub"
    )
    article = Article.objects.create(
        id="stub",
        candidate_link=link,
        title="Subscribers only: council votes",
        status="enrichment_skipped",
        wire_check_status="complete",
        content="To continue reading, please subscribe.",
        text="To continue reading, please subscribe.",
        publish_date=timezone.now(),
        created_at=timezone.now(),
        enrichment_attempts=0,
    )
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason=RULE)
    return article


def test_the_rules_reason_is_one_the_queue_selects(stub, client, reviewer):
    """Every one of them, from the first day the rule runs: a diverted
    article that no queue shows is a rule nobody can check."""
    assert RULE in review_queue.PAYWALL_STUB_SKIP_REASONS
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "Subscribers only: council votes" in body


def test_the_row_says_a_rule_found_it(stub, client, reviewer):
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "Login prompt matched by rule, before AI review" in body
    assert "Story cut off by login prompt" not in body


def test_the_two_walls_are_not_the_same_flag():
    rule_flag, rule_words = review_queue.FLAGS[RULE]
    model_flag, model_words = review_queue.FLAGS["paywall_stub"]
    assert rule_flag != model_flag
    assert rule_words != model_words


def test_a_rule_wall_is_still_a_paywall_stub(stub, client, reviewer):
    """It shares the case, so the counts a reviewer works from do not
    split in two the day the rule ships."""
    counted = Article.objects.filter(review_queue._case_q(review_queue.PAYWALL_STUB))
    assert list(counted.values_list("id", flat=True)) == ["stub"]
