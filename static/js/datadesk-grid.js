/* The builder's data grid: see, sort, filter, and select the data before
 * choosing a visualization.
 *
 * Operates on the rows the server produced (an aggregated pivot, or an
 * uploaded file), so it stays instant: the pivot has already reduced
 * 20k records to the few hundred groups a chart draws. Selection is a
 * view over those rows — filters, sort, and which columns participate —
 * and the chart preview redraws from the same view, so what you see in
 * the grid is exactly what the chart plots.
 */
(function (global) {
  "use strict";

  const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

  function columnType(rows, key) {
    const values = rows.map((r) => r[key]).filter((v) => v != null && v !== "");
    if (!values.length) return "empty";
    if (values.every((v) => typeof v === "number" || (v !== "" && !isNaN(+v)))) {
      // A FIPS/GEOID reads as a number but must never be averaged or
      // formatted with separators.
      return /(fips|geoid|zip|id)$/i.test(key) ? "code" : "number";
    }
    if (values.every((v) => ISO_DATE.test(String(v)))) return "date";
    return "text";
  }

  function summarize(rows, key, type) {
    const values = rows.map((r) => r[key]);
    const present = values.filter((v) => v != null && v !== "");
    const out = { nulls: values.length - present.length };
    if (type === "number") {
      const nums = present.map(Number);
      out.min = Math.min(...nums);
      out.max = Math.max(...nums);
      out.sum = nums.reduce((a, b) => a + b, 0);
    } else {
      out.distinct = new Set(present.map(String)).size;
    }
    return out;
  }

  function Grid(el, rows, options) {
    const opts = options || {};
    const columns = rows.length ? Object.keys(rows[0]) : [];
    const types = {};
    const stats = {};
    for (const c of columns) {
      types[c] = columnType(rows, c);
      stats[c] = summarize(rows, c, types[c]);
    }
    const state = {
      sort: null,
      desc: true,
      filters: {},
      selected: new Set(columns),
      limit: 200,
    };

    function view() {
      let out = rows;
      for (const [key, term] of Object.entries(state.filters)) {
        if (!term) continue;
        const t = String(term).toLowerCase();
        const range = t.match(/^([<>])\s*(-?[\d.]+)$/);
        out = out.filter((r) => {
          const v = r[key];
          if (range) {
            const n = Number(v);
            return range[1] === ">" ? n > +range[2] : n < +range[2];
          }
          return String(v ?? "").toLowerCase().includes(t);
        });
      }
      if (state.sort) {
        const key = state.sort;
        const numeric = types[key] === "number";
        out = out.slice().sort((a, b) => {
          const x = a[key], y = b[key];
          const cmp = numeric
            ? (Number(x) || 0) - (Number(y) || 0)
            : String(x ?? "").localeCompare(String(y ?? ""));
          return state.desc ? -cmp : cmp;
        });
      }
      return out;
    }

    function selectedRows() {
      const keep = [...state.selected];
      return view().map((r) => Object.fromEntries(keep.map((k) => [k, r[k]])));
    }

    function render() {
      const shown = view();
      el.replaceChildren();

      const bar = document.createElement("div");
      bar.className = "grid-bar";
      bar.innerHTML =
        `<span class="grid-count"><strong>${shown.length.toLocaleString()}</strong>` +
        ` of ${rows.length.toLocaleString()} rows · ` +
        `${state.selected.size} of ${columns.length} columns</span>`;
      const reset = document.createElement("button");
      reset.type = "button";
      reset.className = "linklike";
      reset.textContent = "reset";
      reset.onclick = () => {
        state.filters = {};
        state.sort = null;
        state.selected = new Set(columns);
        render();
        changed();
      };
      bar.appendChild(reset);
      el.appendChild(bar);

      const scroll = document.createElement("div");
      scroll.className = "grid-scroll";
      const table = document.createElement("table");
      table.className = "grid data-grid";

      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      const filterRow = document.createElement("tr");
      filterRow.className = "grid-filters";
      for (const c of columns) {
        const th = document.createElement("th");
        th.className = types[c] === "number" ? "count" : "";
        const label = document.createElement("button");
        label.type = "button";
        label.className = "grid-sort";
        label.innerHTML =
          `<input type="checkbox" ${state.selected.has(c) ? "checked" : ""}>` +
          `<span>${c}</span><em>${types[c]}</em>` +
          (state.sort === c ? `<i>${state.desc ? "↓" : "↑"}</i>` : "");
        label.querySelector("input").onclick = (e) => {
          e.stopPropagation();
          if (state.selected.has(c)) state.selected.delete(c);
          else state.selected.add(c);
          render();
          changed();
        };
        label.onclick = () => {
          state.desc = state.sort === c ? !state.desc : true;
          state.sort = c;
          render();
          changed();
        };
        th.appendChild(label);
        const hint = document.createElement("span");
        hint.className = "grid-stat";
        const st = stats[c];
        hint.textContent = types[c] === "number"
          ? `${st.min?.toLocaleString()}–${st.max?.toLocaleString()}`
          : `${st.distinct} distinct${st.nulls ? ` · ${st.nulls} blank` : ""}`;
        th.appendChild(hint);
        headRow.appendChild(th);

        const ft = document.createElement("th");
        const input = document.createElement("input");
        input.type = "search";
        input.placeholder = types[c] === "number" ? "> 100" : "contains…";
        input.value = state.filters[c] || "";
        input.oninput = () => {
          state.filters[c] = input.value;
          clearTimeout(input._t);
          input._t = setTimeout(() => { render(); changed(); }, 200);
        };
        ft.appendChild(input);
        filterRow.appendChild(ft);
      }
      thead.append(headRow, filterRow);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      for (const row of shown.slice(0, state.limit)) {
        const tr = document.createElement("tr");
        for (const c of columns) {
          const td = document.createElement("td");
          const v = row[c];
          td.textContent = v == null ? "" : String(v);
          if (types[c] === "number") td.className = "count";
          if (!state.selected.has(c)) td.classList.add("unselected");
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      scroll.appendChild(table);
      el.appendChild(scroll);

      if (shown.length > state.limit) {
        const more = document.createElement("p");
        more.className = "notice";
        more.textContent =
          `Showing the first ${state.limit} rows; the chart uses all ${shown.length.toLocaleString()}.`;
        el.appendChild(more);
      }
    }

    function changed() {
      if (opts.onChange) opts.onChange(selectedRows(), { types, state });
    }

    render();
    return { rows: selectedRows, types, state, refresh: render };
  }

  // Which chart kinds suit the current selection — the picker explains
  // rather than hides, so an unavailable form says what it needs.
  function suitability(rows, types) {
    const cols = Object.keys(types);
    const has = (t) => cols.filter((c) => types[c] === t);
    const numbers = has("number");
    const texts = has("text");
    const dates = has("date");
    const codes = has("code").concat(
      cols.filter((c) => /(fips|geoid)/i.test(c)));
    const latlon = cols.filter((c) => /^(lat|latitude)$/i.test(c)).length &&
                   cols.filter((c) => /^(lon|lng|longitude)$/i.test(c)).length;
    const need = (ok, why) => ({ ok, why });
    return {
      table: need(true, ""),
      bar: need(numbers.length && (texts.length || dates.length),
        "needs a category column and a number column"),
      line: need(numbers.length && dates.length,
        "needs a date column and a number column"),
      area: need(numbers.length && dates.length,
        "needs a date column and a number column"),
      scatter: need(numbers.length >= 2, "needs two number columns"),
      donut: need(numbers.length && texts.length,
        "needs a category column and a number column"),
      chord: need(texts.length >= 2 && numbers.length,
        "needs two category columns and a number column"),
      arc: need(texts.length >= 2, "needs two category columns"),
      choropleth: need(codes.length && numbers.length,
        "needs a FIPS/GEOID column and a number column"),
      points: need(Boolean(latlon), "needs latitude and longitude columns"),
      storymap: need(false, "built from a corpus story-map spec"),
    };
  }

  global.DatadeskGrid = { Grid, suitability, columnType };
})(window);
