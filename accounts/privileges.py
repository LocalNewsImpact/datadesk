"""What a role permits, and which application it permits it in.

ROADMAP item 1. Two levels rather than two vocabularies: a **role** is a
name someone is granted — viewer, reviewer, editor, designer, admin — and
a **privilege** is what that name permits — read, write, design. A grant
stores the role; this module is the single place a role is turned into
privileges, so no check re-derives that mapping for itself.

The set of privileges is small and stable. The set of role names grows as
applications join the suite, and each new one is a row in ROLES rather
than a new concept.

**`write` corrects what is there; `create` brings new data in.** That is
the reviewer/editor line, and it is a privilege rather than a role test.
A reviewer works the review queue and fixes the records in front of them.
An editor does that and may also import — which does not correct a record,
it adds records that were not there. Two different powers, so two
privileges.

An earlier draft made this a role test on the grounds that no privilege
expressed "how many records one click changes". That framing was wrong:
the difference is not volume, it is whether anything new arrives. Named
properly, it collapses into the same vocabulary as everything else, and
no check has to mention a role name.

**One thing is still not a privilege.** Administration — users, roles,
the audit log — is not scoped to a dataset, so `is_application_admin`
asks about the role. Everything else, including starting a dataset,
asks for a privilege.

**Editor and admin carry the same privileges, and differ by reach.** An
editor holds everything on the datasets they hold it on — they are the
person who starts a dataset and then owns it. An admin holds the same
everywhere, in every application, plus user administration. So the
question a check asks is almost never "editor or admin"; it is "may this
person do this here", and the scope answers it.
"""

# --- privileges -------------------------------------------------------------

READ = "read"
WRITE = "write"
CREATE = "create"
DESIGN = "design"
#: Saying what an article is about, in the classification queue.
#:
#: Separate from `write` because the two are different trusts. `write`
#: corrects the records -- a byline, a headline, a status -- and a
#: classifier does not do that. It reads a story and records a judgement
#: about it, into a table of its own, and can do nothing else.
CLASSIFY = "classify"

PRIVILEGES = (READ, WRITE, CREATE, DESIGN, CLASSIFY)

# --- roles ------------------------------------------------------------------

VIEWER = "viewer"
REVIEWER = "reviewer"
EDITOR = "editor"
DESIGNER = "designer"
ADMIN = "admin"

#: The roles in order, least to most. Order is the meaning: each one
#: carries everything below it and adds one thing of its own, so the
#: ladder is what the privilege table is built from rather than a
#: comment beside it.
ROLES = (VIEWER, DESIGNER, REVIEWER, EDITOR, ADMIN)

#: Not a rung. `classifier` is a role of its own, deliberately outside
#: the ladder, and the ladder is why it has to be.
#:
#: A rung carries everything beneath it, so a classifier placed anywhere
#: on it would either hold `read` over the whole corpus -- and the point
#: of the classification queue is that a coder sees the records they
#: were assigned and no others -- or hand `classify` to every viewer and
#: designer above it, and then there is no saying who is doing the
#: classification.
#:
#: Controlling both is the requirement: which people classify, and which
#: records they are given. Neither survives inheritance.
CLASSIFIER = "classifier"

ROLE_CHOICES = [
    (VIEWER, "Viewer — reads and exports"),
    (DESIGNER, "Designer — and authors visuals"),
    (REVIEWER, "Reviewer — and corrects the data that is there"),
    (EDITOR, "Editor — and brings new data in"),
    (ADMIN, "Admin — everything, every scope, plus user admin"),
    # Last, and apart: not a step on the ladder above it.
    (CLASSIFIER, "Classifier — labels assigned stories, and nothing else"),
]

#: What each role adds to the one below it.
#:
#: A role carries every privilege of every role beneath it. Written as
#: five separate sets, that was a promise nobody was keeping: a reviewer
#: held `write` and not `design`, so somebody trusted to correct the
#: records could not draw a chart of them -- which reads as an oversight
#: because it was one.
#:
#: Written as a ladder it cannot drift. Each row says the one thing that
#: role adds, the sets are accumulated below, and a test asserts each
#: role is a superset of its predecessor.
#:
#: The order of the rungs is the claim being made:
#:
#:   viewer     reads and exports
#:   designer   ...and authors visuals from what it can read
#:   reviewer   ...and corrects the records it is drawing
#:   editor     ...and brings new records in
#:   admin      ...and is not limited to one dataset
#:
#: `classifier` is not on this ladder. See CLASSIFIER above.
#:
#: Admin adds no privilege. What makes it admin is the absence of a
#: scope, which the Grant model enforces rather than this table.
ROLE_ADDS = {
    VIEWER: frozenset({READ}),
    DESIGNER: frozenset({DESIGN}),
    REVIEWER: frozenset({WRITE}),
    # Two, not one. An editor runs the classification programme -- draws
    # the cohorts, reads the agreement, decides what is settled -- so
    # `classify` arrives here rather than lower down. A reviewer
    # corrects records and does not label them; that is a different job
    # and a different person.
    EDITOR: frozenset({CREATE, CLASSIFY}),
    ADMIN: frozenset(),
}


def _accumulate():
    """Each role's privileges: its own, plus everything below it."""
    carried, out = set(), {}
    for role in ROLES:
        carried |= ROLE_ADDS[role]
        out[role] = frozenset(carried)
    return out


#: A role is the set of privileges it carries, accumulated up the ladder.
#: Reading the WRITE column down this table is reading who the review
#: queue is for.
ROLE_PRIVILEGES = _accumulate()

#: `classify` and nothing else. Not `read`: a classifier who can read the
#: corpus can choose what to label, and a sample somebody chose is not a
#: sample. The queue serves them their assignments; that is the whole of
#: their access.
ROLE_PRIVILEGES[CLASSIFIER] = frozenset({CLASSIFY})

#: Every role the console grants: the ladder, plus the ones beside it.
#:
#: `ROLES` is the ladder and is what the superset property is about.
#: This is what "is this a real role" should be asked against -- a check
#: written against `ROLES` alone would quietly stop covering anything
#: that is deliberately not a rung.
ALL_ROLES = (*ROLES, CLASSIFIER)

#: Bringing new data in — an import, or starting a dataset. Both are
#: `create`: neither corrects a record that exists, both add records that
#: did not. Starting a dataset asks the question without a scope, because
#: the dataset does not exist yet; an import asks it about the dataset the
#: rows are landing in.
IMPORT_PRIVILEGE = CREATE

#: Spend is a management fact rather than a research one, so cost figures
#: follow `write` and not `read` (ROADMAP item 1). Named here rather than
#: written as a bare WRITE at the call site, so the reason survives.
COST_PRIVILEGE = WRITE

#: Taking data away follows `read`. The deliverable CSVs are the shape the
#: research is published in, so withholding them from the people doing the
#: research would make `read` mean "look at a page". A viewer and a
#: designer both export; what limits them is which datasets they can read,
#: not whether they may export at all. The export views already carry
#: `@role_required` rather than an editor check — this names why.
EXPORT_PRIVILEGE = READ


def privileges_for_role(role):
    """The privileges a role carries, or an empty set for an unknown name."""
    return ROLE_PRIVILEGES.get(role, frozenset())


def role_permits(role, privilege):
    """Does this role carry this privilege?"""
    return privilege in privileges_for_role(role)


def role_may_create(role):
    """Bring new data in: an import, or a new dataset."""
    return role_permits(role, CREATE)
