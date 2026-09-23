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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Jon Smtih",
            "decision": "fix",
            "names": "Jon Smith",
        },
    )
    row = BylineNormalization.objects.get(raw_byline="Jon Smtih")
    assert row.decision == "fix"
    assert row.canonical_names == ["Jon Smith"]
    assert row.decided_by == "ed"


def test_a_decided_string_leaves_the_queue(page):
    """So a worked queue empties as it is worked. The crawler rewrites the row
    only if the string still shows a defect, and a decided one does not."""
    _candidate("Jon Smtih")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Jon Smtih",
            "decision": "fix",
            "names": "Jon Smith",
        },
    )
    assert not BylineReviewCandidate.objects.filter(raw_byline="Jon Smtih").exists()


def test_a_drop_names_nobody(page):
    _candidate("Sports Desk", signal="NOT_A_PERSON", signal_label="not a person")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Sports Desk",
            "decision": "drop",
            "names": "Sports Desk",
        },
    )
    row = BylineNormalization.objects.get(raw_byline="Sports Desk")
    assert row.decision == "drop"
    # Even though a name was in the box: "not a real name" means the string
    # names no person, and
    # keeping the text would put the desk back into both reports.
    assert row.canonical_names == []


def test_a_fix_with_no_name_is_refused(page):
    """An empty fix stores an empty name list, which is what drop means -- so
    it would silently drop a real reporter."""
    _candidate("Jon Smtih")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Jon Smtih",
            "decision": "fix",
            "names": "   ",
        },
    )
    assert not BylineNormalization.objects.exists()
    assert BylineReviewCandidate.objects.filter(raw_byline="Jon Smtih").exists()


def test_the_article_author_column_is_not_written_here(page):
    """The crawler owns that write. One writer for the column, and a review
    request that updated thousands of rows would be a write nobody asked for."""
    _candidate("Jon Smtih")
    article = _article("a-1", "Jon Smtih")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Jon Smtih",
            "decision": "fix",
            "names": "Jon Smith",
        },
    )
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


def test_the_row_offers_a_few_of_the_stories(page):
    _candidate("Jon Smith")
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smith", host_id="s-2")
    from review import bylines

    samples = bylines.sample_articles("d-mo", "Jon Smith")
    assert {s["host"] for s in samples} == {"one.example", "two.example"}


def test_the_sample_spreads_across_the_hosts(page):
    """The question a cross-owner row asks is whether the same person really
    writes for both papers; a sample from one of them cannot answer it."""
    for n in range(6):
        _article(f"a-one-{n}", "Jon Smith")
    _article("a-two", "Jon Smith", host_id="s-2")
    from review import bylines

    samples = bylines.sample_articles("d-mo", "Jon Smith")
    by_host = {}
    for sample in samples:
        by_host[sample["host"]] = by_host.get(sample["host"], 0) + 1
    assert by_host == {"one.example": bylines.PER_HOST, "two.example": 1}


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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "wire",
        },
    )
    assert [a.status for a in Article.objects.order_by("id")] == ["wire", "wire"]


def test_excluding_reaches_a_story_at_any_status(page):
    """Stopping at the local statuses leaves the same wire stories at `labeled`
    to be enriched next week, and the byline comes back."""
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter", status="labeled")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "wire",
        },
    )
    assert Article.objects.get(id="a-1").status == "wire"


def test_excluding_takes_the_byline_out_of_the_reports(page):
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    _article("a-2", "Jon Smith")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "wire",
        },
    )
    from review import bylines

    assert [r["byline"] for r in bylines.bylines_with_hosts("d-mo")] == ["Jon Smith"]
    assert not BylineReviewCandidate.objects.filter(raw_byline="Wire Reporter").exists()


def test_excluding_without_saying_what_they_are_is_refused(page):
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "",
        },
    )
    assert response.status_code == 400
    assert Article.objects.get(id="a-1").status == "enriched"
    assert not BylineNormalization.objects.exists()


def test_an_excluded_story_carries_the_same_decision_note_as_one_dispositioned(page):
    """Through the extraction queue's own `record`, so an article excluded here
    is indistinguishable from one dispositioned a row at a time."""
    _candidate("Wire Reporter", signal="CROSS_OWNER", signal_label="crosses owners")
    _article("a-1", "Wire Reporter")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "wire",
        },
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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Wire Reporter",
            "decision": "exclude",
            "content_type": "wire",
        },
    )
    row = BylineNormalization.objects.get(raw_byline="Wire Reporter")
    assert row.applied_at is not None
    assert row.articles_updated == 1
    assert Article.objects.get(id="a-1").author == "Wire Reporter"


def test_a_sample_offers_to_fix_that_one_articles_byline(page):
    """Christopher Replogle has 896 stories on ky3.com and one on
    unterrifieddemocrat.com, whose page reads "By Neal A. Johnson, UD Editor" --
    the JSON-LD it was parsed from carries neither name.

    Neither decision on the row is right for that: excluding throws out 896
    genuine stories and fixing the string renames them. The correction belongs
    to the one article."""
    _candidate(
        "Christopher Replogle", signal="CROSS_OWNER", signal_label="crosses owners"
    )
    _article("a-1", "Christopher Replogle")
    body = _queue(page).content.decode()
    assert reverse("review:edit_field", args=["a-1", "author"]) in body


# --- the whole spread, and a replacement across the part that is wrong ------


def test_a_row_is_not_expanded_until_asked(page):
    """The spread of a byline with 900 stories is a page of its own; rendering it
    for all 25 rows would be 25 of those."""
    _candidate("Christopher Replogle")
    _article("a-1", "Christopher Replogle")
    body = _queue(page).content.decode()
    assert "Show every story" in body
    assert 'name="new_byline"' not in body


def test_expanding_shows_every_story_with_the_outliers_first(page):
    _candidate("Christopher Replogle")
    for n in range(3):
        _article(f"a-main-{n}", "Christopher Replogle")
    _article("a-out", "Christopher Replogle", host_id="s-2")
    from review import bylines

    groups = bylines.every_article("d-mo", "Christopher Replogle")
    assert [(g["host"], g["outlier"]) for g in groups] == [
        ("two.example", True),
        ("one.example", False),
    ]


def test_equal_counts_are_a_stringer_not_an_outlier(page):
    """Two hosts with the same number of stories is somebody filing to both."""
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smith", host_id="s-2")
    from review import bylines

    assert not any(g["outlier"] for g in bylines.every_article("d-mo", "Jon Smith"))


def test_the_spread_includes_a_story_at_any_status(page):
    """A wrong byline is wrong on a story nobody has enriched yet too."""
    _article("a-1", "Jon Smith", status="labeled")
    from review import bylines

    groups = bylines.every_article("d-mo", "Jon Smith")
    assert [a["status"] for g in groups for a in g["articles"]] == ["labeled"]


def test_the_expanded_form_offers_the_replacement(page):
    _candidate("Christopher Replogle")
    _article("a-1", "Christopher Replogle")
    body = page.get(
        reverse("review:bylines"),
        {"dataset": "Mizzou-Missouri-State", "expand": "Christopher Replogle"},
    ).content.decode()
    assert 'name="new_byline"' in body
    assert 'name="article" value="a-1"' in body


def test_a_replacement_writes_only_the_picked_stories(page):
    _candidate("Christopher Replogle")
    _article("a-keep", "Christopher Replogle")
    _article("a-fix", "Christopher Replogle", host_id="s-2")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Christopher Replogle",
            "decision": "replace",
            "article": ["a-fix"],
            "new_byline": "Neal A. Johnson",
        },
    )
    assert Article.objects.get(id="a-fix").author == "Neal A. Johnson"
    assert Article.objects.get(id="a-keep").author == "Christopher Replogle"


def test_a_replacement_records_nothing_against_the_byline(page):
    """The string is right on the stories left alone, so the candidate stays in
    the queue until somebody decides the string itself."""
    _candidate("Christopher Replogle")
    _article("a-fix", "Christopher Replogle")
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Christopher Replogle",
            "decision": "replace",
            "article": ["a-fix"],
            "new_byline": "Neal A. Johnson",
        },
    )
    assert not BylineNormalization.objects.exists()
    assert BylineReviewCandidate.objects.filter(
        raw_byline="Christopher Replogle"
    ).exists()


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


def test_a_replacement_with_no_name_is_refused(page):
    _candidate("Christopher Replogle")
    _article("a-fix", "Christopher Replogle")
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Christopher Replogle",
            "decision": "replace",
            "article": ["a-fix"],
            "new_byline": "  ",
        },
    )
    assert response.status_code == 400
    assert Article.objects.get(id="a-fix").author == "Christopher Replogle"


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
    assert 'value="cluster"' in body


def test_the_leading_spelling_is_preselected(page):
    """It carries the most stories, so it is the answer a reviewer would give if
    they agreed — and agreeing should be one click."""
    _cluster()
    body = _queue(page).content.decode()
    # The leading spelling's radio carries `checked`, the minority one does not.
    # Compared on the rendered attributes rather than on exact whitespace, which
    # the template is free to change.
    radios = [
        body[at : at + 160]
        for at in range(len(body))
        if body.startswith('name="canonical"', at)
    ]
    assert "Angela Hutschreider" in radios[0] and "checked" in radios[0]
    assert "Angie Hutschreider" in radios[1] and "checked" not in radios[1]


def test_keeping_one_spelling_records_every_spelling(page):
    """The corpus ends up with one name, and the decisions survive a
    re-extraction that writes an old spelling again."""
    _cluster()
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Angela Hutschreider",
            "decision": "cluster",
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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Angela Hutschreider",
            "decision": "cluster",
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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Angela Hutschreider",
            "decision": "cluster",
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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Angela Hutschreider",
            "decision": "cluster",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Angela Hutschreider",
        },
    )
    assert not BylineReviewCandidate.objects.exists()


def test_a_spelling_not_offered_is_refused(page):
    """The names being merged are what the answer means, so a canonical that was
    not one of the spellings shown is refused rather than written."""
    _cluster()
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Angela Hutschreider",
            "decision": "cluster",
            "spelling": ["Angela Hutschreider", "Angie Hutschreider"],
            "canonical": "Someone Else Entirely",
        },
    )
    assert response.status_code == 400
    assert not BylineNormalization.objects.exists()


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
    assert "shares a byline" in body
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


def test_the_outlying_newsroom_is_named_on_the_row(page):
    _replogle()
    body = _queue(page).content.decode()
    assert "Outlying:" in body
    assert "two.example" in body


def test_the_main_newsroom_is_not_called_an_outlier(page):
    _replogle()
    from review import bylines

    row = BylineReviewCandidate.objects.get(raw_byline="Christopher Replogle")
    assert [o["host"] for o in bylines.outlying_hosts(row)] == ["two.example"]


def test_equal_counts_make_neither_an_outlier(page):
    """Somebody filing to both papers, which is the legitimate case this signal
    cannot tell apart on its own."""
    _article("a-1", "Jon Smith")
    _article("a-2", "Jon Smith", host_id="s-2")
    row = _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x")
    from review import bylines

    assert bylines.outlying_hosts(row) == []


def test_the_outlying_stories_are_on_the_row_and_ticked(page):
    """Not in a drawer. This is the question the row asks."""
    _replogle()
    body = _queue(page).content.decode()
    assert 'name="article" value="a-out"' in body
    assert "Set on the ticked stories" in body


def test_the_main_newsrooms_stories_are_not_ticked_on_the_row(page):
    """Ticking 896 genuine stories by default would be the opposite of the fix."""
    _replogle()
    body = _queue(page).content.decode()
    assert 'value="a-main-0"' not in body.split("Show every story")[0]


def test_a_proven_mismatch_is_offered_as_one_click(page):
    """The page names somebody else, so the name is already known and the
    reviewer only confirms it."""
    _replogle(
        mismatches=[
            {
                "article_id": "a-out",
                "url": "https://two.example/a-out",
                "title": "Linn R-2 hires Haslag",
                "host": "two.example",
                "printed": "Neal A. Johnson",
            }
        ]
    )
    body = _queue(page).content.decode()
    assert "Set that story to Neal A. Johnson" in body
    assert 'name="new_byline" value="Neal A. Johnson"' in body


def test_the_one_click_correction_writes_only_that_story(page):
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
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Christopher Replogle",
            "decision": "replace",
            "article": ["a-out"],
            "new_byline": "Neal A. Johnson",
        },
    )
    assert Article.objects.get(id="a-out").author == "Neal A. Johnson"
    assert Article.objects.get(id="a-main-0").author == "Christopher Replogle"


def test_a_row_with_no_outlier_shows_no_outlier_form(page):
    """The 19 rows that are not cross-owner, and the cross-owner rows whose
    counts are even."""
    _candidate("Jon Smtih")
    _article("a-1", "Jon Smtih")
    body = _queue(page).content.decode()
    assert "Outlying:" not in body


def test_a_byline_with_one_newsroom_has_no_outlier(page):
    _article("a-1", "Jon Smith")
    row = _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x")
    from review import bylines

    assert bylines.outlying_hosts(row) == []


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


def test_keep_is_the_default_for_every_newsroom(page):
    """A form that excluded by default would be a blanket ruling with extra
    steps, and one careless submit would re-dispose all 42 of her domains."""
    _syndicated()
    body = _queue(page).content.decode()
    selects = [
        body[at : at + 200]
        for at in range(len(body))
        if body.startswith('name="disposition"', at)
    ]
    assert selects, "no per-newsroom ruling offered"
    for select in selects:
        assert 'value=""' in select
        assert "selected" not in select


def test_each_newsroom_is_ruled_on_its_own(page):
    """One republishes, the other has her filing directly. The whole point."""
    _article("a-third", "Steph Quinn, Clara Bates", host_id="s-3")
    _syndicated()
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["one.example", "two.example", "three.example"],
            "disposition": ["", "wire", ""],
        },
    )
    assert Article.objects.get(id="a-away-1").status == "wire"
    assert Article.objects.get(id="a-third").status == "enriched"
    assert Article.objects.get(id="a-home-0").status == "enriched"


def test_a_newsroom_kept_is_not_written(page):
    _syndicated()
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["one.example", "two.example"],
            "disposition": ["", ""],
        },
    )
    assert {a.status for a in Article.objects.all()} == {"enriched"}


def test_the_byline_is_accepted_not_dropped(page):
    """She is a real reporter with a real name. The ruling is about which stories
    are local reporting, and nothing about the name is wrong."""
    _syndicated()
    page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["one.example", "two.example"],
            "disposition": ["", "wire"],
        },
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
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["nowhere.example"],
            "disposition": ["wire"],
        },
    )
    assert response.status_code == 400
    assert not BylineNormalization.objects.exists()


def test_a_disposition_that_is_not_one_is_refused(page):
    _syndicated()
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["one.example", "two.example"],
            "disposition": ["", "something-else"],
        },
    )
    assert response.status_code == 400
    assert Article.objects.get(id="a-away-1").status == "enriched"


def test_a_host_without_its_ruling_is_refused(page):
    """The two lists are paired by position, so a mismatch means the form did not
    arrive as it was rendered."""
    _syndicated()
    response = page.post(
        reverse("review:bylines"),
        {
            "dataset": "Mizzou-Missouri-State",
            "raw_byline": "Steph Quinn",
            "decision": "primary",
            "host": ["one.example", "two.example"],
            "disposition": ["wire"],
        },
    )
    assert response.status_code == 400


def test_a_byline_on_one_newsroom_is_not_offered_the_ruling(page):
    _article("a-1", "Jon Smith")
    _candidate("Jon Smith", signal="CROSS_OWNER", signal_label="x")
    body = _queue(page).content.decode()
    assert "One newsroom is theirs" not in body
