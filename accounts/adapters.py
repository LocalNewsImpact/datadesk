"""Google sign-in restricted to the allowed hosted domains (SCOPE.md §2.1).

The pattern proven on sources.localnewsimpact.org (NewsSourceDirectory
directory/auth.py), generalized from one hosted domain to the
ALLOWED_AUTH_DOMAINS list. The `hd` parameter in SOCIALACCOUNT_PROVIDERS is
a hint to Google's account chooser; it changes which accounts are offered
and does not prevent anyone completing the flow with a personal account.
The claim has to be checked here, or the login screen only looks
restricted.
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.http import HttpResponseForbidden


def _back_to_sign_in(request, message):
    """Refuse, and leave somewhere to go.

    A bare 403 ends the flow on a page with no way out of it, and the way
    people get out of it is to pick a different account on the Google tab
    still open behind them. That resends the same single-use state, which
    allauth has already spent, so the second attempt fails as "Third-Party
    Login Failure" -- a 401 that has nothing to do with the account they
    chose.

    That is not a hypothetical: it is how an admin whose browser defaults
    to a personal Google account was told, twice in thirty seconds, that
    the address he owns the console with could not sign in.

    Landing back on the sign-in page ends this attempt cleanly and starts
    the next one with a fresh state.
    """
    from django.contrib import messages
    from django.shortcuts import redirect

    if request is not None:
        messages.error(request, message)
        return redirect("account_login")
    # No request only in a direct call from a test; the plain refusal is
    # still the honest answer there.
    return HttpResponseForbidden(message)


class DomainRestrictedAdapter(DefaultSocialAccountAdapter):
    """Admit only verified addresses in the configured hosted domains."""

    def pre_social_login(self, request, sociallogin):
        allowed = [d.lower() for d in settings.ALLOWED_AUTH_DOMAINS]
        if not allowed:
            # Development convenience: no restriction configured.
            return

        extra = sociallogin.account.extra_data or {}
        email = (extra.get("email") or "").lower()
        verified = bool(extra.get("email_verified"))

        # Nothing gets in on an address Google has not verified, whichever
        # door it comes through. An unverified address is a claim, and both
        # doors below are decisions about a particular person.
        if not verified:
            raise ImmediateHttpResponse(
                _back_to_sign_in(request, "Google has not verified that address.")
            )

        # The organisation's own door. `hd` establishes the Workspace
        # domain and the address check ties the claim to the account, so a
        # personal address cannot borrow a domain it does not belong to.
        domain_ok = (extra.get("hd") or "").lower() in allowed
        email_ok = any(email.endswith(f"@{domain}") for domain in allowed)
        if domain_ok and email_ok:
            return

        # ...and the invited one. A personal Google account carries no
        # `hd` at all, so there is nothing for the domain check to accept
        # and no consent screen could change that: somebody outside the
        # organisation gets in because an admin named their address, or
        # not at all.
        from accounts.models import Invitation

        if Invitation.for_email(email) is not None:
            return

        # ...and the provisioned one. The admin's add-account screen writes a
        # user and a grant, not an invitation, so an account made that way
        # had no standing here at all: its owner could only ever use the
        # password door, and met a 403 at the one they were pointed to.
        # Creating the account outright is the same decision an invitation
        # records, made in a different screen.
        #
        # `is_active` is the whole of the check that matters: switching an
        # account off is how access is taken away, and it has to close this
        # door too.
        from django.contrib.auth import get_user_model

        provisioned = get_user_model().objects.filter(
            email__iexact=email, is_active=True
        )
        if provisioned.exists():
            return

        raise ImmediateHttpResponse(
            _back_to_sign_in(
                request,
                "That account cannot sign in here. This console is open to "
                f"{', '.join(allowed)} accounts and to people an "
                "administrator has given an account — try another account.",
            )
        )

    def is_open_for_signup(self, request, sociallogin):
        return True

    def save_user(self, request, sociallogin, form=None):
        """Make the grant the invitation promised, on first sign-in.

        Here rather than when the invitation was written, because a grant
        needs a user and a user does not exist until Google has said who
        they are. Somebody invited and never granted would sign in
        successfully to a console holding nothing, which reads as a
        broken login rather than as a role nobody gave them.
        """
        user = super().save_user(request, sociallogin, form=form)
        from django.utils import timezone

        from accounts.models import Grant, Invitation

        invitation = Invitation.for_email(user.email)
        if invitation is None:
            return user

        Grant.objects.get_or_create(
            user=user,
            app=invitation.app,
            scope=invitation.scope,
            defaults={"role": invitation.role, "granted_by": invitation.invited_by},
        )
        if invitation.accepted_at is None:
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=["accepted_at"])

        from audit.models import AuditLogEntry

        AuditLogEntry.objects.create(
            # The person signing in. They are who acted -- the invitation
            # was the earlier decision, and its own entry names whoever
            # made it.
            actor=user,
            action="accounts:invitation_accepted",
            target_table="accounts_invitation",
            target_ids=[invitation.email],
            after={
                "app": invitation.app,
                "scope": invitation.scope,
                "role": invitation.role,
            },
            reason=(
                f"{user.email} signed in for the first time and was granted "
                f"{invitation.role} on {invitation.scope}"
            ),
        )
        return user


class NoPublicSignupAdapter(DefaultAccountAdapter):
    """Nobody registers themselves; an admin creates every account.

    Password sign-in is allowed. Somebody without a Google account -- a
    colleague at an institution that does not use it, a contractor -- is
    still one person in the same `User` table holding the same grants;
    only the door differs. Refusing passwords outright meant they could
    not have an account at all.

    `is_open_for_signup` stays False, which is the part that matters: the
    sign-up form is closed, so an address nobody invited cannot make
    itself an account.
    """

    def is_open_for_signup(self, request):
        return False
