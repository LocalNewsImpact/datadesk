"""Template context: what the signed-in person may do, for navigation.

Was the signed-in user's single role. Roles are per dataset now (ROADMAP
item 1), so a person holds several and no one of them describes what a
template should show.

These are **affordances, not access control**. `may_write` says "show the
edit link" and is true if the person may write on *any* dataset; the view
behind the link checks the dataset actually in play. Hiding a link is not
a guard, which is why every view carries its own — see
`accounts.decorators`.
"""

from accounts.access import (
    has_any_grant,
    has_privilege_anywhere,
    is_application_admin,
)
from accounts.privileges import DESIGN, WRITE

_NONE = {
    "has_access": False,
    "is_admin": False,
    "may_write": False,
    "may_design": False,
}


def access(request):
    from accounts.decorators import APP

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return dict(_NONE)
    return {
        "has_access": has_any_grant(user, APP),
        "is_admin": is_application_admin(user, APP),
        "may_write": has_privilege_anywhere(user, APP, WRITE),
        "may_design": has_privilege_anywhere(user, APP, DESIGN),
    }
