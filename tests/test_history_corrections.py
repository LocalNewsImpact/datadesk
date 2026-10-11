"""Reviewer corrections to the newspaper history's catalogues live in a table.

On 2026-10-10 the history's corrections were constants in the build's code, so
"the Columbia Missourian renamed itself the Missourian" took a pull request;
then they were a CSV in a bucket, with no history, no check on a row and no
form. These are the table that holds them, and the files the build reads,
written from it (docs/NEWSPAPER_HISTORY.md).
"""

import csv
import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from visuals.history_corrections import FIELDS, export, live, load
from visuals.models import HistoryCorrection, Outlet, OutletEvent

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

H = HistoryCorrection

LINK = {
    "kind": "link",
    "title": "Columbia Missourian",
    "county": "Boone",
    "other_title": "Missourian",
    "other_county": "Boone",
    "evidence": "Renamed the Missourian in 2003; film runs on",
}


@pytest.fixture
def reviewer():
    return User.objects.create_user("reviewer", email="reviewer@localnewsimpact.org")


def _row(**kw):
    return dict.fromkeys(FIELDS, "") | kw


def _write(tmp_path, rows):
    path = tmp_path / "corrections.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def test_a_backfill_records_each_correction_once(reviewer, tmp_path):
    path = _write(
        tmp_path,
        [
            _row(**LINK),
            _row(kind="drop", title="X", county="Ralls", evidence="same film"),
        ],
    )
    assert load(
        reviewer,
        [
            _row(**LINK),
            _row(kind="drop", title="X", county="Ralls", evidence="same film"),
        ],
    ) == (2, 0, [])
    call_command("import_history_corrections", str(path), by=reviewer.email)
    assert H.objects.count() == 2
    c = H.objects.get(kind="link")
    assert c.recorded_by == reviewer and c.origin == "backfill"


@pytest.mark.parametrize(
    "row, says",
    [
        (
            _row(kind="link", title="A", county="Boone", evidence="e"),
            "needs other_title",
        ),
        (_row(**{**LINK, "evidence": ""}), "evidence"),
        (_row(kind="rename", title="A", evidence="e"), "kind of correction"),
        (
            _row(
                kind="set", county="Clay", field="county", value="Cedar", evidence="e"
            ),
            "a title or a town",
        ),
        (
            _row(
                kind="set",
                title="A",
                county="Clay",
                field="owner",
                value="x",
                evidence="e",
            ),
            "changes one of",
        ),
        (
            _row(
                kind="set",
                title="A",
                county="Clay",
                field="start_year",
                value="197?",
                evidence="e",
            ),
            "start_year",
        ),
        (
            _row(kind="moved", title="A", value="Parma", evidence="e"),
            "two or more towns",
        ),
        (
            _row(
                kind="print_ended", title="A", field="sold", value="2024", evidence="e"
            ),
            "merged or closed",
        ),
        (
            _row(
                kind="print_ended",
                title="A",
                field="closed",
                value="soon",
                evidence="e",
            ),
            "in a year",
        ),
        (
            _row(
                kind="reels_belong_to",
                title="A",
                county="T",
                other_title="B",
                other_county="T",
                field="town",
                value="x",
                evidence="e",
            ),
            "end_year",
        ),
    ],
)
def test_a_bad_row_writes_nothing_and_says_why(reviewer, row, says):
    recorded, skipped, errors = load(reviewer, [_row(**LINK), row])
    assert (recorded, skipped) == (0, 0)
    assert errors and errors[0].startswith("line 3:") and says in errors[0]
    assert not H.objects.exists()


def test_a_bad_file_stops_the_command(reviewer, tmp_path):
    path = _write(
        tmp_path, [_row(kind="link", title="A", county="Boone", evidence="e")]
    )
    with pytest.raises(CommandError, match="nothing recorded"):
        call_command("import_history_corrections", str(path), by=reviewer.email)
    with pytest.raises(CommandError, match="No user"):
        call_command("import_history_corrections", str(path), by="nobody@example.org")


def test_a_correction_is_never_edited_or_deleted_only_retracted(reviewer):
    load(reviewer, [_row(**LINK)])
    c = H.objects.get()
    c.title = "Other"
    with pytest.raises(ValidationError):
        c.save()
    with pytest.raises(ValidationError):
        c.delete()
    bare = H(kind="retraction", evidence="wrong", recorded_by=reviewer)
    with pytest.raises(ValidationError, match="names the correction"):
        bare.full_clean()
    H.objects.create(
        kind="retraction", retracts=c, evidence="wrong paper", recorded_by=reviewer
    )
    assert live() == []
    # withdrawn, the same correction can be recorded again
    assert load(reviewer, [_row(**LINK)]) == (1, 0, [])


def test_the_build_reads_files_written_from_the_tables(reviewer, tmp_path):
    load(reviewer, [_row(**LINK)])
    Outlet.objects.create(
        outlet_id="o-sdc",
        name="Springfield Daily Citizen",
        city="Springfield",
        county="Greene",
    )
    for oid, name, event, extra in (
        ("o-sdc", "Springfield Daily Citizen", "launched", {}),
        (
            "o-monett",
            "The Monett Times",
            "merged",
            {"merged_into": "lawrencecountyrecord.com"},
        ),
        (
            "o-rch",
            "Ralls County Herald-Enterprise",
            "status",
            {"new_status": "merged", "merged_into": "monroe-ralls.com"},
        ),
        ("o-x", "Some Weekly", "status", {"new_status": "print"}),
        ("o-y", "Undated Launch", "launched", {"effective_date": None}),
    ):
        OutletEvent.objects.create(
            outlet_id=oid,
            outlet_name=name,
            event=event,
            effective_date=extra.pop("effective_date", dt.date(2022, 1, 1)),
            date_precision="year",
            note="its own notes",
            recorded_by=reviewer,
            **extra,
        )
    call_command("export_newspaper_history_inputs", str(tmp_path))

    def rows(name):
        with open(tmp_path / name) as fh:
            return list(csv.DictReader(fh))

    assert rows("reviewer_corrections.csv") == [_row(**LINK)]
    assert rows("registry_founded.csv") == [
        {
            "outlet": "Springfield Daily Citizen",
            "city": "Springfield",
            "county": "Greene",
            "founded_year": "2022",
            "source_for_dates": "its own notes",
        }
    ]
    assert [r["outlet_name"] for r in rows("datadesk_merged_events.csv")] == [
        "The Monett Times",
        "Ralls County Herald-Enterprise",
    ]
    # a retracted merger leaves the file
    OutletEvent.objects.create(
        outlet_id="o-monett",
        outlet_name="The Monett Times",
        event="retraction",
        retracts=OutletEvent.objects.get(outlet_id="o-monett"),
        note="wrong",
        recorded_by=reviewer,
    )
    assert export(tmp_path)["datadesk_merged_events.csv"] == 1


def test_the_admin_adds_but_never_changes_a_correction(reviewer, client):
    admin_user = User.objects.create_superuser(
        "boss", email="boss@localnewsimpact.org", password="pw"
    )
    client.force_login(admin_user)
    r = client.post(
        "/admin/visuals/historycorrection/add/", {**LINK, "evidence_url": ""}
    )
    assert r.status_code == 302, r.content.decode()[:500]
    c = H.objects.get()
    assert c.recorded_by == admin_user and c.origin == "admin"
    r = client.post(
        "/admin/visuals/historycorrection/add/",
        {**LINK, "other_title": "", "evidence_url": ""},
    )
    assert r.status_code == 200 and "needs other_title" in r.content.decode()
    r = client.post(
        f"/admin/visuals/historycorrection/{c.pk}/change/", {**LINK, "title": "X"}
    )
    assert r.status_code == 403
    c.refresh_from_db()
    assert c.title == "Columbia Missourian"
    assert (
        str(c) == "One title carried on as another: Columbia Missourian -> Missourian"
    )
