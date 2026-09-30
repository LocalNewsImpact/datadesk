"""Four findings of the 2026-09-30 review, each a one-file change.

- Tooltip content is HTML built from payload strings; every string now
  passes through `esc` (`tipRow`, `tipHead`).
- A rejected boundary fetch is not remembered for the page's life, and a
  renderer that threw is reported as such rather than as missing
  boundary data.
- `publish` reads one snapshot row under the visual's lock, checks that
  row and pins that row; a first capture of nothing is not pinned.
- `record_snapshot` chooses its version under the same lock.
- The suite runs across the cores.

The renderer functions are executed with node rather than reimplemented:
a Python copy would prove the copy.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from visuals.models import Visual
from visuals.services import NotPublishable, publish, record_snapshot

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "static/js/datadesk-chart.js"


def _function(source, name):
    """The text of a two-space-indented `function name(...) {...}`."""
    start = source.index(f"function {name}(")
    return source[start : source.index("\n  }", start) + 4]


def _const(source, name):
    """The text of a `const name = ...;` at two-space indent."""
    start = source.index(f"  const {name} = ")
    return source[start : source.index(";\n", start) + 1]


def _node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the renderer with")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        where = fh.name
    done = subprocess.run([node, where], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# --- tooltips -----------------------------------------------------------------


class TestATooltipShowsMarkupAsText:
    def test_a_row_escapes_label_and_value(self):
        source = CHART.read_text()
        script = "\n".join(
            [
                _const(source, "fmt"),
                _const(source, "esc"),
                _function(source, "tipRow"),
                _function(source, "tipHead"),
                'const bad = "<img src=x onerror=alert(1)>";',
                "console.log(JSON.stringify({",
                '  row: tipRow("owner", bad),',
                "  label: tipRow(bad, 1),",
                "  head: tipHead('Tom & \"Jerry\"'),",
                '  number: tipRow("stories", 1234),',
                '  missing: tipRow("owner", null),',
                "}));",
            ]
        )
        out = _node(script)
        assert "<img" not in out["row"]
        assert "&lt;img src=x onerror=alert(1)&gt;" in out["row"]
        assert "<img" not in out["label"]
        assert out["head"] == "<strong>Tom &amp; &quot;Jerry&quot;</strong>"
        # Numbers keep their formatting and a missing value its dash.
        assert "1,234" in out["number"]
        assert "—" in out["missing"]

    def test_every_head_goes_through_the_escape(self):
        """No tooltip builds its `<strong>` line from a raw expression."""
        source = CHART.read_text()
        # The one raw interpolation is the helper's own, of the escaped text.
        assert source.count("<strong>${") == 1
        assert "`<strong>${esc(text)}</strong>`" in source
        assert source.count("tipHead(") >= 10
        # innerHTML has one writer, the tooltip node, whose input is
        # built only from tipRow and tipHead.
        assert source.count("innerHTML = ") == 1


# --- boundary fetches ---------------------------------------------------------


class TestABoundaryFetchThatFailedIsRetried:
    def test_a_rejection_is_not_cached(self):
        source = CHART.read_text()
        start = source.index("  const geoCache = {};")
        fetch_json = source[start : source.index("\n  }", start) + 4]
        script = "\n".join(
            [
                "let calls = 0;",
                "global.fetch = (url) => {",
                "  calls += 1;",
                '  if (calls === 1) return Promise.reject(new Error("blip"));',
                "  return Promise.resolve(",
                "    { ok: true, json: async () => ({ n: calls }) });",
                "};",
                fetch_json,
                "(async () => {",
                "  const out = {};",
                '  try { await fetchJSON("a.json"); out.first = "resolved"; }',
                "  catch (e) { out.first = e.message; }",
                '  out.second = await fetchJSON("a.json");',
                '  out.third = await fetchJSON("a.json");',
                "  out.calls = calls;",
                "  console.log(JSON.stringify(out));",
                "})();",
            ]
        )
        out = _node(script)
        assert out["first"] == "blip"
        assert out["second"] == {"n": 2}
        # The success is what is remembered.
        assert out["third"] == {"n": 2}
        assert out["calls"] == 2

    def test_a_status_error_is_not_cached_either(self):
        source = CHART.read_text()
        start = source.index("  const geoCache = {};")
        fetch_json = source[start : source.index("\n  }", start) + 4]
        script = "\n".join(
            [
                "let calls = 0;",
                "global.fetch = () => {",
                "  calls += 1;",
                "  return Promise.resolve(calls === 1",
                "    ? { ok: false }",
                "    : { ok: true, json: async () => ({ ok: calls }) });",
                "};",
                fetch_json,
                "(async () => {",
                '  const first = await fetchJSON("b.json")',
                '    .then(() => "resolved", (e) => e.message);',
                '  const second = await fetchJSON("b.json");',
                "  console.log(JSON.stringify({ first, second, calls }));",
                "})();",
            ]
        )
        out = _node(script)
        assert out["first"] == "b.json"
        assert out["second"] == {"ok": 2}
        assert out["calls"] == 2


class TestARendererErrorIsNotCalledMissingBoundaries:
    def test_the_two_messages(self):
        source = CHART.read_text()
        script = "\n".join(
            [
                "const errors = [];",
                "console.error = (...args) =>",
                "  errors.push(String(args[1] && args[1].message));",
                _function(source, "unavailable"),
                _function(source, "undrawable"),
                "const a = {}, b = {}, c = {}, d = {};",
                'unavailable(a)(new Error("net::ERR_FAILED"));',
                'unavailable(b, " for this level.")(',
                '  new Error("tract/place maps need a joined GEOID column"));',
                'undrawable(c)(new TypeError("x is not a function"));',
                'unavailable(d, " for this level.")(new Error("404"));',
                "console.log(JSON.stringify({ a: a.textContent, b: b.textContent,",
                "  c: c.textContent, d: d.textContent, errors }));",
            ]
        )
        out = _node(script)
        assert out["a"] == "Boundary data unavailable."
        assert out["b"] == "tract/place maps need a joined GEOID column"
        assert out["d"] == "Boundary data unavailable for this level."
        assert out["c"] == "This chart could not be drawn: x is not a function"
        # The renderer's own error reaches the console; a missing file
        # does not.
        assert out["errors"] == ["x is not a function"]

    def test_both_map_renderers_separate_the_two(self):
        """`.then(draw, unavailable).catch(undrawable)` at each site: the
        fetch's rejection takes the first road, a throw inside draw the
        second, and neither is a bare `.catch(() => ...)` any more."""
        source = CHART.read_text()
        assert len(re.findall(r"\}, unavailable\(el", source)) == 2
        assert source.count(".catch(undrawable(el))") == 2
        assert 'el.textContent = "Boundary data unavailable."' not in source
        # A per-state file that is missing is named, not swallowed.
        assert ".catch(() => [])" not in source
        assert "console.warn(`datadesk-chart: no ${level} file for state" in source


# --- publish and capture ------------------------------------------------------

pytestmark = pytest.mark.django_db


@pytest.fixture
def author(django_user_model):
    return django_user_model.objects.create_user(
        "pinner", email="pinner@localnewsimpact.org", is_superuser=True
    )


@pytest.fixture
def visual(author):
    return Visual.objects.create(
        slug="a-figure",
        title="A figure",
        source_kind="bigquery",
        query="SELECT n FROM x",
        template="table",
        created_by=author,
    )


def _queries(ctx):
    return [q["sql"] for q in ctx.captured_queries]


class TestPublishPinsTheRowItChecked:
    def test_a_first_capture_of_nothing_is_kept_but_not_pinned(self, visual, author):
        with (
            mock.patch("explorer.analytics.query_rows", return_value=[]),
            pytest.raises(NotPublishable, match="nothing to publish"),
        ):
            publish(visual, author)
        visual.refresh_from_db()
        assert visual.pinned_snapshot is None
        assert visual.status == Visual.DRAFT
        # The empty capture is a fact worth keeping: version 1, unpinned.
        assert [s.version for s in visual.snapshots.all()] == [1]

    def test_a_first_capture_with_rows_is_pinned(self, visual, author):
        with mock.patch("explorer.analytics.query_rows", return_value=[{"n": 1}]):
            publish(visual, author)
        visual.refresh_from_db()
        assert visual.status == Visual.PUBLISHED
        assert visual.pinned_snapshot.version == 1
        assert visual.pinned_snapshot.data == [{"n": 1}]

    def test_the_snapshot_is_read_once_under_the_lock(self, visual, author):
        record_snapshot(visual, author, [{"n": 1}])
        snapshot_table = visual.snapshots.model._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            publish(visual, author)
        sql = _queries(ctx)
        locks = [i for i, q in enumerate(sql) if "FOR UPDATE" in q]
        assert len(locks) == 1, sql
        reads = [
            i
            for i, q in enumerate(sql)
            if q.startswith("SELECT") and snapshot_table in q and "DESC" in q
        ]
        # One read of the latest snapshot, after the lock: the row
        # checked is the row pinned.
        assert len(reads) == 1, sql
        assert reads[0] > locks[0]
        pins = [
            i
            for i, q in enumerate(sql)
            if q.startswith("UPDATE") and "pinned_snapshot" in q
        ]
        assert pins and pins[0] > reads[0]

    def test_an_empty_latest_is_refused_whatever_came_before(self, visual, author):
        record_snapshot(visual, author, [{"n": 1}])
        record_snapshot(visual, author, [])
        with pytest.raises(NotPublishable, match="version 2 came back empty"):
            publish(visual, author)
        visual.refresh_from_db()
        assert visual.pinned_snapshot is None

    def test_internal_fields_are_refused_before_any_capture(self, visual, author):
        visual.spec = {"fields": ["extraction_review"]}
        visual.save()
        with (
            mock.patch(
                "visuals.corpus.internal_fields", return_value=["extraction_review"]
            ),
            mock.patch("explorer.analytics.query_rows") as rows,
            pytest.raises(NotPublishable, match="internal use"),
        ):
            publish(visual, author)
        rows.assert_not_called()
        assert not visual.snapshots.exists()


class TestACaptureChoosesItsVersionUnderTheLock:
    def test_the_lock_precedes_the_max(self, visual, author):
        visual_table = Visual._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            snapshot = record_snapshot(visual, author, [{"n": 1}])
        sql = _queries(ctx)
        locks = [
            i for i, q in enumerate(sql) if "FOR UPDATE" in q and visual_table in q
        ]
        maxes = [i for i, q in enumerate(sql) if 'MAX("' in q or "MAX(" in q]
        assert locks and maxes, sql
        assert locks[0] < maxes[0]
        assert snapshot.version == 1

    def test_versions_count_up(self, visual, author):
        assert record_snapshot(visual, author, [{"n": 1}]).version == 1
        assert record_snapshot(visual, author, [{"n": 2}]).version == 2


# --- the suite runs across the cores -----------------------------------------


class TestTheSuiteRunsAcrossTheCores:
    def test_make_test_uses_every_core(self):
        makefile = (ROOT / "Makefile").read_text()
        assert "pytest -n auto --cov" in makefile

    def test_xdist_is_a_dev_requirement(self):
        assert "pytest-xdist" in (ROOT / "requirements-dev.txt").read_text()
