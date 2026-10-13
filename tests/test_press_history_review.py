"""Events read from the press wait for a person before they are history.

A model read 9,856 passages of Missouri Press News, the Missouri Press
Association's magazine, and found 6,312 sales, mergers, closures, launches
and renamings. Its reading is good and not perfect: a Pennsylvania paper, a
website launch read as a founding, "the Herald" with no town. So a reading is
a candidate, and only a person's acceptance makes it an outlet event
(docs/OUTLET_EVENTS.md).
"""

import csv
import io

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from visuals.models import HistoryEventCandidate, Outlet, OutletEvent
from visuals.outlet_events import EventError
from visuals.press_history import accept, in_context, key_of, load, proposed, reject

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

PRESS = "Missouri Press News"
PAGE = "https://digital.shsmo.org/digital/collection/lhs/id/13260"

ROW = {
    "paper": "Cabool Enterprise",
    "known_title": "Cabool enterprise",
    "outlet_id": "",
    "town": "Cabool",
    "county": "Texas",
    "event_type": "ownership_change",
    "year": "1961",
    "year_basis": "stated",
    "from": "J. W. Ridgway",
    "to": "Mr. and Mrs. Joe Doe",
    "matched_to": "shsmo",
    "match": "exact",
    "names_in_press": "Cabool Enterprise",
    "mentions": "2",
    "first_reported": "1961-06",
    "last_reported": "1986-06",
    "link": PAGE,
    "page": "Page 12",
    "quote": "Mr. and Mrs. Joe Doe have purchased the Cabool Enterprise "
    "from J. W. Ridgway.",
    "passage": "CABOOL -- Mr. and Mrs. Joe Doe have purchased the Cabool "
    "En- terprise from J. W. Ridgway. Ridgway had published the weekly since 1946.",
    "known_town": "Cabool",
    "flags": "",
    "all_links": f"{PAGE} https://digital.shsmo.org/digital/collection/lhs/id/9",
}


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


def _candidate(**kw):
    row = {**ROW, **kw}
    added, _, errors = load([row], PRESS)
    assert errors == [] and added == 1
    return HistoryEventCandidate.objects.get(key=key_of(row["link"], row["quote"]))


class TestAnImportIsCandidatesOnly:
    def test_it_adds_candidates_and_no_events(self):
        c = _candidate()
        assert c.status == "pending" and c.publication == PRESS
        assert c.issue == "1961-06" and c.mentions == 2 and c.year == 1961
        assert OutletEvent.objects.count() == 0

    def test_the_same_file_twice_adds_once(self):
        assert load([ROW, ROW], PRESS)[:2] == (1, 1)
        assert load([ROW], PRESS)[:2] == (0, 1)
        assert HistoryEventCandidate.objects.count() == 1

    def test_one_bad_row_adds_nothing(self):
        bad = {**ROW, "event_type": "hired", "quote": "other", "year": "61"}
        added, _, errors = load([ROW, bad], PRESS)
        assert added == 0 and HistoryEventCandidate.objects.count() == 0
        assert any(e.startswith("line 3: event_type") for e in errors)
        assert any("year is four digits" in e for e in errors)

    def test_every_field_is_checked(self):
        _, _, errors = load([{"event_type": "closed"}], PRESS)
        assert len(errors) == 3

    def test_it_names_its_publication(self):
        assert load([ROW], " ")[2] == [
            "Name the publication the events were read from."
        ]

    def test_none_from_the_matcher_is_blank(self):
        c = _candidate(matched_to="none", match="none", known_title="", year="")
        assert c.matched_to == "" and c.match == "" and c.year is None

    def test_the_command(self, tmp_path):
        path = tmp_path / "events.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(ROW))
            w.writeheader()
            w.writerow(ROW)
        out = io.StringIO()
        call_command(
            "import_history_event_candidates", str(path), publication=PRESS, stdout=out
        )
        assert "1 added, 0 already held" in out.getvalue()
        with open(path, "a", newline="") as fh:
            csv.DictWriter(fh, fieldnames=list(ROW)).writerow({**ROW, "link": "x"})
        with pytest.raises(CommandError, match="nothing added"):
            call_command(
                "import_history_event_candidates", str(path), publication=PRESS
            )


class TestTheQuoteIsReadInItsPassage:
    """A quote alone was not enough to judge a reading by: "He later
    purchased the Perry County Republic" says nothing of when, or of whom."""

    def test_the_quote_is_marked_word_for_word(self):
        before, said, after = in_context(
            _candidate(quote="purchased the Cabool Enterprise from J. W. Ridgway")
        )
        assert before == "CABOOL -- Mr. and Mrs. Joe Doe have "
        assert said == "purchased the Cabool En- terprise from J. W. Ridgway"
        assert after.startswith(". Ridgway had published")

    def test_a_quote_through_ocr_noise_leaves_the_passage_whole(self):
        c = _candidate(quote="Mr. and Mrs. Joe Doe have bought the Cabool Enterprise.")
        assert in_context(c) == (c.passage, "", "")

    def test_no_passage_no_quote(self):
        assert in_context(_candidate(passage="", quote="..."))[1] == ""

    def test_the_queue_shows_the_passage(self, editor):
        _candidate()
        client = Client()
        client.force_login(editor)
        page = client.get("/outlets/press/").content.decode()
        assert "Ridgway had published the weekly since 1946" in page
        assert (
            "<mark>Mr. and Mrs. Joe Doe have purchased the Cabool En- terprise" in page
        )


class TestWhatACandidateProposes:
    def test_a_stated_year_is_that_year(self):
        p = proposed(_candidate())
        assert (p["event"], p["effective_date"], p["date_precision"]) == (
            "sold",
            "1961-01-01",
            "year",
        )
        assert p["outlet_name"] == "Cabool Enterprise" and p["outlet_id"] == ""
        assert p["note"] == (
            "Mr. and Mrs. Joe Doe bought the Cabool Enterprise from J. W. Ridgway "
            'in 1961. Missouri Press News, 1961-06, Page 12: "CABOOL -- Mr. and '
            'Mrs. Joe Doe have purchased the Cabool En- terprise from J. W. Ridgway."'
        )

    def test_a_first_report_is_on_or_before_its_issue(self):
        p = proposed(
            _candidate(year="1960", year_basis="first report", first_reported="1960-02")
        )
        assert (p["effective_date"], p["date_precision"]) == ("1960-02-29", "by")

    def test_a_merger_names_what_absorbed_it(self):
        p = proposed(_candidate(event_type="merger", to="Houston Herald"))
        assert (p["event"], p["merged_into"], p["to_owner"]) == (
            "merged",
            "Houston Herald",
            "",
        )

    def test_a_renaming_names_the_new_name(self):
        p = proposed(_candidate(event_type="renamed", to="Cabool Enterprise-News"))
        assert (p["event"], p["new_name"], p["to_owner"]) == (
            "renamed",
            "Cabool Enterprise-News",
            "",
        )

    def test_a_lease_is_an_owner_change_that_says_so(self):
        p = proposed(_candidate(event_type="lease"))
        assert p["event"] == "owner_changed"
        assert p["note"].startswith("Mr. and Mrs. Joe Doe leased the Cabool Enterprise")

    def test_a_move_has_no_event_of_its_own(self):
        assert proposed(_candidate(event_type="moved"))["event"] == ""

    def test_a_registry_match_is_that_outlet(self):
        p = proposed(
            _candidate(
                matched_to="registry",
                outlet_id="o-cabool",
                known_title="The Cabool Enterprise",
            )
        )
        assert (p["outlet_id"], p["outlet_name"]) == (
            "o-cabool",
            "The Cabool Enterprise",
        )


class TestAcceptingRecordsTheEvent:
    def test_the_page_is_the_evidence(self, editor):
        c = _candidate()
        event = accept(editor, c.pk, {"evidence_url": "https://example.org/other"})
        assert (
            event.evidence_url == PAGE and event.origin == "Missouri Press News review"
        )
        assert not event.sets_current
        c.refresh_from_db()
        assert (
            c.status == "accepted"
            and c.outlet_event == event
            and c.reviewed_by == editor
        )
        assert (
            AuditLogEntry.objects.filter(action="history_candidate:accept").count() == 1
        )

    def test_the_reviewer_corrects_the_reading(self, editor):
        c = _candidate()
        event = accept(
            editor,
            c.pk,
            {"to_owner": "Joe and Jane Doe", "outlet_name": "Cabool Enterprise"},
        )
        assert event.to_owner == "Joe and Jane Doe"

    def test_history_leaves_the_registry_as_it_stands(self, editor):
        Outlet.objects.create(
            outlet_id="o-cabool",
            name="Cabool Enterprise",
            owner="Today's owner",
            row={"owner": "Today's owner"},
        )
        c = _candidate(matched_to="registry", outlet_id="o-cabool")
        accept(editor, c.pk, {})
        assert Outlet.objects.get(outlet_id="o-cabool").owner == "Today's owner"

    def test_decided_once(self, editor):
        c = _candidate()
        accept(editor, c.pk, {})
        with pytest.raises(EventError, match="already accepted"):
            accept(editor, c.pk, {})
        with pytest.raises(EventError, match="already accepted"):
            reject(editor, c.pk, "changed my mind")
        assert OutletEvent.objects.count() == 1

    def test_a_move_needs_the_reviewer_to_say_what_it_is(self, editor):
        c = _candidate(event_type="moved")
        with pytest.raises(EventError, match="a move has no event"):
            accept(editor, c.pk, {})
        c.refresh_from_db()
        assert c.status == "pending"

    def test_an_event_that_is_not_one_records_nothing(self, editor):
        c = _candidate(event_type="merger", to="")
        with pytest.raises(EventError, match="merged_into"):
            accept(editor, c.pk, {})
        c.refresh_from_db()
        assert c.status == "pending" and OutletEvent.objects.count() == 0

    def test_no_such_candidate(self, editor):
        with pytest.raises(EventError, match="No such candidate"):
            accept(editor, 999, {})


class TestRejecting:
    def test_it_says_why_and_records_nothing(self, editor):
        c = _candidate()
        with pytest.raises(EventError, match="why"):
            reject(editor, c.pk, "  ")
        reject(editor, c.pk, "a Kansas paper")
        c.refresh_from_db()
        assert (c.status, c.reason, c.outlet_event) == (
            "rejected",
            "a Kansas paper",
            None,
        )
        assert OutletEvent.objects.count() == 0
        assert (
            AuditLogEntry.objects.filter(action="history_candidate:reject").count() == 1
        )


class TestTheQueue:
    def test_a_viewer_reads_but_does_not_decide(self, viewer):
        c = _candidate()
        client = Client()
        client.force_login(viewer)
        page = client.get("/outlets/press/")
        assert page.status_code == 200
        assert (
            b"Cabool Enterprise" in page.content
            and b"Accept and record" not in page.content
        )
        assert (
            client.post(
                f"/outlets/press/{c.pk}/", {"decision": "reject", "reason": "x"}
            ).status_code
            == 403
        )

    def test_an_editor_accepts_and_returns_to_the_queue(self, editor):
        c = _candidate()
        client = Client()
        client.force_login(editor)
        assert b"Accept and record" in client.get("/outlets/press/").content
        r = client.post(
            f"/outlets/press/{c.pk}/",
            {
                **proposed(c),
                "decision": "accept",
                "next": "/outlets/press/?event_type=ownership_change",
            },
        )
        assert (
            r.status_code == 302
            and r["Location"] == "/outlets/press/?event_type=ownership_change"
        )
        assert OutletEvent.objects.get().outlet_name == "Cabool Enterprise"
        assert b"Cabool" not in client.get("/outlets/press/").content
        assert b"recorded as" in client.get("/outlets/press/?status=accepted").content

    def test_a_refusal_shows_why_and_keeps_what_was_typed(self, editor):
        c = _candidate(event_type="merger", to="")
        client = Client()
        client.force_login(editor)
        r = client.post(
            f"/outlets/press/{c.pk}/",
            {**proposed(c), "decision": "accept", "note": "typed"},
        )
        assert (
            r.status_code == 400
            and b"merged_into" in r.content
            and b"typed" in r.content
        )

    def test_it_goes_back_only_to_this_site(self, editor):
        c = _candidate()
        client = Client()
        client.force_login(editor)
        r = client.post(
            f"/outlets/press/{c.pk}/",
            {"decision": "reject", "reason": "x", "next": "https://evil.example/"},
        )
        assert r["Location"] == "/outlets/press/"

    def test_placed_papers_come_first(self, editor):
        _candidate(county="", quote="An unplaced reading.")
        _candidate(county="Adair", quote="A placed reading.")
        client = Client()
        client.force_login(editor)
        page = client.get("/outlets/press/")
        assert [c.county for c in page.context["page"]] == ["Adair", ""]
        assert b"<strong>Sale</strong>" in page.content

    def test_the_press_gives_the_town_the_match_another(self, editor):
        _candidate(
            town="Kirksville",
            known_town="Brashear",
            known_title="Kirksville daily news",
        )
        client = Client()
        client.force_login(editor)
        assert (
            b"Kirksville daily news, Brashear" in client.get("/outlets/press/").content
        )

    def test_a_get_goes_to_the_queue(self, editor):
        client = Client()
        client.force_login(editor)
        assert client.get("/outlets/press/1/")["Location"] == "/outlets/press/"

    def test_an_error_for_a_missing_candidate(self, editor):
        client = Client()
        client.force_login(editor)
        assert (
            client.post(
                "/outlets/press/999/", {"decision": "reject", "reason": "x"}
            ).status_code
            == 404
        )

    @pytest.mark.parametrize(
        "query, found",
        [
            ("", 2),
            ("?status=all", 3),
            ("?status=rejected", 1),
            ("?event_type=closed", 1),
            ("?flag=none", 1),
            ("?flag=a+website", 1),
            ("?matched=none", 1),
            ("?matched=shsmo", 1),
            ("?county=texas", 1),
            ("?q=Houston", 1),
        ],
    )
    def test_the_filters(self, editor, query, found):
        _candidate()
        _candidate(
            paper="Houston Herald",
            town="Houston",
            county="",
            event_type="closed",
            quote="The Herald closed.",
            matched_to="none",
            flags="a website, not a paper",
        )
        reject(editor, _candidate(quote="A third reading.").pk, "duplicate")
        client = Client()
        client.force_login(editor)
        page = client.get(f"/outlets/press/{query}")
        assert page.context["page"].paginator.count == found


class TestTheNoteSaysWhatAndWhen:
    """A note that quoted only "He and Walter Ridgway purchased the
    Kirksville Daily Express that year" did not say which year, or that it
    was a sale to the two of them."""

    PASSAGE = (
        "E. E. SWAIN, SR. Edward Everett Swain, Sr., publisher, died. Swain, 89, "
        "had worked for several newspapers before moving to Kirksville in 1909. "
        "He and Walter Ridgway purchased the Kirksville Daily Express that year "
        "and Swain bought complete interest in 1911. He was a past president."
    )

    def _note(self, **kw):
        row = {
            "paper": "Kirksville Daily Express",
            "event_type": "ownership_change",
            "year": "1909",
            "year_basis": "stated",
            "from": "",
            "to": "E. E. Swain, Sr. and Walter Ridgway",
            "quote": "He and Walter Ridgway purchased the Kirksville Daily Express "
            "that year",
            "passage": self.PASSAGE,
            "first_reported": "1972-03",
            "page": "Page 21",
            **kw,
        }
        return proposed(_candidate(**row))["note"]

    def test_the_year_comes_from_the_sentence_before(self):
        assert self._note() == (
            "E. E. Swain, Sr. and Walter Ridgway bought the Kirksville Daily Express "
            'in 1909. Missouri Press News, 1972-03, Page 21: "Swain, 89, had worked '
            "for several newspapers before moving to Kirksville in 1909. He and "
            "Walter Ridgway purchased the Kirksville Daily Express that year and "
            'Swain bought complete interest in 1911."'
        )

    def test_initials_do_not_end_a_sentence(self):
        note = self._note(
            quote="Edward Everett Swain, Sr., publisher, died", year="1972"
        )
        assert '"E. E. SWAIN, SR. Edward Everett Swain, Sr., publisher, died."' in note

    def test_a_year_the_quote_states_needs_no_more(self):
        note = self._note(quote="Swain bought complete interest in 1911", year="1911")
        assert note.endswith(
            ': "He and Walter Ridgway purchased the Kirksville Daily Express that '
            'year and Swain bought complete interest in 1911."'
        )

    def test_a_quote_not_in_the_passage_is_quoted_as_given(self):
        assert self._note(quote="Swain and Ridgway bought it").endswith(
            '"Swain and Ridgway bought it"'
        )

    def test_a_first_report_is_by_its_issue(self):
        assert self._note(year_basis="first report").startswith(
            "E. E. Swain, Sr. and Walter Ridgway bought the Kirksville Daily Express "
            "by March 1972."
        )

    @pytest.mark.parametrize(
        "kind, to, says",
        [
            (
                "merger",
                "Kirksville Journal",
                "The Kirksville Daily Express merged with "
                "Kirksville Journal in 1909.",
            ),
            (
                "founded",
                "Swain",
                "The Kirksville Daily Express was founded by Swain in 1909.",
            ),
            ("founded", "", "The Kirksville Daily Express was founded in 1909."),
            ("closed", "", "The Kirksville Daily Express closed in 1909."),
            (
                "renamed",
                "Daily Express-News",
                "The Kirksville Daily Express was renamed "
                "Daily Express-News in 1909.",
            ),
            ("renamed", "", "Renaming: the Kirksville Daily Express in 1909."),
            ("moved", "", "Move: the Kirksville Daily Express in 1909."),
        ],
    )
    def test_each_kind_says_what_happened(self, kind, to, says):
        assert self._note(event_type=kind, to=to).startswith(says)
