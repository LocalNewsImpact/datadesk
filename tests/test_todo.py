"""What is waiting for the person signing in.

Three review surfaces, each scoped to the datasets somebody may write, so
what is waiting differs by who is asking. Without this a reviewer visits
three pages to find out whether there is anything for them.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review import todo
from review.proposals import ChangeProposal


@pytest.fixture
def editor(client, db):
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.fixture
def work(crawler_schema):
    mo = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    Dataset.objects.create(id="d2", slug="wa", label="Washington")
    source = Source.objects.create(
        id="s1",
        host="a.example",
        host_norm="a.example",
        canonical_name="The Paper",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source_id=source.id)
    link = CandidateLink.objects.create(id="c1", source_id=source.id, url="u")
    Article.objects.create(
        id="a1",
        candidate_link=link,
        status="not_article",
        wire_check_status="complete",
        content="A body.",
        author="Jo Reporter",
        title="Flagged",
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id="s1",
        record_label="The Paper",
        dataset="mo",
        field="city",
        current_value="",
        proposed_value="Columbia",
        state=ChangeProposal.PENDING,
        flag="city_missing",
    )
    return mo


# --- what it lists ------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_dataset_with_work_is_listed(editor, work):
    rows = todo.for_user(editor)
    assert [row["slug"] for row in rows] == ["mo"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_dataset_with_nothing_waiting_is_absent(editor, work):
    """A to-do listing zeroes is a list people stop reading."""
    assert "wa" not in {row["slug"] for row in todo.for_user(editor)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_each_queue_is_named_separately(editor, work):
    keys = {task.key for row in todo.for_user(editor) for task in row["tasks"]}
    assert {"proposals", "extraction", "paywalls"} >= keys
    assert "proposals" in keys and "extraction" in keys


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_task_links_to_its_own_dataset(editor, work):
    """A link that lands on every directory's work is a link somebody has
    to filter before they can start."""
    for row in todo.for_user(editor):
        for task in row["tasks"]:
            assert f"dataset={row['slug']}" in task.url


# --- the number has to match the page -----------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_article_count_is_what_the_queue_would_show(editor, work):
    """Counted from the queue's own selector, narrowing included. The raw
    status holds 1,186 rows in production and the queue puts far fewer in
    front of anybody, so counting the status would promise work the page
    does not show -- and a number that disagrees with the page it links to
    is the one somebody plans their morning around."""
    from review import queue as review_queue

    rows = todo.for_user(editor)
    task = next(t for row in rows for t in row["tasks"] if t.key == "extraction")
    assert task.count == review_queue.queued({"dataset": "mo"}, editor).count()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_total_is_the_sum_of_the_parts(editor, work):
    rows = todo.for_user(editor)
    assert todo.total_for(editor) == sum(r["total"] for r in rows)


# --- scoped to the person asking ----------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_somebody_with_no_grants_has_nothing_waiting(db, work):
    stranger = User.objects.create_user("nobody", email="no@localnewsimpact.org")
    assert todo.for_user(stranger) == []
    assert todo.total_for(stranger) == 0


# --- and it reaches the page ---------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_landing_page_shows_it(client, editor, work):
    body = client.get(reverse("landing")).content.decode()
    assert "Waiting for you" in body
    assert "Missouri" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_sidebar_carries_the_count(client, editor, work):
    """Visible from every page, not only the one somebody lands on."""
    body = client.get(reverse("landing")).content.decode()
    assert "nav-count" in body
