"""Four things a reviewer asked the queue for.

A reviewer works this page by reading a story, deciding what it is, and
narrowing to the rows they mean. Each of these was in the way of that:

- the dialog holding the story closed only from its own button, so a
  click on the page behind it did nothing;
- the story's own URL opened in the same tab, over the queue, losing
  every decision marked on the way down it;
- the list of what an article really is offered seven types, and the
  pipeline recognises more than that;
- the window offered 30, 90, 365 and everything, and a question about
  one month is none of those.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review import dispositions
from review import queue as review_queue

STATIC = "static/js/record-editor.js"


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def dated_articles(crawler_schema):
    """Three stories: this week, last month, last year."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    made = {}
    for name, when in (
        ("thisweek", timezone.now() - timedelta(days=3)),
        ("lastmonth", timezone.now() - timedelta(days=40)),
        ("lastyear", timezone.now() - timedelta(days=400)),
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


# --- the dialog closes when the page behind it is clicked ---------------------


def _editor_source():
    from django.conf import settings

    return (settings.BASE_DIR / STATIC).read_text()


def test_a_click_outside_the_dialog_closes_it():
    source = _editor_source()
    assert "editor.close()" in source
    assert "getBoundingClientRect" in source, (
        "a click on the dialog's own padding must not close it, which "
        "reading only the event target cannot tell apart from the backdrop"
    )


def test_the_close_button_is_still_there():
    """Not everyone reaches for the backdrop, and a dialog with no visible
    way out is a trap."""
    assert "rec-editor-close" in _editor_source()


# --- the story opens where it does not cost the session -----------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_story_url_opens_in_a_new_tab(client, reviewer, dated_articles):
    client.force_login(reviewer)
    page = client.get(reverse("explorer:article_detail", args=["thisweek"]))
    body = page.content.decode()
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer"' in body, "a new tab needs the opener closed"


# --- what a person can say the article is -------------------------------------


def test_the_types_offered_are_the_ones_the_pipeline_knows():
    offered = {t["value"] for t in dispositions.CONTENT_TYPES}
    assert offered == set(dispositions.TYPE_BECOMES), (
        "a type on the form with no status behind it is refused on submit, "
        "and a status with no type cannot be chosen at all"
    )


@pytest.mark.parametrize(
    "value,label",
    [
        ("news", "News"),
        ("opinion", "Opinion"),
        ("weather", "Weather"),
        ("wire", "Wire"),
        ("obituary", "Obituary"),
        ("not_article", "Not an article"),
        ("paywall", "Paywalled stub"),
        ("out_of_scope", "Non-local"),
        ("video", "Video"),
        ("photo_gallery", "Photo gallery"),
    ],
)
def test_every_type_a_reviewer_asked_for_is_offered(value, label):
    assert {"value": value, "label": label} in dispositions.CONTENT_TYPES


def test_out_of_scope_says_what_the_reviewer_means():
    """The label named the pipeline's status. A reviewer is saying the
    story is about somewhere else."""
    labels = {t["value"]: t["label"] for t in dispositions.CONTENT_TYPES}
    assert labels["out_of_scope"] == "Non-local"


def test_a_video_and_a_gallery_are_not_articles_to_the_pipeline():
    """Neither is a status the crawler writes, and inventing one would
    put a value the pipeline does not recognise on the article."""
    assert dispositions.TYPE_BECOMES["video"] == "not_article"
    assert dispositions.TYPE_BECOMES["photo_gallery"] == "not_article"


def test_news_goes_back_to_where_the_stage_rewinds_to():
    """The one type that puts an article BACK. A fixed status would send
    an enrichment-stage row to `cleaned` and re-run labelling on an
    article that had already been through it."""
    assert dispositions.TYPE_BECOMES["news"] == dispositions.REWIND
    assert dispositions.rewind_target(dispositions.EXTRACTION) == "cleaned"
    assert dispositions.rewind_target(dispositions.ENRICHMENT) == "labeled"


# --- the window, and a month that is not a number of days ---------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_custom_range_takes_both_bounds(reviewer, dated_articles):
    since = (timezone.now() - timedelta(days=60)).date().isoformat()
    until = (timezone.now() - timedelta(days=20)).date().isoformat()
    shown = {
        a.id
        for a in review_queue.queued(
            {"days": "custom", "since": since, "until": until}, reviewer
        )
    }
    assert shown == {"lastmonth"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_one_bound_is_a_real_question(reviewer, dated_articles):
    """ "Everything before the March export" needs no start."""
    until = (timezone.now() - timedelta(days=200)).date().isoformat()
    shown = {
        a.id for a in review_queue.queued({"days": "custom", "until": until}, reviewer)
    }
    assert shown == {"lastyear"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_custom_range_with_neither_bound_is_everything(reviewer, dated_articles):
    """It is the state the page is in the moment "Custom…" is chosen, and
    an empty queue there would read as no work rather than no dates."""
    shown = {a.id for a in review_queue.queued({"days": "custom"}, reviewer)}
    assert {"thisweek", "lastmonth", "lastyear"} <= shown


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_half_typed_date_does_not_narrow_anything(reviewer, dated_articles):
    """The field is a filter and somebody is still typing in it."""
    shown = {
        a.id
        for a in review_queue.queued({"days": "custom", "since": "2026-0"}, reviewer)
    }
    assert {"thisweek", "lastmonth", "lastyear"} <= shown


def test_the_date_parser_reads_a_date_and_refuses_the_rest():
    assert review_queue._parse_date("2026-03-04") == date(2026, 3, 4)
    for bad in ("", None, "4 March", "2026-13-40"):
        assert review_queue._parse_date(bad) is None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_custom_option_and_its_two_dates_are_on_the_page(
    client, reviewer, dated_articles
):
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    assert 'value="custom"' in body
    assert 'name="since"' in body and 'name="until"' in body
    assert 'type="date"' in body, "the browser's calendar, not a typed format"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_dates_are_rendered_open_when_the_url_asks_for_them(
    client, reviewer, dated_articles
):
    """A bookmarked custom range has to render as one with no script."""
    client.force_login(reviewer)
    body = client.get(
        reverse("review:queue"), {"days": "custom", "since": "2026-03-01"}
    ).content.decode()
    assert "js-custom-range hidden" not in body
    assert 'value="2026-03-01"' in body
