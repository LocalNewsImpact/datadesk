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

        # All three halves matter. `hd` establishes the Workspace domain;
        # email_verified stops an unverified address from claiming one; the
        # address check ties the two together.
        domain_ok = (extra.get("hd") or "").lower() in allowed
        verified = bool(extra.get("email_verified"))
        email_ok = any(email.endswith(f"@{domain}") for domain in allowed)

        if not (domain_ok and verified and email_ok):
            raise ImmediateHttpResponse(
                HttpResponseForbidden(
                    "This application is restricted to "
                    f"{', '.join(allowed)} accounts."
                )
            )

    def is_open_for_signup(self, request, sociallogin):
        return True


class NoPublicSignupAdapter(DefaultAccountAdapter):
    """Password signup is closed; accounts arrive via Google or a superuser."""

    def is_open_for_signup(self, request):
        return False

    def clean_password(self, password, user=None):
        raise PermissionDenied("Password accounts are not created here.")
