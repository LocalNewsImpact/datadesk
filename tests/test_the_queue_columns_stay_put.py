"""The disposition buttons are in the same place on every row.

A reviewer works down this table pressing the same button forty times.
The column widths were the browser's, worked out from the content, so
the longest headline in a group decided where every other column
started -- and the buttons moved from row to row and from publisher to
publisher. The eye had to find them again each time.

The four columns are declared instead, the headline wraps inside its
half, and the widths are percentages of the table so they hold at every
size the breakpoint allows.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

CSS = Path(settings.BASE_DIR) / "static/css/datadesk.css"
TEMPLATE = Path(settings.BASE_DIR) / "templates/review/_queue_results.html"


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def two_headlines(crawler_schema):
    """One short headline and one very long one, in the same group."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    titles = {
        "short": "Council votes",
        "long": (
            "County commission approves the annual operating budget after a "
            "four-hour hearing in which residents of three townships spoke "
            "against the proposed levy increase and the presiding "
            "commissioner adjourned twice"
        ),
    }
    for name, title in titles.items():
        link = CandidateLink.objects.create(
            id=f"c-{name}", source_id=source.id, url=f"https://a.example/{name}"
        )
        Article.objects.create(
            id=name,
            candidate_link=link,
            title=title,
            status="not_article",
            wire_check_status="complete",
            content="A captured body.",
            text="A captured body.",
            author="Ellen Reporter",
            publish_date=timezone.now(),
            created_at=timezone.now(),
            enrichment_attempts=0,
        )
    return titles


def _queue_rules():
    """The declarations that apply to the queue's table."""
    return "\n".join(
        line for line in CSS.read_text().splitlines() if ".queue-rows" in line
    )


def test_the_widths_are_declared_not_inferred():
    """`table-layout: fixed` is what makes the declared widths the ones
    used; without it the longest cell still decides."""
    assert "table-layout: fixed" in _queue_rules()


def test_the_story_column_is_no_more_than_half():
    rules = _queue_rules()
    width = re.search(r"col\.story \{ width: (\d+)%", rules)
    assert width, "the story column has no declared width"
    assert int(width.group(1)) <= 50


def test_all_four_columns_are_declared():
    """Three of four is the same problem: whatever is left over goes to
    the one that was not named."""
    rules = _queue_rules()
    for column in ("story", "flagged", "has", "decide"):
        assert f"col.{column} {{ width:" in rules, f"{column} has no width"


def test_the_widths_are_relative_so_they_hold_at_any_size():
    """Fixed pixels would hold the columns still and break the table at
    the narrow end of the breakpoint instead."""
    widths = re.findall(r"col\.\w+ \{ width: (\d+)(%|px|rem|em)", _queue_rules())
    assert widths, "no column widths found"
    assert {unit for _n, unit in widths} == {"%"}
    assert sum(int(n) for n, _unit in widths) == 100


def test_the_headline_wraps_rather_than_pushing():
    """`table.rec-table tbody th` is `nowrap`, which is right for a field
    name in a record table and wrong for a headline."""
    rules = _queue_rules()
    assert "white-space: normal" in rules


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_table_declares_its_columns(client, reviewer, two_headlines):
    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "<colgroup>" in body
    for column in ("story", "flagged", "has", "decide"):
        assert f'<col class="{column}">' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_long_headline_is_not_truncated_to_fit(client, reviewer, two_headlines):
    """Wrapping is the point; cutting the headline off would trade one
    problem for a worse one."""
    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert two_headlines["long"] in body


# --- what a row is flagged as ------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_paywalled_stub_is_flagged_as_one(client, reviewer, crawler_schema):
    """ "Exported unenriched" is where the article ended up. The flag is
    `paywall_stub`, and the reviewer needs to know the story is cut off
    by a login prompt."""
    from explorer.models import ArticleEnrichment

    dataset = Dataset.objects.create(id="d2", slug="mo2", label="Missouri")
    source = Source.objects.create(id="s2", host="b.example", host_norm="b.example")
    DatasetSource.objects.create(id="ds2", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(
        id="c-stub", source_id=source.id, url="https://b.example/stub"
    )
    article = Article.objects.create(
        id="stub",
        candidate_link=link,
        title="Subscribers only: council votes",
        status="enrichment_skipped",
        wire_check_status="complete",
        content="Sign in to continue reading.",
        text="Sign in to continue reading.",
        publish_date=timezone.now(),
        created_at=timezone.now(),
        enrichment_attempts=0,
    )
    ArticleEnrichment.objects.create(article_id=article.id, skip_reason="paywall_stub")

    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"days": "all"}).content.decode()
    assert "paywall_stub" in body
    assert "Story cut off by login prompt" in body


def test_the_flag_is_read_from_what_was_recorded():
    """Not from the status: two different flags share `enrichment_skipped`
    and reading it would make them the same row."""
    from review import queue as review_queue

    class Row:
        status = "enrichment_skipped"
        enr_skip_reason = "paywall_stub"
        enr_gate_reason = ""
        metadata = {}

    flag, hint = review_queue.flag_of(Row())
    assert flag == "paywall_stub"
    assert hint == "Story cut off by login prompt"


def test_a_scope_exclusion_keeps_the_place_it_names():
    """`scope_excluded_ukraine` says which somewhere else, and that is
    what a reviewer judges."""
    from review import queue as review_queue

    class Row:
        status = "enrichment_skipped"
        enr_skip_reason = "scope_excluded_ukraine"
        enr_gate_reason = ""
        metadata = {}

    flag, hint = review_queue.flag_of(Row())
    assert flag == "scope_excluded"
    assert "ukraine" in hint


def test_a_row_with_no_recorded_reason_still_says_something_useful():
    """The case it matched is the flag. Repeating the status here is what
    this replaced."""
    from review import queue as review_queue

    class Row:
        status = "not_article"
        enr_skip_reason = ""
        enr_gate_reason = ""
        metadata = {}

    flag, hint = review_queue.flag_of(Row())
    assert flag == "minimal_capture"
    assert hint
