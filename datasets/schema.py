"""What a publisher record is: its fields, which are required, and what
counts as a value for each.

Five places already knew part of this and none of them was the answer.
`review/services.py` knows which fields may be written, `review/flags.py`
knows which are defects when missing, `scan_sources` knows which a file
can supply, `_create_proposed_sources` knows which a new record takes,
and `datasets/publishers.py` knows the words two of them accept. A field
added to the table had to be remembered in all five, and the one that was
forgotten each time was the one that then had no rule at all -- which is
how `zip` and `zip_code`, and `address1` and `address`, both ended up in
the same column meaning the same thing.

This is the declaration those five read from. It says, per field:

  required   a record without it is incomplete, and the scan says so
  rule       what a value has to look like
  vocabulary for a rule of "vocabulary", which list of words it draws on

A vocabulary is a list somebody maintains rather than a constant somebody
edits: `datasets/terms.py` reads it from the database, seeded from the
words the corpus already uses, so a new kind of publication is added on a
page rather than in a deploy.
"""

import re
from dataclasses import dataclass

#: A value is checked against one of these.
TEXT = "text"  # anything non-empty
VOCABULARY = "vocabulary"  # one of a maintained list of words
ZIP = "zip"
PHONE = "phone"
ADDRESS = "address"
STATE = "state"
HOST = "host"
URL = "url"

RULES = (TEXT, VOCABULARY, ZIP, PHONE, ADDRESS, STATE, HOST, URL)

#: How each rule is said to a person. The keys are for the code; a page
#: that read them out said a field held "a address" and "a url".
RULE_NAMES = {
    TEXT: "any text",
    VOCABULARY: "one of these words",
    ZIP: "a ZIP code — five digits, or five and four",
    PHONE: "a phone number — ten digits, punctuated however you like",
    ADDRESS: "a street address — a number and a street",
    STATE: "a state's two-letter postal code",
    HOST: "a host name",
    URL: "a web address, starting http:// or https://",
}


@dataclass(frozen=True)
class FieldSpec:
    """One field of a publisher record."""

    key: str  # "county", or "meta.state" for a key inside the JSON column
    label: str  # what a reader sees
    required: bool
    rule: str = TEXT
    vocabulary: str = ""  # when rule is VOCABULARY
    note: str = ""

    @property
    def rule_name(self):
        """What this field holds, said to a person."""
        return RULE_NAMES.get(self.rule, self.rule)

    @property
    def in_meta(self):
        return self.key.startswith("meta.")

    @property
    def column(self):
        """The database column this field lives in."""
        return self.key.partition(".")[0]


#: The record, in the order somebody reads it: what the publication is
#: called and where it is, then how to reach it, then the two fields whose
#: values come from a maintained vocabulary. The vocabularies are long
#: enough on the page to push everything after them out of sight, which is
#: where the address and the home page were.
#:
#: Required means a record without it is incomplete and the scan says so,
#: not that the database refuses it -- the corpus is full of records that
#: predate the rule, and refusing them at the door would only mean nobody
#: could correct them.
FIELDS = (
    FieldSpec(
        "host",
        "Host",
        required=True,
        rule=HOST,
        note=(
            "The record's only unique column. A publisher without one "
            "cannot be crawled, and a second row for one host cannot be "
            "written."
        ),
    ),
    FieldSpec("canonical_name", "Publication name", required=True),
    FieldSpec("city", "City", required=True),
    FieldSpec("county", "County", required=True),
    FieldSpec("meta.state", "State", required=True, rule=STATE),
    FieldSpec(
        "owner",
        "Owner",
        required=False,
        note="Written the way the corpus writes it, or one company counts as two.",
    ),
    FieldSpec("meta.address1", "Street address", required=False, rule=ADDRESS),
    FieldSpec("meta.address2", "Address, second line", required=False),
    FieldSpec("meta.zip", "ZIP code", required=False, rule=ZIP),
    FieldSpec("meta.phone", "Phone", required=False, rule=PHONE),
    FieldSpec("meta.homepage", "Home page", required=True, rule=URL),
    FieldSpec(
        "type",
        "Kind of publication",
        required=True,
        rule=VOCABULARY,
        vocabulary="publisher_type",
    ),
    FieldSpec(
        "meta.frequency",
        "How often it publishes",
        required=False,
        rule=VOCABULARY,
        vocabulary="publisher_frequency",
    ),
)

BY_KEY = {field.key: field for field in FIELDS}
REQUIRED = tuple(field.key for field in FIELDS if field.required)

#: Keys that mean what a field above means and are written differently.
#: Both spellings are in the column today -- `zip` on 1,090 records and
#: `zip_code` on one, `address1` on 1,090 and `address` on one -- and a
#: reader of either finds nothing on the records using the other.
ALIASES = {
    "zip_code": "meta.zip",
    "address": "meta.address1",
    "publication_frequency": "meta.frequency",
    "media_type": "type",
}

# --- what a value has to look like -------------------------------------------
#
# Loose on purpose. These say "this is not a ZIP code" and never "this is
# the wrong ZIP code": the first is a rule, the second is a fact about the
# world that no pattern knows. A rule that refuses a real value is worse
# than no rule, because the record it refuses is correct.

_ZIP = re.compile(r"^\d{5}(-\d{4})?$")
#: Ten digits, however they are punctuated, optionally +1. Extensions are
#: kept out of the match rather than refused -- "x204" is part of how a
#: newsroom is reached.
_PHONE = re.compile(r"^\+?1?\D*(\d\D*){10}(\s*(x|ext\.?|extension)\s*\d+)?$", re.I)
#: A number and a word. "Main Street" is not an address anybody can post
#: to, and neither is "1600".
_ADDRESS = re.compile(r"^(?=.*\d)(?=.*[A-Za-z]).{4,}$")
_HOST = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
_URL = re.compile(r"^https?://\S+\.\S+$", re.I)


def check(field, value, terms=None):
    """(ok, why) for one value of one field.

    `why` is written for the person looking at the record, so it says what
    is wrong with this value rather than naming the rule that refused it.

    An empty value is not this function's business: whether it may be
    empty is `field.required`, which the scan reads separately. Checking
    both here would report one defect twice.
    """
    text = str(value or "").strip()
    if not text:
        return True, ""
    if field.rule == VOCABULARY:
        from datasets.terms import known

        if known(field.vocabulary, text):
            return True, ""
        return False, f"{text!r} is not one of the {field.label.lower()} words"
    if field.rule == ZIP:
        return (
            (True, "")
            if _ZIP.match(text)
            else (False, f"{text!r} is not a ZIP code: five digits, or five and four")
        )
    if field.rule == PHONE:
        return (
            (True, "")
            if _PHONE.match(text)
            else (False, f"{text!r} is not a phone number: ten digits")
        )
    if field.rule == ADDRESS:
        return (
            (True, "")
            if _ADDRESS.match(text)
            else (False, f"{text!r} is not a street address: a number and a street")
        )
    if field.rule == STATE:
        from datasets.geo import state_code

        return (
            (True, "")
            if state_code(text) and len(text) == 2 and text.isupper()
            else (False, f"{text!r} is not a state's two-letter postal code")
        )
    if field.rule == HOST:
        return (
            (True, "") if _HOST.match(text) else (False, f"{text!r} is not a host name")
        )
    if field.rule == URL:
        return (
            (True, "")
            if _URL.match(text)
            else (
                False,
                f"{text!r} is not a web address: it starts http:// or https://",
            )
        )
    return True, ""


def read(source, key):
    """One field's value off a source, column or key inside `meta`."""
    if key.startswith("meta."):
        return ((source.meta or {}).get(key.partition(".")[2]) or "").strip()
    return (getattr(source, key, "") or "").strip()
