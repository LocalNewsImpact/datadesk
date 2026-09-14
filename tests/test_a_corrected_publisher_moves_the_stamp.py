"""A publisher correction did not move the cache stamp.

`corpus_version()` is what lets every counted answer be kept for a week:
the key carries it, so a stale entry is not possible, only an unused one.
It covered four things -- the newest article, the count of dataset
memberships, the newest enrichment, and geography a person put in.

None of them moves when a PUBLISHER RECORD is corrected. On 2026-09-14 a
source's county went from "Nexstar Media Inc" to Jackson and another from
"Callaway County" to Callaway, in the crawler, and the stamp did not
change: no article was created, no membership moved, nothing was
re-enriched and nobody placed a story. So the keys held and the visual
builder went on offering "Nexstar County" as a place to pick newsrooms
from -- for what would have been the full seven days.

The fifth part is a fingerprint of the publisher fields the newsroom tree
draws from. `sources` has no `updated_at` to take a max over.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _version():
    from django.core.cache import cache

    from visuals.corpus import corpus_version

    # The stamp is itself cached for five minutes; these tests are about
    # what it is derived FROM.
    cache.delete("corpus.version")
    return corpus_version()


def _a_source(crawler_schema, source_id, county):
    from explorer.models import Source

    Source.objects.using("crawler").update_or_create(
        id=source_id,
        defaults={
            "host": f"{source_id}.example.com",
            "host_norm": f"{source_id}.example.com",
            "canonical_name": f"The {source_id}",
            "county": county,
        },
    )


class TestAPublisherEditMovesIt:
    def test_correcting_a_county_changes_the_stamp(self, crawler_schema):
        """THE BUG. This is the edit that was made in production and did
        not invalidate a thing."""
        _a_source(crawler_schema, "s-nex", "Nexstar Media Inc")
        before = _version()
        _a_source(crawler_schema, "s-nex", "Jackson")
        assert _version() != before

    def test_collapsing_two_spellings_changes_it(self, crawler_schema):
        _a_source(crawler_schema, "s-cal", "Callaway County")
        before = _version()
        _a_source(crawler_schema, "s-cal", "Callaway")
        assert _version() != before

    def test_renaming_a_publisher_changes_it(self, crawler_schema):
        """The tree shows the name too, so a rename is as visible as a
        county."""
        from explorer.models import Source

        _a_source(crawler_schema, "s-name", "Boone")
        before = _version()
        Source.objects.using("crawler").filter(id="s-name").update(
            canonical_name="Renamed"
        )
        assert _version() != before

    def test_a_new_publisher_changes_it(self, crawler_schema):
        _a_source(crawler_schema, "s-one", "Boone")
        before = _version()
        _a_source(crawler_schema, "s-two", "Cole")
        assert _version() != before


class TestItIsStableOtherwise:
    def test_asking_twice_gives_the_same_answer(self, crawler_schema):
        """A stamp that moved on its own would throw away every cached
        count on every request, which is the cost the week-long TTL
        exists to avoid."""
        _a_source(crawler_schema, "s-still", "Boone")
        assert _version() == _version()

    def test_a_column_the_tree_never_shows_does_not_move_it(self, crawler_schema):
        """DELIBERATELY NARROW. Fingerprinting every column would move the
        stamp on a crawler housekeeping field -- `bot_sensitivity_updated_at`
        is written by the fetcher -- and discard every cached count in the
        console for a change no reader can see."""
        from explorer.models import Source

        _a_source(crawler_schema, "s-quiet", "Boone")
        before = _version()
        Source.objects.using("crawler").filter(id="s-quiet").update(
            rss_consecutive_failures=7
        )
        assert _version() == before


class TestItIsNotWorthA500:
    def test_a_failure_degrades_to_recompute(self, monkeypatch):
        """A stamp that cannot be derived should mean "recompute", never
        an error on every cached page."""
        from visuals import corpus

        def boom(*_args, **_kwargs):
            raise RuntimeError("no database")

        monkeypatch.setattr(corpus, "connections", None, raising=False)
        with monkeypatch.context() as m:
            m.setattr(
                "django.db.connections.__getitem__",
                boom,
                raising=False,
            )
            value = corpus._publisher_fingerprint()
        assert value
        assert isinstance(value, str)
