"""A story map scanned the whole corpus twice to look up a few dozen rows.

Two lookups sat above the loop that reads them:

    already_placed = set(base.filter(point_lat__isnull=False)
                             .values_list("id", flat=True))
    source_of      = dict(base.values_list("id", "candidate_link__source_id"))

Both are consulted ONLY inside `for ... in manual` -- the stories a
reviewer placed by hand, of which there are a few dozen. Asked of the
whole queryset they read every article in every wired dataset to answer
about that handful.

MEASURED over four datasets, against a database with nothing else
running: 66s for the placed ids and 31s for the sources. A story map that
took 169.3s takes 31.8s with them narrowed, and 12.8s warm, returning the
same 838 points and the same 9,221 stories.

And a map where nobody has placed a story by hand -- the common case --
paid 97 seconds for two answers it never read. It now asks nothing at
all.
"""

from pathlib import Path

SOURCE = Path("visuals/corpus.py").read_text()


def _between(start, end):
    return SOURCE[SOURCE.index(start) : SOURCE.index(end)]


class TestTheLookupsAreNarrowedToWhatConsultsThem:
    def _block(self):
        return _between("    manual_ids = {", "    by_geoid = {}")

    def test_the_placed_ids_are_filtered_to_the_manual_rows(self):
        """THE REGRESSION. Without `id__in` this reads every article in
        every wired dataset."""
        assert "id__in=manual_ids" in self._block()

    def test_the_sources_are_filtered_to_the_manual_rows_too(self):
        block = self._block()
        assert block.count("id__in=manual_ids") >= 2

    def test_neither_is_asked_when_nothing_was_placed_by_hand(self):
        """The common case. A map with no manual placements should issue
        no query for them at all, not an empty-set query."""
        block = self._block()
        assert "if manual_ids" in block
        assert "else set()" in block
        assert "else {}" in block

    def test_the_ids_come_from_the_manual_rows(self):
        assert "manual_ids = {article_id for article_id, *_rest in manual}" in SOURCE


class TestTheyAreStillOnlyReadWhereTheyWereBefore:
    """The narrowing is only safe because these are consulted nowhere
    else. If a later change reads them outside the manual loop, it reads
    a set that no longer covers the corpus -- and would silently place a
    dot twice or lose a publisher."""

    def test_already_placed_is_read_only_in_the_manual_loop(self):
        loop = _between(
            "    for article_id, geoid, level, is_point, city, county",
            "    # AFTER THE HUMAN CENTRES",
        )
        others = SOURCE.count("already_placed") - SOURCE.count("already_placed = ")
        assert "already_placed" in loop
        assert others == loop.count("already_placed")

    def test_source_of_is_read_only_in_the_manual_loop(self):
        loop = _between(
            "    for article_id, geoid, level, is_point, city, county",
            "    # AFTER THE HUMAN CENTRES",
        )
        others = SOURCE.count("source_of") - SOURCE.count("source_of = ")
        assert others == loop.count("source_of")


class TestTheRollUpDoesNotChargeEveryMap:
    """An ArrayAgg on the points query counted publishers exactly for a
    merge, and cost 20.5s -> 63.3s on every story map whether or not
    anyone had asked for one. The ids are fetched inside the roll-up
    branch now, for the dots that actually merge."""

    def test_the_points_query_carries_no_array_aggregate(self):
        points = _between("    points = list(", "    # A HUMAN CENTRE IS A DOT")
        assert "ArrayAgg" not in points

    def test_the_ids_are_fetched_inside_the_roll_up(self):
        rollup = _between(
            '    if (config or {}).get("roll_up") == "city":',
            '    points.sort(key=lambda r: -r["stories"])',
        )
        assert "enrichment__point_geoid__in=wanted" in rollup

    def test_only_the_dots_that_move_are_asked_about(self):
        rollup = _between(
            '    if (config or {}).get("roll_up") == "city":',
            '    points.sort(key=lambda r: -r["stories"])',
        )
        assert 'row["city_geoid"] != row["geoid"]' in rollup
