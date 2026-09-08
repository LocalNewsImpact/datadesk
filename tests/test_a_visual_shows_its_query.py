"""A BigQuery visual's SELECT has to be readable from the console.

The builder let one be written at creation and never again. The edit
page's own note said it is where a visual's plumbing is changed -- "its
data source, its query, its snapshot" -- and the query was the one thing
not there, so `march-article-count` carried 975 characters of SQL that
could only be read out of the database.
"""

import pytest
from django.urls import reverse

from accounts.models import DATADESK, Grant
from visuals.models import Visual


@pytest.fixture
def an_editor(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("vis", email="vis@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


@pytest.fixture
def a_bigquery_visual(db, an_editor):
    return Visual.objects.create(
        title="March article count",
        slug="march-article-count",
        source_kind="bigquery",
        # `builder` is what builder_edit looks for -- a visual of any
        # other template is not one this page owns.
        template="builder",
        query="SELECT 1 AS n, 'a' AS measure, 2 AS articles",
        created_by=an_editor,
    )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_query_is_on_the_page(client, an_editor, a_bigquery_visual):
    client.force_login(an_editor)
    body = client.get(
        reverse("visuals:builder_edit", args=[a_bigquery_visual.slug])
    ).content.decode()
    assert "SELECT 1 AS n" in body, "the SQL is not shown"
    assert 'name="query"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_query_can_be_changed(client, an_editor, a_bigquery_visual):
    client.force_login(an_editor)
    client.post(
        reverse("visuals:builder_edit", args=[a_bigquery_visual.slug]),
        {"form": "source", "query": "SELECT 9 AS n, 'b' AS measure, 8 AS articles"},
    )
    a_bigquery_visual.refresh_from_db()
    assert a_bigquery_visual.query == "SELECT 9 AS n, 'b' AS measure, 8 AS articles"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_empty_query_is_refused(client, an_editor, a_bigquery_visual):
    """The model already says a BigQuery visual needs its query. The form
    goes through that rule rather than restating it, so the two cannot
    drift."""
    client.force_login(an_editor)
    client.post(
        reverse("visuals:builder_edit", args=[a_bigquery_visual.slug]),
        {"form": "source", "query": "   "},
    )
    a_bigquery_visual.refresh_from_db()
    assert a_bigquery_visual.query.startswith("SELECT 1"), "the query was emptied"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_saving_the_query_does_not_run_it(client, an_editor, a_bigquery_visual):
    """Changing a query and running it are separate acts. A reviewer
    correcting a SELECT should be able to read it back before it replaces
    the snapshot everyone is looking at."""
    client.force_login(an_editor)
    client.post(
        reverse("visuals:builder_edit", args=[a_bigquery_visual.slug]),
        {"form": "source", "query": "SELECT 2 AS n, 'c' AS measure, 3 AS articles"},
    )
    assert not a_bigquery_visual.snapshots.exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_change_is_audited(client, an_editor, a_bigquery_visual):
    """A query decides what a published embed shows. Changing one is a
    change to what the public reads."""
    from audit.models import AuditLogEntry

    client.force_login(an_editor)
    client.post(
        reverse("visuals:builder_edit", args=[a_bigquery_visual.slug]),
        {"form": "source", "query": "SELECT 5 AS n, 'd' AS measure, 6 AS articles"},
    )
    entry = AuditLogEntry.objects.filter(action="visual:source").first()
    assert entry is not None
    assert entry.before["query"].startswith("SELECT 1")
    assert entry.after["query"].startswith("SELECT 5")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_corpus_pivot_has_no_source_box(client, an_editor):
    """A pivot is configured by the form above and has no SQL to show."""
    visual = Visual.objects.create(
        title="A pivot",
        slug="a-pivot",
        source_kind="corpus",
        template="builder",
        spec={"shape": "bar", "measure": "articles"},
        created_by=an_editor,
    )
    client.force_login(an_editor)
    body = client.get(
        reverse("visuals:builder_edit", args=[visual.slug])
    ).content.decode()
    assert 'value="source"' not in body
