"""A supplied URL the rules doubt is reviewed, and is not a pipeline verdict.

Ingested URLs -- ones a person supplied as part of a chosen set -- skip
verification so no rule or model removes one. The crawler's `check-ingested`
now runs the URL rules and storysniffer over them anyway and writes one row per
link, `verdict_kind: "ingested"`, with `flagged` set when something doubts it.
The link is still fetched. On WSU (2026-09-21) that is 27 flagged of 2,681.

Only the flagged ones are asked about, in a stratum of their own and in full.
And like a first score, an ingested row is kept out of the strata that ask
whether the pipeline was right: there is no pipeline verdict, a person chose
the URL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from explorer.models import CandidateLink, Source, UrlVerification
from review import discovery

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

A_RESCORE = {"decided_by": "backfill", "rescored_by": "default"}


def _row(crawler_schema, link_id, meta, margin, sniffer=True, status="discovered"):
    source = Source.objects.using("crawler").filter(id="s-ing").first()
    if source is None:
        source = Source.objects.using("crawler").create(
            id="s-ing", host="example.com", canonical_name="The Example"
        )
    link = CandidateLink.objects.using("crawler").create(
        id=link_id,
        url=f"https://example.com/{link_id}",
        source=source,
        status=status,
        discovered_at=datetime(2026, 3, 10, 12, tzinfo=UTC),
    )
    return UrlVerification.objects.using("crawler").create(
        id=f"v-{link_id}",
        candidate_link=link,
        url=link.url,
        storysniffer_result=sniffer,
        verification_confidence=margin,
        new_status=status,
        meta=meta,
    )


FLAGGED = {
    "decided_by": "default",
    "verdict_kind": "ingested",
    "flagged": True,
    "flag_reason": "storysniffer_no",
}
CLEAR = {
    "decided_by": "sniffer",
    "verdict_kind": "ingested",
    "flagged": False,
    "flag_reason": None,
}
A_RESCORE = {"decided_by": "backfill", "rescored_by": "default"}


def _in(stratum):
    return [
        v.id
        for v in UrlVerification.objects.using("crawler").filter(
            discovery.predicate(stratum)
        )
    ]


class TestTheStratumCollectsTheDoubted:
    def test_a_flagged_supplied_url_is_in_it(self, crawler_schema):
        _row(crawler_schema, "f1", FLAGGED, 900.0, sniffer=False, status="article")
        assert _in(discovery.INGESTED) == ["v-f1"]

    def test_a_supplied_url_nothing_doubts_is_not(self, crawler_schema):
        """Checked and fine: nothing to ask."""
        _row(crawler_schema, "c1", CLEAR, 900.0, status="article")
        assert _in(discovery.INGESTED) == []

    def test_a_pipeline_row_is_not(self, crawler_schema):
        _row(
            crawler_schema, "r1", A_RESCORE, 900.0, sniffer=False, status="not_article"
        )
        assert _in(discovery.INGESTED) == []

    def test_a_row_with_no_meta_is_not(self, crawler_schema):
        _row(crawler_schema, "n1", None, 900.0)
        assert _in(discovery.INGESTED) == []

    def test_it_does_not_care_about_the_margin(self, crawler_schema):
        _row(crawler_schema, "lo", FLAGGED, -31.4, sniffer=False, status="article")
        _row(crawler_schema, "hi", FLAGGED, 2708.0, sniffer=False, status="article")
        assert sorted(_in(discovery.INGESTED)) == ["v-hi", "v-lo"]


class TestItIsReviewedInFull:
    def test_nothing_caps_it(self):
        assert discovery.STRATUM_SIZE[discovery.INGESTED] is None

    def test_every_row_had_probability_one(self):
        assert discovery.inclusion_probability(discovery.INGESTED, 27) == 1.0


class TestASuppliedUrlIsNotAPipelineVerdict:
    def test_it_is_kept_out_of_the_doubtful_band(self, crawler_schema):
        _row(crawler_schema, "d1", FLAGGED, 10.0, sniffer=False, status="article")
        assert _in(discovery.DOUBTFUL) == []

    def test_an_unflagged_one_is_kept_out_too(self, crawler_schema):
        _row(crawler_schema, "d2", CLEAR, 10.0, status="article")
        assert _in(discovery.DOUBTFUL) == []

    def test_nothing_overruled_it(self, crawler_schema):
        """storysniffer false at a decisive margin reads as an override --
        but nothing ruled against a supplied URL."""
        _row(crawler_schema, "o1", FLAGGED, 900.0, sniffer=False, status="article")
        assert _in(discovery.OVERRULED) == []

    def test_real_doubtful_and_override_rows_are_still_there(self, crawler_schema):
        _row(crawler_schema, "d3", A_RESCORE, 10.0, status="not_article")
        _row(
            crawler_schema, "o2", A_RESCORE, 900.0, sniffer=False, status="not_article"
        )
        assert _in(discovery.DOUBTFUL) == ["v-d3"]
        assert "v-o2" in _in(discovery.OVERRULED)


def test_the_page_iterates_it():
    keys = [key for key, _label, _description in discovery.STRATA]
    assert discovery.INGESTED in keys
    assert keys.index(discovery.INGESTED) < keys.index(discovery.SAMPLE)
