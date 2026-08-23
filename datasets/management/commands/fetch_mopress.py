"""Fetch the Missouri Press Association public directory as a source document.

MPA publishes a directory of its member papers with the fields our
publisher records are thinnest on: county, ownership, circulation and
publication days. It is evidence, not truth — the page says so itself,
disclaiming the accuracy of what third parties gave it — so this
command only writes a dated document. Comparing it to the corpus and
raising questions is `scan_sources --evidence`, and deciding anything
is a person in the review queue.

The document is kept in the repository rather than a scratch directory
because a reviewer disposing of a conflict months from now needs to see
what the directory said on the day it was read, not what it says today.
"""

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand

LIST_URL = "https://mopress.jumbl.app/contactmanager/contact/publicdirectory"
DRAWER_URL = (
    "https://mopress.jumbl.app/contactmanager/Contact/"
    "GetPublicDirectoryAdditionalSectionQuestion"
)
AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
)

# "503 N. 2nd Street, P.O. Box 159,  Festus, MO 63028   , US", and the
# ZIP+4 is written several ways, including "64470 - 0175".
ADDRESS_TAIL = re.compile(
    r"^(?P<street>.*?),?\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+"
    r"(?P<zip>\d{5}(?:\s*-\s*\d{4})?)\s*,?\s*(?P<country>[A-Z]{2})?\s*$"
)

# Lines that are chrome, not content, where an address might otherwise
# be read from.
NOT_AN_ADDRESS = {
    "Map not available.",
    "Address not available.",
    "Contact Address",
    "Main Office",
    "Phone Number",
}


def _clean(value):
    return " ".join((value or "").split())


def _get(url, params=None, tries=3):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": AGENT, "X-Requested-With": "XMLHttpRequest"}
    )
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return ""


def _labelled_pairs(node):
    """The directory renders extras as a label followed by its value.

    Both the list card and the drawer use the pattern, with no shared
    class to key on, so the text runs are paired in order.
    """
    parts = [_clean(t) for t in node.stripped_strings]
    parts = [p for p in parts if p and not p.startswith("$(")]
    pairs = {}
    for index, part in enumerate(parts):
        if part.endswith(":") and index + 1 < len(parts):
            pairs[part.rstrip(":").strip()] = parts[index + 1]
    return pairs, parts


def _split_address(line):
    line = _clean(line)
    match = ADDRESS_TAIL.match(line)
    if not match:
        return {"address": line}
    found = match.groupdict()
    return {
        "address": _clean(found["street"]).rstrip(","),
        "city": _clean(found["city"]),
        "state": found["state"],
        "zip": _clean(found["zip"]).replace(" ", ""),
        "country": found.get("country") or "",
    }


def _address_from(lines):
    """The mailing address, from wherever the card happens to carry it.

    Most cards repeat it as a one-liner under the name, but some put a
    tagline there instead and some have none at all — the card then says
    so, and an empty address is the right answer rather than "Map not
    available." parsed as a street. So the candidates are gathered and
    the first one that reads as an address wins.
    """
    candidates = []
    if len(lines) > 1:
        candidates.append(lines[1])
    if "Contact Address" in lines:
        start = lines.index("Contact Address") + 1
        block = []
        for line in lines[start:]:
            if line in ("Phone Number", "Website", "Social Media Info"):
                break
            if line not in NOT_AN_ADDRESS:
                block.append(line)
        # Street and city are often split across two lines.
        candidates.extend(block)
        for index in range(len(block) - 1):
            candidates.append(f"{block[index]} {block[index + 1]}")

    for candidate in candidates:
        candidate = _clean(candidate)
        if not candidate or candidate in NOT_AN_ADDRESS:
            continue
        if ADDRESS_TAIL.match(candidate):
            return _split_address(candidate)
    return {"address": "", "city": "", "state": "", "zip": "", "country": ""}


def parse_list(html):
    """Every directory entry, with what the card itself carries."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for item in soup.select(".public-directory-item"):
        toggle = item.select_one("a.toggleLink[onclick*=onMoreInfoClicked]")
        if toggle is None:
            continue
        found = re.search(
            r"onMoreInfoClicked\(event,\s*(\d+),\s*(\d+)\)", toggle.get("onclick", "")
        )
        if not found:
            continue
        contact_id, contact_type = int(found.group(1)), int(found.group(2))

        lines = [_clean(t) for t in item.stripped_strings]
        lines = [line for line in lines if line]
        name = lines[0] if lines else ""

        website = ""
        for anchor in item.select("a[href]"):
            href = anchor.get("href", "")
            if href.startswith(("http://", "https://")) and "jumbl.app" not in href:
                website = href
                break
        phone = ""
        tel = item.select_one('a[href^="tel:"]')
        if tel is not None:
            phone = _clean(tel.get_text())

        pairs, parts = _labelled_pairs(item)
        # "Ownership" and "County" are headed rather than colon-labelled.
        for key in ("Ownership", "County"):
            if key in parts:
                position = parts.index(key)
                if position + 1 < len(parts):
                    pairs.setdefault(key, parts[position + 1])

        record = {
            "contact_id": contact_id,
            "contact_type": contact_type,
            "name": name,
            "website": website,
            "phone": phone,
            "owner": pairs.get("Ownership", ""),
            "county": pairs.get("County", ""),
        }
        record.update(_address_from(lines))
        records.append(record)
    return records


def parse_drawer(html):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select("script"):
        script.decompose()
    pairs, _ = _labelled_pairs(soup)
    return {
        "circulation": pairs.get("Circulation", ""),
        "service_area": pairs.get("Newspaper service area", ""),
        "publication_days": next(
            (v for k, v in pairs.items() if k.startswith("Publication days")), ""
        ),
        "ownership_company": pairs.get("Ownership company", ""),
        "extras": pairs,
    }


class Command(BaseCommand):
    help = "Read the Missouri Press directory into a dated source document."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            default="",
            help="Where to write. Defaults to data/sources/mopress-<date>.json.",
        )
        parser.add_argument(
            "--fetched",
            required=True,
            help="The date of this reading, YYYY-MM-DD. Recorded in the document.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.4,
            help="Seconds between drawer requests.",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Stop after N entries (for testing)."
        )

    def handle(self, **options):
        self.stdout.write("reading the directory index")
        records = parse_list(_get(LIST_URL))
        if options["limit"]:
            records = records[: options["limit"]]
        self.stdout.write(f"{len(records)} entries")

        for index, record in enumerate(records, 1):
            drawer = parse_drawer(
                _get(
                    DRAWER_URL,
                    {
                        "contactId": record["contact_id"],
                        "contactTypeId": record["contact_type"],
                    },
                )
            )
            record.update(drawer)
            if index % 25 == 0:
                self.stdout.write(f"  {index}/{len(records)}")
            time.sleep(options["delay"])

        out = Path(options["out"]) if options["out"] else None
        if out is None:
            folder = Path(settings.BASE_DIR) / "data" / "sources"
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / f"mopress-{options['fetched']}.json"

        document = {
            "source": "Missouri Press Association public directory",
            "url": LIST_URL,
            "fetched": options["fetched"],
            "count": len(records),
            "caveat": (
                "MPA states the directory is supplied by third parties and "
                "does not warrant its accuracy. Treat every field as "
                "evidence for a reviewer, never as a value to write "
                "unattended."
            ),
            "records": records,
        }
        out.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
        self.stdout.write(f"wrote {out}")

        # The JSON is what the scan reads; the CSV is what a person opens
        # while deciding whether to believe a proposal.
        flat = out.with_suffix(".csv")
        columns = [
            "contact_id",
            "contact_type",
            "name",
            "website",
            "owner",
            "county",
            "city",
            "state",
            "zip",
            "address",
            "phone",
            "circulation",
            "publication_days",
        ]
        with flat.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow(record)
        self.stdout.write(f"wrote {flat}")
