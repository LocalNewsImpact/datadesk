"""The grid sorts by when a row last changed, not only when it was published.

`publish_date` is the newsroom's clock and `created_at` is the row's birth.
Neither answers the question a curation pass asks: what has this pipeline
touched? A status change, a headline repair, a metadata note or a retraction
moves neither one -- and those are most of what a reviewer does here.

The crawler added `articles.last_modified` with a `BEFORE INSERT OR UPDATE`
trigger (crawler #620) so the stamp holds for writers outside that repo, this
console through `datadesk_rw` included. This is the console half: the column
is readable, sortable, and shown.

`extracted_at` is deliberately NOT offered as the answer -- it moves only when
the body is rewritten, so a run that changed 226 statuses leaves it untouched.
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/explorer/articles/"


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    return user


@pytest.fixture
def touched(crawler_schema):
    """Three stories published together, changed on different days.

    Published on one date so `publish_date` cannot order them, which is what
    makes the assertions about `last_modified` rather than about the default.
    """
    dataset = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    source = Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Tribune",
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)

    published = datetime(2026, 3, 1, tzinfo=UTC)
    for i, day in ((1, 10), (2, 20), (3, 15)):
        Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            url=f"https://example.org/{i}",
            title=f"Story {i}",
            status="labeled",
            wire_check_status="complete",
            created_at=published,
            publish_date=published,
            last_modified=datetime(2026, 4, day, tzinfo=UTC),
        )
    return dataset


def _results(client, params=None):
    """The rows alone, without the filter bar that also names publishers."""
    return client.get(
        URL, params or {}, headers={"hx-request": "true"}
    ).content.decode()


def _order(content, titles):
    return sorted(titles, key=content.index)


def test_most_recently_changed_first(client, viewer, touched):
    client.force_login(viewer)
    got = _order(
        _results(client, {"sort": "modified"}), ("Story 1", "Story 2", "Story 3")
    )
    # Changed Apr 20, Apr 15, Apr 10 -- and all published the same day, so
    # this ordering can only have come from last_modified.
    assert got == ["Story 2", "Story 3", "Story 1"]


def test_the_direction_reverses(client, viewer, touched):
    client.force_login(viewer)
    got = _order(
        _results(client, {"sort": "modified", "dir": "asc"}),
        ("Story 1", "Story 2", "Story 3"),
    )
    assert got == ["Story 1", "Story 3", "Story 2"]


def test_it_opens_newest_first(client, viewer, touched):
    from explorer.views import _sort_state

    # A corpus reads newest-changed first, like the date column.
    assert _sort_state({"sort": "modified"}) == ("modified", "desc")
    assert _sort_state({"sort": "modified", "dir": "asc"}) == ("modified", "asc")
    assert _sort_state({"sort": "modified", "dir": "sideways"}) == (
        "modified",
        "desc",
    )


def test_the_column_is_shown_with_a_sort_link(client, viewer, touched):
    client.force_login(viewer)
    content = _results(client)
    assert "Last modified" in content
    assert "sort=modified" in content


def test_a_row_with_no_stamp_sorts_last_and_renders(client, viewer, touched):
    client.force_login(viewer)
    link = CandidateLink.objects.get(id="cl1")
    Article.objects.create(
        id="a9",
        candidate_link=link,
        url="https://example.org/9",
        title="Story 9",
        status="labeled",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        publish_date=datetime(2026, 3, 1, tzinfo=UTC),
        last_modified=None,
    )
    content = _results(client, {"sort": "modified"})
    # nulls_last, so an unstamped row never displaces a real change; and the
    # cell falls back rather than printing "None".
    assert content.index("Story 9") > content.index("Story 1")
    assert "None" not in content


def test_the_empty_row_still_spans_every_column(client, viewer, touched):
    client.force_login(viewer)
    content = _results(client, {"status": "no-such-status"})
    assert "No articles match these filters" in content
    # One more column than before; a stale colspan leaves the message short
    # of the table's width.
    assert 'colspan="11"' in content or 'colspan="12"' in content


def test_the_body_is_not_loaded_to_sort_by_it(client, viewer, touched):
    client.force_login(viewer)
    # last_modified must not join `text`/`raw` in the defer list: it is what
    # the grid now orders and displays.
    from explorer.views import Article as ViewArticle

    deferred = ViewArticle.objects.defer("text", "raw", "text_excerpt")
    assert "last_modified" not in deferred.query.deferred_loading[0]
