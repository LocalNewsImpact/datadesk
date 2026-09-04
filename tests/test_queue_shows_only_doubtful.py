"""The queue holds what there is reason to doubt, not everything flagged.

At 175 extraction rejections per active day against roughly 815 articles,
an unfiltered queue is a backlog nobody works. The filter is the same rule
`classification_doubt` applies, expressed as a Q so the queue can still be
paginated and counted in the database.
"""

import json

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import (
    Article,
    CandidateLink,
    ContentTypeDetection,
    Dataset,
    DatasetSource,
    Source,
)
from review.queue import queued


@pytest.fixture
def reviewer(db):
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


@pytest.fixture
def corpus(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset=dataset, source_id=source.id)

    def article(pk, status, **kwargs):
        link = CandidateLink.objects.create(id=f"c{pk}", source_id=source.id, url="u")
        return Article.objects.create(
            id=pk,
            candidate_link=link,
            status=status,
            wire_check_status="complete",
            content="body",
            **kwargs,
        )

    # An obituary called on one body phrase: 0.17, no corroboration.
    doubted = article("obit-doubted", "obituary", title="Jim Morrison's grave")
    ContentTypeDetection.objects.create(
        article=doubted,
        detected_type="obituary",
        confidence_score=0.17,
        evidence=json.dumps({"content": ["passed away"]}),
    )
    # An obituary the URL and title agree with.
    sound = article("obit-sound", "obituary", title="Charles Morton 1931-2025")
    ContentTypeDetection.objects.create(
        article=sound,
        detected_type="obituary",
        confidence_score=0.38,
        evidence=json.dumps({"url": ["obituaries"], "title_patterns": ["1931-2025"]}),
    )
    article("na", "not_article", title="A rejected story")
    return dataset


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_obituary_called_on_one_phrase_is_in_the_queue(reviewer, corpus):
    assert "obit-doubted" in {a.id for a in queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_obituary_the_url_agrees_with_is_not(reviewer, corpus):
    """Average confidence rises to 0.38 where the path or title
    corroborates, and those calls are right."""
    assert "obit-sound" not in {a.id for a in queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_short_unbylined_rejection_is_not_on_the_landing_view(reviewer, corpus):
    """At 175 a day, not_article cannot be carried whole. A rejection with
    a short body, no byline and a reason from the gate is the ordinary
    case, and the ordinary case is what the narrowing removes."""
    assert "na" not in {a.id for a in queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_explicit_case_filter_shows_it_anyway(reviewer, corpus):
    """Asking for a case is asking to see what matches it."""
    from review.queue import MINIMAL_CAPTURE

    assert "na" in {a.id for a in queued({"case": MINIMAL_CAPTURE}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_sound_classification_is_not_in_the_queue_at_all(reviewer, corpus):
    """Not merely filtered out of the default view. A queue is for what
    looks wrong, and an obituary the URL and title both agree with does
    not, so `all=1` does not reach it either."""
    everything = {a.id for a in queued({"all": "1"}, reviewer)}
    assert "obit-sound" not in everything
    assert {"obit-doubted", "na"} <= everything


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_row_is_not_repeated_by_its_telemetry(reviewer, corpus):
    """The telemetry is a log, so an article can have several rows and the
    join would otherwise multiply it."""
    doubted = Article.objects.get(id="obit-doubted")
    ContentTypeDetection.objects.create(
        article=doubted,
        detected_type="obituary",
        confidence_score=0.17,
        evidence=json.dumps({"content": ["survived by"]}),
    )
    ids = [a.id for a in queued({}, reviewer)]
    assert ids.count("obit-doubted") == 1
