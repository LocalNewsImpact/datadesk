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
    seqLow: "#104281", seqHigh: "#9ec5f4",
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
      light: { ...LIGHT },
      dark: { ...DARK },
    },
    lnic: {
      light: {
        ...LIGHT,
        series: ["#00618f", "#eb6834", "#59bbeb", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
        seqLow: "#d3ecfa", seqHigh: "#003a56",
        divLow: "#003a56", divMid: "#f0efec", divHigh: "#8f1d1d",
      },
      dark: {
        ...DARK,
        series: ["#1d6f9e", "#d95926", "#2f9ecf", "#c98500",
                 "#d55181", "#008300", "#9085e9", "#e66767"],
        seqLow: "#0e4a6d", seqHigh: "#9fd6f2",
        divLow: "#9fd6f2", divMid: "#383835", divHigh: "#e66767",
      },
    },
    mizzou: {
      light: {
        ...LIGHT,
        series: ["#d9a018", "#a31414", "#2a78d6", "#1baf7a",
                 "#e87ba4", "#008300", "#4a3aa7", "#eb6834"],
        seqLow: "#f7e6bd", seqHigh: "#6b4d05",
        divLow: "#184f95", divMid: "#f0efec", divHigh: "#7a0f0f",
      },
      dark: {
        ...DARK,
        series: ["#c98500", "#c23a3a", "#3987e5", "#d95926",
                 "#199e70", "#9085e9", "#d55181", "#008300"],
        seqLow: "#5c4304", seqHigh: "#f0d488",
        divLow: "#9ec5f4", divMid: "#383835", divHigh: "#e66767",
      },
    },
    rji: {
      light: {
        ...LIGHT,
        series: ["#1c5e90", "#d9a018", "#1baf7a", "#eb6834",
                 "#2a78d6", "#e87ba4", "#008300", "#4a3aa7"],
        seqLow: "#d4e5f2", seqHigh: "#0d3350",
        divLow: "#0d3350", divMid: "#f0efec", divHigh: "#8f1d1d",
      },
      dark: {
        ...DARK,
        series: ["#2f7cb8", "#c98500", "#199e70", "#d95926",
                 "#3987e5", "#d55181", "#008300", "#9085e9"],
        seqLow: "#123a5c", seqHigh: "#a8cce8",
        divLow: "#a8cce8", divMid: "#383835", divHigh: "#e66767",
      },
    },
  };
  const DEFAULT_THEME = "lnic";

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
  function boundaries(base, level, ids) {
    const spec = GEO_LEVELS[level] || GEO_LEVELS.states;
    if (!spec.perState) {
      return fetchJSON(base + spec.file)
        .then((topo) => toFeatures(topo, spec.object));
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

  function render(el, config, rows, opts) {
    const t = theme(config.theme);
    el.textContent = "";
    if (!rows || !rows.length) {
      el.textContent = "No data.";
      return;
    }
    const Plot = global.Plot;
    const width = Math.max(320, el.clientWidth || 640);
    const kind = config.kind || "table";

    if (kind === "table") return renderTable(el, rows);
    if (kind === "choropleth" || kind === "points") {
      return renderMap(el, config, rows, opts, t, width);
    }
    if (kind === "donut") return renderDonut(el, config, rows, t, width);
    if (kind === "chord") return renderChord(el, config, rows, t, width);
    if (kind === "arc") return renderArc(el, config, rows, t, width);

    const x = config.x, y = config.y, series = config.series;
    if (!x || !y) { el.textContent = "Pick the x and y columns."; return; }
    rows = coerce(coerce(rows.slice(), y), x);

    let domain = [], folded = false;
    if (series) {
      ({ rows, domain, folded } = foldSeries(
        rows, series, kind === "scatter" ? CAP_ALLPAIRS : CAP_ADJACENT));
    }
    const color = series ? colorScale(domain, t, folded) : undefined;
    const stroke1 = t.series[0];
    const marks = baseMarks(Plot, t);
    const horizontal = kind === "bar" && config.horizontal;
    const common = { tip: true };
    const sort = config.sort === "y"
      ? (horizontal ? { y: "-x" } : { x: "-y" })
      : undefined;

    let marginRight;
    if (kind === "bar") {
      const enc = horizontal
        ? { y: x, x: y, fill: series || stroke1, sort, inset: 0.5 }
        : { x, y, fill: series || stroke1, sort, inset: 0.5 };
      if (series && config.stacked === false) {
        enc[horizontal ? "fy" : "fx"] = enc[horizontal ? "y" : "x"];
        enc[horizontal ? "y" : "x"] = series;
      }
      marks.push((horizontal ? Plot.barX : Plot.barY)(rows, { ...enc, ...common, rx: 2 }));
      marks.push(horizontal ? Plot.ruleX([0], { stroke: t.boundary }) : Plot.ruleY([0], { stroke: t.boundary }));
    } else if (kind === "line" || kind === "area") {
      const enc = { x, y, ...common };
      if (series) enc.stroke = series; else enc.stroke = stroke1;
      if (kind === "area") {
        const area = { x, y, fillOpacity: 0.25 };
        if (series) area.fill = series; else area.fill = stroke1;
        marks.push(Plot.areaY(rows, area));
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

    let xScale = { label: config.xlabel || undefined, tickSize: 0 };
    if (kind === "bar" && !config.sort) {
      const axis = horizontal ? y : x;
      const order = [];
      for (const r of rows) if (!order.includes(r[axis])) order.push(r[axis]);
      if (horizontal) var yDomain = order; else xScale.domain = order;
    }

    const plot = Plot.plot({
      width,
      height: 420,
      marginRight,
      style: { background: "transparent", color: t.ink,
               fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' },
      color,
      x: xScale,
      y: { label: config.ylabel || undefined, tickSize: 0, grid: false,
           ...(typeof yDomain !== "undefined" ? { domain: yDomain } : {}) },
      marks,
    });
    el.appendChild(plot);
  }

  function renderTable(el, rows) {
    const cols = Object.keys(rows[0]);
    const table = document.createElement("table");
    table.className = "dd-table";
    table.innerHTML = "<thead><tr>" +
      cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead>";
    const tbody = document.createElement("tbody");
    for (const row of rows.slice(0, 500)) {
      const tr = document.createElement("tr");
      for (const c of cols) {
        const td = document.createElement("td");
        td.textContent = row[c] ?? "";
        if (isFiniteNumber(row[c])) td.className = "num";
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    el.replaceChildren(table);
  }

  function renderMap(el, config, rows, opts, t, width) {
    const Plot = global.Plot;
    const level = GEO_LEVELS[config.geo_level] ? config.geo_level : "states";
    const idLength = GEO_LEVELS[level].idLength;
    const joinIds = config.geo_join
      ? rows.map((r) => pad(r[config.geo_join], idLength))
      : [];
    boundaries(opts.geoBase, level, joinIds).then((features) => {
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
  function quantizeRamp(low, high, n) {
    const parse = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [a, b] = [parse(low), parse(high)];
    return Array.from({ length: n }, (_, i) => {
      const k = i / (n - 1);
      const rgb = a.map((v, j) => Math.round(v + (b[j] - v) * k));
      return "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("");
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
    svg.append("g").selectAll("path").data(arcs).join("path")
      .attr("d", shape)
      .attr("fill", (d, i) => colors[i])
      .attr("stroke", t.surface).attr("stroke-width", 2)
      .append("title").text((d) =>
        `${d.data[0]}: ${d.data[1].toLocaleString()} ` +
        `(${(100 * d.data[1] / total).toFixed(1)}%)`);
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
  }

  // Shared: fold a from/to edge list to at most eight named groups.
  function edgeGroups(rows, from, to) {
    const order = [];
    for (const r of rows) {
      for (const v of [r[from], r[to]]) {
        if (v != null && v !== "" && !order.includes(v)) order.push(v);
      }
    }
    if (order.length <= 8) return { names: order, fold: (v) => v };
    const keep = new Set(order.slice(0, 8));
    return {
      names: [...order.slice(0, 8), "Other"],
      fold: (v) => (keep.has(v) ? v : "Other"),
    };
  }

  // Flows between groups. Identity is carried by the labels on every
  // group arc — color is redundant there, so the eight-slot order holds.
  function renderChord(el, config, rows, t, width) {
    const d3 = global.d3;
    const { from, to, value } = config;
    if (!from || !to || !value) {
      el.textContent = "Pick the from, to, and value columns."; return;
    }
    rows = coerce(rows.slice(), value);
    const { names, fold } = edgeGroups(rows, from, to);
    const colors = slotColors(names, t);
    const index = new Map(names.map((n, i) => [n, i]));
    const matrix = names.map(() => names.map(() => 0));
    for (const r of rows) {
      const a = index.get(fold(r[from])), b = index.get(fold(r[to]));
      if (a != null && b != null) matrix[a][b] += +r[value] || 0;
    }
    const size = Math.min(width, 560);
    const R = size / 2 - 70;
    const chords = d3.chord().padAngle(0.04)
      .sortSubgroups(d3.descending)(matrix);
    const svg = svgRoot(size, size, t);
    const group = svg.append("g").selectAll("g").data(chords.groups).join("g");
    group.append("path")
      .attr("d", d3.arc().innerRadius(R).outerRadius(R + 12))
      .attr("fill", (d) => colors[d.index])
      .append("title").text((d) => `${names[d.index]}: ${d.value.toLocaleString()}`);
    group.append("text")
      .each((d) => { d.angle = (d.startAngle + d.endAngle) / 2; })
      .attr("transform", (d) =>
        `rotate(${(d.angle * 180) / Math.PI - 90}) translate(${R + 18})` +
        (d.angle > Math.PI ? " rotate(180)" : ""))
      .attr("text-anchor", (d) => (d.angle > Math.PI ? "end" : "start"))
      .attr("dy", "0.35em").attr("fill", "currentColor")
      .text((d) => names[d.index]);
    svg.append("g").selectAll("path").data(chords).join("path")
      .attr("d", d3.ribbon().radius(R - 2))
      .attr("fill", (d) => colors[d.source.index])
      .attr("fill-opacity", 0.7)
      .attr("stroke", t.surface).attr("stroke-width", 0.5)
      .append("title").text((d) =>
        `${names[d.source.index]} → ${names[d.target.index]}: ` +
        d.source.value.toLocaleString() +
        (d.source.index !== d.target.index
          ? `
${names[d.target.index]} → ${names[d.source.index]}: ` +
            d.target.value.toLocaleString()
          : ""));
    el.replaceChildren(svg.node());
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
      .attr("stroke-width", (r) => w(value ? +r[value] || 0 : 1))
      .append("title").text((r) =>
        `${r[from]} → ${r[to]}` + (value ? `: ${r[value]}` : ""));
    const node = svg.append("g").selectAll("g").data(names).join("g")
      .attr("transform", (n) => `translate(${xAt(n)},${Y})`);
    node.append("circle").attr("r", 5)
      .attr("fill", (n, i) => colors[i])
      .attr("stroke", t.surface).attr("stroke-width", 1.5);
    node.append("text").attr("transform", "rotate(35)")
      .attr("x", 4).attr("y", 14).attr("fill", "currentColor")
      .text((n) => n);
    el.replaceChildren(svg.node());
    void arcSpan;
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
    let width = el.clientWidth;
    new ResizeObserver(() => {
      if (Math.abs(el.clientWidth - width) > 24) { width = el.clientWidth; draw(); }
    }).observe(el);
    return { redraw: draw };
  }

  global.DatadeskChart = { render, mount, renderTable };
})(window);
