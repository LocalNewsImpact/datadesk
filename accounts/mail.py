"""Sending mail as the consortium, through the Gmail API.

There is no SMTP here and there does not need to be. The crawler's weekly
health check already sends as `chair@localnewsimpact.org` using a service
account with domain-wide delegation, and this is the same credential
doing the same thing for Django's mail.

The REST call is one POST, so this does not pull in
`google-api-python-client` for it: that package is ~50MB of generated
clients to reach one endpoint that `google-auth` can already sign for.

Unconfigured, it says so and refuses rather than pretending to send. A
password-set link that silently went nowhere would leave somebody locked
out of an account an admin believes they can use.
"""

import base64
import json

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

#: What a delegated credential is allowed to do here. Sending, and
#: nothing else: this account can reach every mailbox in the domain, so
#: the scope is the wall.
SCOPES = ("https://www.googleapis.com/auth/gmail.send",)

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailAPIBackend(BaseEmailBackend):
    """Django's email backend, over the Gmail API."""

    def send_messages(self, messages):
        if not messages:
            return 0
        credentials = _delegated()
        if credentials is None:
            if self.fail_silently:
                return 0
            raise RuntimeError(
                "Gmail is not configured: set GMAIL_CREDENTIALS_JSON and "
                "GMAIL_DELEGATED_USER."
            )

        import requests
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
        sent = 0
        for message in messages:
            body = {
                "raw": base64.urlsafe_b64encode(message.message().as_bytes()).decode()
            }
            answer = requests.post(
                SEND_URL,
                json=body,
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=30,
            )
            if answer.status_code >= 400:
                if not self.fail_silently:
                    raise RuntimeError(
                        f"Gmail refused the message: {answer.status_code} "
                        f"{answer.text[:200]}"
                    )
                continue
            sent += 1
        return sent


def _delegated():
    """The service account, acting as the address it sends from, or None.

    Domain-wide delegation is what lets a service account send as a
    person. Without `with_subject` the credential is the robot's own
    mailbox, which does not exist, and Gmail refuses it.
    """
    raw = getattr(settings, "GMAIL_CREDENTIALS_JSON", "")
    sender = getattr(settings, "GMAIL_DELEGATED_USER", "")
    if not (raw and sender):
        return None
    from google.oauth2 import service_account

    info = json.loads(base64.b64decode(raw).decode())
    return service_account.Credentials.from_service_account_info(
        info, scopes=list(SCOPES)
    ).with_subject(sender)


def configured():
    """Whether a link can actually be sent, for a screen that has to say
    so before it promises one."""
    return bool(
        getattr(settings, "GMAIL_CREDENTIALS_JSON", "")
        and getattr(settings, "GMAIL_DELEGATED_USER", "")
    )
