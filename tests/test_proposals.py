"""The proposal queue: grouped by record, decided in a session, applied
as one audited batch."""

import io
from unittest import mock

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


def test_a_frequency_fix_is_written_into_the_record(client, editor, publisher):
    """`meta.frequency` is a key inside a JSON column, like `meta.state`,
    and had to be named before it could be written. Until it was, the
    thirty-one proposals the scan raised for it could be seen and not
    applied."""
    p = _proposal(publisher, "meta.frequency", "Weekly", "weekly")
    client.post(URL, {f"d-{p.pk}": "accept"})
    publisher.refresh_from_db()
    assert (publisher.meta or {}).get("frequency") == "weekly"
    p.refresh_from_db()
    assert p.state == ChangeProposal.ACCEPTED


def test_a_write_the_boundary_refuses_is_said_rather_than_raised(
    client, editor, publisher
):
    """The flag vocabulary and the write boundary are kept apart, so a
    check can name a field the boundary does not include. Applying it
    raises BoundaryViolation, which nothing caught: submitting a filtered
    queue of thirty-one proposals answered with a server error and no clue
    which of them caused it.

    Nothing is decided, including the rejections in the same submission. A
    half-applied batch is worse than one that did not go through, because
    what was refused is the part nobody sees.
    """
    refused = _proposal(publisher, "meta.cohort", "", "anything")
    alongside = _proposal(publisher, "owner", "Somebody", "Somebody Else")
    response = client.post(
        URL,
        {f"d-{refused.pk}": "accept", f"d-{alongside.pk}": "reject"},
        follow=True,
    )
    assert response.status_code == 200
    page = response.content.decode()
    assert "Nothing was saved" in page
    assert "meta.cohort" in page, "which field was refused must be on the page"

    refused.refresh_from_db()
    alongside.refresh_from_db()
    assert refused.state == ChangeProposal.PENDING
    assert alongside.state == ChangeProposal.PENDING, "decided anyway"


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
        # What kind of publication, which `datasets/schema.py` calls
        # required: a record that does not say cannot be placed by
        # anything organised by medium. Thirty-one records in production
        # say nothing here, and they are questions now.
        type="digital native",
        # Its own state, not the dataset's. A record carrying none is a
        # record with something wrong -- see the tests below. The home
        # page is required too: a record that does not say where the
        # publication lives cannot be linked to or checked.
        meta={"state": "MO", "homepage": "https://ok.example"},
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


def test_a_type_spelled_differently_is_queued_with_the_spelling_to_use(mo, editor):
    """Four records say 'digital_native' where 902 say 'digital native'.
    The chart folds them so one kind is one filter, which makes the filter
    usable and leaves the record wrong. Fixing it once here fixes it for
    everything that reads the field."""
    odd = Source.objects.create(
        id="s-type1",
        host="t1.example",
        host_norm="t1.example",
        type="digital_native",
    )
    flags = _scanned(mo, odd)
    assert "type_spelling" in flags
    proposal = flags["type_spelling"]
    assert proposal.proposed_value == "digital native"
    assert "digital_native" in proposal.detail

    # The spelling the corpus already uses is not a defect.
    fine = Source.objects.create(
        id="s-type2", host="t2.example", host_norm="t2.example", type="digital native"
    )
    assert "type_spelling" not in _scanned(mo, fine)


def test_a_type_that_cannot_be_placed_is_queued_without_a_guess(mo, editor):
    """Ten records say only 'broadcast', which does not answer whether this
    is a television station or a radio one. What is missing is the answer,
    so nothing is proposed: a queue that offered one would be guessing in
    front of the person who came here to decide."""
    vague = Source.objects.create(
        id="s-type3", host="t3.example", host_norm="t3.example", type="broadcast"
    )
    flags = _scanned(mo, vague)
    assert "type_indistinct" in flags
    assert flags["type_indistinct"].proposed_value == ""
    assert "television or radio" in flags["type_indistinct"].detail

    # A word the vocabulary does not know at all is raised the same way,
    # and read by a person rather than guessed at.
    other = Source.objects.create(
        id="s-type4", host="t4.example", host_norm="t4.example", type="government"
    )
    assert "type_indistinct" in _scanned(mo, other)


def test_how_often_it_publishes_is_read_the_same_way(mo, editor):
    """The same two defects on the frequency field: 29 records say 'Weekly'
    where 85 say 'weekly', and nine record 'Broadcast', which is not a
    frequency at all."""
    cased = Source.objects.create(
        id="s-freq1",
        host="f1.example",
        host_norm="f1.example",
        meta={"frequency": "Weekly"},
    )
    flags = _scanned(mo, cased)
    assert flags["frequency_spelling"].proposed_value == "weekly"

    wrong = Source.objects.create(
        id="s-freq2",
        host="f2.example",
        host_norm="f2.example",
        meta={"frequency": "Broadcast"},
    )
    flags = _scanned(mo, wrong)
    assert "frequency_indistinct" in flags
    assert flags["frequency_indistinct"].proposed_value == ""


def test_two_frequencies_that_differ_are_not_called_a_misspelling(mo, editor):
    """Bi-weekly, tri-weekly and semi-weekly filter together and are three
    different answers to how often something publishes. Proposing one as
    the fix for another would put a wrong value in front of a reviewer as
    the right one, so none of them is proposed at all."""
    for i, value in enumerate(("bi-weekly", "Tri-weekly", "semi weekly"), start=5):
        source = Source.objects.create(
            id=f"s-freq{i}",
            host=f"f{i}.example",
            host_norm=f"f{i}.example",
            meta={"frequency": value},
        )
        flags = _scanned(mo, source)
        assert "frequency_spelling" not in flags, value

    # Two answers in one field is a question, though, and asked as one.
    both = Source.objects.create(
        id="s-freq9",
        host="f9.example",
        host_norm="f9.example",
        meta={"frequency": "weekly/daily"},
    )
    flags = _scanned(mo, both)
    assert "frequency_indistinct" in flags
    assert flags["frequency_indistinct"].proposed_value == ""


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


def test_a_name_that_is_both_a_city_and_a_county_is_not_misfiled(mo, editor):
    """Missouri has a St. Louis city and a St. Louis County, a Jackson and
    a Jackson County, a Jasper and a Jasper County. A name being both is
    ordinary. Flagging every one buried the real mapping errors under
    dozens that were not.
    """
    for name in ("St. Louis", "Jackson", "Jasper"):
        source = Source.objects.create(
            id=f"s-both-{name.replace('. ', '').replace(' ', '')}",
            host=f"{name.lower().replace('. ', '').replace(' ', '')}.example",
            host_norm=f"{name.lower().replace('. ', '').replace(' ', '')}.example",
            city=name,
            county=name,
            owner="Someone",
            meta={"state": "MO"},
        )
        flags = _scanned(mo, source)
        assert "county_misfiled" not in flags, f"{name} county"
        assert "city_misfiled" not in flags, f"{name} city"


def test_a_value_wrong_for_its_own_field_is_still_caught(mo, editor):
    """The guard must not swallow the case it was written for."""
    Source.objects.create(
        id="s-owner-x",
        host="ox.example",
        host_norm="ox.example",
        owner="Nexstar Media Inc",
        meta={"state": "MO"},
    )
    wrong = Source.objects.create(
        id="s-mis-x",
        host="mx.example",
        host_norm="mx.example",
        city="Columbia",
        county="Nexstar Media Inc",
        meta={"state": "MO"},
    )
    flags = _scanned(mo, wrong)
    assert "county_misfiled" in flags


def test_the_queue_filter_does_not_claim_the_records_are_all_wrong(
    client, editor, crawler_schema
):
    """ "Everything wrong 19" reads as a claim about the filtered records
    rather than a way to clear the filter and see the whole queue."""
    body = client.get("/review/proposals/").content.decode()
    assert "Review all" in body
    assert "Everything wrong" not in body


def test_a_check_that_stops_firing_takes_its_proposals_with_it(mo, editor):
    """`_retire_settled` sweeps questions a person has answered. Nothing
    swept questions the app had stopped asking, so correcting the
    misfiled-column check left ninety-odd of its mistakes in the queue for
    good -- and a queue holding questions nothing asks is a queue nobody
    trusts.
    """
    from django.core.management import call_command

    from explorer.models import DatasetSource

    source = Source.objects.create(
        id="s-stale",
        host="s.example",
        host_norm="s.example",
        city="Columbia",
        county="Boone",
        owner="Someone",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds-stale", dataset=mo, source=source)

    # A proposal from a check that no longer fires on this record.
    ChangeProposal.objects.create(
        target="sources",
        record_id=source.id,
        record_label=source.host_norm,
        dataset=mo.slug,
        field="county",
        flag="county_misfiled",
        detail="Boone is a city, not a county",
    )
    call_command("scan_sources", dataset=mo.slug)
    assert not ChangeProposal.objects.filter(
        record_id=source.id, flag="county_misfiled", state=ChangeProposal.PENDING
    ).exists()


def test_a_record_outside_this_scan_is_left_alone(mo, editor, crawler_schema):
    """Only what was actually looked at. A proposal on a record in another
    dataset was not re-examined, and must not be swept on the strength of
    not having been looked at."""
    from django.core.management import call_command

    from explorer.models import DatasetSource

    inside = Source.objects.create(
        id="s-in",
        host="in.example",
        host_norm="in.example",
        city="Columbia",
        county="Boone",
        owner="Someone",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds-in", dataset=mo, source=inside)

    elsewhere = Source.objects.create(
        id="s-out",
        host="out.example",
        host_norm="out.example",
    )
    ChangeProposal.objects.create(
        target="sources",
        record_id=elsewhere.id,
        record_label=elsewhere.host_norm,
        field="county",
        flag="county_missing",
    )
    call_command("scan_sources", dataset=mo.slug)
    assert ChangeProposal.objects.filter(record_id=elsewhere.id).exists()


def test_a_dry_run_withdraws_nothing(mo, editor):
    from django.core.management import call_command

    from explorer.models import DatasetSource

    source = Source.objects.create(
        id="s-dry",
        host="d2.example",
        host_norm="d2.example",
        city="Columbia",
        county="Boone",
        owner="Someone",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds-dry", dataset=mo, source=source)
    ChangeProposal.objects.create(
        target="sources",
        record_id=source.id,
        record_label=source.host_norm,
        field="county",
        flag="county_misfiled",
    )
    call_command("scan_sources", dataset=mo.slug, dry_run=True)
    assert ChangeProposal.objects.filter(record_id=source.id).exists()


# --- running the scan from the queue -----------------------------------------
#
# The scan is what puts questions here, and the only way to run it was a
# command somebody had to remember.


def test_the_queue_says_when_it_last_ran(client, editor, crawler_schema):
    """An empty queue means nothing is wrong or nothing has looked, and a
    reviewer cannot tell which."""
    body = client.get("/review/proposals/").content.decode()
    assert "Never scanned" in body

    from django.utils import timezone

    from review.proposals import ScanRun

    ScanRun.objects.create(
        state=ScanRun.DONE, finished_at=timezone.now(), scanned=211, queued=9
    )
    body = client.get("/review/proposals/").content.decode()
    assert "Last scanned" in body
    assert "211 publishers" in body


def test_the_button_runs_the_scan(client, editor, mo):
    from explorer.models import DatasetSource
    from review.proposals import ScanRun

    bare = Source.objects.create(id="s-btn", host="b2.example", host_norm="b2.example")
    DatasetSource.objects.create(id="ds-btn", dataset=mo, source=bare)

    client.post("/review/proposals/rescan/", {"dataset": mo.slug})
    run = ScanRun.objects.first()
    assert run.state == ScanRun.DONE
    assert run.scanned == 1
    assert ChangeProposal.objects.filter(record_id="s-btn").exists()


def test_a_second_scan_is_refused_while_one_is_in_flight(client, editor, mo):
    """Two scans would each sweep rows the other had just made, and the
    queue would hold whichever finished last."""
    from review.proposals import ScanRun

    ScanRun.objects.create(dataset=mo.slug, state=ScanRun.RUNNING)
    client.post("/review/proposals/rescan/", {"dataset": mo.slug})
    assert ScanRun.objects.count() == 1, "a second run was started"


def test_a_run_that_never_finished_does_not_block_forever(client, editor, mo):
    """A crash mid-scan would otherwise lock the button permanently."""
    from datetime import timedelta

    from django.utils import timezone

    from review.proposals import ScanRun

    stuck = ScanRun.objects.create(dataset=mo.slug, state=ScanRun.RUNNING)
    ScanRun.objects.filter(pk=stuck.pk).update(
        started_at=timezone.now() - timedelta(hours=3)
    )
    assert ScanRun.running() is None

    client.post("/review/proposals/rescan/", {"dataset": mo.slug})
    assert ScanRun.objects.count() == 2


def test_a_failed_scan_is_recorded_rather_than_swallowed(client, editor, mo):
    from unittest.mock import patch

    from review.proposals import ScanRun

    with patch("django.core.management.call_command", side_effect=RuntimeError("nope")):
        client.post("/review/proposals/rescan/", {"dataset": mo.slug})
    run = ScanRun.objects.first()
    assert run.state == ScanRun.FAILED
    assert "nope" in run.note


def test_the_scan_needs_write_not_merely_a_sign_in(client, crawler_schema):
    """It changes the queue everybody else works from."""
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    watcher = User.objects.create_user("watcher", email="w@localnewsimpact.org")
    Grant.objects.create(user=watcher, app=DATADESK, scope="", role="viewer")
    client.force_login(watcher)
    response = client.post("/review/proposals/rescan/", {})
    assert response.status_code in (302, 403)

    from review.proposals import ScanRun

    assert not ScanRun.objects.exists()


# --- coverage is the point ---------------------------------------------------
#
# A scan that has to be asked for one dataset at a time is as complete as
# the last person's memory. Missouri's owners were found and fixed while
# 894 Vermont publishers with none recorded stayed invisible, because
# nobody had ever typed Vermont's name.


@pytest.fixture
def vt(crawler_schema):
    from explorer.models import Dataset

    return Dataset.objects.create(
        id="d-vt", slug="vt", label="Vermont", meta={"default_state": "VT"}
    )


def _in(dataset, source):
    from explorer.models import DatasetSource

    DatasetSource.objects.create(
        id=f"ds-{dataset.slug}-{source.id}", dataset=dataset, source=source
    )
    return source


def test_a_scan_with_no_dataset_named_covers_every_one(mo, vt, editor):
    from django.core.management import call_command

    _in(
        mo,
        Source.objects.create(
            id="s-mo1",
            host="mo1.example",
            host_norm="mo1.example",
            canonical_name="Missouri Paper",
            city="Columbia",
            county="Boone",
            owner="",
            meta={"state": "MO"},
        ),
    )
    _in(
        vt,
        Source.objects.create(
            id="s-vt1",
            host="vt1.example",
            host_norm="vt1.example",
            canonical_name="Vermont Paper",
            city="Burlington",
            county="Chittenden",
            owner="",
            meta={"state": "VT"},
        ),
    )

    out = io.StringIO()
    call_command("scan_sources", stdout=out)

    queued = ChangeProposal.objects.filter(target="sources", flag="owner_missing")
    assert set(queued.values_list("record_id", flat=True)) == {"s-mo1", "s-vt1"}
    # ...and each proposal knows which directory it belongs to, or it
    # cannot be filtered back out again.
    assert set(queued.values_list("dataset", flat=True)) == {"mo", "vt"}
    # The summary names each, because a bare count over several datasets
    # says nothing about which one it counted.
    assert "mo:" in out.getvalue() and "vt:" in out.getvalue()


def test_a_named_dataset_still_scans_only_that_one(mo, vt, editor):
    from django.core.management import call_command

    _in(
        mo,
        Source.objects.create(
            id="s-mo2",
            host="mo2.example",
            host_norm="mo2.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    _in(
        vt,
        Source.objects.create(
            id="s-vt2",
            host="vt2.example",
            host_norm="vt2.example",
            canonical_name="Vermont Paper",
            owner="",
            meta={"state": "VT"},
        ),
    )

    call_command("scan_sources", dataset="mo", stdout=io.StringIO())
    assert set(
        ChangeProposal.objects.filter(target="sources").values_list(
            "dataset", flat=True
        )
    ) == {"mo"}


def test_the_queue_can_be_worked_one_directory_at_a_time(client, mo, vt, editor):
    """Scanning everything is what makes the queue complete and what
    makes it long. Somebody working Missouri must not have to read past
    Vermont to do it."""
    from django.core.management import call_command

    _in(
        mo,
        Source.objects.create(
            id="s-mo3",
            host="mo3.example",
            host_norm="mo3.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    for i in range(3):
        _in(
            vt,
            Source.objects.create(
                id=f"s-vt3{i}",
                host=f"vt3{i}.example",
                host_norm=f"vt3{i}.example",
                canonical_name="Vermont Paper",
                owner="",
                meta={"state": "VT"},
            ),
        )
    call_command("scan_sources", stdout=io.StringIO())

    every = client.get(URL).content.decode()
    assert "Every directory" in every, "no way to choose a directory"

    only_mo = client.get(URL + "?dataset=mo").content.decode()
    assert "mo3.example" in only_mo
    assert "vt30.example" not in only_mo, "Vermont is in Missouri's queue"

    only_vt = client.get(URL + "?dataset=vt").content.decode()
    assert "vt30.example" in only_vt
    assert "mo3.example" not in only_vt


def test_the_flag_counts_follow_the_directory_being_worked(client, mo, vt, editor):
    """A count that ignores the filter promises rows the filter hides."""
    from django.core.management import call_command

    _in(
        mo,
        Source.objects.create(
            id="s-mo4",
            host="mo4.example",
            host_norm="mo4.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    for i in range(4):
        _in(
            vt,
            Source.objects.create(
                id=f"s-vt4{i}",
                host=f"vt4{i}.example",
                host_norm=f"vt4{i}.example",
                canonical_name="Vermont Paper",
                owner="",
                meta={"state": "VT"},
            ),
        )
    call_command("scan_sources", stdout=io.StringIO())

    mo_only = ChangeProposal.objects.filter(dataset="mo", flag="owner_missing").count()
    vt_only = ChangeProposal.objects.filter(dataset="vt", flag="owner_missing").count()
    assert (mo_only, vt_only) == (1, 4)

    body = client.get(URL + "?dataset=mo").content.decode()
    # The "No owner recorded" chip counts Missouri's one, not all five.
    import re

    chip = re.search(r"No owner recorded <span>(\d+)</span>", body)
    assert chip and chip.group(1) == "1", body[body.find("No owner recorded") - 200 :][
        :400
    ]


# --- running by itself -------------------------------------------------------
#
# A scan somebody has to remember to run is a queue that is as complete
# as the last person's memory -- the same failure as the required
# --dataset, one level up. Daily is the floor; skipping what has not
# moved is what makes daily cheap enough to leave switched on.


def test_a_second_scan_of_unchanged_records_does_no_work(mo, editor):
    from django.core.management import call_command

    from review.proposals import DatasetScan

    _in(
        mo,
        Source.objects.create(
            id="s-mo5",
            host="mo5.example",
            host_norm="mo5.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )

    first = io.StringIO()
    call_command("scan_sources", if_changed=True, stdout=first)
    assert "publishers scanned" in first.getvalue()
    assert DatasetScan.objects.get(dataset="mo").stamp

    second = io.StringIO()
    call_command("scan_sources", if_changed=True, stdout=second)
    assert "unchanged, not scanned" in second.getvalue()
    assert "publishers scanned" not in second.getvalue()


def test_a_record_that_changes_is_scanned_again(mo, editor):
    from django.core.management import call_command

    source = _in(
        mo,
        Source.objects.create(
            id="s-mo6",
            host="mo6.example",
            host_norm="mo6.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    call_command("scan_sources", if_changed=True, stdout=io.StringIO())

    source.owner = "CherryRoad Media"
    source.save(update_fields=["owner"])

    out = io.StringIO()
    call_command("scan_sources", if_changed=True, stdout=out)
    assert "publishers scanned" in out.getvalue(), "an edited record was not re-read"


def test_a_new_check_re_reads_records_that_have_not_moved(mo, editor):
    """A scan is the checks applied to the records, so a new check is a
    reason to look again at records that have not changed."""
    from django.core.management import call_command

    from review.proposals import sources_stamp

    _in(
        mo,
        Source.objects.create(
            id="s-mo7",
            host="mo7.example",
            host_norm="mo7.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    before = sources_stamp("mo")
    call_command("scan_sources", if_changed=True, stdout=io.StringIO())

    import review.flags as flags_module

    one_more = flags_module.FLAGS + (flags_module.FLAGS[0],)
    with mock.patch.object(flags_module, "FLAGS", one_more):
        assert sources_stamp("mo") != before, "the vocabulary is not in the stamp"
        out = io.StringIO()
        call_command("scan_sources", if_changed=True, stdout=out)
        assert "publishers scanned" in out.getvalue()


def test_a_dry_run_does_not_claim_the_records_were_scanned(mo, editor):
    """A stamp written for a run that queued nothing would make the next
    real run skip records it never looked at."""
    from django.core.management import call_command

    from review.proposals import DatasetScan

    _in(
        mo,
        Source.objects.create(
            id="s-mo8",
            host="mo8.example",
            host_norm="mo8.example",
            canonical_name="Missouri Paper",
            owner="",
            meta={"state": "MO"},
        ),
    )
    call_command("scan_sources", if_changed=True, dry_run=True, stdout=io.StringIO())
    assert not DatasetScan.objects.filter(dataset="mo").exists()

    out = io.StringIO()
    call_command("scan_sources", if_changed=True, stdout=out)
    assert "publishers scanned" in out.getvalue()


def test_every_flag_proposes_a_field_the_queue_can_write():
    """A flag names the field its defect is on, and accepting the proposal
    writes that field. The two lists are maintained apart, so a check can
    be added for a field the write boundary does not include -- which is
    not a refusal a reviewer sees. `audited_update_rows` raises
    BoundaryViolation, nothing catches it, and submitting the queue
    answers with a server error.

    `frequency_spelling` shipped that way: it proposed 'weekly' for
    `meta.frequency`, which was not writable, so a filtered queue of 31
    proposals 500d on submit.
    """
    from explorer.models import Source
    from review.flags import FLAGS
    from review.services import WRITABLE

    writable = set(WRITABLE[Source])
    for flag in FLAGS:
        if not flag.field:
            # Raised by evidence rather than by the record: value_disputed
            # and no_match name no single field.
            continue
        assert flag.field in writable, (
            f"{flag.key} is on {flag.field}, which the write boundary does "
            "not include, so accepting its proposal raises rather than saves"
        )


# --- what a publisher record is (datasets/schema.py) -------------------------


def test_the_rules_accept_what_a_record_actually_holds():
    """Loose on purpose: these say "this is not a ZIP code" and never
    "this is the wrong ZIP code". A rule that refuses a correct value is
    worse than no rule, because the record it refuses is right."""
    from datasets.schema import BY_KEY, check

    good = {
        "meta.zip": ["65201", "65201-1234"],
        "meta.phone": [
            "573-882-4713",
            "(573) 882-4713",
            "+1 573 882 4713",
            "573.882.4713 x204",
        ],
        "meta.address1": ["120 Neff Hall", "1 N Main St"],
        "meta.homepage": ["https://komu.com", "http://example.org/news"],
        "host": ["komu.com", "news.example.co.uk"],
        "meta.state": ["MO", "VT"],
    }
    for key, values in good.items():
        for value in values:
            ok, why = check(BY_KEY[key], value)
            assert ok, f"{key} refused {value!r}: {why}"

    bad = {
        "meta.zip": ["652", "6520A", "65201-12"],
        "meta.address1": ["Main Street", "1600"],
        "meta.homepage": ["komu.com", "ftp://example.org"],
        "meta.state": ["Missouri", "mo"],
    }
    for key, values in bad.items():
        for value in values:
            ok, why = check(BY_KEY[key], value)
            assert not ok, f"{key} accepted {value!r}"
            assert value in why, "the reason must name the value"

    # Empty is not this function's business: whether a field may be empty
    # is `required`, and checking both here reports one defect twice.
    for field in BY_KEY.values():
        assert check(field, "")[0]
        assert check(field, None)[0]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_word_added_to_the_vocabulary_stops_the_queue_asking(mo, editor):
    """The point of the page. A kind of publication nobody listed is a
    question about every record that uses it, and adding the word is the
    answer -- without a deploy, and without the queue asking again."""
    from datasets.models import VocabularyTerm
    from datasets.terms import forget, known

    odd = Source.objects.create(
        id="s-podcast",
        host="pod.example",
        host_norm="pod.example",
        canonical_name="A Podcast",
        city="Columbia",
        county="Boone",
        owner="Somebody",
        type="podcast",
        meta={"state": "MO"},
    )
    assert "type_indistinct" in _scanned(mo, odd)

    VocabularyTerm.objects.create(
        vocabulary="publisher_type", value="podcast", label="Podcast"
    )
    forget("publisher_type")
    assert known("publisher_type", "Podcast"), "case is folded, as everywhere"

    # The scan again, not `_scanned`: the membership row it makes is
    # already there, and making it twice is a unique-key error rather than
    # a second scan.
    from django.core.management import call_command

    ChangeProposal.objects.filter(record_id=odd.id).delete()
    call_command("scan_sources", dataset=mo.slug)
    again = {p.flag for p in ChangeProposal.objects.filter(record_id=odd.id)}
    assert "type_indistinct" not in again


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_vocabulary_falls_back_to_what_the_corpus_used(mo):
    """With no rows at all a vocabulary would mean "every recorded value
    is wrong", which on the morning of the migration is 1,149 records and
    no way to tell which a person should look at."""
    from datasets.models import VocabularyTerm
    from datasets.terms import forget, known

    VocabularyTerm.objects.all().delete()
    forget()
    assert known("publisher_type", "digital native")
    assert known("publisher_frequency", "Weekly")
    assert not known("publisher_type", "podcast")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_schema_page_is_admins_only(client, editor):
    """It decides what the whole console treats as correct: a word added
    stops the queue asking about every record that uses it."""
    assert client.get("/review/schema/").status_code == 403


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_admin_adds_and_retires_a_word(client, admin_user):
    from accounts.models import DATADESK, Grant
    from datasets.models import VocabularyTerm

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)

    page = client.get("/review/schema/").content.decode()
    assert "What a publisher record is" in page
    assert "Required" in page and "Optional" in page
    # The rules are shown, not only the words, and said to a person: a
    # page that read the keys out said a field held "a zip" and "a url".
    assert "five digits, or five and four" in page
    assert "a state&#x27;s two-letter postal code" in page or (
        "a state's two-letter postal code" in page
    )

    client.post(
        "/review/schema/",
        {"vocabulary": "publisher_type", "value": "Podcast", "label": "Podcast"},
    )
    term = VocabularyTerm.objects.get(vocabulary="publisher_type", value="podcast")
    assert term.label == "Podcast" and not term.retired

    # Retired rather than deleted: the word is still on the records
    # written while it was offered.
    client.post(
        "/review/schema/", {"vocabulary": "publisher_type", "retire": "podcast"}
    )
    term.refresh_from_db()
    assert term.retired
    assert VocabularyTerm.objects.filter(value="podcast").exists()


def test_one_list_of_fields_and_not_five():
    """Five places knew part of what a publisher record is and they
    disagreed: `meta.state` could not be written at all for a while, and
    `meta.frequency` was raised as a defect by a queue that then refused
    to apply the fix.

    Each reads the schema now. This asserts they still do — a field added
    to the declaration has to arrive everywhere at once, which is the
    whole point of there being one.
    """
    from datasets.schema import FIELDS
    from explorer.models import Source
    from review.imports import importable_fields
    from review.management.commands.scan_sources import EVIDENCE_FIELDS
    from review.services import WRITABLE

    declared = {f.key for f in FIELDS} - {"host"}
    # The write boundary is the schema plus the paywall panel, which is
    # not schema fields: a checkbox, an amount and the period it covers
    # are three shapes the schema's rules do not have. Named here rather
    # than allowed for, so a fifth thing cannot join them quietly.
    from datasets.views import PAYWALL_FIELDS

    # The secret's name is writable so the paywall page can record where
    # a credential was stored, and it is never typed: it is derived from
    # the host after Secret Manager has accepted the write.
    assert set(WRITABLE[Source]) == declared | set(PAYWALL_FIELDS) | {
        "auth_secret_name"
    }
    # The extractor's own parameters stay out.
    assert "auth_config" not in WRITABLE[Source]
    # And a spreadsheet cannot name one, or an upload could point a
    # publisher at another publisher's credentials.
    assert "auth_secret_name" not in importable_fields("sources")
    # A file supplies what the schema declares. Whether a publication has
    # a paywall is a judgement somebody makes on the record.
    assert set(EVIDENCE_FIELDS) == declared
    # The import path reads the write boundary, so it follows too.
    assert set(importable_fields("sources")) == declared | set(PAYWALL_FIELDS)
    # The source form writes only the keys it builds itself, so a posted
    # secret name reaches nothing there either -- asserted where that form
    # is tested.

    # And every field the schema says is asked about has something asking,
    # or the word on the page is the whole of it. Required and suggested
    # both ask; what differs is whether empty is an answer.
    from review.flags import FLAGS

    asks = {f.field for f in FLAGS}
    for field in FIELDS:
        if field.asked and field.key != "host":
            assert field.key in asks, f"{field.key} is asked for and nothing asks"
        if field.need == "optional":
            # Nothing chases an optional field. Owner sat in the queue on
            # 894 records while the schema called it optional, which is
            # two answers to one question.
            assert f"{field.key}_missing" not in {f.key for f in FLAGS}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reported_publisher_keeps_what_was_reported(client, editor, mo):
    """The create path named five columns and one key, so an address, a
    ZIP and a telephone number were accepted on the page and dropped
    between the page and the row."""
    import uuid as _uuid

    submission = _uuid.uuid4()
    for field, value in (
        ("host", "reported.example"),
        ("canonical_name", "The Reported"),
        ("city", "Columbia"),
        ("meta.state", "MO"),
        ("meta.zip", "65201"),
        ("meta.phone", "573-882-4713"),
        ("meta.address1", "120 Neff Hall"),
    ):
        ChangeProposal.objects.create(
            target="sources",
            record_id="",
            submission=submission,
            field=field,
            proposed_value=value,
            state=ChangeProposal.PENDING,
            origin="a reader",
        )
    decisions = {
        f"d-{p.pk}": "accept"
        for p in ChangeProposal.objects.filter(submission=submission)
    }
    client.post(URL, decisions)

    made = Source.objects.get(host_norm="reported.example")
    assert made.canonical_name == "The Reported"
    assert made.meta["zip"] == "65201"
    assert made.meta["phone"] == "573-882-4713"
    assert made.meta["address1"] == "120 Neff Hall"
    assert made.meta["state"] == "MO"


def test_the_schema_page_is_in_the_navigation():
    """A page nothing links to is a page nobody finds. This one shipped
    that way: it was live at /review/schema/ and reachable only by typing
    the path."""
    from accounts.sections import SECTION_GROUPS

    urls = {
        section.get("url") for group in SECTION_GROUPS for section in group["sections"]
    }
    assert "review:schema" in urls


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_record_is_not_asked_for_the_host_it_already_has(mo, editor):
    """The home page and the host are the same record. The one publisher
    carrying both reads https://krcgtv.com/ against a host of krcgtv.com:
    the same fact twice, with a scheme and a slash.

    So a record without one is not incomplete, and the queue does not ask
    about it. The field stays for the ones where the two differ -- a site
    served over http, a publication on a path or a subdomain, a domain
    that redirects -- which is a correction somebody makes, not a value
    eleven hundred records are missing.
    """
    from review.flags import BY_KEY

    assert "homepage_missing" not in BY_KEY

    bare = Source.objects.create(
        id="s-home",
        host="Example.com",
        host_norm="example.com",
        canonical_name="An Example",
        city="Columbia",
        county="Boone",
        owner="Somebody",
        type="digital native",
        meta={"state": "MO"},
    )
    assert not _scanned(mo, bare), "asked for something the host answers"


def test_what_a_record_needs_and_what_it_may_omit():
    """Changed on purpose, so it is asserted rather than assumed: a
    publication's home page is part of what it is, and who owns it is not
    always known to the person writing the record down."""
    from datasets.schema import BY_KEY

    # Neither is required, and for opposite reasons: an owner is not
    # always known to the person writing the record down, and a home page
    # is usually the host with https:// in front of it.
    assert not BY_KEY["meta.homepage"].required
    assert not BY_KEY["owner"].required
    # Still checked when it is given: an override that is not a web
    # address overrides the host with something worse.
    from datasets.schema import check

    assert check(BY_KEY["meta.homepage"], "https://krcgtv.com/")[0]
    assert not check(BY_KEY["meta.homepage"], "krcgtv.com")[0]

    # Reachable before the vocabularies, which are long enough on the page
    # to push everything after them out of sight.
    from datasets.schema import FIELDS

    order = [f.key for f in FIELDS]
    assert order.index("meta.homepage") < order.index("type")
    assert order.index("owner") < order.index("meta.address1")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_words_are_grouped_under_what_they_mean(client, admin_user):
    """Listed flat, a row read "digital counts as Digital written digital
    native" -- three values in a row with nothing saying which was which.
    The kinds come first now, and under each the words that normalize to
    it."""
    from accounts.models import DATADESK, Grant

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    page = client.get("/review/schema/").content.decode()

    # The kind, what records write for it, and the words that mean it.
    assert "Digital" in page
    assert "recorded as" in page
    assert 'placeholder="another word for digital"' in page
    # A kind with no one spelling says so rather than proposing one.
    assert "no one spelling" in page
    # And a kind nobody has yet can be added.
    assert "Add a kind" in page


def test_three_levels_of_need_and_what_each_means():
    """Two were not enough. Owner sat in the queue on 894 records while
    the schema called it optional, which is two answers to one question:
    either the queue should stop asking or the schema should stop calling
    it optional. It is neither -- a record with no owner is not
    incomplete, and it is still worth asking."""
    from datasets.schema import ASKED, BY_KEY, OPTIONAL, REQUIRED, SUGGESTED

    assert BY_KEY["county"].need == REQUIRED
    assert BY_KEY["owner"].need == SUGGESTED
    assert BY_KEY["meta.zip"].need == OPTIONAL

    # Required and suggested both reach the queue; optional does not.
    assert BY_KEY["county"].asked and BY_KEY["owner"].asked
    assert not BY_KEY["meta.zip"].asked
    assert "owner" in ASKED and "meta.zip" not in ASKED

    # Only required means a record without it is incomplete.
    assert BY_KEY["county"].required
    assert not BY_KEY["owner"].required


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_suggested_field_is_still_asked_about(mo, editor):
    """The 894 records with no owner keep their question. What changed is
    that the schema now agrees they should have one."""
    ownerless = Source.objects.create(
        id="s-noowner",
        host="noowner.example",
        host_norm="noowner.example",
        canonical_name="No Owner",
        city="Columbia",
        county="Boone",
        type="digital native",
        meta={"state": "MO"},
    )
    assert "owner_missing" in _scanned(mo, ownerless)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_says_which_of_the_three(client, admin_user):
    from accounts.models import DATADESK, Grant

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    page = client.get("/review/schema/").content.decode()
    assert "Required" in page and "Suggested" in page and "Optional" in page
    assert "the queue asks" in page


def test_the_spellings_are_words_and_agree_with_each_other():
    """Seeded from whichever spelling the most records happened to carry,
    they came out inconsistent: `digital native` beside `video_broadcast`.
    So the queue proposed an underscore for one kind and a space for
    another, and read as though the underscore were the right form of that
    word.

    Nothing reads the underscore. The only code comparing against
    `video_broadcast` is the crawler's coverage-radius calculation, which
    reads a legacy `sources/publinks.csv` rather than this column.
    """
    from datasets.publishers import GROUPED_VALUES

    for vocabulary, groups in GROUPED_VALUES.items():
        for key, _label, spelling, _covered in groups:
            assert "_" not in spelling, f"{vocabulary}:{key} proposes {spelling!r}"
            assert spelling == spelling.lower(), f"{vocabulary}:{key}"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_record_spelt_with_an_underscore_is_offered_the_words(mo, editor):
    """Which is the whole point of the flag: two spellings of one kind
    count as two."""
    from datasets.terms import forget

    forget()
    odd = Source.objects.create(
        id="s-tv",
        host="tv.example",
        host_norm="tv.example",
        canonical_name="A Station",
        city="Columbia",
        county="Boone",
        type="video_broadcast",
        meta={"state": "MO"},
    )
    flags = _scanned(mo, odd)
    assert flags["type_spelling"].proposed_value == "video broadcast"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_vocabulary_field_is_fixed_from_its_words(client, editor, publisher):
    """Typing is how `digital_native` gets written beside `digital native`
    in the first place. A queue whose fix box invites it is a queue
    creating the defect it exists to clear."""
    p = _proposal(publisher, "type", "video_broadcast", "video broadcast")
    page = client.get(URL).content.decode()

    # A menu of what the field may hold, not a box to type into.
    assert f'<select class="fixval" name="v-{p.pk}"' in page
    for word in ("video broadcast", "audio broadcast", "digital native"):
        assert f'<option value="{word}">' in page
    # And not the words that merely mean one: a fix writes what the record
    # should say, and `tv` is what records say rather than what they
    # should hold.
    assert '<option value="tv">' not in page

    client.post(URL, {f"d-{p.pk}": "fix", f"v-{p.pk}": "audio broadcast"})
    publisher.refresh_from_db()
    assert publisher.type == "audio broadcast"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_value_no_menu_could_produce_is_refused(client, editor, publisher):
    """The page offers a menu, so this is a stale page, a second tab or a
    posted form. Refused rather than written: the alternative is the queue
    writing the defect it exists to clear.

    Left pending rather than rejected, because nobody decided anything --
    and the decisions beside it still go through.
    """
    bad = _proposal(publisher, "type", "video_broadcast", "video broadcast")
    beside = _proposal(publisher, "city", "Columbia", "Colombia")

    client.post(
        URL,
        {
            f"d-{bad.pk}": "fix",
            f"v-{bad.pk}": "televisual broadcasting",
            f"d-{beside.pk}": "reject",
        },
    )
    publisher.refresh_from_db()
    assert publisher.type != "televisual broadcasting"
    bad.refresh_from_db()
    beside.refresh_from_db()
    assert bad.state == ChangeProposal.PENDING
    assert beside.state == ChangeProposal.REJECTED


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_free_text_field_still_takes_free_text(client, editor, publisher):
    """Most fields have no vocabulary and a city is one of them: the
    gazetteer knows far more names than any list here would."""
    p = _proposal(publisher, "city", "Columbia", "Colombia")
    page = client.get(URL).content.decode()
    assert f'<input type="text" class="fixval" name="v-{p.pk}"' in page

    client.post(URL, {f"d-{p.pk}": "fix", f"v-{p.pk}": "Columbia Heights"})
    publisher.refresh_from_db()
    assert publisher.city == "Columbia Heights"


def test_keeping_a_value_inside_meta_settles_the_question(mo, editor, client):
    """A decision only has to be remembered when the defect survives it.

    Accepting a spelling fix rewrites the value, so nothing flags on the
    next scan whether or not the decision was remembered. Keeping the
    value is the case that matters: the record still reads the way it did,
    the check still fires, and the only thing stopping the question coming
    back is the record of somebody having answered it.

    Two places check that, and both read the field with
    `getattr(source, field)`. That is not an attribute for
    `meta.frequency`, so both answered "" -- which never matches the value
    somebody settled on. Every key inside `meta` was re-asked on every
    scan, for as long as the record kept its value.
    """
    from django.core.management import call_command

    source = Source.objects.create(
        id="s-freq",
        host="freq.example",
        host_norm="freq.example",
        canonical_name="A Weekly",
        city="Columbia",
        county="Boone",
        type="print native",
        # 'Broadcast' is not a frequency. The queue proposes nothing for
        # it, so a reviewer either types a value or keeps what is there.
        meta={"state": "MO", "frequency": "Broadcast"},
    )
    flags = _scanned(mo, source)
    assert "frequency_indistinct" in flags

    # Kept: the reviewer looked and left it alone.
    client.post(URL, {f"d-{flags['frequency_indistinct'].pk}": "reject"})
    source.refresh_from_db()
    assert (source.meta or {}).get("frequency") == "Broadcast"

    # The defect is still there and the check still fires. What must not
    # happen is being asked about it again.
    ChangeProposal.objects.filter(state=ChangeProposal.PENDING).delete()
    call_command("scan_sources", dataset=mo.slug)
    again = {
        p.flag
        for p in ChangeProposal.objects.filter(record_id=source.id, state="pending")
    }
    assert "frequency_indistinct" not in again, "the answered question came back"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_settled_question_inside_meta_is_swept_from_the_queue(mo, editor, client):
    """The other half: a question already sitting in the queue when the
    decision was made. `_retire_settled` read the field the same wrong
    way, so it swept the columns and never the keys."""
    from django.core.management import call_command

    source = Source.objects.create(
        id="s-freq2",
        host="freq2.example",
        host_norm="freq2.example",
        canonical_name="Another One",
        city="Columbia",
        county="Boone",
        type="print native",
        meta={"state": "MO", "frequency": "Broadcast"},
    )
    flags = _scanned(mo, source)
    client.post(URL, {f"d-{flags['frequency_indistinct'].pk}": "reject"})

    # A second copy of the same question, as a scan running while somebody
    # was deciding would leave behind.
    ChangeProposal.objects.create(
        target="sources",
        record_id=source.id,
        record_label=source.host_norm,
        dataset=mo.slug,
        field="meta.frequency",
        flag="frequency_indistinct",
        current_value="Broadcast",
        proposed_value="",
        state=ChangeProposal.PENDING,
    )
    call_command("scan_sources", dataset=mo.slug)
    assert not ChangeProposal.objects.filter(
        record_id=source.id, field="meta.frequency", state="pending"
    ).exists(), "the queue kept a question that had been answered"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_report_does_not_throw_away_the_decisions_beside_it(
    client, editor, publisher
):
    """The mechanism worked until the fields changed under it.

    `value_malformed` names several fields at once -- a ZIP that is not a
    ZIP, a host that is not a host -- so it carries no single field, and
    the empty name went into the batch as a field to write. The write
    boundary refuses that, and refusing is all-or-nothing: one of these in
    a submission threw away every decision beside it, which is why
    answering anything in that queue appeared to do nothing.

    Seven sit in one dataset's queue in production, which is the queue
    somebody was working when this stopped sticking.
    """
    report = _proposal(publisher, "", "", "")
    report.flag = "value_malformed"
    report.detail = "zip code: '6404' is not a ZIP code"
    report.save(update_fields=["flag", "detail"])
    ordinary = _proposal(publisher, "owner", "", "CherryRoad Media")

    client.post(URL, {f"d-{report.pk}": "accept", f"d-{ordinary.pk}": "accept"})

    publisher.refresh_from_db()
    assert publisher.owner == "CherryRoad Media", "the decision beside it was lost"
    report.refresh_from_db()
    ordinary.refresh_from_db()
    assert ordinary.state == ChangeProposal.ACCEPTED
    # Answered and closed, with nothing written: the value to put right is
    # on the record, and this is the queue saying so.
    assert report.state == ChangeProposal.ACCEPTED


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_submission_that_lands_as_nothing_says_so(client, editor, publisher):
    """It redirected in silence, so "the page lost my decisions" and "it
    worked" looked exactly the same -- and the queue coming back with the
    same questions was the only evidence either way."""
    page = client.post(URL, {"nothing": "here"}, follow=True).content.decode()
    assert "Nothing was submitted" in page


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_comes_back_as_it_was_being_worked(client, editor, publisher):
    """Every decision redirected to the bare queue, so somebody working
    one directory and one flag was returned to 3,645 questions across four
    states -- with the record they had just answered on that page again,
    carrying the questions they had not answered yet. That reads as a
    decision that did not take."""
    p = _proposal(publisher, "owner", "", "CherryRoad Media")
    response = client.post(
        f"{URL}?dataset=mo&flag=owner_missing&state=pending",
        {f"d-{p.pk}": "accept"},
    )
    assert response.status_code == 302
    assert "dataset=mo" in response["Location"]
    assert "flag=owner_missing" in response["Location"]


def test_a_word_added_reaches_the_queue_without_a_record_moving(mo, editor):
    """The schema page exists so a vocabulary can change without a deploy.
    A change nothing acts on is a page that lies about what it does.

    `--if-changed` skips a dataset whose stamp has not moved, and the
    stamp covered the checks and the records and not the words those
    checks read. So adding a word left every dataset's stamp identical,
    the nightly scan skipped them all, and the edit reached the queue only
    when something else happened to move a record.
    """
    from datasets.models import VocabularyTerm
    from datasets.terms import forget
    from review.proposals import sources_stamp

    source = Source.objects.create(
        id="s-word",
        host="word.example",
        host_norm="word.example",
        canonical_name="A Podcast",
        city="Columbia",
        county="Boone",
        type="podcast",
        meta={"state": "MO"},
    )
    from explorer.models import DatasetSource

    DatasetSource.objects.create(id="ds-word", dataset=mo, source=source)

    before = sources_stamp(mo.slug)
    VocabularyTerm.objects.create(
        vocabulary="publisher_type", value="podcast", label="Podcast"
    )
    forget("publisher_type")
    assert sources_stamp(mo.slug) != before, "the word did not move the stamp"

    # And retiring one moves it too, so the question comes back.
    after_adding = sources_stamp(mo.slug)
    VocabularyTerm.objects.filter(vocabulary="publisher_type", value="podcast").update(
        retired=True
    )
    forget("publisher_type")
    assert sources_stamp(mo.slug) != after_adding, "retiring did not move the stamp"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_changing_a_spelling_moves_the_stamp(mo, editor):
    """The same for what a kind is recorded as: change it and every record
    spelt the old way is a question, and none of them moved."""
    from datasets.models import VocabularyTerm
    from datasets.terms import forget
    from review.proposals import sources_stamp

    before = sources_stamp(mo.slug)
    VocabularyTerm.objects.filter(
        vocabulary="publisher_type", spelling="video broadcast"
    ).update(spelling="video-broadcast")
    forget("publisher_type")
    assert sources_stamp(mo.slug) != before


# --- paywalls ----------------------------------------------------------------


def test_a_secret_is_named_after_the_publisher():
    """Matched against the eight that already exist, because the crawler
    reads them by that name: `www.` comes off and dots become dashes."""
    from review.credentials import secret_name_for

    assert secret_name_for("www.spokesman.com") == "publisher-auth-spokesman-com"
    assert secret_name_for("ptleader.com") == "publisher-auth-ptleader-com"
    assert (
        secret_name_for("www.pendoreillerivervalley.com")
        == "publisher-auth-pendoreillerivervalley-com"
    )
    assert (
        secret_name_for("www.union-bulletin.com") == "publisher-auth-union-bulletin-com"
    )


def test_credentials_are_written_as_the_crawler_reads_them():
    """A JSON object under `versions/latest`, which is what
    `authenticated_login.py` fetches. Nothing here changes for it."""
    import json

    from review.credentials import store

    class FakeClient:
        def __init__(self):
            self.created, self.versions = [], []

        def create_secret(self, request):
            self.created.append(request["secret_id"])

        def add_secret_version(self, request):
            self.versions.append(
                (request["parent"], json.loads(request["payload"]["data"]))
            )

    client = FakeClient()
    name = store(
        "www.spokesman.com", {"username": "a@b.com", "password": "hunter2"}, client
    )
    assert name == "publisher-auth-spokesman-com"
    assert client.created == ["publisher-auth-spokesman-com"]
    parent, payload = client.versions[0]
    assert parent.endswith("/secrets/publisher-auth-spokesman-com")
    assert payload == {"username": "a@b.com", "password": "hunter2"}


def test_a_secret_that_exists_gets_a_version_not_a_refusal():
    """Replacing a password is a new version of the same secret, which is
    what the crawler asks for when it reads `versions/latest`."""
    from review.credentials import store

    class Existing:
        def __init__(self):
            self.versions = []

        def create_secret(self, request):
            raise Exception("409 Secret already exists")

        def add_secret_version(self, request):
            self.versions.append(request["parent"])

    client = Existing()
    assert store("ptleader.com", {"username": "u", "password": "p"}, client)
    assert client.versions, "the new password was not stored"


def test_a_refusal_says_what_refused():
    """A permission this account does not hold is the likeliest failure
    and the one somebody can act on."""
    import pytest as _pytest

    from review.credentials import CredentialError, store

    class Denied:
        def create_secret(self, request):
            raise Exception("403 Permission 'secretmanager.secrets.create' denied")

        def add_secret_version(self, request):  # pragma: no cover
            raise AssertionError("should not be reached")

    with _pytest.raises(CredentialError) as raised:
        store("x.example", {"username": "u"}, Denied())
    assert "denied" in str(raised.value)

    # And nothing to store is refused before any of that.
    with _pytest.raises(CredentialError):
        store("x.example", {"username": "", "password": ""}, Denied())


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_paywall_page_ranks_by_what_is_being_lost(client, admin_user, mo):
    """The pipeline already knows which publishers it cannot read: an
    article a paywall stopped is skipped with a paywall reason, and 57
    publishers have those in production while none was marked. The page
    ranks them by how many, because that is the size of the hole each one
    leaves."""
    from accounts.models import DATADESK, Grant
    from explorer.models import Article, ArticleEnrichment, CandidateLink, DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)

    made = []
    for i, (host, stubs) in enumerate((("big.example", 3), ("small.example", 1))):
        source = Source.objects.create(
            id=f"s-pw{i}", host=host, host_norm=host, canonical_name=host.title()
        )
        DatasetSource.objects.create(id=f"ds-pw{i}", dataset=mo, source=source)
        link = CandidateLink.objects.create(
            id=f"cl-pw{i}", url=f"https://{host}/a", source=source
        )
        for n in range(stubs):
            article = Article.objects.create(
                id=f"a-pw{i}-{n}", status="ok", candidate_link=link
            )
            # The enrichment is keyed by its article, not an id of its own.
            ArticleEnrichment.objects.create(
                article=article, skip_reason="paywall_stub"
            )
        made.append(source)

    page = client.get("/review/paywalls/").content.decode()
    assert "Publisher paywalls" in page
    # Ranked: the one losing three articles is above the one losing one.
    assert page.index("Big.Example") < page.index("Small.Example")
    assert "4 articles have been lost" in page


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_paywalls_are_shown_one_directory_at_a_time(client, admin_user, mo):
    """Fifty-seven publishers across four states is a list nobody works end
    to end, and whose paywalls are worth paying for is a question somebody
    asks about one directory."""
    from accounts.models import DATADESK, Grant
    from explorer.models import Dataset, DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)

    other = Dataset.objects.create(id="d-vt2", slug="vermont", label="Vermont")
    for i, (dataset, host) in enumerate(((mo, "mo.example"), (other, "vt.example"))):
        source = Source.objects.create(
            id=f"s-dir{i}",
            host=host,
            host_norm=host,
            canonical_name=host.split(".")[0].upper(),
            has_paywall=True,
        )
        DatasetSource.objects.create(id=f"ds-dir{i}", dataset=dataset, source=source)

    both = client.get("/review/paywalls/").content.decode()
    assert "MO" in both and "VT" in both
    assert 'value="vermont"' in both

    one = client.get("/review/paywalls/?dataset=vermont").content.decode()
    assert "VT" in one
    assert "mo.example" not in one


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_stored_credential_never_comes_back(client, admin_user, mo, monkeypatch):
    """The page can say a secret exists and what it is called. That is all
    it can say: a console that could read a password back is a console
    that leaks one."""
    from accounts.models import DATADESK, Grant
    from explorer.models import DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-cred",
        host="paywalled.example",
        host_norm="paywalled.example",
        canonical_name="The Paywalled",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-cred", dataset=mo, source=source)

    stored = {}
    monkeypatch.setattr(
        "review.credentials.store",
        lambda host, fields, client=None: stored.update(fields)
        or "publisher-auth-paywalled-example",
    )
    client.post(
        "/review/paywalls/",
        {
            "source_id": source.id,
            "has_paywall": "1",
            "username": "reporter@example.com",
            "password": "hunter2",
        },
    )
    source.refresh_from_db()
    assert source.auth_secret_name == "publisher-auth-paywalled-example"
    assert stored == {"username": "reporter@example.com", "password": "hunter2"}

    page = client.get("/review/paywalls/").content.decode()
    assert "publisher-auth-paywalled-example" in page
    assert "hunter2" not in page, "the console read a password back"
    assert "reporter@example.com" not in page


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reviewer_reaches_the_paywalls_of_their_datasets(client, editor, mo):
    """Which publishers are behind a paywall, what a subscription costs
    and where to sign in are the same kind of judgement as the rest of
    the queue, made about the datasets that person already reviews."""
    from explorer.models import DatasetSource

    source = Source.objects.create(
        id="s-rev",
        host="rev.example",
        host_norm="rev.example",
        canonical_name="Reviewable",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-rev", dataset=mo, source=source)

    page = client.get("/review/paywalls/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "Reviewable" in body
    # The rest of the page is theirs: the price, the sign-in page, the
    # ruling-out and the report.
    assert 'name="subscription_cost"' in body
    assert 'name="no_paywall"' in body
    assert "format=csv" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reviewer_does_not_store_credentials(client, editor, mo):
    """Storing one writes a secret into the crawler's project, where the
    extractor reads it. Refused rather than ignored: a credential
    somebody typed and believes is stored is worse than one they were
    told to hand to an administrator."""
    from explorer.models import DatasetSource

    source = Source.objects.create(
        id="s-nocred",
        host="nocred.example",
        host_norm="nocred.example",
        canonical_name="No Credentials Here",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-nocred", dataset=mo, source=source)

    body = client.get("/review/paywalls/").content.decode()
    assert 'name="password"' not in body
    assert "no sign-in stored" in body

    response = client.post(
        "/review/paywalls/",
        {
            "source_id": source.id,
            "username": "reviewer",
            "password": "hunter2",
            "subscription_cost": "5",
            "subscription_period": "monthly",
        },
        follow=True,
    )
    source.refresh_from_db()
    assert not source.auth_secret_name, "a reviewer stored a credential"
    # And is told, with the rest of the row saved rather than thrown away.
    assert "stored by an administrator" in response.content.decode()
    assert str(source.subscription_cost) == "5.00"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_paywalls_filter_by_whether_the_sign_in_is_automated(client, editor, mo):
    """Two halves of different work: one needs a subscription bought and a
    credential stored, the other is being read today."""
    from explorer.models import DatasetSource

    for i, (host, automated) in enumerate(
        (("auto.example", True), ("manual.example", False))
    ):
        source = Source.objects.create(
            id=f"s-si{i}",
            host=host,
            host_norm=host,
            canonical_name=host.split(".")[0].title(),
            has_paywall=True,
            requires_login=automated,
        )
        DatasetSource.objects.create(id=f"ds-si{i}", dataset=mo, source=source)

    # By host, because "Auto" is also the theme switcher's third button.
    both = client.get("/review/paywalls/").content.decode()
    assert "auto.example" in both and "manual.example" in both

    waiting = client.get("/review/paywalls/?sign_in=manual").content.decode()
    assert "manual.example" in waiting and "auto.example" not in waiting

    working = client.get("/review/paywalls/?sign_in=automated").content.decode()
    assert "auto.example" in working and "manual.example" not in working

    # The report follows the page, and is named after what it holds.
    report = client.get("/review/paywalls/?format=csv&sign_in=manual")
    assert 'filename="paywalls-manual.csv"' in report["Content-Disposition"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_card_opens_the_record_it_asks_about(client, editor, publisher):
    """A reviewer looking at four questions about a publisher can often
    answer them by editing it, and had to leave the queue, find the record
    in the dataset admin, edit it, and come back to a page that no longer
    knew what they had been doing.

    An ordinary link to the ordinary edit page, so it works with no
    JavaScript and for anybody who opens it in a tab. The dialog is what
    the script adds.
    """
    _proposal(publisher, "owner", "", "CherryRoad Media")
    page = client.get(URL).content.decode()
    assert f'href="/manage/sources/{publisher.id}/"' in page
    assert 'class="rec-edit"' in page
    # And the dialog is added rather than assumed: the link stands alone,
    # and the script that upgrades it is a file both review pages load.
    assert "js/record-editor.js" in page


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_paywall_with_no_sign_in_is_a_question(mo, editor):
    """Marking a publisher as paywalled is only half the job: until a
    credential is stored, every article behind it stays unread. The queue
    asks, and proposes nothing -- a password is not a value it can hold,
    and the detail says where to put one."""
    locked = Source.objects.create(
        id="s-locked",
        host="locked.example",
        host_norm="locked.example",
        canonical_name="The Locked Gazette",
        city="Columbia",
        county="Boone",
        type="print native",
        has_paywall=True,
        meta={"state": "MO"},
    )
    flags = _scanned(mo, locked)
    assert "credentials_missing" in flags
    assert flags["credentials_missing"].proposed_value == ""
    assert "paywall page" in flags["credentials_missing"].detail

    # Once a credential is stored the question is gone.
    locked.auth_secret_name = "publisher-auth-locked-example"
    locked.save(update_fields=["auth_secret_name"])
    ChangeProposal.objects.filter(record_id=locked.id).delete()
    assert "credentials_missing" not in _scanned_again(mo, locked)


def _scanned_again(dataset, source):
    """Re-scan a record whose membership row already exists."""
    from django.core.management import call_command

    call_command("scan_sources", dataset=dataset.slug)
    return {p.flag: p for p in ChangeProposal.objects.filter(record_id=source.id)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_publisher_nobody_marked_is_not_asked_about(mo, editor):
    """The flag is about the record, not the evidence: a publisher whose
    articles hit paywalls but which nobody has marked belongs on the
    paywall page, where the evidence is, rather than in the queue."""
    unmarked = Source.objects.create(
        id="s-unmarked",
        host="unmarked.example",
        host_norm="unmarked.example",
        canonical_name="Unmarked",
        city="Columbia",
        county="Boone",
        type="print native",
        meta={"state": "MO"},
    )
    assert "credentials_missing" not in _scanned(mo, unmarked)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_paywall_row_opens_the_record_and_the_publication(client, admin_user, mo):
    """The name opens the record, because what a reviewer decides here is
    often decided by editing the publisher. The host opens the
    publication, because "is this behind a paywall and what does it cost"
    is answered by going and looking."""
    from accounts.models import DATADESK, Grant
    from explorer.models import DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-row",
        host="www.example-news.com",
        host_norm="www.example-news.com",
        canonical_name="Example News",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-row", dataset=mo, source=source)

    page = client.get("/review/paywalls/").content.decode()
    assert f'href="/manage/sources/{source.id}/"' in page
    assert 'class="rec-edit"' in page
    assert 'href="https://www.example-news.com"' in page
    assert 'target="_blank"' in page
    # The host is a web address, not a row heading: a `th` would set it
    # bold, and it is not a title.
    assert '<th scope="row">' not in page.split("<tbody>")[1]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_ruling_out_a_paywall_takes_it_off_the_page(client, admin_user, mo):
    """The page already asserts these are paywalled -- the extractor could
    not read them past one -- so the box is the exception. Ticking it says
    this publisher is not paywalled after all, and saving takes it off the
    page for good.

    `has_paywall` cannot say that by itself: false is what all 1,149
    records say before anybody has looked, so it means "nobody has
    decided" and "there is no paywall" at once, and the page would ask
    about the same publisher for ever.
    """
    from accounts.models import DATADESK, Grant
    from explorer.models import (
        Article,
        ArticleEnrichment,
        CandidateLink,
        DatasetSource,
    )

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-ev", host="ev.example", host_norm="ev.example", canonical_name="Evidence"
    )
    DatasetSource.objects.create(id="ds-ev", dataset=mo, source=source)
    link = CandidateLink.objects.create(
        id="cl-ev", url="https://ev.example/a", source=source
    )
    article = Article.objects.create(id="a-ev", status="ok", candidate_link=link)
    ArticleEnrichment.objects.create(article=article, skip_reason="paywall_stub")

    assert "Evidence" in client.get("/review/paywalls/").content.decode()

    client.post("/review/paywalls/", {"source_id": source.id, "no_paywall": "1"})
    source.refresh_from_db()
    assert source.has_paywall is False
    assert "Evidence" not in client.get("/review/paywalls/").content.decode()

    # And it comes back by saying so on the record, which is the stronger
    # statement: the record says what a publisher is.
    source.has_paywall = True
    source.save(update_fields=["has_paywall"])
    assert "Evidence" in client.get("/review/paywalls/").content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_saving_without_ruling_it_out_confirms_the_paywall(client, admin_user, mo):
    """Leaving the box alone and saving is confirming what the page says,
    which is what recording a price or a login page means."""
    from accounts.models import DATADESK, Grant
    from explorer.models import DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-confirm", host="c.example", host_norm="c.example", canonical_name="Conf"
    )
    DatasetSource.objects.create(id="ds-confirm", dataset=mo, source=source)

    client.post(
        "/review/paywalls/",
        {
            "source_id": source.id,
            "subscription_cost": "5",
            "subscription_period": "monthly",
        },
    )
    source.refresh_from_db()
    assert source.has_paywall is True
    assert str(source.subscription_cost) == "5.00"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_price_is_recorded_where_it_is_decided(client, admin_user, mo):
    """Somebody is looking at the site to answer whether it has a paywall
    at all, and what a subscription costs is on the same screen. The same
    validator as the record page, so the two cannot disagree about what an
    amount is."""
    from accounts.models import DATADESK, Grant
    from explorer.models import DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-price",
        host="price.example",
        host_norm="price.example",
        canonical_name="Priced",
    )
    DatasetSource.objects.create(id="ds-price", dataset=mo, source=source)

    client.post(
        "/review/paywalls/",
        {
            "source_id": source.id,
            "has_paywall": "1",
            "subscription_cost": "$9.99",
            "subscription_period": "monthly",
            "login_url": "https://price.example/subscribe",
        },
    )
    source.refresh_from_db()
    assert source.has_paywall is True
    assert str(source.subscription_cost) == "9.99"
    assert source.subscription_period == "monthly"
    assert source.login_url == "https://price.example/subscribe"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_amount_with_no_period_is_refused_here_too(client, admin_user, mo):
    """$12 a month and $12 a year are different subscriptions, and the
    page says so rather than writing a number nobody can read."""
    from accounts.models import DATADESK, Grant
    from explorer.models import DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)
    source = Source.objects.create(
        id="s-noperiod",
        host="np.example",
        host_norm="np.example",
        canonical_name="No Period",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-np", dataset=mo, source=source)

    client.post(
        "/review/paywalls/",
        {"source_id": source.id, "has_paywall": "1", "subscription_cost": "12"},
    )
    source.refresh_from_db()
    assert source.subscription_cost is None
    page = client.get("/review/paywalls/").content.decode()
    assert "monthly or annual" in page


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_paywalls_export_as_a_report(client, admin_user, mo):
    """A report of one directory's paywalls is what somebody takes to
    whoever decides what to subscribe to, so it carries the directory
    being looked at and the same order the page shows."""
    import csv as csv_module
    import io

    from accounts.models import DATADESK, Grant
    from explorer.models import Dataset, DatasetSource

    Grant.objects.get_or_create(user=admin_user, app=DATADESK, scope="", role="admin")
    client.force_login(admin_user)

    other = Dataset.objects.create(id="d-wa", slug="washington", label="Washington")
    for i, (dataset, host, name, cost) in enumerate(
        (
            (mo, "mo-paper.example", "Missouri Paper", "12.99"),
            (other, "wa-paper.example", "Washington Paper", "9.99"),
        )
    ):
        source = Source.objects.create(
            id=f"s-csv{i}",
            host=host,
            host_norm=host,
            canonical_name=name,
            has_paywall=True,
            subscription_cost=cost,
            subscription_period="monthly",
            login_url=f"https://{host}/login",
        )
        DatasetSource.objects.create(id=f"ds-csv{i}", dataset=dataset, source=source)

    response = client.get("/review/paywalls/?format=csv")
    assert response.status_code == 200
    assert "text/csv" in response["Content-Type"]
    assert 'filename="paywalls.csv"' in response["Content-Disposition"]
    rows = list(csv_module.reader(io.StringIO(response.content.decode())))
    assert rows[0] == [
        "publisher",
        "url",
        "login page",
        "subscription cost",
        "per",
        "articles lost",
    ]
    body = {r[0]: r for r in rows[1:]}
    assert body["Missouri Paper"][1] == "https://mo-paper.example"
    assert body["Missouri Paper"][2] == "https://mo-paper.example/login"
    assert body["Missouri Paper"][3] == "12.99"
    # The period travels with the amount, or the number cannot be read.
    assert body["Missouri Paper"][4] == "monthly"

    # Filtered the same way the page is, and named after the directory.
    one = client.get("/review/paywalls/?format=csv&dataset=washington")
    assert 'filename="paywalls-washington.csv"' in one["Content-Disposition"]
    text = one.content.decode()
    assert "Washington Paper" in text
    assert "Missouri Paper" not in text


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_paywall_filter_survives_a_save(client, editor, mo):
    """Saving redirected to the bare page, so somebody working one
    directory was returned to every dataset -- with the record they had
    just answered somewhere in it. That reads as a save that did not take,
    and it means re-choosing the filter after every row."""
    from explorer.models import DatasetSource

    source = Source.objects.create(
        id="s-keep",
        host="keep.example",
        host_norm="keep.example",
        canonical_name="Kept",
        has_paywall=True,
    )
    DatasetSource.objects.create(id="ds-keep", dataset=mo, source=source)

    response = client.post(
        f"/review/paywalls/?dataset={mo.slug}&sign_in=manual",
        {
            "source_id": source.id,
            "subscription_cost": "7",
            "subscription_period": "annual",
        },
    )
    assert response.status_code == 302
    assert f"dataset={mo.slug}" in response["Location"]
    assert "sign_in=manual" in response["Location"]

    # Including when the save is refused, or correcting the amount means
    # finding the directory again first.
    refused = client.post(
        f"/review/paywalls/?dataset={mo.slug}&sign_in=manual",
        {"source_id": source.id, "subscription_cost": "12"},
    )
    assert f"dataset={mo.slug}" in refused["Location"]
