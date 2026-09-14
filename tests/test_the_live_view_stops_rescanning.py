"""The processing page took forty seconds, and it polls itself every fifteen.

Two shapes, both measured against production on 2026-09-14.

THE REWORK PANEL: 40,745ms. It scoped rework rows to the caller's datasets
with an OR of two `IN (subquery)` filters, which gives the planner nothing
to drive from -- it materialised a sequential scan of all 261,370
candidate_links and re-scanned it once per rework row, 35 times. A
correlated EXISTS is a primary-key lookup per rework row instead, of which
there are hundreds rather than hundreds of thousands. Same rows, 31ms.

STAGE COUNTS: five separate `count()` calls, so five round trips to answer
one question. Grouped, each table's index is read once: 252ms -> 53ms for
links, 1,206ms -> 504ms for articles, and three round trips become one.

Both are asserted on the SQL rather than on a stopwatch. A timing test on a
seeded sqlite-sized table would pass on the slow version too -- the shapes
only diverge at production's row counts, and what went wrong is visible in
the query itself.
"""

from __future__ import annotations

import pytest
from django.db.models import Exists, OuterRef, Q

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _rework_sql(dataset_ids):
    """The rework panel's own filter, as SQL."""
    from explorer.models import Article, CandidateLink, PipelineRework

    return str(
        PipelineRework.objects.filter(
            Q(record_type="article")
            & Q(
                Exists(
                    Article.objects.filter(
                        id=OuterRef("record_id"), dataset_id__in=dataset_ids
                    )
                )
            )
            | Q(record_type="candidate_link")
            & Q(
                Exists(
                    CandidateLink.objects.filter(
                        id=OuterRef("record_id"), dataset_id__in=dataset_ids
                    )
                )
            )
        ).query
    )


class TestTheReworkPanelDrivesFromTheSmallTable:
    def test_it_asks_with_exists(self):
        sql = _rework_sql(["d1"])
        assert "EXISTS" in sql

    def test_it_does_not_ask_with_in_subquery(self):
        """THE REGRESSION TO CATCH. `record_id__in=Article.objects...` reads
        naturally and is what was there; it is also what made the planner
        scan 261,370 rows thirty-five times."""
        sql = _rework_sql(["d1"])
        assert "IN (SELECT" not in sql, "an IN-subquery is back in the rework filter"

    def test_the_module_uses_it(self):
        """Asserted against the source, because the query above is a
        reconstruction: what ships has to have the same shape."""
        from pathlib import Path

        source = Path("explorer/processing.py").read_text()
        rework = source[source.index("def rework(") : source.index("def by_domain(")]
        assert "Exists(" in rework
        assert "record_id__in=" not in rework, "the IN-subquery filter is back"


class TestTheStageCountsAskOncePerTable:
    def test_it_makes_two_queries_not_five(
        self, crawler_schema, django_assert_num_queries
    ):
        """One per table. Five `count()` calls answered one question in five
        round trips, on a page that re-asks every fifteen seconds."""
        from explorer import processing

        with django_assert_num_queries(2, using="crawler"):
            processing.stage_counts()

    def test_every_status_still_reported(self, crawler_schema):
        """Grouping must not drop a status that has no rows: a stage with
        nothing in it reads as zero, not as missing."""
        from explorer import processing

        counts = processing.stage_counts()
        assert set(counts) == {
            "discovered",
            "article",
            "extracted",
            "labeled",
            "enriched",
        }
        assert all(isinstance(v, int) for v in counts.values())

    def test_the_counts_are_right(self, crawler_schema):
        """The grouping is only worth having if it still counts."""
        from explorer.models import Article, CandidateLink, Dataset

        Dataset.objects.create(id="d1", slug="mo", label="Missouri")
        for i, status in enumerate(["discovered", "article", "article", "extracted"]):
            CandidateLink.objects.create(
                id=f"l{i}",
                url=f"https://a.example/{i}",
                status=status,
                dataset_id="d1",
            )
        for i, status in enumerate(["labeled", "enriched", "enriched"]):
            Article.objects.create(id=f"a{i}", status=status, dataset_id="d1")

        from explorer import processing

        counts = processing.stage_counts()
        assert counts["discovered"] == 1
        assert counts["article"] == 2
        assert counts["extracted"] == 1
        assert counts["labeled"] == 1
        assert counts["enriched"] == 2
