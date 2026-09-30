"""Five shared choke points, each with the test that caught it.

The version rebuild stampeded; typed dates cast the column and defeated
its index; a waiter for a shared answer slept in a server thread; an
anonymous reader could run a live source on every hit; and "__all__" was
read as a list of dataset slugs (docs/REVIEW_2026-09-30.md, items 2, 3, 5, 7, 8).
"""

import threading
import time
from datetime import UTC, datetime
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import Client

from accounts.access import ALL_SCOPES
from tests.test_builder_steps import author, corpus, dataset, newsroom
from visuals import corpus as corpus_module
from visuals.corpus import (
    StillComputing,
    _base_queryset,
    _publisher_rows,
    answer_once,
    corpus_version,
    day_range,
    scope_key,
    scoped_members,
)
from visuals.models import Visual
from visuals.views import newsroom_counts_for

__all__ = ["author", "corpus", "dataset", "newsroom"]

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

UTC = UTC


class TestAllScopesIsAMarkerNotAList:
    def test_the_marker_reads_every_membership(self, corpus, dataset):
        assert scoped_members(ALL_SCOPES).count() == 1
        assert scoped_members([dataset.slug]).count() == 1
        assert scoped_members(["another"]).count() == 0
        # Empty is unfiltered, as it always was: a visual not yet wired to
        # a dataset offers every newsroom.
        assert scoped_members([]).count() == 1
        assert scoped_members(None).count() == 1

    def test_a_superuser_gets_complete_counts_and_publishers(self, corpus, newsroom):
        counts = newsroom_counts_for(ALL_SCOPES)
        assert counts.get(newsroom.id), counts
        # One (type, frequency) pair per source in scope; the marker read
        # as a list of slugs returned none.
        assert len(_publisher_rows(ALL_SCOPES)) == 1
        assert _publisher_rows(["another"]) == []

    def test_the_key_does_not_spell_the_marker_out(self):
        assert scope_key(ALL_SCOPES) == "all"
        assert scope_key(["b", "a"]) == ["a", "b"]
        assert scope_key(None) == [] and scope_key([]) == []


class TestATypedDayIsATimestampRange:
    def test_half_open_and_aware(self):
        start, end = day_range("2026-03-01", "2026-03-31")
        assert start == datetime(2026, 3, 1, tzinfo=UTC)
        assert end == datetime(2026, 4, 1, tzinfo=UTC)
        assert day_range("", None) == (None, None)
        only_from, none = day_range("2026-03-01", "")
        assert only_from == datetime(2026, 3, 1, tzinfo=UTC) and none is None

    def test_a_date_object_is_taken_too(self):
        from datetime import date

        start, end = day_range(date(2026, 3, 1), date(2026, 3, 31))
        assert (start, end) == (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 4, 1, tzinfo=UTC),
        )

    def test_the_query_casts_nothing(self):
        sql = str(
            _base_queryset(
                {"from": "2026-03-01", "to": "2026-03-31", "subset": "complete"},
                ALL_SCOPES,
            ).query
        )
        assert "::date" not in sql and "AT TIME ZONE" not in sql
        assert 'publish_date" >= ' in sql and 'publish_date" < ' in sql

    def test_the_last_second_of_the_last_day_is_in(self, corpus):
        from explorer.models import Article

        ids = set(
            Article.objects.filter(
                publish_date__gte=day_range("2026-03-01", "2026-03-31")[0],
                publish_date__lt=day_range("2026-03-01", "2026-03-31")[1],
            ).values_list("id", flat=True)
        )
        last_second = datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC)
        next_day = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
        late = Article.objects.create(
            id="a-late", status="ok", publish_date=last_second
        )
        after = Article.objects.create(id="a-after", status="ok", publish_date=next_day)
        start, end = day_range("2026-03-01", "2026-03-31")
        drawn = set(
            Article.objects.filter(
                publish_date__gte=start, publish_date__lt=end
            ).values_list("id", flat=True)
        )
        assert late.id in drawn and after.id not in drawn
        assert ids <= drawn


class TestOneRequestRebuildsTheVersion:
    def test_a_rebuild_in_progress_serves_the_last_stamp(
        self, django_assert_num_queries
    ):
        cache.delete("corpus.version")
        cache.set("corpus.version.previous", "stamp-before", 60)
        assert cache.add("corpus.version.lock", 1, 30)
        try:
            with django_assert_num_queries(0):
                assert corpus_version() == "stamp-before"
        finally:
            cache.delete("corpus.version.lock")

    def test_a_rebuild_keeps_its_stamp_for_the_next_one(self, corpus):
        cache.delete("corpus.version")
        cache.delete("corpus.version.previous")
        stamp = corpus_version()
        assert cache.get("corpus.version") == stamp
        assert cache.get("corpus.version.previous") == stamp
        assert cache.get("corpus.version.lock") is None

    def test_with_nothing_to_serve_a_waiter_takes_the_stamp_when_it_lands(
        self, django_assert_num_queries
    ):
        cache.delete("corpus.version")
        cache.delete("corpus.version.previous")
        assert cache.add("corpus.version.lock", 1, 30)

        def rebuilder():
            time.sleep(0.5)
            cache.set("corpus.version", "stamp-new", 60)
            cache.delete("corpus.version.lock")

        threading.Thread(target=rebuilder).start()
        with django_assert_num_queries(0):
            assert corpus_version() == "stamp-new"

    def test_the_seven_queries_run_once_under_concurrency(self):
        """Four requests arrive on an expired stamp; one rebuilds.

        The rebuild is stood in for: a thread cannot see the test
        transaction's tables, and what is under test is the lock."""
        cache.delete("corpus.version")
        cache.delete("corpus.version.previous")
        calls = []

        def counted(*models):
            calls.append(1)
            time.sleep(0.4)
            cache.set("corpus.version", "stamp-x", 60)
            cache.set("corpus.version.previous", "stamp-x", 600)
            return "stamp-x"

        with mock.patch.object(corpus_module, "_stamp_now", counted):
            got = []
            threads = [
                threading.Thread(target=lambda: got.append(corpus_version()))
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        assert len(calls) == 1 and len(set(got)) == 1


class TestAReadersLiveAnswerIsShared:
    @pytest.fixture
    def live_stories(self, author):
        from visuals.models import STORIES
        from visuals.services import publish

        visual = Visual.objects.create(
            slug="stories-live",
            title="Stories",
            source_kind=STORIES,
            created_by=author,
            config={"kind": "table"},
        )
        with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 1}]):
            publish(visual, author)
        Visual.objects.filter(pk=visual.pk).update(allow_live=True)
        visual.refresh_from_db()
        return visual

    def test_two_anonymous_readers_run_the_source_once(self, live_stories):
        reader = Client()
        with mock.patch(
            "visuals.views.fetch_source_data", return_value=[{"a": 2}]
        ) as fetch:
            first = reader.get("/visuals/stories-live/data.json?live=1")
            second = reader.get("/visuals/stories-live/data.json?live=1")
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["data"] == [{"a": 2}] == second.json()["data"]
        assert fetch.call_count == 1

    def test_an_edit_is_a_new_answer(self, live_stories):
        reader = Client()
        with mock.patch(
            "visuals.views.fetch_source_data", return_value=[{"a": 2}]
        ) as fetch:
            reader.get("/visuals/stories-live/data.json?live=1")
            Visual.objects.filter(pk=live_stories.pk).update(
                updated_at=datetime(2030, 1, 1, tzinfo=UTC)
            )
            reader.get("/visuals/stories-live/data.json?live=1")
        assert fetch.call_count == 2

    def test_the_author_always_sees_the_source_now(self, client, author, live_stories):
        client.force_login(author)
        with mock.patch(
            "visuals.views.fetch_source_data", return_value=[{"a": 3}]
        ) as fetch:
            client.get("/visuals/stories-live/data.json?live=1")
            client.get("/visuals/stories-live/data.json?live=1")
        assert fetch.call_count == 2


class TestAWaiterDoesNotHoldAThread:
    def test_asked_not_to_wait_it_says_the_answer_is_coming(self, monkeypatch):
        monkeypatch.setattr(corpus_module, "corpus_version", lambda: "v1")
        key = corpus_module._cache_key("t", ["q"])
        assert cache.add(f"{key}.running", 1, 30)
        try:
            with pytest.raises(StillComputing) as caught:
                answer_once("t", ["q"], lambda: "never", wait=False)
            assert caught.value.retry_after == 3
        finally:
            cache.delete(f"{key}.running")

    def test_an_answer_already_in_is_returned_without_waiting(self, monkeypatch):
        monkeypatch.setattr(corpus_module, "corpus_version", lambda: "v1")
        assert answer_once("t", ["q2"], lambda: "first") == "first"
        assert answer_once("t", ["q2"], lambda: "never", wait=False) == "first"

    def test_the_feed_answers_202_and_when_to_come_back(self, author):
        from visuals.models import STORIES
        from visuals.services import publish

        visual = Visual.objects.create(
            slug="slow-live",
            title="Slow",
            source_kind=STORIES,
            created_by=author,
            config={"kind": "table"},
        )
        with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 1}]):
            publish(visual, author)
        Visual.objects.filter(pk=visual.pk).update(allow_live=True)
        reader = Client()
        with mock.patch(
            "visuals.views.fetch_source_data", side_effect=StillComputing("k", 3)
        ):
            got = reader.get("/visuals/slow-live/data.json?live=1")
            csv = reader.get("/visuals/slow-live/data.csv?live=1")
        assert got.status_code == 202 and got["Retry-After"] == "3"
        assert got.json() == {"computing": True, "retry_after": 3}
        assert csv.status_code == 202

    def test_the_pages_ask_again_on_202(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "templates/visuals/renderers"
        for name in ("builder.html", "table.html"):
            assert "r.status === 202" in (root / name).read_text(), name
