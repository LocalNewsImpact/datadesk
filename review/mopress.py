"""Matching the Missouri Press directory to our publisher records.

MPA has no identifier in common with the corpus, so a match has to be
argued from what both sides carry: the website, then the publication
name and town. Every match is therefore a claim, and the code says how
each one was reached (`basis`) so a reviewer can weigh a proposal by
how it was arrived at rather than trusting it flat.

Nothing here writes. It produces evidence for `scan_sources`, which
raises questions, which a person answers (REVIEW.md).
"""

import json
import re
from pathlib import Path

# MPA publishes ownership as a company name for some entries and as a
# category for others. A category is true and useful, and it is not a
# company: writing "Independently Owned Newspaper" into `owner` beside
# "Gannett" would make the field mean two things and break every count
# grouped by it. These are reported, never proposed.
OWNER_CATEGORIES = {
    "independently owned newspaper",
    "ownership information not listed",
    "cooperative or chamber operated",
    "not listed",
    "n/a",
}

PUBLICATION = 1  # contact_type for a newspaper, as opposed to a person

_PUNCT = re.compile(r"[^a-z0-9]+")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_THE = re.compile(r",\s*the$")


def load(path):
    document = json.loads(Path(path).read_text())
    return document, [
        record
        for record in document["records"]
        if record.get("contact_type") == PUBLICATION
    ]


def host_of(url):
    """The bare host, the way host_norm is written."""
    if not url:
        return ""
    url = url.strip().lower()
    url = re.sub(r"^[a-z]+://", "", url)
    url = url.split("/")[0].split("?")[0].split("#")[0]
    url = url.split("@")[-1].split(":")[0]
    return url[4:] if url.startswith("www.") else url


def fold_name(name):
    """A publication name reduced to what two spellings of it share.

    MPA qualifies names with the town after a pipe — "Atchison County
    Mail | Rockport" — and moves the article to the end. Neither is a
    difference in the paper.
    """
    if not name:
        return ""
    name = name.strip().lower()
    name = name.split("|")[0].strip()
    name = _TRAILING_THE.sub("", name)
    name = _LEADING_THE.sub("", name)
    return _PUNCT.sub(" ", name).strip()


def fold_county(county):
    """MPA writes "Boone County"; the corpus writes "Boone"."""
    county = (county or "").strip()
    county = re.sub(r"\s+County$", "", county, flags=re.I)
    return county.strip()


def usable_owner(owner):
    """The ownership value, when it names a company rather than a kind."""
    owner = (owner or "").strip()
    if not owner or owner.lower() in OWNER_CATEGORIES:
        return ""
    return owner


def match(records, sources):
    """Pair directory entries with publisher records.

    Three passes, most defensible first, and a host or name that points
    at more than one record on either side is not a match at all — an
    ambiguous pairing is how the wrong paper gets somebody else's
    county.
    """
    by_host, by_name = {}, {}
    host_seen, name_seen = {}, {}
    for source in sources:
        host = host_of(source.host_norm or source.host)
        if host:
            host_seen[host] = host_seen.get(host, 0) + 1
            by_host.setdefault(host, source)
        folded = fold_name(source.canonical_name)
        if folded:
            name_seen[folded] = name_seen.get(folded, 0) + 1
            by_name.setdefault(folded, source)

    entry_hosts = {}
    entry_names = {}
    for record in records:
        host = host_of(record.get("website"))
        if host:
            entry_hosts[host] = entry_hosts.get(host, 0) + 1
        folded = fold_name(record.get("name"))
        if folded:
            entry_names[folded] = entry_names.get(folded, 0) + 1

    matched, unmatched, ambiguous = [], [], []
    used = set()
    for record in records:
        host = host_of(record.get("website"))
        folded = fold_name(record.get("name"))

        if host and host_seen.get(host) == 1 and entry_hosts.get(host) == 1:
            source = by_host[host]
            if source.id not in used:
                used.add(source.id)
                matched.append((record, source, "website"))
                continue

        if folded and name_seen.get(folded) == 1 and entry_names.get(folded) == 1:
            source = by_name[folded]
            if source.id not in used:
                used.add(source.id)
                matched.append((record, source, "name"))
                continue

        if (host and host_seen.get(host, 0) > 1) or (
            folded and name_seen.get(folded, 0) > 1
        ):
            ambiguous.append(record)
        else:
            unmatched.append(record)

    return matched, unmatched, ambiguous, used


def evidence_rows(matched):
    """One row per matched publisher, in the shape --evidence reads.

    Only fields the directory actually carries are filled; a blank cell
    is "the directory did not say", never "make it empty".
    """
    rows = []
    for record, source, basis in matched:
        row = {
            "host_norm": host_of(source.host_norm or source.host),
            "canonical_name": "",
            "city": (record.get("city") or "").strip(),
            "county": fold_county(record.get("county")),
            "owner": usable_owner(record.get("owner")),
            "type": "",
            "_basis": basis,
            "_mpa_name": record.get("name", ""),
            "_contact_id": record.get("contact_id"),
        }
        rows.append(row)
    return rows
