"""Each queue row groups its facts by what they are about.

A reviewer reads a row to answer two separate questions: what is this
story, and where does the pipeline have it. Those were interleaved --
the byline sat two columns away from the headline it belongs to, under
a heading about what the article "has", sharing a line with the wire
verdict, which is not a fact about the story at all but about the
pipeline's judgement of it. So the byline moves under the headline and
the wire chip moves under the status chip it qualifies.
"""

import re

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/queue/"

CELLS = re.compile(r"<(th|td)\b[^>]*>(.*?)</\1>", re.S)


@pytest.fixture
def reviewer(client):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    client.force_login(user)
    return user


@pytest.fixture
def row(client, reviewer, crawler_schema):
    """The cells of one flagged row, in the order they are rendered."""
    from datetime import timedelta

    from django.utils import timezone

    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(
        id="s1", host="a.example", host_norm="a.example", canonical_name="The Herald"
    )
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(
        id="cl1", source_id=source.id, url="https://a.example/one"
    )
    Article.objects.create(
        id="a1",
        candidate_link=link,
        title="A flagged story",
        author="Jane Doe",
        status="not_article",
        wire_check_status="local",
        content="A captured body.",
        text="A captured body.",
        publish_date=timezone.now() - timedelta(days=2),
        created_at=timezone.now() - timedelta(days=2),
        enrichment_attempts=0,
    )
    content = client.get(URL, {"all": "1"}).content.decode()
    line = next(r for r in content.split("<tr") if "A flagged story" in r)
    return [body for _tag, body in CELLS.findall(line)]


def test_the_byline_reads_under_the_headline(row):
    story = row[0]
    assert "A flagged story" in story
    assert "by Jane Doe" in story
    # Below the date and length, not above them: the block reads
    # headline, when and how long, who wrote it.
    assert story.index("character") < story.index("by Jane Doe")


def test_an_article_with_no_byline_still_says_so_there(client, reviewer, row):
    """`no byline` is the finding a reviewer is often looking for, so it
    moves with the byline rather than staying behind in the old column."""
    Article.objects.filter(id="a1").update(author="")
    content = client.get(URL, {"all": "1"}).content.decode()
    line = next(r for r in content.split("<tr") if "A flagged story" in r)
    story = CELLS.findall(line)[0][1]
    assert "no byline" in story


def test_the_wire_verdict_reads_under_the_status(row):
    flagged = row[1]
    assert "data-status=" in flagged
    assert "data-wire=" in flagged
    assert flagged.index("data-status=") < flagged.index("data-wire=")


def test_what_the_article_has_is_only_what_it_has(row):
    """The third column answers one question now. Leaving the byline or
    the wire chip behind as well as moving them is the way this edit
    fails quietly."""
    label = row[2]
    assert "Jane Doe" not in label
    assert "data-wire=" not in label


def test_the_moved_lines_are_styled_where_they_now_sit():
    css = pytest.importorskip("pathlib").Path("static/css/datadesk.css").read_text()
    assert ".queue-rows th .byline" in css
    # Sized with the status chip it sits under, not with body text.
    assert ".queue-rows .wire-note .chip { font-size: .7rem; }" in css
