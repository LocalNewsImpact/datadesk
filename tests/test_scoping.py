"""Rows are narrowed to the datasets somebody holds (ROADMAP item 1).

The guards in tests/test_admin_access.py decide whether a page opens.
These decide what is on it, and they are the half that fails quietly: a
wrong guard denies somebody and they say so within the hour, a missing
filter here shows them another dataset's stories and nobody notices.

So every test below is written as the leak rather than as the feature —
each one asserts that a story from a dataset the reader does not hold is
*absent*, not that the ones they do hold are present.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, UNIVERSAL, Grant
from accounts.privileges import CREATE, DESIGN, READ, WRITE
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from explorer.scoping import ALL_SCOPES, datasets_for, narrow, scopes_for

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

MINE = "mine"
THEIRS = "theirs"


@pytest.fixture
def two_datasets(crawler_schema):
    """Two datasets, one story each, joined the way the corpus joins them."""
    made = {}
    for i, slug in enumerate((MINE, THEIRS), start=1):
        dataset = Dataset.objects.create(id=i, slug=slug, label=slug.title())
        source = Source.objects.create(
            id=i, host=f"{slug}.example.org", canonical_name=slug.title()
        )
        DatasetSource.objects.create(id=i, dataset_id=dataset.id, source_id=source.id)
        link = CandidateLink.objects.create(
            id=i, url=f"https://{slug}.example.org/1", source_id=source.id
        )
        made[slug] = Article.objects.create(
            id=i, candidate_link_id=link.id, url=link.url, title=f"{slug} story"
        )
    return made


@pytest.fixture
def reader(db):
    return User.objects.create_user("reader", "reader@localnewsimpact.org", "x")


def grant(user, role, scope):
    return Grant.objects.create(user=user, app=DATADESK, scope=scope, role=role)


# --- the leak ---------------------------------------------------------------


def test_a_story_from_an_unheld_dataset_is_absent(reader, two_datasets):
    grant(reader, "viewer", MINE)
    rows = narrow(Article.objects.all(), reader, READ)
    assert {a.title for a in rows} == {"mine story"}


def test_no_grants_means_no_rows_rather_than_all_rows(reader, two_datasets):
    """The failure this module exists to prevent. An empty permitted set
    is not "no filter"; it is nothing."""
    assert narrow(Article.objects.all(), reader, READ).count() == 0


def test_an_application_wide_grant_sees_everything(reader, two_datasets):
    grant(reader, "viewer", "")
    assert narrow(Article.objects.all(), reader, READ).count() == 2


def test_a_privilege_the_role_lacks_narrows_to_nothing(reader, two_datasets):
    """A viewer holds the dataset for `read` and not for `write`, so a
    queryset narrowed on `write` is empty even though they hold a grant
    on it."""
    grant(reader, "viewer", MINE)
    assert narrow(Article.objects.all(), reader, READ).count() == 1
    assert narrow(Article.objects.all(), reader, WRITE).count() == 0


def test_scopes_are_per_privilege(reader, two_datasets):
    grant(reader, "reviewer", MINE)
    grant(reader, "designer", THEIRS)
    assert scopes_for(reader, READ) == {MINE, THEIRS, UNIVERSAL}
    assert scopes_for(reader, WRITE) == {MINE}
    assert scopes_for(reader, DESIGN) == {THEIRS, UNIVERSAL}
    assert scopes_for(reader, CREATE) == frozenset()


# --- the picker -------------------------------------------------------------


def test_the_dataset_picker_offers_only_what_can_be_chosen(reader, two_datasets):
    """A selector listing a dataset somebody cannot choose is an
    invitation to a 403 — and the guard would then refuse them for
    picking what they were shown."""
    grant(reader, "viewer", MINE)
    assert [d.slug for d in datasets_for(reader, READ)] == [MINE]


def test_the_picker_is_empty_without_grants(reader, two_datasets):
    assert datasets_for(reader, READ).count() == 0


def test_application_wide_access_does_not_enumerate(reader, two_datasets):
    """`ALL_SCOPES` rather than a list of every slug: a query for this
    person must not become `WHERE slug IN (...)`, which would grow with
    the corpus and would silently exclude a dataset created after the
    grant."""
    grant(reader, "viewer", "")
    assert scopes_for(reader, READ) is ALL_SCOPES
    assert datasets_for(reader, READ).count() == 2


# --- through the views ------------------------------------------------------


def test_the_grid_shows_only_held_datasets(client, reader, two_datasets):
    grant(reader, "viewer", MINE)
    client.force_login(reader)
    body = client.get("/explorer/articles/").content.decode()
    assert "mine story" in body
    assert "theirs story" not in body


def test_naming_an_unheld_dataset_is_refused_not_emptied(client, reader, two_datasets):
    """Being shown an empty page reads as "this dataset has no stories".
    Being refused reads as "this is not yours", which is true."""
    grant(reader, "viewer", MINE)
    client.force_login(reader)
    assert client.get(f"/explorer/articles/?dataset={THEIRS}").status_code == 403


def test_an_unheld_story_is_not_found_rather_than_forbidden(
    client, reader, two_datasets
):
    """404, not 403: a 403 would confirm the article exists, which is
    itself a disclosure."""
    grant(reader, "viewer", MINE)
    client.force_login(reader)
    theirs = two_datasets[THEIRS]
    assert client.get(f"/explorer/articles/{theirs.id}/").status_code == 404
    assert client.get(f"/explorer/articles/{two_datasets[MINE].id}/").status_code == 200


def test_an_export_carries_only_held_rows(client, reader, two_datasets):
    """The worst version of a missing filter, because it leaves the
    building."""
    grant(reader, "viewer", MINE)
    client.force_login(reader)
    body = client.post("/review/export/", {"columns": ["title"]}).content.decode()
    assert "mine story" in body
    assert "theirs story" not in body
