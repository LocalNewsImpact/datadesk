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

PRIVILEGES = (READ, WRITE, CREATE, DESIGN)

# --- roles ------------------------------------------------------------------

VIEWER = "viewer"
REVIEWER = "reviewer"
EDITOR = "editor"
DESIGNER = "designer"
ADMIN = "admin"

ROLES = (VIEWER, REVIEWER, EDITOR, DESIGNER, ADMIN)

ROLE_CHOICES = [
    (VIEWER, "Viewer — reads and exports"),
    (REVIEWER, "Reviewer — corrects the data that is there"),
    (EDITOR, "Editor — corrects it, and brings new data in"),
    (DESIGNER, "Designer — reads, and authors visuals"),
    (ADMIN, "Admin — everything, every scope, plus user admin"),
]

#: A role is defined as the set of privileges it carries.
#:
#: A designer is a viewer plus `design`: it reads and exports everything a
#: viewer does, and adds authoring and publishing visuals. It does not hold
#: `write`, so a designer never sees a disposition to make — authoring a
#: visual is not the right to change the records it draws on.
#:
#: An editor holds all three. They are the person who starts a dataset and
#: then has full rights on it, and who is commonly a viewer on everyone
#: else's. Admin is the same set with no scope limit.
#:
#: Reading the WRITE column down this table is reading who the review queue
#: is for.
ROLE_PRIVILEGES = {
    VIEWER: frozenset({READ}),
    DESIGNER: frozenset({READ, DESIGN}),
    REVIEWER: frozenset({READ, WRITE}),
    EDITOR: frozenset({READ, WRITE, CREATE, DESIGN}),
    ADMIN: frozenset({READ, WRITE, CREATE, DESIGN}),
}

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
