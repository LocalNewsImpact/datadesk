"""Template context: the signed-in user's role, for navigation."""

from accounts.roles import role_for_user


def role(request):
    if not request.user.is_authenticated:
        return {"role": None}
    return {"role": role_for_user(request.user)}
