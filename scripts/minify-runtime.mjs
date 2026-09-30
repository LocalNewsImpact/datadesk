// Minify the chart runtime for the image, and refuse to ship it if the
// minified build answers differently from the source.
//
//   node scripts/minify-runtime.mjs <source.js> <out.js>
//
// Every chart page loads datadesk-chart.js, a table included: 210 KB as
// written, 68 KB gzipped, most of it the comments that say why the code is
// the way it is (review 2026-09-30, item 27). Minified it is 25 KB gzipped.
// The source stays as written -- it is what is read and tested -- and the
// Dockerfile's `runtime-js` stage puts this build in its place before
// collectstatic, so templates and hashed names are unchanged.
//
// THE CHECK. Both builds are loaded as a page loads them, with the vendored
// d3, and asked the same questions through `__test`. A minifier that
// changed behaviour fails the image build here rather than on a reader's
// map. esbuild renames identifiers; nothing in the runtime reads a
// function's name or evaluates a string, and `__test`'s keys are property
// names, which are never renamed.

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import vm from "node:vm";
import { buildSync } from "esbuild";

const [source, out] = process.argv.slice(2);
if (!source || !out) {
  console.error("usage: node scripts/minify-runtime.mjs <source.js> <out.js>");
  process.exit(2);
}

const code = readFileSync(source, "utf8");
const built = buildSync({
  stdin: { contents: code, loader: "js" },
  minify: true,
  target: "es2020",
  legalComments: "none",
  write: false,
});
const minified = built.outputFiles[0].text;

// The same stand-in browser tests/chart_runtime.py gives the runtime.
const d3 = readFileSync(join(dirname(source), "d3.min.js"), "utf8");
function load(runtime) {
  const context = {
    console,
    document: {
      addEventListener() {},
      querySelectorAll: () => [],
      documentElement: { dataset: { theme: "light" } },
    },
    matchMedia: () => ({ matches: false }),
  };
  context.window = context;
  context.self = context;
  vm.createContext(context);
  vm.runInContext(d3, context);
  vm.runInContext(runtime, context);
  return context.DatadeskChart;
}

const QUESTIONS = {
  keys: (C) => Object.keys(C).concat(Object.keys(C.__test)).sort(),
  themes: (C) => C.__test.THEMES,
  ramp: (C) => C.__test.quantizeRamp("#cde2fb", "#0d366b", 11),
  bands: (C) => {
    const t = C.__test.theme();
    const values = [1, 1, 1, 2, 2, 4, 5, 7, 9, 12, 18, 25, 40, 88, 204]
      .concat(Array.from({ length: 30 }, (_, i) => 15 + i * 7))
      .sort((a, b) => a - b);
    return ["", "fixed", "5", "absolute"].map((mode) => {
      const config = mode === "absolute" ? { band_scale: "absolute" } : { bands: mode };
      const b = C.__test.storyMapBands(values, config, null, t);
      return {
        steps: b.steps, cuts: b.cuts, ramp: b.ramp, labels: b.bandLabels,
        shades: values.map(b.shadeFor), edges: values.map(b.countyEdge),
      };
    });
  },
  painted: (C) => C.__test.paintedValues(
    [{ geoid: "29019", n: 40 }, { geoid: "29095", n: 0 }, { geoid: "20091", n: 9 }],
    new Set(["29019", "29095"]), (a) => a.n, null),
  sorted: (C) => C.__test.orderRows(
    [{ a: "x", n: 3 }, { a: "y", n: 1 }, { a: "x", n: 2 }], "n", true, 1, ["a"]),
  escaped: (C) => [C.__test.tipRow("<b>", "a & b"), C.__test.tipHead('"q"')],
  spread: (C) => C.__test.spreadPoints([[100, 100], [100, 100], [140, 90]], 3.5),
  frame: (C) => C.__test.framedBy(
    ["29019", "29095", "20091"].map((id) => ({ id })), "29", [],
    (f) => String(f.id), (all) => all),
  values: (C) => [C.__test.fmtValue(1234.5, "n"), C.__test.fmtValue(0.4567, "pct")],
};

const before = load(code);
const after = load(minified);
const differ = [];
for (const [name, ask] of Object.entries(QUESTIONS)) {
  const a = JSON.stringify(ask(before));
  const b = JSON.stringify(ask(after));
  if (a !== b) differ.push(`${name}:\n  source   ${a.slice(0, 300)}\n  minified ${b.slice(0, 300)}`);
}
if (differ.length) {
  console.error("the minified runtime answers differently:\n" + differ.join("\n"));
  process.exit(1);
}

writeFileSync(out, minified);
console.log(
  `datadesk-chart.js ${code.length} -> ${minified.length} bytes; ` +
  `${Object.keys(QUESTIONS).length} questions answered alike`
);
