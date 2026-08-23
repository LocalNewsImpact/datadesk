"""Matching an outside directory to our publishers.

MPA and the corpus share no identifier, so every pairing is an argument
from the website and the name. The risk this file guards is not a
missed match — that only means a question goes unasked — but a wrong
one, which proposes another paper's county as though it were this
paper's.
"""

import json
from types import SimpleNamespace

from review import mopress


def _source(id, host, name="", city="", county="", owner=""):
    return SimpleNamespace(
        id=id,
        host=host,
        host_norm=host,
        canonical_name=name,
        city=city,
        county=county,
        owner=owner,
    )


def _entry(name, website="", city="", county="", owner="", contact_type=1):
    return {
        "contact_id": abs(hash(name)) % 10000,
        "contact_type": contact_type,
        "name": name,
        "website": website,
        "city": city,
        "county": county,
        "owner": owner,
    }


# --- reading the directory --------------------------------------------------


def test_only_publications_are_compared(tmp_path):
    """The directory lists people and associate members too. A person is
    not a publisher record and must never be matched to one."""
    path = tmp_path / "mopress-2026-01-01.json"
    path.write_text(
        json.dumps(
            {
                "source": "MPA",
                "fetched": "2026-01-01",
                "records": [
                    _entry("Example Herald", contact_type=1),
                    _entry("Jane Doe", contact_type=2),
                    _entry("Big Coloring Books", contact_type=3),
                ],
            }
        )
    )
    _, records = mopress.load(path)
    assert [r["name"] for r in records] == ["Example Herald"]


# --- the normalisations -----------------------------------------------------


def test_a_website_reduces_to_its_host():
    assert mopress.host_of("http://www.MyLeaderPaper.com/") == "myleaderpaper.com"
    assert mopress.host_of("https://example.org/news?x=1") == "example.org"
    assert mopress.host_of("") == ""


def test_the_town_qualifier_and_the_article_are_not_the_name():
    assert (
        mopress.fold_name("Atchison County Mail | Rockport") == "atchison county mail"
    )
    assert mopress.fold_name("Aurora Advertiser, The") == "aurora advertiser"
    assert mopress.fold_name("The Kansas City Star") == "kansas city star"


def test_the_county_suffix_is_dropped_to_match_the_corpus():
    assert mopress.fold_county("Boone County") == "Boone"
    assert mopress.fold_county("St. Louis City") == "St. Louis City"
    assert mopress.fold_county("") == ""


def test_a_kind_of_ownership_is_not_a_company_name():
    """75 of the directory's entries say "Independently Owned Newspaper".
    That is true and it is not a company; writing it into `owner` beside
    "Gannett" would make the field mean two things."""
    assert mopress.usable_owner("Gannett") == "Gannett"
    assert mopress.usable_owner("Independently Owned Newspaper") == ""
    assert mopress.usable_owner("Ownership information not listed") == ""
    assert mopress.usable_owner("Cooperative or Chamber Operated") == ""


# --- matching ---------------------------------------------------------------


def test_a_shared_website_is_the_strongest_pairing():
    sources = [_source("s1", "myleaderpaper.com", "Arnold-Imperial Leader")]
    entries = [_entry("Arnold-Imperial Leader", "http://www.myleaderpaper.com")]
    matched, unmatched, ambiguous, _ = mopress.match(entries, sources)
    assert len(matched) == 1
    assert matched[0][2] == "website"
    assert not unmatched and not ambiguous


def test_the_name_pairs_what_the_website_cannot():
    sources = [_source("s1", "example.com", "Aurora Advertiser")]
    entries = [_entry("Aurora Advertiser, The", website="")]
    matched, _, _, _ = mopress.match(entries, sources)
    assert len(matched) == 1
    assert matched[0][2] == "name"


def test_two_records_on_one_host_are_not_matched():
    """A host pointing at two publisher records cannot identify either.
    Guessing here is how one paper is given another's county."""
    sources = [
        _source("s1", "shared.com", "Morning Paper"),
        _source("s2", "shared.com", "Evening Paper"),
    ]
    entries = [_entry("Morning Paper", "https://shared.com")]
    matched, unmatched, ambiguous, _ = mopress.match(entries, sources)
    assert matched == [] or matched[0][2] == "name"
    assert not any(m[2] == "website" for m in matched)


def test_two_directory_entries_on_one_host_are_not_matched():
    sources = [_source("s1", "group.com", "Only Record")]
    entries = [
        _entry("Paper One", "https://group.com"),
        _entry("Paper Two", "https://group.com"),
    ]
    matched, _, ambiguous, _ = mopress.match(entries, sources)
    assert not any(m[2] == "website" for m in matched)


def test_one_publisher_record_is_claimed_once():
    sources = [_source("s1", "a.com", "Shared Name")]
    entries = [
        _entry("Shared Name", "https://a.com"),
        _entry("Shared Name", website=""),
    ]
    matched, unmatched, ambiguous, used = mopress.match(entries, sources)
    assert len(matched) == 1
    assert len(used) == 1


def test_an_entry_with_no_counterpart_is_reported_not_invented():
    sources = [_source("s1", "a.com", "Ours")]
    entries = [_entry("Not In The Corpus", "https://elsewhere.com", city="Nevada")]
    matched, unmatched, _, _ = mopress.match(entries, sources)
    assert matched == []
    assert [r["name"] for r in unmatched] == ["Not In The Corpus"]


# --- what becomes evidence --------------------------------------------------


def test_evidence_carries_only_what_the_directory_said():
    """A blank cell means the directory was silent, never "clear this"."""
    sources = [_source("s1", "a.com", "Ours")]
    entries = [
        _entry(
            "Ours",
            "https://a.com",
            city="Columbia",
            county="Boone County",
            owner="Independently Owned Newspaper",
        )
    ]
    matched, _, _, _ = mopress.match(entries, sources)
    row = mopress.evidence_rows(matched)[0]
    assert row["host_norm"] == "a.com"
    assert row["city"] == "Columbia"
    assert row["county"] == "Boone"
    assert row["owner"] == ""  # a category, not a company
    assert row["canonical_name"] == ""
    assert row["_basis"] == "website"


def test_evidence_is_keyed_to_our_host_not_the_directorys():
    """The directory's URL found the record; the corpus's own host is
    what the evidence loader joins on."""
    sources = [_source("s1", "ours.example", "Ours")]
    entries = [_entry("Ours", website="")]
    matched, _, _, _ = mopress.match(entries, sources)
    assert mopress.evidence_rows(matched)[0]["host_norm"] == "ours.example"
