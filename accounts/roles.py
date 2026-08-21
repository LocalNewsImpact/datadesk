"""The three-role model from SCOPE.md §2.1, as Django group names.

- viewer: dashboards, data browsing, published visuals
- editor: review/cleanup actions, imports, visual authoring
- admin: dataset management, user administration, destructive actions

Groups are created by the accounts data migration. Role assignment for new
sign-ins is a human action in the Django admin; SCOPE.md does not define an
automatic default.
"""

VIEWER = "viewer"
EDITOR = "editor"
ADMIN = "admin"

ROLES = (VIEWER, EDITOR, ADMIN)

# Highest role wins when a user belongs to several groups.
_PRECEDENCE = (ADMIN, EDITOR, VIEWER)


def role_for_user(user):
    """Return the user's role name, or None when no role is assigned.

    Superusers report as admin regardless of group membership.
    """
    if user.is_superuser:
        return ADMIN
    names = set(user.groups.values_list("name", flat=True))
    for role in _PRECEDENCE:
        if role in names:
            return role
    return None
