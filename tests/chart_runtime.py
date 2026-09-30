"""The chart runtime, run in node, for tests that ask it what it does.

A renderer test that reads `datadesk-chart.js` as text breaks on a rename
and passes through a real regression: the first push of the layered map
was rejected by two of them for a renamed guard, not a change in
behaviour (review 2026-09-30, item 29). This loads the whole runtime --
with d3 and topojson when asked, as a page would -- and hands a test
`DatadeskChart.__test` as `T`, so it can put values in and read values
out.

Six test files carried their own copy of this harness, and five of them
ran fragments spliced out of the source rather than the runtime, one of
them with stand-ins for d3 and `quantizeRamp` that the shipped code
never meets.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static" / "js"
CHART = STATIC / "datadesk-chart.js"
LIBS = {
    "d3": STATIC / "d3.min.js",
    "topojson": STATIC / "topojson-client.min.js",
}

#: Just enough browser for the runtime to load: it reads the page's theme
#: and the OS colour scheme at draw time, and nothing else at load.
_BROWSER = """
global.window = global;
global.document = {
  addEventListener() {}, querySelectorAll: () => [],
  documentElement: { dataset: { theme: "light" } },
};
global.matchMedia = () => ({ matches: false });
"""


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the renderer with")
    return node


def run(script, libs=("d3",)):
    """Run `script` after the runtime has loaded; return what it printed.

    The script sees `T` (the runtime's `__test`) and whichever of `d3`
    and `topojson` were asked for, and prints one JSON value with
    `console.log(JSON.stringify(...))`.
    """
    # The vendored builds are UMD: under node they fill `module.exports`
    # rather than a global, so they are required and hung on the global
    # the runtime reads them from.
    loads = "".join(
        f"global.{name} = require({json.dumps(str(LIBS[name]))});\n" for name in libs
    )
    program = (
        _BROWSER
        + loads
        + CHART.read_text()
        + "\nconst T = DatadeskChart.__test;\n"
        + script
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(program)
        where = fh.name
    try:
        done = subprocess.run([_node(), where], capture_output=True, text=True)
    finally:
        os.unlink(where)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def value(expression, libs=("d3",)):
    """Evaluate one JavaScript expression against the runtime."""
    return run(f"console.log(JSON.stringify({expression}));", libs=libs)
