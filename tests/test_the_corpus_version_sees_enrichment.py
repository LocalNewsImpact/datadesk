"""Re-reading a story changes the corpus, so it has to change the stamp.

`corpus_version()` is in every cache key in `visuals.corpus`, and those
answers are kept for a week on the strength of one promise in its
docstring: "an entry cannot go stale, because data that has moved lands
under a different key."

It tracked two things -- the newest article and the number of dataset
memberships -- and neither moves when a story is re-enriched. But
enrichment is where scope, the CIN label and the geography come from, and
a story map draws almost entirely from it.

Seen on 2026-09-11: 174 articles re-enriched, the map's own query
rebuilt to shade 23 counties instead of 9, and the preview still drawing
the old numbers because the key had not moved. The promise held only for
data the stamp could see.
"""

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _stamp():
    from django.core.cache import cache

    from visuals.corpus import corpus_version

    # The stamp caches itself for five minutes; each assertion here wants
    # the value as it is now, not as it was when something else asked.
    cache.delete("corpus.version")
    return corpus_version()


def test_enrichment_is_part_of_the_stamp(crawler_schema):
    """Three parts, and the third is the one that was missing."""
    from explorer.models import ArticleEnrichment

    stamp = _stamp()
    assert (
        stamp.count(":") >= 2
    ), "the stamp still has two parts; a re-enrichment cannot move it"
    # The last part is the newest enrichment, or `none` where there is
    # not one yet -- a fresh database rather than a bug.
    assert stamp.rsplit(":", 1)[-1] in ("none",) or ArticleEnrichment.objects.exists()


def test_a_later_enrichment_moves_it(crawler_schema):
    """The property the cache depends on: new data, new key."""
    from datetime import timedelta

    from django.utils import timezone

    from explorer.models import Article, ArticleEnrichment

    before = _stamp()

    article = Article.objects.using("crawler").create(
        id="corpus-version-probe", url="https://example.test/a"
    )
    ArticleEnrichment.objects.using("crawler").create(
        article=article, enriched_at=timezone.now() + timedelta(hours=1)
    )

    assert _stamp() != before, (
        "a story enriched after the last one did not change the stamp, so "
        "every answer computed before it stays cached"
    )


def test_the_key_moves_with_the_stamp(crawler_schema):
    """`_cache_key` embeds the stamp, which is the whole mechanism. A
    stamp that moves and a key that does not would be no better."""
    from visuals.corpus import _cache_key, corpus_version

    key = _cache_key("question", {"shape": "story_map"}, [])
    assert corpus_version() in key
