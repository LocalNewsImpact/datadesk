"""The processing page could not see the review backlog at all.

Every panel on it reads the pipeline's own tables -- link statuses,
article statuses, extraction telemetry -- and none of those can tell a
record a reviewer rewound from the backlog it is sitting in. 107 articles
at `cleaned` because a decision sent them back looked exactly like the
345 the pipeline put there.

`pipeline_rework` is the list of what was asked for, so this panel is the
only place the page can say what the review queues owe and what has been
carried. A count that stops falling is the signal that a stage is stuck.
"""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def corpus(crawler_schema):
    from explorer.models import Dataset, DatasetSource, Source

    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    other = Dataset.objects.create(id="d2", slug="vt", label="Vermont")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    return source, dataset, other


def _article(source, article_id, status, dataset_id="d1"):
    from explorer.models import Article, CandidateLink

    link = CandidateLink.objects.create(
        id=f"cl-{article_id}",
        url=f"https://a.example/{article_id}",
        source=source,
        status="extracted",
        dataset_id=dataset_id,
    )
    return Article.objects.create(
        id=article_id,
        status=status,
        candidate_link=link,
        dataset_id=dataset_id,
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 10, 12)),
    )


def _owed(record_type, record_id, stage, done=False, outcome=None):
    from explorer.models import PipelineRework

    return PipelineRework.objects.create(
        record_type=record_type,
        record_id=record_id,
        stage=stage,
        reason="review: accept, waiting for " + stage,
        requested_by="nightly-reconciliation",
        done_at=timezone.now() if done else None,
        outcome=outcome,
    )


def test_it_counts_what_is_waiting_and_what_was_carried(corpus):
    from explorer import processing

    source, *_ = corpus
    _article(source, "a1", "cleaned")
    _article(source, "a2", "labeled")
    _owed("article", "a1", "classify")
    _owed("article", "a2", "enrich", done=True, outcome="enriched")

    panel = processing.rework(["d1"])

    assert panel["open"] == 1
    by_stage = {s["stage"]: s for s in panel["stages"]}
    assert by_stage["classify"]["open"] == 1
    assert by_stage["classify"]["closed"] == 0
    assert by_stage["enrich"]["closed"] == 1
    assert by_stage["enrich"]["last"] is not None


def test_a_carried_record_says_what_status_it_reached(corpus):
    """The outcome is the status, not "we tried": that distinction is the
    whole reason a failed stage leaves its row open."""
    from explorer import processing

    source, *_ = corpus
    _article(source, "a3", "enriched")
    _owed("article", "a3", "enrich", done=True, outcome="enriched")

    carried = processing.rework(["d1"])["carried"]

    assert carried[0]["outcome"] == "enriched"
    assert carried[0]["record_id"] == "a3"
    assert "waiting for enrich" in carried[0]["reason"]


def test_a_link_is_scoped_by_its_own_dataset(corpus):
    """The table names a record, not a dataset. An article's dataset is on
    the article and a link's is on the link, so scoping by a column that
    does not exist would show every dataset's work on one page."""
    from explorer import processing
    from explorer.models import CandidateLink

    source, _, other = corpus
    CandidateLink.objects.create(
        id="l-mine",
        url="https://a.example/1",
        source=source,
        status="article",
        dataset_id="d1",
    )
    CandidateLink.objects.create(
        id="l-theirs",
        url="https://a.example/2",
        source=source,
        status="article",
        dataset_id="d2",
    )
    _owed("candidate_link", "l-mine", "extract")
    _owed("candidate_link", "l-theirs", "extract")

    assert processing.rework(["d1"])["open"] == 1
    assert processing.rework(["d2"])["open"] == 1
    assert processing.rework(None)["open"] == 2


def test_the_stages_are_in_pipeline_order(corpus):
    """extract, then classify, then enrich: the order a record travels."""
    from explorer import processing

    source, *_ = corpus
    _article(source, "a4", "cleaned")
    _article(source, "a5", "labeled")
    _owed("article", "a4", "classify")
    _owed("article", "a5", "enrich")
    _owed("candidate_link", "cl-a4", "extract")

    assert [s["stage"] for s in processing.rework(["d1"])["stages"]] == [
        "extract",
        "classify",
        "enrich",
    ]


def test_a_stage_nothing_owes_is_not_listed(corpus):
    """An empty row for every stage on every page is noise. Absence is the
    normal state."""
    from explorer import processing

    source, *_ = corpus
    _article(source, "a6", "cleaned")
    _owed("article", "a6", "classify")

    assert [s["stage"] for s in processing.rework(["d1"])["stages"]] == ["classify"]


def test_nothing_owed_renders_nothing(corpus):
    from explorer import processing

    panel = processing.rework(["d1"])
    assert panel == {"stages": [], "open": 0, "carried": []}


def test_the_page_renders_the_panel(client, corpus):
    """Through the view, so the context key and the template agree. A panel
    computed and never rendered is the same as no panel."""
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    source, *_ = corpus
    _article(source, "a7", "cleaned")
    _owed("article", "a7", "classify")

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)

    body = client.get(reverse("explorer:processing")).content.decode()

    assert "Review backlog being carried" in body
    assert "1 outstanding" in body
    assert "waiting for classify" not in body, "an open row has nothing to show yet"


def test_the_page_shows_what_was_carried(client, corpus):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    source, *_ = corpus
    _article(source, "a8", "enriched")
    _owed("article", "a8", "enrich", done=True, outcome="enriched")

    user = User.objects.create_user("ed2", email="ed2@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)

    body = client.get(reverse("explorer:processing")).content.decode()

    assert "Nothing\n    outstanding." in body or "Nothing" in body
    assert "waiting for enrich" in body, "the reason a record was carried"
    assert "enriched" in body
