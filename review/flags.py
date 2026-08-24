"""The flag vocabulary, and the checks that raise it (REVIEW.md).

A flag names a defect in a record — something missing, something that
does not exist, something contradicted. It never names the state of a
proposed edit. Each flag is a definition plus a check, and the set the
filter shows is exactly what its name says.

Adding a flag is deliberate: a name, a definition a reviewer would
recognise, and a check that raises it. This is where new defects are
learned, so this list is expected to grow.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    key: str
    label: str  # what a reviewer sees in the filter
    defect: str  # what is wrong with the record
    field: str  # the field the defect is on
    # (source, context) -> (is_flagged, detail). None where the defect
    # comes from evidence rather than from the record alone.
    check: Callable | None = None


# --- the checks -------------------------------------------------------------
#
# Each returns (flagged, detail, better).
#
#   detail  what makes this record's instance of the defect specific
#   better  the value we believe is correct, where the check knows it —
#           empty where it does not. This is what the queue offers as
#           the proposed change, because a column headed "Proposed
#           change" must hold the value that would improve the record.


def _missing(field):
    def check(source, context):
        return not (getattr(source, field) or "").strip(), "no value recorded", ""

    return check


def _county_unknown(source, context):
    value = (source.county or "").strip()
    if not value:
        return False, "", ""
    from datasets.geo import canonical_county, states_with_county, suggest_counties

    state = context["state_of"](source)
    if canonical_county(state, value)[1]:
        return False, "", ""
    elsewhere = states_with_county(value)
    hints = suggest_counties(state, value)
    better = hints[0] if hints else ""
    if elsewhere:
        return (
            True,
            f"{value} is a county in {', '.join(elsewhere)}, not {state}",
            better,
        )
    return True, f"{value} is not a county in {state}", better


def _looks_like(value, field, source, context):
    """What `value` actually is, if it is not what `field` should hold.

    A value that fails its own field's gazetteer may still be a valid
    something-else, and which it is decides the repair: a misspelt county
    is corrected, an owner sitting in the county column is cleared and the
    owner checked. Saying only "not a county" leaves the reviewer to work
    that out from the string.
    """
    from datasets.geo import canonical_county
    from datasets.places import place_geoid

    state = context["state_of"](source)
    if field != "owner" and value in context.get("owners", ()):
        return "an owner"
    if field != "county" and state and canonical_county(state, value)[1]:
        return "a county"
    if field != "city" and state and place_geoid(state, value):
        return "a city"
    return ""


def _misfiled(field):
    """A value that belongs to a different column.

    Imports arrive with their columns one place out, and the result reads
    as a nonsense county rather than as a mapping error. This names it:
    'Nexstar Media Inc is an owner, not a county' is a repair somebody can
    make, where 'not a county in MO' is a puzzle.
    """

    def check(source, context):
        value = (getattr(source, field, "") or "").strip()
        if not value:
            return False, "", ""
        actually = _looks_like(value, field, source, context)
        if not actually:
            return False, "", ""
        # Nothing is proposed: the value is evidence about another field,
        # and what this one should hold is not knowable from it.
        return True, f"{value} is {actually}, not a {field}", ""

    return check


def _county_multiple(source, context):
    import re

    value = (source.county or "").strip()
    parts = [p for p in re.split(r"\s*(?:,|/|;|&|\band\b)\s*", value, flags=re.I) if p]
    if len(parts) < 2:
        return False, "", ""
    # Which of the counties is meant is the reviewer's call, so nothing
    # is proposed.
    return True, f"names {len(parts)} counties: {', '.join(parts)}", ""


def _city_unknown(source, context):
    value = (source.city or "").strip()
    if not value:
        return False, "", ""
    from datasets.places import validate_city

    state = context["state_of"](source)
    if not state:
        return False, "", ""
    known, hints = validate_city(state, value)
    if known:
        return False, "", ""
    detail = f"{value} is not a place in {state}"
    if hints:
        detail += f"; the gazetteer has {', '.join(hints)}"
    return True, detail, (hints[0] if hints else "")


def _owner_unknown(source, context):
    value = (source.owner or "").strip()
    if not value:
        return False, "", ""
    from datasets.owners import canonical_owner, fold

    canonical, kind = canonical_owner(value, context["owners"])
    if kind == "unknown":
        return True, f"{value} matches no owner the corpus records elsewhere", ""
    if fold(canonical) != fold(value):
        return (
            True,
            f"recorded as {value}; elsewhere the corpus writes {canonical}",
            canonical,
        )
    return False, "", ""


def _state_missing(source, context):
    """A publisher record needs a state whether or not it has a city.

    This used to return early when the city was blank, so a record with no
    city, no county and no state was flagged for two of the three and the
    scan never mentioned the third. Twenty-two sources in the Missouri
    dataset sit in exactly that state and are invisible in every
    geography-shaped view.
    """
    recorded = ((source.meta or {}).get("state") or "").strip()
    if recorded:
        return False, "", ""
    # The dataset's own default is the likeliest answer and is offered as
    # the proposal rather than applied silently: a Missouri dataset can
    # hold an outlet that is not in Missouri, and inheriting the default
    # would write that in without anybody looking.
    suggested = (context.get("default_state") or "").strip()
    detail = "no value recorded"
    if suggested:
        detail = f"no value recorded; this dataset defaults to {suggested}"
    return True, detail, suggested


def _state_unknown(source, context):
    """A state that is recorded but is not the postal code.

    "MO" and "Missouri" are the same place and sort into different groups.
    The stored form is the postal code -- it is what the gazetteer joins on
    and what almost every record already holds. Legibility is the reading
    end's job: `state_name` turns MO into Missouri wherever a person sees
    it, so the column stays uniform without anybody reading an abbreviation.
    """
    from datasets.geo import state_code

    recorded = ((source.meta or {}).get("state") or "").strip()
    if not recorded:
        return False, "", ""
    canonical = state_code(recorded)
    if not canonical:
        return True, f"{recorded!r} is not a state", ""
    if canonical == recorded:
        return False, "", ""
    return True, f"recorded as {recorded!r}", canonical


FLAGS = (
    Flag(
        key="county_missing",
        label="No county recorded",
        defect="The publisher has no county, so it cannot be placed or counted.",
        field="county",
        check=_missing("county"),
    ),
    Flag(
        key="county_unknown",
        label="County does not exist here",
        defect="The county recorded is not a county in the publisher's state.",
        field="county",
        check=_county_unknown,
    ),
    Flag(
        key="county_misfiled",
        label="The county holds something else",
        defect=(
            "The value is a valid owner or city, so the column is one place "
            "out rather than the county being misspelt."
        ),
        field="county",
        check=_misfiled("county"),
    ),
    Flag(
        key="city_misfiled",
        label="The city holds something else",
        defect=(
            "The value is a valid owner or county, so the column is one "
            "place out rather than the city being misspelt."
        ),
        field="city",
        check=_misfiled("city"),
    ),
    Flag(
        key="county_multiple",
        label="County names more than one",
        defect="The county field holds several counties, so it cannot be joined.",
        field="county",
        check=_county_multiple,
    ),
    Flag(
        key="city_missing",
        label="No city recorded",
        defect="The publisher has no city.",
        field="city",
        check=_missing("city"),
    ),
    Flag(
        key="city_unknown",
        label="City does not exist here",
        defect="The city recorded is not a Census place in the publisher's state.",
        field="city",
        check=_city_unknown,
    ),
    Flag(
        key="state_missing",
        label="No state recorded",
        defect=(
            "A publisher record needs a state. Without one the city and "
            "county cannot be checked, and the record is absent from every "
            "view organised by geography."
        ),
        field="meta.state",
        check=_state_missing,
    ),
    Flag(
        key="state_unknown",
        label="The state is not the postal code",
        defect=(
            "'MO' and 'Missouri' are the same place and sort into different "
            "groups. The postal code is the stored form; screens show the "
            "full name."
        ),
        field="meta.state",
        check=_state_unknown,
    ),
    Flag(
        key="owner_missing",
        label="No owner recorded",
        defect="The publisher has no owner, so ownership cannot be reported.",
        field="owner",
        check=_missing("owner"),
    ),
    Flag(
        key="owner_unknown",
        label="Owner spelled differently",
        defect=(
            "The owner is written in a way the corpus does not use elsewhere, "
            "so the same company counts as two."
        ),
        field="owner",
        check=_owner_unknown,
    ),
    Flag(
        key="name_missing",
        label="No publication name",
        defect="The publisher has no name, so it can only be identified by host.",
        field="canonical_name",
        check=_missing("canonical_name"),
    ),
)

BY_KEY = {flag.key: flag for flag in FLAGS}

# Raised by evidence rather than by the record alone: a file that
# disagrees with what is recorded, or that gives two answers for one
# field. Both are ordinary questions — a reviewer can always Keep or
# Fix, whatever the file did.
EVIDENCE_FLAGS = (
    Flag(
        key="value_disputed",
        label="A source file disagrees",
        defect=(
            "A file gives a different value than the record holds. Either the "
            "record is out of date or the file is wrong."
        ),
        field="",
        check=None,
    ),
    Flag(
        key="no_match",
        label="A publisher we do not have",
        defect=(
            "Somebody reports an outlet the corpus has no record of. Nothing "
            "is changed by accepting it -- a record is created."
        ),
        field="",
        check=None,
    ),
    Flag(
        key="reported",
        label="Somebody reported a change",
        defect=(
            "A person who can see this record says a field is out of date, and "
            "said where they got it. Nothing has been written; the record "
            "decides whether to take it."
        ),
        field="",
        check=None,
    ),
    Flag(
        key="evidence_conflict",
        label="A source file offers two values",
        defect=(
            "A file gives more than one value for this field, so none is "
            "proposed. Both are named; the record decides between them."
        ),
        field="",
        check=None,
    ),
)

ALL_FLAGS = FLAGS + EVIDENCE_FLAGS
ALL_BY_KEY = {flag.key: flag for flag in ALL_FLAGS}
