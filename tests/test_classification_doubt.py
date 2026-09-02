"""Doubt scored from what the pipeline recorded, not from the text after it.

The content type detector writes confidence_score, reason and evidence to
content_type_detection_telemetry for every row it decides. That is its own
stated uncertainty and it beats anything inferred here afterwards. Coverage
since telemetry began: weather 100%, obituary 97.7%, opinion 95.6%.

Validated against 47,419 production rows: 1,253 surface at the threshold,
about 6 per active day.
"""

import pytest

from review.queue import (
    classification_doubt,
    evidence_is_corroborated,
    same_site_alias,
)

THRESHOLD = 5


# --- obituary: a body phrase with nothing agreeing --------------------------


def test_an_obituary_called_on_one_body_phrase_surfaces():
    """1,098 rows, average confidence 0.17, never above 0.25. The evidence
    is usually a single phrase -- {"content": ["passed away"]}, 542 of them
    -- which caught a feature about Jim Morrison's grave and BackStoppers,
    a charity for families of fallen first responders."""
    assert (
        classification_doubt("obituary", 0.17, {"content": ["passed away"]})
        >= THRESHOLD
    )


def test_an_obituary_the_url_agrees_with_does_not_surface():
    """Where the path or title corroborates, average confidence rises to
    0.38 and the calls are right."""
    evidence = {"url": ["obituaries"], "title_patterns": ["1931-2025"]}
    assert classification_doubt("obituary", 0.38, evidence) < THRESHOLD


@pytest.mark.parametrize(
    "evidence,corroborated",
    [
        ({"content": ["passed away"]}, False),
        ({"url": ["obituaries"]}, True),
        ({"title": ["In Memoriam"]}, True),
        ({"title_patterns": ["1931-2025"]}, True),
        ({}, False),
        (None, False),
        ("not a dict", False),
    ],
)
def test_what_counts_as_corroboration(evidence, corroborated):
    assert evidence_is_corroborated(evidence) is corroborated


# --- weather and opinion: always corroborated -------------------------------


@pytest.mark.parametrize("status", ["weather", "opinion"])
def test_weather_and_opinion_do_not_surface_on_confidence_alone(status):
    """Neither has a single content-only case in the corpus, so a middling
    score is not on its own a reason to doubt them."""
    assert classification_doubt(status, 0.48, {"url": [status]}) < THRESHOLD


# --- wire: judged on the relationship, not the method -----------------------


def test_an_article_whose_canonical_is_its_own_host_surfaces():
    """The bug in its purest form. All 155 suspects in production are
    ky3.com articles canonical-ing to ky3.com, called cross-domain
    syndication and removed from CIN counting."""
    assert (
        classification_doubt(
            "wire",
            wire_method="canonical_cross_domain",
            publisher_host="www.ky3.com",
            canonical_host="www.ky3.com",
        )
        >= THRESHOLD
    )


def test_real_syndication_does_not_surface():
    """Surfacing the method itself would be 113 a day, nearly all correct.
    Only 155 of 15,220 point at the publisher's own host."""
    assert (
        classification_doubt(
            "wire",
            wire_method="canonical_cross_domain",
            publisher_host="example.com",
            canonical_host="www.npr.org",
        )
        < THRESHOLD
    )


@pytest.mark.parametrize(
    "left,right,alias",
    [
        ("www.ky3.com", "ky3.com", True),
        ("nwaonline.com", "www.nwaonline.com", True),
        ("kansascity.com", "kansas.com", False),
        ("emissourian.com", "missourian.com", False),
        ("", "ky3.com", False),
    ],
)
def test_which_hosts_are_the_same_newsroom(left, right, alias):
    """Compared on the first label. Substring matching caught
    kansascity.com against kansas.com, which are different newsrooms."""
    assert same_site_alias(left, right) is alias


# --- absence of evidence -----------------------------------------------------


def test_a_verdict_with_no_telemetry_scores_zero():
    """Absence of evidence is not evidence the call was wrong. Wire
    telemetry covers only 12.8%, so guessing here would flood the queue."""
    assert classification_doubt("obituary") == 0
    assert classification_doubt("wire") == 0
