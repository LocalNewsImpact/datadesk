"""The filter dropdowns are read from the corpus, and that reading was
the slowest thing left on the page.

Measured on production 2026-09-08, once the queue's own queries were
down to 0.44s for a whole page: the three vocabulary queries took 5.2s
cold and 1.3s warm, and none of them looked at the dataset or the window
the reviewer was on. `_wire_services` alone fetched 47,423 JSON values
across the wire to fill one dropdown.

It was also wrong. `articles.wire` holds an array of names on 18,117
flagged rows and an object carrying `provider` on 24,521, and only the
arrays were read -- nine syndications covering 394 articles named a
service that could not be picked in the filter that exists to work them.
"""

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review import queue as q

MIZZOU = "d-mizzou"


@pytest.fixture
def reviewer(db):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    return user


@pytest.fixture
def wire_rows(crawler_schema):
    """Both shapes production holds, in two datasets."""
    mizzou = Dataset.objects.create(id=MIZZOU, slug="mo", label="Missouri")
    other = Dataset.objects.create(id="d-wa", slug="wa", label="Washington")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=mizzou.id, source_id=source.id)
    DatasetSource.objects.create(id="ds2", dataset_id=other.id, source_id=source.id)

    def make(pk, dataset_id, wire):
        link = CandidateLink.objects.create(
            id=f"cl-{pk}",
            url=f"https://a.example/{pk}",
            source=source,
            dataset_id=dataset_id,
        )
        return Article.objects.create(
            id=pk,
            candidate_link=link,
            dataset_id=dataset_id,
            status="wire",
            wire_check_status="complete",
            content="A syndicated story, long enough to be doubted." * 8,
            wire=wire,
        )

    # An array shape, twice, so it outranks the singletons by volume.
    make("a1", MIZZOU, ["The Associated Press"])
    make("a2", MIZZOU, ["The Associated Press"])
    # The object shape: named by `provider`, and invisible until now.
    make("a3", MIZZOU, {"provider": "BrandPoint", "detection_method": "url"})
    # A bare string, which this column has also held.
    make("a4", MIZZOU, "Reuters")
    # Shapes that name nothing must not raise.
    make("a5", MIZZOU, {})
    make("a6", MIZZOU, None)
    # Another dataset, to prove the scoping.
    make("a7", "d-wa", ["Cascade Public Media"])
    yield
    cache.clear()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_object_shaped_row_names_its_service(reviewer, wire_rows):
    """24,521 flagged rows carry `{"provider": ...}` and were skipped, so
    the services on them could not be chosen in the wire filter."""
    assert "BrandPoint" in q._wire_services(reviewer)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_shape_the_column_holds_is_read(reviewer, wire_rows):
    assert set(q._wire_services(reviewer)) == {
        "The Associated Press",
        "BrandPoint",
        "Reuters",
        "Cascade Public Media",
    }


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_order_is_volume(reviewer, wire_rows):
    """Ordered by how many articles each accounts for -- the head of the
    list is where the corpus was spent. Counted from distinct values
    weighted by how many rows hold each, so the weighting is the part
    that can break."""
    assert q._wire_services(reviewer)[0] == "The Associated Press"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_value_that_names_nothing_costs_one_option(reviewer, wire_rows):
    """`{}` and NULL are in production. A dropdown should lose an option
    to an unreadable value, never the page."""
    assert q.service_names({}) == ()
    assert q.service_names(None) == ()
    assert q.service_names(42) == ()
    assert q.service_names({"detection_method": "url"}) == ()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_dropdown_offers_what_the_page_is_showing(reviewer, wire_rows):
    """Every value offered should return rows. Scoped to the dataset the
    reviewer is on, a service from another dataset is not offered."""
    scoped = q._wire_services(reviewer, {"dataset": "mo"})
    assert "The Associated Press" in scoped
    assert "Cascade Public Media" not in scoped


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_whole_vocabulary_follows_the_page(reviewer, wire_rows):
    cache.clear()
    scoped = q.vocab(reviewer, {"dataset": "mo"})
    assert "Cascade Public Media" not in scoped["services"]
    # The dataset picker is what changes the scope, so it is never scoped.
    assert {d["slug"] for d in scoped["datasets"]} == {"mo", "wa"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_second_read_is_held(reviewer, wire_rows):
    """The corpus does not change between two page loads, and reading it
    cost more than the page's own queries."""
    cache.clear()
    first = q.vocab(reviewer, {"dataset": "mo"})
    Article.objects.filter(id="a3").delete()
    assert q.vocab(reviewer, {"dataset": "mo"}) == first


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_different_scope_is_read_again(reviewer, wire_rows):
    """Held per scope, or the March page would be shown January's
    vocabulary."""
    cache.clear()
    missouri = q.vocab(reviewer, {"dataset": "mo"})["services"]
    washington = q.vocab(reviewer, {"dataset": "wa"})["services"]
    assert "Cascade Public Media" not in missouri
    assert "Cascade Public Media" in washington


@pytest.mark.django_db(databases=["default", "crawler"])
def test_two_readers_do_not_share_one_vocabulary(reviewer, wire_rows):
    """Grants differ. A vocabulary built for one reader must not be
    served to another, whose datasets may be a different set."""
    other = User.objects.create_user("other", email="other@localnewsimpact.org")
    Grant.objects.create(user=other, app=DATADESK, scope="", role="reviewer")
    assert q._vocab_key(reviewer, {}) != q._vocab_key(other, {})


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_unreachable_crawler_is_not_held(reviewer):
    """`None` means "ask again". Holding it would keep the page saying
    "not connected" for five minutes after the database came back."""
    cache.clear()
    # No crawler_schema fixture: the tables are absent.
    assert q.vocab(reviewer, {}) is None
    assert cache.get(q._vocab_key(reviewer, {})) is None
