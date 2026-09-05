"""Publishers whose parser returns the same boilerplate every time.

A parser meeting a page shape it does not handle returns the same block
for every article on the site -- a comment policy, a subscriber wall, a
list of counties.

WHAT THE TELL IS, AND WHAT IT IS NOT
------------------------------------
This looked for bodies of identical length, and that finds only the
cases where the body is nothing but the boilerplate. Lengths rarely
repeat to the character: the block repeats and what comes with it moves
the total by anything from five characters to fifty. The key is a
fingerprint of the block itself.

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
from review.models import Boilerplate

#: A block a parser returns instead of the story.
COMMENT_POLICY = (
    "We welcome comments from readers. Keep them civil, keep them on topic, "
    "and do not post anything you would not say to somebody's face. Comments "
    "are moderated and may be removed without notice. "
)


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


def _articles(crawler_schema, host, body, count, status="labeled"):
    """`body` may be a string, or a callable taking the article number."""
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
            text=body(n) if callable(body) else body,
            content=body(n) if callable(body) else body,
            status=status,
            wire_check_status="complete",
            enrichment_attempts=0,
            created_at=timezone.now(),
        )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_repeated_body_is_found(crawler_schema):
    _articles(crawler_schema, "repeats.example", "B" * 220, 12)
    call_command("find_boilerplate", "--min-articles", "10")

    found = Boilerplate.objects.get()
    assert found.host == "repeats.example"
    assert found.length == 220
    assert found.articles == 12


@pytest.mark.django_db(databases=["default", "crawler"])
def test_ordinary_variation_is_not_a_pattern(crawler_schema):
    """Ordinary articles, which is the normal case and must not fill this
    list.

    The bodies here differ in their words, not only in their length. A
    fixture of one repeated character used to pass this because the
    lengths differed; under a key that reads the text it would be found,
    correctly -- thirty bodies opening with the same 160 characters are
    the same opening whatever their totals are.
    """
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
            text=(
                f"The council heard item {n} on Tuesday evening, and the vote "
                f"that followed was {n} to {30 - n} after a debate about "
                f"whether the {n}th amendment belonged in the budget at all."
            ),
            status="labeled",
            wire_check_status="complete",
            enrichment_attempts=0,
        )
    call_command("find_boilerplate", "--min-articles", "10")
    assert not Boilerplate.objects.exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_statuses_say_what_was_done_with_them(crawler_schema):
    """The finding is not that a body repeats. It is that a failed
    capture was confidently labelled something."""
    _articles(crawler_schema, "mixed.example", "D" * 300, 8, status="wire")
    _articles(crawler_schema, "mixed.example", "D" * 300, 6, status="labeled")
    call_command("find_boilerplate", "--min-articles", "10")

    found = Boilerplate.objects.get()
    assert found.statuses == {"wire": 8, "labeled": 6}
    row = extraction_problems.boilerplate_patterns()[0]
    # `wire` already excludes an article; `labeled` sends it on. Six of
    # these are in the pipeline.
    assert row.total_reaching_the_pipeline == 6


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_pattern_that_stopped_is_still_datable(crawler_schema):
    """Whether it is still happening is the difference between a list
    somebody works and a graveyard. The newest article says which."""
    _articles(crawler_schema, "dated.example", "E" * 250, 11)
    call_command("find_boilerplate", "--min-articles", "10")
    assert Boilerplate.objects.get().latest_article is not None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_pattern_that_no_longer_holds_is_removed(crawler_schema):
    """Otherwise the list is a record of what was once true, which is
    not a thing anybody opens twice."""
    Boilerplate.objects.create(host="gone.example", length=200, articles=99)
    call_command("find_boilerplate", "--min-articles", "10")
    assert not Boilerplate.objects.filter(host="gone.example").exists()


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
    call_command("find_boilerplate", "--min-articles", "10")
    assert "BE PART OF THE CONVERSATION" in Boilerplate.objects.get().sample


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_body_that_is_only_boilerplate_is_one_finding(crawler_schema):
    """Identical bodies carry the same 160 characters at both ends, so
    they match under both fingerprints. Two rows for one pattern is a
    list that double-counts the thing it exists to count."""
    _articles(crawler_schema, "twice.example", COMMENT_POLICY, 12)
    call_command("find_boilerplate", "--min-articles", "10")

    rows = Boilerplate.objects.filter(host="twice.example")
    assert rows.count() == 1
    assert rows.first().matched_on == "opening"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_shows_them(client, reviewer, crawler_schema):
    _articles(crawler_schema, "page.example", "F" * 260, 11, status="wire")
    call_command("find_boilerplate", "--min-articles", "10")

    client.force_login(reviewer)
    body = client.get(reverse("review:extraction_problems")).content.decode()
    assert "page.example" in body
    assert "Boilerplate" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_says_how_the_list_is_filled(client, reviewer, crawler_schema):
    client.force_login(reviewer)
    body = client.get(reverse("review:extraction_problems")).content.decode()
    assert "find_boilerplate" in body


# --- what the exact-length key could not see ---------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_same_boilerplate_is_found_when_the_lengths_differ(crawler_schema):
    """The case the old key missed, and the common one.

    Every body opens with the same comment policy and ends with a
    different scrap of story, so the totals span fifty characters and no
    two match. Grouped by length these are twelve separate findings of
    one article each, which is to say nothing at all.
    """
    _articles(
        crawler_schema,
        "policy.example",
        lambda n: COMMENT_POLICY + "x" * (n * 4),
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    found = Boilerplate.objects.get(host="policy.example")
    assert found.articles == 12
    assert found.length != found.length_max, "the totals are meant to differ"
    assert found.length_max - found.length == 44


@pytest.mark.django_db(databases=["default", "crawler"])
def test_boilerplate_at_the_end_is_found_too(crawler_schema):
    """A parser that appends the block to a stub, rather than returning
    only the block. A fingerprint of the opening alone would miss it."""
    _articles(
        crawler_schema,
        "appended.example",
        lambda n: f"Story {n}. " + "y" * (n * 3) + COMMENT_POLICY,
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    found = Boilerplate.objects.filter(host="appended.example").first()
    assert found is not None
    assert found.matched_on == "ending"
    assert found.articles == 12


@pytest.mark.django_db(databases=["default", "crawler"])
def test_stories_that_merely_open_alike_are_not_boilerplate(crawler_schema):
    """A standing dateline or a section kicker is not a failed capture.
    The fingerprint is long enough that only a real shared block matches."""
    _articles(
        crawler_schema,
        "kicker.example",
        lambda n: "COLUMBIA — " + f"The council took up item {n} " * 12,
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    assert not Boilerplate.objects.filter(host="kicker.example").exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_fingerprint_ignores_whitespace_and_case(crawler_schema):
    """The same block emitted with different indentation on different
    pages is the same block."""
    _articles(
        crawler_schema,
        "spacing.example",
        lambda n: ("  " * n).join(COMMENT_POLICY.split(" ", 1)) + f" tail {n}",
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    assert Boilerplate.objects.filter(host="spacing.example").exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_one_row_per_publisher_and_block(crawler_schema):
    """Two different failures on one site are two findings; the same
    failure is not two."""
    _articles(crawler_schema, "two.example", lambda n: COMMENT_POLICY + "z" * n, 12)
    _articles(
        crawler_schema,
        "two.example",
        lambda n: "Subscribers only. " * 12 + str(n),
        12,
        status="paywall",
    )
    call_command("find_boilerplate", "--min-articles", "10")

    rows = Boilerplate.objects.filter(host="two.example")
    assert rows.count() == 2
    assert len({row.fingerprint for row in rows}) == 2


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_spread_is_not_bounded(crawler_schema):
    """One pattern, totals that match exactly and totals that differ by
    hundreds. The block is the finding; the arithmetic on lengths is not
    a filter, and the old key made it one."""
    _articles(
        crawler_schema,
        "spread.example",
        lambda n: COMMENT_POLICY + ("s" * (0 if n < 4 else n * 90)),
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    found = Boilerplate.objects.get(host="spread.example")
    assert found.articles == 12
    assert found.length_max - found.length > 500


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_long_body_carrying_the_block_is_still_found(crawler_schema):
    """Bodies over 5,000 characters were dropped, because at the old key
    a shared length that long was a coincidence. A shared opening is
    not."""
    _articles(
        crawler_schema,
        "long.example",
        lambda n: COMMENT_POLICY + f"A real story about item {n}. " * 300,
        12,
    )
    call_command("find_boilerplate", "--min-articles", "10")

    assert Boilerplate.objects.filter(host="long.example").exists()
