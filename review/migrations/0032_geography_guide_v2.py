"""GEO-2: the geography guide with places outside the state.

Made only where GEO-1 exists (production): a fresh install makes its
first guide from the code (review.geography_icr.geography_guide). The
text is copied here, not imported, so the migration means the same
thing however the code later changes. GEO-1 is left as it was: answers
were given under it.
"""

from django.db import migrations

GUIDE = """\
Say where the story is ABOUT: where its events happen, and whose
community, government or institution it concerns. Not where its effects
reach, and not every place it mentions.

Choose the smallest area that holds the subject. A story set in one
town is about that town, even when it names the county, a highway or
nearby towns.

A county agency acting in a town is about that town: deputies arresting
somebody in Fulton is a Fulton story. A school, library, hospital or fire
district's story about one of its buildings is about the town that
building is in; it is wider only when it is about the whole district (a
district-wide levy, budget or election).

A state event is the venue, not the subject. A team from one town at a
state tournament is a story about that town. A name with the state in it
is still in one place: the University of Missouri and its teams are in
Columbia; the Missouri State Fair is in Sedalia.

A state official acting on named places is about those places. It is
the whole state only when the action applies across it: a law, a rule,
a statewide program.

Several towns, a county, or wider is for two or more communities
sharing the story with no single one at its centre, a county
government's own business, or a story set in a county but in no
particular town. Where the people in a story are from is not a second
place: a crash near Centerville whose driver lives in Ellington is a
Centerville story.

A place outside the state is answered "Outside the state, or not about
a place": Quincy, Illinois; Kansas City, Kansas; a Kansas bill. Do not
pin it. A story set in the state that also names places across the
border is about the place in the state.

A dateline alone does not locate a story. "COLUMBIA, Mo. —" says where
the reporter filed from; read the story for where it happened.

Zoom in until the town fills the map before you drop a pin. The name
under the pin is what will be recorded: if it is not the place you
meant, move the pin.
"""

CLASSES = {
    "P": [
        "One place",
        "A town, a neighbourhood, an address or a site. Type it, or pin it "
        "on the map.",
    ],
    "R": [
        "Several towns, a county, or a wider area",
        "Two or more towns or counties with no single one at the centre, or "
        "a county government's own business. Add the towns or the counties, "
        "or both: type them, or click the map.",
    ],
    "S": [
        "The whole state",
        "State law, the governor, a state agency acting across the state.",
    ],
    "U": [
        "Local, but the text never says where",
        "The story is plainly about one community and never names it.",
    ],
    "N": [
        "Outside the state, or not about a place",
        "A place in another state or country, national or international "
        "news, or a story with no geography: a column about the newsroom, a "
        "recipe.",
    ],
}


def forward(apps, schema_editor):
    CodebookVersion = apps.get_model("review", "CodebookVersion")
    if not CodebookVersion.objects.filter(task="geography", name="GEO-1").exists():
        return
    CodebookVersion.objects.get_or_create(
        name="GEO-2",
        defaults={
            "task": "geography",
            "instructions": GUIDE,
            "guidance": CLASSES,
            "display_names": {},
            "rejections": [],
        },
    )


def backward(apps, schema_editor):
    CodebookVersion = apps.get_model("review", "CodebookVersion")
    CodebookVersion.objects.filter(name="GEO-2", task="geography").delete()


class Migration(migrations.Migration):
    dependencies = [("review", "0031_geography_outside_the_state")]
    operations = [migrations.RunPython(forward, backward)]
