"""Item 14 of the 2026-09-30 review, and the builder half of item 27.

- `import_registry` wrote one `update_or_create` per outlet; now three
  queries for the table.
- `apply_current([one])` loaded every current event; now that outlet's.
- The reliability report ran one query per variable; now one per level.
- The builder preview loads d3-sankey and passes the hashed boundary URLs.
"""

# ruff: noqa: F401, F811 -- fixtures are imported from the modules that own them.

import csv
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.test_outlet_events import SALE, editor
from visuals.models import CensusValue, Outlet, OutletEvent
from visuals.outlets import import_registry

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.django_db

COLUMNS = ["outlet_id", "outlet", "state", "city", "county", "county_fips", "map"]


def _registry(tmp_path, n, prefix="o"):
    path = tmp_path / "registry.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for i in range(n):
            w.writerow(
                {
                    "outlet_id": f"{prefix}-{i}",
                    "outlet": f"Paper {i}",
                    "state": "MO",
                    "city": "Weston",
                    "county": "Platte",
                    "county_fips": "29165",
                    "map": "yes",
                }
            )
    return str(path)


def _writes(ctx, table):
    return [
        q["sql"]
        for q in ctx.captured_queries
        if table in q["sql"] and q["sql"].split()[0] in ("INSERT", "UPDATE")
    ]


class TestTheRegistryIsWrittenInBulk:
    def test_a_first_import_is_one_insert(self, tmp_path):
        table = Outlet._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            counts = import_registry(_registry(tmp_path, 12))
        assert counts["created"] == 12 and counts["updated"] == 0
        writes = _writes(ctx, table)
        assert len(writes) == 1 and writes[0].startswith("INSERT")
        assert Outlet.objects.count() == 12

    def test_a_second_import_is_one_update(self, tmp_path):
        import_registry(_registry(tmp_path, 12))
        first = Outlet.objects.get(pk="o-3").imported_at
        table = Outlet._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            counts = import_registry(_registry(tmp_path, 12))
        assert counts == {
            "rows": 12,
            "created": 0,
            "updated": 12,
            "removed": 0,
            "events_applied": 0,
        }
        writes = _writes(ctx, table)
        assert len(writes) == 1 and writes[0].startswith("UPDATE")
        assert Outlet.objects.get(pk="o-3").imported_at > first

    def test_new_and_gone_rows_are_still_counted(self, tmp_path):
        import_registry(_registry(tmp_path, 3))
        counts = import_registry(_registry(tmp_path, 5, prefix="p"))
        assert (counts["created"], counts["updated"], counts["removed"]) == (5, 0, 3)
        assert sorted(Outlet.objects.values_list("pk", flat=True)) == [
            f"p-{i}" for i in range(5)
        ]

    def test_a_repeated_id_is_still_refused(self, tmp_path):
        path = _registry(tmp_path, 2)
        with open(path, "a") as fh:
            fh.write("o-1,Again,MO,Weston,Platte,29165,yes\n")
        with pytest.raises(ValueError, match="repeated outlet_id"):
            import_registry(path)
        assert Outlet.objects.count() == 0


class TestOneOutletsEventsAreRead:
    def test_apply_current_for_one_outlet_reads_its_events_only(self, editor, tmp_path):
        from visuals.outlet_events import apply_current, record

        import_registry(_registry(tmp_path, 3))
        for i in range(3):
            record(
                editor,
                {
                    **SALE,
                    "outlet_id": f"o-{i}",
                    "outlet_name": f"Paper {i}",
                    "sets_current": "1",
                },
            )
        table = OutletEvent._meta.db_table
        with CaptureQueriesContext(connection) as ctx:
            apply_current(["o-1"])
        reads = [q["sql"] for q in ctx.captured_queries if table in q["sql"]]
        assert reads
        for sql in reads:
            assert "o-1" in sql, sql

    def test_apply_current_for_everyone_is_unchanged(self, editor, tmp_path):
        from visuals.outlet_events import apply_current, record

        import_registry(_registry(tmp_path, 2))
        record(
            editor,
            {**SALE, "outlet_id": "o-0", "outlet_name": "Paper 0", "sets_current": "1"},
        )
        Outlet.objects.filter(pk="o-0").update(owner="")
        assert apply_current() == 1
        assert Outlet.objects.get(pk="o-0").owner == SALE["to_owner"]


class TestTheReliabilityReportIsOneQuery:
    def test_one_query_per_level(self):
        from visuals import census

        CensusValue.objects.create(
            year=census.YEAR,
            level="tract",
            geoid="29019000100",
            variable="median_age",
            estimate=33,
            moe=1,
        )
        CensusValue.objects.create(
            year=census.YEAR,
            level="tract",
            geoid="29019000200",
            variable="median_age",
            estimate=33,
            moe=40,
        )
        with CaptureQueriesContext(connection) as ctx:
            report = {r["key"]: r for r in census.reliability("tract")}
        assert len(ctx.captured_queries) == 1
        assert report["median_age"]["places"] == 2
        assert report["median_age"]["hatched"] == 1
        assert report["foreign_born"]["places"] == 0


class TestTheBuilderPreviewHasWhatItDraws:
    def test_sankey_and_the_hashed_boundary_urls(self):
        page = (ROOT / "templates/visuals/builder_edit.html").read_text()
        assert "js/d3-sankey.min.js" in page
        assert "geoUrls: {" in page
        for level in ("nation", "states", "counties"):
            assert f"{level}: \"{{% static 'geo/{level}-10m.json' %}}\"" in page
