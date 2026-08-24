"""The proposal queue: grouped by record, decided in a session, applied
as one audited batch."""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from explorer.models import Source
from review.proposals import ChangeProposal

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/proposals/"


@pytest.fixture
def editor(client):
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.fixture
def publisher(crawler_schema):
    return Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Tribune",
        city="Columbia",
        county="Boone",
        owner="",
    )


def _proposal(publisher, field, current, proposed, **kwargs):
    return ChangeProposal.objects.create(
        target="sources",
        record_id=publisher.id,
        record_label=publisher.host_norm,
        origin="test sheet",
        field=field,
        current_value=current,
        proposed_value=proposed,
        flag=kwargs.pop("flag", "value_disputed"),
        **kwargs,
    )


def test_one_record_is_one_card(client, editor, publisher):
    """Four fields on one publisher are one decision, not four rows in a
    file the reviewer has to reassemble."""
    for field, value in (
        ("owner", "CherryRoad Media"),
        ("city", "Columbia"),
        ("canonical_name", "Columbia Tribune"),
    ):
        _proposal(publisher, field, "", value)
    content = client.get(URL).content.decode()
    assert content.count('class="rec"') == 1
    assert content.count('class="prop"') == 3


def test_submitting_applies_accepted_and_leaves_rejected(client, editor, publisher):
    accept = _proposal(publisher, "owner", "", "CherryRoad Media")
    reject = _proposal(publisher, "canonical_name", "Tribune", "The Tribune")
    client.post(URL, {f"d-{accept.pk}": "accept", f"d-{reject.pk}": "reject"})

    publisher.refresh_from_db()
    assert publisher.owner == "CherryRoad Media"
    assert publisher.canonical_name == "Tribune"
    accept.refresh_from_db()
    reject.refresh_from_db()
    assert accept.state == ChangeProposal.ACCEPTED
    assert reject.state == ChangeProposal.REJECTED
    assert accept.decided_by == editor
    entry = AuditLogEntry.objects.get(action="proposal:apply")
    assert entry.before == {"s1": {"owner": ""}}
    assert accept.audit_entry == entry


def test_a_fix_writes_the_reviewers_value(client, editor, publisher):
    p = _proposal(publisher, "city", "Columbia", "Colombia")
    client.post(URL, {f"d-{p.pk}": "fix", f"v-{p.pk}": "Columbia Heights"})
    publisher.refresh_from_db()
    assert publisher.city == "Columbia Heights"
    p.refresh_from_db()
    assert p.state == ChangeProposal.FIXED
    assert p.final_value == "Columbia Heights"


def test_a_fix_without_a_value_stays_pending(client, editor, publisher):
    p = _proposal(publisher, "city", "Columbia", "Colombia")
    client.post(URL, {f"d-{p.pk}": "fix", f"v-{p.pk}": "   "})
    p.refresh_from_db()
    assert p.state == ChangeProposal.PENDING
    publisher.refresh_from_db()
    assert publisher.city == "Columbia"


def test_a_file_offering_two_values_is_still_decidable(client, editor, publisher):
    """Not knowing which value to propose is ordinary — the proposed
    column is simply empty. It is not a reason to withhold the question:
    the person reading the record usually knows which value is meant,
    and Keep and Fix always apply."""
    dupe = _proposal(
        publisher,
        "owner",
        "",
        "",
        flag="evidence_conflict",
        detail=(
            "Sources sheet gives more than one value here — "
            "\u201cGannett\u201d and \u201cLee Enterprises\u201d — "
            "so neither is proposed; the record has no value"
        ),
    )
    assert dupe.actionable is True
    content = client.get(URL).content.decode()
    assert "Not offered for a decision" not in content
    assert "more than one value here" in content
    assert f"d-{dupe.pk}" in content  # it carries a decision control


def test_viewers_cannot_reach_the_queue(client, publisher):
    user = User.objects.create_user("viewer", email="v@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    assert client.get(URL).status_code == 403


def test_a_rejected_value_is_not_the_ordinary_path(client, editor, publisher):
    """The record says Rock Port and a file says Rockport, which is not
    a Missouri place. The row must say so where the reviewer reads it."""
    p = _proposal(
        publisher,
        "city",
        "Rock Port",
        "Rockport",
        flag="city_unknown",
        detail="Rockport is not a Missouri place",
        suggested_value="Rock Port",
        suggestion="the gazetteer spells it Rock Port",
    )
    content = client.get(URL).content.decode()
    assert "Accept Proposal" in content
    assert p.detail in content
    assert "Rock Port" in content
    # The gazetteer's spelling is what the record already holds, so Keep
    # is the answer; nothing repeats it.
    assert p.useful_suggestion == ""


def test_the_checked_value_is_offered_when_it_differs_from_both(
    client, editor, publisher
):
    p = _proposal(
        publisher,
        "city",
        "Rockport",
        "Rockpot",
        flag="city_unknown",
        detail="Rockpot is not a Missouri place",
        suggested_value="Rock Port",
    )
    assert p.useful_suggestion == "Rock Port"
    content = client.get(URL).content.decode()
    # Offered as the field's placeholder, not a button that duplicates
    # what Accept or Keep already do.
    assert 'placeholder="Rock Port"' in content


def test_a_row_without_a_rejected_value_reads_the_same(client, editor, publisher):
    _proposal(publisher, "owner", "", "CherryRoad Media", flag="owner_missing")
    content = client.get(URL).content.decode()
    assert "Accept Proposal" in content


def test_a_proposal_that_changes_nothing_is_not_a_question(client, editor, publisher):
    """A sheet spelling that resolves to what is already recorded is
    agreement, not a change: "Writing X over X" is not a decision."""
    Source.objects.filter(id="s1").update(owner="CherryRoad Media")
    _proposal(publisher, "owner", "CherryRoad Media", "CherryRoad Media")
    real = _proposal(publisher, "city", "Columbia", "Columbia Heights")
    content = client.get(URL).content.decode()
    assert content.count('class="prop"') == 1
    assert f'data-id="{real.pk}"' in content


def test_a_suggestion_is_only_offered_when_it_is_a_third_option(
    client, editor, publisher
):
    """A suggestion equal to what is recorded is what Keep already does;
    showing it again puts the same value on screen twice."""
    same_as_current = _proposal(
        publisher,
        "canonical_name",
        "KMBZ",
        "KFTK",
        flag="owner_unknown",
        suggested_value="KMBZ",
    )
    assert same_as_current.useful_suggestion == ""
    content = client.get(URL).content.decode()
    assert 'placeholder="another value"' in content


def test_the_three_columns_share_one_vocabulary(client, editor, publisher):
    """Column, button and hint have to read as one instruction."""
    _proposal(publisher, "city", "Columbia", "Ashland")
    content = client.get(URL).content.decode()
    for phrase in (
        "Proposed change",
        "Current text",
        "Something else",
        "Accept Proposal",
        "Update it",
        ">Keep<",
        "No Change",
        ">Fix<",
        "Use this",
    ):
        assert phrase in content, phrase
    for gone in ("Accept anyway", "overrule the check", "Leave as is"):
        assert gone not in content, gone


# --- what a session does and does not carry ---------------------------------


def test_deciding_some_leaves_the_rest_in_the_queue(client, editor, publisher):
    """A reviewer works ten and submits; the rest stay for next time."""
    decided = [
        _proposal(publisher, "city", "Columbia", "Ashland"),
        _proposal(publisher, "owner", "", "CherryRoad Media"),
    ]
    untouched = _proposal(publisher, "canonical_name", "Tribune", "The Tribune")

    client.post(
        URL,
        {f"d-{decided[0].pk}": "accept", f"d-{decided[1].pk}": "reject"},
    )

    untouched.refresh_from_db()
    assert untouched.state == ChangeProposal.PENDING
    assert f'data-id="{untouched.pk}"' in client.get(URL).content.decode()
    for p in decided:
        p.refresh_from_db()
        assert p.state != ChangeProposal.PENDING


def test_a_fix_with_nothing_typed_does_not_block_the_others(client, editor, publisher):
    """An abandoned fix stays in the queue; the decisions around it go
    through rather than being held hostage to it."""
    started = _proposal(publisher, "city", "Columbia", "Ashland")
    finished = _proposal(publisher, "owner", "", "CherryRoad Media")

    client.post(
        URL,
        {
            f"d-{started.pk}": "fix",
            f"v-{started.pk}": "  ",
            f"d-{finished.pk}": "accept",
        },
    )

    started.refresh_from_db()
    finished.refresh_from_db()
    assert started.state == ChangeProposal.PENDING
    assert finished.state == ChangeProposal.ACCEPTED
    publisher.refresh_from_db()
    assert publisher.owner == "CherryRoad Media"
    assert publisher.city == "Columbia"


def test_two_pending_rows_for_one_field_ask_once(client, editor, publisher):
    _proposal(publisher, "city", "Columbia", "Ashland")
    _proposal(publisher, "city", "Columbia", "Ashland")
    assert ChangeProposal.objects.filter(field="city").count() == 2
    assert client.get(URL).content.decode().count('class="prop"') == 1


def test_a_decision_records_who_made_it_and_when(client, editor, publisher):
    p = _proposal(publisher, "city", "Columbia", "Ashland")
    client.post(URL, {f"d-{p.pk}": "accept"})
    p.refresh_from_db()
    assert p.decided_by == editor
    assert p.decided_at is not None
    assert p.audit_entry is not None
    assert p.audit_entry.actor == editor


def test_decided_rows_leave_the_pending_queue(client, editor, publisher):
    p = _proposal(publisher, "city", "Columbia", "Ashland")
    client.post(URL, {f"d-{p.pk}": "accept"})
    assert f'data-id="{p.pk}"' not in client.get(URL).content.decode()
    # Still findable when asked for explicitly.
    assert f'data-id="{p.pk}"' in client.get(URL + "?state=all").content.decode()


# --- the scan finds defects in the corpus, not in a file ---------------------


def test_the_scan_flags_records_no_file_mentions(crawler_schema, editor):
    """Coverage is the point: a publisher nobody wrote a spreadsheet row
    for still has to surface if something is wrong with it (REVIEW.md)."""
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    broken = Source.objects.create(
        id="s9",
        host="quiet.example",
        host_norm="quiet.example",
        canonical_name="Quiet Weekly",
        city="Columbia",
        county="",
        owner="",
    )
    DatasetSource.objects.create(id="ds9", dataset=dataset, source=broken)

    call_command("scan_sources", dataset="mo")
    flags = set(
        ChangeProposal.objects.filter(record_id="s9").values_list("flag", flat=True)
    )
    assert "county_missing" in flags
    assert "owner_missing" in flags


def test_the_scan_does_not_queue_a_record_with_nothing_wrong(crawler_schema, editor):
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    fine = Source.objects.create(
        id="s10",
        host="ok.example",
        host_norm="ok.example",
        canonical_name="The Columbia Example",
        city="Columbia",
        county="Boone",
        owner="CherryRoad Media",
        # Its own state, not the dataset's. A record carrying none is a
        # record with something wrong -- see the tests below.
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds10", dataset=dataset, source=fine)

    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(record_id="s10").exists()


def test_the_scan_names_the_defect_not_the_check(crawler_schema, editor):
    """A county that is not a county in this state says exactly that."""
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    wrong = Source.objects.create(
        id="s11",
        host="kc.example",
        host_norm="kc.example",
        canonical_name="KC Example",
        city="Kansas City",
        county="Wyandotte",
        owner="CherryRoad Media",
    )
    DatasetSource.objects.create(id="ds11", dataset=dataset, source=wrong)

    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s11", flag="county_unknown")
    assert "Wyandotte is a county in KS, not MO" in p.detail
    assert p.flag_label == "County does not exist here"


def test_a_rescan_does_not_ask_again_about_a_decision(client, crawler_schema, editor):
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s12",
        host="x.example",
        host_norm="x.example",
        canonical_name="Example",
        city="Columbia",
        county="Boone",
        owner="",
    )
    DatasetSource.objects.create(id="ds12", dataset=dataset, source=source)

    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s12", flag="owner_missing")
    client.post(URL, {f"d-{p.pk}": "reject"})

    call_command("scan_sources", dataset="mo")
    assert (
        ChangeProposal.objects.filter(record_id="s12", flag="owner_missing").count()
        == 1
    )


def test_the_proposed_column_holds_the_value_we_believe_correct(crawler_schema, editor):
    """The record says Kirksville; a file says Kirskville, which is not a
    Missouri place. Proposing the misspelling under "Accept" would ask a
    reviewer to write a value the app has just called wrong."""
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s20",
        host="kirksville.example",
        host_norm="kirksville.example",
        canonical_name="Daily Express",
        city="Kirksville",
        county="Adair",
        owner="CherryRoad Media",
    )
    DatasetSource.objects.create(id="ds20", dataset=dataset, source=source)

    evidence = "host_norm,city\nkirksville.example,Kirskville\n"
    path = "/tmp/dd_evidence_kirks.csv"
    with open(path, "w") as fh:
        fh.write(evidence)
    call_command("scan_sources", dataset="mo", evidence=path)

    assert not ChangeProposal.objects.filter(record_id="s20", field="city").exists()


def test_a_misspelled_record_is_proposed_the_gazetteer_spelling(crawler_schema, editor):
    """When the record itself is wrong, the proposal is the correction —
    that column always holds the better value."""
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s21",
        host="vedette.example",
        host_norm="vedette.example",
        canonical_name="Vedette",
        city="Grenfield",
        county="Dade",
        owner="CherryRoad Media",
    )
    DatasetSource.objects.create(id="ds21", dataset=dataset, source=source)

    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s21", flag="city_unknown")
    assert p.current_value == "Grenfield"
    assert p.proposed_value == "Greenfield"


def test_a_defect_with_no_known_correction_offers_no_accept(crawler_schema, editor):
    """ "Jasper and Newton" names two counties; which was meant is the
    reviewer's call, so nothing is proposed and Accept is absent."""
    from django.core.management import call_command

    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s22",
        host="four.example",
        host_norm="four.example",
        canonical_name="Four States",
        city="Joplin",
        county="Jasper and Newton",
        owner="CherryRoad Media",
    )
    DatasetSource.objects.create(id="ds22", dataset=dataset, source=source)

    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s22", flag="county_multiple")
    assert p.proposed_value == ""


def _mo_publisher(**overrides):
    from explorer.models import Dataset, DatasetSource

    dataset = Dataset.objects.filter(slug="mo").first() or Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    fields = {
        "id": "s20",
        "host": "y.example",
        "host_norm": "y.example",
        "canonical_name": "Example Herald",
        "city": "Columbia",
        "county": "Boone",
        "owner": "Example Media",
    }
    fields.update(overrides)
    source = Source.objects.create(**fields)
    DatasetSource.objects.create(id=f"ds-{source.id}", dataset=dataset, source=source)
    return source


def test_a_decision_taken_before_the_flags_existed_still_counts(
    client, crawler_schema, editor
):
    """The vocabulary arrived after people had already answered questions,
    so those rows carry no flag. A rescan must not re-ask them."""
    from django.core.management import call_command

    _mo_publisher(owner="")
    ChangeProposal.objects.create(
        target="sources",
        record_id="s20",
        record_label="y.example",
        dataset="mo",
        field="owner",
        flag="",  # decided under the old model
        current_value="",
        proposed_value="Example Media",
        state=ChangeProposal.REJECTED,
    )

    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(
        record_id="s20", field="owner", state=ChangeProposal.PENDING
    ).exists()


def test_a_field_that_changes_after_a_decision_is_asked_again(
    client, crawler_schema, editor
):
    """A ruling covers the value it was made against. When the corpus
    moves on, the question is genuinely new."""
    from django.core.management import call_command

    source = _mo_publisher(owner="")
    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s20", flag="owner_missing")
    client.post(URL, {f"d-{p.pk}": "reject"})

    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(
        record_id="s20", flag="owner_missing", state=ChangeProposal.PENDING
    ).exists()

    # The crawler writes a county that does not exist in Missouri.
    Source.objects.filter(pk=source.pk).update(county="Wyandotte")
    call_command("scan_sources", dataset="mo")
    assert ChangeProposal.objects.filter(
        record_id="s20", field="county", state=ChangeProposal.PENDING
    ).exists()


def test_an_applied_decision_settles_the_value_it_wrote(client, crawler_schema, editor):
    """Accepting writes the proposal into the corpus. The rescan reads
    that new value and must recognise it as settled, not as a fresh
    defect."""
    from django.core.management import call_command

    _mo_publisher(county="")
    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s20", flag="county_missing")
    client.post(URL, {f"d-{p.pk}": "accept"})

    assert Source.objects.get(pk="s20").county == p.proposed_value
    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(
        record_id="s20", field="county", state=ChangeProposal.PENDING
    ).exists()


def test_whitespace_and_case_do_not_reopen_a_settled_field(
    client, crawler_schema, editor
):
    from django.core.management import call_command

    source = _mo_publisher(owner="")
    call_command("scan_sources", dataset="mo")
    p = ChangeProposal.objects.get(record_id="s20", flag="owner_missing")
    client.post(URL, {f"d-{p.pk}": "fix", f"v-{p.pk}": "Rust Communications"})

    Source.objects.filter(pk=source.pk).update(owner="  Rust   Communications ")
    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(
        record_id="s20", field="owner", state=ChangeProposal.PENDING
    ).exists()


def test_a_question_already_answered_is_cleared_from_the_queue(
    client, crawler_schema, editor
):
    """Rows queued before the decision was recorded must leave the queue,
    not merely stop being recreated."""
    from django.core.management import call_command

    _mo_publisher(owner="")
    ChangeProposal.objects.create(
        target="sources",
        record_id="s20",
        record_label="y.example",
        dataset="mo",
        field="owner",
        flag="",
        current_value="",
        proposed_value="Example Media",
        state=ChangeProposal.REJECTED,
    )
    stale = ChangeProposal.objects.create(
        target="sources",
        record_id="s20",
        record_label="y.example",
        dataset="mo",
        field="owner",
        flag="owner_missing",
        current_value="",
        proposed_value="Example Media",
        state=ChangeProposal.PENDING,
    )

    call_command("scan_sources", dataset="mo")
    assert not ChangeProposal.objects.filter(pk=stale.pk).exists()


# --- what a source file is taken to be saying --------------------------------


def _evidence_for(rows, name="Sources sheet"):
    """Run the evidence loader over rows, without touching a file."""
    import csv
    import io

    from review.management.commands.scan_sources import Command

    buffer = io.StringIO()
    columns = ["host_norm", "canonical_name", "city", "county", "owner", "type"]
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    command = Command()
    command._read = lambda path: buffer.getvalue()
    return command._evidence({"evidence": "x.csv", "evidence_name": name})


def test_two_rows_for_one_host_are_not_a_contradiction():
    """A section of a paper shares its parent's domain. Lee's Summit
    Journal and the Kansas City Star are both on kansascity.com, and the
    fields they agree on are not in dispute because of it."""
    evidence = _evidence_for(
        [
            {
                "host_norm": "kansascity.com",
                "city": "Kansas City",
                "type": "print native",
            },
            {
                "host_norm": "kansascity.com",
                "city": "Kansas City",
                "owner": "McClatchy",
            },
        ]
    )
    assert evidence[("kansascity.com", "city")]["conflicting"] is False
    assert evidence[("kansascity.com", "city")]["value"] == "Kansas City"
    assert evidence[("kansascity.com", "type")]["value"] == "print native"
    assert evidence[("kansascity.com", "owner")]["value"] == "McClatchy"


def test_only_two_different_values_for_one_field_conflict():
    evidence = _evidence_for(
        [
            {"host_norm": "kansascity.com", "canonical_name": "The Kansas City Star"},
            {
                "host_norm": "kansascity.com",
                "canonical_name": "Lee's Summit Missouri Journal News",
            },
        ]
    )
    entry = evidence[("kansascity.com", "canonical_name")]
    assert entry["conflicting"] is True
    assert entry["candidates"] == [
        "The Kansas City Star",
        "Lee's Summit Missouri Journal News",
    ]
    # Nothing is proposed, because nothing is known to be better.
    assert entry["value"] == ""


def test_a_later_row_does_not_erase_an_earlier_value():
    """Keying on (host, field) and assigning made the last row win, so a
    real disagreement was invisible and the surviving value was whichever
    the file happened to list second."""
    evidence = _evidence_for(
        [
            {"host_norm": "a.example", "county": "Boone"},
            {"host_norm": "a.example", "county": "Callaway"},
        ]
    )
    assert evidence[("a.example", "county")]["candidates"] == ["Boone", "Callaway"]


def test_the_detail_reads_as_one_sentence():
    """The queue showed "the record says nothing from Sources sheet" —
    the detail and a separate origin hint run together."""
    from review.management.commands.scan_sources import Command

    single = Command._evidence_detail(
        {
            "origin": "Sources sheet",
            "value": "print native",
            "candidates": ["print native"],
        },
        "",
    )
    assert single == "Sources sheet says “print native”; the record has no value"

    both = Command._evidence_detail(
        {
            "origin": "Sources sheet",
            "value": "",
            "candidates": [
                "The Kansas City Star",
                "Lee's Summit Missouri Journal News",
            ],
        },
        "Lee's Summit Missouri Journal News",
    )
    assert "more than one value here" in both
    assert both.endswith("the record says “Lee's Summit Missouri Journal News”")


def test_new_evidence_answers_a_question_that_had_no_proposal(
    client, crawler_schema, editor, tmp_path
):
    """A flag can be raised before anything is known to propose: "no
    owner recorded" with no candidate anywhere is a research task, not a
    decision. When a directory later supplies a value, the question
    already in the queue should carry it — not stay blank while the new
    proposal is refused as a duplicate."""
    from django.core.management import call_command

    _mo_publisher(owner="", host="z.example", host_norm="z.example", id="s30")
    call_command("scan_sources", dataset="mo")
    blank = ChangeProposal.objects.get(record_id="s30", flag="owner_missing")
    assert blank.proposed_value == ""

    evidence = tmp_path / "mopress.csv"
    evidence.write_text(
        "host_norm,canonical_name,city,county,owner,type\n"
        "z.example,,,,Rust Communications,\n"
    )
    call_command(
        "scan_sources",
        dataset="mo",
        evidence=str(evidence),
        evidence_name="Missouri Press directory",
    )

    blank.refresh_from_db()
    assert blank.proposed_value == "Rust Communications"
    assert blank.state == ChangeProposal.PENDING
    # Still one question, not two.
    assert ChangeProposal.objects.filter(record_id="s30", field="owner").count() == 1


def test_a_proposal_already_offering_a_value_is_left_alone(
    client, crawler_schema, editor, tmp_path
):
    """A row a reviewer may be looking at does not have its proposed
    value changed underneath them."""
    from django.core.management import call_command

    _mo_publisher(owner="", host="y2.example", host_norm="y2.example", id="s31")
    call_command("scan_sources", dataset="mo")
    row = ChangeProposal.objects.get(record_id="s31", flag="owner_missing")
    row.proposed_value = "Example Media"
    row.save(update_fields=["proposed_value"])

    evidence = tmp_path / "mopress.csv"
    evidence.write_text(
        "host_norm,canonical_name,city,county,owner,type\n"
        "y2.example,,,,Rust Communications,\n"
    )
    call_command(
        "scan_sources",
        dataset="mo",
        evidence=str(evidence),
        evidence_name="Missouri Press directory",
    )
    row.refresh_from_db()
    assert row.proposed_value == "Example Media"


# --- publishers the corpus has never heard of --------------------------------
#
# An ordinary proposal names a record and changes a field on it. These name
# none: the proposal is that the record should exist, and accepting it is
# what creates one.


def _reported_publisher(user, dataset="", **overrides):
    import uuid

    from review.proposals import ChangeProposal

    fields = {
        "canonical_name": "The Santa Fe Times",
        "host": "santafetimes.example",
        "city": "Alma",
        "county": "Lafayette",
        "state": "MO",
        "owner": "Lexington Area Chamber of Commerce",
    }
    fields.update(overrides)
    submission = uuid.uuid4()
    ChangeProposal.objects.bulk_create(
        [
            ChangeProposal(
                target="sources",
                record_id="",
                submission=submission,
                record_label=fields["canonical_name"],
                field=field,
                proposed_value=value,
                flag="no_match",
                origin="reported",
                dataset=dataset,
                citation="https://example.test/chamber",
                proposed_by=user,
            )
            for field, value in fields.items()
        ]
    )
    return submission


def test_two_reported_publishers_are_two_decisions(client, editor, crawler_schema):
    """Both have an empty record_id. Grouping on that would collapse every
    pending publisher in the queue into one row."""
    _reported_publisher(editor)
    _reported_publisher(editor, canonical_name="The Odessan", host="odessan.example")
    body = client.get("/review/proposals/").content.decode()
    assert "The Santa Fe Times" in body
    assert "The Odessan" in body


def test_accepting_a_reported_publisher_creates_the_record(
    client, editor, crawler_schema
):
    from explorer.models import Source
    from review.proposals import ChangeProposal

    _reported_publisher(editor)
    decisions = {
        f"d-{p.pk}": "accept"
        for p in ChangeProposal.objects.filter(state=ChangeProposal.PENDING)
    }
    client.post("/review/proposals/", decisions)

    source = Source.objects.get(host_norm="santafetimes.example")
    assert source.canonical_name == "The Santa Fe Times"
    assert source.owner == "Lexington Area Chamber of Commerce"
    assert source.county == "Lafayette"
    assert (source.meta or {}).get("state") == "MO"


def test_rejecting_the_host_rejects_the_publisher(client, editor, crawler_schema):
    """The host is the record's only unique column and the crawler's only way
    in. Without it there is nothing to create."""
    from explorer.models import Source
    from review.proposals import ChangeProposal

    _reported_publisher(editor)
    decisions = {}
    for p in ChangeProposal.objects.filter(state=ChangeProposal.PENDING):
        decisions[f"d-{p.pk}"] = "reject" if p.field == "host" else "accept"
    client.post("/review/proposals/", decisions)

    assert not Source.objects.filter(canonical_name="The Santa Fe Times").exists()


def test_a_reported_publisher_joins_the_dataset_it_was_reviewed_in(
    client, editor, crawler_schema
):
    """Accepted and orphaned is not much better than not accepted."""
    from explorer.models import Dataset, DatasetSource, Source
    from review.proposals import ChangeProposal

    dataset = Dataset.objects.create(id="d-lex", slug="missouri", label="Missouri")
    _reported_publisher(editor, dataset=dataset.slug)
    client.post(
        "/review/proposals/",
        {
            f"d-{p.pk}": "accept"
            for p in ChangeProposal.objects.filter(state=ChangeProposal.PENDING)
        },
    )
    source = Source.objects.get(host_norm="santafetimes.example")
    assert DatasetSource.objects.filter(dataset=dataset, source=source).exists()


def test_a_host_already_taken_is_refused_not_duplicated(client, editor, crawler_schema):
    """The reviewer wanting this publisher already has it; the change belongs
    on the record that exists."""
    from explorer.models import Source
    from review.proposals import ChangeProposal

    Source.objects.create(
        id="s-existing",
        host="santafetimes.example",
        host_norm="santafetimes.example",
        canonical_name="Santa Fe Times",
    )
    _reported_publisher(editor)
    client.post(
        "/review/proposals/",
        {
            f"d-{p.pk}": "accept"
            for p in ChangeProposal.objects.filter(state=ChangeProposal.PENDING)
        },
    )
    assert Source.objects.filter(host_norm="santafetimes.example").count() == 1


def test_a_reviewer_can_correct_a_field_while_accepting_the_publisher(
    client, editor, crawler_schema
):
    """Fix applies to a create exactly as it does to a change."""
    from explorer.models import Source
    from review.proposals import ChangeProposal

    _reported_publisher(editor)
    decisions = {}
    for p in ChangeProposal.objects.filter(state=ChangeProposal.PENDING):
        if p.field == "owner":
            decisions[f"d-{p.pk}"] = "fix"
            decisions[f"v-{p.pk}"] = "Lexington Area Chamber of Commerce, Inc."
        else:
            decisions[f"d-{p.pk}"] = "accept"
    client.post("/review/proposals/", decisions)

    source = Source.objects.get(host_norm="santafetimes.example")
    assert source.owner == "Lexington Area Chamber of Commerce, Inc."


# --- a publisher record's required fields ------------------------------------
#
# County, city and state are required. What made them invisible was not a
# missing check but a check that returned early: `state_missing` only fired
# when a city was recorded, so a record with none of the three was flagged
# for two and never for the third.


def _scanned(dataset, source):
    from django.core.management import call_command

    from explorer.models import DatasetSource

    DatasetSource.objects.create(id=f"ds-{source.id}", dataset=dataset, source=source)
    call_command("scan_sources", dataset=dataset.slug)
    return {p.flag: p for p in ChangeProposal.objects.filter(record_id=source.id)}


@pytest.fixture
def mo(crawler_schema):
    from explorer.models import Dataset

    return Dataset.objects.create(
        id="d-mo", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )


def test_a_record_with_no_state_is_flagged_even_with_no_city(mo, editor):
    """The bug: the check bailed before looking, so twenty-two sources with
    no city, county or state were flagged for two of the three."""
    bare = Source.objects.create(id="s-bare", host="b.example", host_norm="b.example")
    flags = _scanned(mo, bare)
    assert "state_missing" in flags
    assert "county_missing" in flags
    assert "city_missing" in flags


def test_the_datasets_default_state_is_proposed_not_applied(mo, editor):
    """The likeliest answer, offered. A Missouri dataset can hold an outlet
    that is not in Missouri, and inheriting the default would write that in
    without anybody looking."""
    bare = Source.objects.create(id="s-bare2", host="c.example", host_norm="c.example")
    flags = _scanned(mo, bare)
    assert flags["state_missing"].proposed_value == "MO"
    assert "defaults to MO" in flags["state_missing"].detail


def test_a_state_written_out_in_full_is_flagged(mo, editor):
    """'MO' and 'Missouri' are the same place and sort into different
    groups. The postal code is what is stored; screens show the full name."""
    spelt = Source.objects.create(
        id="s-spelt",
        host="d.example",
        host_norm="d.example",
        city="Columbia",
        county="Boone",
        owner="Someone",
        meta={"state": "Missouri"},
    )
    flags = _scanned(mo, spelt)
    assert "state_unknown" in flags
    assert flags["state_unknown"].proposed_value == "MO"
    assert flags["state_unknown"].current_value == "Missouri", "the wrong value shows"


def test_an_owner_in_the_county_column_says_so(mo, editor):
    """'Nexstar Media Inc is an owner, not a county' is a repair somebody
    can make. 'Not a county in MO' is a puzzle."""
    Source.objects.create(
        id="s-owner",
        host="e.example",
        host_norm="e.example",
        owner="Nexstar Media Inc",
        meta={"state": "MO"},
    )
    misfiled = Source.objects.create(
        id="s-mis",
        host="f.example",
        host_norm="f.example",
        city="Columbia",
        county="Nexstar Media Inc",
        meta={"state": "MO"},
    )
    flags = _scanned(mo, misfiled)
    assert "county_misfiled" in flags
    assert "is an owner, not a county" in flags["county_misfiled"].detail
    del misfiled


def test_a_city_in_the_county_column_says_so(mo, editor):
    """Brookfield is a city in Linn County and sits in the county column of
    a real record."""
    wrong = Source.objects.create(
        id="s-brook",
        host="g.example",
        host_norm="g.example",
        city="Brookfield",
        county="Brookfield",
        owner="Someone",
        meta={"state": "MO"},
    )
    flags = _scanned(mo, wrong)
    assert "county_misfiled" in flags
    assert "is a city, not a county" in flags["county_misfiled"].detail


def test_a_correct_record_is_not_flagged_for_geography(mo, editor):
    good = Source.objects.create(
        id="s-good",
        host="h.example",
        host_norm="h.example",
        canonical_name="The Columbia Example",
        city="Columbia",
        county="Boone",
        owner="CherryRoad Media",
        meta={"state": "MO"},
    )
    flags = _scanned(mo, good)
    for key in ("state_missing", "state_unknown", "county_misfiled", "city_misfiled"):
        assert key not in flags, f"{key} fired on a clean record"


# --- a required field that could not be written ------------------------------
#
# The state is a required field of a publisher record and had no way to be
# written at all: the source form did not carry it, and accepting a proposal
# that named it hit the write boundary. So the scan could ask for a state and
# nothing could supply one.


def test_the_state_is_inside_the_write_boundary():
    from explorer.models import Source
    from review.services import WRITABLE

    assert "meta.state" in WRITABLE[Source]
    assert "meta" not in WRITABLE[Source], "the rest of the blob stays closed"


def test_writing_the_state_leaves_the_rest_of_the_blob_alone(crawler_schema, editor):
    """A dotted write replaces one key. Writing the whole column would drop
    whatever else the record kept there."""
    from review.services import audited_update

    source = Source.objects.create(
        id="s-meta",
        host="m.example",
        host_norm="m.example",
        meta={"state": "", "notes": "keep me", "rss": "https://m.example/feed"},
    )
    audited_update(
        editor, [source], {"meta.state": "MO"}, action="source:edit", reason="test"
    )
    source.refresh_from_db()
    assert source.meta["state"] == "MO"
    assert source.meta["notes"] == "keep me"
    assert source.meta["rss"] == "https://m.example/feed"


def test_a_state_write_can_be_reverted(crawler_schema, editor):
    """Whatever writes it has to be undoable like every other audited
    change, which means `before` has to record the key and not the blob."""
    from review.services import audited_update, revert

    source = Source.objects.create(
        id="s-rev",
        host="r.example",
        host_norm="r.example",
        meta={"state": "Missouri", "notes": "keep me"},
    )
    entry = audited_update(
        editor, [source], {"meta.state": "MO"}, action="source:edit", reason="normalise"
    )
    assert entry.before["s-rev"]["meta.state"] == "Missouri"

    revert(editor, entry, reason="undo")
    source.refresh_from_db()
    assert source.meta["state"] == "Missouri"
    assert source.meta["notes"] == "keep me"


def test_a_proposal_naming_the_state_can_be_accepted(client, editor, mo):
    """The whole point: the scan proposes MO, and accepting it writes MO.
    Before this the proposal could be raised and never applied."""
    bare = Source.objects.create(
        id="s-accept",
        host="a2.example",
        host_norm="a2.example",
        city="Columbia",
        county="Boone",
        owner="Someone",
    )
    flags = _scanned(mo, bare)
    proposal = flags["state_missing"]
    assert proposal.proposed_value == "MO"

    client.post("/review/proposals/", {f"d-{proposal.pk}": "accept"})
    bare.refresh_from_db()
    assert (bare.meta or {}).get("state") == "MO"
