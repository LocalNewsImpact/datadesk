"""Publishers whose parser returns the same thing every time.

A parser meeting a page shape it does not handle returns one string for
every article on the site, and the tell is that the body length repeats
to the character.

Production, 2026-09-04, before anybody had reported one:

    newspressnow.com       228 chars  486 articles  472 of them `wire`
    westplainsdailyquill   121 chars  613 articles  148 of them `labeled`

The first is a comment policy recorded as wire syndication 472 times. The
second is a subscriber wall, and 148 of them went into the pipeline as
real articles.
"""

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from explorer.models import Article, CandidateLink, Source
from review import extraction_problems
from review.models import RepeatedBody


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


def _articles(crawler_schema, host, body, count, status="labeled"):
    source, _ = Source.objects.get_or_create(
        id=host, defaults={"host": host, "host_norm": host}
    )
    for n in range(count):
        link = CandidateLink.objects.create(
            id=f"{host}-{status}-{n}", source_id=source.id, url=f"https://{host}/{n}"
        )
        Article.objects.create(
            id=f"{host}-{status}-a{n}",
            candidate_link=link,
            title=f"story {n}",
            text=body,
            content=body,
            status=status,
            wire_check_status="complete",
            enrichment_attempts=0,
            created_at=timezone.now(),
        )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_repeated_body_is_found(crawler_schema):
    _articles(crawler_schema, "repeats.example", "B" * 220, 12)
    call_command("find_repeated_bodies", "--min-articles", "10")

    found = RepeatedBody.objects.get()
    assert found.host == "repeats.example"
    assert found.length == 220
    assert found.articles == 12


@pytest.mark.django_db(databases=["default", "crawler"])
def test_ordinary_variation_is_not_a_pattern(crawler_schema):
    """Bodies of differing lengths are articles, which is the normal
    case and must not fill this list."""
    source = Source.objects.create(
        id="varied.example", host="varied.example", host_norm="varied.example"
    )
    for n in range(30):
        link = CandidateLink.objects.create(
            id=f"v{n}", source_id=source.id, url=f"https://varied.example/{n}"
        )
        Article.objects.create(
            id=f"va{n}",
            candidate_link=link,
            text="C" * (200 + n),
            status="labeled",
            wire_check_status="complete",
            enrichment_attempts=0,
        )
    call_command("find_repeated_bodies", "--min-articles", "10")
    assert not RepeatedBody.objects.exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_statuses_say_what_was_done_with_them(crawler_schema):
    """The finding is not that a body repeats. It is that a failed
    capture was confidently labelled something."""
    _articles(crawler_schema, "mixed.example", "D" * 300, 8, status="wire")
    _articles(crawler_schema, "mixed.example", "D" * 300, 6, status="labeled")
    call_command("find_repeated_bodies", "--min-articles", "10")

    found = RepeatedBody.objects.get()
    assert found.statuses == {"wire": 8, "labeled": 6}
    row = extraction_problems.repeated_bodies()[0]
    # `wire` already excludes an article; `labeled` sends it on. Six of
    # these are in the pipeline.
    assert row.total_reaching_the_pipeline == 6


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_pattern_that_stopped_is_still_datable(crawler_schema):
    """Whether it is still happening is the difference between a list
    somebody works and a graveyard. The newest article says which."""
    _articles(crawler_schema, "dated.example", "E" * 250, 11)
    call_command("find_repeated_bodies", "--min-articles", "10")
    assert RepeatedBody.objects.get().latest_article is not None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_pattern_that_no_longer_holds_is_removed(crawler_schema):
    """Otherwise the list is a record of what was once true, which is
    not a thing anybody opens twice."""
    RepeatedBody.objects.create(host="gone.example", length=200, articles=99)
    call_command("find_repeated_bodies", "--min-articles", "10")
    assert not RepeatedBody.objects.filter(host="gone.example").exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_sample_says_which_boilerplate_it_is(crawler_schema):
    """ "Short and repeated" describes a comment policy and a subscriber
    wall equally. Only the words say which parser rule is missing."""
    _articles(
        crawler_schema,
        "sample.example",
        "BE PART OF THE CONVERSATION " + "x" * 200,
        11,
    )
    call_command("find_repeated_bodies", "--min-articles", "10")
    assert "BE PART OF THE CONVERSATION" in RepeatedBody.objects.get().sample


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_shows_them(client, reviewer, crawler_schema):
    _articles(crawler_schema, "page.example", "F" * 260, 11, status="wire")
    call_command("find_repeated_bodies", "--min-articles", "10")

    client.force_login(reviewer)
    body = client.get(reverse("review:extraction_problems")).content.decode()
    assert "page.example" in body
    assert "Bodies that repeat exactly" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_says_how_the_list_is_filled(client, reviewer, crawler_schema):
    client.force_login(reviewer)
    body = client.get(reverse("review:extraction_problems")).content.decode()
    assert "find_repeated_bodies" in body
