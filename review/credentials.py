"""Subscriber credentials for a paywalled publisher.

They are not in the database and are not going to be. The crawler reads
them from GCP Secret Manager under the name in `sources.auth_secret_name`,
which is why the sources table carries its comment that credentials are
never stored in it -- a password column would be readable by every role
holding SELECT on that table, including the read-only analytics role this
console connects as, and every CSV anybody exports.

So this writes the secret and the console records only its name.

The secrets live in the crawler's project, because the crawler is what
reads them. Writing one therefore needs `secretmanager.admin` (or the
create/add pair) for this service account over there, which is a grant
somebody makes once -- see infra/README.md.
"""

import json
import os
import re

#: Where the crawler looks. The secrets are read by the extractor, which
#: runs in the crawler's project, so that is where they are kept.
PROJECT = os.environ.get("PUBLISHER_SECRET_PROJECT", "mizzou-news-crawler")

#: The convention already in use for the eight that exist:
#: publisher-auth-spokesman-com for www.spokesman.com.
_PREFIX = "publisher-auth-"


class CredentialError(Exception):
    """Writing the secret did not work; the message is user-facing."""


def secret_name_for(host):
    """The secret's name for a publisher, by the existing convention.

    Derived rather than stored, so the name cannot drift from the host it
    belongs to -- and matched against what the eight existing secrets are
    called: `www.` comes off, dots become dashes.
    """
    host = re.sub(r"^www\.", "", (host or "").strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-")
    if not slug:
        raise CredentialError("That publisher has no host to name a secret after.")
    return f"{_PREFIX}{slug}"


def _client():
    try:
        from google.cloud import secretmanager
    except ImportError as exc:  # pragma: no cover - the library is a dependency
        raise CredentialError(
            "google-cloud-secret-manager is not installed here, so credentials "
            "cannot be stored."
        ) from exc
    return secretmanager.SecretManagerServiceClient()


def store(host, fields, client=None):
    """Write one publisher's credentials, and return the secret's name.

    `fields` is what the login needs: username and password for most, and
    username and billing ZIP for a SimpleCirc publisher. Stored as the
    JSON object the crawler already reads, so nothing there changes.

    A new version rather than a new secret where one exists: the crawler
    asks for `versions/latest`, and replacing a password is a new version
    of the same secret.
    """
    payload = {k: v for k, v in (fields or {}).items() if v}
    if not payload:
        raise CredentialError("Type the credentials to store.")

    name = secret_name_for(host)
    client = client or _client()
    parent = f"projects/{PROJECT}"
    try:
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": name,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except Exception as exc:  # already there is the ordinary case
            if "AlreadyExists" not in type(exc).__name__ and "exists" not in str(exc):
                raise
        client.add_secret_version(
            request={
                "parent": f"{parent}/secrets/{name}",
                "payload": {"data": json.dumps(payload).encode("utf-8")},
            }
        )
    except CredentialError:
        raise
    except Exception as exc:
        # Said, not swallowed. A permission this account does not hold is
        # the likeliest failure and the one somebody can act on.
        raise CredentialError(f"Secret Manager refused it: {exc}") from exc
    return name
