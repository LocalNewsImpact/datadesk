"""Newsrooms at one point are each drawn.

Outlets placed at the same point -- a town's centre, one building -- drew
as one dot: 235 on the Missouri outlet map showed as 160.
"""

import itertools
import math

import pytest

from tests.test_a_table_is_a_pivot import _node

R = 3.5


def spread(xy, radius=R):
    return _node(f"T.spreadCoincident({xy}, {radius})")


def closest(points):
    return min(math.dist(a, b) for a, b in itertools.combinations(points, 2))


@pytest.mark.parametrize("n", [2, 3, 5, 8, 12])
def test_newsrooms_at_one_point_do_not_overlap(n):
    out = spread([[100, 100]] * n)
    assert len({tuple(p) for p in out}) == n
    assert closest(out) >= 2 * R


def test_they_stay_near_their_point():
    out = spread([[100, 100]] * 5)
    assert max(math.dist(p, (100, 100)) for p in out) < 6 * R


def test_a_dot_on_its_own_does_not_move():
    xy = [[10, 10], [200, 50], [60, 300]]
    assert spread(xy) == xy


def test_dots_nearly_on_top_of_each_other_are_spread():
    """Two addresses a few metres apart project a pixel apart."""
    out = spread([[100, 100], [101, 100], [300, 300]])
    assert math.dist(out[0], out[1]) >= 2 * R
    assert out[2] == [300, 300]


def test_a_fanned_group_does_not_land_on_its_neighbours():
    """Six at one point, with a town's dot just beside them."""
    out = spread([[100, 100]] * 6 + [[110, 100], [100, 111]])
    assert closest(out) >= 2 * R


def test_only_the_outlet_map_spreads():
    """A story dot is sized by its count; overlapping is its meaning."""
    from pathlib import Path

    chart = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    js = chart.read_text()
    assert "const xy = byCategory ? spreadCoincident(at, dotR) : at;" in js
