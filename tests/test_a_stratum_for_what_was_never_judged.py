"""Some links have no verdict to second-guess.

Every stratum in the discovery queue asked the same question: was the
pipeline right about this. That question has no answer for a link at
`candidate_links.status = 'discovered'` -- it was found, and neither the
URL rules nor the model has ruled on it since. The crawler now scores
those on request and marks each row a `prescore` rather than a backfill,
with `verdict_kind: "never_judged"`.

WITHOUT A STRATUM THEY ARE INVISIBLE. 410 were written against production
on 2026-09-14. 30 fall inside the doubtful band on margin alone; the
other 380 fall outside every doubt-ranked band, so the only thing that
could reach them is the uniform sample -- 400 rows drawn across a cohort
of hundreds of thousands, which for 410 rows is a handful.

And the 30 that DO fall in the doubtful band were being asked about
twice, once with a question that has no answer for them. A first score is
not a second opinion, so it is kept out of the strata that are.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from explorer.models import CandidateLink, Source, UrlVerification
from review import discovery

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

FIRST_SCORE = {"decided_by": "prescore", "verdict_kind": "never_judged"}
A_RESCORE = {"decided_by": "backfill", "rescored_by": "default"}


def _row(crawler_schema, link_id, meta, margin, sniffer=True, status="discovered"):
    source = Source.objects.using("crawler").filter(id="s-nj").first()
    if source is None:
        source = Source.objects.using("crawler").create(
            id="s-nj", host="example.com", canonical_name="The Example"
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


def _in(stratum):
    return [
        v.id
        for v in UrlVerification.objects.using("crawler").filter(
            discovery.predicate(stratum)
        )
    ]


class TestTheStratumCollectsThem:
    def test_a_first_score_is_in_it(self, crawler_schema):
        _row(crawler_schema, "nj1", FIRST_SCORE, 900.0)
        assert _in(discovery.NEVER_JUDGED) == ["v-nj1"]

    def test_a_rescore_is_not(self, crawler_schema):
        """It has a verdict. The question for it is whether that verdict
        was right, which is what the other strata ask."""
        _row(crawler_schema, "rs1", A_RESCORE, 900.0, status="not_article")
        assert _in(discovery.NEVER_JUDGED) == []

    def test_a_row_with_no_meta_is_not(self, crawler_schema):
        _row(crawler_schema, "none1", None, 900.0)
        assert _in(discovery.NEVER_JUDGED) == []

    def test_it_does_not_care_about_the_margin(self, crawler_schema):
        """380 of the 410 sit outside every band. The stratum is defined
        by there being no verdict, not by where the score landed."""
        _row(crawler_schema, "low", FIRST_SCORE, -31.4)
        _row(crawler_schema, "high", FIRST_SCORE, 2708.0)
        assert sorted(_in(discovery.NEVER_JUDGED)) == ["v-high", "v-low"]


class TestItIsReviewedInFull:
    def test_nothing_caps_it(self, crawler_schema):
        """Each row is a link waiting on a decision, not a sample of
        anything: a cap would leave the rest waiting."""
        assert discovery.STRATUM_SIZE[discovery.NEVER_JUDGED] is None

    def test_every_row_had_probability_one(self, crawler_schema):
        """A rate measured over rows that were not equally likely to be
        drawn is not a rate. Full review is 1.0."""
        assert discovery.inclusion_probability(discovery.NEVER_JUDGED, 410) == 1.0

    def test_the_draw_returns_all_of_them(self, crawler_schema):
        for i in range(5):
            _row(crawler_schema, f"all{i}", FIRST_SCORE, 900.0 + i)
        base = UrlVerification.objects.using("crawler").filter(
            discovery.predicate(discovery.NEVER_JUDGED)
        )
        assert len(list(discovery.drawn(base, discovery.NEVER_JUDGED))) == 5


class TestAFirstScoreIsNotASecondOpinion:
    def test_it_is_kept_out_of_the_doubtful_band(self, crawler_schema):
        """30 of the 410 land inside it on margin alone, and would have
        been asked about twice -- once with a question that has no answer
        for them."""
        _row(crawler_schema, "dbt", FIRST_SCORE, 10.0)
        assert _in(discovery.DOUBTFUL) == []

    def test_a_real_doubtful_row_is_still_there(self, crawler_schema):
        """The exclusion must not empty the band it is narrowing."""
        _row(crawler_schema, "dbt2", A_RESCORE, 10.0, status="not_article")
        assert _in(discovery.DOUBTFUL) == ["v-dbt2"]

    def test_nothing_overruled_a_link_nothing_ruled_on(self, crawler_schema):
        """Every first score written so far has `storysniffer_result`
        true and would miss the overruled predicate anyway, which is why
        this is stated rather than left to chance: the next one that does
        not would be labelled an override of a verdict never reached."""
        _row(crawler_schema, "ovr", FIRST_SCORE, 900.0, sniffer=False)
        assert _in(discovery.OVERRULED) == []

    def test_a_real_override_is_still_there(self, crawler_schema):
        _row(crawler_schema, "ovr2", A_RESCORE, 900.0, sniffer=False)
        assert _in(discovery.OVERRULED) == ["v-ovr2"]


class TestTheQueueRendersIt:
    def test_it_is_in_the_strata_the_page_iterates(self):
        keys = [key for key, _label, _description in discovery.STRATA]
        assert discovery.NEVER_JUDGED in keys

    def test_it_has_a_label_and_a_description(self):
        by_key = {key: (label, text) for key, label, text in discovery.STRATA}
        label, text = by_key[discovery.NEVER_JUDGED]
        assert label
        assert text

    def test_the_label_does_not_claim_a_disagreement(self):
        """The name is the whole point. "Model disagrees" over rows the
        model never spoke about is the mistake this queue already made
        once."""
        label = discovery.STRATUM_LABELS[discovery.NEVER_JUDGED].lower()
        assert "disagree" not in label
        assert "wrong" not in label


class TestThePageItselfRenders:
    """THE PREDICATE COMPILING IS NOT THE PAGE WORKING.

    `Coalesce` refuses to guess an output field across a TextField and
    the CharField a bare `Value("")` resolves to, and it raises only when
    the expression is compiled -- so every direct-predicate test above
    passed while the queue returned a 500. The page has to be asked.
    """

    @pytest.fixture
    def reviewer(self, django_user_model):
        from accounts.models import DATADESK, Grant

        user = django_user_model.objects.create_user(
            "njr", email="njr@localnewsimpact.org"
        )
        Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
        return user

    def test_the_queue_opens_on_this_stratum(self, client, reviewer, crawler_schema):
        from django.urls import reverse

        _row(crawler_schema, "page1", FIRST_SCORE, 900.0)
        client.force_login(reviewer)
        response = client.get(
            reverse("review:discovery"), {"stratum": discovery.NEVER_JUDGED}
        )
        assert response.status_code == 200

    def test_every_stratum_opens(self, client, reviewer, crawler_schema):
        """The exclusion was added to two other strata as well, and an
        expression that will not compile takes down whichever page reads
        it."""
        from django.urls import reverse

        _row(crawler_schema, "page2", FIRST_SCORE, 10.0)
        _row(crawler_schema, "page3", A_RESCORE, 10.0, status="not_article")
        client.force_login(reviewer)
        for key, _label, _text in discovery.STRATA:
            response = client.get(reverse("review:discovery"), {"stratum": key})
            assert response.status_code == 200, key

    def test_the_row_is_actually_shown(self, client, reviewer, crawler_schema):
        """A 200 over an empty stratum would pass the tests above."""
        from django.urls import reverse

        _row(crawler_schema, "shown", FIRST_SCORE, 900.0)
        client.force_login(reviewer)
        body = client.get(
            reverse("review:discovery"), {"stratum": discovery.NEVER_JUDGED}
        ).content.decode()
        assert "example.com/shown" in body
