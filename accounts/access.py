"""Asking whether someone may do a thing (ROADMAP item 1).

Every access question in the suite should come through here, so the rules
live in one place rather than being re-derived at each view. The shape is
always the same: a person, an application, a privilege, and optionally a
dataset.

    has_privilege(user, DATADESK, WRITE, scope="mizzou")
    permitted_scopes(user, DATADESK, READ)

A grant with an empty scope covers the whole application, so it answers
for every dataset in it. A grant naming a dataset answers only for that
one. A superuser answers yes to everything and needs no rows.
"""

from accounts.models import UNIVERSAL, WHOLE_APPLICATION, Grant
from accounts.privileges import ADMIN, CREATE, DESIGN, READ, role_permits

#: Returned by `permitted_scopes` when the person's access is not limited
#: to particular datasets — a superuser, or an application-wide grant.
#: A caller that would otherwise build a `WHERE slug IN (...)` should skip
#: the filter entirely rather than enumerate every dataset that exists.
ALL_SCOPES = "__all__"


def _grants(user, app):
    if not user.is_authenticated:
        return Grant.objects.none()
    return Grant.objects.filter(user=user, app=app)


def roles_for(user, app, scope=None):
    """Every role this person holds that answers for `scope`.

    An application-wide grant answers for any scope, so it is always
    included. Asking with `scope=None` asks only about application-wide
    grants.
    """
    if not user.is_authenticated:
        return frozenset()
    wanted = {WHOLE_APPLICATION}
    if scope:
        wanted.add(scope)
    return frozenset(
        _grants(user, app).filter(scope__in=wanted).values_list("role", flat=True)
    )


#: What everyone holds on the universal dataset. Read it and build
#: visuals from it; nobody edits reference data through this console.
UNIVERSAL_PRIVILEGES = frozenset({READ, DESIGN})


def has_privilege(user, app, privilege, scope=None):
    """May this person do this here?

    A superuser may. Otherwise some grant answering for `scope` must
    carry the privilege — except on the universal dataset, which everyone
    reads and designs against without a row.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if scope == UNIVERSAL and privilege in UNIVERSAL_PRIVILEGES:
        return True
    return any(role_permits(role, privilege) for role in roles_for(user, app, scope))


def may_import(user, app, scope=None):
    """Bring new rows into a dataset.

    `create`, not `write`: an import does not correct a record that
    exists, it adds records that did not. That is the reviewer/editor
    line, and it is an ordinary privilege check like any other.
    """
    return has_privilege(user, app, CREATE, scope)


def has_privilege_anywhere(user, app, privilege, include_universal=False):
    """Does this person hold this privilege on *anything* in this app?

    The question a page asks at its front door. Datadesk's views are
    corpus-wide with an optional `?dataset=` filter rather than one page
    per dataset, so the guard cannot name a scope — it asks whether there
    is any dataset this person could be here for, and the queryset then
    narrows the rows to exactly those.

    **The universal dataset does not count by default.** Everybody reads
    reference data, so counting it would open every corpus page to
    somebody with no grants at all — who would then be shown an empty
    grid while the landing page told them access had not been granted.
    Reference data is a dataset; it is not a corpus. A page that genuinely
    serves it passes `include_universal=True` and says so.

    Kept separate from `has_privilege(..., scope=None)`, which asks the
    narrower question of whether the person holds the privilege over the
    whole application. Holding one dataset answers this and not that.
    """
    scopes = permitted_scopes(user, app, privilege)
    if scopes is ALL_SCOPES:
        return True
    if not include_universal:
        scopes = scopes - {UNIVERSAL}
    return bool(scopes)


def may_import_anywhere(user, app):
    """Imports, asked without naming a dataset. See `may_import`."""
    return has_privilege_anywhere(user, app, CREATE)


def is_application_admin(user, app):
    """Application-wide administration.

    Deliberately not "holds admin somewhere": the check constraint on
    `Grant` refuses an admin row that names a dataset, so an admin grant
    is application-wide by construction and this reads it back.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _grants(user, app).filter(role=ADMIN).exists()


def may_create_dataset(user, app):
    """Start a new dataset.

    The same privilege as an import — both bring something into
    existence — asked without a scope, because the dataset does not exist
    yet. Holding `create` on one dataset answers it: someone who owns one
    may obviously start a second.
    """
    return has_privilege_anywhere(user, app, CREATE)


def permitted_scopes(user, app, privilege):
    """The dataset slugs this person may exercise `privilege` on.

    Returns `ALL_SCOPES` when the answer is not a list — a superuser, or
    an application-wide grant carrying the privilege. Callers filtering a
    queryset should test for that first and skip the filter, rather than
    expanding it into every slug in the corpus.
    """
    if not user.is_authenticated:
        return frozenset()
    if user.is_superuser:
        return ALL_SCOPES

    # Everyone reads and designs against reference data, so it is in the
    # set before any grant is consulted.
    scoped = {UNIVERSAL} if privilege in UNIVERSAL_PRIVILEGES else set()
    for scope, role in _grants(user, app).values_list("scope", "role"):
        if not role_permits(role, privilege):
            continue
        if scope == WHOLE_APPLICATION:
            return ALL_SCOPES
        scoped.add(scope)
    return frozenset(scoped)


def has_any_grant(user, app):
    """Does this person have any standing in this application at all?

    The question the front door asks. A signed-in person with no grant is
    not a viewer with nothing to see — they have not been given access
    yet, and the landing page says so.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return _grants(user, app).exists()
