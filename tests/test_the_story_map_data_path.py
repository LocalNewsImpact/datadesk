"""Four findings of the 2026-09-30 review on the story map's data path and
on writes that took no row lock.

- The layered map reads its coverage through the story map's own cache
  key, so a layer edit does not re-run the points query, the manual
  merge and the roll-up.
- The shaded layer is read in id order and `meta.areas_truncated` says
  when the limit cut it.
- Import-apply and revert lock the rows they change, in one query,
  inside the transaction.
- "Already retracted" is decided under the withdrawn event's lock.
"""

# ruff: noqa: F401, F811 -- fixtures are imported from the modules that own them.

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext

from accounts.access import ALL_SCOPES
from tests.test_builder_steps import (
    author,
    corpus,
    dataset,
    newsroom,
)
from tests.test_review import article, editor
from visuals import corpus as corpus_module
from visuals import layermap
from visuals.corpus import answer_once, scope_key
from visuals.services import _scope_key

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


# --- the coverage comes through the story map's key --------------------------


class TestTheCoverageIsTheStoryMapsAnswer:
    def test_the_two_key_functions_agree(self):
        """Sharing the key only works if both sides spell it the same."""
        assert scope_key(ALL_SCOPES) == _scope_key(ALL_SCOPES) == "all"
        assert scope_key(["b", "a"]) == _scope_key(["b", "a"]) == ["a", "b"]
        assert scope_key([]) == _scope_key([]) == []

    def test_a_layer_edit_does_not_rerun_the_story_map(
        self, corpus, dataset, monkeypatch
    ):
        calls = []

        def fake(spec, scopes, config=None):
            calls.append(config)
            return {
                "points": [],
                "areas": [{"geoid": "29019", "stories": 7}],
                "meta": {},
            }

        monkeypatch.setattr("visuals.corpus.run_story_map", fake)
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        scopes = [dataset.slug]
        one = layermap.run_layer_map(
            spec, scopes, {"kind": "layermap", "base_coverage": True, "layers": []}
        )
        two = layermap.run_layer_map(
            spec,
            scopes,
            {
                "kind": "layermap",
                "base_coverage": True,
                "layers": [{"variable": "median_age"}],
            },
        )
        assert one["areas"] == two["areas"] == [{"geoid": "29019", "stories": 7}]
        assert len(calls) == 1

    def test_a_story_map_already_answered_is_not_asked_again(
        self, corpus, dataset, monkeypatch
    ):
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        scopes = [dataset.slug]
        answer_once(
            "visuals.storymap",
            [spec, scope_key(scopes), ""],
            lambda: {
                "points": [],
                "areas": [{"geoid": "29095", "stories": 3}],
                "meta": {},
            },
        )

        def never(*a, **k):
            raise AssertionError("the story map was re-run")

        monkeypatch.setattr("visuals.corpus.run_story_map", never)
        out = layermap.run_layer_map(
            spec, scopes, {"kind": "layermap", "base_coverage": True, "layers": []}
        )
        assert out["areas"] == [{"geoid": "29095", "stories": 3}]


# --- the shaded layer is cut in a known order and says so --------------------


def _mentions(geoids_by_article):
    from explorer.models import ArticleEnrichment

    for article_id, geoids in geoids_by_article.items():
        ArticleEnrichment.objects.filter(article_id=article_id).update(
            geoids=__import__("json").dumps(geoids)
        )


class TestTheShadedLayerIsCutInOrder:
    def test_the_cut_is_reported_and_by_id(self, corpus, dataset, monkeypatch):
        _mentions({"a0": ["29019"], "a1": ["29095"], "a2": ["29510"]})
        monkeypatch.setattr(corpus_module, "MAX_RAW_GROUPS", 2)
        # A county code stands for itself; the crosswalk is not the claim.
        monkeypatch.setattr(
            "datasets.geo.to_county", lambda g, level: g if len(g) == 5 else None
        )
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        out = corpus_module.run_story_map(spec, [dataset.slug], {})
        assert out["meta"]["areas_truncated"] == 2
        assert out["meta"]["area_stories"] == 2
        # a0 and a1, the first two by id -- never a2 on one run and a0 on the next.
        assert sorted(a["geoid"] for a in out["areas"]) == ["29019", "29095"]

    def test_under_the_limit_nothing_is_said(self, corpus, dataset):
        _mentions({"a0": ["29019"], "a1": ["29095"], "a2": ["29510"]})
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        out = corpus_module.run_story_map(spec, [dataset.slug], {})
        assert "areas_truncated" not in out["meta"]
        assert out["meta"]["area_stories"] == 3

    def test_the_query_is_ordered(self, corpus, dataset):
        _mentions({"a0": ["29019"]})
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        with CaptureQueriesContext(connections["crawler"]) as ctx:
            corpus_module.run_story_map(spec, [dataset.slug], {})
        reads = [
            q["sql"]
            for q in ctx.captured_queries
            if "geoids" in q["sql"] and "ORDER BY" in q["sql"]
        ]
        assert reads, [q["sql"][:80] for q in ctx.captured_queries]


# --- rows are locked before they are changed ---------------------------------


def _locking_reads(ctx, table):
    return [
        q["sql"]
        for q in ctx.captured_queries
        if q["sql"].startswith("SELECT")
        and table in q["sql"]
        and "FOR UPDATE" in q["sql"]
    ]


def _bare_reads(ctx, table):
    return [
        q["sql"]
        for q in ctx.captured_queries
        if q["sql"].startswith("SELECT")
        and table in q["sql"]
        and "FOR UPDATE" not in q["sql"]
        and "IN (" not in q["sql"]
    ]


class TestImportApplyAndRevertLockTheirRows:
    def test_apply_locks_in_one_query(self, editor, article):
        from review.services import audited_update_rows

        table = article._meta.db_table
        with CaptureQueriesContext(connections["crawler"]) as ctx:
            entry = audited_update_rows(
                editor, type(article), {"a1": {"author": "New"}}, "import:apply"
            )
        assert len(_locking_reads(ctx, table)) == 1
        assert not _bare_reads(ctx, table)
        article.refresh_from_db()
        assert article.author == "New"
        assert entry.before == {"a1": {"author": "Jane Doe"}}

    def test_revert_locks_in_one_query(self, editor, article):
        from review.services import audited_update, revert

        entry = audited_update(editor, [article], {"author": "Wrong"}, "edit:author")
        table = article._meta.db_table
        with CaptureQueriesContext(connections["crawler"]) as ctx:
            revert(editor, entry)
        assert len(_locking_reads(ctx, table)) == 1
        assert not _bare_reads(ctx, table)
        article.refresh_from_db()
        assert article.author == "Jane Doe"

    def test_a_vanished_row_still_fails_the_batch(self, editor, article):
        from review.services import audited_update_rows

        with pytest.raises(ValueError, match="no longer exists"):
            audited_update_rows(
                editor, type(article), {"nope": {"author": "x"}}, "import:apply"
            )


# --- a retraction is decided under the event's lock --------------------------


class TestARetractionTakesTheEventsLock:
    def test_the_lock_precedes_the_check(self, editor):
        from tests.test_outlet_events import SALE
        from visuals.models import OutletEvent
        from visuals.outlet_events import EventError, record

        sale = record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        table = OutletEvent._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            record(editor, {"note": "wrong paper"}, retracts=sale)
        sql = [q["sql"] for q in ctx.captured_queries]
        locks = [i for i, q in enumerate(sql) if "FOR UPDATE" in q and table in q]
        checks = [
            i
            for i, q in enumerate(sql)
            if "FOR UPDATE" not in q and 'WHERE "outlet_event"."retracts_id"' in q
        ]
        inserts = [
            i for i, q in enumerate(sql) if q.startswith("INSERT") and table in q
        ]
        assert locks and checks and inserts, sql
        assert locks[0] < checks[0] < inserts[0]
        with pytest.raises(EventError, match="already retracted"):
            record(editor, {"note": "again"}, retracts=sale)
        assert OutletEvent.objects.count() == 2
