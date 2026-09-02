"""Which rejected articles are worth a person's time.

`not_article` is written in two places and they mean different things.
Extraction judges a body "furniture, not prose", drops `text` and leaves
`content` as captured. The enrichment content gate runs on articles that
already reached `labeled`. The discriminator is the enrichment row: over
the corpus it separates 1,051 extraction rows from 207 enrichment rows
with no overlap on any column.

At 175 extraction rejections per active day against ~815 articles, a
queue holding all of them is a backlog. These scores pick the ones most
likely to be wrong. Measured against production they surface 22 of 207
enrichment rows and 63 of 1,051 extraction rows -- about 11 a day.
"""

import pytest

from review.queue import (
    DOUBT_THRESHOLD,
    doubt,
    looks_rot47,
    prose_density,
)


class _Article:
    def __init__(self, text="", content="", author="", confidence=0.0):
        self.text = text
        self.content = content
        self.author = author
        self.primary_label_confidence = confidence


class _Enrichment:
    def __init__(self, reason=None):
        self.content_gate_reason = reason


PROSE = "A real sentence here. And then another one. And a third. "
FURNITURE = "Home About Contact Sports Weather Obituaries Classifieds "


# --- the signals ------------------------------------------------------------


def test_prose_density_separates_writing_from_furniture():
    """Length alone ranks the wrong rows first: the corpus band that reads
    least like prose averages 11,404 characters, because a 108KB table of
    box scores is long and a real 1,500-character story is not."""
    assert prose_density(PROSE * 40) > 4
    assert prose_density(FURNITURE * 40) < 1


def test_prose_density_of_nothing_is_zero_not_an_error():
    assert prose_density("") == 0.0
    assert prose_density(None) == 0.0


@pytest.mark.parametrize("marker", ["k^Am", "kE23=6", "lQA5C2?<Qm"])
def test_rot47_ciphertext_is_recognised(marker):
    """`kE23=6 4=2DDlQ` is `<table class="p`. TownNews serves paywalled
    bodies ROT47-encoded; undecoded they reach extraction as ciphertext
    and read as furniture."""
    assert looks_rot47(f"some text {marker} more text")


def test_plain_prose_is_not_mistaken_for_ciphertext():
    assert not looks_rot47(PROSE * 10)
    assert not looks_rot47("")


# --- the enrichment gate ----------------------------------------------------


def test_a_heuristic_kill_on_a_long_bylined_story_surfaces():
    """The strongest single signal. `boilerplate_score >= HEURISTIC_REJECT`
    returns with no reason recorded; those 12 rows average 5,853
    characters and 11 of 12 are bylined -- one is an 18,044-character
    bylined feature."""
    article = _Article(text="x" * 6000, author="Jo Reporter", confidence=0.76)
    assert doubt(article, _Enrichment(None)) >= DOUBT_THRESHOLD


def test_a_reasoned_rejection_of_a_short_body_does_not_surface():
    article = _Article(text="x" * 300)
    scored = doubt(article, _Enrichment("Only copyright boilerplate, no story."))
    assert scored < DOUBT_THRESHOLD


def test_a_reason_that_is_only_whitespace_counts_as_no_reason():
    article = _Article(text="x" * 6000, author="Jo", confidence=0.76)
    assert doubt(article, _Enrichment("   ")) >= DOUBT_THRESHOLD


# --- extraction -------------------------------------------------------------


def test_rot47_always_surfaces():
    """Never a correct rejection: the body was never decoded."""
    article = _Article(content="kE23=6 4=2DDlQ" + "x" * 100000, author="StatBot")
    assert doubt(article) >= DOUBT_THRESHOLD


def test_rot47_surfaces_even_without_a_byline():
    assert doubt(_Article(content="k^Am" + "x" * 5000)) >= DOUBT_THRESHOLD


def test_a_prose_body_with_a_byline_surfaces():
    assert doubt(_Article(content=PROSE * 40, author="Jo")) >= DOUBT_THRESHOLD


def test_navigation_furniture_does_not_surface():
    assert doubt(_Article(content=FURNITURE * 80)) < DOUBT_THRESHOLD


def test_a_row_with_nothing_left_on_it_does_not_surface():
    """788 of 1,051 went down the paywall branch, which empties both
    fields. There is nothing on the row to judge, so asking for a verdict
    would be asking without showing the evidence."""
    assert doubt(_Article()) == 0


def test_length_alone_does_not_carry_a_row_over_the_threshold():
    """The defect the density signal exists to prevent."""
    assert doubt(_Article(content=FURNITURE * 2000)) < DOUBT_THRESHOLD
