"""The queue looks at recent work, and its verbs say what they do.

TWO THINGS THAT WERE WRONG TOGETHER
-----------------------------------
Every article ever crawled is not a queue, it is an archive: the corpus
is 164,000 articles and the flagged ones go back to 2025, so a page that
opened on all of it buried this week under last December.

And on a scope-recorded article the two verbs read as the same answer.
"Accept: stays in the export, unenriched" and "Reject: it is a real
story, put it back" both say keep it, and for a story about somewhere
else neither is what a reviewer wants -- what they want is Out of scope,
in the list beside the buttons, which nothing pointed at.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review import queue as review_queue
from review.dispositions import _what_accept_does, _what_reject_does


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def two_articles(crawler_schema):
    """One from this week, one from last year."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    made = {}
    for name, when in (
        ("recent", timezone.now() - timedelta(days=3)),
        ("old", timezone.now() - timedelta(days=400)),
    ):
        link = CandidateLink.objects.create(
            id=f"c-{name}", source_id=source.id, url=f"https://a.example/{name}"
        )
        made[name] = Article.objects.create(
            id=name,
            candidate_link=link,
            title=f"a {name} story",
            status="not_article",
            wire_check_status="complete",
            content="A captured body.",
            text="A captured body.",
            author="Ellen Reporter",
            publish_date=when,
            created_at=when,
            enrichment_attempts=0,
        )
    return made


# --- the window ---------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_opens_on_recent_work(reviewer, two_articles):
    shown = {a.id for a in review_queue.queued({}, reviewer)}
    assert "recent" in shown
    assert "old" not in shown


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_window_can_be_widened(reviewer, two_articles):
    """A question about a publisher's history is a real question, just
    not the default one."""
    shown = {a.id for a in review_queue.queued({"days": "all"}, reviewer)}
    assert {"recent", "old"} <= shown


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_article_with_no_dates_is_kept(reviewer, two_articles, crawler_schema):
    """An extraction that found no publish date, written before
    created_at was populated: the worst captures in the corpus, and the
    ones this queue is for. A window that hid them would hide exactly
    what it should surface."""
    Article.objects.filter(id="old").update(publish_date=None, created_at=None)
    assert "old" in {a.id for a in review_queue.queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_nonsense_window_reads_as_the_default(reviewer, two_articles):
    """It comes from a query string."""
    shown = {a.id for a in review_queue.queued({"days": "everything"}, reviewer)}
    assert shown == {"recent"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_window_is_on_the_page(client, reviewer, two_articles):
    """A default that hides rows has to be visible, or it reads as data
    missing."""
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    assert 'name="days"' in body
    assert "Last 30 days" in body
    assert "Everything" in body


# --- the verbs on a scope row -------------------------------------------------


def test_reject_says_something_different_where_the_row_is_finished():
    """A scope-recorded article is `enrichment_skipped` and in the
    export. Rejecting the call sends it back to be enriched again; it
    does not put back something that was taken away."""

    class Scope:
        status = "enrichment_skipped"

    class Excluded:
        status = "not_article"

    assert _what_reject_does(Scope()) == "Wrong — say what it is below"
    assert _what_reject_does(Excluded()) == "It is a real story, put it back"


def test_accept_and_reject_do_not_read_as_the_same_answer():
    """On an exported row they both keep it, which is why the page has to
    point at the type list instead."""

    class Scope:
        status = "enrichment_skipped"

    accept = _what_accept_does(Scope())
    reject = _what_reject_does(Scope())
    assert accept != reject
    # Accept says it changes nothing, which is the fact a reviewer needs;
    # it used to describe the state and leave them to work that out.
    assert accept.startswith("Nothing changes")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_says_what_each_verb_will_do(client, reviewer, two_articles):
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    # Accept says it writes nothing; Reject carries the list.
    assert "Nothing is written" in body
    assert "Say what it is, in the list" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_empty_window_says_it_is_the_window(client, reviewer, two_articles):
    """Against production the last 30 days held nothing -- the crawl is
    paused and the newest flagged article is over 90 days old. Without
    this the page reads as broken rather than as quiet."""
    Article.objects.filter(id="recent").update(
        publish_date=timezone.now() - timedelta(days=400),
        created_at=timezone.now() - timedelta(days=400),
    )
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    assert "the last 30 days" in body
    assert "Look at everything" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_empty_everything_does_not_blame_the_window(
    client, reviewer, crawler_schema
):
    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "Look at everything" not in body


# --- rejecting a finished row needs the type ----------------------------------


def test_reject_always_carries_the_disposition():
    """Rejecting a flag says the call was wrong; the list says what it
    should have been. One without the other is half an answer, so the
    verb carries the list the way the sources queue's Fix carries the
    value it writes."""
    from review import kernel

    reject = kernel.get("extraction").verb("reject")
    assert reject.takes_value is True
    assert [choice["value"] for choice in reject.values]


def test_restore_is_offered_only_where_there_is_somewhere_to_put_it():
    """An article enrichment has finished with has already been through.
    "Back to the pipeline" there is a button that does nothing a reviewer
    means."""
    from review import kernel

    class Finished:
        status = "enrichment_skipped"
        content = "A body."
        text = "A body."

    class Excluded:
        status = "not_article"
        content = "A body."
        text = "A body."

    offered = {v.name for v in kernel.get("extraction").offered(Finished())}
    assert "restore" not in offered
    assert "reject" in offered

    offered = {v.name for v in kernel.get("extraction").offered(Excluded())}
    assert "restore" in offered


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reject_without_the_type_is_not_applied(reviewer, two_articles):
    """Left in the queue and counted, not applied as a blank. The buttons
    say the same thing; this is the server holding to it."""
    from review import kernel
    from review import submit as review_submit

    article = two_articles["recent"]
    Article.objects.filter(id=article.id).update(status="enrichment_skipped")
    article.refresh_from_db()

    receipt = review_submit.submit(
        kernel.get("extraction"),
        {article.id: ("reject", "")},
        {article.id: article},
        reviewer,
    )
    assert receipt["incomplete"] == 1
    assert receipt["decided"] == 0
    article.refresh_from_db()
    assert article.status == "enrichment_skipped"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reject_with_the_type_is_applied(reviewer, two_articles):
    from review import kernel
    from review import submit as review_submit

    article = two_articles["recent"]
    Article.objects.filter(id=article.id).update(status="enrichment_skipped")
    article.refresh_from_db()

    receipt = review_submit.submit(
        kernel.get("extraction"),
        {article.id: ("reject", "out_of_scope")},
        {article.id: article},
        reviewer,
    )
    assert receipt["decided"] == 1
    article.refresh_from_db()
    assert article.status == "out_of_scope"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_says_the_type_is_required(client, reviewer, two_articles):
    Article.objects.filter(id="recent").update(status="enrichment_skipped")
    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "Nothing is submitted until you do" in body
