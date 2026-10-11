"""Broadcast stations whose news we collect: one row per FCC facility, its
first air date and its call letters over the years (docs/NEWSPAPER_HISTORY.md).

Call letters move between stations -- KMOX-TV was today's KMOV, and before
that the permit that became KPLR -- so a station is its facility id.
"""

import csv
import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError

from visuals.broadcast import (
    FIELDS,
    load,
    parse_call_signs,
    parse_date,
    rows_for_export,
)
from visuals.history_corrections import export
from visuals.models import BroadcastStation, CallSign

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

KMBZ = {
    "facility_id": "6382",
    "service": "AM",
    "city": "Kansas City",
    "state": "MO",
    "outlet_id": "o-kmbz",
    "first_air_date": "1922-04-05",
    "call_signs": "WPE 1922-1923; KFIX 1923-1925; KMBZ 1967-",
    "evidence": "Wikipedia; FCC CDBS facility file",
    "evidence_url": "https://en.wikipedia.org/wiki/KMBZ_(AM)",
    "note": "",
}


@pytest.fixture
def reviewer():
    return User.objects.create_user("reviewer", email="reviewer@localnewsimpact.org")


@pytest.mark.parametrize(
    "text, want",
    [
        ("1953-12-21", (dt.date(1953, 12, 21), "day")),
        ("1971-02", (dt.date(1971, 2, 1), "month")),
        ("1948", (dt.date(1948, 1, 1), "year")),
        ("", (None, "")),
    ],
)
def test_a_first_air_date_keeps_its_precision(text, want):
    assert parse_date(text) == want


def test_a_bad_date_or_call_sign_list_is_refused():
    with pytest.raises(ValueError, match="1953-12-21"):
        parse_date("Dec. 1953")
    with pytest.raises(ValueError, match="'KMIZ 1985-'"):
        parse_call_signs("KMIZ")
    with pytest.raises(ValueError, match="Exactly one"):
        parse_call_signs("KCBJ-TV 1971-1985")
    assert parse_call_signs("KCBJ-TV 1971-1985; KMIZ 1985-") == [
        ("KCBJ-TV", 1971, 1985),
        ("KMIZ", 1985, None),
    ]


def test_a_sheet_records_stations_and_loading_it_again_updates_them(reviewer, tmp_path):
    path = tmp_path / "stations.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        w.writerow(KMBZ)
    call_command("import_broadcast_stations", str(path), by=reviewer.email)
    s = BroadcastStation.objects.get()
    assert (s.first_air_date, s.date_precision) == (dt.date(1922, 4, 5), "day")
    assert s.call_sign == "KMBZ" and str(s) == "KMBZ (Kansas City)"
    assert [c.call_sign for c in s.call_signs.all()] == ["WPE", "KFIX", "KMBZ"]
    assert load(reviewer, [{**KMBZ, "call_signs": "KMBZ 1967-"}]) == (1, [])
    assert BroadcastStation.objects.count() == 1
    assert [str(c) for c in s.call_signs.all()] == ["KMBZ (1967-)"]


def test_a_bad_sheet_writes_nothing(reviewer, tmp_path):
    rows = [KMBZ, {**KMBZ, "facility_id": "", "service": "SW"}]
    written, errors = load(reviewer, rows)
    assert written == 0 and errors[0].startswith("line 3:")
    assert "needs facility_id" in errors[0] and "service is one of" in errors[0]
    assert not BroadcastStation.objects.exists()
    path = tmp_path / "bad.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(CommandError, match="nothing recorded"):
        call_command("import_broadcast_stations", str(path), by=reviewer.email)
    with pytest.raises(CommandError, match="No user"):
        call_command("import_broadcast_stations", str(path), by="nobody@example.org")


def test_a_station_has_one_current_call_sign(reviewer):
    load(reviewer, [KMBZ])
    s = BroadcastStation.objects.get()
    with pytest.raises(IntegrityError):
        CallSign.objects.create(station=s, call_sign="KXXX", start_year=2000)


def test_the_build_reads_the_stations_with_their_dates_as_recorded(reviewer, tmp_path):
    load(
        reviewer,
        [
            KMBZ,
            {
                **KMBZ,
                "facility_id": "50744",
                "first_air_date": "1971-02",
                "call_signs": "KMRN 1971-",
            },
            {
                **KMBZ,
                "facility_id": "35531",
                "first_air_date": "1948",
                "call_signs": "KREI 1948-",
            },
            {
                **KMBZ,
                "facility_id": "35533",
                "first_air_date": "",
                "call_signs": "KTJJ -",
            },
        ],
    )
    dates = {r["facility_id"]: r["first_air_date"] for r in rows_for_export()}
    assert dates == {
        "6382": "1922-04-05",
        "50744": "1971-02",
        "35531": "1948",
        "35533": "",
    }
    assert export(tmp_path)["broadcast_stations.csv"] == 4
    with open(tmp_path / "broadcast_stations.csv") as fh:
        got = list(csv.DictReader(fh))
    assert got[-1]["call_signs"] == "WPE 1922-1923; KFIX 1923-1925; KMBZ 1967-"
    assert BroadcastStation.objects.get(facility_id="35533").call_sign == "KTJJ"
    orphan = BroadcastStation.objects.create(
        facility_id="1", service="AM", city="X", outlet_id="o", evidence="e"
    )
    assert orphan.call_sign == "facility 1"
