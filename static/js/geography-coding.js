/* The coder's map for the geography question (G-WP2).
 *
 * The coder says what kind of place the story is about, then marks it:
 *
 *   P  one pin
 *   R  counties clicked, and/or towns pinned
 *   S  a state, from the menu
 *   U  N  nothing to mark
 *
 * A pin is accepted only when the map is zoomed in far enough that one
 * town fills the view (data-pin-view-km): dropped on a whole-state view
 * it lands wherever the cursor was and resolves to a town nobody chose.
 *
 * Every pin is resolved by the server (data-where-url) and the result is
 * shown under the map, so what the coder sees is what will be recorded.
 * The server resolves it again on save; nothing here is trusted.
 *
 * Drawn with the vendored d3 and topojson-client over the TIGER files in
 * static/geo: no tiles, no calls to anybody else.
 */
(function () {
  "use strict";

  var root = document.getElementById("geo-map");
  var form = document.getElementById("geo-form");
  if (!root || !form || !window.d3 || !window.topojson) return;

  var d3 = window.d3;
  var topojson = window.topojson;
  var base = root.dataset.geoBase;
  var startState = root.dataset.startState || "29";
  var placeStates = (root.dataset.placeStates || "").split(",").filter(Boolean);
  var pinViewKm = parseFloat(root.dataset.pinViewKm) || 80;
  var whereUrl = root.dataset.whereUrl;
  var findUrl = root.dataset.findUrl;

  var pinsField = document.getElementById("geo-pins");
  var countiesField = document.getElementById("geo-counties");
  var scaleNote = document.getElementById("geo-scale");
  var answerList = document.getElementById("geo-answer");
  var mapWrap = document.querySelector(".geo-map-wrap");
  var stateLabel = document.querySelector(".geo-state");
  var modeGroup = document.querySelector(".geo-mode");
  var modeCounties = document.querySelector(".geo-mode-counties");
  var modePins = document.querySelector(".geo-mode-pins");

  var answerClass = "";
  var clickMode = "counties"; // R only: what a click on the map does
  var pins = []; // {lat, lon, view_km, label}
  var counties = new Map(); // geoid -> name

  // ------------------------------------------------------------ the map

  // The map's box is hidden until a class that needs it is chosen, and a
  // hidden box has no width; the form it sits in has the same one.
  var width = root.clientWidth || form.clientWidth || 600;
  var height = Math.max(360, Math.round(width * 0.72));
  var svg = d3
    .select(root)
    .append("svg")
    .attr("viewBox", [0, 0, width, height])
    .attr("width", "100%")
    .attr("height", height);
  var layer = svg.append("g");
  var gCounties = layer.append("g").attr("class", "geo-counties");
  var gPlaces = layer.append("g").attr("class", "geo-places");
  var gStates = layer.append("g").attr("class", "geo-states");
  var gLabels = layer.append("g").attr("class", "geo-labels");
  var gPins = layer.append("g").attr("class", "geo-pins");

  var projection = d3.geoMercator();
  var path = d3.geoPath(projection);
  var transform = d3.zoomIdentity;
  var countyFeatures = [];
  var placeFeatures = new Map(); // geoid -> feature
  var placeLabels = []; // {name, x, y}

  var zoom = d3
    .zoom()
    .scaleExtent([1, 600])
    .on("zoom", function (event) {
      transform = event.transform;
      layer.attr("transform", transform);
      redrawScale();
    });
  svg.call(zoom).on("dblclick.zoom", null);

  function viewKm() {
    // The visible width at mid-height, on the ground.
    var y = height / 2;
    var left = projection.invert(transform.invert([0, y]));
    var right = projection.invert(transform.invert([width, y]));
    if (!left || !right) return Infinity;
    return d3.geoDistance(left, right) * 6371;
  }

  function redrawScale() {
    var km = viewKm();
    var k = transform.k;
    // Strokes and pins keep their size on screen as the map scales.
    gPins.selectAll("circle").attr("r", 6 / k).attr("stroke-width", 2 / k);
    var close = km <= pinViewKm * 1.5;
    gPlaces.attr("display", km <= 250 ? null : "none");
    drawLabels(close);
    if (answerClass === "P" || (answerClass === "R" && clickMode === "pins")) {
      scaleNote.textContent =
        km <= pinViewKm
          ? "Close enough to drop a pin (" + Math.round(km) + " km across)."
          : "Zoom in to drop a pin: the map is " +
            Math.round(km) +
            " km across, and a pin needs " +
            pinViewKm +
            " or less.";
    } else if (answerClass === "R") {
      scaleNote.textContent = "Click a county to add it; click it again to take it off.";
    } else {
      scaleNote.textContent = "";
    }
  }

  function drawLabels(show) {
    if (!show) {
      gLabels.selectAll("text").remove();
      return;
    }
    var k = transform.k;
    var x0 = transform.invertX(0);
    var x1 = transform.invertX(width);
    var y0 = transform.invertY(0);
    var y1 = transform.invertY(height);
    var visible = placeLabels
      .filter(function (d) {
        return d.x >= x0 && d.x <= x1 && d.y >= y0 && d.y <= y1;
      })
      .slice(0, 120);
    gLabels
      .selectAll("text")
      .data(visible, function (d) {
        return d.geoid;
      })
      .join("text")
      .attr("x", function (d) {
        return d.x;
      })
      .attr("y", function (d) {
        return d.y;
      })
      .attr("font-size", 11 / k)
      .text(function (d) {
        return d.name;
      });
  }

  function zoomTo(feature) {
    var b = path.bounds(feature);
    var dx = b[1][0] - b[0][0];
    var dy = b[1][1] - b[0][1];
    var x = (b[0][0] + b[1][0]) / 2;
    var y = (b[0][1] + b[1][1]) / 2;
    var k = Math.min(600, 0.8 / Math.max(dx / width, dy / height));
    svg
      .transition()
      .duration(500)
      .call(
        zoom.transform,
        d3.zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-x, -y)
      );
  }

  d3.json(base + "counties-10m.json").then(function (us) {
    var states = topojson.feature(us, us.objects.states).features;
    var start = states.find(function (s) {
      return String(s.id).padStart(2, "0") === startState;
    });
    projection.fitSize([width, height], start || topojson.feature(us, us.objects.nation));
    countyFeatures = topojson.feature(us, us.objects.counties).features;
    gCounties
      .selectAll("path")
      .data(countyFeatures)
      .join("path")
      .attr("d", path)
      .attr("data-geoid", function (d) {
        return String(d.id).padStart(5, "0");
      })
      .on("click", function (event, d) {
        if (answerClass !== "R" || clickMode !== "counties") return;
        event.stopPropagation();
        toggleCounty(String(d.id).padStart(5, "0"), d.properties.name);
      });
    gStates
      .append("path")
      .datum(topojson.mesh(us, us.objects.states, function (a, b) {
        return a !== b;
      }))
      .attr("d", path);
    redrawScale();
    // The start state's places first, the others after.
    var order = [startState].concat(
      placeStates.filter(function (s) {
        return s !== startState;
      })
    );
    order
      .filter(function (s) {
        return placeStates.indexOf(s) !== -1;
      })
      .reduce(function (chain, state) {
        return chain.then(function () {
          return loadPlaces(state);
        });
      }, Promise.resolve());
  });

  function loadPlaces(state) {
    return d3.json(base + "places/" + state + ".json").then(function (topo) {
      var features = topojson.feature(topo, topo.objects.places).features;
      features.forEach(function (f) {
        placeFeatures.set(f.properties.GEOID, f);
        var c = path.centroid(f);
        if (isFinite(c[0])) {
          placeLabels.push({ geoid: f.properties.GEOID, name: f.properties.NAME, x: c[0], y: c[1] });
        }
      });
      gPlaces
        .append("g")
        .selectAll("path")
        .data(features)
        .join("path")
        .attr("d", path);
      redrawScale();
    });
  }

  // ------------------------------------------------------- the answer

  svg.on("click", function (event) {
    var wantsPin =
      answerClass === "P" || (answerClass === "R" && clickMode === "pins");
    if (!wantsPin) return;
    var km = viewKm();
    if (km > pinViewKm) {
      scaleNote.textContent =
        "Zoom in further before dropping a pin: one town should fill the map.";
      return;
    }
    var point = transform.invert(d3.pointer(event, svg.node()));
    var lonlat = projection.invert(point);
    if (!lonlat) return;
    var pin = { lat: lonlat[1], lon: lonlat[0], view_km: km, label: "…" };
    if (answerClass === "P") pins = [pin];
    else pins.push(pin);
    resolvePin(pin);
    render();
  });

  function resolvePin(pin) {
    var url = whereUrl + "?lat=" + pin.lat + "&lon=" + pin.lon;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (where) {
        if (!where.county) {
          pin.label = "Not in a U.S. county: move it";
        } else {
          pin.label =
            (where.place_name ? where.place_name + ", " : "Outside any town, ") +
            where.county_name +
            " County";
        }
        render();
      });
  }

  function toggleCounty(geoid, name) {
    if (counties.has(geoid)) counties.delete(geoid);
    else counties.set(geoid, name);
    render();
  }

  function render() {
    gPins
      .selectAll("circle")
      .data(pins)
      .join("circle")
      .attr("cx", function (d) {
        return projection([d.lon, d.lat])[0];
      })
      .attr("cy", function (d) {
        return projection([d.lon, d.lat])[1];
      })
      .on("click", function (event, d) {
        event.stopPropagation();
        pins = pins.filter(function (p) {
          return p !== d;
        });
        render();
      });
    gCounties.selectAll("path").classed("chosen", function (d) {
      return counties.has(String(d.id).padStart(5, "0"));
    });
    redrawScale();

    answerList.replaceChildren();
    pins.forEach(function (pin) {
      var li = document.createElement("li");
      li.textContent = "Pin: " + pin.label;
      answerList.appendChild(li);
    });
    counties.forEach(function (name) {
      var li = document.createElement("li");
      li.textContent = "County: " + name;
      answerList.appendChild(li);
    });
    pinsField.value = JSON.stringify(
      pins.map(function (p) {
        return { lat: p.lat, lon: p.lon, view_km: p.view_km };
      })
    );
    countiesField.value = JSON.stringify(Array.from(counties.keys()));
  }

  // ----------------------------------------------------- the controls

  form.querySelectorAll("input[name=answer_class]").forEach(function (radio) {
    radio.addEventListener("change", function () {
      answerClass = radio.value;
      // A change of class starts the marking again: a P answer is one
      // pin, and an R answer's pins and counties are not a P answer.
      pins = [];
      counties.clear();
      mapWrap.hidden = !(answerClass === "P" || answerClass === "R");
      stateLabel.hidden = answerClass !== "S";
      modeGroup.hidden = answerClass !== "R";
      render();
    });
  });

  function setMode(mode) {
    clickMode = mode;
    modeCounties.setAttribute("aria-pressed", String(mode === "counties"));
    modePins.setAttribute("aria-pressed", String(mode === "pins"));
    redrawScale();
  }
  modeCounties.addEventListener("click", function () {
    setMode("counties");
  });
  modePins.addEventListener("click", function () {
    setMode("pins");
  });

  // Find a place: moves the map, never answers.
  var find = document.getElementById("geo-find");
  var list = document.getElementById("geo-find-list");
  var found = [];
  find.addEventListener("input", function () {
    var typed = find.value.trim();
    var hit = found.find(function (h) {
      return label(h) === typed;
    });
    if (hit) {
      goTo(hit);
      return;
    }
    if (typed.length < 2) return;
    fetch(findUrl + "?q=" + encodeURIComponent(typed) + "&state=" + startState, {
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        found = data.results || [];
        list.replaceChildren();
        found.forEach(function (h) {
          var option = document.createElement("option");
          option.value = label(h);
          list.appendChild(option);
        });
      });
  });
  find.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (found.length) goTo(found[0]);
    }
  });

  function label(h) {
    return h.name + (h.kind === "county" ? " County" : "") + ", " + h.state;
  }

  function goTo(h) {
    var feature =
      h.kind === "county"
        ? countyFeatures.find(function (f) {
            return String(f.id).padStart(5, "0") === h.geoid;
          })
        : placeFeatures.get(h.geoid);
    if (feature) {
      zoomTo(feature);
    } else {
      scaleNote.textContent =
        "That place is not drawn on this map; zoom to it by hand.";
    }
  }

  // ------------------------------------------------- form and guide

  var opened = Date.now();
  form.addEventListener("submit", function (event) {
    document.getElementById("opened-at").value = Math.round((Date.now() - opened) / 1000);
    if (answerClass === "P" && pins.length !== 1) {
      event.preventDefault();
      scaleNote.textContent = "Drop one pin on the place.";
    } else if (answerClass === "R" && !pins.length && !counties.size) {
      event.preventDefault();
      scaleNote.textContent = "Click the counties, or pin the towns.";
    }
  });

  var guide = document.getElementById("geo-guide");
  document.querySelectorAll(".geo-guide-open").forEach(function (link) {
    link.addEventListener("click", function (event) {
      event.preventDefault();
      guide.hidden = false;
    });
  });
  var close = document.querySelector(".geo-guide-close");
  if (close) {
    close.addEventListener("click", function () {
      guide.hidden = true;
    });
  }
})();
