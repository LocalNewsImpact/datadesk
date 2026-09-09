"""A cohort has to be defensible before anybody codes 3,000 rows.

Three properties make the sample worth having, and each fails silently:

- every record is seen by the same number of coders, and no coder sees
  one twice;
- how a record was drawn travels with it, because a rate measured over
  rows that were not equally likely to be drawn is not a rate;
- membership is permanent, so a figure computed over a cohort does not
  move when the next one is drawn.

See docs/CLASSIFICATION_REVIEW_QUEUE.md sections 3 and 7b.
"""

import pytest
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction

from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def coders(db):
    return [
        User.objects.create_user(name, email=f"{name}@localnewsimpact.org")
        for name in ("ed", "sam", "jo", "kit")
    ]


# ------------------------------------------------------------- the cohort


def test_a_cohort_is_a_scope_a_grant_can_name():
    """A classifier is granted a cohort rather than a dataset, and
    `Grant.scope` is a slug -- so the slug has to be derived from the
    number rather than typed, or a grant can name a cohort that does not
    exist."""
    cohort = ClassificationCohort.objects.create(number=7, slug="cohort-7")
    assert cohort.slug == ClassificationCohort.slug_for(7)
    assert cohort.is_open


def test_a_cohort_number_is_unique():
    ClassificationCohort.objects.create(number=1, slug="cohort-1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationCohort.objects.create(number=1, slug="cohort-1-again")


# ---------------------------------------------------------- the guarantees


def test_a_record_is_sampled_once_per_cohort():
    """Membership is permanent and singular: the same article drawn twice
    into one cohort would be counted twice in every rate over it."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    ClassificationSample.objects.create(
        cohort=cohort, article_id="a-1", stratum=ClassificationSample.RANDOM
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationSample.objects.create(
            cohort=cohort, article_id="a-1", stratum=ClassificationSample.CONFIDENT
        )


def test_a_coder_is_assigned_a_record_once(coders):
    """Never the same record twice: two dispositions from one person are
    one opinion counted twice, which is what three coders exist to
    avoid."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    ClassificationAssignment.objects.create(
        cohort=cohort, article_id="a-1", assigned_to=coders[0]
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationAssignment.objects.create(
            cohort=cohort, article_id="a-1", assigned_to=coders[0]
        )


def test_a_coder_answers_a_record_once(coders):
    """The same rule on the decision itself. `ReviewDecision` could not
    express this -- it is unique per article, so it permits exactly one
    rater."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    ClassificationDecision.objects.create(
        cohort=cohort, article_id="a-1", decided_by=coders[0], primary_label="Sports"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        ClassificationDecision.objects.create(
            cohort=cohort,
            article_id="a-1",
            decided_by=coders[0],
            primary_label="Health",
        )


def test_three_coders_may_answer_the_same_record(coders):
    """The whole reason this is not ReviewDecision."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    for coder in coders[:3]:
        ClassificationDecision.objects.create(
            cohort=cohort,
            article_id="a-1",
            decided_by=coder,
            primary_label="Sports",
        )
    assert ClassificationDecision.objects.filter(article_id="a-1").count() == 3


def test_a_rejection_is_a_disposition_and_not_a_label(coders):
    """A record three people call garbage is not training data, and the
    agreement between them is still worth counting."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    decision = ClassificationDecision.objects.create(
        cohort=cohort,
        article_id="a-1",
        decided_by=coders[0],
        reject_reason=ClassificationDecision.TOO_SHORT,
    )
    assert decision.is_rejection
    assert decision.primary_label == ""


def test_not_local_is_offered_because_coders_reached_for_it():
    """It was the most-used rejection in the historical cohorts -- 24 of
    500 -- and is missing from most proposals of this list."""
    assert ClassificationDecision.NOT_LOCAL in dict(ClassificationDecision.REJECTIONS)


# ------------------------------------------------------------ the command


def test_it_refuses_a_cohort_that_cannot_reach_its_target(coders, crawler_schema):
    """Two coders cannot give three dispositions apiece. Refusing is
    better than a cohort that can never finish."""
    with pytest.raises(CommandError, match="cannot give"):
        call_command(
            "draw_classification_cohort",
            size=10,
            coders="ed,sam",
            target=3,
        )


def test_it_refuses_a_coder_who_does_not_exist(coders, crawler_schema):
    with pytest.raises(CommandError, match="no such user"):
        call_command("draw_classification_cohort", size=10, coders="nobody")


def test_a_dry_run_writes_nothing(crawler_schema):
    call_command("draw_classification_cohort", size=10, dry_run=True)
    assert not ClassificationCohort.objects.exists()
    assert not ClassificationSample.objects.exists()


def test_the_strata_are_named_and_stored():
    """`confident` and `uncertain` follow from the row and could be
    recomputed. Membership of the random stratum follows from nothing
    about the row -- which is what makes it random -- so it has to be
    stored."""
    assert ClassificationSample.RANDOM in dict(ClassificationSample.STRATA)
    assert ClassificationSample.UNLABELLED in dict(ClassificationSample.STRATA)


def test_the_inclusion_probability_defaults_to_the_whole_stratum():
    """1.0 means the stratum was taken whole. An oversampled draw records
    less, and that number is what lets the same rows serve training and
    evaluation both."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    sample = ClassificationSample.objects.create(
        cohort=cohort, article_id="a-1", stratum=ClassificationSample.RANDOM
    )
    assert sample.inclusion_probability == 1.0


# ------------------------------------------- what a real draw delivers


@pytest.fixture
def corpus(crawler_schema):
    """Sixty articles: forty labelled across the confidence bands, twenty
    the analysis stage has not reached."""
    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.using("crawler").create(
        id="s1", host="a.example", canonical_name="A Paper"
    )
    labels = ["Sports", "Health", "Civic Life", "Transportation Systems"]
    for n in range(60):
        link = CandidateLink.objects.using("crawler").create(
            id=f"cl-{n}",
            url=f"https://a.example/{n}",
            source=source,
            status="extracted",
        )
        confidence, label = None, None
        if n < 20:
            confidence, label = 0.9, labels[n % len(labels)]
        elif n < 40:
            confidence, label = 0.3, labels[n % len(labels)]
        Article.objects.using("crawler").create(
            id=f"a-{n}",
            candidate_link=link,
            url=f"https://a.example/{n}",
            title=f"Story {n}",
            status="labeled",
            primary_label=label,
            primary_label_confidence=confidence,
        )
    return 60


def test_every_record_gets_the_same_number_of_coders(coders, corpus):
    """The guarantee the whole assignment machinery exists for. Serving
    whatever has fewest answers gives no guarantee at all: a keen
    reviewer answers a thousand records once each and nothing reaches
    three."""
    call_command(
        "draw_classification_cohort", size=20, coders="ed,sam,jo,kit", target=3
    )
    cohort = ClassificationCohort.objects.get()
    per_article = {}
    for assignment in ClassificationAssignment.objects.filter(cohort=cohort):
        per_article.setdefault(assignment.article_id, set()).add(
            assignment.assigned_to_id
        )
    assert per_article, "nothing was assigned"
    for article_id, who in per_article.items():
        # A set, so three distinct people rather than one person three
        # times -- which would be one opinion counted thrice.
        assert len(who) == 3, (article_id, who)


def test_the_work_is_spread_evenly(coders, corpus):
    """Every coder gets roughly the same amount. A wheel that restarts
    per record would load the first coders and starve the last."""
    call_command(
        "draw_classification_cohort", size=20, coders="ed,sam,jo,kit", target=3
    )
    counts = {}
    for assignment in ClassificationAssignment.objects.all():
        counts[assignment.assigned_to_id] = counts.get(assignment.assigned_to_id, 0) + 1
    assert len(counts) == 4, counts
    assert max(counts.values()) - min(counts.values()) <= 1, counts


def test_a_cohort_names_its_own_scope(coders, corpus):
    """The slug is what a Grant scopes to, so it has to exist and be
    derived rather than typed."""
    call_command("draw_classification_cohort", size=10)
    cohort = ClassificationCohort.objects.get()
    assert cohort.slug == f"cohort-{cohort.number}"


def test_the_draw_records_how_each_row_was_selected(coders, corpus):
    """Every sample carries its stratum and its inclusion probability,
    because a rate over rows that were not equally likely to be drawn is
    not a rate."""
    call_command("draw_classification_cohort", size=20)
    samples = ClassificationSample.objects.all()
    assert samples.exists()
    for sample in samples:
        assert sample.stratum in dict(ClassificationSample.STRATA)
        assert 0 < sample.inclusion_probability <= 1.0


def test_balancing_the_labels_records_a_smaller_probability(coders, corpus):
    """Oversampling the rare categories is right -- it is what makes them
    learnable -- and costs nothing provided the draw says how much it
    over-represented them."""
    call_command("draw_classification_cohort", size=20, balance_labels=True)
    boosted = ClassificationSample.objects.filter(
        stratum__in=(ClassificationSample.CONFIDENT, ClassificationSample.UNCERTAIN)
    )
    assert boosted.exists()
    assert any(s.inclusion_probability < 1.0 for s in boosted)


def test_an_article_is_drawn_into_one_stratum_only(coders, corpus):
    """The strata overlap by construction -- a confident row is also in
    the corpus the random stratum draws from -- so the draw has to take
    each article once or a rate over the cohort double-counts it."""
    call_command("draw_classification_cohort", size=40)
    ids = list(ClassificationSample.objects.values_list("article_id", flat=True))
    assert len(ids) == len(set(ids))
