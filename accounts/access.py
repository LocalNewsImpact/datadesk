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

from accounts.models import WHOLE_APPLICATION, Grant
from accounts.privileges import (
    role_may_create_dataset,
    role_may_import,
    role_permits,
)

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


def has_privilege(user, app, privilege, scope=None):
    """May this person do this here?

    A superuser may. Otherwise some grant answering for `scope` must
    carry the privilege.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return any(role_permits(role, privilege) for role in roles_for(user, app, scope))


def may_import(user, app, scope=None):
    """Imports and bulk operations.

    A role test rather than a privilege test, deliberately: a reviewer and
    an editor both hold `write`, and what separates them is how many
    records one action changes. See `accounts.privileges`.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return any(role_may_import(role) for role in roles_for(user, app, scope))


def may_create_dataset(user, app):
    """Start a new dataset, as opposed to acting within one.

    Asked without a scope, because there is no scope yet. Any editor or
    admin grant answers it — an editor who owns one dataset may start
    another, and the roadmap's alternative (only an application-wide
    grant confers it) would mean someone could own a dataset and not be
    able to make a second.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return any(
        role_may_create_dataset(role)
        for role in _grants(user, app).values_list("role", flat=True)
    )


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

    scoped = set()
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
