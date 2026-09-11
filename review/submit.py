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
        #
        # Per verb first. A row whose verbs take *different* vocabularies
        # posts one box each, and reading `v-<id>` alone got whichever
        # the browser sent last: the discovery queue asks "what kind of
        # story" after `story` and "what is it instead" after `not_story`,
        # and those lists have nothing in common. The bare name is still
        # honoured, because every other queue posts it.
        value = post.get(f"{VALUE_PREFIX}{subject_id}-{verb}")
        if value is None:
            value = post.get(f"{VALUE_PREFIX}{subject_id}", "")
        out[subject_id] = (verb, value.strip())
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

    from audit.models import AuditLogEntry
    from review.models import ReviewDecision

    applied = Counter()
    refused = 0
    incomplete = 0
    unreachable = 0
    rejected = []
    written = []
    # What the session did, per row, for the audit entry below.
    session = []

    for subject_id, (verb_name, value) in decisions.items():
        subject = subjects.get(subject_id)
        if subject is None:
            unreachable += 1
            continue

        # By name, not by identity. `offered` resolves a verb's per-row
        # sublabel and returns a copy, so comparing the objects refused
        # every decision on every queue whose verbs describe themselves
        # per row -- silently, as "somebody else got there first".
        # The RESOLVED verb, from what this row offers. `queue.verb` gives
        # the declaration, whose `takes_value` and `sublabel` are the
        # defaults -- so checking completeness against it asked the wrong
        # question on every row whose verbs differ.
        offered = {available.name: available for available in queue.offered(subject)}
        verb = offered.get(verb_name)
        if verb is None:
            refused += 1
            continue

        # A verb that cannot be carried out without a value is not a
        # decision yet, and is left in the queue rather than applied as a
        # blank. `takes_value` is resolved per row (kernel._resolve), so
        # this is the same rule the buttons enforce: Reject on a row
        # enrichment has finished with waits for the type.
        #
        # A qualifier nobody answered on a verb that does not need one is
        # not incomplete -- it is a second answer the reviewer chose not
        # to give.
        if verb.takes_value and verb.value_required and not value:
            incomplete += 1
            continue

        stage = stage_of(subject) if stage_of else ""
        claim = claim_of(subject) if claim_of else queue.key
        # A VALUE THE QUEUE CANNOT CARRY OUT IS NOT A 500.
        #
        # `apply` validates what a reviewer typed and raises when it does
        # not resolve -- which is correct, and which crashed the whole
        # submission. A reviewer who entered "Fatima, MO" (a real
        # community in Osage County and in no gazetteer, being
        # unincorporated) got an error page, and every OTHER decision on
        # that page was lost with it.
        #
        # Refused per row, with the reason kept: the rest of the session
        # applies, the row stays in the queue, and the reviewer is told
        # what to do about the one that did not.
        try:
            outcome = queue.apply(subject, verb, value, user) or {}
        except ValueError as refusal:
            rejected.append({"id": str(subject_id), "why": str(refusal)})
            continue

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
        session.append(
            {
                "id": str(subject_id),
                "verb": verb.name,
                "value": value,
                "before": outcome.get("before", ""),
                "after": outcome.get("after", ""),
            }
        )

    # One audit entry for the session, which is what this module has said
    # it does since it was written and did not do. Decisions reached the
    # article and the ReviewDecision table; the audit log -- the record of
    # who changed production data and to what -- had nothing in it. A
    # console whose whole premise is an audited write path cannot answer
    # "who rejected these forty articles" from the place built to answer
    # it.
    #
    # Per session, not per row: a reviewer reads down a page and sends the
    # lot, and forty entries for one action is a log nobody reads.
    if session:
        AuditLogEntry.objects.create(
            actor=user,
            action=f"review.{queue.key}.decide",
            target_table=queue.subject_type,
            target_ids=[row["id"] for row in session],
            before=[{"id": row["id"], "status": row["before"]} for row in session],
            after=[
                {
                    "id": row["id"],
                    "verb": row["verb"],
                    "value": row["value"],
                    "status": row["after"],
                }
                for row in session
            ],
            reason=f"{len(session)} decision{'' if len(session) == 1 else 's'}",
        )

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
            # What the queue would not carry out, and why. The reason is
            # the reviewer's to act on -- it names the value that did not
            # resolve -- so it travels rather than being counted.
            "rejected": rejected,
            "nothing": not decisions,
        }
    )
    return receipt
