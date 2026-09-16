"""Two views on geography the pipeline DECIDED, as opposed to skipped.

`review/geography.py` asks a person for geography where the pipeline
found none. These ask whether it was right where it did decide.

WHAT THE GATE REFUSED. The grounding gate refuses a place the article
does not name in its own reporting — 3,854 claims measured across the
corpus. It is the right rule by every measure taken and it is still
applied without a person seeing it, and a wrong refusal is invisible:
the claim is gone and nothing records that it was made.

Nothing is stored for this. `article_places` holds what the MODEL said,
unfiltered by the gate; `article_geoids` holds what survived. The
difference is the refusal.

WHAT IT KEPT BUT BARELY. A point can pass and still be doubtful. Two
signals select — `point_support = 'institution'` (167 central places the
article never names, kept because an institution sits there) and the
code fallback that assigns the publication's own city (86). A third,
name collisions with better-known places, only FLAGS: it rests on a
hand-written list of what a reader knows, and 1,033 articles is too many
to queue on that basis.
"""

from __future__ import annotations

import pytest

from review import doubted_geography as dg

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


class TestWhatCountsAsRefused:
    def _sql(self):
        return str(dg.refused_claims().query)

    def test_it_compares_the_model_against_what_survived(self):
        """`article_places` is written unfiltered; `article_geoids` is
        what the gate let through. The difference is the refusal."""
        sql = self._sql()
        assert "article_places" in sql
        assert "article_geoids" in sql
        assert "NOT" in sql.upper()

    def test_only_story_level_claims_count(self):
        """A country or a state named in passing was never a candidate
        for `article_geoids`, so its absence there is not a refusal."""
        assert dg.STORY_LEVELS == ("place", "county")
        assert "geoid_level" in self._sql()

    def test_only_enriched_articles_count(self):
        """An article the pipeline never read has no claim to refuse."""
        assert "article_enrichment" in self._sql()

    def test_a_claim_with_no_geoid_is_not_a_refusal(self):
        """It was never codeable, so nothing refused it."""
        assert "geoid" in self._sql()


class TestWhatCountsAsDoubted:
    def _sql(self):
        return str(dg.doubted_points().query)

    def test_induction_alone_selects(self):
        """The article never named the place; an institution there did.
        Sound, and the reason the statewide gazetteer exists -- but a
        reviewer should see the points carried by it."""
        assert dg.BY_INSTITUTION == "institution"
        assert "point_support" in self._sql()

    def test_the_code_fallback_selects(self):
        """These assign the publication's own city, which is the bias
        the gate exists to catch."""
        assert dg.FALLBACK_METHODS == ("publication_city", "publication_place_assumed")
        assert "point_method" in self._sql()

    def test_a_named_place_does_not_select(self):
        """8,255 of 8,422 points are named outright. Queueing those
        would bury the 167 that are not."""
        assert dg.NAMED_IN_THE_STORY not in self._sql()

    def test_state_level_points_do_not_select(self):
        """A state rung is exempt from grounding entirely, because
        `name_for('29')` is "MO" and no story prints that."""
        assert "geoid_level" in self._sql()


class TestFlagsInformButDoNotSelect:
    """A flag says what to look for once a row is in front of somebody.
    A criterion decides for them, and the name list is my judgement
    about what a reader knows rather than a measurement."""

    def test_a_collision_is_flagged(self):
        assert dg.flags_for("Mexico") == ["the name is better known as somewhere else"]
        assert dg.flags_for("Cairo")
        assert dg.flags_for("Columbia")

    def test_a_distinctive_name_is_not(self):
        assert dg.flags_for("Silex") == []
        assert dg.flags_for("Sedalia") == []

    def test_induction_is_flagged_as_well_as_selected(self):
        assert dg.flags_for("Joplin", None, dg.BY_INSTITUTION) == [
            "the story never names this place; an institution here does"
        ]

    def test_flags_stack(self):
        flags = dg.flags_for("Columbia", "publication_city", dg.BY_INSTITUTION)
        assert len(flags) == 3

    def test_the_collision_list_never_reaches_the_query(self):
        """THE POINT. 1,033 articles carry a collision; selecting on it
        would bury the 246 with a measured reason."""
        assert "mexico" not in str(dg.doubted_points().query).lower()

    def test_the_list_is_visible_and_arguable(self):
        """Not buried in a query: it is a judgement and it should be
        possible to disagree with it in a review."""
        assert isinstance(dg.BETTER_KNOWN_ELSEWHERE, set)
        assert "mexico" in dg.BETTER_KNOWN_ELSEWHERE
        assert len(dg.BETTER_KNOWN_ELSEWHERE) > 40

    def test_normalisation_survives_punctuation(self):
        assert dg.flags_for("St. Louis") == []
        assert dg.flags_for("  MEXICO  ")


class TestScoping:
    def test_both_views_take_the_same_filters(self):
        """The queue's filters are the same everywhere or a reviewer
        cannot narrow one view the way they narrowed another."""
        import inspect

        refused = set(inspect.signature(dg.refused_claims).parameters)
        doubted = set(inspect.signature(dg.doubted_points).parameters)
        assert refused == doubted == {"dataset", "since", "until", "newsroom"}
