"""A record table has to reflow on a phone, not merely narrow.

Four columns at 700px is 175px each; below that a headline wraps a word
per line and the decision buttons are too small to hit. The fix is not
per-queue: the rules sit on `.rec-table`, so a queue built later --
discovery is not built yet -- is readable on a phone because it used the
table, and not because somebody remembered to add a second class.

What a table does have to bring is `data-label` on its data cells.
Stacking works without them, and reads as a column of unlabelled values
with nothing saying which is the flag and which is the CIN label. These
tests hold every record table in the console to carrying them, so the
next queue's cells are labelled before it ships rather than after
somebody tries to work it on a phone.
"""

import re
from pathlib import Path

import pytest

CSS = Path("static/css/datadesk.css")
TEMPLATES = Path("templates")

#: The reflow lives here. A record table below this width is a stack of
#: cards; above it, the table is untouched.
BREAKPOINT = "48rem"


def _rec_table_blocks():
    """(template path, table markup) for every `.rec-table` in the console."""
    found = []
    for path in TEMPLATES.rglob("*.html"):
        body = path.read_text()
        for match in re.finditer(
            r'<table[^>]*class="[^"]*\brec-table\b[^"]*".*?</table>', body, re.S
        ):
            found.append((path, match.group(0)))
    return found


def _phone_block():
    css = CSS.read_text()
    start = css.index(f"@media (max-width: {BREAKPOINT})")
    depth, i = 0, css.index("{", start)
    for end in range(i, len(css)):
        if css[end] == "{":
            depth += 1
        elif css[end] == "}":
            depth -= 1
            if depth == 0:
                return css[start : end + 1]
    raise AssertionError("the phone block is not closed")


def test_there_are_record_tables_to_hold():
    """The tests below pass vacuously if the search stops finding tables,
    which is exactly when they would stop protecting anything."""
    assert len(_rec_table_blocks()) >= 3


def test_a_row_becomes_a_card_rather_than_a_narrower_row():
    block = _phone_block()
    for rule in (
        ".rec-table tr",
        ".rec-table td",
        ".rec-table tbody th",
    ):
        assert rule in block, f"{rule} still lays out as a table cell on a phone"
    assert "display: block" in block
    assert ".rec-table thead" in block, "the header row has nothing to head"


def test_the_column_head_moves_onto_the_cell():
    block = _phone_block()
    assert "content: attr(data-label)" in block


def test_the_rules_are_not_bound_to_one_queue():
    """On `.rec-table`, so discovery gets this by using the table."""
    block = _phone_block()
    assert ".queue-rows" not in block, (
        "scoped to the extraction queue's class, so a queue built later "
        "would narrow instead of reflowing"
    )


def test_a_decision_is_big_enough_to_hit():
    """2.75rem is the smallest thing a thumb hits reliably. The verb
    buttons are 1.6rem tall on a desktop, where there is a pointer."""
    block = _phone_block()
    assert ".rec-table .verb" in block
    assert "min-height: 2.75rem" in block


def test_a_field_does_not_zoom_the_page_when_it_takes_focus():
    """iOS zooms into any field under 16px and does not zoom back out,
    which leaves the queue scrolled sideways with the decision column off
    the edge."""
    block = _phone_block()
    fields = block[block.index(".rec-table .fixval") :]
    assert "font-size: 1rem" in fields[:400]


def test_the_dock_clears_the_rows_and_the_home_indicator():
    block = _phone_block()
    assert "env(safe-area-inset-bottom)" in block, "the submit button sits under it"
    assert ".queue { padding-bottom" in block, "the last row hides behind the dock"


@pytest.mark.parametrize(
    "path,table", [(p, t) for p, t in _rec_table_blocks()], ids=lambda v: str(v)[:40]
)
def test_every_data_cell_says_which_column_it_answers(path, table):
    """Stacked, a cell with no label is a value with no question."""
    body = table[table.index("<tbody") :] if "<tbody" in table else table
    for cell in re.findall(r"<td\b[^>]*>", body):
        # A cell spanning the row is a message, not a value in a column --
        # "No sources in the datasets you can see" heads nothing.
        if re.search(r'colspan="[4-9]"', cell):
            continue
        assert "data-label=" in cell, (
            f"{path}: {cell.strip()} carries no column head, so on a phone "
            "it stacks as an unlabelled value"
        )
