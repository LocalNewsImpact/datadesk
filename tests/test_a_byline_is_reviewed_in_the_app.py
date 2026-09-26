"""A byline is reviewed in the app, and the two reports read the decisions.

The crawler computes which byline strings show a defect and writes them to
`byline_review_candidates`. Until now they came out as a CSV, which meant the
review had no way to happen: a decision has to be recorded somewhere the
crawler reads, and a CSV is not that.

This is the page. One decision per string, written to `byline_normalizations`,
and the string leaves the queue as it is decided. `articles.author` is NOT
written here -- the crawler owns that column, and a review request that updated
thousands of rows would be a write nobody asked for.

Co-authors are deliberately absent from the queue and present in the reports:
"Alyssa Mueller, Marcus Off" is two reporters on one story, and the reports
count two people without anybody deciding anything about it.
"""

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from explorer.models import (
    Article,
    BylineNormalization,
    BylineReviewCandidate,
    CandidateLink,
    Dataset,
    Source,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def editor():
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


def _candidate(raw, **kwargs):
    fields = {
        "id": f"c-{abs(hash(raw)) % 10**8}",
        "dataset_id": "d-mo",
        "raw_byline": raw,
        "signal": "SPELLING_VARIANT",
        "signal_label": "looks like a misspelling",
        "signals": ["SPELLING_VARIANT"],
        "proposed": [raw],
        "variants": [],
        "differs_by": [],
        "articles": 1,
        "hosts": ["one.example"],
        "owners": [],
    }
    fields.update(kwargs)
    return BylineReviewCandidate.objects.create(**fields)


def _article(article_id, author, host_id="s-1", status="enriched"):
    link = CandidateLink.objects.create(
        id=f"l-{article_id}",
        url=f"https://one.example/{article_id}",
        source_id=host_id,
        status="article",
    )
    return Article.objects.create(
        id=article_id,
        candidate_link=link,
        dataset_id="d-mo",
        url=link.url,
        author=author,
        status=status,
    )


@pytest.fixture
def page(crawler_schema, editor):
    Dataset.objects.create(id="d-mo", slug="Mizzou-Missouri-State", label="Missouri")
    Source.objects.create(
        id="s-1",
        host="one.example",
        host_norm="one.example",
        canonical_name="The One",
        owner="Missourian Publishing",
    )
    Source.objects.create(
        id="s-2",
        host="two.example",
        host_norm="two.example",
        canonical_name="The Two",
        owner="Missourian Publishing",
    )
    # A third, unrelated newsroom: a byline on several is not several instances
    # of one fact, and the ruling has to be able to differ between them.
    Source.objects.create(
        id="s-3",
        host="three.example",
        host_norm="three.example",
        canonical_name="The Three",
        owner="Someone Else Entirely",
    )
    client = Client()
    client.force_login(editor)
    return client


def _queue(client, **params):
    params.setdefault("dataset", "Mizzou-Missouri-State")
    return client.get(reverse("review:bylines"), params)


def _submit(client, *rows, **extra):
    """The page's ONE submit, built the way the form builds it.

    Each row is a dict with `raw` and whatever the reviewer answered on it. Keys
    that name a newsroom or a story use a colon -- `"ruling:two.example"`,
    `"edit:a-1"` -- and land as `ruling-<row>-two.example`, `edit-<row>-a-1`.
    """
    data = {"dataset": "Mizzou-Missouri-State", "signal": ""}
    data.update(extra)
    data["row"] = [str(i) for i in range(len(rows))]
    for i, row in enumerate(rows):
        data[f"raw-{i}"] = row["raw"]
        for key, value in row.items():
            if key == "raw":
                continue
            if ":" in key:
                prefix, rest = key.split(":", 1)
                data[f"{prefix}-{i}-{rest}"] = value
            else:
                data[f"{key}-{i}"] = value
    return client.post(reverse("review:bylines"), data)


# --- the queue is reachable, which is the thing that was missing ------------


def test_the_review_section_offers_bylines():
    """In the left nav, under Review, or nobody finds the page."""
    from accounts.sections import SECTION_GROUPS

    urls = [
        section["url"]
        for group in SECTION_GROUPS
        for section in group.get("sections", ())
        if group.get("label") == "Review"
    ]
    assert "review:bylines" in urls


def test_the_queue_shows_a_candidate(page):
    _candidate("Jon Smtih")
    body = _queue(page).content.decode()
    assert "Jon Smtih" in body
    assert "looks like a misspelling" in body


def test_the_queue_names_the_difference_from_the_standard(page):
    """Two spellings that differ only in an accent are indistinguishable in a
    table, and a reviewer who cannot see the difference cannot decide."""
    _candidate("Jose Ramirez", variants=["José Ramírez"], differs_by=["accents"])
    body = _queue(page).content.decode()
    assert "José Ramírez" in body
    assert "accents" in body


def test_one_reason_at_a_time(page):
    _candidate("Jon Smtih")
    _candidate("Sports Desk", signal="NOT_A_PERSON", signal_label="not a person")
    body = _queue(page, signal="NOT_A_PERSON").content.decode()
    assert "Sports Desk" in body
    assert "Jon Smtih" not in body


# --- a decision is recorded, and is the crawler's to apply -----------------


def test_a_fix_records_the_names(page):
    _candidate("Jon Smtih")
    _submit(page, {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"})
    row = BylineNormalization.objects.get(raw_byline="Jon Smtih")
    assert row.decision == "fix"
    assert row.canonical_names == ["Jon Smith"]
    assert row.decided_by == "ed"


def test_a_decided_string_leaves_the_queue(page):
    """So a worked queue empties as it is worked. The crawler rewrites the row
    only if the string still shows a defect, and a decided one does not."""
    _candidate("Jon Smtih")
    _submit(page, {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"})
    assert not BylineReviewCandidate.objects.filter(raw_byline="Jon Smtih").exists()


def test_a_drop_names_nobody(page):
    _candidate("Sports Desk", signal="NOT_A_PERSON", signal_label="not a person")
    _submit(page, {"raw": "Sports Desk", "decision": "drop", "names": "Sports Desk"})
    row = BylineNormalization.objects.get(raw_byline="Sports Desk")
    assert row.decision == "drop"
    # Even though a name was in the box: "not a real name" means the string
    # names no person, and keeping the text would put the desk back into both
    # reports.
    assert row.canonical_names == []


def test_a_fix_with_no_name_is_refused(page):
    """An empty fix stores an empty name list, which is what drop means -- so
    it would silently drop a real reporter."""
    _candidate("Jon Smtih")
    _submit(page, {"raw": "Jon Smtih", "decision": "fix", "names": "   "})
    assert not BylineNormalization.objects.exists()
    assert BylineReviewCandidate.objects.filter(raw_byline="Jon Smtih").exists()


def test_the_article_author_column_is_not_written_here(page):
    """The crawler owns that write. One writer for the column, and a review
    request that updated thousands of rows would be a write nobody asked for."""
    _candidate("Jon Smtih")
    article = _article("a-1", "Jon Smtih")
    _submit(page, {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"})
    article.refresh_from_db()
    assert article.author == "Jon Smtih"


def test_a_dataset_the_reviewer_cannot_reach_is_refused(page):
    Dataset.objects.create(id="d-vt", slug="VT-Community-News", label="Vermont")
    user = User.objects.create_user("narrow", email="n@localnewsimpact.org")
    Grant.objects.create(
        user=user, app=DATADESK, scope="Mizzou-Missouri-State", role="editor"
    )
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("review:bylines"),
        {
            "dataset": "VT-Community-News",
            "raw_byline": "Anyone",
            "decision": "drop",
        },
    )
    assert response.status_code == 400
    assert not BylineNormalization.objects.exists()


# --- the reports, which are what the review is for ------------------------


def _report(client, which):
    return client.get(
        reverse("review:byline_report"),
        {"dataset": "Mizzou-Missouri-State", "report": which},
    )


def test_co_authors_count_as_the_people_they_are(page):
    _article("a-1", "Alyssa Mueller, Marcus Off")
    from review import bylines

    rows = {r["byline"]: r for r in bylines.bylines_with_hosts("d-mo")}
    assert set(rows) == {"Alyssa Mueller", "Marcus Off"}


def test_a_fix_collapses_two_spellings_into_one_byline(page):
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smtih")
    from review import bylines

    bylines.decide("d-mo", "Jon Smtih", bylines.FIX, ["Jon Smith"], None)
    rows = bylines.bylines_with_hosts("d-mo")
    assert [(r["byline"], r["articles"]) for r in rows] == [("Jon Smith", 2)]


def test_a_dropped_string_is_in_neither_report(page):
    _article("a-1", "Sports Desk")
    _article("a-2", "Jon Smith")
    from review import bylines

    bylines.decide("d-mo", "Sports Desk", bylines.DROP, [], None)
    assert [r["byline"] for r in bylines.bylines_with_hosts("d-mo")] == ["Jon Smith"]
    assert [r["bylines"] for r in bylines.hosts_with_bylines("d-mo")] == [1]


def test_only_local_articles_are_counted(page):
    """A byline on a wire story or a paywall stub is not a local reporter's
    byline, and counting it inflates every number on both reports."""
    _article("a-1", "Jon Smith")
    _article("a-2", "AP Staff", status="wire")
    _article("a-3", "Nobody", status="paywall")
    from review import bylines

    assert [r["byline"] for r in bylines.bylines_with_hosts("d-mo")] == ["Jon Smith"]


def test_a_byline_on_two_hosts_names_both(page):
    """Not a defect: a stringer files to several papers, and papers under one
    owner share copy. The report says which, and the reader judges it."""
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smith", host_id="s-2")
    from review import bylines

    row = bylines.bylines_with_hosts("d-mo")[0]
    assert row["hosts"] == ["one.example", "two.example"]
    assert row["owners"] == ["Missourian Publishing"]


def test_the_host_report_counts_people_not_strings(page):
    _article("a-1", "Jon Smith")
    _article("a-2", "Jane Doe")
    _article("a-3", "Jon Smith")
    from review import bylines

    rows = bylines.hosts_with_bylines("d-mo")
    assert [(r["host"], r["bylines"], r["articles"]) for r in rows] == [
        ("one.example", 2, 3)
    ]


def test_both_reports_render(page):
    _article("a-1", "Jon Smith")
    assert "Jon Smith" in _report(page, "bylines").content.decode()
    assert "one.example" in _report(page, "hosts").content.decode()


def test_a_report_downloads_as_csv(page):
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smith", host_id="s-2")
    response = page.get(
        reverse("review:byline_report"),
        {"dataset": "Mizzou-Missouri-State", "report": "bylines", "format": "csv"},
    )
    body = response.content.decode("utf-8-sig")
    assert response["Content-Type"].startswith("text/csv")
    # " | " between hosts: a comma inside a cell reads as a column break to
    # every viewer that is not a CSV parser.
    assert "Jon Smith,2,one.example | two.example" in body


# --- reading the evidence, and excluding a byline that is not local ---------
#
# `cross_owner` is 96 of Mizzou's 148 candidates, and is the softest signal: a
# byline under unrelated owners is legitimate for a stringer and a defect for a
# wire reporter the parser credited as local. Nothing else on the row tells them
# apart, so the stories have to be on the page -- and when the answer is "these
# are wire stories", the stories have to be re-disposed too, or they are
# enriched again next week and the byline comes back.


def test_the_links_open_in_their_own_window(page):
    """Reading a story must not lose a queue page with decisions typed in."""
    _candidate("Jon Smith")
    _article("a-1", "Jon Smith")
    body = _queue(page).content.decode()
    assert 'target="_blank"' in body
    assert "https://one.example/a-1" in body


def test_the_row_links_to_what_we_captured(page):
    _candidate("Jon Smith")
    _article("a-1", "Jon Smith")
    body = _queue(page).content.decode()
    assert reverse("explorer:article_detail", args=["a-1"]) in body


def test_the_exclusions_are_the_extraction_queues_own_words(page):
    """One vocabulary. The two lists drifted once before -- extraction offered
    one word for seventeen things discovery could name."""
    from review import bylines
    from review.dispositions import CONTENT_TYPES

    offered = {t["value"] for t in bylines.exclusion_types()}
    assert "wire" in offered
    assert "obituary" in offered
    assert offered < {t["value"] for t in CONTENT_TYPES}


def test_a_disposition_that_keeps_the_story_is_not_offered(page):
    """ "News" means this IS a story we keep, which is the opposite of excluding
    the byline -- one dropdown must not both exclude and restore."""
    from review import bylines
    from review.dispositions import BAD_CAPTURE

    offered = {t["value"] for t in bylines.exclusion_types()}
    assert "news" not in offered
    assert BAD_CAPTURE not in offered


def test_excluding_re_disposes_every_story(page):
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _article("a-2", "Wire Reporter", host_id="s-2")
    _submit(
        page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": "wire"}
    )
    assert [a.status for a in Article.objects.order_by("id")] == ["wire", "wire"]


def test_excluding_reaches_a_story_at_any_status(page):
    """Stopping at the local statuses leaves the same wire stories at `labeled`
    to be enriched next week, and the byline comes back."""
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter", status="labeled")
    _submit(
        page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": "wire"}
    )
    assert Article.objects.get(id="a-1").status == "wire"


def test_excluding_takes_the_byline_out_of_the_reports(page):
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _article("a-2", "Jon Smith")
    _submit(
        page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": "wire"}
    )
    from review import bylines

    assert [r["byline"] for r in bylines.bylines_with_hosts("d-mo")] == ["Jon Smith"]
    assert not BylineReviewCandidate.objects.filter(raw_byline="Wire Reporter").exists()


def test_excluding_without_saying_what_they_are_is_refused(page):
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _submit(page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": ""})
    assert Article.objects.get(id="a-1").status == "enriched"
    assert not BylineNormalization.objects.exists()


def test_an_excluded_story_carries_the_same_decision_note_as_one_dispositioned(page):
    """Through the extraction queue's own `record`, so an article excluded here
    is indistinguishable from one dispositioned a row at a time."""
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _submit(
        page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": "wire"}
    )
    from review.models import ReviewDecision

    assert ReviewDecision.objects.filter(subject_id="a-1").exists()
    assert Article.objects.get(id="a-1").metadata


def test_an_exclusion_is_born_applied(page):
    """The crawler's nightly apply writes a decision's names onto
    `articles.author`, and an exclusion carries no name -- so left unapplied it
    would blank the byline on every one of these stories. A wire reporter really
    wrote the wire story; the answer is about the stories, and it is already
    carried out."""
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _submit(
        page, {"raw": "Wire Reporter", "decision": "exclude", "content_type": "wire"}
    )
    row = BylineNormalization.objects.get(raw_byline="Wire Reporter")
    assert row.applied_at is not None
    assert row.articles_updated == 1
    assert Article.objects.get(id="a-1").author == "Wire Reporter"


# --- the whole spread, and a replacement across the part that is wrong ------


def test_a_replacement_writes_only_the_picked_stories(page):
    _candidate("Christopher Replogle")
    _article("a-keep", "Christopher Replogle")
    _article("a-fix", "Christopher Replogle", host_id="s-2")
    _submit(
        page,
        {"raw": "Christopher Replogle", "edit:a-fix": "Neal A. Johnson"},
    )
    assert Article.objects.get(id="a-fix").author == "Neal A. Johnson"
    assert Article.objects.get(id="a-keep").author == "Christopher Replogle"


def test_a_replacement_is_audited_and_revertible(page):
    _article("a-fix", "Christopher Replogle")
    from review import bylines

    bylines.replace_on(
        "d-mo",
        "Christopher Replogle",
        ["a-fix"],
        "Neal A. Johnson",
        User.objects.get(username="ed"),
    )
    entry = AuditLogEntry.objects.get(action="byline:replace")
    assert entry.before == {"a-fix": {"author": "Christopher Replogle"}}
    assert entry.after == {"author": "Neal A. Johnson"}


def test_a_replacement_cannot_reach_a_story_with_another_byline(page):
    _article("a-other", "Jon Smith")
    from review import bylines

    with pytest.raises(ValueError):
        bylines.replace_on(
            "d-mo", "Christopher Replogle", ["a-other"], "Neal A. Johnson", None
        )
    assert Article.objects.get(id="a-other").author == "Jon Smith"


# --- the write goes on the write connection ---------------------------------
#
# `crawler` is the READ-ONLY alias: it authenticates as `datadesk_ro`, which
# Postgres refuses every write on. The suite pops `crawler_rw`, so both aliases
# are one sqlite file here and `using("crawler")` on a write passes every test
# and fails in production only, as "permission denied for table
# byline_normalizations". That is exactly how the page shipped.


def test_a_write_never_goes_through_the_read_only_alias(settings):
    """With `crawler_rw` configured -- which is production -- the alias a byline
    write uses must not be the read-only one."""
    from review import bylines

    settings.DATABASES["crawler_rw"] = dict(settings.DATABASES["crawler"])
    try:
        assert bylines.write_alias() == "crawler_rw"
    finally:
        del settings.DATABASES["crawler_rw"]


def test_no_byline_write_names_the_read_only_alias():
    """The source form of the same rule, because the aliases collapse to one
    database in the suite and a hard-coded `using("crawler")` on a write is
    invisible to every behavioural test."""
    import inspect

    from review import bylines

    for name in ("decide", "_decide", "exclude", "replace_on"):
        source = inspect.getsource(getattr(bylines, name))
        assert 'using("crawler")' not in source, (
            f"{name} writes through the read-only alias; it must use "
            "write_alias() so production routes it to crawler_rw"
        )


def test_the_decision_is_labelled_not_a_real_name(page):
    """ "Names nobody" read backwards -- as though the reviewer were naming
    nobody, rather than saying the string does not name anybody. The stored value
    is unchanged, so decisions already recorded keep their meaning."""
    from review import bylines

    _candidate("Admin", signal="NOT_A_PERSON", signal_label="not a person")
    body = _queue(page).content.decode()
    assert "Not a real name" in body
    assert "Names nobody" not in body
    assert bylines.DECISION_LABELS[bylines.DROP] == "not a real name"
    assert bylines.DROP == "drop"


# --- a spelling cluster is one question -------------------------------------
#
# "Bruce E Stidham" and "Bruce E. Stidham" were two rows, each naming the other
# as a variant, and a reviewer answered the same person twice and hoped the two
# answers agreed. On Mizzou there are 14 such clusters covering 28 spellings.


def _cluster(**kwargs):
    group = [
        {
            "name": "Angela Hutschreider",
            "articles": 40,
            "hosts": ["linncountyleader.com"],
            "differs_by": "",
        },
        {
            "name": "Angie Hutschreider",
            "articles": 1,
            "hosts": ["chillicothenews.com"],
            "differs_by": "spelling",
        },
    ]
    return _candidate(
        "Angela Hutschreider",
        signal="SPELLING_VARIANT",
        signal_label="One person, several spellings",
        group=group,
        variants=["Angie Hutschreider"],
        articles=41,
        **kwargs,
    )


def test_the_cluster_shows_every_spelling_with_its_own_count(page):
    """Which spelling is right is judged by comparing those: 40 against 1."""
    _cluster()
    body = _queue(page).content.decode()
    assert "Angie Hutschreider" in body
    assert "chillicothenews.com" in body
    assert 'name="canonical-0"' in body


def test_keeping_one_spelling_records_every_spelling(page):
    """The corpus ends up with one name, and the decisions survive a
    re-extraction that writes an old spelling again."""
    _cluster()
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Angela Hutschreider",
        },
    )
    rows = {r.raw_byline: r for r in BylineNormalization.objects.all()}
    assert rows["Angela Hutschreider"].decision == "accept"
    assert rows["Angie Hutschreider"].decision == "fix"
    assert rows["Angie Hutschreider"].canonical_names == ["Angela Hutschreider"]


def test_the_minority_spelling_can_be_the_one_kept(page):
    """The count is evidence, not the answer. 12 against 3 needs a look, and the
    reviewer may know the smaller one is right."""
    _cluster()
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Angie Hutschreider",
        },
    )
    rows = {r.raw_byline: r for r in BylineNormalization.objects.all()}
    assert rows["Angela Hutschreider"].canonical_names == ["Angie Hutschreider"]
    assert rows["Angie Hutschreider"].decision == "accept"


def test_different_people_is_a_real_answer(page):
    """A reviewer who can see two people but cannot say so gets the pair offered
    again every night."""
    _cluster()
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "__different__",
        },
    )
    rows = {r.raw_byline: r for r in BylineNormalization.objects.all()}
    assert [r.decision for r in rows.values()] == ["accept", "accept"]
    assert rows["Angie Hutschreider"].canonical_names == ["Angie Hutschreider"]


def test_the_whole_cluster_leaves_the_queue(page):
    _cluster()
    _candidate("Angie Hutschreider", signal="SPELLING_VARIANT", signal_label="x")
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Angela Hutschreider",
        },
    )
    assert not BylineReviewCandidate.objects.exists()


def test_a_single_name_still_gets_the_ordinary_form(page):
    """Clustering changes rows that have several spellings and nothing else."""
    _candidate("Jon Smtih")
    body = _queue(page).content.decode()
    assert 'value="cluster"' not in body
    assert 'value="fix"' in body


def test_a_shared_byline_is_named_on_the_row(page):
    """A name beside a co-author is a different question from a name alone."""
    _candidate(
        "Marcus Officer",
        sources=["Alyssa Mueller, Marcus Officer", "Marcus Officer, Jonathan Ketz"],
    )
    body = _queue(page).content.decode()
    assert "Shares a byline" in body
    assert "Alyssa Mueller, Marcus Officer" in body


# --- which newsroom is wrong, on the row ------------------------------------
#
# `cross_owner` is 119 of Mizzou's 138 rows and is the softest signal the queue
# has. The row said only that the condition held: "Show every story" sat inside a
# collapsed "Read 8", and the one outlying story was one unlabelled row among
# eight spread two-per-host. No request in production ever carried `expand=`.


def _replogle(**kwargs):
    """The real shape: 896 stories on one host, one on an unrelated one."""
    for n in range(4):
        _article(f"a-main-{n}", "Christopher Replogle")
    _article("a-out", "Christopher Replogle", host_id="s-2")
    return _candidate(
        "Christopher Replogle",
        signal="CROSS_OWNER",
        signal_label="Same name, unrelated owners",
        articles=5,
        hosts=["one.example", "two.example"],
        **kwargs,
    )


# --- one newsroom is the reporter's; the rest republish ----------------------
#
# Steph Quinn has 112 stories on missouriindependent.com, where she works, and
# 288 across 41 other domains, because States Newsroom copy is syndicated across
# Missouri. Her byline is not wrong on any of them: what is wrong is counting the
# republished ones as that paper's local reporting. Across the Mizzou queue that
# is 125 bylines, 12,179 stories elsewhere, 8,031 not yet wire.


def _syndicated():
    """Her shape: the most stories at home, the rest co-authored elsewhere."""
    for n in range(3):
        _article(f"a-home-{n}", "Steph Quinn")
    _article("a-away-1", "Rudi Keller, Steph Quinn", host_id="s-2")
    _article("a-away-2", "Steph Quinn, Clara Bates", host_id="s-2")
    return _candidate(
        "Steph Quinn",
        signal="CROSS_OWNER",
        signal_label="Same name, unrelated owners",
        articles=5,
        hosts=["one.example", "two.example"],
    )


def test_a_co_authored_byline_counts_towards_its_newsrooms(page):
    """An exact match would say she writes for one newsroom: 41 of her domains
    carry her only inside a string naming three or four reporters."""
    row = _syndicated()
    from review import bylines

    counts = {c["host"]: c["articles"] for c in bylines.host_choices(row)}
    assert counts == {"one.example": 3, "two.example": 2}


def test_a_substring_of_another_name_is_not_a_match(page):
    """`contains` reaches "Rudi Keller, Steph Quinn" from "Steph Quinn", and on
    its own it would reach "Dan Fox" from "Dan"."""
    _article("a-1", "Dan Fox")
    row = _candidate("Dan", signal="NOT_A_PERSON", signal_label="x")
    from review import bylines

    assert bylines.host_choices(row) == []


def test_the_newsrooms_are_listed_biggest_first(page):
    """Usually the reporter's own comes first. A reading aid, not an answer:
    every newsroom is decided on its own."""
    _syndicated()
    from review import bylines

    row = BylineReviewCandidate.objects.get(raw_byline="Steph Quinn")
    assert [c["host"] for c in bylines.host_choices(row)] == [
        "one.example",
        "two.example",
    ]


def test_each_newsroom_is_ruled_on_its_own(page):
    """One republishes, the other has her filing directly. The whole point."""
    _article("a-third", "Steph Quinn, Clara Bates", host_id="s-3")
    _syndicated()
    _submit(
        page,
        {
            "raw": "Steph Quinn",
            "host": ["one.example", "two.example", "three.example"],
            "ruling:two.example": "wire",
        },
    )
    assert Article.objects.get(id="a-away-1").status == "wire"
    assert Article.objects.get(id="a-third").status == "enriched"
    assert Article.objects.get(id="a-home-0").status == "enriched"


def test_the_byline_is_accepted_not_dropped(page):
    """She is a real reporter with a real name. The ruling is about which stories
    are local reporting, and nothing about the name is wrong."""
    _syndicated()
    _submit(
        page,
        {"raw": "Steph Quinn", "host": ["two.example"], "ruling:two.example": "wire"},
    )
    row = BylineNormalization.objects.get(raw_byline="Steph Quinn")
    assert row.decision == "accept"
    assert row.canonical_names == ["Steph Quinn"]
    assert not BylineReviewCandidate.objects.filter(raw_byline="Steph Quinn").exists()


def test_a_story_already_carrying_the_disposition_is_not_written_again(page):
    """So a second pass over a byline costs nothing and the count reported is of
    what actually changed."""
    _syndicated()
    Article.objects.filter(id="a-away-1").update(status="wire")
    from review import bylines

    result = bylines.rule_newsrooms(
        "d-mo",
        "Steph Quinn",
        {"one.example": "", "two.example": "wire"},
        User.objects.get(username="ed"),
    )
    assert result["stories"] == 1
    assert result["kept"] == 1


def test_a_newsroom_that_does_not_carry_the_byline_is_ignored(page):
    """A stale form naming a newsroom the byline has left should not fail the
    ruling on the ones it still has."""
    _syndicated()
    from review import bylines

    result = bylines.rule_newsrooms(
        "d-mo",
        "Steph Quinn",
        {"nowhere.example": "wire", "two.example": "wire"},
        User.objects.get(username="ed"),
    )
    assert result["stories"] == 2


def test_a_ruling_naming_no_real_newsroom_is_refused(page):
    _syndicated()
    response = _submit(
        page,
        {
            "raw": "Steph Quinn",
            "host": ["nowhere.example"],
            "ruling:nowhere.example": "wire",
        },
    )
    assert response.status_code == 400
    assert not BylineNormalization.objects.exists()


def test_a_disposition_that_is_not_one_is_refused(page):
    _syndicated()
    _submit(
        page,
        {
            "raw": "Steph Quinn",
            "host": ["two.example"],
            "ruling:two.example": "something-else",
        },
    )
    assert Article.objects.get(id="a-away-1").status == "enriched"
    assert not BylineNormalization.objects.exists()


def test_a_replacement_alone_records_nothing_against_the_byline(page):
    """`replace_on` is a statement about stories. It says nothing about what the
    string means, which is the page's call when it settles the row (see
    `test_a_row_changed_but_not_decided_is_kept_as_it_stands`)."""
    _candidate("Christopher Replogle")
    _article("a-fix", "Christopher Replogle")
    from review import bylines

    bylines.replace_on(
        "d-mo",
        "Christopher Replogle",
        ["a-fix"],
        "Neal A. Johnson",
        User.objects.get(username="ed"),
    )
    assert not BylineNormalization.objects.exists()


def test_nothing_on_a_cluster_is_preselected(page):
    """The leading spelling used to be, so agreeing was one click. On a page
    submitted whole that would settle every cluster on it, including the ones
    nobody read: the only checked radio is "No decision"."""
    _cluster()
    body = _queue(page).content.decode()
    radios = [
        body[at : at + 200]
        for at in range(len(body))
        if body.startswith('name="canonical-0"', at)
    ]
    assert len(radios) == 4, "no decision, two spellings, different people"
    assert [("checked" in radio.split(">")[0]) for radio in radios] == [
        True,
        False,
        False,
        False,
    ]


def test_a_cluster_left_at_no_decision_is_not_settled(page):
    _cluster()
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "",
        },
    )
    assert not BylineNormalization.objects.exists()
    assert BylineReviewCandidate.objects.exists()


def test_a_spelling_not_offered_is_refused(page):
    """The names being merged are what the answer means, so a canonical that was
    not one of the spellings shown is refused rather than written."""
    _cluster()
    _submit(
        page,
        {
            "raw": "Angela Hutschreider",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Someone Else Entirely",
        },
    )
    assert not BylineNormalization.objects.exists()


def test_a_single_name_still_gets_the_ordinary_decision(page):
    """Clustering changes rows that have several spellings and nothing else."""
    _candidate("Jon Smtih")
    body = _queue(page).content.decode()
    assert 'name="canonical-0"' not in body
    assert 'name="decision-0"' in body


# --- ONE submit for the page --------------------------------------------------
#
# The queue used to be decided a control at a time: a decision, a newsroom
# ruling, an outlier replacement, a cluster -- each its own form, each
# redirecting back to a page that re-read the whole queue. The page is now
# worked 25 rows at a time and disposed of once, at the bottom.


def _post_form(body):
    """The page's form and nothing else on it."""
    start = body.index('<form method="post" id="page-of-bylines"')
    return body[start : body.index("</form>", start)]


def test_the_page_has_one_form_and_one_submit(page):
    for n in range(3):
        _candidate(f"Reporter {n}")
    _syndicated()
    body = _queue(page).content.decode()
    content = body[body.index('<section class="card card-wide">') :]
    assert content.count('<form method="post"') == 1
    assert _post_form(body).count('type="submit"') == 1


def test_the_controls_of_the_old_page_are_gone(page):
    """Each of these was a submit of its own, in one of three places."""
    _replogle()
    body = _queue(page).content.decode()
    for old in (
        "Show every story",
        "Outlying:",
        'value="primary"',
        'value="replace"',
        "Set on the ticked stories",
        "edit this one",
        "wrong byline",
    ):
        assert old not in body, old


def test_a_page_is_twenty_five_rows(page):
    for n in range(30):
        _candidate(f"Reporter {n:02d}")
    body = _queue(page).content.decode()
    assert body.count('name="row"') == 25
    second = _queue(page, page=2).content.decode()
    assert second.count('name="row"') == 5


def test_reading_a_page_is_one_query_for_its_stories(page):
    """Two unindexed `author LIKE '%name%'` scans a row was 50 a page, and a
    page is re-read after every submit."""
    from django.db import connections
    from django.test.utils import CaptureQueriesContext

    for n in range(25):
        name = f"Reporter Number{n}"
        _article(f"a-{n}", name)
        _article(f"b-{n}", f"Other Person, {name}", host_id="s-2")
        _candidate(name, signal="CROSS_OWNER", signal_label="x", articles=2)
    with CaptureQueriesContext(connections["crawler"]) as queries:
        _queue(page)
    scans = [q for q in queries.captured_queries if "LIKE" in q["sql"].upper()]
    assert len(scans) == 1


def test_a_submit_with_nothing_touched_changes_nothing(page):
    _syndicated()
    _candidate("Jon Smtih")
    _submit(
        page,
        {"raw": "Steph Quinn", "decision": "", "host": ["one.example", "two.example"]},
        {"raw": "Jon Smtih", "decision": "", "names": "Jon Smith"},
    )
    assert not BylineNormalization.objects.exists()
    assert {a.status for a in Article.objects.all()} == {"enriched"}
    assert BylineReviewCandidate.objects.count() == 2


def test_one_submit_disposes_of_every_row_answered(page):
    _candidate("Jon Smtih")
    _candidate("Sports Desk", signal="NOT_A_PERSON", signal_label="not a person")
    _candidate("Left Alone")
    _submit(
        page,
        {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"},
        {"raw": "Sports Desk", "decision": "drop"},
        {"raw": "Left Alone", "decision": ""},
    )
    decided = {r.raw_byline: r.decision for r in BylineNormalization.objects.all()}
    assert decided == {"Jon Smtih": "fix", "Sports Desk": "drop"}
    assert [c.raw_byline for c in BylineReviewCandidate.objects.all()] == ["Left Alone"]


def test_one_bad_row_refuses_the_whole_page(page):
    """Applying 24 and refusing one leaves the reviewer to work out which is
    which."""
    _candidate("Jon Smtih")
    _candidate("Bad Fix")
    _submit(
        page,
        {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"},
        {"raw": "Bad Fix", "decision": "fix", "names": ""},
    )
    assert not BylineNormalization.objects.exists()
    assert BylineReviewCandidate.objects.count() == 2


def test_every_problem_on_the_page_is_reported_at_once(page):
    from django.contrib.messages import get_messages

    _candidate("One")
    _candidate("Two")
    response = _submit(
        page,
        {"raw": "One", "decision": "fix", "names": ""},
        {"raw": "Two", "decision": "exclude", "content_type": ""},
    )
    seen = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("One" in m for m in seen)
    assert any("Two" in m for m in seen)
    assert any("Nothing was saved" in m for m in seen)


def test_a_page_written_half_way_is_rolled_back(page):
    """A ruling that fails on row 2 must not leave row 1 decided."""
    _candidate("Jon Smtih")
    _syndicated()
    _submit(
        page,
        {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"},
        {
            "raw": "Steph Quinn",
            "host": ["nowhere.example"],
            "ruling:nowhere.example": "wire",
        },
    )
    assert not BylineNormalization.objects.exists()


def test_a_dataset_that_is_not_yours_is_refused_on_the_page_submit(page):
    response = _submit(page, {"raw": "x", "decision": "drop"}, dataset="Nope")
    assert response.status_code == 400


# --- a drawer of stories under each newsroom ---------------------------------


def _rooms(body):
    """The newsroom blocks of the page, keyed by host."""
    marker = 'name="host-0" value="'
    out = {}
    for block in body.split(marker)[1:]:
        out[block.split('"', 1)[0]] = block
    return out


def test_each_newsroom_has_a_drawer_of_its_own_stories(page):
    _syndicated()
    rooms = _rooms(_queue(page).content.decode())
    assert set(rooms) == {"one.example", "two.example"}
    assert 'name="edit-0-a-home-0"' in rooms["one.example"]
    assert 'name="edit-0-a-away-1"' not in rooms["one.example"]
    assert 'name="edit-0-a-away-1"' in rooms["two.example"]
    assert 'name="edit-0-a-home-0"' not in rooms["two.example"]


def test_a_drawer_is_a_sample_not_every_story(page):
    for n in range(8):
        _article(f"a-{n}", "Jon Smith")
    _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x", articles=8)
    from review import bylines

    body = _queue(page).content.decode()
    room = _rooms(body)["one.example"]
    assert room.count('name="edit-0-') == bylines.SAMPLE_PER_HOST
    assert f"Read {bylines.SAMPLE_PER_HOST} of 8" in room


def test_the_drawer_shows_the_newest_stories_first(page):
    import datetime

    from django.utils import timezone

    for n in range(3):
        _article(f"a-{n}", "Jon Smith")
        Article.objects.filter(id=f"a-{n}").update(
            publish_date=timezone.now() - datetime.timedelta(days=n)
        )
    row = _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x")
    from review import bylines

    (room,) = bylines.host_choices(row)
    assert [s["id"] for s in room["samples"]] == ["a-0", "a-1", "a-2"]


def test_a_byline_on_one_newsroom_still_gets_its_drawer(page):
    """Christopher Replogle's 896 stories on ky3.com and one elsewhere: the
    correction is on the one story, and the row has to offer it."""
    _article("a-1", "Jon Smith")
    _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x")
    body = _queue(page).content.decode()
    assert 'name="edit-0-a-1"' in body


def test_a_drawer_links_open_in_their_own_window(page):
    _syndicated()
    room = _rooms(_queue(page).content.decode())["one.example"]
    assert 'target="_blank"' in room
    assert reverse("explorer:article_detail", args=["a-home-0"]) in room


def test_a_printed_name_is_offered_beside_its_story(page):
    """The page names somebody else, so the name is already known and the
    reviewer only ticks it. It is NOT filled in for them: on a page submitted
    whole, a pre-filled name is an answer nobody gave."""
    _replogle(
        mismatches=[
            {
                "article_id": "a-out",
                "url": "https://two.example/a-out",
                "title": "T",
                "host": "two.example",
                "printed": "Neal A. Johnson",
            }
        ]
    )
    rooms = _rooms(_queue(page).content.decode())
    assert 'name="use-0-a-out" value="Neal A. Johnson"' in rooms["two.example"]
    assert 'name="use-0-' not in rooms["one.example"]
    ticked = rooms["two.example"].split('name="use-0-a-out"')[1].split(">")[0]
    assert "checked" not in ticked


def test_ticking_the_printed_name_writes_it_on_that_story_only(page):
    _replogle()
    _submit(
        page,
        {"raw": "Christopher Replogle", "use:a-out": "Neal A. Johnson"},
    )
    assert Article.objects.get(id="a-out").author == "Neal A. Johnson"
    assert Article.objects.get(id="a-main-0").author == "Christopher Replogle"


def test_a_typed_name_wins_over_the_ticked_one(page):
    _replogle()
    _submit(
        page,
        {
            "raw": "Christopher Replogle",
            "edit:a-out": "N. A. Johnson",
            "use:a-out": "Neal A. Johnson",
        },
    )
    assert Article.objects.get(id="a-out").author == "N. A. Johnson"


def test_a_blank_edit_writes_nothing(page):
    _replogle()
    _submit(page, {"raw": "Christopher Replogle", "edit:a-out": "   "})
    assert Article.objects.get(id="a-out").author == "Christopher Replogle"
    assert not BylineNormalization.objects.exists()


def test_a_story_beside_a_co_author_can_be_corrected(page):
    """ "Rudi Keller, Steph Quinn" is one of hers, and its byline may be wrong."""
    _syndicated()
    _submit(page, {"raw": "Steph Quinn", "edit:a-away-1": "Rudi Keller"})
    assert Article.objects.get(id="a-away-1").author == "Rudi Keller"


def test_a_story_that_does_not_name_the_byline_cannot_be_reached(page):
    _article("a-other", "Jon Smith")
    _candidate("Christopher Replogle")
    response = _submit(
        page, {"raw": "Christopher Replogle", "edit:a-other": "Neal A. Johnson"}
    )
    assert response.status_code == 400
    assert Article.objects.get(id="a-other").author == "Jon Smith"


# --- a newsroom's ruling and its reason, and the other answers on the row -----


def test_a_newsroom_carries_its_own_reason(page):
    _syndicated()
    _submit(
        page,
        {
            "raw": "Steph Quinn",
            "host": ["one.example", "two.example"],
            "ruling:two.example": "wire",
            "why:two.example": "States Newsroom copy, republished",
            "why:one.example": "she works here",
        },
    )
    from review.models import ReviewDecision

    reasons = set(
        ReviewDecision.objects.filter(subject_id__startswith="a-away").values_list(
            "reason", flat=True
        )
    )
    assert reasons == {"States Newsroom copy, republished"}
    assert not ReviewDecision.objects.filter(subject_id__startswith="a-home").exists()


def test_a_row_changed_but_not_decided_is_kept_as_it_stands(page):
    """The reviewer read the row and acted on it. Leaving it in the queue would
    make every row worked need a second answer."""
    _replogle()
    _submit(page, {"raw": "Christopher Replogle", "edit:a-out": "Neal A. Johnson"})
    row = BylineNormalization.objects.get(raw_byline="Christopher Replogle")
    assert row.decision == "accept"
    assert not BylineReviewCandidate.objects.filter(
        raw_byline="Christopher Replogle"
    ).exists()


def test_a_ruling_and_a_correction_land_together(page):
    """One row, both answers: the newsroom that republishes, and the one story
    on the other whose byline is wrong."""
    _syndicated()
    _submit(
        page,
        {
            "raw": "Steph Quinn",
            "host": ["one.example", "two.example"],
            "ruling:two.example": "wire",
            "edit:a-home-0": "Stephanie Quinn",
        },
    )
    assert Article.objects.get(id="a-away-1").status == "wire"
    assert Article.objects.get(id="a-home-0").author == "Stephanie Quinn"
    assert Article.objects.get(id="a-home-1").author == "Steph Quinn"


def test_an_explicit_decision_wins_over_the_implied_one(page):
    _replogle()
    _submit(
        page,
        {
            "raw": "Christopher Replogle",
            "edit:a-out": "Neal A. Johnson",
            "decision": "fix",
            "names": "Christopher Replogle Jr.",
        },
    )
    row = BylineNormalization.objects.get(raw_byline="Christopher Replogle")
    assert row.decision == "fix"
    assert row.canonical_names == ["Christopher Replogle Jr."]


def test_the_page_says_how_the_submit_went(page):
    _candidate("Jon Smtih")
    response = _submit(
        page, {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith"}
    )
    body = page.get(response["Location"]).content.decode()
    assert "1 bylines dealt with" in body or "1 byline" in body


# --- the owner is read from the corpus, not from the candidate's snapshot -----
#
# `byline_review_candidates.owners` is written by the crawler when it computes
# the row. "McClathy" was corrected to "The McClatchy Company" on
# www.kansascity.com on 2026-09-23 and six candidate rows went on printing the
# misspelling, because nothing re-reads that column until the next refresh.


def test_the_owner_comes_from_the_corpus_not_the_candidate(page):
    _article("a-1", "Ben Wheeler")
    _candidate(
        "Ben Wheeler",
        signal="CROSS_OWNER",
        signal_label="Same name, unrelated owners",
        owners=["McClathy"],
        hosts=["one.example"],
    )
    body = _queue(page).content.decode()
    assert "Missourian Publishing" in body, "the owner the source table carries"
    assert "McClathy" not in body, "the snapshot the candidate carries"


def test_a_row_with_no_matching_stories_keeps_its_recorded_owner(page):
    """A byline the page prints and nothing stored has no articles carrying the
    name, so the corpus can say nothing about its owner."""
    _candidate("Nobody At All", owners=["Some Owner Ltd"], hosts=["one.example"])
    assert "Some Owner Ltd" in _queue(page).content.decode()


class TestAnOpenDrawerBelongsToItsNewsroom:
    """The drawer and the newsroom it belongs to are one `tbody` in the
    markup, and nothing drew that.

    Open, a drawer is five or more story rows deep, so the newsroom whose
    stories they are scrolls off the top of the block and the drawer reads as
    a sibling of the NEXT newsroom instead of a child of its own. The grouping
    was real and invisible; this draws it.
    """

    STYLES = Path(__file__).resolve().parent.parent / "static/css/datadesk.css"
    CSS = STYLES.read_text()
    GROUP = ".byline-newsrooms tbody:has(details[open])"

    def test_the_open_group_is_lifted_off_the_page(self):
        assert (
            ".byline-newsrooms tbody:has(details[open]) > tr > td "
            "{ background: var(--surface-2); }" in self.CSS
        )

    def test_the_group_takes_one_rail_down_its_left_edge(self):
        """Unbroken from the newsroom row through the last story -- that
        continuity is the thing that says "these belong together"."""
        assert f"{self.GROUP} > tr > td:first-child" in self.CSS
        assert "box-shadow: inset 3px 0 0 var(--accent);" in self.CSS

    def test_it_applies_only_while_the_drawer_is_open(self):
        """A closed drawer is a single summary line under its row and needs
        no help; tinting every newsroom would make the page a field of
        stripes and say nothing."""
        assert self.GROUP in self.CSS
        assert ".byline-newsrooms tbody > tr > td { background:" not in self.CSS

    def test_it_is_drawn_in_theme_tokens(self):
        """`--surface-2` and `--accent` are both redefined for dark mode, so
        the group reads in either theme without a second rule."""
        group = self.CSS[self.CSS.index(self.GROUP) :][:400]
        assert "#" not in group.split(".byline-stories")[0]


# --- the credit side of a wire ruling ----------------------------------------


class TestAHomeNewsroomIsCreditedWithTheWireCopies:
    """Ruling the outlying newsrooms only ever subtracted.

    Their copies go to `wire`, which takes them out of enrichment, out of the
    BigQuery export and out of both byline reports -- and says nothing about
    whose reporting they are. Steph Quinn's 307 wire copies across 36 Missouri
    domains are the Missouri Independent's work.

    The home newsroom is CHOSEN, in the same select as every other ruling. It
    was first inferred as "the one newsroom left at local reporting", which
    fails on the ordinary case and is what these tests mostly guard.
    """

    def test_the_wire_copies_name_the_newsroom_marked_home(self, page):
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-out", "Jon Smith", host_id="s-2")
        _article("a-out2", "Jon Smith", host_id="s-3")
        _candidate("Jon Smith", hosts=["one.example", "two.example", "three.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example", "three.example"],
                "ruling:one.example": "home",
                "ruling:two.example": "wire",
                "ruling:three.example": "wire",
            },
        )
        assert Article.objects.get(id="a-out").syndicated_from_source_id == "s-1"
        assert Article.objects.get(id="a-out2").syndicated_from_source_id == "s-1"

    def test_the_home_newsrooms_own_stories_are_left_alone(self, page):
        """HOME is not a disposition. Her stories there are her reporting."""
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-out", "Jon Smith", host_id="s-2")
        _candidate("Jon Smith", hosts=["one.example", "two.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example"],
                "ruling:one.example": "home",
                "ruling:two.example": "wire",
            },
        )
        home = Article.objects.get(id="a-home")
        assert home.status == "enriched"
        assert home.syndicated_from_source_id is None

    def test_other_newsrooms_may_stay_local_without_being_home(self, page):
        """THE CASE THE FIRST ATTEMPT GOT WRONG. A reporter with several
        newsrooms that genuinely carry her reporting and several that
        republish it: the extra local ones are left at "local reporting" and
        the wire copies are still credited. Inferring home as "the one left
        over" could not credit anything here without first mislabelling a
        legitimate newsroom as wire."""
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-also-local", "Jon Smith", host_id="s-2")
        _article("a-out", "Jon Smith", host_id="s-3")
        _candidate("Jon Smith", hosts=["one.example", "two.example", "three.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example", "three.example"],
                "ruling:one.example": "home",
                "ruling:three.example": "wire",
            },
        )
        assert Article.objects.get(id="a-also-local").status == "enriched"
        assert Article.objects.get(id="a-out").syndicated_from_source_id == "s-1"

    def test_nothing_is_credited_without_a_home(self, page):
        """The ruling and the credit are two claims. A reviewer may know these
        are not local reporting without knowing whose they are."""
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-out", "Jon Smith", host_id="s-2")
        _candidate("Jon Smith", hosts=["one.example", "two.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example"],
                "ruling:two.example": "wire",
            },
        )
        out = Article.objects.get(id="a-out")
        assert out.status == "wire"
        assert out.syndicated_from_source_id is None

    def test_two_newsrooms_cannot_both_be_home(self, page):
        """Two homes is two answers to where she works. The page saves
        nothing when anything on it is wrong, so the wire ruling beside it
        does not land either."""
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-also", "Jon Smith", host_id="s-2")
        _article("a-out", "Jon Smith", host_id="s-3")
        _candidate("Jon Smith", hosts=["one.example", "two.example", "three.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example", "three.example"],
                "ruling:one.example": "home",
                "ruling:two.example": "home",
                "ruling:three.example": "wire",
            },
        )
        assert Article.objects.get(id="a-out").status == "enriched"
        assert Article.objects.get(id="a-out").syndicated_from_source_id is None

    def test_only_wire_earns_credit(self, page):
        """An obituary or a section front is not somebody else's reporting;
        it is not reporting."""
        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-obit", "Jon Smith", host_id="s-2")
        _candidate("Jon Smith", hosts=["one.example", "two.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example"],
                "ruling:one.example": "home",
                "ruling:two.example": "obituary",
            },
        )
        assert Article.objects.get(id="a-obit").syndicated_from_source_id is None

    def test_the_audit_records_how_many_were_credited(self, page):
        from audit.models import AuditLogEntry

        _article("a-home", "Jon Smith", host_id="s-1")
        _article("a-out", "Jon Smith", host_id="s-2")
        _candidate("Jon Smith", hosts=["one.example", "two.example"])
        _submit(
            page,
            {
                "raw": "Jon Smith",
                "host": ["one.example", "two.example"],
                "ruling:one.example": "home",
                "ruling:two.example": "wire",
            },
        )
        entry = AuditLogEntry.objects.filter(action="byline:newsrooms").latest("id")
        assert entry.after["credited"] == 1

    def test_the_select_offers_home(self, page):
        _article("a-1", "Jon Smith")
        _candidate("Jon Smith")
        body = _queue(page).content.decode()
        assert '<option value="home">' in body
        assert "home newsroom" in body


def test_answering_a_stale_decision_again_settles_it(page):
    """Stale means the answer predates a change in the facts; answering again
    is the answer to the changed facts. Left stale, the crawler read it as no
    decision and put the string back on every refresh -- fourteen cross-owner
    bylines re-decided on 2026-09-25 were back the next morning."""
    import datetime as dt

    from explorer.models import BylineNormalization
    from review import bylines

    bylines.decide("d-mo", "Jon Smith", bylines.ACCEPT, ["Jon Smith"], None)
    BylineNormalization.objects.filter(raw_byline="Jon Smith").update(
        stale_at=dt.datetime(2026, 9, 25, 4, 47), stale_reason="ownership corrected"
    )

    bylines.decide("d-mo", "Jon Smith", bylines.ACCEPT, ["Jon Smith"], None)

    row = BylineNormalization.objects.get(raw_byline="Jon Smith")
    assert row.stale_at is None
    assert row.stale_reason is None


def test_a_queue_with_thousands_of_fields_is_accepted(page):
    """Rudi Keller's drawer alone carried hundreds of story boxes, and the
    whole form was refused with a bare 400 at Django's default of 1,000
    fields before any of it was read."""
    from explorer.models import BylineNormalization

    _candidate("Jon Smtih")
    blanks = {f"edit:story-{n}": "" for n in range(3000)}
    response = _submit(
        page, {"raw": "Jon Smtih", "decision": "fix", "names": "Jon Smith", **blanks}
    )
    assert response.status_code != 400
    assert BylineNormalization.objects.filter(raw_byline="Jon Smtih").exists()
