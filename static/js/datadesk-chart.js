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
      light: { ...LIGHT, points: ["#eb6834", "#008300", "#4a3aa7"] },
      dark: { ...DARK, points: ["#d95926", "#008300", "#9085e9"] },
    },
    lnic: {
      light: {
        ...LIGHT,
        series: ["#00618f", "#eb6834", "#59bbeb", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
        seqLow: "#d3ecfa", seqHigh: "#003a56",
        divLow: "#003a56", divMid: "#f0efec", divHigh: "#8f1d1d",
        // Dots must not read as another step of the shading ramp.
        points: ["#eb6834", "#008300", "#4a3aa7"],
      },
      dark: {
        ...DARK,
        series: ["#1d6f9e", "#d95926", "#2f9ecf", "#c98500",
                 "#d55181", "#008300", "#9085e9", "#e66767"],
        seqLow: "#0e4a6d", seqHigh: "#9fd6f2",
        divLow: "#9fd6f2", divMid: "#383835", divHigh: "#e66767",
        points: ["#d95926", "#008300", "#9085e9"],
      },
    },
    mizzou: {
      light: {
        ...LIGHT,
        series: ["#d9a018", "#a31414", "#2a78d6", "#1baf7a",
                 "#e87ba4", "#008300", "#4a3aa7", "#eb6834"],
        seqLow: "#f7e6bd", seqHigh: "#6b4d05",
        divLow: "#184f95", divMid: "#f0efec", divHigh: "#7a0f0f",
        points: ["#a31414", "#2a78d6", "#008300"],
      },
      dark: {
        ...DARK,
        series: ["#c98500", "#c23a3a", "#3987e5", "#d95926",
                 "#199e70", "#9085e9", "#d55181", "#008300"],
        seqLow: "#5c4304", seqHigh: "#f0d488",
        divLow: "#9ec5f4", divMid: "#383835", divHigh: "#e66767",
        points: ["#c23a3a", "#3987e5", "#008300"],
      },
    },
    rji: {
      light: {
        ...LIGHT,
        series: ["#1c5e90", "#d9a018", "#1baf7a", "#eb6834",
                 "#2a78d6", "#e87ba4", "#008300", "#4a3aa7"],
        seqLow: "#d4e5f2", seqHigh: "#0d3350",
        divLow: "#0d3350", divMid: "#f0efec", divHigh: "#8f1d1d",
        points: ["#d9a018", "#e34948", "#008300"],
      },
      dark: {
        ...DARK,
        series: ["#2f7cb8", "#c98500", "#199e70", "#d95926",
                 "#3987e5", "#d55181", "#008300", "#9085e9"],
        seqLow: "#123a5c", seqHigh: "#a8cce8",
        divLow: "#a8cce8", divMid: "#383835", divHigh: "#e66767",
        points: ["#c98500", "#e66767", "#008300"],
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

  function render(el, config, rows, opts) {
    const t = theme(config.theme);
    el.textContent = "";
    const Plot = global.Plot;
    const width = Math.max(320, el.clientWidth || 640);
    const kind = config.kind || "table";
    // The story map's payload is an object of layers, not a row array;
    // every other form takes rows and needs at least one.
    if (kind !== "storymap" && (!rows || !rows.length)) {
      el.textContent = "No data.";
      return;
    }

    if (kind === "table") return renderTable(el, rows, opts && opts.credits);
    if (kind === "choropleth" || kind === "points") {
      return renderMap(el, config, rows, opts, t, width);
    }
    if (kind === "storymap") return renderStoryMap(el, config, rows, opts, t, width);
    if (kind === "donut") return renderDonut(el, config, rows, t, width);
    if (kind === "chord") return renderChord(el, config, rows, t, width);
    if (kind === "arc") return renderArc(el, config, rows, t, width);

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
      color = colorScale(domain, t, folded);
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
      const enc = horizontal
        ? { y: x, x: y, fill: series || stroke1, inset: 0.5 }
        : { x, y, fill: series || stroke1, sort, inset: 0.5 };
      // Plot stacks each column in that column's own row order, so the
      // segments would sit in a different sequence per category. Pinning
      // the order to the colour domain makes the stack readable across
      // columns: the same need is always the same band.
      if (series) enc.order = domain;
      if (series && config.stacked === false) {
        enc[horizontal ? "fy" : "fx"] = enc[horizontal ? "y" : "x"];
        enc[horizontal ? "y" : "x"] = series;
      }
      // "percent" turns a stack into a composition: each column fills the
      // axis and the series read as shares.
      if (config.stack === "percent" && series) {
        enc.offset = "expand";
      }
      marks.push((horizontal ? Plot.barX : Plot.barY)(rows, { ...enc, ...common, rx: 2 }));
      marks.push(horizontal ? Plot.ruleX([0], { stroke: t.boundary }) : Plot.ruleY([0], { stroke: t.boundary }));
    } else if (kind === "line" || kind === "area") {
      const enc = { x, y, ...common };
      if (series) enc.stroke = series; else enc.stroke = stroke1;
      if (kind === "area") {
        const area = { x, y, fillOpacity: 0.25 };
        if (series) { area.fill = series; area.order = domain; }
        else area.fill = stroke1;
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

    const percentStack = kind === "bar" && config.stack === "percent" && series;
    let xScale = { label: config.xlabel || undefined, tickSize: 0 };
    let yDomain;
    let marginLeft;
    let marginBottom;
    let height = 420;
    if (kind === "bar") {
      // The category axis: y when horizontal, x otherwise.
      const axis = horizontal ? x : x;
      const order = [];
      for (const r of rows) if (!order.includes(r[axis])) order.push(r[axis]);
      if (config.sort === "y") {
        // A percent stack is all 100% wide, so "by value" means by the
        // category's total — otherwise the ordering says nothing.
        const totals = new Map();
        for (const r of rows) {
          totals.set(r[axis], (totals.get(r[axis]) || 0) + (+r[y] || 0));
        }
        order.sort((a, b) => (totals.get(b) || 0) - (totals.get(a) || 0));
      }
      if (horizontal) {
        yDomain = order;
        // Room for the longest label, and a band tall enough to read.
        const longest = Math.max(...order.map((v) => String(v ?? "").length));
        marginLeft = Math.min(220, 16 + 6.6 * longest);
        height = Math.max(320, Math.min(1600, order.length * 22 + 90));
      } else {
        xScale.domain = order;
        if (order.length > 8) {
          // Upright labels would collide; rotate and reserve the depth.
          const longest = Math.max(...order.map((v) => String(v ?? "").length));
          xScale.tickRotate = -45;
          marginBottom = Math.min(160, 40 + 5.2 * longest);
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

  function exportBar(el, rows, slug) {
    const bar = document.createElement("div");
    bar.className = "dd-export";

    const csv = document.createElement("button");
    csv.type = "button";
    csv.textContent = "Download CSV";
    csv.title = "Flourish takes a CSV upload";
    csv.addEventListener("click", () =>
      download(asDelimited(rows, ","), (slug || "data") + ".csv", "text/csv"));

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

    bar.append(csv, copy, note);
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

  function oneTable(rows) {
    // The first row that is actually an object. A list of bare numbers or
    // strings has no columns to name, and keying off row zero regardless
    // gave `0, 1, 2` as headers.
    const first = rows.find((r) => r && typeof r === "object" && !Array.isArray(r));
    const cols = first ? Object.keys(first) : null;
    const table = document.createElement("table");
    table.className = "dd-table";
    if (cols) {
      table.innerHTML = "<thead><tr>" +
        cols.map((c) => `<th>${c}</th>`).join("") + "</tr></thead>";
    }
    const tbody = document.createElement("tbody");
    for (const row of rows.slice(0, 500)) {
      const tr = document.createElement("tr");
      for (const c of cols || [null]) {
        const value = c === null ? row : row?.[c];
        const td = document.createElement("td");
        td.textContent = value ?? "";
        if (isFiniteNumber(value)) td.className = "num";
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
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
  function renderTable(el, data, credits, takeaway) {
    const groups = tablesIn(data);
    const slug = el.id.replace(/^dd-chart-/, "");
    if (takeaway) takeaway.hidden = false;
    if (!groups.length) {
      const note = document.createElement("p");
      note.className = "dd-note";
      note.textContent = "This visual has no tabular data to show.";
      el.replaceChildren(note);
      if (takeaway) el.prepend(takeaway);
      return;
    }
    el.replaceChildren();
    if (takeaway) el.appendChild(takeaway);
    for (const { name, rows } of groups) {
      if (name && groups.length > 1) {
        const heading = document.createElement("h3");
        heading.className = "dd-table-name";
        heading.textContent = name;
        el.appendChild(heading);
      }
      el.appendChild(oneTable(rows));
      // One export per list rather than one for the page: a reader who
      // wants the county totals should not have to take the point layer
      // with them to get it.
      // Every row, not the five hundred shown: somebody exporting wants
      // the data, and the cap above is about what a page can render.
      exportBar(el, rows, name ? `${slug}-${name}` : slug);
    }
    creditLine(el, credits);
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
  function quantizeRamp(low, high, n) {
    const parse = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [a, b] = [parse(low), parse(high)];
    return Array.from({ length: n }, (_, i) => {
      const k = i / (n - 1);
      const rgb = a.map((v, j) => Math.round(v + (b[j] - v) * k));
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
  function edgeGroups(rows, from, to, fixed) {
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
    const keep = new Set(order.slice(0, 8));
    return {
      names: [...order.slice(0, 8), "Other"],
      fold: (v) => (keep.has(v) ? v : "Other"),
    };
  }

  // Flows between groups. Identity is carried by the labels on every
  // group arc — color is redundant there, so the eight-slot order holds.
  // A small stable hash, so the ids a chart mints for its own defs do not
  // collide with another chart's on the same page.
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
    const { names, fold } = edgeGroups(rows, from, to, fixed && fixed.order);
    const colors = fixed
      ? names.map((n, i) => {
          const at = fixed.order.indexOf(n);
          // Anything the vocabulary does not name falls back to a slot,
          // taken from the end so it cannot collide with a pinned hue.
          return at >= 0 ? fixed.colors[at] : slotColors(names, t)[i];
        })
      : slotColors(names, t);
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
      .attr("fill", (d) => colors[d.index]);
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
      .attr("fill", (d) => colors[d.source.index])
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
      ...areas.map((a) => String(a.county || "")),
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
          const st = String(a.county).slice(0, 2);
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
      const byCounty = new Map(areas.map((a) => [String(a.county), a.stories]));
      const max = d3.max(areas, (a) => a.stories) || 0;
      const ramp = quantizeRamp(t.seqLow, t.seqHigh, 5);
      // Bands are quartiles of the counties that actually have stories,
      // so the map stays informative whether it is a 500-article sample
      // or the whole corpus. config.bands: "fixed" restores the March
      // map's 1-2 / 3-5 / 6-9 / 10+ cuts.
      const values = areas.map((a) => a.stories).filter((n) => n > 0).sort(d3.ascending);
      const cuts = config.bands === "fixed" || values.length < 8
        ? [2, 5, 9]
        : [0.25, 0.5, 0.75].map((q) => Math.max(1, Math.round(d3.quantile(values, q))));
      const bandOf = (n) =>
        !n ? 0 : n <= cuts[0] ? 1 : n <= cuts[1] ? 2 : n <= cuts[2] ? 3 : 4;
      const shadeFor = (n) => (n ? ramp[bandOf(n)] : t.missing);
      const bandLabels = [
        "0",
        cuts[0] === 1 ? "1" : `1\u2013${cuts[0]}`,
        cuts[1] === cuts[0] + 1 ? `${cuts[1]}` : `${cuts[0] + 1}\u2013${cuts[1]}`,
        cuts[2] === cuts[1] + 1 ? `${cuts[2]}` : `${cuts[1] + 1}\u2013${cuts[2]}`,
        `${cuts[2] + 1}+`,
      ];

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
          tipRow(`${payload.meta && payload.meta.area_scope || "place-set"}` +
            " stories", n || 0);
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
        const scale = document.createElement("span");
        scale.className = "dd-ramp";
        scale.append(document.createTextNode(
          ((payload.meta && payload.meta.area_scope) || "place-set") +
          " stories touching each county:"));
        bandLabels.map((label, i) => [label, i ? ramp[i] : t.missing])
          .forEach(([label, color]) => {
          const chip = document.createElement("span");
          const sw = document.createElement("span");
          sw.className = "dd-swatch";
          sw.style.background = color;
          chip.append(sw, label);
          scale.appendChild(chip);
        });
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
    let width = el.clientWidth;
    new ResizeObserver(() => {
      if (Math.abs(el.clientWidth - width) > 24) { width = el.clientWidth; draw(); }
    }).observe(el);
    return { redraw: draw };
  }

  global.DatadeskChart = { render, mount, renderTable };
})(window);
