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
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden


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
                HttpResponseForbidden("Google has not verified that address.")
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

        raise ImmediateHttpResponse(
            HttpResponseForbidden(
                f"This application is restricted to {', '.join(allowed)} "
                "accounts and people who have been invited."
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
    """Password signup is closed; accounts arrive via Google or a superuser."""

    def is_open_for_signup(self, request):
        return False

    def clean_password(self, password, user=None):
        raise PermissionDenied("Password accounts are not created here.")
