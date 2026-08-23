"""What a role permits, and which application it permits it in.

ROADMAP item 1. Two levels rather than two vocabularies: a **role** is a
name someone is granted — viewer, reviewer, editor, designer, admin — and
a **privilege** is what that name permits — read, write, design. A grant
stores the role; this module is the single place a role is turned into
privileges, so no check re-derives that mapping for itself.

The set of privileges is small and stable. The set of role names grows as
applications join the suite, and each new one is a row in ROLES rather
than a new concept.

**The one place the two levels do not collapse.** A reviewer and an
editor both hold `write`. What separates them is how many records one
click changes: a reviewer answers review-queue questions and records
dispositions, an editor may also import and run bulk operations. No
privilege expresses that, so the import paths ask `may_import`, which
tests the role. Everywhere else a check should ask for a privilege and
never mention a role name.
"""

# --- privileges -------------------------------------------------------------

READ = "read"
WRITE = "write"
DESIGN = "design"

PRIVILEGES = (READ, WRITE, DESIGN)

# --- roles ------------------------------------------------------------------

VIEWER = "viewer"
REVIEWER = "reviewer"
EDITOR = "editor"
DESIGNER = "designer"
ADMIN = "admin"

ROLES = (VIEWER, REVIEWER, EDITOR, DESIGNER, ADMIN)

ROLE_CHOICES = [
    (VIEWER, "Viewer — read"),
    (REVIEWER, "Reviewer — read, and dispositions"),
    (EDITOR, "Editor — read, dispositions, and imports"),
    (DESIGNER, "Designer — read, and authoring visuals"),
    (ADMIN, "Admin — everything, every scope"),
]

#: A role is defined as the set of privileges it carries. A designer does
#: not hold `write`: authoring a visual is `design`, and it does not carry
#: the right to change the records the visual draws on.
ROLE_PRIVILEGES = {
    VIEWER: frozenset({READ}),
    REVIEWER: frozenset({READ, WRITE}),
    EDITOR: frozenset({READ, WRITE}),
    DESIGNER: frozenset({READ, DESIGN}),
    ADMIN: frozenset({READ, WRITE, DESIGN}),
}

#: Roles whose single action may change many records: imports, bulk
#: operations. See the module docstring — this is a role test on purpose.
IMPORTING_ROLES = frozenset({EDITOR, ADMIN})

#: Spend is a management fact rather than a research one, so cost figures
#: follow `write` and not `read` (ROADMAP item 1). Named here rather than
#: written as a bare WRITE at the call site, so the reason survives.
COST_PRIVILEGE = WRITE


def privileges_for_role(role):
    """The privileges a role carries, or an empty set for an unknown name."""
    return ROLE_PRIVILEGES.get(role, frozenset())


def role_permits(role, privilege):
    """Does this role carry this privilege?"""
    return privilege in privileges_for_role(role)


def role_may_import(role):
    """Imports and bulk operations, which no privilege distinguishes."""
    return role in IMPORTING_ROLES
