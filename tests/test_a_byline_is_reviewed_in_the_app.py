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
    # Even though a name was in the box: dropping means it names nobody, and
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
