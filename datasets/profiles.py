"""Enrichment-profile validation (SCOPE.md §2.5), mirroring the
crawler's src/enrichment/profiles.py — the schema authority. The profile
lives at datasets.metadata -> 'enrichment_profile'; a typo here fails
the crawler's run at startup, so the editor refuses to save one.
"""

import datetime

# Mirrors the crawler's PRODUCTION_PRESETS and EXCLUDABLE_SCOPES.
PRODUCTION_PRESETS = ("subject", "topic", "format", "temporal_orientation", "user_need")
EXCLUDABLE_SCOPES = (
    "international",
    "national",
    "statewide",
    "regional",
    "other",
    "elsewhere_to_local",
    "local_to_elsewhere",
)
_KNOWN_KEYS = {
    "version",
    "export_exclude_scopes",
    "steady_state_since",
    "content_gate",
    "scope",
    "places",
    "geocode",
    "people",
    "organizations",
    "metadata_presets",
}
_BOOL_KEYS = ("content_gate", "scope", "places", "geocode", "people", "organizations")


class ProfileError(ValueError):
    """Invalid profile; the message is user-facing."""


def validate_profile(raw):
    """Validate a parsed profile object; returns it normalized."""
    if not isinstance(raw, dict):
        raise ProfileError("The profile must be a JSON object.")
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ProfileError(f"Unknown profile keys: {', '.join(sorted(unknown))}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProfileError("version must be an integer >= 1.")
    for key in _BOOL_KEYS:
        if key in raw and not isinstance(raw[key], bool):
            raise ProfileError(f"{key} must be true or false.")
    presets = raw.get("metadata_presets", [])
    if not isinstance(presets, list) or any(
        p not in PRODUCTION_PRESETS for p in presets
    ):
        raise ProfileError(
            f"metadata_presets entries must be among: {', '.join(PRODUCTION_PRESETS)}"
        )
    scopes = raw.get("export_exclude_scopes", [])
    if not isinstance(scopes, list) or any(s not in EXCLUDABLE_SCOPES for s in scopes):
        raise ProfileError(
            f"export_exclude_scopes entries must be among: "
            f"{', '.join(EXCLUDABLE_SCOPES)}"
        )
    since = raw.get("steady_state_since")
    if since is not None:
        try:
            datetime.date.fromisoformat(since)
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                "steady_state_since must be an ISO date (YYYY-MM-DD)."
            ) from exc
    return raw


def requires_version_bump(old, new):
    """The reprocessing contract: content changes require a version bump
    (the crawler reprocesses where profile_version < version)."""
    if not old:
        return False
    old_content = {k: v for k, v in old.items() if k != "version"}
    new_content = {k: v for k, v in new.items() if k != "version"}
    return old_content != new_content and new.get("version", 0) <= old.get("version", 0)
