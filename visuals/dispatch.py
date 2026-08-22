"""Tell the publish workflow that a visual was published (SCOPE.md §2.7).

Curation happens in the database and leaves no git event behind, so the
console sends one itself: a GitHub `repository_dispatch` of type
`publish-visuals`, carrying the slug. `.github/workflows/publish.yml`
listens for it.

Unconfigured, this does nothing and says so in the log. Publishing must
not fail because a token is missing — the pin is already set in the
database by then, and the workflow's daily run is the backstop.
"""

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

EVENT_TYPE = "publish-visuals"
_API = "https://api.github.com/repos/{repo}/dispatches"
_TIMEOUT_SECONDS = 5


def _settings():
    """(repo, token) from the environment, or (None, None)."""
    repo = os.environ.get("GITHUB_DISPATCH_REPO", "").strip()
    token = os.environ.get("GITHUB_DISPATCH_TOKEN", "").strip()
    if not repo or not token:
        return None, None
    return repo, token


def notify_published(slug):
    """Fire the dispatch for one visual. True when it was sent.

    Never raises: a publish that reached the database has succeeded, and
    an unreachable GitHub must not turn that into an error page.
    """
    repo, token = _settings()
    if repo is None:
        logger.info(
            "publish dispatch not configured (GITHUB_DISPATCH_REPO / "
            "GITHUB_DISPATCH_TOKEN); the workflow's daily run will pick "
            "up %s",
            slug,
        )
        return False

    request = urllib.request.Request(
        _API.format(repo=repo),
        data=json.dumps(
            {"event_type": EVENT_TYPE, "client_payload": {"slug": slug}}
        ).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            # GitHub answers 204 with no body.
            if response.status not in (200, 201, 204):
                logger.warning(
                    "publish dispatch for %s returned %s", slug, response.status
                )
                return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("publish dispatch for %s failed: %s", slug, exc)
        return False
    logger.info("dispatched %s for %s", EVENT_TYPE, slug)
    return True
