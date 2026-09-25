/* Datadesk chart runtime (SCOPE.md §2.6 v2).
 *
 * Renders a builder config + data rows with the vendored Observable Plot.
 * Color system: the validated reference palette — categorical slots are
 * assigned in fixed order by first appearance and NEVER cycled: series
 * beyond the cap fold into a gray "Other". Sequential is one hue
 * (blue, light→dark); diverging is blue↔red with a neutral gray midpoint.
 * Every multi-series chart carries a legend; every chart gets hover tips;
 * the host template provides the data-table view (the relief rule for the
 * light-mode contrast WARN, and the accessibility table).
 */
(function (global) {
  "use strict";

  const LIGHT = {
    series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
             "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    seqLow: "#cde2fb", seqHigh: "#0d366b",
    divLow: "#0d366b", divMid: "#f0efec", divHigh: "#8f1d1d",
    other: "#898781", missing: "#e1e0d9",
    ink: "#0b0b0b", muted: "#898781", grid: "#e1e0d9",
    boundary: "#c3c2b7", surface: "#fcfcfb",
  };
  const DARK = {
    series: ["#3987e5", "#d95926", "#199e70", "#c98500",
             "#d55181", "#008300", "#9085e9", "#e66767"],
    // WIDENED 2026-09-14 so dark carries ten bands as well as light does.
    // A dark-mode ramp runs dark to pale, and this one stopped at
    // #9ec5f4 -- 0.481 of relative luminance against light's 0.706. Ten
    // bands over that separated at 0.045 where light managed 0.068, so
    // the same map was measurably harder to read in dark mode.
    //
    // The room was all at the pale end: the dark ends already sit near
    // the surface and cannot go lower without the sparsest counties
    // merging into the background. Lightened along the same line toward
    // white, so the hue is unchanged and only the reach reaches further.
    seqLow: "#104281", seqHigh: "#d2e4fa",
    divLow: "#9ec5f4", divMid: "#383835", divHigh: "#e66767",
    other: "#898781", missing: "#2c2c2a",
    ink: "#ffffff", muted: "#898781", grid: "#2c2c2a",
    boundary: "#383835", surface: "#1a1a19",
  };

  // Brand themes (validated with the dataviz palette validator, both
  // modes, 2026-08-21 — rerun it before touching any series array):
  //   lnic    — localnewsimpact.org blues (the house default)
  //   mizzou  — MU gold #f1b82d stepped chart-safe, MU crimson
  //   rji     — RJI steel blue #1c5e90, MU-affiliation gold
  //   datadesk — the neutral reference palette
  // Chrome (ink, grid, surfaces) is shared; only series and ramps swap.
  const THEMES = {
    datadesk: {
      light: {
        ...LIGHT,
        points: ["#a31414", "#4a3aa7", "#c2187e", "#eda100", "#6b6b6b"],
      },
      dark: {
        ...DARK,
        points: ["#c23a3a", "#9085e9", "#d55181", "#eda100", "#9a9a9a"],
      },
    },
    lnic: {
      light: {
        ...LIGHT,
        series: ["#00618f", "#eb6834", "#59bbeb", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
        seqLow: "#d3ecfa", seqHigh: "#003a56",
        divLow: "#003a56", divMid: "#f0efec", divHigh: "#8f1d1d",
        // Dots must not read as another step of the shading ramp, and
        // must survive a mono print. The five slots are PRECISION-indexed
        // (place, block, county, state, tract); `place` and `state` carry
        // 8,863 and 2,090 of the corpus's points, so they take the two
        // most separated colours -- 0.083 and 0.435 relative luminance, a
        // gap of 0.352. The three they replaced spanned 0.205 in total and
        // merged in greyscale.
        points: ["#a31414", "#4a3aa7", "#c2187e", "#eda100", "#6b6b6b"],
      },
      dark: {
        ...DARK,
        series: ["#1d6f9e", "#d95926", "#2f9ecf", "#c98500",
                 "#d55181", "#008300", "#9085e9", "#e66767"],
        seqLow: "#0e4a6d", seqHigh: "#d9effa",
        divLow: "#9fd6f2", divMid: "#383835", divHigh: "#e66767",
        points: ["#c23a3a", "#9085e9", "#d55181", "#eda100", "#9a9a9a"],
      },
    },
    mizzou: {
      light: {
        ...LIGHT,
        series: ["#d9a018", "#a31414", "#2a78d6", "#1baf7a",
                 "#e87ba4", "#008300", "#4a3aa7", "#eb6834"],
        seqLow: "#f7e6bd", seqHigh: "#6b4d05",
        divLow: "#184f95", divMid: "#f0efec", divHigh: "#7a0f0f",
        // Gold is this theme's ramp, so `state` is a light blue instead:
        // same 0.353 greyscale gap from `place`, no collision with the
        // shading underneath.
        points: ["#a31414", "#4a3aa7", "#c2187e", "#59bbeb", "#6b6b6b"],
      },
      dark: {
        ...DARK,
        series: ["#c98500", "#c23a3a", "#3987e5", "#d95926",
                 "#199e70", "#9085e9", "#d55181", "#008300"],
        seqLow: "#5c4304", seqHigh: "#f5e3b3",
        divLow: "#9ec5f4", divMid: "#383835", divHigh: "#e66767",
        points: ["#c23a3a", "#9085e9", "#d55181", "#59bbeb", "#9a9a9a"],
      },
    },
    rji: {
      light: {
        ...LIGHT,
        series: ["#1c5e90", "#d9a018", "#1baf7a", "#eb6834",
                 "#2a78d6", "#e87ba4", "#008300", "#4a3aa7"],
        seqLow: "#d4e5f2", seqHigh: "#0d3350",
        divLow: "#0d3350", divMid: "#f0efec", divHigh: "#8f1d1d",
        points: ["#a31414", "#4a3aa7", "#c2187e", "#d9a018", "#6b6b6b"],
      },
      dark: {
        ...DARK,
        series: ["#2f7cb8", "#c98500", "#199e70", "#d95926",
                 "#3987e5", "#d55181", "#008300", "#9085e9"],
        seqLow: "#123a5c", seqHigh: "#d5e6f4",
        divLow: "#a8cce8", divMid: "#383835", divHigh: "#e66767",
        points: ["#c23a3a", "#9085e9", "#d55181", "#eda100", "#9a9a9a"],
      },
    },
  };
  const DEFAULT_THEME = "lnic";

  // Named taxonomies: a fixed vocabulary whose categories must always
  // appear in the same order with the same colour, whatever a particular
  // chart's volumes are. A taxonomy is never folded into "Other" — the
  // whole point is that the reader can compare the same ten needs across
  // every column.
  //
  // The CIN palette is ten slots, ordered warm/cool alternating so no two
  // neighbouring segments of a stack share a hue family. Validated with
  // the dataviz palette validator on the adjacent pairlist, both modes,
  // 2026-08-22 — rerun it before changing any value.
  const TAXONOMIES = {
    cin: {
      order: [
        "Emergencies and Public Safety",
        "Health",
        "Education",
        "Economic Development",
        "Environment and Planning",
        "Transportation Systems",
        "Civic Life",
        "Political life",
        "Civic information",
        "Sports",
      ],
      light: ["#256abf", "#eb6834", "#1baf7a", "#a35a00", "#4a3aa7",
              "#eda100", "#e87ba4", "#008300", "#9a4dbf", "#8f8fdc"],
      dark: ["#3987e5", "#d95926", "#199e70", "#a35a00", "#9085e9",
             "#c98500", "#d55181", "#008300", "#cf5fa8", "#6d8fdd"],
    },
  };

  function taxonomy(name, t) {
    const spec = TAXONOMIES[name];
    if (!spec) return null;
    const dark = t.surface !== LIGHT.surface;
    return { order: spec.order, colors: dark ? spec.dark : spec.light };
  }

  // Series caps per form: adjacent-comparison forms validated to 8;
  // all-pairs forms (scatter, categorical map points) to 3.
  const CAP_ADJACENT = 8;
  const CAP_ALLPAIRS = 3;

  function theme(name) {
    const modes = THEMES[name] || THEMES[DEFAULT_THEME];
    const stamped = document.documentElement.dataset.theme;
    if (stamped === "dark") return modes.dark;
    if (stamped === "light") return modes.light;
    return matchMedia("(prefers-color-scheme: dark)").matches
      ? modes.dark
      : modes.light;
  }

  function isFiniteNumber(v) {
    return v !== "" && v !== null && !isNaN(v) && isFinite(+v);
  }
  const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

  // Coerce a column: all-numeric -> numbers, all-ISO-dates -> Dates.
  function coerce(rows, key) {
    const values = rows.map((r) => r[key]).filter((v) => v != null && v !== "");
    if (!values.length) return rows;
    if (values.every(isFiniteNumber)) {
      return rows.map((r) => ({ ...r, [key]: r[key] === "" || r[key] == null ? null : +r[key] }));
    }
    if (values.every((v) => ISO_DATE.test(String(v)))) {
      return rows.map((r) => ({ ...r, [key]: r[key] ? new Date(r[key]) : null }));
    }
    return rows;
  }

  // Fixed-order slot assignment with fold-to-Other beyond the cap.
  function foldSeries(rows, key, cap) {
    const order = [];
    for (const r of rows) {
      const v = r[key];
      if (v != null && v !== "" && !order.includes(v)) order.push(v);
    }
    if (order.length <= cap) return { rows, domain: order, folded: false };
    const keep = new Set(order.slice(0, cap));
    return {
      rows: rows.map((r) => keep.has(r[key]) ? r : { ...r, [key]: "Other" }),
      domain: [...order.slice(0, cap), "Other"],
      folded: true,
    };
  }

  function colorScale(domain, t, folded) {
    const range = domain.map((d, i) =>
      folded && d === "Other" ? t.other : t.series[i % t.series.length]);
    return { domain, range, legend: domain.length > 1 };
  }

  // The most a scale can be read as one. Beyond this the steps are too
  // close to tell apart and the thing has stopped being a scale a reader
  // can follow, so it falls back to distinct hues -- which at least
  // separate.
  const SCALE_STEPS = 9;

  // An ordered scale: None, Very little, Some, Quite a bit, A lot. Those
  // are not five unrelated things, and five unrelated hues say they are.
  // One hue, light to dark, in the order the values appear -- so the
  // segments read as a progression and the legend reads as its scale.
  //
  // The order is the order the values first appear in the data, which is
  // the only statement of it there is: a scale is ordered by what it
  // means, and nothing in a column of text says which end is which.
  function scaleColors(domain, t) {
    if (domain.length < 2 || domain.length > SCALE_STEPS) return null;
    return {
      domain,
      range: quantizeRamp(t.seqLow, t.seqHigh, domain.length),
      legend: true,
    };
  }

  function pad(v, n) {
    return String(v ?? "").replace(/\.0$/, "").padStart(n, "0");
  }

  // Geographic levels, nation → census tract. Nation/state/county ship
  // as single national files; places and tracts load per state, the
  // states derived from the data's GEOID prefixes
  // (infra/fetch_boundaries.sh builds and commits those files).
  const GEO_LEVELS = {
    nation: { file: "nation-10m.json", object: "nation", idLength: 0 },
    states: { file: "states-10m.json", object: "states", idLength: 2 },
    counties: { file: "counties-10m.json", object: "counties", idLength: 5 },
    places: { perState: "places/", idLength: 7 },
    tracts: { perState: "tracts/", idLength: 11 },
  };

  const geoCache = {};
  function fetchJSON(url) {
    geoCache[url] = geoCache[url] || fetch(url).then((r) => {
      if (!r.ok) throw new Error(url);
      return r.json();
    });
    return geoCache[url];
  }

  function toFeatures(topo, objectName) {
    const object = topo.objects[objectName] || Object.values(topo.objects)[0];
    const features = global.topojson.feature(topo, object).features;
    for (const f of features) {
      if (f.id == null && f.properties) f.id = f.properties.GEOID;
    }
    return features;
  }

  // Resolve a level's features; ids drive which per-state files load.
  function boundaries(base, level, ids, urls) {
    const spec = GEO_LEVELS[level] || GEO_LEVELS.states;
    if (!spec.perState) {
      // The resolved URL where the page gave us one. It carries the
      // manifest hash, which is both what makes it cacheable for good and
      // what makes it the same URL the page preloads -- built here from a
      // bare directory it was neither, and the file came down twice.
      const url = (urls && urls[level]) || base + spec.file;
      return fetchJSON(url).then((topo) => toFeatures(topo, spec.object));
    }
    const states = [...new Set((ids || []).map((id) => id.slice(0, 2)))]
      .filter((s) => /^\d\d$/.test(s));
    if (!states.length) {
      return Promise.reject(new Error(
        "tract/place maps need a joined GEOID column to pick the states"));
    }
    return Promise.all(
      states.map((s) => fetchJSON(`${base}${spec.perState}${s}.json`)
        .then((topo) => toFeatures(topo, level)))
    ).then((sets) => sets.flat());
  }

  function baseMarks(Plot, t) {
    return [Plot.gridY({ stroke: t.grid, strokeOpacity: 1 })];
  }

  // How wide the chart may draw. `render` empties the element before it
  // measures, so an element whose width comes from its own content reports
  // nothing -- and the old `|| 640` turned that into a chart drawn at 640px
  // in a pane twice that wide, silently, in every preview. Climb until
  // something has a width of its own; that ancestor is the column the chart
  // is meant to fill. Subtract padding at each level so the answer is room to
  // draw in, not the box around it.
  function roomFor(el) {
    for (let node = el; node; node = node.parentElement) {
      const box = getComputedStyle(node);
      const room = node.clientWidth
        - (parseFloat(box.paddingLeft) || 0)
        - (parseFloat(box.paddingRight) || 0);
      if (room > 0) return room;
    }
    return 640;
  }

  function render(el, config, rows, opts) {
    const t = theme(config.theme);
    el.textContent = "";
    const Plot = global.Plot;
    const width = Math.max(320, roomFor(el));
    const kind = config.kind || "table";
    // The story map's payload is an object of layers, not a row array;
    // every other form takes rows and needs at least one.
    if (kind !== "storymap" && (!rows || !rows.length)) {
      el.textContent = "No data.";
      return;
    }

    if (kind === "table")
      return renderTable(el, rows, opts && opts.credits, null, null, config);
    if (kind === "roster")
      return renderRoster(el, config, rows, opts, t);
    if (kind === "choropleth" || kind === "points") {
      return renderMap(el, config, rows, opts, t, width);
    }
    if (kind === "locator") {
      return renderLocator(el, config, rows, opts, t, width);
    }
    if (kind === "storymap") return renderStoryMap(el, config, rows, opts, t, width);
    if (kind === "donut") return renderDonut(el, config, rows, t, width);
    if (kind === "chord") return renderChord(el, config, rows, t, width);
    if (kind === "sankey") return renderSankey(el, config, rows, t, width);
    if (kind === "arc") return renderArc(el, config, rows, t, width);
    if (kind === "flowmap")
      return renderFlowMap(el, config, rows, opts, t, width);

    const x = config.x, y = config.y, series = config.series;
    if (!x || !y) { el.textContent = "Pick the x and y columns."; return; }
    rows = coerce(coerce(rows.slice(), y), x);

    let domain = [], folded = false;
    let color;
    const taxa = series ? taxonomy(config.taxonomy, t) : null;
    if (taxa) {
      // Every category in the taxonomy's order, present in the data or
      // not, so the legend and the stack read the same on every chart.
      const seen = new Set(rows.map((r) => r[series]));
      domain = taxa.order.filter((v) => seen.has(v));
      const extra = [...seen].filter(
        (v) => v != null && v !== "" && !taxa.order.includes(v));
      const range = domain.map((v) => taxa.colors[taxa.order.indexOf(v)]);
      // A value outside the taxonomy is a data problem, shown as such.
      domain.push(...extra);
      range.push(...extra.map(() => t.other));
      color = { domain, range, legend: domain.length > 1 };
    } else if (series) {
      ({ rows, domain, folded } = foldSeries(
        rows, series, kind === "scatter" ? CAP_ALLPAIRS : CAP_ADJACENT));
      // A scale is never folded into "Other": a bucket at the end of a
      // progression is not a step of it, and the fold would put one
      // there.
      color =
        (!folded && config.series_scale === "sequential"
          ? scaleColors(domain, t)
          : null) || colorScale(domain, t, folded);
    }
    const stroke1 = t.series[0];
    const marks = baseMarks(Plot, t);
    const horizontal = kind === "bar" && config.horizontal;
    const common = { tip: true };
    const sort = config.sort === "y"
      ? (horizontal ? { y: "-x" } : { x: "-y" })
      : undefined;

    let marginRight;
    if (kind === "bar") {
      // Ordering is set through the scale domain above, not per-mark, so
      // a percent stack can order by total rather than by segment.
      // Off puts them side by side: the bars are grouped rather than
      // stacked, which moves the category to a facet axis and every
      // decision about scales below with it.
      const grouped = !!series && config.stacked === false;
      const enc = horizontal
        ? { y: x, x: y, fill: series || stroke1, inset: 0.5 }
        : { x, y, fill: series || stroke1, sort, inset: 0.5 };
      // Plot stacks each column in that column's own row order, so the
      // segments would sit in a different sequence per category. Pinning
      // the order to the colour domain makes the stack readable across
      // columns: the same need is always the same band.
      if (series) enc.order = domain;
      if (grouped) {
        // Side by side: the category becomes the facet, and the series
        // are the bands inside each one.
        enc[horizontal ? "fy" : "fx"] = enc[horizontal ? "y" : "x"];
        enc[horizontal ? "y" : "x"] = series;
        // `order` is a stack option and there is no stack; `sort` would
        // rank the bands by value inside each facet separately, so the
        // same series would sit in a different place in every one. The
        // colour domain orders them instead, which is what keeps a stack
        // readable across columns for the same reason.
        delete enc.order;
        delete enc.sort;
      }
      // "percent" turns a stack into a composition: each column fills the
      // axis and the series read as shares.
      if (config.stack === "percent" && series) {
        enc.offset = "expand";
      }
      // A stacked segment is part of a whole, so the hover says both what
      // it is and what it is of.
      if (series && config.stacked !== false) {
        shareInTip(enc, rows, x, y, horizontal ? "x" : "y");
      }
      // `common` first: it carries `tip: true`, and spread last it
      // overwrote the tip options a percent stack sets above -- so the
      // line saying what the share is a share of appeared beside the
      // fraction it replaces rather than instead of it.
      marks.push((horizontal ? Plot.barX : Plot.barY)(rows, { ...common, ...enc, rx: 2 }));
      marks.push(horizontal ? Plot.ruleX([0], { stroke: t.boundary }) : Plot.ruleY([0], { stroke: t.boundary }));
    } else if (kind === "line" || kind === "area") {
      const enc = { x, y, ...common };
      if (series) enc.stroke = series; else enc.stroke = stroke1;
      if (kind === "area") {
        const area = { x, y, fillOpacity: 0.25 };
        if (series) { area.fill = series; area.order = domain; }
        else area.fill = stroke1;
        marks.push(Plot.areaY(rows, area));
        // Stacked bands are parts of a whole, the same as a stacked bar's
        // segments, so the hover says the value and its share of that
        // date's total. One series is not a composition and gets neither.
        if (series) shareInTip(enc, rows, x, y, "y");
      }
      marks.push(Plot.line(rows, { ...enc, strokeWidth: 2 }));
      // Selective direct labels: line-end names when few series.
      if (series && domain.length >= 2 && domain.length <= 4) {
        marks.push(Plot.text(rows, Plot.selectLast({
          x, y, z: series, text: series, dx: 6, textAnchor: "start", fill: t.ink,
        })));
        marginRight = 12 + 7 * Math.max(...domain.map((d) => String(d).length));
      }
      marks.push(Plot.ruleY([0], { stroke: t.boundary }));
    } else if (kind === "scatter") {
      const enc = { x, y, r: 4, ...common };
      if (series) enc.fill = series; else enc.fill = stroke1;
      if (config.size) {
        rows = coerce(rows, config.size);
        enc.r = config.size;
      }
      // 2px surface ring separates overlapping marks.
      marks.push(Plot.dot(rows, { ...enc, stroke: t.surface, strokeWidth: 1 }));
    } else {
      el.textContent = "Unknown chart kind: " + kind;
      return;
    }

    const percentStack = kind === "bar" && config.stack === "percent" && series;
    let xScale = { label: config.xlabel || undefined, tickSize: 0 };
    let yDomain;
    let fxDomain;
    let fyDomain;
    let marginLeft;
    let marginBottom;
    let height = 420;
    if (kind === "bar") {
      // Where the category ends up. Stacked, it is the band axis -- y
      // when the bars run horizontally, x when they stand up. Side by
      // side, it is the facet axis instead, and the series takes the band.
      //
      // This ordering was written onto the band axis either way, so a
      // grouped chart set the x domain to a list of counties while every
      // bar's x was a CIN need. No bar fell inside the domain and none
      // drew: axes, a grid, a baseline and nothing else. The line that
      // chose the axis read `horizontal ? x : x`, which is the same
      // answer twice.
      const grouped = !!series && config.stacked === false;
      const order = [];
      for (const r of rows) if (!order.includes(r[x])) order.push(r[x]);
      if (config.sort === "y") {
        // A percent stack is all 100% wide, so "by value" means by the
        // category's total — otherwise the ordering says nothing. The
        // same is true of a group of bars, whose height says nothing
        // about the category until the group is added up.
        const totals = new Map();
        for (const r of rows) {
          totals.set(r[x], (totals.get(r[x]) || 0) + (+r[y] || 0));
        }
        order.sort((a, b) => (totals.get(b) || 0) - (totals.get(a) || 0));
      }
      // Which names end up on which axis. Grouped, the bands are the
      // series and the facets are the category; stacked, the bands are
      // the category and there are no facets. Every margin below is room
      // for a name, so each has to be measured against the names that
      // will actually be there -- sized for the category either way, the
      // left of a grouped horizontal chart reserved seven characters for
      // "Boone" and cut "Emergencies" down to "nergencies".
      const bandValues = grouped ? domain : order;
      const room = (values) =>
        Math.min(220, 16 + 6.6 * Math.max(...values.map((v) => String(v ?? "").length)));
      if (horizontal) {
        if (grouped) {
          fyDomain = order;
          // In the colour order, so a series sits in the same place in
          // every facet.
          yDomain = domain;
          // The facet names sit on the right, and nothing had reserved
          // them any width: "Jackson" arrived as "Jacksc".
          marginRight = room(order);
        } else {
          yDomain = order;
        }
        marginLeft = room(bandValues);
        // A band tall enough to read -- and a group needs one per bar,
        // not one per category.
        const bands = order.length * (grouped ? domain.length : 1);
        height = Math.max(320, Math.min(1600, bands * 22 + 90));
      } else {
        if (grouped) {
          fxDomain = order;
          // The label somebody wrote for the category goes with the
          // category onto the facet axis.
          xScale = { domain, tickSize: 0 };
        } else {
          xScale.domain = order;
        }
        // Upright labels would collide; rotate and reserve the depth.
        // Counted across every facet, because that is how many labels
        // are drawn.
        const ticks = bandValues.length * (grouped ? order.length : 1);
        if (ticks > 8) {
          xScale.tickRotate = -45;
          marginBottom = Math.min(160, 40 + 5.2 * (room(bandValues) - 16) / 6.6);
        }
      }
    }

    const plot = Plot.plot({
      width,
      height,
      marginLeft,
      marginRight,
      marginBottom,
      style: { background: "transparent", color: t.ink,
               fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      color,
      // The value axis carries the percent formatting: x when the bars
      // run horizontally, y when they stand up.
      x: { ...xScale, ...(percentStack && horizontal ? { percent: true } : {}) },
      y: { label: config.ylabel || undefined, tickSize: 0, grid: false,
           ...(percentStack && !horizontal ? { percent: true } : {}),
           ...(yDomain ? { domain: yDomain } : {}) },
      // The facet axes, which only a group of bars uses. Without a domain
      // here the ordering computed above reached nothing, and the label
      // somebody wrote for the category stayed on an axis now showing the
      // series.
      ...(fxDomain
        ? { fx: { domain: fxDomain, label: config.xlabel || undefined,
                  tickSize: 0 } }
        : {}),
      ...(fyDomain
        ? { fy: { domain: fyDomain, label: config.xlabel || undefined,
                  tickSize: 0 } }
        : {}),
      marks,
    });
    el.appendChild(plot);
  }

  // --- taking the data somewhere else --------------------------------------
  //
  // The table view is where somebody looks at the numbers, so it is where
  // they will want them out. Two formats, because the two tools take data
  // two different ways: Flourish uploads a file, Datawrapper pastes into a
  // box, and its box reads tab-separated the way a spreadsheet copies.

  function asDelimited(rows, sep) {
    const cols = Object.keys(rows[0]);
    const cell = (v) => {
      const s = v === null || v === undefined ? "" : String(v);
      // A comma or a quote or a newline inside a value breaks the row it
      // sits in unless it is quoted, and a quote inside a quoted value has
      // to be doubled. Datawrapper and Flourish both read it this way.
      return /["\n\r]|,|\t/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [cols.join(sep)]
      .concat(rows.map((r) => cols.map((c) => cell(r[c])).join(sep)))
      .join("\n");
  }

  function download(text, name, type) {
    const url = URL.createObjectURL(new Blob([text], { type: type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    // Revoked on the next turn: revoking synchronously races the click in
    // some browsers and the file arrives empty.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportBar(el, rows, slug, withCsv) {
    const bar = document.createElement("div");
    bar.className = "dd-export";

    let csv = null;
    if (withCsv) {
      csv = document.createElement("button");
      csv.type = "button";
      csv.textContent = "Download CSV";
      csv.title = "Flourish takes a CSV upload";
      csv.addEventListener("click", () =>
        download(asDelimited(rows, ","), (slug || "data") + ".csv", "text/csv"));
    }

    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Copy for Datawrapper";
    copy.title = "Tab-separated, which is what its paste box reads";
    copy.addEventListener("click", () => {
      const text = asDelimited(rows, "\t");
      const said = (ok) => {
        copy.textContent = ok ? "Copied" : "Press \u2318C";
        setTimeout(() => { copy.textContent = "Copy for Datawrapper"; }, 2000);
      };
      // Clipboard access needs a secure context and a permission that can
      // be refused. A textarea the reader can copy from by hand is the
      // fallback, rather than a button that silently does nothing.
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => said(true), () => said(false));
      } else {
        const box = document.createElement("textarea");
        box.className = "dd-copybox";
        box.value = text;
        bar.appendChild(box);
        box.select();
        said(false);
      }
    });

    const note = document.createElement("span");
    note.className = "dd-export-note";
    note.textContent = rows.length.toLocaleString() + " rows";

    if (csv) bar.append(csv);
    bar.append(copy, note);
    el.appendChild(bar);
  }

  // A feed is not always a list of rows. The map kinds carry
  // {meta, areas, points} -- named lists plus a metadata object -- which
  // the chart path has always understood and this one had not:
  // `Object.keys(rows[0])` read `undefined` and threw, so "View data" was
  // dead on every map ever published, with the failure only in the console.
  function tablesIn(data) {
    if (Array.isArray(data)) return data.length ? [{ name: "", rows: data }] : [];
    if (!data || typeof data !== "object") return [];
    return Object.entries(data)
      .filter(([, v]) => Array.isArray(v) && v.length)
      .map(([name, rows]) => ({ name, rows }));
  }

  // A blank sorts last whichever way the column runs: a newsroom with no
  // owner recorded is not the smallest owner, and sorting descending should
  // not bring every gap to the top.
  function compareCells(x, y, num, dir) {
    const bx = x == null || x === "", by = y == null || y === "";
    if (bx || by) return bx === by ? 0 : bx ? 1 : -1;
    const c = num
      ? +x - +y
      : String(x).localeCompare(String(y), undefined, { numeric: true });
    return c * dir;
  }

  // Which group a row is in at depth `d`: the values of every outer Row down
  // to and including that one. Two owners can each have a newsroom called
  // "News", so a group is its whole path, never its last name alone.
  function groupPath(row, outer, d) {
    return JSON.stringify(outer.slice(0, d + 1).map((c) => row[c] ?? ""));
  }

  // SORTING KEEPS A GROUP TOGETHER. Flat, it is an ordinary sort. Grouped,
  // each group is placed by its BEST member under the column and direction
  // chosen -- the largest when descending, the smallest when ascending --
  // and its members sort inside it. Not by a total: unique bylines do not
  // add up, since a reporter filing for two of an owner's papers is one at
  // each and one person overall. The best member never double-counts.
  //
  // Sorting by a Row column works the same way, and needs no special case:
  // every member of a group shares its outer Rows, so its best is its name.
  function orderRows(rows, col, num, dir, outer) {
    const leaf = (a, b) => compareCells(a[col], b[col], num, dir);
    if (!outer.length) return rows.slice().sort(leaf);
    const best = outer.map(() => new Map());
    outer.forEach((_, d) => {
      for (const r of rows) {
        const key = groupPath(r, outer, d);
        const v = r[col];
        if (!best[d].has(key) || compareCells(v, best[d].get(key), num, dir) < 0) {
          best[d].set(key, v);
        }
      }
    });
    return rows.slice().sort((a, b) => {
      for (let d = 0; d < outer.length; d++) {
        const ka = groupPath(a, outer, d), kb = groupPath(b, outer, d);
        if (ka === kb) continue;
        // Two groups tied on their best still may not interleave.
        return compareCells(best[d].get(ka), best[d].get(kb), num, dir)
          || ka.localeCompare(kb);
      }
      return leaf(a, b);
    });
  }

  // Sorted rows into one entry per outermost group, in the order they
  // arrive. Each line records the shallowest inner group it opens (1 and
  // deeper), or -1 when it opens none, which is what decides whether an
  // inner group's name is said on that line.
  function stackRows(sorted, outer) {
    const groups = [];
    let current = null;
    sorted.forEach((row, i) => {
      const key = groupPath(row, outer, 0);
      if (!current || current.key !== key) {
        current = { key, name: row[outer[0]], lines: [] };
        groups.push(current);
      }
      let opens = -1;
      for (let d = 1; d < outer.length; d++) {
        const prev = current.lines.length ? sorted[i - 1] : null;
        if (!prev || groupPath(row, outer, d) !== groupPath(prev, outer, d)) {
          opens = d;
          break;
        }
      }
      current.lines.push({ row, opens });
    });
    return groups;
  }

  // SORT AND FILTER ON EVERY TABLE, and nesting when it is asked for.
  //
  // `config.rows` names the columns that are Rows, in order, resolved on the
  // server from the pivot's dimensions; the feed cannot say where Rows end
  // and Values begin, and guessing from the values fails on a year. With
  // "Group rows" ticked, every Row but the last is a group, and the first
  // Row is one displayed row each -- see stackRows.
  function oneTable(rows, config) {
    // The first row that is actually an object. A list of bare numbers or
    // strings has no columns to name, and keying off row zero regardless
    // gave `0, 1, 2` as headers.
    const first = rows.find((r) => r && typeof r === "object" && !Array.isArray(r));
    const table = document.createElement("table");
    table.className = "dd-table";
    const tbody = document.createElement("tbody");
    if (!first) {
      for (const row of rows.slice(0, 500)) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.textContent = row ?? "";
        if (isFiniteNumber(row)) td.className = "num";
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      return table;
    }

    const cols = Object.keys(first);
    // A column is a number when every value in it is, so one "n/a" in a
    // column of counts sorts it as text rather than sorting it wrongly.
    const numeric = new Set(cols.filter((c) => rows.some(
      (r) => r?.[c] != null && r[c] !== "") && rows.every(
      (r) => r?.[c] == null || r[c] === "" || isFiniteNumber(r[c]))));
    const named = ((config && config.rows) || []).filter((c) => cols.includes(c));
    const outer = config && config.group_rows ? named.slice(0, -1) : [];
    const allGroups = outer.length
      ? new Set(rows.map((r) => groupPath(r, outer, 0))).size
      : rows.length;

    // Flat opens in the feed's own order, which is the pivot's. Grouped
    // cannot: the feed is largest-first across every group, which would
    // scatter an owner's newsrooms down the page. It opens on the first
    // Value, largest first, which is the same question asked group by group.
    const firstValue = cols.findIndex((c) => !named.includes(c) && numeric.has(c));
    let sortAt = outer.length ? (firstValue >= 0 ? firstValue : 0) : -1;
    let dir = sortAt >= 0 && numeric.has(cols[sortAt]) ? -1 : 1;

    const wrap = document.createElement("div");
    wrap.className = "dd-tableview";
    const bar = document.createElement("div");
    bar.className = "dd-table-bar";
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "Filter rows\u2026";
    search.setAttribute("aria-label", "Filter the rows");
    const count = document.createElement("span");
    count.className = "dd-table-count";
    bar.append(search, count);
    const scroll = document.createElement("div");
    scroll.className = "dd-table-scroll";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    thead.appendChild(headRow);
    table.append(thead, tbody);
    scroll.appendChild(table);
    wrap.append(bar, scroll);

    function paint() {
      const needle = search.value.trim().toLowerCase();
      const found = needle
        ? rows.filter((r) => cols.some(
          (c) => String(r?.[c] ?? "").toLowerCase().includes(needle)))
        : rows;
      const list = sortAt < 0
        ? found
        : orderRows(found, cols[sortAt], numeric.has(cols[sortAt]), dir, outer);

      headRow.replaceChildren();
      cols.forEach((c, k) => {
        const th = document.createElement("th");
        if (numeric.has(c)) th.className = "num";
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = c + (k === sortAt ? (dir === 1 ? " \u25B2" : " \u25BC") : "");
        b.addEventListener("click", () => {
          if (k === sortAt) dir = -dir;
          else { sortAt = k; dir = numeric.has(c) ? -1 : 1; }
          paint();
        });
        if (k === sortAt) th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        th.appendChild(b);
        headRow.appendChild(th);
      });

      tbody.replaceChildren();
      if (!outer.length) {
        const shown = list.slice(0, 500);
        for (const row of shown) {
          const tr = document.createElement("tr");
          for (const c of cols) {
            const value = row?.[c];
            const td = document.createElement("td");
            td.textContent = value ?? "";
            if (numeric.has(c)) td.className = "num";
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
        }
        count.textContent = list.length > shown.length
          ? `Showing ${shown.length.toLocaleString()} of ${list.length.toLocaleString()}`
          : needle ? `${list.length.toLocaleString()} of ${rows.length.toLocaleString()}` : "";
        return;
      }

      // ONE DISPLAYED ROW PER GROUP. A byline filing for seven papers is one
      // row with seven lines in it, not seven rows: the reader is counting
      // bylines, and a byline spread down the page over rows that each look
      // like a record reads as seven of them. Every column below the first
      // Row is a stack of lines, one per leaf, aligned across the columns so
      // a line reads across as one record.
      const groups = stackRows(list, outer);
      const shown = groups.slice(0, 500);
      for (const group of shown) {
        const tr = document.createElement("tr");
        tr.className = "dd-grouped";
        for (const c of cols) {
          const td = document.createElement("td");
          if (c === outer[0]) {
            td.textContent = group.name ?? "";
            tr.appendChild(td);
            continue;
          }
          if (numeric.has(c)) td.className = "num";
          const depth = outer.indexOf(c);
          const stack = document.createElement("div");
          stack.className = "dd-stack";
          for (const line of group.lines) {
            const div = document.createElement("div");
            div.className = "dd-line";
            div.textContent = line.row?.[c] ?? "";
            // An inner group is named on its first line and left unsaid on
            // the lines after it, as the outermost one is by the row itself.
            if (depth > 0 && (line.opens < 0 || depth < line.opens)) {
              div.classList.add("dd-said");
            }
            stack.appendChild(div);
          }
          td.appendChild(stack);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      count.textContent = groups.length > shown.length
        ? `Showing ${shown.length.toLocaleString()} of ${groups.length.toLocaleString()}`
        : needle ? `${groups.length.toLocaleString()} of ${allGroups.toLocaleString()}` : "";
    }

    search.addEventListener("input", paint);
    paint();
    return wrap;
  }

  // Who to credit and who to ask, shown with the numbers rather than on
  // the page around them. A reader who opens the data is the one checking
  // the chart, and this is the answer to "says who?" -- it also travels
  // into the embed, where there is no page of ours to put it on.
  function creditLine(el, credits) {
    if (!credits || !credits.length) return;
    const p = document.createElement("p");
    p.className = "dd-credit";
    credits.forEach((c, i) => {
      if (i) p.appendChild(document.createTextNode(" · "));
      p.appendChild(document.createTextNode(`${c.dataset}: `));
      if (c.contact) {
        const a = document.createElement("a");
        a.href = `mailto:${c.contact}`;
        a.textContent = c.owner || c.contact;
        p.appendChild(a);
      } else {
        p.appendChild(document.createTextNode(c.owner || ""));
      }
    });
    el.appendChild(p);
  }

  // `takeaway` is whatever the page wants at the top of this panel --
  // the download links, and which version they ask for. It belongs here
  // rather than under the chart: it is about the data, and the panel is
  // where somebody has gone looking for the data. Under the chart it was
  // a footer on a figure that already carries its own title and source
  // inside its bounds.
  // `takeaway` is whatever the page wants at the top of this panel --
  // the download links, and which version they ask for. `back` is the
  // control that returns to the chart, which belongs here rather than on
  // the figure it has replaced: a caption under a table is not where
  // somebody looks for the way out of it.
  //
  // Both are moved rather than built, because the page made them and
  // knows what they say. Moving is also what keeps them out of
  // `replaceChildren`'s way when the chart draws again.
  // A REPORT IS NOT A LIST OF ROWS.
  //
  // The byline report is one row per byline with several publications under
  // it, and those publications belong to owners: six Rust Communications
  // titles under one byline are ONE owner, not six. Flattened into a plain
  // table it is six rows repeating the byline; rendered as two lists it says
  // which publications and which owners and never which owner ran which
  // publication.
  //
  // So the query returns long form -- subject, item, group, value -- and this
  // nests it twice: items group under their owner, groups stack under the
  // subject, and both cells emit the same groups in the same order so a group
  // reads across.
  function renderRoster(el, config, rows, opts, t) {
    const subject = config.subject, item = config.item;
    const group = config.item_group, value = config.item_value;
    // THE BOX SAYS THE EXCEPTION. The builder stores a checkbox only when it
    // is ticked -- unticked writes nothing at all -- so a flag meaning "on"
    // can never be turned off: unticking it is indistinguishable from never
    // having seen it. Both of these name the non-default instead. Unticked,
    // the roster totals and offers search, as every roster did before either
    // setting existed.
    const totals = config.roster_no_total !== true;
    const searchable = config.roster_no_search !== true;
    if (!subject || !item) {
      el.textContent = "Pick the column to make one row per, and the column that repeats under it.";
      return;
    }

    // Long form in, nested out. Insertion order is the query's order, which
    // is the author's: a roster sorted by articles arrives that way.
    const bySubject = new Map();
    for (const row of rows) {
      const key = String(row[subject] ?? "");
      if (!key) continue;
      if (!bySubject.has(key)) bySubject.set(key, []);
      bySubject.get(key).push({
        item: String(row[item] ?? ""),
        group: group ? String(row[group] ?? "") : "",
        value: value ? Number(row[value]) || 0 : null,
      });
    }
    const subjects = [...bySubject].map(([name, items]) => ({
      name,
      items,
      total: items.reduce((sum, i) => sum + (i.value || 0), 0),
      groups: new Set(items.map((i) => i.group)).size,
    }));

    const draw = config.roster_draw === "0"
      ? Infinity
      : Math.max(1, parseInt(config.roster_draw, 10) || 400);

    // NO COUNT COLUMNS. A first version carried "how many items" and "how
    // many groups" beside the total, and on a byline report both read 1 and 1
    // on almost every row -- most reporters file at one newsroom. Two columns
    // of ones, and their labels came out pluralised by machine ("Publisher
    // names"). The chips themselves say how many there are, and the eye counts
    // three lines faster than it reads the number 3.
    const cols = [
      { label: labelOf(subject), sort: (s) => s.name },
      // ONLY WHEN THE NUMBER ADDS UP. Articles do: forty here and ten there
      // is fifty. Unique bylines do not -- a reporter filing for two papers
      // is one at each and one person overall, so summing per-publication
      // counts under an owner double-counts exactly the people the report
      // is about. The author says which kind of number this is.
      value && totals ? { label: "Total", num: true, sort: (s) => s.total } : null,
      { label: labelOf(item), pair: "item", sort: (s) => s.items.length },
      group ? { label: labelOf(group), pair: "group", sort: (s) => s.groups } : null,
    ].filter(Boolean);

    // With no total the first column is the subject, and a subject sorts
    // alphabetically rather than largest-first.
    let sortAt = value && totals ? 1 : 0;
    let dir = value && totals ? -1 : 1;

    const wrap = document.createElement("div");
    wrap.className = "dd-roster";
    const bar = document.createElement("div");
    bar.className = "dd-roster-bar";
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = `Search ${labelOf(subject).toLowerCase()}, ${labelOf(item).toLowerCase()}…`;
    search.setAttribute("aria-label", "Search the roster");
    const pick = document.createElement("select");
    pick.setAttribute("aria-label", labelOf(group || item));
    const count = document.createElement("p");
    count.className = "dd-roster-count";
    const scroll = document.createElement("div");
    scroll.className = "dd-roster-scroll";
    const table = document.createElement("table");
    table.className = "dd-roster-table";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    const tbody = document.createElement("tbody");
    thead.append(headRow);
    table.append(thead, tbody);
    scroll.append(table);

    // THE DROPDOWN IS THE DATA. Its options are the distinct groups the query
    // returned, so the SQL informs it by what it selects and there is no
    // second configuration to fall out of step.
    if (group) {
      const seen = [...new Set(rows.map((r) => String(r[group] ?? "")).filter(Boolean))].sort();
      pick.append(new Option(`Any ${labelOf(group).toLowerCase()}`, ""));
      for (const name of seen) pick.append(new Option(name, name));
      bar.append(pick);
    }
    if (searchable) bar.insertBefore(search, bar.firstChild);
    if (bar.childElementCount) wrap.append(bar);
    wrap.append(count, scroll);
    el.replaceChildren(wrap);

    function matching() {
      const needle = search.value.trim().toLowerCase();
      const only = group ? pick.value : "";
      return subjects.filter((s) => {
        if (only && !s.items.some((i) => i.group === only)) return false;
        if (!needle) return true;
        return (s.name + " " + s.items.map((i) => i.item + " " + i.group).join(" "))
          .toLowerCase().includes(needle);
      });
    }

    function grouped(items) {
      const order = [];
      const by = new Map();
      for (const i of items) {
        if (!by.has(i.group)) { by.set(i.group, []); order.push(i.group); }
        by.get(i.group).push(i);
      }
      return order.map((name) => ({ name, items: by.get(name) }));
    }

    function chipsFor(subjectRow, which) {
      const box = document.createElement("div");
      box.className = "dd-roster-chips";
      for (const block of grouped(subjectRow.items)) {
        const cell = document.createElement("div");
        cell.className = "dd-roster-grp";
        // Both columns reserve the block's height, so the group name sits
        // level with the first item it owns rather than drifting.
        cell.style.setProperty("--n", block.items.length);
        if (which === "group") {
          cell.append(chip(block.name));
        } else {
          for (const i of block.items) {
            const c = chip(i.item);
            if (i.value !== null) {
              const n = document.createElement("span");
              n.className = "dd-roster-n";
              n.textContent = " " + i.value.toLocaleString();
              c.append(n);
            }
            c.title = block.name ? `${i.item} — ${block.name}` : i.item;
            cell.append(c);
          }
        }
        box.append(cell);
      }
      return box;
    }

    function chip(text) {
      const span = document.createElement("span");
      span.className = "dd-roster-chip";
      span.textContent = text;
      return span;
    }

    function paint() {
      const found = matching();
      const order = found.slice().sort((a, b) => {
        const col = cols[sortAt];
        const x = col.sort ? col.sort(a) : a.name;
        const y = col.sort ? col.sort(b) : b.name;
        const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
        return c * dir;
      });

      headRow.replaceChildren();
      cols.forEach((col, k) => {
        const th = document.createElement("th");
        if (col.num) th.className = "num";
        if (col.sort) {
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = col.label + (k === sortAt ? (dir === 1 ? " ▲" : " ▼") : "");
          b.addEventListener("click", () => {
            if (k === sortAt) dir = -dir;
            else { sortAt = k; dir = col.num ? -1 : 1; }
            paint();
          });
          th.append(b);
          if (k === sortAt) th.setAttribute("aria-sort", dir === 1 ? "ascending" : "descending");
        } else th.textContent = col.label;
        headRow.append(th);
      });

      tbody.replaceChildren();
      for (const s of order.slice(0, draw)) {
        const tr = document.createElement("tr");
        for (const col of cols) {
          const td = document.createElement("td");
          if (col.pair) td.append(chipsFor(s, col.pair));
          else if (col.num) {
            td.className = "num";
            td.textContent = col.sort(s).toLocaleString();
          } else td.textContent = s.name;
          tr.append(td);
        }
        tbody.append(tr);
      }
      const shown = Math.min(order.length, draw);
      count.textContent = order.length > draw
        ? `Showing ${shown.toLocaleString()} of ${order.length.toLocaleString()} matching · ${subjects.length.toLocaleString()} in all`
        : `${order.length.toLocaleString()} of ${subjects.length.toLocaleString()}`;
    }

    search.addEventListener("input", paint);
    pick.addEventListener("input", paint);
    paint();
    // Every matching row, not the drawn ones: the cap is about first paint.
    exportBar(el, rows, (el.id || "roster").replace(/^dd-chart-/, ""), true);
    creditLine(el, opts && opts.credits);
  }

  //: A column's own name, which is what the reader knows it by. The pivot
  //: emits display labels, so this is usually the label already.
  function labelOf(name) {
    return String(name || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  }

  function renderTable(el, data, credits, takeaway, back, config) {
    const groups = tablesIn(data);
    const slug = el.id.replace(/^dd-chart-/, "");
    if (takeaway) takeaway.hidden = false;
    if (!groups.length) {
      const note = document.createElement("p");
      note.className = "dd-note";
      note.textContent = "This visual has no tabular data to show.";
      el.replaceChildren(panelHead(takeaway, back), note);
      return;
    }
    el.replaceChildren(panelHead(takeaway, back));
    for (const { name, rows } of groups) {
      if (name && groups.length > 1) {
        const heading = document.createElement("h3");
        heading.className = "dd-table-name";
        heading.textContent = name;
        el.appendChild(heading);
      }
      // Above the table, not below it. A control for taking the data is
      // not something to find after scrolling past five hundred rows.
      //
      // One export per list rather than one for the page: a reader who
      // wants the county totals should not have to take the point layer
      // with them to get it. Every row, not the five hundred shown --
      // the cap is about what a page can render.
      //
      // Where the page offers its own downloads it offers the same CSV,
      // from the snapshot file rather than from the rows on screen, and
      // two buttons doing one thing is worse than either. The copy is
      // still ours: no server file is a clipboard.
      exportBar(el, rows, name ? `${slug}-${name}` : slug, !takeaway);
      el.appendChild(oneTable(rows, config));
    }
    creditLine(el, credits);
  }

  // A GEOID says what it is by how long it is. Making this a setting is
  // how a county map comes out blank: the control's default is `states`,
  // 5-digit codes join nothing, and the page says nothing about it.
  const LEVEL_BY_LENGTH = { 2: "states", 5: "counties", 7: "places", 11: "tracts" };

  function levelOfIds(ids) {
    const lengths = new Set(ids.map((id) => String(id).length));
    if (lengths.size === 1) {
      const only = LEVEL_BY_LENGTH[[...lengths][0]];
      if (only) return only;
    }
    // Mixed or unrecognised lengths: the longest wins, so a list of
    // counties with one state in it still draws counties rather than
    // refusing. Nothing recognisable at all falls back to counties, the
    // level almost every list in this corpus is.
    const known = [...lengths].map((n) => LEVEL_BY_LENGTH[n]).filter(Boolean);
    return known.length ? known[known.length - 1] : "counties";
  }

  // A basemap with chosen areas highlighted. No value, no scale, no
  // legend: an area is in the list or it is not.
  //
  // Drawn the way the story map is drawn, because they are the same kind
  // of picture and looked like two different products: the surrounding
  // states FILTERED OUT rather than left for the projection to crop, the
  // same `missing`/`boundary` palette, the same 0.62 aspect. Fitting a
  // projection to the data still draws everything else in the file --
  // Kansas and Illinois arrived around Missouri because they were in the
  // national counties topojson and nothing had excluded them.
  function renderLocator(el, config, rows, opts, t, width) {
    const key = config.area || config.geo_join;
    if (!key) {
      el.textContent = "Pick the column of area codes.";
      return;
    }
    const raw = rows.map((r) => r[key]).filter((v) => v != null && v !== "");
    if (!raw.length) {
      el.textContent = "No area codes in that column.";
      return;
    }
    const level = levelOfIds(raw.map((v) => pad(v, String(v).length)));
    const idLength = GEO_LEVELS[level].idLength;
    const ids = new Set(raw.map((v) => pad(v, idLength)));
    // A NAME, NOT A CODE. Hovering a county said "29019", which is the
    // join key and not an answer to the question somebody is asking by
    // hovering. Built whether or not the labels toggle is on: that toggle
    // decides whether names are DRAWN on the map, and a tooltip is not a
    // label.
    //
    // The data's own name column wins where there is one -- it is the
    // spelling whoever made the file chose. Otherwise the boundary file
    // carries `properties.name` for every county, state and place, which
    // is where "Boone" comes from when the upload is a bare list of codes.
    const labelBy = new Map();
    const nameKey = Object.keys(rows[0] || {}).find(
      (k) => k !== key && typeof rows[0][k] === "string");
    if (nameKey) {
      for (const r of rows) labelBy.set(pad(r[key], idLength), r[nameKey]);
    }
    const nameOf = (f) =>
      labelBy.get(String(f.id))
      || (f.properties && f.properties.name)
      || String(f.id);

    Promise.all([
      boundaries(opts.geoBase, level, [...ids], opts.geoUrls),
      boundaries(opts.geoBase, "states", [...ids], opts.geoUrls),
    ]).then(([areas, states]) => {
      const homeStates = new Set([...ids].map((id) => id.slice(0, 2)));
      const frame = config.locator_frame;
      // What the map is OF. Everything else is dropped rather than drawn
      // and cropped, so no neighbouring state appears half-shown at the
      // edge.
      const inFrame = (f) =>
        frame === "nation" ? true
        : frame === "areas" ? ids.has(String(f.id))
        : homeStates.has(String(f.id).slice(0, 2));

      const base = areas.filter(inFrame);
      const outline = states.filter(
        (f) => frame === "nation" || homeStates.has(String(f.id)));
      const picked = base.filter((f) => ids.has(String(f.id)));
      if (!picked.length) {
        el.textContent =
          "None of those codes matched a " + level.replace(/s$/, "") + ".";
        return;
      }

      const height = Math.round(width * 0.62);
      const marks = [
        // The basemap: every area in the frame, in the same grey an
        // unshaded county gets on a story map.
        Plot.geo(base, { fill: t.missing, stroke: t.boundary, strokeWidth: 0.6 }),
        // The highlights.
        Plot.geo(picked, {
          fill: t.seqHigh, stroke: t.surface, strokeWidth: 0.6,
          title: nameOf, tip: true,
        }),
        // State lines last, over both, so the frame reads as one shape.
        Plot.geo(outline, { fill: "none", stroke: t.boundary, strokeWidth: 1.2 }),
      ];
      if (config.locator_labels) {
        // THE LABEL CARRIES ITS OWN GROUND, AND THAT GROUND IS THE PAGE.
        //
        // A county label sits on TWO grounds at once: the highlight it names,
        // and the basemap wherever the word is wider than the county, which on
        // a state map is most of them. No single ink reads on both, and a halo
        // is a way of not choosing. So the label gets a plate of its own.
        //
        // The plate is `t.surface` -- the page ground -- and NOT the highlight
        // colour. Drawing it in `t.seqHigh` was the first attempt and it is
        // wrong twice over. Over the county it names it vanishes, so the plate
        // does no work at all. And wherever the word overhangs the county, a
        // `seqHigh` rectangle sitting on the basemap READS AS ANOTHER
        // HIGHLIGHTED AREA: the map appears to highlight more counties than
        // the data holds, which is the one thing a locator must not do.
        // Highlight colour means "in the set", and a label is not in the set.
        //
        // On the page ground the ink is just `t.ink`, correct in both themes by
        // construction, with no luminance test to make and no halo.
        //
        // The plate is measured and inserted after layout, because its width is
        // the rendered width of the word and nothing knows that until the text
        // exists.
        marks.push(Plot.text(picked, {
          text: nameOf,
          fontSize: 10,
          fontWeight: 500,
          fill: t.ink,
          x: (f) => d3.geoCentroid(f)[0], y: (f) => d3.geoCentroid(f)[1],
        }));
      }
      const figure = Plot.plot({
        width, height,
        projection: {
          type: "albers-usa",
          domain: { type: "FeatureCollection", features: base },
        },
        marks,
        style: { background: "transparent", color: t.ink },
      });
      // IN THE DOCUMENT FIRST. `getBBox` on a detached node reports nothing,
      // so measuring before the figure is mounted silently plates nothing.
      el.replaceChildren(figure);
      if (config.locator_labels) plateLabels(figure, new Set(picked.map(nameOf)), t);
    }).catch((err) => { el.textContent = String(err.message || err); });
  }

  //: The plate behind each locator label: a rounded rect in the PAGE SURFACE,
  //: sized to the word it sits under, inserted behind it.
  //:
  //: Never the highlight colour. A plate in `t.seqHigh` is invisible over the
  //: county it names, and over the basemap it reads as one more highlighted
  //: area -- the map then claims counties the data does not.
  //:
  //: Measured rather than guessed -- `getBBox` is the only thing that knows how
  //: wide "Fredericktown" came out in the reader's own font. A locator draws no
  //: axes, so every `text` in the figure is a label, but they are matched
  //: against the names anyway rather than assumed.
  const PLATE_PAD_X = 3.5;
  const PLATE_PAD_Y = 1.5;

  function plateLabels(figure, names, t) {
    for (const node of figure.querySelectorAll("text")) {
      if (!names.has(node.textContent)) continue;
      let box;
      try { box = node.getBBox(); } catch { continue; }
      if (!box || !box.width) continue;
      const plate = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      // Plot positions each label with its OWN `transform`, so `getBBox` comes
      // back in that label's local space -- centred on the origin, not on the
      // county. A sibling rect without the transform lands at the figure's
      // corner, which is where all thirteen of them stacked up.
      const placement = node.getAttribute("transform");
      if (placement) plate.setAttribute("transform", placement);
      plate.setAttribute("x", box.x - PLATE_PAD_X);
      plate.setAttribute("y", box.y - PLATE_PAD_Y);
      plate.setAttribute("width", box.width + PLATE_PAD_X * 2);
      plate.setAttribute("height", box.height + PLATE_PAD_Y * 2);
      plate.setAttribute("rx", 2);
      plate.setAttribute("fill", t.surface);
      // A hairline edge, so the plate separates from the basemap grey without
      // competing with the county lines it crosses.
      plate.setAttribute("stroke", t.boundary);
      plate.setAttribute("stroke-width", 0.5);
      node.parentNode.insertBefore(plate, node);
    }
  }

  function renderMap(el, config, rows, opts, t, width) {
    const Plot = global.Plot;
    const level = GEO_LEVELS[config.geo_level] ? config.geo_level : "states";
    const idLength = GEO_LEVELS[level].idLength;
    const joinIds = config.geo_join
      ? rows.map((r) => pad(r[config.geo_join], idLength))
      : [];
    boundaries(opts.geoBase, level, joinIds, opts.geoUrls).then((features) => {
      const marks = [];
      let colorOpt;

      if (config.kind === "choropleth") {
        if (!config.geo_join || !config.geo_value) {
          el.textContent = "Pick the FIPS/GEOID column and the value column.";
          return;
        }
        rows = coerce(rows.slice(), config.geo_value);
        const byId = new Map(
          rows.map((r) => [pad(r[config.geo_join], idLength), r]));
        const joined = features.filter((f) => byId.has(f.id));
        const value = (f) => {
          const row = byId.get(f.id);
          return row ? row[config.geo_value] : null;
        };
        colorOpt = {
          type: "quantize",
          n: 7,
          tickFormat: ".3~g",
          range: config.geo_palette === "diverging"
            ? divergingRamp(t.divLow, t.divMid, t.divHigh, 7)
            : quantizeRamp(t.seqLow, t.seqHigh, 7),
          legend: true,
          label: config.geo_value,
        };
        marks.push(Plot.geo(features, {
          fill: (f) => (byId.has(f.id) ? value(f) : undefined),
          stroke: t.boundary, strokeWidth: 0.5,
        }));
        marks.push(Plot.geo(joined, {
          fill: value, stroke: t.surface, strokeWidth: 0.5, tip: true,
          channels: {
            name: (f) => f.properties.name || f.properties.NAME || f.id,
          },
        }));
        // Tract- and place-scale maps are unreadable at national extent:
        // they always fit to the joined features.
        var domainFeatures =
          (config.geo_fit || GEO_LEVELS[level].perState) && joined.length
            ? joined
            : null;
      } else {
        marks.push(Plot.geo(features, {
          fill: t.missing, stroke: t.boundary, strokeWidth: 0.5,
        }));
      }

      // Point layer: the points kind, or lat/lon on top of a choropleth.
      if (config.lat && config.lon) {
        rows = coerce(coerce(rows.slice(), config.lat), config.lon);
        const dot = {
          x: config.lon, y: config.lat, r: config.size || 4,
          fill: t.series[0], stroke: t.surface, strokeWidth: 1,
          fillOpacity: 0.85, tip: true,
        };
        if (config.size) rows = coerce(rows, config.size);
        if (config.label) dot.channels = { name: config.label };
        marks.push(Plot.dot(rows, dot));
      }

      const projection = domainFeatures
        ? { type: "albers", domain:
            { type: "FeatureCollection", features: domainFeatures } }
        : "albers-usa";
      const plot = Plot.plot({
        width,
        height: Math.round(width * 0.62),
        projection,
        style: { background: "transparent", color: t.ink,
                 fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
        color: colorOpt,
        marks,
      });
      el.replaceChildren(plot);
    }).catch((err) => {
      el.textContent = /GEOID/.test(String(err))
        ? String(err.message || err)
        : "Boundary data unavailable for this level" +
          " (infra/fetch_boundaries.sh adds states).";
    });
  }

  // A one-hue quantized ramp between two endpoints, in sRGB-linear steps.
  // EVENLY SPACED IN PERCEIVED LIGHTNESS, not in raw sRGB. Stepping the
  // hex channels linearly does not step the eye linearly: the middle of
  // a light-to-dark ramp moves far faster than its ends, so with ten
  // bands the palest three were nearly one colour while the darkest were
  // wastefully far apart. Measured on the four themes, the worst
  // adjacent pair at ten bands was 0.024 of relative luminance; even
  // spacing makes every pair 0.071, which is roughly three times the
  // separation and better than FOUR bands managed before.
  //
  // Every colour still sits on the same straight line between the two
  // endpoints, so the ramp stays one hue family -- only where along that
  // line each step lands has changed.
  function quantizeRamp(low, high, n) {
    const parse = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [a, b] = [parse(low), parse(high)];
    const at = (k) => a.map((v, j) => v + (b[j] - v) * k);
    const lum = (rgb) => {
      const lin = rgb.map((v) => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
    };
    const [lo, hi] = [lum(at(0)), lum(at(1))];
    // The position on the line whose luminance is `target`. Bisection
    // rather than an inverse: luminance along the line is monotonic but
    // has no closed form worth writing, and twenty halvings put it well
    // inside a rounding error of one 8-bit channel.
    const solve = (target) => {
      let lowK = 0;
      let highK = 1;
      for (let i = 0; i < 20; i += 1) {
        const mid = (lowK + highK) / 2;
        const here = lum(at(mid));
        if (hi > lo ? here < target : here > target) lowK = mid;
        else highK = mid;
      }
      return (lowK + highK) / 2;
    };
    return Array.from({ length: n }, (_, i) => {
      const k = n === 1 ? 0 : solve(lo + ((hi - lo) * i) / (n - 1));
      const rgb = at(k).map((v) => Math.round(v));
      return "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
    });
  }

  // A pointer-driven tooltip: hover on desktop, tap on touch, click to
  // pin so a value stays readable. One per chart container.
  function tooltip(el) {
    if (getComputedStyle(el).position === "static") el.style.position = "relative";
    let node = el.querySelector(".dd-tip");
    if (!node) {
      node = document.createElement("div");
      node.className = "dd-tip";
      node.hidden = true;
      el.appendChild(node);
    }
    let pinned = false;
    const place = (event) => {
      const box = el.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;
      node.style.left =
        Math.max(4, Math.min(x + 14, box.width - node.offsetWidth - 8)) + "px";
      node.style.top = Math.max(4, y - node.offsetHeight - 12) + "px";
    };
    return {
      show(html, event) {
        if (pinned) return;
        node.innerHTML = html;
        node.hidden = false;
        place(event);
      },
      move(event) { if (!pinned) place(event); },
      hide() { if (!pinned) node.hidden = true; },
      pin(html, event) {
        pinned = false;
        this.show(html, event);
        pinned = true;
        node.classList.add("pinned");
      },
      unpin() {
        pinned = false;
        node.classList.remove("pinned");
        node.hidden = true;
      },
      isPinned() { return pinned; },
      node,
    };
  }

  const fmt = (v) =>
    typeof v === "number"
      ? (Number.isInteger(v)
          ? v.toLocaleString()
          : v.toLocaleString(undefined, { maximumFractionDigits: 4 }))
      : String(v == null ? "\u2014" : v);

  function tipRow(label, value) {
    return `<span class="dd-tip-k">${label}</span>` +
           `<span class="dd-tip-v">${fmt(value)}</span>`;
  }

  // Hover/tap/pin plus sibling dimming for a d3 selection. The tooltip
  // node is kept out of the way of replaceChildren by callers.
  function interactive(sel, tip, html, opts) {
    const group = (opts && opts.group) || null;
    const related = (opts && opts.related) || null;
    const undim = () => group && group.style("opacity", null);
    const isolate = (target) => {
      if (group && related) {
        group.style("opacity", (other) => (related(target, other) ? 1 : 0.15));
      }
    };
    sel
      .style("cursor", "pointer")
      .on("pointerenter pointermove", function (event, d) {
        tip.show(html(d, this), event);
        tip.move(event);
        if (!tip.isPinned()) isolate(d);
      })
      .on("pointerleave", () => {
        tip.hide();
        if (!tip.isPinned()) undim();
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        if (tip.isPinned()) {
          tip.unpin();
          undim();
        } else {
          tip.pin(html(d, this), event);
          isolate(d);
        }
      });
  }

  // Per-slice label ink: black or white by the fill's relative luminance.
  function inkOn(hex) {
    const [r, g, b] = [1, 3, 5].map((i) =>
      parseInt(hex.slice(i, i + 2), 16) / 255);
    const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b) > 0.4
      ? "#0b0b0b" : "#ffffff";
  }

  function htmlLegend(el, domain, colors) {
    const div = document.createElement("div");
    div.className = "dd-legend";
    domain.forEach((d, i) => {
      const item = document.createElement("span");
      const swatch = document.createElement("span");
      swatch.className = "dd-swatch";
      swatch.style.background = colors[i];
      item.append(swatch, String(d));
      div.appendChild(item);
    });
    el.appendChild(div);
  }

  // Slot colors for a name list, gray for the fold bucket.
  function slotColors(domain, t) {
    return domain.map((d, i) =>
      d === "Other" ? t.other : t.series[i % t.series.length]);
  }

  function svgRoot(width, height, t) {
    return d3.create("svg")
      .attr("width", width).attr("height", height)
      .attr("viewBox", [-width / 2, -height / 2, width, height])
      .attr("style",
        'max-width:100%;height:auto;font-family:system-ui,-apple-system,' +
        '"Segoe UI",sans-serif;font-size:12px;color:' + t.ink);
  }

  // Parts of a whole. Aggregates y by x, folds past five slices, labels
  // What a stacked segment is, and what it is of.
  //
  // A stack says "part of a whole" and the hover said only one of the
  // two. On a percent stack it was worse than incomplete: `expand`
  // replaces the value with a fraction of its column, so the tip read
  // "Articles (%) 42" and the 420 articles behind it were nowhere on the
  // chart -- no way to tell a small share of a large county from a large
  // share of a small one.
  //
  // Both in one line, which is what the donut has always done: its slices
  // report the value and the share together.
  //
  // The composed line replaces the value channel rather than sitting
  // beside it. Two lines, one of them a bare fraction of the other, read
  // as two numbers that disagree.
  function shareInTip(enc, rows, keyColumn, valueColumn, axis) {
    const whole = new Map();
    for (const row of rows) {
      const key = row[keyColumn];
      whole.set(key, (whole.get(key) || 0) + (+row[valueColumn] || 0));
    }
    enc.channels = {
      ...(enc.channels || {}),
      [valueColumn]: (row) => {
        const value = +row[valueColumn] || 0;
        const of = whole.get(row[keyColumn]) || 0;
        const share = of ? (100 * value) / of : 0;
        return `${value.toLocaleString()} (${share.toFixed(1)}%)`;
      },
    };
    enc.tip = { ...(enc.tip || {}), format: { [axis]: false } };
    return enc;
  }

  // the slices that have room and legends the rest; total in the hole.
  function renderDonut(el, config, rows, t, width) {
    const d3 = global.d3;
    const x = config.x, y = config.y;
    if (!x || !y) { el.textContent = "Pick the category and value columns."; return; }
    rows = coerce(rows.slice(), y);
    let entries = [...d3.rollup(
      rows, (v) => d3.sum(v, (r) => +r[y] || 0), (r) => r[x])];
    entries.sort((a, b) => b[1] - a[1]);
    if (entries.length > 5) {
      entries = [...entries.slice(0, 5),
        ["Other", d3.sum(entries.slice(5), (e) => e[1])]];
    }
    const domain = entries.map((e) => e[0]);
    const colors = slotColors(domain, t);
    const total = d3.sum(entries, (e) => e[1]);
    const R = Math.min(width, 440) / 2 - 8;
    const svg = svgRoot(width, 2 * R + 16, t);
    const arcs = d3.pie().value((e) => e[1]).sort(null).padAngle(0.01)(entries);
    const shape = d3.arc().innerRadius(R * 0.62).outerRadius(R);
    const slices = svg.append("g").selectAll("path").data(arcs).join("path")
      .attr("d", shape)
      .attr("fill", (d, i) => colors[i])
      .attr("stroke", t.surface).attr("stroke-width", 2);
    const labelAt = d3.arc().innerRadius(R * 0.81).outerRadius(R * 0.81);
    svg.append("g").selectAll("text")
      .data(arcs.filter((d) => d.endAngle - d.startAngle > 0.35)).join("text")
      .attr("transform", (d) => `translate(${labelAt.centroid(d)})`)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", (d) => inkOn(colors[arcs.indexOf(d)]))
      .text((d) => `${(100 * d.data[1] / total).toFixed(0)}%`);
    svg.append("text").attr("text-anchor", "middle").attr("dy", "-0.2em")
      .attr("fill", "currentColor").attr("font-size", 22)
      .text(total.toLocaleString());
    svg.append("text").attr("text-anchor", "middle").attr("dy", "1.4em")
      .attr("fill", t.muted).text(config.ylabel || y);
    el.replaceChildren();
    htmlLegend(el, domain, colors);
    el.appendChild(svg.node());
    const tip = tooltip(el);
    interactive(slices, tip, (d) =>
      `<strong>${d.data[0]}</strong>` +
      tipRow(config.ylabel || "value", d.data[1]) +
      tipRow("share", (100 * d.data[1] / total).toFixed(1) + "%"),
      { group: slices, related: (target, other) => target === other });
  }

  // Shared: fold a from/to edge list to at most eight named groups.
  function edgeGroups(rows, from, to, fixed, pin) {
    const order = [];
    for (const r of rows) {
      for (const v of [r[from], r[to]]) {
        if (v != null && v !== "" && !order.includes(v)) order.push(v);
      }
    }
    // A pinned taxonomy is a closed vocabulary somebody chose on purpose,
    // so every one of its categories keeps its own arc and its own place
    // in the ring. Folding applies to open-ended data, where an eight-hue
    // palette is the limit; applied here it invented an "Other" that is
    // not a CIN need and quietly merged two that are.
    //
    // A value the taxonomy does not list still gets its own arc rather
    // than being swept up -- if the data disagrees with the vocabulary,
    // that is worth seeing, not hiding.
    if (fixed && fixed.length) {
      const known = new Set(fixed);
      const present = new Set(order);
      return {
        names: [
          ...fixed.filter((n) => present.has(n)),
          ...order.filter((n) => !known.has(n)),
        ],
        fold: (v) => v,
      };
    }
    if (order.length <= 8) return { names: order, fold: (v) => v };
    // The subject is kept whatever its position. Folding is by order of
    // APPEARANCE, which is arbitrary relative to what a chart is about: a
    // study of three counties lost one of them to "Other" because its
    // largest flow happened to sort ninth.
    const pinned = (pin || []).filter((n) => order.includes(n));
    const rest = order.filter((n) => !pinned.includes(n));
    const room = Math.max(0, 8 - pinned.length);
    const kept = [...pinned, ...rest.slice(0, room)];
    const keep = new Set(kept);
    return {
      names: [...kept, "Other"],
      fold: (v) => (keep.has(v) ? v : "Other"),
    };
  }

  // Flows between groups. Identity is carried by the labels on every
  // group arc — color is redundant there, so the eight-slot order holds.
  // A small stable hash, so the ids a chart mints for its own defs do not
  // collide with another chart's on the same page.
  //: One per flow map drawn, so two on a page cannot mint the same
  //: marker id. Module scope rather than a property of the export,
  //: which is not assigned until the end of this file.
  let arrowSeq = 0;

  function hashOf(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return h;
  }

  // Trim each label to the room it was given, measuring rather than
  // estimating. A character-width guess was what let the text run past
  // the end of its path, where SVG cuts it without a mark -- the reader
  // cannot tell a truncated name from a short one.
  //
  // Must run with the node in the document: getComputedTextLength on a
  // detached element returns zero, and everything would "fit".
  function fitLabels(svgNode) {
    svgNode.querySelectorAll("text[data-room]").forEach((text) => {
      const room = parseFloat(text.getAttribute("data-room"));
      const path = text.querySelector("textPath");
      const full = path.textContent;
      if (text.getComputedTextLength() <= room) return;
      // Nothing legible fits. Better nothing than "E…" -- the tooltip and
      // the table still carry the name.
      let n = full.length;
      while (n > 3) {
        n -= 1;
        path.textContent = full.slice(0, n).trimEnd() + "\u2026";
        if (text.getComputedTextLength() <= room) return;
      }
      text.remove();
    });
  }

  // A sankey: how much flows from each of one thing to each of another.
  //
  // Two vocabularies, which is what separates it from the chord beneath.
  // A chord folds one vocabulary against itself and its ring makes both
  // ends the same set; a sankey has owners down the left and the
  // newsrooms they own down the right, and the same name on both sides
  // would be two different nodes.
  //
  // Colour follows the left column, the one the flows come from: that is
  // the thing the chart is comparing -- who owns how much of this -- and
  // there are few enough of them to tell apart. Colouring by the right
  // column instead gives a hue per newsroom, which past about nine is a
  // key nobody can read.
  function renderSankey(el, config, rows, t, width) {
    const d3 = global.d3;
    const { from, to, value } = config;
    if (!from || !to || !value) {
      el.textContent = "Pick the from, to, and value columns."; return;
    }
    if (typeof d3.sankey !== "function") {
      el.textContent = "The sankey layout did not load.";
      return;
    }
    rows = coerce(rows.slice(), value);
    const { nodes, links, lefts, sides, folded } = sankeyGraph(rows, from, to, value);
    if (!links.length) { el.textContent = "No flows to draw."; return; }

    // Never cycled. Past the palette a hue would be reused for a second
    // owner, and two owners in one colour is a chart that says they are
    // the same one. The tail takes the neutral instead and keeps its own
    // node and its own label, because a long tail of small owners is
    // most of what this chart is about -- folding them into one "Other"
    // would answer the question by deleting it.
    const hue = new Map(lefts.map((at, i) =>
      [at, i < t.series.length ? t.series[i] : t.other]));
    const greyed = Math.max(0, lefts.length - t.series.length);

    // Room for the longest label on each side, measured rather than
    // guessed: the names here are newsroom names and owner names, and
    // guessing clipped "Southeast Missourian" in half.
    // Room per node rather than per link: a node is what carries a
    // label, and a label is what has to be readable. Both sides are
    // capped, so this cannot run away.
    const rows_deep = Math.max(sides[0].size, sides[1].size);
    const height = Math.max(320, Math.min(26 * rows_deep + 40, 760));
    const svg = d3.create("svg")
      .attr("viewBox", [0, 0, width, height])
      .attr("width", width).attr("height", height)
      .attr("font-family", 'system-ui, -apple-system, "Segoe UI", sans-serif')
      .attr("font-size", 12);
    // In the document before anything is measured. `getComputedTextLength`
    // returns 0 on a detached element, and a detached svg is what
    // `d3.create` makes -- so every measurement here was zero, the room
    // for labels came out as the 10px of padding, and 26 of them ran off
    // the edges of the ownership map while the trimming decided they all
    // fitted.
    el.replaceChildren(svg.node());
    const measure = svg.append("g").attr("visibility", "hidden");
    function widest(side) {
      let most = 0;
      for (const name of sides[side].keys()) {
        const node = measure.append("text").text(name).node();
        most = Math.max(most, node.getComputedTextLength
          ? node.getComputedTextLength() : name.length * 6.6);
      }
      return most;
    }

    // Names get the room they need, and the flows keep a floor.
    //
    // Each side used to be capped at 28% of the width whether or not
    // anything needed capping, so "The Kansas City Star" and "St. Louis
    // city, MO" were trimmed while the middle of the diagram had room to
    // spare. A cap that binds when nothing is short is not a cap, it is
    // the layout.
    //
    // Only where the two sides together would leave the flows less than
    // the floor do they give way, and then both by the same proportion:
    // trimming one side to spare the other would say the names on that
    // side matter less.
    let leftRoom = widest(0) + 10;
    let rightRoom = widest(1) + 10;
    measure.remove();

    // What the bands need to still read as bands. Below this the diagram
    // is two columns of text with a smear between them.
    const floor = Math.max(120, width * 0.3);
    const spare = Math.max(60, width - floor);
    if (leftRoom + rightRoom > spare) {
      const share = spare / (leftRoom + rightRoom);
      leftRoom = Math.floor(leftRoom * share);
      rightRoom = Math.floor(rightRoom * share);
    }

    const layout = d3.sankey()
      .nodeWidth(10)
      .nodePadding(Math.max(4, Math.min(14, 220 / Math.max(nodes.length, 1))))
      .extent([[leftRoom, 10], [Math.max(leftRoom + 60, width - rightRoom), height - 10]]);
    const graph = layout({
      nodes: nodes.map((n) => ({ ...n })),
      links: links.map((l) => ({ ...l })),
    });

    // Links under nodes, so a band never covers the block it arrives at.
    const bands = svg.append("g")
      .attr("fill", "none")
      .selectAll("path")
      .data(graph.links)
      .join("path")
        .attr("d", d3.sankeyLinkHorizontal())
        .attr("stroke", (d) => hue.get(d.source.index) || t.other)
        .attr("stroke-width", (d) => Math.max(1, d.width))
        .attr("stroke-opacity", 0.45);

    const blocks = svg.append("g")
      .selectAll("rect")
      .data(graph.nodes)
      .join("rect")
        .attr("x", (d) => d.x0)
        .attr("y", (d) => d.y0)
        .attr("width", (d) => d.x1 - d.x0)
        .attr("height", (d) => Math.max(1, d.y1 - d.y0))
        .attr("fill", (d) => d.side === 0 ? (hue.get(d.index) || t.other) : t.muted);

    // Outside the diagram on both sides, so a label never sits on a band.
    //
    // Trimmed to the room measured for it. The room is capped at a share
    // of the width -- a name may be longer than any sane column -- and
    // capping the room without shortening the text is what ran
    // "www.fourstateshomepage.com" 171 pixels off the right edge and cut
    // 26 labels on the ownership map. The whole name stays in the
    // tooltip, so nothing is lost by trimming it.
    const labels = svg.append("g")
      .attr("fill", t.ink)
      .selectAll("text")
      .data(graph.nodes)
      .join("text")
        .attr("x", (d) => d.side === 0 ? d.x0 - 6 : d.x1 + 6)
        .attr("y", (d) => (d.y0 + d.y1) / 2)
        .attr("dy", "0.35em")
        .attr("text-anchor", (d) => d.side === 0 ? "end" : "start")
        .each(function (d) {
          trimTo(this, d.name, (d.side === 0 ? leftRoom : rightRoom) - 8);
        });

    // The same hover layer every other d3 kind here carries. The sankey
    // was drawn last and had only the browser's own <title>: a tooltip
    // that waits a second, cannot be styled, and never appears at all on
    // a touch screen. It also could not do the thing this chart most
    // needs, which is to separate one flow from the eighty crossing it.
    //
    // A band isolates itself. A block isolates every band touching it,
    // which is how you read "everything this publisher covers" off a
    // diagram where its bands run under six others. Labels answer for
    // their own block, because the label is the part small enough to be
    // trimmed and so the part a reader points at to find out what it said.
    const tip = tooltip(el);
    const touching = (node, band) => band.source === node || band.target === node;
    const blockTip = (d) =>
      `<strong>${d.name}</strong>` +
      tipRow(d.side === 0 ? "flows out" : "flows in", d.value);

    interactive(bands, tip, (d) =>
      `<strong>${d.source.name} \u2192 ${d.target.name}</strong>` +
      tipRow(value, d.value),
      { group: bands, related: (target, other) => target === other });
    interactive(blocks, tip, blockTip,
      { group: bands, related: touching });
    interactive(labels, tip, blockTip,
      { group: bands, related: touching });

    // What was folded, and into what. A chart of the top twelve that
    // looks like a chart of everything is the one thing a cap must not
    // do quietly.
    if (folded && (folded.left || folded.right)) {
      const said = [];
      if (folded.left) said.push(folded.left + " on the left");
      if (folded.right) said.push(folded.right + " on the right");
      const note = document.createElement("p");
      note.className = "dd-note";
      note.textContent =
        said.join(" and ") + " are gathered into \u201c" + REST +
        "\u201d, which is drawn at its full size. Narrow the values on the " +
        "fields step to choose which are shown.";
      el.appendChild(note);
    }
    if (greyed) {
      const note = document.createElement("p");
      note.className = "dd-note";
      note.textContent =
        "The " + t.series.length + " largest are coloured; the other " +
        greyed + (greyed === 1 ? " is grey" : " are grey") +
        " and told apart by their labels.";
      el.appendChild(note);
    }
  }

  // The panel's own top: what the page put there on the left, the way
  // back on the right. One row rather than two stacked, because the way
  // back is a control and the takeaway is prose, and a control below
  // three lines of prose is one somebody has to hunt for.
  function panelHead(takeaway, back) {
    const head = document.createElement("div");
    head.className = "dd-panel-head";
    const left = document.createElement("div");
    left.className = "dd-panel-lead";
    if (takeaway) left.appendChild(takeaway);
    head.appendChild(left);
    if (back) head.appendChild(back);
    return head;
  }

  // Put `text` in `node`, shortened with an ellipsis until it fits
  // `room`. Measured rather than counted: "www.stltoday.com" and
  // "Webster Groves" are the same number of characters and not the same
  // width, and a character budget cuts one of them early and the other
  // late.
  function trimTo(node, text, room) {
    node.textContent = text;
    if (!node.getComputedTextLength || room <= 0) return;
    if (node.getComputedTextLength() <= room) return;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      node.textContent = text.slice(0, mid) + "\u2026";
      if (node.getComputedTextLength() <= room) lo = mid; else hi = mid - 1;
    }
    node.textContent = lo > 0 ? text.slice(0, lo) + "\u2026" : "\u2026";
  }

  //: What a blank name is called. A newsroom with no owner recorded is
  //: not owned by nobody, and a node labelled "" says it is.
  const NOT_RECORDED = "Not recorded";

  // How many nodes a side can carry and still be read. A sankey of
  // cities against publishers is 111 against 179 -- a wall of labels in
  // 4pt type, which is not a chart of anything. Twelve a side is about
  // what fits at a readable size, and on that corpus the top twelve
  // cities are 69% of the articles.
  //
  // The rest are not dropped. Each side folds its tail into one node, so
  // the total still adds up and a reader can see how much is in it --
  // dropping them would silently redraw the question as "the top twelve"
  // while still looking like a chart of everything.
  const CAP_SIDE = 12;
  const REST = "Everything else";

  // The graph a sankey draws, without drawing it. Separated because this
  // is the part with decisions in it -- two vocabularies, summing, the
  // colour order, what is too much to draw -- and the drawing is d3
  // doing what d3 does.
  function sankeyGraph(rows, from, to, value, cap) {
    const room = cap === undefined ? CAP_SIDE : cap;
    // What each name is worth on its own side, before anything folds.
    const weigh = (key) => {
      const total = new Map();
      for (const r of rows) {
        const name = String(r[key] ?? "").trim() || NOT_RECORDED;
        total.set(name, (total.get(name) || 0) + (+r[value] || 0));
      }
      return total;
    };
    const keep = (key) => {
      const ranked = [...weigh(key)].sort((a, b) => b[1] - a[1]);
      return {
        names: new Set(ranked.slice(0, room).map(([name]) => name)),
        folded: Math.max(0, ranked.length - room),
      };
    };
    const left = keep(from), right = keep(to);
    const fold = (side, name) => {
      const clean = String(name ?? "").trim() || NOT_RECORDED;
      return side.names.has(clean) ? clean : REST;
    };
    if (left.folded || right.folded) {
      rows = rows.map((r) => ({
        ...r,
        [from]: fold(left, r[from]),
        [to]: fold(right, r[to]),
      }));
    }
    const graph = _sankeyGraph(rows, from, to, value);
    graph.folded = { left: left.folded, right: right.folded };
    return graph;
  }

  function _sankeyGraph(rows, from, to, value) {
    // One node per name per side. A name can appear on both, and an
    // owner that is also a newsroom is two nodes with a link between
    // them rather than one node with a loop.
    const sides = [new Map(), new Map()];
    const nodes = [];
    function nodeAt(side, name) {
      const key = String(name ?? "").trim() || NOT_RECORDED;
      if (!sides[side].has(key)) {
        sides[side].set(key, nodes.length);
        nodes.push({ name: key, side });
      }
      return sides[side].get(key);
    }

    // Summed, because a pivot hands back one row per pair and a file may
    // hand back several. Two rows for the same pair are one flow, not
    // two bands stacked at the same place.
    const flows = new Map();
    for (const r of rows) {
      const amount = +r[value] || 0;
      if (amount <= 0) continue;
      const a = nodeAt(0, r[from]), b = nodeAt(1, r[to]);
      const key = a + ":" + b;
      flows.set(key, (flows.get(key) || 0) + amount);
    }
    const links = [...flows].map(([key, amount]) => {
      const [a, b] = key.split(":").map(Number);
      return { source: a, target: b, value: amount };
    });

    // The left column, biggest first, is the colour order -- so the
    // largest gets the first hue and the ordering is a fact about the
    // data rather than about the order the rows arrived in.
    const weight = new Map();
    for (const link of links) {
      weight.set(link.source, (weight.get(link.source) || 0) + link.value);
    }
    const lefts = [...sides[0].values()].sort(
      (a, b) => (weight.get(b) || 0) - (weight.get(a) || 0));
    return { nodes, links, lefts, sides };
  }


  // A flow map: counties drawn, and an arc between the two each row
  // names.
  //
  // A chord shows that a system exists; only a map shows WHERE. The
  // finding these were built for is that Audrain commutes toward
  // Columbia and Osage toward Jefferson City -- two hubs, forty miles
  // apart -- which a ring of ribbons cannot express because it has no
  // geography in it.
  //
  // Rows carry the county each end IS (`from_geo`, `to_geo`) as well as
  // what it is called. Names alone cannot land an arc on a shape: eight
  // states have a Boone County.
  function renderFlowMap(el, config, rows, opts, t, width) {
    const d3 = global.d3;
    const from = config.from, to = config.to, value = config.value;
    const fromGeo = config.from_geo || "from_fips";
    const toGeo = config.to_geo || "to_fips";
    if (!from || !to || !value) {
      el.textContent = "Pick the from, to, and value columns."; return;
    }
    rows = coerce(rows.slice(), value).filter(
      (r) => r[fromGeo] && r[toGeo] && +r[value] > 0);
    if (!rows.length) {
      el.textContent = "No flows with a county at both ends."; return;
    }

    const ids = [];
    for (const r of rows) ids.push(String(r[fromGeo]), String(r[toGeo]));

    boundaries(opts.geoBase, "counties", ids, opts.geoUrls).then((features) => {
      const wanted = new Set(ids.map(String));
      const focus = String(config.focus || "").trim();
      const chosen = Array.isArray(config.frame) ? config.frame.map(String) : [];
      let shown;
      if (chosen.length) {
        const keep = new Set(chosen);
        shown = features.filter((f) => keep.has(String(f.id)));
      } else if (/^\d{5}$/.test(focus)) {
        shown = features.filter((f) => String(f.id).slice(0, 2) === focus.slice(0, 2));
      } else if (/^\d{2}$/.test(focus)) {
        shown = features.filter((f) => String(f.id).slice(0, 2) === focus);
      } else {
        shown = features.filter((f) => wanted.has(String(f.id)));
      }
      if (!shown.length) shown = features.filter((f) => wanted.has(String(f.id)));
      if (!shown.length) {
        const sample = String(rows[0][fromGeo] || "");
        el.textContent = /^\d{5}$/.test(sample)
          ? "Those county codes are not in the basemap."
          : `The county columns hold "${sample}", which is a name rather `
            + "than a five-digit county code. Point From county and To "
            + "county at columns of FIPS codes.";
        return;
      }

      // Height from the SHAPE. Counties in one corner of a state are
      // tall and narrow, and a fixed ratio left half the canvas empty
      // while squeezing the part with the data in it.
      const fitted = d3.geoAlbersUsa().fitWidth(
        width, { type: "FeatureCollection", features: shown });
      const bounds = d3.geoPath(fitted).bounds(
        { type: "FeatureCollection", features: shown });
      const height = Math.round(Math.min(
        Math.max(bounds[1][1] - bounds[0][1], width * 0.45), width * 1.4));
      // Inset: arcs bow outside the counties they join, and a projection
      // fitted to the counties alone clips them.
      const pad = Math.round(Math.min(width, height) * 0.09);
      const projection = d3.geoAlbersUsa().fitExtent(
        [[pad, pad], [width - pad, height - pad]],
        { type: "FeatureCollection", features: shown });
      const path = d3.geoPath(projection);
      // Centroid of the SHAPE, not the bounding box: a river county is a
      // crescent and its box centre can sit outside it.
      const at = new Map(shown.map((f) => [String(f.id), path.centroid(f)]));
      const shapeOf = new Map(shown.map((f) => [String(f.id), f]));

      const svg = d3.create("svg")
        .attr("width", width).attr("height", height)
        .attr("viewBox", [0, 0, width, height])
        .attr("style",
          'max-width:100%;height:auto;display:block;font-family:system-ui,'
          + '-apple-system,"Segoe UI",sans-serif;font-size:12px');

      const pin = new Set(String(config.highlight || "").split(",")
        .map((n) => n.trim()).filter(Boolean));
      const nameOf = new Map();
      for (const r of rows) {
        nameOf.set(String(r[fromGeo]), String(r[from]));
        nameOf.set(String(r[toGeo]), String(r[to]));
      }
      const isSubject = (geoid) => pin.has(nameOf.get(String(geoid)));

      // Subject counties read LIGHTER than the land around them. A
      // darker patch reads as a hole, and a pale ground is what the
      // lines need to show against. Borders stay one weight: a heavier
      // edge on three of seventeen counties reads as a property of those
      // borders rather than of the counties.
      svg.append("g").selectAll("path").data(shown).join("path")
        .attr("d", path)
        .attr("fill", (f) => (pin.size && isSubject(f.id)
          ? d3.interpolateLab(t.missing, t.surface)(0.55) : t.missing))
        .attr("stroke", t.boundary)
        .attr("stroke-width", 0.6);

      // Where a line crosses into a county, by bisection on "is this
      // point inside it" in geographic space. The border is a polygon
      // with hundreds of vertices and intersecting it directly buys
      // nothing here. Null when the target is not inside the county at
      // all -- a river county is a crescent and its centroid can fall
      // outside it.
      const crossInto = (fromPt, toPt, geoid) => {
        const shape = shapeOf.get(geoid);
        if (!shape || !projection.invert) return null;
        const at01 = (u) => [
          fromPt[0] + (toPt[0] - fromPt[0]) * u,
          fromPt[1] + (toPt[1] - fromPt[1]) * u,
        ];
        const inside = (u) => {
          const ll = projection.invert(at01(u));
          return !!ll && d3.geoContains(shape, ll);
        };
        if (!inside(1)) return null;
        let out = 0, inn = 1;
        for (let i = 0; i < 18; i += 1) {
          const mid = (out + inn) / 2;
          if (inside(mid)) inn = mid; else out = mid;
        }
        return at01(inn);
      };
      const along = (a, b, u) => [
        a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];

      const DEEP = 0.68, SHALLOW = 0.3;

      // Share, not raw workers. Boone sends 3,458 to Cole and Osage
      // 2,548 -- similar lines -- but that is 4% of Boone's
      // out-commuters against 44% of Osage's. The denominator is the
      // county's OWN traffic in that direction, taken at whichever end
      // is the subject.
      const outOf = new Map(), intoOf = new Map();
      for (const r of rows) {
        const a = String(r[fromGeo]), b = String(r[toGeo]);
        const n = +r[value] || 0;
        outOf.set(a, (outOf.get(a) || 0) + n);
        intoOf.set(b, (intoOf.get(b) || 0) + n);
      }

      const legs = [];
      for (const r of rows) {
        const a = String(r[fromGeo]), b = String(r[toGeo]);
        if (!at.has(a) || !at.has(b)) continue;
        const n = +r[value] || 0;
        const base = isSubject(a) ? outOf.get(a) : intoOf.get(b);
        legs.push({
          a, b, n,
          aName: String(r[from]), bName: String(r[to]),
          share: base ? n / base : 0,
        });
      }

      const pairs = new Map();
      for (const leg of legs) {
        const key = leg.a < leg.b ? `${leg.a}|${leg.b}` : `${leg.b}|${leg.a}`;
        const seen = pairs.get(key) || { legs: [] };
        seen.legs.push(leg);
        pairs.set(key, seen);
      }

      const drawn = [];
      [...pairs.entries()].forEach(([key, pair]) => {
        pair.legs.sort((x, y) => y.n - x.n);
        const lead = pair.legs[0];
        pair.legs.forEach((leg, rank) => {
          drawn.push(Object.assign({}, leg, {
            rank, pairShare: lead.share, key,
          }));
        });
      });

      // Only what touches the subject, and only what is a real part of
      // its traffic: 3% of a county's commuters. It is a share, so it
      // means the same for Boone and for Osage.
      const FLOOR = 0.03;
      const kept = (pin.size
        ? drawn.filter((r) => isSubject(r.a) || isSubject(r.b))
        : drawn).filter((r) => r.share >= FLOOR);

      // A CEILING ON ARROWS PER COUNTY.
      //
      // The floor is a share, so it means the same thing for a small
      // county as a large one -- but it says nothing about how many
      // arrows end up in one place. A hub county can clear it twenty
      // times over, and past a certain count no amount of routing saves
      // the picture: the arcs have nowhere left to go, the search runs
      // out of legal arrangements and falls back, and bases start
      // landing in counties the flow has nothing to do with.
      //
      // So each county admits a fixed number of pairs, largest first. A
      // pair is taken only if BOTH its counties still have room, which
      // bounds the arrows at every county on the map and not just at
      // the highlighted ones. A hub then spends its own budget on its
      // largest flows and the rest are left out, which is the trade the
      // cap exists to make.
      const CAP = +config.max_arrows > 0 ? +config.max_arrows : 6;
      const room = new Map();
      const taken = new Set();
      const ranked = [...new Set(kept.map((r) => r.key))]
        .map((key) => kept.find((r) => r.key === key))
        .sort((m, n) => n.pairShare - m.pairShare);
      for (const pair of ranked) {
        const left = room.get(pair.a) || 0, right = room.get(pair.b) || 0;
        if (left >= CAP || right >= CAP) continue;
        taken.add(pair.key);
        room.set(pair.a, left + 1);
        room.set(pair.b, right + 1);
      }
      const shownArcs = kept.filter((r) => taken.has(r.key));
      if (!shownArcs.length) {
        el.replaceChildren(svg.node());
        return;
      }

      // Line weight scales with the canvas AND with the counties.
      //
      // `max(20, width / 36)` floored the widest line at 20px however
      // small the map, so a 375px phone drew the same weight as a 720px
      // page and the biggest arcs swamped the counties they belong to.
      // Canvas width alone is not enough either: framing on a whole
      // state puts 115 counties in the same box, and a line sized for
      // seventeen buries them.
      //
      // So the cap is whichever is smaller -- a share of the canvas, or
      // a share of a typical county drawn on it. The floor stays
      // absolute, because below about a pixel a line is not drawn so
      // much as implied. Arrowheads are measured in stroke widths, so
      // they follow without a rule of their own.
      const spans = shown.map((f) => {
        const box = path.bounds(f);
        return Math.min(box[1][0] - box[0][0], box[1][1] - box[0][1]);
      });
      const typical = d3.median(spans) || width / 10;
      const fat = Math.max(3, Math.min(width / 36, typical * 0.4));
      // WHAT WIDTH MEANS, and it is a real choice -- but it is the ONLY
      // thing this setting changes. `share` goes on deciding which arcs
      // clear the 3% floor and which six a county keeps, because those
      // are questions about a county's own traffic and the answers
      // should not move when the drawing does. Making `share` itself a
      // headcount broke both: every count is above 0.03, so the floor
      // stopped filtering and the map filled with arrows.
      //
      // WIDTH IS THE COMMUTER COUNT, and there is no switch. One meaning
      // everywhere, nothing inverts, and the big county dominates --
      // true rather than tidy.
      //
      // The alternative, and what shipped first, was a share of the
      // subject county's OWN traffic. Within a single county that is
      // exactly right; across counties of different sizes it inverts,
      // because 90% of a very small county's outflow outdraws 50% of a
      // very large one's. On the Audrain/Boone/Osage map 13% of arrow
      // pairs had the WIDER arrow carrying FEWER people, worst case
      // 10.6x -- `Miller -> Osage` at 173 people drew wider than
      // `Randolph -> Boone` at 1,841. Under a headcount, none do.
      //
      // Dividing every flow by one shared denominator is not offered
      // either: the scale below normalises to the largest value, so a
      // shared constant cancels and draws exactly this picture.
      //
      // `share` still means what it always meant -- a share of the
      // county's own traffic -- and still drives the 3% floor, the
      // fan-out order and the tooltip. It simply no longer sets width.
      // Making `share` ITSELF a headcount, rather than separating the
      // two, broke the floor: every count is above 0.03, so nothing
      // filtered and the map filled with arrows.
      const widthOf = (r) => r.n;
      // SQUARE ROOT WHEN WIDTH IS A HEADCOUNT. Commuting counts are
      // heavily skewed -- the Boone corridor carries several times what
      // a rural pair does -- and on a linear scale the top flows sit at
      // the cap and overpower the map: everything else reads as absent
      // rather than as smaller.
      //
      // The root compresses the top without reordering anything, which
      // is the whole point: a wider arrow still means more people, it
      // just stops meaning "and nothing else matters".
      const w = d3.scaleSqrt()
        .domain([0, d3.max(shownArcs, widthOf) || 1])
        .range([Math.max(1.2, fat / 8), fat * 0.58]);

      // Where INSIDE the destination each leg aims. Five flows into
      // Boone all aimed at its centroid is the Boone collision: five
      // heads on one point. They fan across the county instead, spread
      // at right angles to the way each one arrives, so each has its
      // own place to land.
      const slots = new Map();
      for (const r of shownArcs.slice().sort((x, y) => y.share - x.share)) {
        const seen = slots.get(r.b) || [];
        seen.push(r);
        slots.set(r.b, seen);
      }
      const spread = new Map();
      for (const [geoid, group] of slots) {
        const box = path.bounds(shapeOf.get(geoid));
        const reach = Math.min(box[1][0] - box[0][0], box[1][1] - box[0][1]);
        // BY WIDTH, NOT BY INDEX. This gave every arrival the same slot
        // -- `(i - mid) * reach * 0.3` -- which holds only while the
        // arrows are about the same width. They are, when width is a
        // share of a county's own traffic: everything is a percentage
        // and the spread is narrow. When width is a headcount the top
        // flows are several times the rest, a wide arrow overruns a slot
        // sized for an average one, and the heads collide on the way in.
        //
        // Each leg now takes a band as wide as it is drawn, plus a small
        // gap, and the bands are laid end to end about the centre. Two
        // arrivals cannot overlap because neither is given room the
        // other is using.
        const widths = group.map((r) => w(widthOf(r)));
        const gap = Math.max(2, reach * 0.06);
        const need = widths.reduce((a, b) => a + b, 0) + gap * (group.length - 1);
        // A county only has so much edge. Where the bands do not fit,
        // every one is squeezed by the same factor, so the order and the
        // relative spacing survive and the fan stays inside the shape.
        const squeeze = need > reach ? reach / need : 1;
        let cursor = (-need * squeeze) / 2;
        group.forEach((r, i) => {
          const band = widths[i] * squeeze;
          spread.set(r, {
            step: cursor + band / 2,
            crowd: group.length,
          });
          cursor += band + gap * squeeze;
        });
      }

      // One hue for the whole map. Three put three meanings on colour at
      // once, and which county an arc belongs to is already given by
      // where it starts.
      //
      // COLOUR CARRIES DIRECTION, not size within a pair. It used to be
      // `rank === 0`, the larger leg of its own pair -- which is a
      // comparison a reader cannot make across the map, because a dark
      // arrow in one pair may be smaller than a light arrow in another.
      // Nothing about the shading was readable as a signal.
      //
      // Now it says which way the traffic runs relative to the counties
      // under study, which is the question the map exists to answer:
      //
      //   leaving a highlighted county   dark
      //   arriving at one                light
      //
      // Between TWO highlighted counties neither of those applies --
      // both are subjects -- so the pair rule stands there and the
      // larger leg is the darker, which is the one case where comparing
      // two arcs by shade is meaningful.
      const HUE = t.series[2 % t.series.length];
      const shadeBig = d3.interpolateLab(HUE, t.ink)(0.2);
      const shadeSmall = d3.interpolateLab(HUE, t.surface)(0.42);
      const colourOf = (r) => {
        const leaving = isSubject(r.a);
        const arriving = isSubject(r.b);
        if (leaving && !arriving) return shadeBig;
        if (arriving && !leaving) return shadeSmall;
        return r.rank === 0 ? shadeBig : shadeSmall;
      };
      const INK = 0.95, INK_SMALL = 1;
      const inkFor = (r) => (r.rank === 0 ? INK : INK_SMALL);

      // BOTH legs of a pair lie on ONE circle.
      //
      // Each leg used to take its radius from its own half of the route,
      // so the two halves met at the border with a kink in them. The
      // radius comes from the whole span now and both legs share it, so
      // a pair reads as a single smooth arc that happens to change
      // colour and thickness where the counties meet.
      // Arrowhead size, in stroke widths: a head is always in
      // proportion to its own line. The geometry needs this before it
      // can leave room for it.
      const HEAD = 2.4, HEAD_W = 3.4;
      // THE SHORTEST SHAFT THAT STILL READS AS AN ARROW, in multiples of
      // the arrow's own width. A flat 2.5 was hardcoded in the four
      // places that measure a leg -- the landing, the collision trace,
      // the legality check and the drawn path -- which made the minimum
      // length of any arrow 4.9 times its width, head included. For the
      // widest arrows that floor was what set their length: Callaway's
      // into Boone ran most of the way across the county because it was
      // fat, not because it had far to go. A wide arrow's head is
      // already unmistakable and needs almost no shaft behind it; a
      // hairline one needs the length to read at all.
      const leanOf = (wide) => Math.max(
        0.8, 2.6 - (fat ? Math.min(1, wide / fat) : 0) * 2.2);

      // A point inside `geoid` offset sideways from its centre, or the
      // centre when that lands outside the county -- a crescent county
      // can be stepped straight out of.
      const aimAt = (fromPt, geoid, step) => {
        const centre = at.get(geoid);
        if (!step || !projection.invert) return centre;
        const dx = centre[0] - fromPt[0], dy = centre[1] - fromPt[1];
        const len = Math.hypot(dx, dy) || 1;
        const tryAt = [centre[0] - (dy / len) * step,
          centre[1] + (dx / len) * step];
        const ll = projection.invert(tryAt);
        return (ll && d3.geoContains(shapeOf.get(geoid), ll)) ? tryAt : centre;
      };

      // How deep into its destination a leg reaches.
      //
      // `refX` 0 seats the arrowhead's BASE at the line's end, so the
      // head reaches HEAD stroke-widths further -- 48px on the widest
      // line here, enough to carry a point over the county's far border.
      // The line stops a head short of where the flow lands, so the
      // depth has to open far enough that what is left is a readable
      // line and not head alone.
      const landingOf = (r) => {
        const cA = at.get(r.a), cB = at.get(r.b);
        const lay = spread.get(r) || { step: 0, crowd: 1 };
        const aim = aimAt(cA, r.b, lay.step);
        const enter = crossInto(cA, aim, r.b) || cB;
        const reach = Math.hypot(aim[0] - enter[0], aim[1] - enter[1]) || 1;
        // Each later arrival into the same county stops shorter than the
        // one before, so flows end at their own depths rather than
        // piling five heads onto one centroid.
        const back = 1 - Math.min(lay.crowd - 1, 4) * 0.06;
        const wide = w(widthOf(r));
        // AS SHORT AS PRACTICAL TO BE CLEARLY DIRECTIONAL, and it does
        // not need to reach the centroid. `want` is the shortest arrow
        // that still reads as one -- a head plus a little shaft -- and
        // it was the FLOOR under a fixed 68%-of-the-way-in depth, so
        // every arrow drove most of the way to the centre whatever its
        // width. It is now the target and the old depth is the cap.
        //
        // The shaft a head needs shrinks as the arrow fattens: a wide
        // arrow is unmistakably directional on a stub, a hairline one
        // needs length to read at all. So the widest arrows -- which
        // are the ones that overlap most, and the ones that overpower
        // a county when they cross it -- become the shortest.
        const rel = fat ? Math.min(1, wide / fat) : 0;
        const want = HEAD * wide + wide * leanOf(wide);
        // The cap shrinks with width too. For the fattest arrows `want`
        // is longer than the run to the centroid, so the cap is what
        // binds and they drove 68% of the way in regardless -- which is
        // exactly the arrow that least needs the distance and most
        // overpowers the county it crosses.
        const cap = (r.rank === 0 ? DEEP : SHALLOW) * back * (1 - rel * 0.45);
        const depth = Math.min(0.95, cap, Math.max(want / reach, 0.12));
        return along(enter, aim, depth);
      };

      // ONE CIRCLE PER PAIR.
      //
      // Equal radii are not the same arc: two arcs of the same radius
      // leaving one point in different directions have different
      // centres, and the pair kinks where they meet. So the pair is one
      // circle, built THROUGH the base and tangent to the line between
      // the two counties, and the legs are the two halves of it -- same
      // centre, same radius, read as one arc that changes colour and
      // thickness at the county line.
      //
      // WHAT IS PINNED AND WHAT IS FREE. The base is pinned: it sits on
      // the border of the county the pair is about, and both legs leave
      // from it. Free are which way the arc bows and how hard, where
      // along that border the base sits, and how far each leg runs.
      // Pairs are placed heaviest first and each takes the least bent,
      // least shifted, least shortened arrangement that clears what is
      // already down -- so the big flows keep the straight routes and
      // the small ones go around them.
      // More places to try. Each is a pair's base sliding along the line
      // between its two counties; with only five, a crowded county ran
      // out of room and later pairs had to overlap. The tie-breaker
      // below still prefers the unshifted arrangement, so this only
      // matters where something is in the way.
      const SLIDE = [0, -0.4, 0.4, -0.8, 0.8, -1.2, 1.2];
      // Radius as a multiple of the distance between the two counties,
      // flattest first. The tight end is what lets a pair whose
      // counties do not touch bend around a third county instead of
      // going through it -- at 2.8 the arc only leaves the straight
      // line by about a twenty-second of its length, nowhere near
      // enough to route Audrain's line to Cole through Callaway.
      const CURVE = [9, 6, 4, 2.8, 2, 1.4, 1];
      const SIDE = [1, -1];
      // How far along its circle a leg runs before its head. SHORTENING
      // IS THE CHEAPEST WAY OUT OF A COLLISION -- an arrow that stops
      // earlier still starts in the right county, still points the right
      // way and still carries its width, so nothing it says is lost --
      // and it was both the least available option (34% at most) and the
      // most penalised one (`ri * 22`, the heaviest tie-breaker), so the
      // search would rather bend a pair into another arrow than let it
      // end sooner. Three arrivals into Boone sat on top of each other
      // for want of stopping short.
      const RUN = [1, 0.82, 0.66, 0.52, 0.4];
      const STEPS = 14;

      // The base, and the direction the pair runs, for a given shift
      // along the border. Shifting both centroids the same way sideways
      // slides the crossing along the border without turning the pair.
      const baseFor = (x, y, slide) => {
        const cX = at.get(x), cY = at.get(y);
        const span = Math.hypot(cY[0] - cX[0], cY[1] - cX[1]) || 1;
        const ux = (cY[0] - cX[0]) / span, uy = (cY[1] - cX[1]) / span;
        const off = slide * span * 0.16;
        const shift = (c) => [c[0] - uy * off, c[1] + ux * off];
        const aX = shift(cX), aY = shift(cY);
        const faceX = crossInto(aY, aX, x) || crossInto(cY, cX, x) || cX;
        const faceY = crossInto(aX, aY, y) || crossInto(cX, cY, y) || cY;
        const both = isSubject(x) && isSubject(y);
        const own = !both && isSubject(y) ? y : x;
        const meet = (both || !isSubject(own))
          ? [(faceX[0] + faceY[0]) / 2, (faceX[1] + faceY[1]) / 2]
          : (own === x ? faceX : faceY);
        return { meet, ux, uy, span };
      };

      const circleFor = (key, legs, slide, curve, side, run) => {
        const [x, y] = key.split("|");
        const { meet, ux, uy, span } = baseFor(x, y, slide);
        const rad = span * curve;
        // Centre one radius off the base, square to the way the pair
        // runs, so the circle passes through the base and leaves it
        // headed at the other county. `side` picks which way it bows.
        const cx = meet[0] + uy * rad * side;
        const cy = meet[1] - ux * rad * side;
        const reach = new Map();
        for (const leg of legs) {
          const land = landingOf(leg);
          reach.set(leg.b, run
            * Math.hypot(land[0] - meet[0], land[1] - meet[1]));
        }
        return {
          cx, cy, rad, y, reach, side,
          base: Math.atan2(meet[1] - cy, meet[0] - cx),
        };
      };

      // Points down the middle of every leg, arrowheads included, at the
      // width they are drawn -- enough to tell whether two pairs are on
      // top of one another.
      const traceOf = (c, legs) => {
        const out = [];
        const runs = [];
        for (const leg of legs) {
          const line = [];
          out.line = line;
          runs.push(line);
          const wide = w(widthOf(leg));
          const turn = (leg.b === c.y ? -1 : 1) * c.side;
          const far = Math.max((c.reach.get(leg.b) || 0) - HEAD * wide,
            wide * leanOf(wide)) + HEAD * wide;
          for (let i = 0; i <= STEPS; i += 1) {
            const angle = c.base + turn * ((far * (i / STEPS)) / c.rad);
            const at01 = {
              x: c.cx + c.rad * Math.cos(angle),
              y: c.cy + c.rad * Math.sin(angle),
              // The head is wider than its line, so it needs more room.
              wide: i > STEPS - 3 ? wide * HEAD_W : wide,
            };
            out.push(at01);
            runs[runs.length - 1].push(at01);
          }
        }
        return { points: out, runs };
      };

      // Proximity alone misses a crossing: on a long leg the samples sit
      // 25px apart and one line can pass clean between two of another's
      // points, which is how Audrain's line to Cole came to run straight
      // through the middle of Boone's. Segments are tested for a real
      // intersection as well, so a crossing is caught however coarsely
      // the two lines happen to be sampled.
      const turns = (a, b, c0) => Math.sign(
        (b.x - a.x) * (c0.y - a.y) - (b.y - a.y) * (c0.x - a.x));
      const crosses = (a, b, c0, d) => (
        turns(a, b, c0) !== turns(a, b, d)
        && turns(c0, d, a) !== turns(c0, d, b));

      // Two things the search may never trade away for a clear route:
      // the base stays on a border of one of the pair's own counties,
      // and every arrowhead's point lands inside the county the flow is
      // going to. A candidate that breaks either is not a candidate.
      const inside = (pt, geoid) => {
        if (!projection.invert) return true;
        const ll = projection.invert(pt);
        return !!ll && d3.geoContains(shapeOf.get(geoid), ll);
      };
      const legal = (c, legs, x, y) => {
        const seat = [c.cx + c.rad * Math.cos(c.base),
          c.cy + c.rad * Math.sin(c.base)];
        if (!inside(seat, x) && !inside(seat, y)) return false;
        for (const leg of legs) {
          const wide = w(widthOf(leg));
          const turn = (leg.b === c.y ? -1 : 1) * c.side;
          const far = Math.max((c.reach.get(leg.b) || 0) - HEAD * wide,
            wide * leanOf(wide)) + HEAD * wide;
          const angle = c.base + turn * (far / c.rad);
          if (!inside([c.cx + c.rad * Math.cos(angle),
            c.cy + c.rad * Math.sin(angle)], leg.b)) return false;
        }
        return true;
      };

      // A pair whose counties do not touch has to cross something on
      // the way. It should cross the quiet ground, not one of the
      // highlighted counties, which are where every other arrow is:
      // Audrain's line to Cole has Callaway and Boone to choose from
      // and belongs in Callaway.
      const trespass = (trace, x, y) => {
        if (!pin.size || !projection.invert) return 0;
        let cost = 0;
        for (const geoid of shapeOf.keys()) {
          if (geoid === x || geoid === y || !isSubject(geoid)) continue;
          const shape = shapeOf.get(geoid);
          for (const p of trace.points) {
            const ll = projection.invert([p.x, p.y]);
            if (ll && d3.geoContains(shape, ll)) cost += 60;
          }
        }
        return cost;
      };

      // HOW FAR APART ARRIVALS INTO ONE COUNTY SHOULD CROSS ITS BORDER.
      // Scaled to the county, because a big county has a long border to
      // spread across and a small one does not. Roughly a third of the
      // shape's diagonal: enough that two arrivals read as entering
      // through different stretches, not so much that a county with
      // four of them cannot satisfy it.
      const apart = new Map();
      {
        const path = d3.geoPath(projection);
        for (const [geoid, shape] of shapeOf) {
          const box = path.bounds(shape);
          apart.set(geoid, Math.hypot(box[1][0] - box[0][0],
            box[1][1] - box[0][1]) * 0.32);
        }
      }

      // Where each leg crosses into the county it is going to.
      const doorsOf = (c, legs) => {
        const out = [];
        legs.forEach((leg, i) => {
          const line = c.runs[i];
          for (let k = 0; k < line.length; k += 1) {
            if (inside([line[k].x, line[k].y], leg.b)) {
              out.push({ geoid: leg.b, x: line[k].x, y: line[k].y });
              return;
            }
          }
        });
        return out;
      };

      // THE FAN SPREADS WHERE ARROWS STOP; THIS SPREADS WHERE THEY ARRIVE.
      // Landing points are fanned around the destination's centroid, so
      // two arrivals can end well apart and still have entered the county
      // through the same few pixels of border and lain on each other the
      // whole way in -- which is what Randolph's and Audrain's arrows into
      // Boone were doing. Osage reads clearly because its arrivals happen
      // to come in through different stretches of its border; costing the
      // distance between crossings is what makes that general rather than
      // lucky.
      //
      // Semi-equal, not equal. It is a cost, so the exterior border
      // (`legal`), the arrows already placed and the trespass rule can all
      // still override it -- a county whose only clear approach is one
      // stretch of border keeps that approach.
      const bunched = (doors, already) => {
        let cost = 0;
        for (const d of doors) {
          const want = apart.get(d.geoid) || 0;
          if (!want) continue;
          for (const e of already) {
            if (e.geoid !== d.geoid) continue;
            const gap = Math.hypot(d.x - e.x, d.y - e.y);
            // Normalised, so it is worth about one crossing when two
            // arrows enter through the same point and nothing at all
            // once they are a third of the county apart.
            if (gap < want) cost += (((want - gap) / want) ** 2) * 400;
          }
        }
        return cost;
      };

      const clash = (trace, placed, laid) => {
        let cost = 0;
        for (const p of trace.points) {
          for (const q of placed) {
            const need = (p.wide + q.wide) / 2 + 3;
            const gap = Math.hypot(p.x - q.x, p.y - q.y);
            // NORMALISED BY THE ROOM THE PAIR NEEDED, not measured in
            // raw pixels. `(need - gap) ** 2` is in units of width
            // squared, so the fattest arrows -- exactly the ones the
            // people-width scale makes fattest -- scored their overlaps
            // in the thousands while everything else in the cost
            // function was worth tens. Length, arc and border spacing
            // were all being computed correctly and then drowned: the
            // search was not ignoring them, it could not hear them.
            // A full overlap is now worth about one crossing whatever
            // the arrow's width.
            if (gap < need) cost += (((need - gap) / need) ** 2) * 60;
          }
        }
        for (const mine of trace.runs) {
          for (const theirs of laid) {
            for (let i = 1; i < mine.length; i += 1) {
              for (let j = 1; j < theirs.length; j += 1) {
                if (crosses(mine[i - 1], mine[i], theirs[j - 1], theirs[j])) {
                  cost += 400;
                }
              }
            }
          }
        }
        return cost;
      };

      const circles = new Map();
      const byPair = new Map();
      for (const r of shownArcs) {
        byPair.set(r.key, (byPair.get(r.key) || []).concat([r]));
      }
      const placed = [], laid = [], doorway = [];
      // WIDEST FIRST, because the search is greedy: each pair is placed
      // against everything already down, and nothing moves once placed.
      // Whoever goes first gets the room. Ordering by `share` gave first
      // pick to the largest share of its own county's traffic, which
      // under a headcount width can be a thin arrow -- so the widest
      // arrows were placed last, into whatever space was left, and they
      // are the ones that cannot fit in a gap.
      const heaviest = [...byPair.entries()].sort(
        (m, n) => w(widthOf(n[1][0])) - w(widthOf(m[1][0])));
      for (const [key, legs] of heaviest) {
        const [x, y] = key.split("|");
        let best = null;
        SLIDE.forEach((slide, si) => {
          CURVE.forEach((curve, ci) => {
            for (const side of SIDE) {
              RUN.forEach((run, ri) => {
                const c = circleFor(key, legs, slide, curve, side, run);
                if (!legal(c, legs, x, y)) return;
                const trace = traceOf(c, legs);
                // Collisions dominate; the rest are tie-breakers that
                // keep the straightest, longest, unshifted arrangement
                // when nothing is in the way.
                // Shortening now costs about what sliding does, rather
                // than twice as much: it is a tie-breaker, so a clear
                // map still draws full-length arrows, but a crowded one
                // reaches for the shorter arrow before the contorted
                // one.
                const doors = doorsOf(trace, legs);
                const cost = clash(trace, placed, laid)
                  + trespass(trace, x, y)
                  + bunched(doors, doorway)
                  // SHORT AND STRAIGHT BEATS LONG AND BENT. Shortening
                  // was the dearest escape from a collision and bending
                  // among the cheapest, so the search bought its way out
                  // of every crowded county with the tightest arc on
                  // offer -- Cooper, Audrain and Osage->Cole all came
                  // back at the extreme end of CURVE while barely
                  // shortening at all. An arrow that stops earlier still
                  // says everything it has to say; one bent into a hook
                  // reads as a different kind of flow. Curving is now the
                  // dearest of the three and shortening the cheapest.
                  + si * 14 + ci * 26 + ri * 5 + (side < 0 ? 6 : 0);
                if (!best || cost < best.cost) best = { c, trace, cost, doors };
              });
            }
          });
        });
        if (!best) {
          const c = circleFor(key, legs, 0, CURVE[1], 1, 1);
          const trace = traceOf(c, legs);
          best = { c, trace, doors: doorsOf(trace, legs) };
        }
        circles.set(key, best.c);
        for (const p of best.trace.points) placed.push(p);
        for (const line of best.trace.runs) laid.push(line);
        for (const d of best.doors) doorway.push(d);
      }

      const routeOf = (r, wide) => {
        const c = circles.get(r.key);
        if (!c) return "M0,0";
        // Going toward Y runs one way round the circle; the other leg
        // is the same circle travelled the other way.
        const turn = (r.b === c.y ? -1 : 1) * c.side;
        const head = HEAD * wide;
        const run = Math.max((c.reach.get(r.b) || 0) - head,
          wide * leanOf(wide));
        const stop = c.base + turn * (run / c.rad);
        const from = [c.cx + c.rad * Math.cos(c.base),
          c.cy + c.rad * Math.sin(c.base)];
        const till = [c.cx + c.rad * Math.cos(stop),
          c.cy + c.rad * Math.sin(stop)];
        return `M${from[0]},${from[1]}A${c.rad},${c.rad} 0 0,`
          + `${turn > 0 ? 1 : 0} ${till[0]},${till[1]}`;
      };

      // One arrow per colour, scaled by the line it caps: `markerUnits`
      // defaults to stroke widths, so a head is always in proportion to
      // its own line. `overflow` visible because a marker clips to its
      // viewBox and the tip sits on the edge -- without it every point
      // comes out flattened.
      const defs = svg.append("defs");
      // UNIQUE PER MOUNT, not per canvas width. The id was
      // `dd-ar-<n>-<hash of width>`, and `arrowIds` starts empty on
      // every render -- so two flow maps of the same width on one page
      // both minted `dd-ar-0-<same hash>`. SVG resolves `marker-end` by
      // id across the whole document, so the second chart's markers
      // captured the first's arrowheads and half the heads came out the
      // wrong colour. Seen side by side while choosing what width should
      // mean; it would happen to any page carrying two of these.
      const arrowIds = new Map();
      const mint = (arrowSeq += 1);
      const arrowFor = (colour) => {
        if (!arrowIds.has(colour)) {
          const id = `dd-ar-${mint}-${arrowIds.size}`;
          arrowIds.set(colour, id);
          defs.append("marker")
            .attr("id", id).attr("viewBox", "0 0 10 10")
            .attr("refX", 0).attr("refY", 5)
            .attr("markerWidth", HEAD).attr("markerHeight", HEAD_W)
            .attr("overflow", "visible").attr("orient", "auto")
            .append("path").attr("d", "M0,2 L10,5 L0,8 Z")
            .attr("fill", colour);
        }
        return arrowIds.get(colour);
      };
      const haloArrow = arrowFor(t.boundary);

      // WITHIN a pair the smaller leg draws last, so it sits on its
      // partner. ACROSS pairs the larger wins, so a small flow crossing
      // a big one passes underneath. Sorting by the pair's weight first
      // and by rank second satisfies both.
      const ordered = shownArcs.slice().sort(
        (x, y) => (x.pairShare - y.pairShare) || (x.rank - y.rank));
      // The lighter leg gets a hairline behind it -- a slightly wider
      // line and a slightly larger arrowhead in the boundary colour.
      // SVG cannot outline a stroke, and a stroked arrowhead draws its
      // edge inside the fill as well as outside, which read as a diamond
      // sitting in the middle of the head.
      const layers = [];
      for (const r of ordered) {
        if (r.rank > 0) layers.push({ r, halo: true });
        layers.push({ r, halo: false });
      }

      const all = svg.append("g").attr("fill", "none")
        .selectAll("path").data(layers).join("path")
        .attr("d", (d) => routeOf(d.r, w(widthOf(d.r)) + (d.halo ? 1.2 : 0)))
        // Butt, not round: a round cap pokes out from under the head.
        .attr("stroke-linecap", "butt")
        .attr("stroke-width", (d) => w(widthOf(d.r)) + (d.halo ? 1.2 : 0))
        .attr("stroke", (d) => (d.halo ? t.boundary : colourOf(d.r)))
        .attr("stroke-opacity", (d) => (d.halo ? 1 : inkFor(d.r)))
        .attr("marker-end", (d) => `url(#${d.halo
          ? haloArrow : arrowFor(colourOf(d.r))})`);

      el.replaceChildren(svg.node());
      const tip = tooltip(el);
      interactive(all.filter((d) => !d.halo), tip, (d) => {
        const r = d.r;
        return `${r.aName} → ${r.bName}<br>`
          + `${(r.share * 100).toFixed(1)}% of `
          + `${isSubject(r.a) ? r.aName + "'s out-commuters"
            : r.bName + "'s in-commuters"}`
          + `<br>${r.n.toLocaleString()} workers`;
      });
    });
  }

  function renderChord(el, config, rows, t, width) {
    const d3 = global.d3;
    const { from, to, value } = config;
    if (!from || !to || !value) {
      el.textContent = "Pick the from, to, and value columns."; return;
    }
    rows = coerce(rows.slice(), value);
    // The taxonomy the visual pinned, if it pinned one. That is what keeps
    // a CIN need the same colour in this chart as in every other, which is
    // the whole reason the option exists -- and the chord ignored it.
    const fixed = taxonomy(config.taxonomy, t);
    // What the chart is ABOUT, if the author said. Kept out of "Other"
    // and drawn in the accent hues, with everything else muted, so the
    // subject reads at a glance instead of being one of nine colours.
    const pin = String(config.highlight || "").split(",")
      .map((n) => n.trim()).filter(Boolean);
    const { names, fold } = edgeGroups(rows, from, to, fixed && fixed.order, pin);
    const colors = fixed
      ? names.map((n, i) => {
          const at = fixed.order.indexOf(n);
          // Anything the vocabulary does not name falls back to a slot,
          // taken from the end so it cannot collide with a pinned hue.
          return at >= 0 ? fixed.colors[at] : slotColors(names, t)[i];
        })
      : slotColors(names, t);
    // Muted for everything that is not the subject. Not grey-on-grey:
    // the partners still need telling apart from each other, so they keep
    // their own hues at half strength and the subject keeps full.
    const highlighted = new Set(pin);
    const ink = pin.length
      ? names.map((n, i) =>
          highlighted.has(n) ? t.series[i % t.series.length] : t.missing)
      : colors;
    const index = new Map(names.map((n, i) => [n, i]));
    const matrix = names.map(() => names.map(() => 0));
    for (const r of rows) {
      const a = index.get(fold(r[from])), b = index.get(fold(r[to]));
      if (a != null && b != null) matrix[a][b] += +r[value] || 0;
    }
    // Labels follow the ring rather than spiking out of it, so the room
    // they need is a band around the arc, not the length of the longest
    // name. That is most of the old margin given back to the circle.
    const labelRoom = 34;
    const size = Math.min(width, 620);
    const R = size / 2 - labelRoom;
    const chords = d3.chord().padAngle(0.04)
      .sortSubgroups(d3.descending)(matrix);
    const svg = svgRoot(size, size, t);
    const group = svg.append("g").selectAll("g").data(chords.groups).join("g");
    const groupArcs = group.append("path")
      .attr("d", d3.arc().innerRadius(R).outerRadius(R + 12))
      .attr("fill", (d) => ink[d.index]);
    // Labels ride along the arc, not out from it. Set radially they read
    // as spokes at every angle but the horizontal, which is what "at 90
    // degrees to the circle" looks like -- the eye has to travel around
    // the ring turning its head. A textPath keeps them on the curve.
    //
    // A path per group rather than one ring, because each label is
    // centred on its own arc and the bottom half has to be reversed.
    // Outside the band, not on it. On it, the text sat over saturated
    // fills -- dark on dark green and dark on purple -- and a label is
    // only useful if it can be read. Outside, it is always on the chart
    // surface, whose contrast is a known quantity.
    const BAND = 12;
    const LABEL_R = R + BAND + 3;
    const uid = `chord-${Math.abs(hashOf(names.join("|")))}`;
    const defs = svg.append("defs");

    // 0 is twelve o'clock and angles run clockwise, so the lower half --
    // between three and nine o'clock -- would carry its text upside down.
    // Those arcs are drawn the other way round instead.
    const arcPath = (a, b, flip) => {
      const at = (ang) => [
        (LABEL_R * Math.sin(ang)).toFixed(2),
        (-LABEL_R * Math.cos(ang)).toFixed(2),
      ];
      const [x1, y1] = at(flip ? b : a);
      const [x2, y2] = at(flip ? a : b);
      const large = Math.abs(b - a) > Math.PI ? 1 : 0;
      return `M${x1},${y1}A${LABEL_R},${LABEL_R} 0 ${large} ${flip ? 0 : 1} ${x2},${y2}`;
    };

    // How much ring a label may use: up to halfway to the arc on either
    // side of it, not the width of its own arc.
    //
    // Its own arc was the first answer and it is wrong twice over. A name
    // longer than its arc overflowed a path that stopped at the arc's
    // ends, and SVG clips a textPath at both -- which is how "Environment
    // and Planning" lost its E as well as its tail. And a real
    // distribution is lopsided: on March's data, Civic Life takes a
    // quarter of the ring and Economic Development a few degrees, so
    // sizing to the arc means the small categories can never be named at
    // all. The space between neighbours is the space actually free.
    const two = Math.PI * 2;
    const mids = chords.groups.map((g) => (g.startAngle + g.endAngle) / 2);
    const spans = mids.map((mid, i) => {
      const before = mids[(i - 1 + mids.length) % mids.length];
      const after = mids[(i + 1) % mids.length];
      const left = ((mid - before + two) % two) / 2;
      const right = ((after - mid + two) % two) / 2;
      // A tenth held back on each side, so two full labels never touch.
      return { mid, half: Math.min(left, right) * 0.9 };
    });

    group.each(function (d, i) {
      d.angle = mids[i];
      const { mid, half } = spans[i];
      const flip = mid > Math.PI / 2 && mid < (3 * Math.PI) / 2;
      const id = `${uid}-${i}`;
      defs.append("path").attr("id", id)
        .attr("d", arcPath(mid - half, mid + half, flip));

      d3.select(this).append("text")
        // dy moves the text along the path's own "down", which points at
        // the centre on the top half and away from it on the flipped
        // bottom half. So the sign differs to put both outside the ring.
        .attr("dy", flip ? "0.95em" : "-0.4em")
        .attr("fill", "currentColor")
        .attr("data-room", (half * 2 * LABEL_R).toFixed(1))
        .append("textPath")
        .attr("href", `#${id}`)
        .attr("startOffset", "50%")
        .attr("text-anchor", "middle")
        .text(String(names[d.index]));
    });

    const ribbons = svg.append("g").selectAll("path").data(chords).join("path")
      .attr("d", d3.ribbon().radius(R - 2))
      // A ribbon takes the subject's colour from whichever end is the
      // subject, so a flow INTO Boone reads as Boone's as much as one
      // out of it. Coloured by source alone, half of the subject's own
      // traffic rendered in a partner's hue.
      .attr("fill", (d) =>
        pin.length && highlighted.has(names[d.target.index])
        && !highlighted.has(names[d.source.index])
          ? ink[d.target.index]
          : ink[d.source.index])
      .attr("fill-opacity", 0.7)
      .attr("stroke", t.surface).attr("stroke-width", 0.5);

    el.replaceChildren(svg.node());
    fitLabels(svg.node());
    const tip = tooltip(el);
    // Hovering a group isolates every flow touching it; a ribbon isolates
    // that one pair.
    interactive(groupArcs, tip, (d) =>
      `<strong>${names[d.index]}</strong>` + tipRow("total", d.value),
      { group: ribbons,
        related: (target, other) =>
          other.source.index === target.index ||
          other.target.index === target.index });
    interactive(ribbons, tip, (d) =>
      `<strong>${names[d.source.index]} \u2192 ${names[d.target.index]}</strong>` +
      tipRow("value", d.source.value) +
      (d.source.index !== d.target.index
        ? tipRow(`${names[d.target.index]} \u2192 ${names[d.source.index]}`,
                 d.target.value)
        : ""),
      { group: ribbons, related: (target, other) => target === other });
  }

  // Arc diagram: nodes on a baseline, arcs above, weight as stroke width.
  function renderArc(el, config, rows, t, width) {
    const d3 = global.d3;
    const { from, to, value } = config;
    if (!from || !to) { el.textContent = "Pick the from and to columns."; return; }
    if (value) rows = coerce(rows.slice(), value);
    const { names, fold } = edgeGroups(rows, from, to);
    const colors = slotColors(names, t);
    const index = new Map(names.map((n, i) => [n, i]));
    const margin = 40, baseline = 60;
    const xAt = d3.scalePoint(names, [-width / 2 + margin, width / 2 - margin]);
    const weights = rows.map((r) => (value ? +r[value] || 0 : 1));
    const w = d3.scaleSqrt()
      .domain([0, d3.max(weights) || 1]).range([1, 10]);
    const arcSpan = (a, b) => Math.abs(xAt(names[b]) - xAt(names[a]));
    const height = Math.min(
      420, baseline + margin + d3.max([120, width / 3.2]));
    const svg = svgRoot(width, height, t);
    const Y = height / 2 - baseline;
    svg.append("g").selectAll("path").data(rows).join("path")
      .attr("d", (r) => {
        const a = xAt(fold(r[from])), b = xAt(fold(r[to]));
        if (a == null || b == null) return null;
        const rad = Math.abs(b - a) / 2;
        return `M${a},${Y} A${rad},${rad} 0 0,${a < b ? 1 : 0} ${b},${Y}`;
      })
      .attr("fill", "none")
      .attr("stroke", (r) => colors[index.get(fold(r[from]))])
      .attr("stroke-opacity", 0.55)
      .attr("stroke-width", (r) => w(value ? +r[value] || 0 : 1));
    const arcPaths = svg.selectAll("path");
    const node = svg.append("g").selectAll("g").data(names).join("g")
      .attr("transform", (n) => `translate(${xAt(n)},${Y})`);
    node.append("circle").attr("r", 5)
      .attr("fill", (n, i) => colors[i])
      .attr("stroke", t.surface).attr("stroke-width", 1.5);
    node.append("text").attr("transform", "rotate(35)")
      .attr("x", 4).attr("y", 14).attr("fill", "currentColor")
      .text((n) => n);
    el.replaceChildren(svg.node());
    const tip = tooltip(el);
    interactive(arcPaths, tip, (r) =>
      `<strong>${r[from]} \u2192 ${r[to]}</strong>` +
      (value ? tipRow(value, r[value]) : ""),
      { group: arcPaths, related: (target, other) => target === other });
    interactive(node.select("circle"), tip, (n) => {
      const touching = rows.filter(
        (r) => fold(r[from]) === n || fold(r[to]) === n);
      const total = value
        ? touching.reduce((a, r) => a + (+r[value] || 0), 0)
        : touching.length;
      return `<strong>${n}</strong>` +
        tipRow("connections", touching.length) + tipRow("total", total);
    }, { group: arcPaths,
         related: (target, r) =>
           fold(r[from]) === target || fold(r[to]) === target });
    void arcSpan;
  }

  // The story map: two layers over one payload (see visuals/corpus.py).
  //   counties shaded by how many place-set ("regional") stories touch them
  //   dots at each story central, sized by story count, coloured by the
  //   precision the model actually claimed — place / block / county.
  // Both layers are hover-isolating and tappable.
  const PRECISION = { place: 0, block: 1, county: 2, state: 3, tract: 4 };

  //: The fixed ladder absolute banding cuts at, so one shade means one count
  //: on every map that uses it. Roughly logarithmic because the counts are:
  //: Missouri counties run 1..969 with a median of 44, and even steps would
  //: put almost every county in the first band.
  const ABSOLUTE_BANDS = [1, 2, 5, 10, 20, 50, 100, 200, 500];

  function renderStoryMap(el, config, data, opts, t, width) {
    const d3 = global.d3;
    const payload = Array.isArray(data) ? { points: data, areas: [] } : (data || {});
    const points = payload.points || [];
    const areas = payload.areas || [];
    if (!points.length && !areas.length) {
      // "No mapped stories" is true and useless: it does not say whether
      // the slice is empty, whether the newsrooms chosen published
      // nothing, or whether the map is centred on a place none of them
      // write about -- which is what happens to a duplicated map
      // retargeted at one county and still filtered to another's
      // newsrooms. The feed says which of those it is.
      const why = (payload.meta || {}).empty_because;
      el.textContent = why || "No mapped stories.";
      return;
    }

    const ids = [
      ...areas.map((a) => String(a.geoid || "")),
      ...points.map((p) => String(p.geoid || "")),
    ].filter(Boolean);

    boundaries(opts.geoBase, "counties", ids, opts.geoUrls).then((features) => {
      // Focus decides the frame, never what is drawn: every county in
      // view is painted, so a state without stories reads as "none"
      // rather than as a hole in the map.
      const focus = String(config.focus || "").trim();
      let framed;
      // An explicit list wins. The builder resolves "Boone, MO" and the
      // chosen extent into the counties to paint, so a published map
      // shows what its config says rather than what a rule re-derives.
      const chosen = Array.isArray(config.frame) ? config.frame.map(String) : [];
      if (chosen.length) {
        const wanted = new Set(chosen);
        framed = features.filter((f) => wanted.has(String(f.id)));
      } else if (/^\d{5}$/.test(focus)) {
        // A county focus frames its whole state — a lone county floating
        // in white says nothing about where it is.
        framed = features.filter((f) => String(f.id).slice(0, 2) === focus.slice(0, 2));
      } else if (/^\d{2}$/.test(focus)) {
        framed = features.filter((f) => String(f.id).slice(0, 2) === focus);
      } else {
        // Auto: frame the states carrying most of the stories, so a
        // handful of distant mentions do not zoom the map out to the
        // whole country.
        const weight = new Map();
        for (const a of areas) {
          const st = String(a.geoid).slice(0, 2);
          weight.set(st, (weight.get(st) || 0) + a.stories);
        }
        for (const p of points) {
          const st = String(p.geoid || "").slice(0, 2);
          if (st) weight.set(st, (weight.get(st) || 0) + p.stories);
        }
        const total = [...weight.values()].reduce((a, b) => a + b, 0);
        const keep = new Set(
          [...weight].filter(([, n]) => n >= total * 0.02).map(([st]) => st));
        framed = features.filter((f) => keep.has(String(f.id).slice(0, 2)));
      }
      if (!framed.length) framed = features;
      // An explicit focus draws only that geography — the March map is
      // Missouri and nothing else. Auto-framing keeps every county in
      // view so no state reads as a hole.
      const focused = /^\d{2,5}$/.test(focus);
      const shown = focused ? framed : features;
      const inFrame = new Set(shown.map((f) => String(f.id).slice(0, 2)));
      // The counties this map actually paints. Declared here because the
      // scale, the legend's gate and the cuts all read it, and `const`
      // is not hoisted -- used above its declaration it throws.
      //
      // It follows the frame wherever that came from: an explicit
      // `config.frame`, a focus, or the auto weighting above.
      const painted = new Set(shown.map((f) => String(f.id)));
      const byCounty = new Map(areas.map((a) => [String(a.geoid), a.stories]));
      // Whether there is anything to put a scale on. Read off the
      // painted counties for the same reason the cuts are: a frame with
      // no stories in it must not draw a key for somebody else's.
      const max = d3.max(
        areas.filter((a) => painted.has(String(a.geoid))),
        (a) => a.stories
      ) || 0;
      // Bands are equal-count groups of the counties that actually have
      // stories, so the map stays informative whether it is a 500-article
      // sample or the whole corpus. config.bands: "fixed" restores the
      // March map's 1-2 / 3-5 / 6-9 / 10+ cuts; a number sets how many
      // steps the ramp has.
      //
      // HOW MANY STEPS IS NOW A SETTING, and it had to become one. Four
      // bands over a skewed count puts everything above the third
      // quartile in one colour: the Missouri map's top band read "12+"
      // while the counties in it held between 12 and 204 stories, so a
      // county with fifteen and one with two hundred were the same shade
      // and the map could not be read as a ranking at all.
      // DECILES BY DEFAULT. Four bands over a skewed count is not a
      // ranking: the Missouri map's top band read "12+" and held
      // counties with anything from 12 to 204 stories in one colour.
      // Ten is what the ramp can carry now that its steps are spaced by
      // lightness -- every adjacent pair differs by about 0.071 of
      // relative luminance, which is more separation than the old
      // four-band scale had. Twelve is the cap for the same reason: at
      // 0.059 it is still readable, and past that the palest steps stop
      // being tellable apart on a small county.
      const steps = config.bands === "fixed"
        ? 4
        : Math.min(12, Math.max(3, parseInt(config.bands, 10) || 10));
      // BANDED ON WHAT IS DRAWN, not on what the feed carries. The
      // payload holds every county the corpus touched -- 710 of them on
      // the Missouri map -- while the map paints 115. The 595 counties
      // outside the frame have a median of 2 stories, so the deciles
      // came out at 1,1,1,2,2,4,7,20,44 and every Missouri county
      // (median 44, max 969) landed in the top band or two: the whole
      // state one flat colour. Over the counties actually shown the
      // same cuts are 15,22,30,38,44,56,70,92,184.
      //
      const values = areas
        .filter((a) => painted.has(String(a.geoid)))
        .map((a) => a.stories)
        .filter((n) => n > 0)
        .sort(d3.ascending);
      // Cuts at i/steps, rising, de-duplicated. A count with many ties
      // can put two quantiles on the same number, which would draw two
      // bands covering the same range with one of them always empty.
      // RELATIVE OR ABSOLUTE, and the difference is what the colour means.
      //
      // Relative (the default) cuts at this map's own quantiles, so every
      // map uses the whole ramp and a county's shade is its RANK among the
      // counties drawn beside it. Read alone, that is what you want: a map
      // of six small counties should not be six shades of pale.
      //
      // Absolute cuts at a fixed ladder, so a shade means a COUNT and means
      // the same count on every map. Read next to another map, that is what
      // you want, and relative shading actively misleads -- two maps top out
      // at the same dark blue whether the county behind it holds fifteen
      // stories or two hundred.
      //
      // The ladder is roughly logarithmic because the counts are: Missouri
      // counties run from 1 to 969 and the median is 44, so even steps would
      // put almost every county in the first band.
      const cuts = config.band_scale === "absolute"
        ? ABSOLUTE_BANDS
        : config.bands === "fixed" || values.length < steps * 2
          ? [2, 5, 9].slice(0, steps - 1)
          : Array.from({ length: steps - 1 }, (_, i) =>
              Math.max(1, Math.round(d3.quantile(values, (i + 1) / steps))))
              .reduce((kept, cut) => {
                if (!kept.length || cut > kept[kept.length - 1]) kept.push(cut);
                return kept;
              }, []);
      // SIZED AFTER THE CUTS, not from `steps`. `bandOf` can return at most
      // `cuts.length + 1`, and de-duplication drops any quantile that ties
      // with the one below it -- ten deciles over counties holding 1,1,1,2,2,4
      // survive as three or four distinct cuts. Built from `steps + 1` the
      // ramp then had shades no band could ever reach, so a map topped out at
      // a mid-tone and looked lighter than a map of smaller numbers whose
      // cuts happened to survive. The darkest band is now always `seqHigh`.
      const ramp = quantizeRamp(t.seqLow, t.seqHigh, cuts.length + 2);
      const bandOf = (n) => {
        if (!n) return 0;
        for (let i = 0; i < cuts.length; i += 1) if (n <= cuts[i]) return i + 1;
        return cuts.length + 1;
      };
      const shadeFor = (n) => (n ? ramp[bandOf(n)] : t.missing);
      // NOT `top`: that is a global in a browser (`window.top`), and
      // this only gets away with the name because it sits inside a
      // function. Hoisted to module scope it would throw
      // "Identifier 'top' has already been declared" and take the whole
      // chart library down with it -- which is exactly what happened to
      // a flat copy of this block.
      const highest = d3.max(values) || 0;
      // THE TOP BAND SAYS WHERE IT ENDS. "12+" hides the whole tail: the
      // reader cannot tell whether the darkest county holds 13 stories or
      // 204, which on this corpus is the difference between a flat map
      // and a very concentrated one.
      const bandLabels = ["0"].concat(
        cuts.map((cut, i) => {
          const from = i === 0 ? 1 : cuts[i - 1] + 1;
          return from === cut ? `${cut}` : `${from}–${cut}`;
        }),
        (() => {
          const from = cuts.length ? cuts[cuts.length - 1] + 1 : 1;
          return from >= highest ? `${from}` : `${from}–${highest}`;
        })()
      );

      const projection = d3.geoAlbersUsa().fitSize(
        [width, Math.round(width * 0.62)],
        { type: "FeatureCollection", features: framed });
      const path = d3.geoPath(projection);
      const height = Math.round(width * 0.62);
      const svg = d3.create("svg")
        .attr("width", width).attr("height", height)
        .attr("viewBox", [0, 0, width, height])
        .attr("style",
          'max-width:100%;height:auto;display:block;font-family:system-ui,' +
          '-apple-system,"Segoe UI",sans-serif;font-size:12px');
      const clipId = "dd-clip-" + Math.abs(width | 0) + "-" + shown.length;
      svg.append("clipPath").attr("id", clipId)
        .append("rect").attr("width", width).attr("height", height);
      const frame = svg.append("g").attr("clip-path", `url(#${clipId})`);

      const counties = frame.append("g").selectAll("path").data(shown).join("path")
        .attr("d", path)
        .attr("fill", (f) => shadeFor(byCounty.get(String(f.id))))
        .attr("stroke", t.boundary).attr("stroke-width", 0.6);

      const r = d3.scaleSqrt()
        .domain([0, d3.max(points, (p) => p.stories) || 1])
        .range([2.5, Math.max(9, width / 45)]);
      // Centrals outside the frame are counted, not drawn floating in
      // whitespace (the artifact listed them as "beyond the frame").
      const visible = focused
        ? points.filter((p) => inFrame.has(String(p.geoid || "").slice(0, 2)))
        : points;
      const beyond = points.length - visible.length;
      const placed = visible.filter(
        (p) => p.lon != null && p.lat != null && projection([p.lon, p.lat]));
      const dots = frame.append("g").selectAll("circle").data(placed).join("circle")
        .attr("transform", (p) => `translate(${projection([p.lon, p.lat])})`)
        .attr("r", (p) => r(p.stories))
        .attr("fill", (p) =>
          (t.points || t.series)[PRECISION[p.level] ?? 0] ||
          t.series[PRECISION[p.level] ?? 0])
        .attr("fill-opacity", 0.85)
        .attr("stroke", t.surface).attr("stroke-width", 1);

      el.replaceChildren(svg.node());
      const tip = tooltip(el);
      interactive(counties, tip, (f) => {
        const n = byCounty.get(String(f.id));
        return `<strong>${f.properties.name || f.id}</strong>` +
          tipRow("county FIPS", f.id) +
          // Every story that mentions a place in this county. The label
          // used to name the scope the shading was filtered to; there is
          // no filter now, so it says what it counts.
          tipRow("stories mentioning it", n || 0);
      }, { group: counties, related: (target, other) => target === other });
      interactive(dots, tip, (p) =>
        `<strong>${p.place || p.geoid}</strong>` +
        tipRow("FIPS", p.geoid) +
        tipRow("precision", p.level) +
        tipRow("stories", p.stories) +
        tipRow("publishers", p.publishers),
        { group: dots, related: (target, other) => target === other });

      // Two legends: the dot precisions and the shading thresholds.
      const legend = document.createElement("div");
      legend.className = "dd-legend";
      for (const level of ["place", "block", "county"]) {
        if (!placed.some((p) => p.level === level)) continue;
        const item = document.createElement("span");
        const dot = document.createElement("span");
        dot.className = "dd-swatch round";
        dot.style.background =
          (t.points || t.series)[PRECISION[level]] || t.series[PRECISION[level]];
        item.append(dot, level);
        legend.appendChild(item);
      }
      if (max) {
        // A STRIP, NOT A CHIP PER BAND. Every band used to carry its own
        // swatch AND its own text; at ten bands that is eleven labelled
        // chips like "143-208" laid across the top of the map, which does
        // not fit and wraps into a paragraph of numbers.
        //
        // A ramp is one object, so it is drawn as one: the swatches butt
        // together and only a few boundaries are written under it. That
        // is how a reader uses a choropleth key anyway -- to place a
        // shade between two ends, not to look up an exact band.
        const scale = document.createElement("span");
        scale.className = "dd-ramp";
        scale.append(document.createTextNode(
          "stories mentioning each county:"));

        // `0` is not a step of the ramp -- it is the absence of data --
        // so it keeps its own chip and its own word.
        const none = document.createElement("span");
        const noneSw = document.createElement("span");
        noneSw.className = "dd-swatch";
        noneSw.style.background = t.missing;
        none.append(noneSw, "none");
        scale.appendChild(none);

        const strip = document.createElement("span");
        strip.className = "dd-ramp-strip";
        // ONE SPECTRUM WITH A FEW MILESTONES. The bands are flush, so
        // the bar reads as a single scale rather than as ten categories
        // -- which is what the map is, a continuum cut into steps.
        //
        // A number under every block was the version that collided with
        // itself and told the reader far more than a key is for. Four
        // milestones sit where their value actually falls along the bar,
        // and every block still knows its own band on hover.
        const dataLabels = bandLabels.slice(1);
        const blocks = document.createElement("span");
        blocks.className = "dd-ramp-blocks";
        dataLabels.forEach((label, i) => {
          const sw = document.createElement("span");
          sw.className = "dd-ramp-block";
          sw.style.background = ramp[i + 1];
          sw.title = `${label} stories`;
          blocks.appendChild(sw);
        });

        // The marks: round numbers, placed where they actually fall.
        //
        // These used to be the raw quantile cuts -- 74, 142, 604 -- which
        // are an artefact of where the counties happened to land and mean
        // nothing to a reader. Rounded to 75 and 150 they are numbers
        // somebody can hold.
        //
        // THE BAR'S AXIS IS RANK, NOT VALUE, because the bands are
        // equal-count: each holds about a tenth of the counties, so the
        // tenth band spans 605 to 2,093 while the first spans 1 to 20.
        // A mark is therefore placed by finding the band its value falls
        // in and interpolating inside it, rather than by value across the
        // bar. That keeps every number true to the shade above it, which
        // is the only thing the key has to be right about.
        const marks = document.createElement("span");
        marks.className = "dd-ramp-marks";
        const edges = [0].concat(cuts, [highest]);
        const positionOf = (v) => {
          for (let i = 0; i < edges.length - 1; i += 1) {
            if (v <= edges[i + 1]) {
              const span = edges[i + 1] - edges[i];
              const within = span > 0 ? (v - edges[i]) / span : 0;
              return (i + within) / (edges.length - 1);
            }
          }
          return 1;
        };
        // 1, 2, 2.5, 5 and 7.5 times a power of ten: the numbers people
        // round to without being asked.
        const roundish = (n) => {
          if (n <= 10) return n;
          const power = Math.pow(10, Math.floor(Math.log10(n)));
          const steps = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
          let best = power;
          let gap = Infinity;
          steps.forEach((s) => {
            const candidate = s * power;
            if (Math.abs(candidate - n) < gap) {
              gap = Math.abs(candidate - n);
              best = candidate;
            }
          });
          return Math.round(best);
        };
        const mark = (value, text, align) => {
          const m = document.createElement("span");
          m.className = "dd-ramp-mark";
          m.textContent = text;
          m.style.left = `${positionOf(value) * 100}%`;
          if (align) m.dataset.align = align;
          marks.appendChild(m);
        };
        // A LADDER OF ROUND NUMBERS -- 50, 250, 500, 1,000 -- rather
        // than the quantile cuts, which are an artefact of where the
        // counties happened to land and mean nothing to a reader.
        // 1, 2 and 5 times a power of ten: 2, 5, 10, 20, 50, 100, 200.
        // These are the numbers people round to without being asked.
        // 2.5 was in here and produced "3" on a small map, which is not
        // a round number at that scale -- it is just a number.
        const ladder = [];
        for (let power = 1; power <= highest; power *= 10) {
          [1, 2, 5].forEach((m) => {
            const v = Math.round(m * power);
            // `<=` so the top of a small scale can be its own last rung:
            // a map whose busiest county has 200 stories should end at
            // 200, not at 100.
            if (v > 1 && v <= highest) ladder.push(v);
          });
        }
        ladder.sort((a, b) => a - b);
        // Placed by rank, so several round numbers can land inside one
        // band and pile up. Kept only where they are far enough apart to
        // read -- a crowded key is worse than a sparse one.
        // Wide enough that four marks is the usual outcome. This is a
        // small key on the edge of a map, not an axis: three or four
        // round numbers and the two ends is all it has room to say.
        // Wide enough that three or four marks is the usual outcome.
        // This is a small key on the edge of a map, not an axis.
        const APART = 0.2;
        // THE END MARK IS NOT AT 1.0. It sits where its own round value
        // falls -- 1,000 lands at 0.93 on the March map -- so measuring
        // the gap against the end of the BAR let the last interior mark
        // sit 0.19 away from it and the two labels ran together: "250"
        // and "1,000" rendered as "250,000".
        // THE END MARK HAS TO MEAN THE END. The largest round number
        // below the maximum can be far below it -- on a map peaking at
        // 2,093 the ladder offers 2,000, which is fine, but one peaking
        // at 12 offers 10 and one peaking at 190 offers 100, which
        // labels the darkest shade at half what it holds. Where the
        // nearest rung is not close, the maximum speaks for itself.
        const rung = ladder.filter((v) => v <= highest).pop() || highest;
        const topValue = rung >= highest * 0.6 ? rung : highest;
        const topAt = positionOf(topValue);
        let lastAt = 0;
        mark(1, "1", "start");
        ladder.forEach((v) => {
          const at = positionOf(v);
          if (v < topValue && at - lastAt >= APART && topAt - at >= APART) {
            lastAt = at;
            mark(v, v.toLocaleString());
          }
        });
        mark(topValue, topValue.toLocaleString(), "end");
        strip.append(blocks, marks);
        scale.appendChild(strip);
        legend.appendChild(scale);
      }
      if (beyond) {
        const note = document.createElement("span");
        note.className = "dd-beyond";
        note.textContent =
          `${beyond.toLocaleString()} central${beyond === 1 ? "" : "s"} beyond the frame`;
        legend.appendChild(note);
      }
      el.prepend(legend);
    }).catch(() => { el.textContent = "Boundary data unavailable."; });
  }

  // Two half-ramps meeting at the neutral midpoint (odd n keeps it center).
  function divergingRamp(low, mid, high, n) {
    const half = Math.floor(n / 2) + 1;
    const a = quantizeRamp(low, mid, half);
    const b = quantizeRamp(mid, high, half);
    return [...a, ...b.slice(1)];
  }

  function mount(el, config, rows, opts) {
    const draw = () => render(el, config, rows, opts);
    draw();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
    // The observer must measure what the renderer measures, or a pane that
    // widens redraws at a width the chart does not use.
    let width = roomFor(el);
    new ResizeObserver(() => {
      const room = roomFor(el);
      if (Math.abs(room - width) > 24) { width = room; draw(); }
    }).observe(el);
    return { redraw: draw };
  }

  // Reached by the test harness only. The colour decisions are the part
  // worth asserting on and the part a screenshot cannot check: whether
  // five ordered levels come out as a progression or as five unrelated
  // hues is a fact about these functions, not about the page.
  global.DatadeskChart = {
    render, mount, renderTable,
    __test: { scaleColors, colorScale, theme, quantizeRamp, sankeyGraph, orderRows, stackRows },
  };
})(window);
