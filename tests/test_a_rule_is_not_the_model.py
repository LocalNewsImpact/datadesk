"""The "Model disagrees" stratum held no disagreements with the model.

`storysniffer_result` is not storysniffer's answer. FOUR mechanisms can
write `False` into it and only one of them is the model: the wire-service
URL filter, the URL pattern rules and the asset-extension check all
return before `sniffer.guess()` is ever called, and set the column on
their way out. So a stratum defined as "a decisive positive margin that
was rejected anyway" collects rule hits, not overrides of the model.

MEASURED AGAINST PRODUCTION 2026-09-14: 69,514 rows sit above the
decisive margin with the column false, and ZERO of them were decided by
storysniffer -- 51,815 wire, the rest URL patterns (`feed`, `video`,
`image_placeholder`, `opinion`, `asset_extension`, `gallery`). A reviewer
working that queue by hand got 50 of 50 wire, feeds, video and photo
galleries, every rejection correct, and was being asked to second-guess a
model that had not spoken.

The mechanism was already on the row the whole time. Reading it is the
fix.

TWO KEYS, because two paths write it. The live verification path puts
the mechanism in `meta.decided_by`. The backfill puts the literal
"backfill" there -- it is recording that the original decision's
mechanism was never captured -- and puts the rescore's mechanism in
`meta.rescored_by`. Every row in production today is a backfill, so
reading `decided_by` alone matches "backfill" on all 245,473 of them and
the stratum empties for the wrong reason.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from explorer.models import CandidateLink, Source, UrlVerification
from review import discovery

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _row(crawler_schema, link_id, meta, margin=500.0, sniffer=False):
    source = Source.objects.using("crawler").filter(id="s-mech").first()
    if source is None:
        source = Source.objects.using("crawler").create(
            id="s-mech", host="example.com", canonical_name="The Example"
        )
    link = CandidateLink.objects.using("crawler").create(
        id=link_id,
        url=f"https://example.com/{link_id}",
        source=source,
        status="not_article",
        discovered_at=datetime(2026, 3, 10, 12, tzinfo=UTC),
    )
    return UrlVerification.objects.using("crawler").create(
        id=f"v-{link_id}",
        candidate_link=link,
        url=link.url,
        storysniffer_result=sniffer,
        verification_confidence=margin,
        new_status="not_article",
        meta=meta,
    )


def _overruled():
    return UrlVerification.objects.using("crawler").filter(
        discovery.predicate(discovery.OVERRULED)
    )


class TestOnlyTheModelCanDisagreeWithItself:
    def test_a_wire_hit_is_not_a_disagreement(self, crawler_schema):
        """51,815 of the stratum. The wire filter matched the URL and
        returned; storysniffer was never asked."""
        _row(crawler_schema, "wire1", {"decided_by": "backfill", "rescored_by": "wire"})
        assert not _overruled().exists()

    @pytest.mark.parametrize(
        "pattern",
        ["feed", "video", "image_placeholder", "opinion", "asset_extension"],
    )
    def test_a_url_pattern_is_not_a_disagreement(self, crawler_schema, pattern):
        """The five largest pattern types in the stratum."""
        _row(
            crawler_schema,
            f"p-{pattern}",
            {"decided_by": "backfill", "rescored_by": f"pattern:{pattern}"},
        )
        assert not _overruled().exists()

    def test_the_model_saying_no_is_kept(self, crawler_schema):
        """`default` is what the crawler records when nothing filtered and
        `guess()` returned false -- the model's own rejection, which is
        the only thing a decisive positive margin can contradict."""
        _row(
            crawler_schema,
            "model1",
            {"decided_by": "backfill", "rescored_by": "default"},
        )
        assert _overruled().exists()

    def test_the_stratum_is_not_simply_emptied(self, crawler_schema):
        """A predicate that matched nothing would also pass every test
        above. One row of each, and only the model's survives."""
        _row(
            crawler_schema,
            "mix-wire",
            {"decided_by": "backfill", "rescored_by": "wire"},
        )
        _row(
            crawler_schema,
            "mix-model",
            {"decided_by": "backfill", "rescored_by": "default"},
        )
        assert [v.id for v in _overruled()] == ["v-mix-model"]


class TestBothPathsAreRead:
    def test_the_live_path_writes_decided_by(self, crawler_schema):
        """No `rescored_by` at all: a row the crawler wrote as it
        verified. The mechanism is in `decided_by`."""
        _row(crawler_schema, "live1", {"decided_by": "default"})
        assert _overruled().exists()

    def test_the_live_paths_rule_hit_is_still_excluded(self, crawler_schema):
        _row(crawler_schema, "live2", {"decided_by": "wire"})
        assert not _overruled().exists()

    def test_the_backfill_literal_is_not_mistaken_for_a_mechanism(self, crawler_schema):
        """`decided_by` is the string "backfill" on every production row.
        Reading that key first would compare "backfill" against the model
        names and drop the whole table -- the right answer for the wrong
        reason, and wrong the moment discovery runs again."""
        _row(
            crawler_schema,
            "bf1",
            {"decided_by": "backfill", "rescored_by": "default"},
        )
        assert _overruled().exists()

    def test_a_row_with_no_meta_is_not_counted_as_the_model(self, crawler_schema):
        """Unknown is not "the model said so". A row whose mechanism was
        never recorded cannot be presented as a disagreement with one."""
        _row(crawler_schema, "nometa", None)
        assert not _overruled().exists()


class TestTheQueryCompilesAgainstAJsonColumn:
    def test_it_reads_the_key_as_text(self, crawler_schema):
        """`url_verifications.meta` is Postgres `json`, NOT `jsonb`.
        Django's own key lookup (`meta__rescored_by="x"`) compiles to
        `->` compared against a jsonb literal, and Postgres has no such
        operator for `json`: it raises `operator does not exist`. Only
        `->>` works, which is what `KeyTextTransform` emits."""
        sql = str(_overruled().query)
        assert "->>" in sql
        assert "#>" not in sql, "a jsonb path operator is back"

    def test_the_predicate_needs_nothing_from_its_caller(self, crawler_schema):
        """THE SHAPE THAT MADE THIS SAFE. The first version annotated the
        mechanism onto the queryset and filtered on the annotation, which
        works only if every caller remembers -- and one test did not, so
        it raised `FieldError: Cannot resolve keyword`. In the review
        queue that is a 500, not a wrong count. Filtering a bare manager
        is the proof it carries its own left-hand side."""
        _row(crawler_schema, "bare", {"decided_by": "default"})
        assert (
            UrlVerification.objects.using("crawler")
            .filter(discovery.predicate(discovery.OVERRULED))
            .exists()
        )
