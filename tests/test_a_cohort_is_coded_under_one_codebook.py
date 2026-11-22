"""A cohort is coded under one frozen codebook, and says which.

Agreement is only comparable between answers given under the same
definitions. V2 renames a category on screen, narrows another and adds
three rejections; a V1 cohort must keep seeing V1, a V2 cohort V2, and
no rate may be computed over both.

See docs/CIN_V2_DATADESK.md, WP1.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from review.classification import (
    V1_REJECTIONS,
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
    CodebookSettings,
    CodebookVersion,
    agreement_report,
    draw_cohort,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/classification/"
NEW = ["obituary", "advertorial", "service_copy"]


def _v2():
    draft = CodebookSettings.load()
    draft.instructions = "V2 instructions."
    draft.display_names = {"Civic Life": "Community Life"}
    draft.guidance = {"Civic information": ["How to reach it.", "get a library card"]}
    draft.rejections = list(V1_REJECTIONS) + NEW
    draft.save()
    return draft.freeze("V2.0")


def _coder(name, cohort):
    user = User.objects.create_user(name, email=f"{name}@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    return user


def _assign(cohort, coder, article_id, crawler=True):
    if crawler:
        from explorer.models import Article, CandidateLink, Source

        source, _ = Source.objects.using("crawler").get_or_create(
            id="s1", defaults={"host": "a.example", "canonical_name": "A Paper"}
        )
        link = CandidateLink.objects.using("crawler").create(
            id=f"cl-{article_id}",
            url=f"https://a.example/{article_id}",
            source=source,
            status="extracted",
        )
        Article.objects.using("crawler").create(
            id=article_id,
            candidate_link=link,
            url=link.url,
            title="A headline",
            status="labeled",
            text="word " * 300,
        )
    ClassificationSample.objects.get_or_create(
        cohort=cohort, article_id=article_id, defaults={"stratum": "random"}
    )
    return ClassificationAssignment.objects.create(
        cohort=cohort, article_id=article_id, assigned_to=coder
    )


@pytest.fixture
def v1_cohort(db):
    return ClassificationCohort.objects.create(
        number=1, slug="cohort-1", codebook_version=CodebookVersion.v1()
    )


@pytest.fixture
def v2_cohort(db):
    return ClassificationCohort.objects.create(
        number=2, slug="cohort-2", codebook_version=_v2()
    )


# ------------------------------------------------------------ the version


def test_freezing_copies_the_draft_and_fills_what_it_left_unsaid():
    v2 = _v2()
    assert v2.instructions == "V2 instructions."
    assert v2.shown("Civic Life") == "Community Life"
    assert v2.shown("Sports") == "Sports"
    # Only one category was redrafted; the rest carry the standing text.
    assert v2.guidance["Civic information"][0] == "How to reach it."
    assert len(v2.guidance) == 10
    assert set(NEW) <= set(v2.rejections)


def test_a_version_cannot_be_edited():
    v2 = _v2()
    v2.instructions = "Changed after the fact."
    with pytest.raises(ValueError):
        v2.save()


def test_v1_offers_the_six_original_rejections_and_no_renames():
    v1 = CodebookVersion.v1()
    assert [value for value, _ in v1.rejection_choices()] == list(V1_REJECTIONS)
    assert all(stored == shown for stored, shown in v1.label_choices())


def test_a_new_cohort_opens_under_the_latest_version(crawler_schema):
    CodebookVersion.v1()
    first, _ = draw_cohort(4)
    assert first.codebook_version.name == "V1"
    _v2()
    second, _ = draw_cohort(4)
    assert second.codebook_version.name == "V2.0"
    # The cohort already open keeps the codebook it was opened under.
    first.refresh_from_db()
    assert first.codebook_version.name == "V1"


def test_a_cohort_from_before_versions_reads_as_v1(db):
    cohort = ClassificationCohort.objects.create(number=9, slug="cohort-9")
    assert cohort.codebook.name == "V1"


# -------------------------------------------------------------- the queue


def test_two_cohorts_show_their_own_codebooks(
    client, crawler_schema, v1_cohort, v2_cohort
):
    old, new = _coder("old", v1_cohort), _coder("new", v2_cohort)
    _assign(v1_cohort, old, "a-1")
    _assign(v2_cohort, new, "a-2")

    client.force_login(old)
    body = client.get(URL).content.decode()
    assert "Community Life" not in body
    assert "Obituary" not in body

    client.force_login(new)
    body = client.get(URL).content.decode()
    assert '<option value="Civic Life">Community Life</option>' in body
    assert "Obituary or death notice" in body
    # Beside a story only the categories are shown; the instructions are
    # on the empty-queue page.
    assert "How to reach it." in body


def test_community_life_is_stored_as_civic_life(client, crawler_schema, v2_cohort):
    """The stored label is the model's class name. V2 changes what a
    coder reads, not what is written."""
    coder = _coder("new", v2_cohort)
    assignment = _assign(v2_cohort, coder, "a-2")
    client.force_login(coder)
    client.post(URL, {"assignment": assignment.pk, "primary": "Civic Life"})
    decision = ClassificationDecision.objects.get()
    assert decision.primary_label == "Civic Life"
    assert decision.codebook_version.name == "V2.0"


def test_a_v1_cohort_cannot_record_a_v2_rejection(client, crawler_schema, v1_cohort):
    coder = _coder("old", v1_cohort)
    assignment = _assign(v1_cohort, coder, "a-1")
    client.force_login(coder)
    client.post(URL, {"assignment": assignment.pk, "reject": "obituary"})
    assert not ClassificationDecision.objects.exists()
    client.post(URL, {"assignment": assignment.pk, "reject": "not_local"})
    assert ClassificationDecision.objects.get().reject_reason == "not_local"


def test_a_v2_cohort_records_a_new_rejection(client, crawler_schema, v2_cohort):
    coder = _coder("new", v2_cohort)
    assignment = _assign(v2_cohort, coder, "a-2")
    client.force_login(coder)
    client.post(URL, {"assignment": assignment.pk, "reject": "obituary"})
    assert ClassificationDecision.objects.get().reject_reason == "obituary"


def test_a_label_outside_the_vocabulary_is_not_stored(
    client, crawler_schema, v2_cohort
):
    """The menu shows Community Life; the value posted is Civic Life. A
    display name posted as a value is not a category."""
    coder = _coder("new", v2_cohort)
    assignment = _assign(v2_cohort, coder, "a-2")
    client.force_login(coder)
    client.post(URL, {"assignment": assignment.pk, "primary": "Community Life"})
    assert not ClassificationDecision.objects.exists()


# ---------------------------------------------------------- never pooled


def test_agreement_is_reported_under_one_codebook(v1_cohort, v2_cohort):
    a, b = _coder("a", v1_cohort), _coder("b", v1_cohort)
    for cohort, article, labels in (
        (v1_cohort, "x-1", ("Sports", "Health")),
        (v2_cohort, "x-2", ("Sports", "Sports")),
    ):
        for coder, label in zip((a, b), labels, strict=True):
            ClassificationDecision.objects.create(
                cohort=cohort,
                article_id=article,
                decided_by=coder,
                primary_label=label,
                codebook_version=cohort.codebook_version,
            )
    v1 = agreement_report(CodebookVersion.v1())
    v2 = agreement_report(v2_cohort.codebook_version)
    assert (v1["pairs"], v1["overall"]) == (1, 0.0)
    assert (v2["pairs"], v2["overall"]) == (1, 100.0)
    # The default is the current version, never both.
    assert agreement_report()["pairs"] == 1


def test_the_report_shows_the_name_coders_saw(v2_cohort):
    a, b = _coder("a", v2_cohort), _coder("b", v2_cohort)
    for coder in (a, b):
        ClassificationDecision.objects.create(
            cohort=v2_cohort,
            article_id="x-2",
            decided_by=coder,
            primary_label="Civic Life",
            codebook_version=v2_cohort.codebook_version,
        )
    row = agreement_report(v2_cohort.codebook_version)["categories"][0]
    assert (row["label"], row["shown"]) == ("Civic Life", "Community Life")


def test_kappa_is_reported_beside_percent_agreement(v2_cohort):
    """V1 was reported as Cohen's kappa (0.583), so V2 has to be given
    in it too. Four stories: Sports/Sports, Health/Health, Sports/Health
    and Health/a rejection. A rejection is one more answer."""
    a, b = _coder("a", v2_cohort), _coder("b", v2_cohort)
    for article, (first, second) in enumerate(
        (
            ("Sports", "Sports"),
            ("Health", "Health"),
            ("Sports", "Health"),
            ("Health", ""),
        )
    ):
        for coder, label in ((a, first), (b, second)):
            ClassificationDecision.objects.create(
                cohort=v2_cohort,
                article_id=f"k-{article}",
                decided_by=coder,
                primary_label=label,
                reject_reason="" if label else "not_local",
                codebook_version=v2_cohort.codebook_version,
            )
    report = agreement_report(v2_cohort.codebook_version)
    assert report["overall"] == 50.0
    # Observed 0.5; chance: first coder Sports 2/4, Health 2/4; second
    # Sports 1/4, Health 2/4, rejected 1/4 -> 0.5*0.25 + 0.5*0.5 = 0.375.
    assert report["kappa"] == round((0.5 - 0.375) / (1 - 0.375), 3)
    sports = next(r for r in report["categories"] if r["label"] == "Sports")
    # Sports against the rest: (T,T) (F,F) (T,F) (F,F).
    assert sports["kappa"] == round((0.75 - 0.5) / (1 - 0.5), 3)
    assert report["cohorts"][0]["kappa"] == report["kappa"]


def _answers(cohort, rows):
    """rows: (story, coder, label) in the order given; "" is a rejection."""
    for story, coder, label in rows:
        ClassificationDecision.objects.create(
            cohort=cohort,
            article_id=story,
            decided_by=coder,
            primary_label=label,
            reject_reason="" if label else "not_local",
            codebook_version=cohort.codebook_version,
        )


def test_each_category_reports_observed_expected_and_kappa(v2_cohort):
    a, b = _coder("a", v2_cohort), _coder("b", v2_cohort)
    _answers(
        v2_cohort,
        [
            ("s-0", a, "Sports"),
            ("s-0", b, "Sports"),
            ("s-1", a, "Health"),
            ("s-1", b, "Health"),
            ("s-2", a, "Sports"),
            ("s-2", b, "Health"),
            ("s-3", a, "Health"),
            ("s-3", b, ""),
        ],
    )
    report = agreement_report(v2_cohort.codebook_version)
    assert report["overall"] == 50.0
    assert report["expected"] == 37.5
    sports = next(r for r in report["categories"] if r["label"] == "Sports")
    # Sports against the rest: (T,T) (F,F) (T,F) (F,F). Observed 3/4;
    # a chose it 2/4, b 1/4: expected 0.5*0.25 + 0.5*0.75.
    found = (sports["observed"], sports["expected"], sports["kappa"])
    assert found == (75.0, 50.0, 0.5)
    # Sports is 3 of the 8 answers; 2 pairs name it, 1 where both did.
    assert (sports["share"], sports["pairs"], sports["rate"]) == (37.5, 2, 50.0)
    # Lowest kappa first.
    kappas = [r["kappa"] for r in report["categories"]]
    assert kappas == sorted(kappas)


def test_a_coder_out_of_line_with_the_others_shows(v2_cohort):
    """Two coders who agree and a third who never matches either. The
    third's kappa is read against the kappa of the pairs they are not
    in."""
    a, b, c = (_coder(n, v2_cohort) for n in ("a", "b", "c"))
    _answers(
        v2_cohort,
        [
            ("s-0", a, "Sports"),
            ("s-0", b, "Sports"),
            ("s-1", a, "Health"),
            ("s-1", b, "Health"),
            ("s-2", a, "Sports"),
            ("s-2", c, "Health"),
            ("s-3", b, "Health"),
            ("s-3", c, "Sports"),
        ],
    )
    report = agreement_report(v2_cohort.codebook_version)
    rows = {row["name"]: row for row in report["coders"]}
    c_row = rows["c"]
    assert (c_row["n"], c_row["observed"], c_row["kappa"]) == (2, 0.0, -1.0)
    assert rows["c"]["kappa_without"] == 1.0
    # a: (S,S) (H,H) (S,H). Observed 6/9; a chose S2 H1, partners S1 H2,
    # so expected (2*1 + 1*2)/9 = 4/9; kappa (6/9 - 4/9) / (5/9) = 0.4.
    assert rows["a"]["kappa"] == 0.4

    from review.classification import CIN_LABELS

    sports = CIN_LABELS.index("Sports")
    grid = {row["name"]: row["cells"] for row in report["coder_share"]}
    assert grid["c"][sports] == {"coder": 50.0, "others": 50.0}
    kappa = {row["name"]: row["cells"] for row in report["coder_kappa"]}
    assert kappa["c"][sports] == -1.0


def test_the_report_page_shows_the_coder_tables(client, v2_cohort):
    admin = User.objects.create_user("admin", password="x")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    a, b = _coder("ann", v2_cohort), _coder("bob", v2_cohort)
    _answers(v2_cohort, [("s-0", a, "Sports"), ("s-0", b, "Health")])
    client.force_login(admin)
    body = client.get(reverse("review:cin_reporting")).content.decode()
    for text in (
        "Expected by chance",
        "Kappa without them",
        "ann",
        "bob",
        "Kappa by coder and category",
        "Community Life",
    ):
        assert text in body


# ---------------------------------------------------------- the admin page


def test_an_admin_freezes_the_draft_on_the_screen(client):
    admin = User.objects.create_user("admin", password="x")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    client.force_login(admin)
    post = {
        "instructions": "Fresh wording.",
        "shown-9": "Community Life",
        "rejections": list(V1_REJECTIONS) + ["obituary"],
        "action": "freeze",
        "name": "V2.0",
    }
    for i in range(10):
        post[f"covers-{i}"] = f"covers {i}"
        post[f"could-{i}"] = f"could {i}"
    body = client.post(reverse("review:cin_codebook"), post).content.decode()
    assert "Frozen as V2.0" in body
    v2 = CodebookVersion.objects.get(name="V2.0")
    assert v2.instructions == "Fresh wording."
    assert v2.shown("Civic Life") == "Community Life"
    assert "obituary" in v2.rejections and "advertorial" not in v2.rejections
    assert v2.frozen_by == admin
    # The same name twice is refused, and says so.
    body = client.post(reverse("review:cin_codebook"), post).content.decode()
    assert "already a version named V2.0" in body
    assert CodebookVersion.objects.filter(name="V2.0").count() == 1


def test_all_ten_categories_are_shown_in_full_beside_the_story(
    client, crawler_schema, v2_cohort
):
    coder = _coder("new", v2_cohort)
    _assign(v2_cohort, coder, "a-2")
    client.force_login(coder)
    body = client.get(URL).content.decode()
    box = body.split('class="category-box"')[1].split("</dl>")[0]
    assert box.count("<dt>") == 10
    assert "<dd>How to reach it.</dd>" in box
