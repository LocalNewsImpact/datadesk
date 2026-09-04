"""One queue, in three parts, used by every queue in the console.

There were two queues here and they agreed on nothing: two records, two
submit paths, two receipt shapes and two templates, about 750 lines of
parallel code that had converged only where somebody had recently touched
both. A third was coming (the source directory) and a fourth (analysis),
each of which would have added its own.

These tests are about the kernel rather than about extraction: what a
queue has to declare, what the submit path does with a verb a row cannot
carry out, and that a new queue needs no new machinery.
"""

import pytest
from django.contrib.auth.models import User

from review import kernel
from review.models import ReviewDecision


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


class _Subject:
    """Something decidable, standing in for an article or a record."""

    def __init__(self, pk, allowed=None):
        self.pk = pk
        self.allowed = allowed


def _queue(**overrides):
    written = []

    def apply(subject, verb, value, user):
        written.append((subject.pk, verb.name, value))
        return {"label": f"subject {subject.pk}", "before": "was", "after": verb.name}

    settings = {
        "key": "test-queue",
        "subject_type": "widget",
        "verbs": (
            kernel.Verb(name="accept", label="Accept", past="accepted"),
            kernel.Verb(name="fix", label="Fix", takes_value=True, past="fixed"),
        ),
        "apply": apply,
    }
    settings.update(overrides)
    queue = kernel.Queue(**settings)
    # The Queue is frozen, which is the point of it: a queue is a
    # declaration, not something a request mutates. The recorder lives
    # beside it instead.
    return queue, written


# --- what a queue has to declare ---------------------------------------------


def test_a_verb_is_data_not_markup():
    """The button used to be written into each template three times over
    -- text, tooltip and dock tally -- which is how the two queues' verbs
    drifted with the difference living in HTML."""
    verb = kernel.Verb(name="accept", label="Accept", sublabel="Leave it out")
    assert verb.name == "accept"
    assert verb.label == "Accept"
    assert verb.sublabel == "Leave it out"


def test_a_verb_without_a_name_is_refused():
    """The name is what gets recorded."""
    with pytest.raises(ValueError):
        kernel.Verb(name="", label="Accept")


def test_a_past_tense_is_derived_when_not_given():
    """So a receipt can say "3 accepted" without every queue spelling it."""
    assert kernel.Verb(name="accept", label="Accept").past == "accepted"
    spelled = kernel.Verb(name="reject", label="Reject", past="rejected")
    assert spelled.past == "rejected"


def test_a_queue_with_no_verbs_is_refused():
    with pytest.raises(ValueError):
        _queue(verbs=())[0]


def test_a_queue_cannot_declare_a_verb_twice():
    with pytest.raises(ValueError):
        _queue(
            verbs=(kernel.Verb(name="a", label="A"), kernel.Verb(name="a", label="A"))
        )[0]


def test_a_verb_the_queue_does_not_offer_is_not_resolved():
    """A posted verb is user input. One that reached `apply` would report
    success and do nothing."""
    assert _queue()[0].verb("demolish") is None


# --- which verbs a row can carry out -----------------------------------------


def test_a_row_offers_every_verb_by_default():
    queue, written = _queue()
    assert queue.offered(_Subject("s1")) == queue.verbs


def test_a_row_offers_only_what_it_can_carry_out():
    """Reject needs a body to hand back; re-extract needs the archived
    capture. A button that cannot act is a promise the submit path then
    refuses without saying so."""
    queue, written = _queue(verbs_for=lambda subject: subject.allowed)
    offered = queue.offered(_Subject("s1", allowed=["accept"]))
    assert [verb.name for verb in offered] == ["accept"]


# --- the registry ------------------------------------------------------------


def test_the_extraction_queue_is_registered():
    queue = kernel.get("extraction")
    assert queue.subject_type == "article"
    assert {verb.name for verb in queue.verbs} == {
        "accept",
        "reject",
        "reclassify",
        "reextract",
    }


def test_an_unknown_queue_says_which_ones_exist():
    """A key comes from this repository's own URL configuration, so a
    missing one is a wiring mistake and not something to degrade around."""
    with pytest.raises(LookupError) as raised:
        kernel.get("no-such-queue")
    assert "extraction" in str(raised.value)


def test_registering_a_different_queue_under_a_taken_key_is_refused():
    kernel.register(_queue(key="test-registry")[0])
    with pytest.raises(ValueError):
        kernel.register(_queue(key="test-registry")[0])


# --- one submit path ---------------------------------------------------------


def _submit(queue, decisions, subjects, user):
    from review import submit as review_submit

    return review_submit.submit(queue, decisions, subjects, user)


@pytest.mark.django_db
def test_a_decision_is_recorded_against_its_subject(reviewer):
    queue, written = _queue()
    subject = _Subject("s1")
    receipt = _submit(queue, {"s1": ("accept", "")}, {"s1": subject}, reviewer)

    assert receipt["accepted"] == 1
    assert receipt["decided"] == 1
    recorded = ReviewDecision.objects.get()
    assert (recorded.subject_type, recorded.subject_id) == ("widget", "s1")
    assert recorded.verb == "accept"
    assert recorded.queue == "test-queue"


@pytest.mark.django_db
def test_a_verb_the_row_cannot_carry_out_writes_nothing(reviewer):
    """Not an error: a page loaded before somebody else acted offers verbs
    that are no longer available. Counted, because a submission that lands
    as nothing has to be distinguishable from one that worked."""
    queue, written = _queue(verbs_for=lambda subject: subject.allowed)
    subject = _Subject("s1", allowed=["accept"])
    receipt = _submit(queue, {"s1": ("fix", "x")}, {"s1": subject}, reviewer)

    assert receipt["refused"] == 1
    assert receipt["decided"] == 0
    assert written == []
    assert not ReviewDecision.objects.exists()


@pytest.mark.django_db
def test_a_verb_that_takes_a_value_and_has_none_stays_in_the_queue(reviewer):
    queue, written = _queue()
    receipt = _submit(queue, {"s1": ("fix", "")}, {"s1": _Subject("s1")}, reviewer)

    assert receipt["incomplete"] == 1
    assert receipt["decided"] == 0
    assert not ReviewDecision.objects.exists()


@pytest.mark.django_db
def test_a_subject_outside_reach_is_counted_not_applied(reviewer):
    """The narrowing IS the access check. Repeating it here would be a
    second implementation of who may act on what."""
    queue, written = _queue()
    receipt = _submit(queue, {"s1": ("accept", "")}, {}, reviewer)

    assert receipt["unreachable"] == 1
    assert not ReviewDecision.objects.exists()


@pytest.mark.django_db
def test_a_receipt_counts_every_verb_the_queue_declares(reviewer):
    """A queue that adds a verb gets it reported without touching the
    submit path or the template."""
    queue, written = _queue()
    receipt = _submit(
        queue,
        {"s1": ("accept", ""), "s2": ("fix", "a value")},
        {"s1": _Subject("s1"), "s2": _Subject("s2")},
        reviewer,
    )
    assert receipt["accepted"] == 1
    assert receipt["fixed"] == 1
    assert receipt["decided"] == 2


@pytest.mark.django_db
def test_deciding_the_same_question_again_replaces_the_answer(reviewer):
    """A person may revisit a question. Two rows for one question would
    make "what was decided" ambiguous."""
    queue, written = _queue()
    subject = _Subject("s1")
    _submit(queue, {"s1": ("accept", "")}, {"s1": subject}, reviewer)
    _submit(queue, {"s1": ("fix", "a value")}, {"s1": subject}, reviewer)

    assert ReviewDecision.objects.count() == 1
    assert ReviewDecision.objects.get().verb == "fix"


@pytest.mark.django_db
def test_two_subjects_with_the_same_id_in_different_queues_are_separate(reviewer):
    """Ids are only unique within their own table, and two queues can hold
    the same one. Neither the id nor the type alone identifies a subject."""
    _submit(_queue()[0], {"s1": ("accept", "")}, {"s1": _Subject("s1")}, reviewer)
    other, _ = _queue(key="other-queue", subject_type="gadget")
    _submit(other, {"s1": ("accept", "")}, {"s1": _Subject("s1")}, reviewer)

    assert ReviewDecision.objects.count() == 2


def test_posted_reads_a_session_from_a_form():
    from review import submit as review_submit

    parsed = review_submit.posted(
        {"d-a1": "accept", "d-a2": "", "v-a1": " typed ", "csrfmiddlewaretoken": "x"}
    )
    # A row with no verb is not a decision. That is how a reviewer leaves
    # a question unanswered: by not answering it.
    assert parsed == {"a1": ("accept", "typed")}
