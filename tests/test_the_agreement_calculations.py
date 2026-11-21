"""Every agreement calculation, against cases worked by hand.

The same code scores the 2023 V1 coding and a V2 round, so that a
difference between the two is a difference in the coding. The last test
runs it over the real V1 files where they are available and checks the
figures the pilot is judged against.

See docs/CIN_V2_DATADESK.md, WP7.
"""

import os

import pytest

from review import cin_metrics as m


def a(label, coder="x", secondary=""):
    return {"coder": coder, "label": label, "secondary": secondary}


# ---------------------------------------------------------- scoring rule


def test_the_scoring_rule():
    assert m.score(a("Sports"), a("Sports")) is True
    assert m.score(a("Sports"), a("Health")) is False
    # A category against a rejection: they disagreed about whether the
    # story should be labelled at all.
    assert m.score(a("Sports"), a("")) is False
    # Two rejections: not a story to label, so not scored either way.
    assert m.score(a(""), a("")) is None


def test_first_pair_agreement_counts_one_trial_a_story():
    stories = {
        "1": [a("Sports"), a("Sports"), a("Health")],  # third coder ignored
        "2": [a("Sports"), a("Health")],
        "3": [a(""), a("")],  # not scored
        "4": [a("Health"), a("")],  # mismatch
        "5": [a("Health")],  # one coder: no pair
    }
    got = m.first_pair_agreement(stories)
    assert (got["hits"], got["n"]) == (1, 3)
    assert got["rate"] == pytest.approx(1 / 3)
    assert got["low"] < got["rate"] < got["high"]


def test_a_categorys_rate_is_over_stories_either_coder_put_in_it():
    stories = {
        "1": [a("Sports"), a("Sports")],
        "2": [a("Sports"), a("Health")],
        "3": [a("Health"), a("Health")],
        "4": [a("Sports"), a("")],
    }
    sports = m.first_pair_agreement(stories, "Sports")
    assert (sports["hits"], sports["n"]) == (1, 3)
    health = m.first_pair_agreement(stories, "Health")
    assert (health["hits"], health["n"]) == (1, 2)


def test_pairwise_agreement_counts_every_scored_pair():
    stories = {"1": [a("Sports"), a("Sports"), a("Health")], "2": [a(""), a("")]}
    got = m.pairwise_agreement(stories)
    # Three pairs on story 1, one of them a match; story 2 is not scored.
    assert (got["hits"], got["n"]) == (1, 3)


def test_wilson_interval():
    low, high = m.wilson(83, 100)
    assert low == pytest.approx(0.7445, abs=1e-3)
    assert high == pytest.approx(0.8910, abs=1e-3)
    assert m.wilson(0, 0) == (None, None)


def test_groups_are_counted_at_their_given_share():
    civic = {
        str(i): [a("Sports"), a("Sports" if i < 5 else "Health")] for i in range(10)
    }
    other = {f"o{i}": [a("Sports"), a("Sports")] for i in range(10)}
    got = m.weighted_agreement([(civic, 0.4), (other, 0.6)])
    # 50% and 100%, at 40% and 60%.
    assert got["rate"] == pytest.approx(0.8)
    assert got["low"] < 0.8 < got["high"]
    assert m.weighted_agreement([({}, 0.4), (other, 0.6)])["rate"] is None


def test_the_change_from_before_is_measured_on_the_same_stories():
    now = {str(i): [a("Sports"), a("Sports")] for i in range(40)}
    before = {
        str(i): [a("Sports"), a("Sports" if i < 10 else "Health")] for i in range(40)
    }
    got = m.paired_change(now, before, resamples=500)
    assert got["change"] == pytest.approx(0.75)
    assert got["low"] > 0.5 and got["n"] == 40
    # Seeded: the same answers give the same interval.
    assert got == m.paired_change(now, before, resamples=500)


# ------------------------------------------------------------ reliability


def test_alpha_is_one_for_perfect_agreement_and_low_for_none():
    perfect = {str(i): [a("Sports" if i % 2 else "Health")] * 2 for i in range(10)}
    assert m.krippendorff_alpha(perfect) == pytest.approx(1.0)
    opposed = {
        str(i): [a("Sports" if i % 2 else "Health"), a("Health" if i % 2 else "Sports")]
        for i in range(10)
    }
    assert m.krippendorff_alpha(opposed) < 0
    assert m.krippendorff_alpha({"1": [a("Sports")] * 2}) is None


def test_alpha_matches_a_published_worked_example():
    """Two coders, ten stories, two values, one disagreement, worked by
    hand from the coincidence counts."""
    first, second = "aaabaaaaab", "aaaaaaaaab"
    stories = {str(i): [a(first[i]), a(second[i])] for i in range(10)}
    # Hand count: 9 matching units of 10; values a x17, b x3.
    # D_o = 2 (one disagreeing unit, 1*1/(2-1) each way)
    # D_e = (17*3 + 3*17) / 19 = 5.368
    assert m.krippendorff_alpha(stories) == pytest.approx(1 - 2 / (102 / 19))


def test_alpha_takes_a_different_number_of_coders_on_each_story():
    stories = {
        "1": [a("Sports"), a("Sports"), a("Sports")],
        "2": [a("Health"), a("Health")],
        "3": [a("Sports"), a("Health")],
        "4": [a(""), a("")],  # dropped: nobody would label it
    }
    assert 0 < m.krippendorff_alpha(stories) < 1
    assert m.krippendorff_alpha(stories, "Sports") is not None


def test_kappa():
    pairs = [("a", "a")] * 40 + [("b", "b")] * 40 + [("a", "b")] * 20
    # observed 0.8; chance 0.6*0.4 + 0.4*0.6 = 0.48
    assert m.cohen_kappa(pairs) == pytest.approx((0.8 - 0.48) / 0.52)
    assert m.cohen_kappa([]) is None


# ----------------------------------------------------------------- labels


def test_the_label_a_story_settles_on():
    assert m.settled_label([a("Sports"), a("Sports")]) == (0.9, "Sports")
    assert m.settled_label([a("Sports"), a("Health"), a("Health")]) == (0.9, "Health")
    # One coder's first choice is another's second.
    assert m.settled_label([a("Sports"), a("Health", secondary="Sports")]) == (
        0.7,
        "Sports",
    )
    assert m.settled_label([a("Sports"), a("Health")]) == (None, None)
    # Two coders rejecting it settles that it is not labelled.
    assert m.settled_label([a(""), a(""), a("Sports")]) == (None, None)
    assert m.settled_label([a("Sports")]) == (None, None)


def test_stories_left_out_are_counted_against_what_coders_chose():
    stories = {
        "1": [a("Sports"), a("Health")],
        "2": [a("Sports"), a("Education")],
        "3": [a("Sports"), a("Sports")],  # labelled
        "4": [a("Sports")],  # not enough answers to say
    }
    assert m.left_out(stories) == {"Sports": 2, "Health": 1, "Education": 1}


# ----------------------------------------------------------------- coders


def test_a_coder_is_scored_on_first_pairs_only():
    stories = {
        "1": [a("Sports", "x"), a("Sports", "y")],
        "2": [a("Sports", "x"), a("Health", "y"), a("Health", "z")],
    }
    got = m.coder_agreement(stories)
    assert (got["x"]["hits"], got["x"]["n"]) == (1, 2)
    # z was only ever a third coder, on a story two others split on.
    assert "z" not in got


def test_pace():
    got = m.coder_pace({"x": [(30, False), (50, False), (None, True)]})
    assert got["x"]["answers"] == 3
    assert got["x"]["median_seconds"] == 40
    assert got["x"]["rejection_rate"] == pytest.approx(1 / 3)


# -------------------------------------------------------------- the sizes


def test_the_sizes_in_the_rounds_document():
    """docs/CIN_ROUNDS.md: 12% to 25% is 139 stories, 65% to 75% is 329."""
    assert m.stories_needed(0.12, 0.25) == 139
    assert m.stories_needed(0.12, 0.20) == 329
    assert m.stories_needed(0.65, 0.75) == 329
    assert m.stories_needed(0.65, 0.70) == 1377
    assert m.stories_needed(0.12, 0.25, power=0.90) == 186


def test_the_pause_rule_for_check_stories():
    """A coder is paused at the number of misses a normal coder reaches
    by chance no more than 1 time in 100."""
    assert m.pause_threshold(40, 0.10) == 10
    assert m.pause_threshold(40, 0.05) == 7
    assert m.pause_threshold(20, 0.10) == 7


def test_alpha_080_reads_as_83_percent_agreement():
    assert m.raw_agreement_for_alpha(0.80, 0.15) == pytest.approx(0.83)


def test_alpha_comes_with_an_interval_over_stories():
    def story(*labels):
        return [{"coder": i, "label": x, "secondary": ""} for i, x in enumerate(labels)]

    stories = {i: story("Sports", "Sports", "Sports") for i in range(20)}
    stories |= {i: story("Health", "Health", "Health") for i in range(20, 36)}
    stories |= {i: story("Health", "Sports", "Health") for i in range(36, 40)}
    stories[40] = story("", "", "")  # every coder rejected it: left out
    stories[41] = story("Sports")  # one answer: left out
    got = m.alpha_with_interval(stories)
    assert got["n"] == 40
    assert got["rate"] == pytest.approx(m.krippendorff_alpha(stories))
    assert got["low"] < got["rate"] < got["high"] <= 1.0
    # The same answers, the same interval.
    assert m.alpha_with_interval(stories) == got
    # Nothing to compare: no figure, and no interval.
    assert m.alpha_with_interval({0: story("Sports", "Sports")})["rate"] is None


# ------------------------------------------------- the real V1 coding

#: First-pair agreement in the 2023 V1 coding, by the scoring rule, in
#: percent. The pilot's "V1, same stories" column is these.
EXPECTED_V1 = {
    "civic": 33.2,
    "other": 72.7,
    "all": 57.0,
    "Civic information": 11.3,
    "Civic Life": 32.6,
    "all 1,000": 55.4,
}


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.skipif(
    not os.environ.get("DATADESK_V1_FILES"),
    reason="set DATADESK_V1_FILES to the folder holding the five V1 exports",
)
def test_the_v1_baseline_from_the_real_files():
    """The figures the pilot is judged against, from the files they came
    from: 934 stories kept, 367 of them civic."""
    from django.core.management import call_command

    from review.classification import ClassificationStory, v1_answers

    call_command("import_v1_stories", os.environ["DATADESK_V1_FILES"])
    kept = ClassificationStory.objects.exclude(v1_outcome=ClassificationStory.REJECTED)
    assert ClassificationStory.objects.count() == 1000
    assert kept.count() == 934
    civic = set(kept.filter(civic=True).values_list("id", flat=True))
    other = set(kept.filter(civic=False).values_list("id", flat=True))
    assert (len(civic), len(other)) == (367, 567)

    def rate(ids, label=None):
        return round(100 * m.first_pair_agreement(v1_answers(ids), label)["rate"], 1)

    got = {
        "civic": rate(civic),
        "other": rate(other),
        "all": rate(civic | other),
        "Civic information": rate(civic | other, "Civic information"),
        "Civic Life": rate(civic | other, "Civic Life"),
        "all 1,000": rate(None),
    }
    assert got == EXPECTED_V1
