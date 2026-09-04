"""One path from a posted review session to a receipt.

There were two, and they had drifted into different answers to the same
questions: what counts as a decision, what happens to a verb a row cannot
carry out, what a person is told afterwards. The proposals path was 139
lines and the extraction path 49, and the shorter one was shorter mostly
by not doing things the longer one had learned to do.

The receipt is one shape for every queue, keyed by the verb's own past
tense, so a queue that adds a verb gets it counted without touching this.

WHAT A SESSION IS
-----------------
A person reads down the page, marks what they find, and sends the lot.
Each row posts `d-<subject id>` carrying a verb name, and optionally
`v-<subject id>` carrying a typed value. Nothing is written until submit,
and the whole session is one audit event rather than one per row.
"""

from __future__ import annotations

from collections import Counter

DECISION_PREFIX = "d-"
VALUE_PREFIX = "v-"


def posted(post) -> dict[str, tuple[str, str]]:
    """The decisions in a posted form, as {subject id: (verb, value)}.

    A row with no verb is not a decision and is not here. That is how a
    reviewer leaves a question unanswered: by not answering it.
    """
    out = {}
    for key, verb in post.items():
        if not key.startswith(DECISION_PREFIX) or not verb:
            continue
        subject_id = key[len(DECISION_PREFIX) :]
        # The value is read whatever the verb is. It carries a verb's own
        # typed value where the verb takes one, and the queue's qualifier
        # otherwise -- what the thing actually is, said alongside the verb
        # rather than instead of it.
        out[subject_id] = (verb, post.get(f"{VALUE_PREFIX}{subject_id}", "").strip())
    return out


def submit(queue, decisions, subjects, user, *, stage_of=None, claim_of=None):
    """Apply a session of decisions and return a receipt.

    queue       the review.kernel.Queue being worked.
    decisions   {subject id: (verb, value)}, from `posted`.
    subjects    {subject id: subject}, already narrowed to what this
                person may act on. A decision about anything not in here
                is counted as unreachable rather than applied -- the
                narrowing is the access check, and doing it here would
                duplicate it.
    stage_of    which stage raised the claim, per subject. Optional; a
                queue whose claims have no stage passes nothing.
    claim_of    what was claimed, per subject. Defaults to the queue key,
                which is right for a queue that asks one thing.

    Nothing is written for a verb the row cannot carry out. That is not an
    error: a page loaded before somebody else acted will offer verbs that
    are no longer available, and refusing the row quietly is better than
    failing the batch. It is counted, because a submission that lands as
    nothing has to be distinguishable from one that worked.
    """
    from lnic_contracts import review_note as contract

    from review.models import ReviewDecision

    applied = Counter()
    refused = 0
    incomplete = 0
    unreachable = 0
    written = []

    for subject_id, (verb_name, value) in decisions.items():
        subject = subjects.get(subject_id)
        if subject is None:
            unreachable += 1
            continue

        # By name, not by identity. `offered` resolves a verb's per-row
        # sublabel and returns a copy, so comparing the objects refused
        # every decision on every queue whose verbs describe themselves
        # per row -- silently, as "somebody else got there first".
        verb = queue.verb(verb_name)
        offered = {available.name for available in queue.offered(subject)}
        if verb is None or verb.name not in offered:
            refused += 1
            continue

        # A verb that writes a value and has none is not a decision yet.
        # Left in the queue rather than applied as a blank. A QUALIFIER
        # with no value is not incomplete: it is a second answer the
        # reviewer chose not to give.
        if verb.takes_value and not value:
            incomplete += 1
            continue

        stage = stage_of(subject) if stage_of else ""
        claim = claim_of(subject) if claim_of else queue.key
        outcome = queue.apply(subject, verb, value, user) or {}

        ReviewDecision.objects.update_or_create(
            subject_type=queue.subject_type,
            subject_id=str(subject_id),
            field=outcome.get("field", ""),
            question=contract.question(claim, stage),
            defaults={
                "queue": queue.key,
                "subject_label": outcome.get("label", "")[:300],
                "claim": claim,
                "stage": stage,
                "verb": verb.name,
                "value": value,
                "before": outcome.get("before", ""),
                "after": outcome.get("after", ""),
                "wrote": outcome.get("wrote", {}),
                "reason": outcome.get("reason", ""),
                "decided_by": user,
            },
        )
        applied[verb.past] += 1
        written.append(subject_id)

    receipt = {verb.past: applied.get(verb.past, 0) for verb in queue.verbs}
    receipt.update(
        {
            "queue": queue.key,
            "decided": sum(applied.values()),
            # Marked but not applied, each for its own reason. Counted
            # separately: "you typed no value" and "somebody else got
            # there first" are different things to be told.
            "incomplete": incomplete,
            "refused": refused,
            "unreachable": unreachable,
            "nothing": not decisions,
        }
    )
    return receipt
