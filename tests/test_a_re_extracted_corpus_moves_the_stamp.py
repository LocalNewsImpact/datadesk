"""Re-extraction did not move the cache stamp, and reground could not.

`corpus_version()` is what lets every counted answer be kept for a week:
the key carries it, so a stale entry is not possible, only an unused one.
It covered five things -- the newest article, the count of dataset
memberships, the newest enrichment, geography a person put in, and a
fingerprint of the publisher records.

NONE OF THEM MOVES WHEN THE CORPUS IS RE-EXTRACTED. On 2026-09-16 every
enriched article -- 19,926 of them -- was re-extracted against the
statewide gazetteer and rematched. That rewrites `article_entities` and
the place matches drawn from it, and it creates no article, moves no
membership, re-enriches nothing and places no story. The keys would have
held and the map would have gone on drawing pre-re-extraction geography
for the full seven days.

AND A MAX OVER A TIMESTAMP CANNOT SEE A DELETE. `enrich reground`
removes the geoids an article's own text does not support and gives
nothing a new timestamp. A corpus that just had thousands of unsupported
places removed is, to every part above and to the sixth, identical to one
that did not. The seventh part is a count, because a count is what moves
when rows go away.
"""

from __future__ import annotations

from datetime import UTC

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _version():
    from django.core.cache import cache

    from visuals.corpus import corpus_version

    # The stamp is itself cached for five minutes; these tests are about
    # what it is derived FROM.
    cache.delete("corpus.version")
    return corpus_version()


def _an_article(article_id, extracted_at=None):
    from explorer.models import Article

    Article.objects.using("crawler").update_or_create(
        id=article_id,
        defaults={
            "url": f"https://example.com/{article_id}",
            "status": "enriched",
            "wire_check_status": "checked",
            "entities_extracted_at": extracted_at,
        },
    )


def _a_geoid(article_id, geoid):
    from explorer.models import ArticleGeoid

    return ArticleGeoid.objects.using("crawler").create(
        article_id=article_id, geoid=geoid, geoid_level="place", source="model"
    )


class TestReExtractionMovesIt:
    def test_extracting_an_article_changes_the_stamp(self, crawler_schema):
        """THE BUG. A full re-extraction that moved nothing else."""
        from datetime import datetime

        _an_article("a-1", extracted_at=datetime(2026, 9, 16, 14, 0, tzinfo=UTC))
        before = _version()
        _an_article("a-1", extracted_at=datetime(2026, 9, 16, 18, 30, tzinfo=UTC))
        assert _version() != before

    def test_a_corpus_never_extracted_still_has_a_stamp(self, crawler_schema):
        """All-null must not raise; it reads as 'none' like the others."""
        _an_article("a-2", extracted_at=None)
        assert _version()


class TestRegroundMovesIt:
    def test_removing_a_geoid_changes_the_stamp(self, crawler_schema):
        """What reground does, and what no timestamp can see."""
        _an_article("a-3")
        row = _a_geoid("a-3", "2900100")
        before = _version()
        row.delete()
        assert _version() != before

    def test_adding_a_geoid_changes_it_too(self, crawler_schema):
        _an_article("a-4")
        before = _version()
        _a_geoid("a-4", "2901234")
        assert _version() != before

    def test_an_empty_corpus_counts_zero_rather_than_failing(self, crawler_schema):
        assert _version()
