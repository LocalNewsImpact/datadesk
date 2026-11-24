"""GEO-2: the geography guide with places outside the state.

A revised guide is a new version (GEO-1 is what earlier answers were
given under). Recording a practice review admits its coders to a guide
for the same question frozen since the practice opened, so a pilot
under the revised guide gives them stories.
"""

import importlib

import pytest
from django.apps import apps
from django.contrib.auth.models import User

from review.classification import (
    CodebookVersion,
    CoderStanding,
    Round,
    hold_review,
    open_round,
    standings,
)
from review.geography_icr import CLASSES, GEOGRAPHY, GUIDE, geography_guide

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

geo2 = importlib.import_module("review.migrations.0032_geography_guide_v2")


def test_the_migration_carries_the_guide_and_answers_in_the_code():
    assert geo2.GUIDE == GUIDE
    assert {code: [shown, about] for code, shown, about in CLASSES} == geo2.CLASSES
    assert "Outside the state" in GUIDE and "Quincy, Illinois" in GUIDE


def test_geo2_is_made_beside_geo1_and_becomes_the_guide(db):
    first = geography_guide()
    assert first.name == "GEO-1"
    geo2.forward(apps, None)
    geo2.forward(apps, None)  # once only
    assert CodebookVersion.objects.filter(task=GEOGRAPHY).count() == 2
    assert geography_guide().name == "GEO-2"
    assert CodebookVersion.objects.get(name="GEO-1").instructions == first.instructions


def test_a_fresh_install_gets_no_geo2(db):
    geo2.forward(apps, None)
    assert not CodebookVersion.objects.filter(name="GEO-2").exists()


def test_the_practice_review_admits_to_the_revised_guide(db, monkeypatch):
    from review import classification

    practice = open_round("Where", Round.PRACTICE, task=GEOGRAPHY)
    coder = User.objects.create_user("coder", email="coder@missouri.edu")
    monkeypatch.setattr(
        classification,
        "round_report",
        lambda _round: {"coders": [{"user": coder, "finished": True, "standing": ""}]},
    )
    geo2.forward(apps, None)
    revised = CodebookVersion.objects.get(name="GEO-2")
    admitted = hold_review(practice, by=None)
    assert admitted == [coder]
    assert standings(practice.codebook_version)[coder.id] == CoderStanding.ADMITTED
    assert standings(revised)[coder.id] == CoderStanding.ADMITTED
