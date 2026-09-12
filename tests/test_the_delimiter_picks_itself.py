"""The infra scripts join environment variables with a delimiter, and
twice now the delimiter has been a bet that lost.

gcloud needs one because these values contain commas of their own: a
comma-joined list read as comma-separated becomes one variable whose
value is every other variable, and fifteen of them once ended up inside
CLOUD_SQL_CONNECTION_NAME.

"@" was the answer to that, and it lasted until a variable held an
email. `GMAIL_DELEGATED_USER=chair@localnewsimpact.org` split into two
entries and gcloud rejected `localnewsimpact.org` as a malformed dict --
so `./infra/reconcile_queues.sh` could not create its job, and
`scan_sources.sh` would have failed identically on its next run, having
been written before that variable existed.

Any fixed character is a bet on what nobody will ever put in a variable.
These assert that the scripts choose one instead.
"""

import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1] / "infra"
SCRIPTS = sorted(INFRA.glob("*.sh"))

#: The scripts that READ the service's environment back and join it.
#:
#: Not every script that sets environment variables: `manage.sh` writes a
#: short hand-listed set with no commas inside any value, so it needs no
#: delimiter and choosing one would be ceremony. The ones that matter are
#: those building the flag from whatever the service happens to hold,
#: which is where an unexpected character arrives.
JOINERS = [p for p in SCRIPTS if "READ_ENV" in p.read_text()]


def test_there_is_something_to_check():
    """A rename would otherwise make every test below vacuously pass."""
    assert JOINERS, f"no script in {INFRA} reads the service environment back"


def test_a_script_that_hardcodes_its_variables_is_not_required_to_choose():
    """`manage.sh` lists a handful of values it wrote itself. Holding it
    to this rule would be ceremony, and the test exists to say that is
    deliberate rather than an oversight."""
    hardcoded = [
        p
        for p in SCRIPTS
        if "--set-env-vars" in p.read_text() and "READ_ENV" not in p.read_text()
    ]
    for script in hardcoded:
        body = script.read_text()
        assert "^@^" not in body, f"{script.name} uses a delimiter but chooses none"


@pytest.mark.parametrize("script", JOINERS, ids=lambda p: p.name)
def test_no_script_hardcodes_the_delimiter(script):
    """`^@^` is the specific bet that lost."""
    body = script.read_text()
    assert "^@^" not in body, "the delimiter is fixed at '@', which an email contains"


@pytest.mark.parametrize("script", JOINERS, ids=lambda p: p.name)
def test_the_delimiter_is_chosen_from_the_values(script):
    body = script.read_text()
    assert "${DELIM}" in body or "$DELIM" in body, "no chosen delimiter is used"
    assert "for candidate in" in body, "nothing selects a delimiter"


@pytest.mark.parametrize("script", JOINERS, ids=lambda p: p.name)
def test_it_fails_loudly_when_no_delimiter_is_available(script):
    """Falling back to a character that appears in a value would corrupt
    the environment silently, which is worse than not deploying."""
    body = script.read_text()
    assert "sys.exit" in body, "no failure path when every candidate is taken"


@pytest.mark.parametrize("script", JOINERS, ids=lambda p: p.name)
def test_the_candidates_exclude_characters_that_appear_in_real_values(script):
    """`@` is in an email, `:` is in every Cloud SQL instance name and
    every secret reference, `,` is what the delimiter exists to avoid,
    and `=` separates a name from its value."""
    body = script.read_text()
    match = re.search(r'for candidate in "([^"]+)"', body)
    assert match, "the candidate list is not readable"
    candidates = set(match.group(1))
    unusable = candidates & set("@:,=")
    assert not unusable, f"unusable candidates: {unusable}"
    assert len(candidates) >= 3, "one or two candidates is barely a choice"


def test_the_chosen_delimiter_survives_a_value_that_contains_it():
    """The selection logic itself, run against the case that broke it."""
    plain = ["GMAIL_DELEGATED_USER=chair@localnewsimpact.org", "DB_NAME=datadesk"]
    joined = "".join(plain)
    for candidate in "|~#%^!+":
        if candidate not in joined:
            break
    else:  # pragma: no cover - the list is long enough
        pytest.fail("no delimiter available")
    assert candidate not in joined
    assert candidate.join(plain).split(candidate) == plain
