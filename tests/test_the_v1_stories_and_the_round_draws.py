"""The 2023 V1 stories are loaded, and rounds draw by their own rules.

The pilot is drawn from the V1 stories so a V2 answer is compared with
the V1 answers on the same story. Training draws fill each category to
its floor, V1 records counted first, across three state datasets.

See docs/CIN_V2_DATADESK.md, WP3 and WP4.
"""

import csv

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import DATADESK, Grant
from accounts.privileges import CLASSIFIER
from review import v1_import
from review.classification import (
    PER_SMALL_DATASET,
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationStory,
    CodebookVersion,
    CoderStanding,
    Round,
    V1Decision,
    assign_cohort,
    draw_pilot,
    draw_training_batch,
    hit_rates,
    open_round,
    set_standing,
    stories_for,
    training_counts,
    training_plan,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

HEAD = [
    "Article ID",
    "URL",
    "Headline",
    "Article",
    "Primary Category",
    "Secondary Category",
    "Also Present",
    "article_id",
]

# story -> {group: (primary, secondary)}
CODING = {
    "1": {"A": ("Sports", ""), "C": ("Sports", "")},  # 0.9
    "2": {"A": ("Civic Life", ""), "C": ("Health", "Civic Life")},  # 0.7, civic
    "3": {"A": ("NOT LOCAL", ""), "C": ("NOT LOCAL", "")},  # rejected
    "4": {"B": ("Civic information", ""), "D": ("Education", "")},  # none, civic
    "5": {"B": ("Health", ""), "D": ("Education", ""), "E": ("Health", "")},  # 0.9
    "6": {"B": ("Education", ""), "D": ("Education", "")},  # 0.9
}


def _files(folder, coding=CODING):
    for group in "ABCDE":
        rows = [
            [
                story,
                f"https://paper.example/{story}",
                f"Headline {story}",
                f"Text of story {story}.",
                answers[group][0],
                answers[group][1],
                "",
                f"hash{story}",
            ]
            for story, answers in coding.items()
            if group in answers
        ]
        head = list(HEAD)
        if group == "A":
            head[0] = "Article ID:"  # as the real Group A file has it
        with open(
            folder / f"Missouri_News_Labeling_(Group_{group}2026.csv",
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(head)
            writer.writerows(rows)
    return folder


@pytest.fixture
def v1(tmp_path, db):
    call_command("import_v1_stories", str(_files(tmp_path)))
    return tmp_path


# ---------------------------------------------------------------- import


def test_the_import_loads_every_story_and_every_answer(v1):
    assert ClassificationStory.objects.count() == 6
    assert V1Decision.objects.count() == 13
    story = ClassificationStory.objects.get(id="v1:1")
    assert (story.headline, story.body) == ("Headline 1", "Text of story 1.")
    assert story.host == "paper.example" and story.source_hash == "hash1"


def test_each_story_carries_what_v1_made_of_it(v1):
    got = {
        s.id: (s.v1_outcome, s.v1_label, s.civic)
        for s in ClassificationStory.objects.all()
    }
    assert got == {
        "v1:1": (ClassificationStory.LABEL_09, "Sports", False),
        "v1:2": (ClassificationStory.LABEL_07, "Civic Life", True),
        "v1:3": (ClassificationStory.REJECTED, "", False),
        "v1:4": (ClassificationStory.NO_LABEL, "", True),
        "v1:5": (ClassificationStory.LABEL_09, "Health", False),
        "v1:6": (ClassificationStory.LABEL_09, "Education", False),
    }


def test_a_v1_rejection_is_a_reason_and_not_a_label(v1):
    row = V1Decision.objects.get(story_id="v1:3", group="A")
    assert (row.primary_label, row.reject_reason) == ("", "not_local")


def test_the_import_can_be_run_again(v1):
    call_command("import_v1_stories", str(v1))
    assert ClassificationStory.objects.count() == 6
    assert V1Decision.objects.count() == 13


def test_half_the_files_is_refused(tmp_path, db):
    """Half the files is half the coders: every outcome computed from it
    would be wrong and would look complete."""
    _files(tmp_path)
    (tmp_path / "Missouri_News_Labeling_(Group_E2026.csv").unlink()
    with pytest.raises(CommandError, match="All five groups"):
        call_command("import_v1_stories", str(tmp_path))
    assert not ClassificationStory.objects.exists()


def test_an_answer_that_cannot_be_placed_stops_the_import():
    with pytest.raises(ValueError, match="unknown V1 primary"):
        v1_import.split_answer("Lifestyle")
    assert v1_import.split_answer("NONE PRESENT (including technical issues)") == (
        "",
        "no_appropriate_category",
    )


# ----------------------------------------------------------- the queue


def _coder(name):
    return User.objects.create_user(name, email=f"{name}@localnewsimpact.org")


def test_a_coder_reads_a_v1_story_in_the_queue(client, v1, crawler_schema):
    version = CodebookVersion.v1()
    pilot = open_round("Pilot", Round.COMPARISON, codebook_version=version)
    coder = _coder("c")
    set_standing(coder, version, CoderStanding.ADMITTED)
    cohort, _ = draw_pilot(pilot, others=10)
    Grant.objects.create(user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    assign_cohort(cohort)
    client.force_login(coder)
    body = client.get("/review/classification/").content.decode()
    assert "Headline " in body and "Text of story " in body
    assert "paper.example" not in body


def test_one_lookup_finds_a_story_wherever_it_lives(v1, crawler_schema):
    found = stories_for(["v1:1", "no-such-crawler-id"])
    assert found["v1:1"].title == "Headline 1"
    assert "no-such-crawler-id" not in found


# ------------------------------------------------------------- the pilot


def test_the_pilot_takes_every_civic_story_and_some_of_the_rest(v1):
    pilot = open_round("Pilot", Round.COMPARISON)
    cohort, drawn = draw_pilot(pilot, others=2)
    assert drawn == {"civic": 2, "other": 2}
    samples = {s.article_id: s for s in cohort.samples.all()}
    # Both civic stories, including the one V1 coders never agreed on.
    assert samples["v1:2"].stratum == samples["v1:4"].stratum == "civic"
    assert samples["v1:4"].inclusion_probability == 1.0
    # The story two V1 coders rejected is not a story to label.
    assert "v1:3" not in samples
    others = [s for s in samples.values() if s.stratum == "other"]
    assert len(others) == 2
    assert all(s.inclusion_probability == pytest.approx(2 / 3) for s in others)
    assert cohort.round == pilot and cohort.target_coders == pilot.base_coders


def test_a_story_is_drawn_into_one_pilot_only(v1):
    first, _ = draw_pilot(open_round("Pilot", Round.COMPARISON), others=10)
    again, drawn = draw_pilot(open_round("Second", Round.COMPARISON), others=10)
    assert first.samples.count() == 5
    assert again is None and drawn == {"civic": 0, "other": 0}


def test_a_coders_queue_does_not_run_group_by_group(v1):
    """Samples are stored civic first. A coder who meets every civic
    story in a row has been told what the next one is."""
    big = {
        str(n): {"A": ("Civic Life", ""), "C": ("Civic Life", "")} for n in range(40)
    } | {str(n): {"A": ("Sports", ""), "C": ("Sports", "")} for n in range(40, 80)}
    ClassificationStory.objects.all().delete()
    folder = v1 / "big"
    folder.mkdir()
    call_command("import_v1_stories", str(_files(folder, big)))
    version = CodebookVersion.v1()
    pilot = open_round("Pilot", Round.COMPARISON, base_coders=1)
    coder = _coder("c")
    set_standing(coder, version, CoderStanding.ADMITTED)
    cohort, _ = draw_pilot(pilot, others=40)
    Grant.objects.create(user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    assign_cohort(cohort)
    order = list(
        ClassificationAssignment.objects.filter(assigned_to=coder)
        .order_by("assigned_at", "id")
        .values_list("article_id", flat=True)
    )
    strata = dict(cohort.samples.values_list("article_id", "stratum"))
    first_half = [strata[story] for story in order[:40]]
    assert 5 < first_half.count("civic") < 35


# ----------------------------------------------------------- training


def _article(n, label, dataset="", status="labeled"):
    from explorer.models import Article, CandidateLink, Source

    source, _ = Source.objects.using("crawler").get_or_create(
        id="s1", defaults={"host": "a.example", "canonical_name": "A Paper"}
    )
    link = CandidateLink.objects.using("crawler").create(
        id=f"cl-{n}", url=f"https://a.example/{n}", source=source, status="extracted"
    )
    Article.objects.using("crawler").create(
        id=f"a-{n}",
        candidate_link=link,
        url=link.url,
        title=f"Story {n}",
        status=status,
        text="word " * 50,
        primary_label=label,
        primary_label_confidence=0.9,
        dataset_id=dataset or None,
    )


def _training(floor):
    return open_round(
        "Training",
        Round.COLLECTION,
        base_coders=2,
        finish_rule={"per_category": floor},
    )


def test_v1_records_with_a_label_count_first(v1):
    counts = training_counts(_training(5))
    # 0.9 and 0.7 labels count; the rejected story and the one with no
    # agreed label do not.
    assert counts["Sports"] == 1 and counts["Health"] == 1
    assert counts["Education"] == 1 and counts["Civic Life"] == 1
    assert counts["Civic information"] == 0
    assert sum(counts.values()) == 4


def test_a_piloted_v1_story_counts_by_its_v2_label(v1):
    version = CodebookVersion.v1()
    pilot = open_round("Pilot", Round.COMPARISON, base_coders=2)
    cohort, _ = draw_pilot(pilot, others=0)
    a, b = _coder("a"), _coder("b")
    for user in (a, b):
        ClassificationDecision.objects.create(
            cohort=cohort,
            article_id="v1:2",
            decided_by=user,
            primary_label="Economic Development",
            codebook_version=version,
        )
    counts = training_counts(_training(5))
    # V1 called it Civic Life; V2 coders agreed on something else.
    assert counts["Civic Life"] == 0 and counts["Economic Development"] == 1
    # v1:4 was piloted and has no V2 agreement yet, so it counts nowhere.
    assert sum(counts.values()) == 4


def test_the_first_batch_is_divided_by_need(v1):
    rows = {r["label"]: r for r in training_plan(_training(3), 26)}
    # Sports has 1 of 3 and needs 2; Transportation has none and needs 3.
    assert rows["Sports"]["need"] == 2 and rows["Transportation Systems"]["need"] == 3
    assert rows["Sports"]["draw"] == 2 and rows["Transportation Systems"]["draw"] == 3
    assert sum(r["draw"] for r in rows.values()) == 26


def test_a_full_category_draws_nothing(v1, crawler_schema):
    for n in range(6):
        _article(n, "Sports")
        _article(100 + n, "Transportation Systems")
    training = _training(1)  # Sports already has its one from V1
    cohort, plan = draw_training_batch(training, 6)
    drawn = {r["label"]: r["drawn"] for r in plan}
    assert drawn["Sports"] == 0
    assert drawn["Transportation Systems"] == 1
    assert set(cohort.samples.values_list("stratum", flat=True)) == {"category"}


def test_stories_come_from_what_the_model_has_labelled(crawler_schema, db):
    """`labeled` and `enriched` hold the corpus; `cleaned` holds almost
    nothing, and a wire story is never drawn."""
    _article(1, "Sports", status="labeled")
    _article(2, "Sports", status="enriched")
    _article(3, "Sports", status="wire")
    cohort, _ = draw_training_batch(_training(5), 50)
    assert set(cohort.samples.values_list("article_id", flat=True)) == {"a-1", "a-2"}


def test_each_category_takes_from_the_smaller_states_first(crawler_schema, db):
    from explorer.models import Dataset

    for key, slug in (
        ("mo", "Mizzou-Missouri-State"),
        ("wa", "WSU-Washington-State"),
        ("pa", "Penn-State-Lehigh"),
        ("vt", "VT-Community-News"),
    ):
        Dataset.objects.using("crawler").create(id=key, slug=slug, label=slug)
    for n in range(30):
        _article(n, "Education", "mo")
    for n in range(30, 40):
        _article(n, "Education", "wa")
    for n in range(40, 42):
        _article(n, "Education", "pa")  # fewer than a third: take them all
    for n in range(42, 60):
        _article(n, "Education", "vt")

    training = open_round(
        "Training", Round.COLLECTION, finish_rule={"per_category": 12}
    )
    # Only Education has stories, so only Education is drawn.
    cohort, plan = draw_training_batch(training, 120)
    by_state = {}
    for dataset in cohort.samples.values_list("dataset_id", flat=True):
        by_state[dataset] = by_state.get(dataset, 0) + 1
    assert by_state == {"wa": 4, "pa": 2, "mo": 6}
    assert "vt" not in by_state
    assert PER_SMALL_DATASET == 60


def test_the_hit_rate_sets_how_many_more_are_drawn(crawler_schema, db):
    for n in range(40):
        _article(n, "Education")
    version = CodebookVersion.v1()
    training = open_round(
        "Training", Round.COLLECTION, base_coders=2, finish_rule={"per_category": 10}
    )
    cohort, _ = draw_training_batch(training, 100)
    a, b = _coder("a"), _coder("b")
    drawn = sorted(
        cohort.samples.filter(model_label="Education").values_list(
            "article_id", flat=True
        )
    )
    assert len(drawn) == 10
    # Coders place half of them in Education and half elsewhere.
    for i, story in enumerate(drawn):
        label = "Education" if i % 2 == 0 else "Health"
        for user in (a, b):
            ClassificationDecision.objects.create(
                cohort=cohort,
                article_id=story,
                decided_by=user,
                primary_label=label,
                codebook_version=version,
            )
    assert hit_rates(training)["Education"] == pytest.approx(0.5)
    row = next(r for r in training_plan(training, 1000) if r["label"] == "Education")
    # Five found, five to go, at one in two: ten more draws.
    assert (row["have"], row["need"], row["draw"]) == (5, 5, 10)


def test_cohorts_outside_a_round_also_leave_vermont_out(crawler_schema, db):
    from explorer.models import Dataset
    from review.classification import draw_cohort

    Dataset.objects.using("crawler").create(
        id="mo", slug="Mizzou-Missouri-State", label="mo"
    )
    Dataset.objects.using("crawler").create(
        id="vt", slug="VT-Community-News", label="vt"
    )
    for n in range(8):
        _article(n, "Sports", "mo")
    for n in range(8, 40):
        _article(n, "Sports", "vt")
    cohort, _ = draw_cohort(8)
    assert set(cohort.samples.values_list("dataset_id", flat=True)) == {"mo"}
    assert ClassificationCohort.objects.count() == 1
