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
