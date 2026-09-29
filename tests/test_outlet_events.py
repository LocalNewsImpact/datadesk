"""An outlet's history: sales, mergers, closures, launches -- never overwritten.

The registry says what an outlet is now and overwrites itself to say it. On
2026-09-27 fourteen owners were corrected in one afternoon and none of the
old values survived anywhere a timeline could read. These are the events that
keep them (docs/OUTLET_EVENTS.md).
"""

import csv
import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client

from accounts.models import DATADESK, Grant
from explorer.models import Source
from visuals import outlet_events
from visuals.models import Outlet, OutletEvent
from visuals.outlet_events import EventError, clean, load, record
from visuals.outlet_views import EXPORT

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

SEED = outlet_events.__file__.replace("outlet_events.py", "data/outlet_events_seed.csv")


def _user(name, role):
    user = User.objects.create_user(name, email=f"{name}@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
    return user


@pytest.fixture
def editor():
    return _user("editor", "editor")


@pytest.fixture
def viewer():
    return _user("viewer", "viewer")


def _outlet(oid="o-weston", source_id="", owner="Jim and Beth McPherson", **kw):
    row = {
        "outlet": kw.get("name", "Weston Chronicle"),
        "owner": owner,
        "status": kw.get("status", "active"),
        "merged_into": "",
        "map": "yes",
    }
    return Outlet.objects.create(
        outlet_id=oid,
        source_id=source_id,
        name=row["outlet"],
        owner=owner,
        status=row["status"],
        on_map=True,
        category="collected",
        row=row,
    )


SALE = {
    "event": "sold",
    "effective_date": "2026-09-01",
    "date_precision": "by",
    "from_owner": "Jim and Beth McPherson",
    "to_owner": "Megan Jantos",
    "evidence_url": "https://mopress.com/stories/september-2026-missouri-press-news,39849",
}


class TestWhatAnEventMustCarry:
    def test_a_sale_names_its_buyer(self):
        _, errors = clean({**SALE, "to_owner": "", "outlet_name": "Weston"})
        assert any("needs to_owner" in e for e in errors)

    def test_it_says_where_it_came_from(self):
        _, errors = clean({**SALE, "evidence_url": "", "outlet_name": "Weston"})
        assert any("where this came from" in e for e in errors)

    def test_a_correction_needs_no_source(self):
        """A placeholder replaced by a name is our data catching up."""
        _, errors = clean(
            {"event": "correction", "outlet_name": "Weston", "to_owner": "Megan"}
        )
        assert errors == []

    def test_a_planned_closure_is_not_history(self):
        """The Fayette Advertiser plans a last edition in March 2027. Recorded
        now, the timeline would show a paper closed that is printing."""
        later = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        _, errors = clean(
            {
                "event": "closed",
                "outlet_name": "Fayette",
                "effective_date": later,
                "note": "countdown",
            }
        )
        assert any("once it has happened" in e for e in errors)

    def test_a_date_without_precision_is_a_day(self):
        fields, _ = clean({**SALE, "date_precision": "", "outlet_name": "W"})
        assert fields["date_precision"] == "day"

    def test_no_date_has_no_precision(self):
        fields, _ = clean({**SALE, "effective_date": "", "outlet_name": "W"})
        assert fields["effective_date"] is None
        assert fields["date_precision"] == ""


class TestNothingIsOverwritten:
    def test_an_event_is_not_edited(self, editor):
        event = record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        event.note = "changed"
        with pytest.raises(ValidationError):
            event.save()

    def test_an_event_is_not_deleted(self, editor):
        event = record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        with pytest.raises(ValidationError):
            event.delete()

    def test_a_retraction_withdraws_and_both_stay(self, editor):
        _outlet()
        sale = record(
            editor,
            {
                **SALE,
                "outlet_id": "o-weston",
                "outlet_name": "Weston Chronicle",
                "sets_current": "1",
            },
        )
        assert Outlet.objects.get(pk="o-weston").owner == "Megan Jantos"
        record(editor, {"note": "wrong paper"}, retracts=sale)
        assert OutletEvent.objects.count() == 2
        # Back to what the registry file says.
        assert Outlet.objects.get(pk="o-weston").owner == "Jim and Beth McPherson"

    def test_a_retraction_says_why(self, editor):
        sale = record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        with pytest.raises(EventError):
            record(editor, {"note": ""}, retracts=sale)

    def test_retracted_once(self, editor):
        sale = record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        record(editor, {"note": "wrong"}, retracts=sale)
        with pytest.raises(EventError):
            record(editor, {"note": "again"}, retracts=sale)


class TestTheCurrentState:
    def test_history_does_not_touch_the_registry(self, editor):
        """A 2018 sale the record already reflects is history only."""
        _outlet()
        record(editor, {**SALE, "outlet_id": "o-weston", "outlet_name": "Weston"})
        assert Outlet.objects.get(pk="o-weston").owner == "Jim and Beth McPherson"

    def test_a_current_merger_takes_it_off_the_map(self, editor):
        _outlet()
        record(
            editor,
            {
                "event": "merged",
                "outlet_id": "o-weston",
                "outlet_name": "Weston",
                "merged_into": "plattecitizen.com",
                "note": "x",
                "sets_current": "on",
            },
        )
        o = Outlet.objects.get(pk="o-weston")
        assert o.status == "merged"
        assert (o.merged_into, o.on_map) == ("plattecitizen.com", False)

    def test_an_import_does_not_undo_a_recorded_sale(self, editor, tmp_path):
        """The file says what the crawler knew when it was built. A sale
        recorded since is laid back on top, or every import erased it."""
        from visuals.outlets import import_registry

        path = tmp_path / "registry.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["outlet_id", "outlet", "owner", "map"])
            w.writeheader()
            w.writerow(
                {
                    "outlet_id": "o-weston",
                    "outlet": "Weston Chronicle",
                    "owner": "Jim and Beth McPherson",
                    "map": "yes",
                }
            )
        import_registry(str(path))
        record(
            editor,
            {
                **SALE,
                "outlet_id": "o-weston",
                "outlet_name": "Weston",
                "sets_current": "1",
            },
        )
        counts = import_registry(str(path))
        assert Outlet.objects.get(pk="o-weston").owner == "Megan Jantos"
        assert counts["events_applied"] == 1


class TestTheCrawlerRecordAgrees:
    def test_a_current_sale_writes_the_publisher_record(self, crawler_schema, editor):
        Source.objects.create(
            id="s-weston",
            host="plattechronicle.com",
            host_norm="plattechronicle.com",
            canonical_name="Weston Chronicle",
            owner="McPherson Beth",
        )
        _outlet(source_id="s-weston")
        record(
            editor,
            {
                **SALE,
                "outlet_id": "o-weston",
                "outlet_name": "Weston",
                "sets_current": "1",
            },
        )
        assert Source.objects.get(pk="s-weston").owner == "Megan Jantos"
        # The write is the event, not a second one.
        assert OutletEvent.objects.count() == 1

    def test_every_owner_edit_is_an_event(self, crawler_schema, editor):
        """Whatever page writes an owner, the old one survives as history."""
        from review.services import audited_update

        source = Source.objects.create(
            id="s-1",
            host="one.example",
            host_norm="one.example",
            canonical_name="The One",
            owner="Old Owner",
        )
        audited_update(editor, [source], {"owner": "New Owner"}, "source:edit", "sold")
        event = OutletEvent.objects.get()
        assert (event.outlet_id, event.event) == ("s-1", "owner_changed")
        assert (event.from_owner, event.to_owner) == ("Old Owner", "New Owner")
        assert event.effective_date is None

    def test_an_edit_leaving_the_owner_is_no_event(self, crawler_schema, editor):
        from review.services import audited_update

        source = Source.objects.create(
            id="s-1",
            host="one.example",
            host_norm="one.example",
            canonical_name="The One",
            owner="Same",
            city="A",
        )
        audited_update(editor, [source], {"city": "B"}, "source:edit")
        audited_update(editor, [source], {"owner": "Same"}, "source:edit")
        assert not OutletEvent.objects.exists()


class TestBackfill:
    def test_the_same_file_twice_records_once(self, editor):
        rows = [{**SALE, "outlet_id": "o-weston", "outlet_name": "Weston"}]
        assert load(editor, rows)[:2] == (1, 0)
        assert load(editor, rows)[:2] == (0, 1)

    def test_one_bad_row_writes_nothing(self, editor):
        rows = [
            {**SALE, "outlet_name": "Weston"},
            {**SALE, "outlet_name": "Other", "to_owner": ""},
        ]
        recorded, _, errors = load(editor, rows)
        assert recorded == 0 and errors and errors[0].startswith("line 3")
        assert not OutletEvent.objects.exists()

    def test_the_seed_loads_clean(self, editor):
        """Every row of the shipped backfill is an event the rules accept."""
        rows = outlet_events.read_rows(SEED)
        recorded, skipped, errors = load(editor, rows)
        assert errors == []
        assert recorded == len(rows) and skipped == 0
        # History only: nothing in the seed rewrites the registry.
        assert not OutletEvent.objects.filter(sets_current=True).exists()


class TestThePages:
    def test_an_outlet_page_is_its_history(self, editor):
        _outlet()
        record(editor, {**SALE, "outlet_id": "o-weston", "outlet_name": "Weston"})
        client = Client()
        client.force_login(editor)
        page = client.get("/outlets/o-weston/").content.decode()
        assert "Megan Jantos" in page and "Record an event" in page
        # American dates: "by 09-01-2026", not 2026-09-01 or Sep 1, 2026.
        assert "by 09-01-2026" in " ".join(page.split())

    def test_an_editor_records_from_the_page(self, editor):
        _outlet()
        client = Client()
        client.force_login(editor)
        response = client.post("/outlets/o-weston/", {**SALE})
        assert response.status_code == 302
        assert OutletEvent.objects.get().origin == "form"

    def test_a_viewer_reads_but_does_not_record(self, viewer):
        _outlet()
        client = Client()
        client.force_login(viewer)
        page = client.get("/outlets/o-weston/")
        assert page.status_code == 200
        assert "Record an event" not in page.content.decode()
        assert client.post("/outlets/o-weston/", {**SALE}).status_code == 403
        assert not OutletEvent.objects.exists()

    def test_an_outlet_not_in_the_registry(self, editor):
        client = Client()
        client.force_login(editor)
        client.post(
            "/outlets/events/new/",
            {
                "outlet_name": "Oregon Times Observer",
                "event": "closed",
                "effective_date": "2024-12-26",
                "note": "Ripley retired",
            },
        )
        event = OutletEvent.objects.get()
        assert (event.outlet_id, event.outlet_name) == ("", "Oregon Times Observer")

    def test_the_export_is_a_timeline(self, editor):
        record(editor, {**SALE, "outlet_name": "Weston Chronicle"})
        client = Client()
        client.force_login(editor)
        response = client.get("/outlets/events/?format=csv")
        lines = response.content.decode().splitlines()
        assert lines[0].split(",") == list(EXPORT)
        assert lines[1].startswith("2026-09-01,by,")

    def test_the_list(self, editor):
        _outlet()
        client = Client()
        client.force_login(editor)
        assert "Weston Chronicle" in client.get("/outlets/?q=weston").content.decode()


def _review(new_status, **kw):
    return {
        "event": "status",
        "outlet_id": "o-weston",
        "outlet_name": "Weston",
        "new_status": new_status,
        "note": kw.pop("note", "reviewed"),
        "sets_current": kw.pop("sets_current", "on"),
        **kw,
    }


class TestAReviewedStatus:
    """A reviewer's word on what an outlet is -- replica, print, a duplicate --
    recorded here instead of in a file edited, committed and merged (2026-09-28:
    The Dixon Pilot, a replica, took a pull request to say so)."""

    def test_it_names_a_status(self):
        _, errors = clean({"event": "status", "outlet_name": "Weston", "note": "x"})
        assert any("needs new_status" in e for e in errors)

    def test_the_status_is_one_the_registry_draws(self):
        _, errors = clean(_review("gone"))
        assert any("Say what the outlet is" in e for e in errors)

    @pytest.mark.parametrize("status", ["merged", "duplicate"])
    def test_a_merged_or_duplicate_outlet_names_what_it_is_part_of(self, status):
        _, errors = clean(_review(status))
        assert any("needs merged_into" in e for e in errors)
        _, errors = clean(_review(status, merged_into="https://www.monroe-ralls.com/"))
        assert errors == []

    def test_only_a_status_event_carries_one(self):
        fields, _ = clean({**SALE, "outlet_name": "Weston", "new_status": "replica"})
        assert fields["new_status"] == ""

    def test_a_replica_is_drawn_as_one(self, editor):
        _outlet()
        note = "replica edition; not an active digital source"
        record(editor, _review("replica", note=note))
        o = Outlet.objects.get(pk="o-weston")
        assert (o.status, o.category, o.on_map) == ("replica", "replica", True)
        assert o.status_basis == note

    def test_a_legal_sheet_is_listed_but_not_drawn(self, editor):
        _outlet()
        record(
            editor,
            _review("legal", note="", evidence_url="https://www.mopublicnotices.com/"),
        )
        o = Outlet.objects.get(pk="o-weston")
        assert (o.status, o.category, o.on_map) == ("legal", "legal", False)
        assert o.status_basis == "Legal-notice publication"

    @pytest.mark.parametrize(
        "march, category", [(12, "collected"), (0, "not collected")]
    )
    def test_an_active_outlet_is_drawn_by_what_we_collect(
        self, editor, march, category
    ):
        o = _outlet(status="print")
        o.march_articles = march
        o.save()
        record(editor, _review("active"))
        o.refresh_from_db()
        assert (o.status, o.category, o.on_map) == ("active", category, True)

    def test_a_duplicate_points_at_what_it_duplicates(self, editor):
        _outlet()
        record(editor, _review("duplicate", merged_into="a1c30d15"))
        o = Outlet.objects.get(pk="o-weston")
        assert (o.status, o.merged_into, o.on_map) == ("duplicate", "a1c30d15", False)

    def test_history_only_leaves_the_registry_alone(self, editor):
        _outlet()
        record(editor, _review("replica", sets_current=""))
        assert Outlet.objects.get(pk="o-weston").status == "active"

    def test_an_import_keeps_the_review(self, editor, tmp_path):
        """The next rebuild says what the crawler's lists say; the review
        is laid back over it."""
        from visuals.outlets import import_registry

        path = tmp_path / "registry.csv"
        cols = ["outlet_id", "outlet", "status", "status_basis", "map", "map_category"]
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerow(
                {
                    "outlet_id": "o-weston",
                    "outlet": "Weston Chronicle",
                    "status": "active",
                    "status_basis": "sources table",
                    "map": "yes",
                    "map_category": "collected",
                }
            )
        import_registry(str(path))
        record(editor, _review("replica", note="e-edition only"))
        import_registry(str(path))
        o = Outlet.objects.get(pk="o-weston")
        assert (o.status, o.category) == ("replica", "replica")
        assert o.status_basis == "e-edition only"

    def test_a_retracted_review_gives_back_the_file(self, editor, tmp_path):
        from visuals.outlets import import_registry

        path = tmp_path / "registry.csv"
        cols = ["outlet_id", "outlet", "status", "status_basis", "map", "map_category"]
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerow(
                {
                    "outlet_id": "o-weston",
                    "outlet": "Weston Chronicle",
                    "status": "active",
                    "status_basis": "sources table",
                    "map": "yes",
                    "map_category": "collected",
                }
            )
        import_registry(str(path))
        review = record(editor, _review("print"))
        record(editor, {"note": "it has a website"}, retracts=review)
        o = Outlet.objects.get(pk="o-weston")
        assert (o.status, o.category, o.on_map) == ("active", "collected", True)
        assert o.status_basis == "sources table"

    def test_the_form_offers_the_statuses(self, editor):
        _outlet()
        client = Client()
        client.force_login(editor)
        page = client.get("/outlets/o-weston/").content.decode()
        assert 'name="new_status"' in page and "Replica or e-edition only" in page
        response = client.post("/outlets/o-weston/", _review("replica"))
        assert response.status_code == 302
        assert Outlet.objects.get(pk="o-weston").status == "replica"

    def test_the_export_carries_it(self):
        assert "new_status" in EXPORT


class TestTheRegistryIsReadFromTheBucket:
    def test_a_bucket_object_is_read(self, monkeypatch):
        from google.cloud import storage

        from visuals.outlets import DEFAULT_URL, read_registry

        asked = {}

        class Blob:
            def download_as_bytes(self):
                return "﻿outlet_id,outlet\no-1,Weston Chronicle\n".encode()

        class Bucket:
            def blob(self, name):
                asked["blob"] = name
                return Blob()

        class Client:
            def bucket(self, name):
                asked["bucket"] = name
                return Bucket()

        monkeypatch.setattr(storage, "Client", Client)
        rows = read_registry(DEFAULT_URL)
        assert rows == [{"outlet_id": "o-1", "outlet": "Weston Chronicle"}]
        assert asked == {
            "bucket": "mizzou-news-maps-data",
            "blob": "registry/mo_outlet_registry.csv",
        }
