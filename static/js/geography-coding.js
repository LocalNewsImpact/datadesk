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
 * Leaflet (vendored) over CARTO's Positron tiles: streets and place
 * names come from the basemap, drawn in two layers so the county
 * outlines sit between the streets and the names and never cover a
 * label. County outlines are the TIGER files in static/geo -- the 500k
 * files where a state has been built, the national 10m file elsewhere
 * -- because a click on one is an answer, and the answer has to be the
 * county the server will resolve.
 *
 * Search moves the map and never answers: a town or county from the
 * gazetteer, or a street address through the server (data-address-url,
 * the Census geocoder and then OpenStreetMap's), looked up on Enter only.
 */
(function () {
  "use strict";

  var root = document.getElementById("geo-map");
  var form = document.getElementById("geo-form");
  if (!root || !form || !window.L || !window.topojson) return;

  var L = window.L;
  var topojson = window.topojson;
  var base = root.dataset.geoBase;
  var startState = root.dataset.startState || "29";
  var countyStates = (root.dataset.countyStates || "").split(",").filter(Boolean);
  var pinViewKm = parseFloat(root.dataset.pinViewKm) || 80;
  var whereUrl = root.dataset.whereUrl;
  var findUrl = root.dataset.findUrl;
  var addressUrl = root.dataset.addressUrl;

  var pinsField = document.getElementById("geo-pins");
  var countiesField = document.getElementById("geo-counties");
  var scaleNote = document.getElementById("geo-scale");
  var answerList = document.getElementById("geo-answer");
  var mapWrap = document.querySelector(".geo-map-wrap");
  var stateLabel = document.querySelector(".geo-state");
  var modeGroup = document.querySelector(".geo-mode");
  var modeCounties = document.querySelector(".geo-mode-counties");
  var modePins = document.querySelector(".geo-mode-pins");
  var findNote = document.getElementById("geo-find-note");

  var answerClass = "";
  var clickMode = "counties"; // R only: what a click on the map does
  var pins = []; // {lat, lon, view_km, label, marker}
  var counties = new Map(); // geoid -> name
  var countyLayers = new Map(); // geoid -> Leaflet layer

  // ------------------------------------------------------------ the map

  var map = L.map(root, {
    zoomControl: true,
    scrollWheelZoom: true,
    doubleClickZoom: true,
    boxZoom: true,
    keyboard: true,
    minZoom: 3,
    maxZoom: 19,
    zoomSnap: 0.5,
  });
  map.attributionControl.setPrefix(false);
  map.createPane("labels");
  map.getPane("labels").style.zIndex = 450; // above outlines (400), below pins
  map.getPane("labels").style.pointerEvents = "none";

  var osm = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  var tileKey = root.dataset.tileKey;
  if (tileKey) {
    // CARTO Voyager in two layers, the names above the county outlines.
    // Voyager rather than Positron: major roads in colour, so a coder can
    // find a street without zooming to it.
    var tiles =
      "https://basemaps.cartocdn.com/rastertiles/{style}/{z}/{x}/{y}.png?key=" +
      encodeURIComponent(tileKey);
    var credit = osm + ' &copy; <a href="https://carto.com/attributions">CARTO</a>';
    L.tileLayer(tiles, { style: "voyager_nolabels", maxZoom: 19, attribution: credit }).addTo(map);
    L.tileLayer(tiles, { style: "voyager_only_labels", maxZoom: 19, pane: "labels" }).addTo(map);
  } else {
    // No key (local development): OpenStreetMap's own tiles, names baked in.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: osm,
    }).addTo(map);
  }

  // The map's box is hidden until a class that needs it is chosen, and a
  // hidden box has no size: Leaflet is told when it is shown.
  map.setView([38.5, -92.5], 6);

  function viewKm() {
    var b = map.getBounds();
    var lat = map.getCenter().lat;
    return map.distance([lat, b.getWest()], [lat, b.getEast()]) / 1000;
  }

  function redrawScale() {
    var km = viewKm();
    root.classList.toggle(
      "geo-pinning",
      answerClass === "P" || (answerClass === "R" && clickMode === "pins")
    );
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
  map.on("zoomend moveend", redrawScale);

  var countyStyle = { color: "#4a5568", weight: 1, opacity: 0.55, fill: true, fillOpacity: 0 };
  var chosenStyle = { color: "#2456c9", weight: 2, opacity: 0.9, fillColor: "#2456c9", fillOpacity: 0.22 };

  function fips(id) {
    return String(id).padStart(5, "0");
  }

  function loadJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url);
      return r.json();
    });
  }

  var stateBounds = {};
  var ready = loadJson(base + "counties-10m.json").then(function (us) {
    topojson.feature(us, us.objects.states).features.forEach(function (s) {
      stateBounds[String(s.id).padStart(2, "0")] = L.geoJSON(s).getBounds();
    });
    // The built states' 500k counties in place of the 10m ones.
    var national = topojson.feature(us, us.objects.counties).features.filter(function (f) {
      return countyStates.indexOf(fips(f.id).slice(0, 2)) === -1;
    });
    national.forEach(function (f) {
      f.properties = { GEOID: fips(f.id), NAME: f.properties.name };
    });
    L.geoJSON(
      topojson.mesh(us, us.objects.states, function (a, b) {
        return a !== b;
      }),
      { interactive: false, style: { color: "#1d2433", weight: 2, opacity: 0.6 } }
    ).addTo(map);
    return Promise.all(
      countyStates.map(function (state) {
        return loadJson(base + "counties/" + state + ".json")
          .then(function (topo) {
            return topojson.feature(topo, topo.objects.counties).features;
          })
          .catch(function () {
            return [];
          });
      })
    ).then(function (built) {
      var features = national.concat.apply(national, built);
      L.geoJSON(
        { type: "FeatureCollection", features: features },
        {
          style: countyStyle,
          onEachFeature: function (feature, layer) {
            var geoid = feature.properties.GEOID;
            countyLayers.set(geoid, layer);
            layer.on("click", function (event) {
              // Anything but counties mode: the click is the map's.
              if (answerClass !== "R" || clickMode !== "counties") return;
              L.DomEvent.stop(event);
              toggleCounty(geoid, feature.properties.NAME);
            });
          },
        }
      ).addTo(map);
      redrawScale();
    });
  });

  // ------------------------------------------------------- the answer

  // A double click zooms, as it does on every other map: a single click
  // waits long enough to tell the two apart.
  var pending = null;
  map.on("click", function (event) {
    if (pending) return;
    pending = setTimeout(function () {
      pending = null;
      dropPin(event.latlng);
    }, 260);
  });
  map.on("dblclick", function () {
    clearTimeout(pending);
    pending = null;
  });

  function dropPin(latlng) {
    var wantsPin = answerClass === "P" || (answerClass === "R" && clickMode === "pins");
    if (!wantsPin) return;
    var km = viewKm();
    if (km > pinViewKm) {
      scaleNote.textContent = "Zoom in further before dropping a pin: one town should fill the map.";
      return;
    }
    var pin = { lat: latlng.lat, lon: latlng.lng, view_km: km, label: "…" };
    if (answerClass === "P") clearPins();
    pins.push(pin);
    pin.marker = L.circleMarker(latlng, {
      radius: 7,
      color: "#fff",
      weight: 2,
      fillColor: "#c0392b",
      fillOpacity: 1,
      bubblingMouseEvents: false,
    })
      .addTo(map)
      .on("click", function () {
        removePin(pin);
      });
    resolvePin(pin);
    render();
  }

  function clearPins() {
    pins.forEach(function (p) {
      map.removeLayer(p.marker);
    });
    pins = [];
  }

  function removePin(pin) {
    map.removeLayer(pin.marker);
    pins = pins.filter(function (p) {
      return p !== pin;
    });
    render();
  }

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
        pin.marker.bindTooltip(pin.label);
        render();
      });
  }

  function toggleCounty(geoid, name) {
    if (counties.has(geoid)) counties.delete(geoid);
    else counties.set(geoid, name);
    render();
  }

  function render() {
    countyLayers.forEach(function (layer, geoid) {
      layer.setStyle(counties.has(geoid) ? chosenStyle : countyStyle);
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
      clearPins();
      counties.clear();
      var shown = answerClass === "P" || answerClass === "R";
      mapWrap.hidden = !shown;
      stateLabel.hidden = answerClass !== "S";
      modeGroup.hidden = answerClass !== "R";
      // The box has a size only once shown: open it on the state the
      // first time, and leave it where the coder put it after that.
      if (shown) {
        map.invalidateSize();
        if (!fitted) {
          fitted = true;
          ready.then(function () {
            if (stateBounds[startState]) map.fitBounds(stateBounds[startState]);
          });
        }
      }
      render();
    });
  });
  var fitted = false;

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

  // Find: moves the map, never answers.
  var find = document.getElementById("geo-find");
  var list = document.getElementById("geo-find-list");
  var found = [];
  var spot = null; // where an address search put the map

  find.addEventListener("input", function () {
    var typed = find.value.trim();
    var hit = found.find(function (h) {
      return label(h) === typed;
    });
    if (hit) {
      goTo(hit);
      return;
    }
    if (typed.length < 2 || /\d/.test(typed)) return; // an address: Enter
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
    if (event.key !== "Enter") return;
    event.preventDefault();
    var typed = find.value.trim();
    var hit = found.find(function (h) {
      return label(h) === typed;
    });
    if (hit) goTo(hit);
    else if (typed.length >= 3) lookUp(typed);
  });

  function label(h) {
    return h.name + (h.kind === "county" ? " County" : "") + ", " + h.state;
  }

  function goTo(h) {
    if (h.bbox) {
      map.fitBounds([
        [h.bbox[1], h.bbox[0]],
        [h.bbox[3], h.bbox[2]],
      ], { maxZoom: 14 });
      say("");
    } else {
      lookUp(h.name + ", " + h.state);
    }
  }

  function say(text) {
    if (findNote) findNote.textContent = text;
  }

  function lookUp(typed) {
    say("Looking up “" + typed + "”…");
    fetch(addressUrl + "?q=" + encodeURIComponent(typed), { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        var hit = (data.results || [])[0];
        if (!hit) {
          say("Nothing found for “" + typed + "”. Try a town, or a street with a house number.");
          return;
        }
        if (hit.bbox) {
          map.fitBounds([
            [hit.bbox[1], hit.bbox[0]],
            [hit.bbox[3], hit.bbox[2]],
          ], { maxZoom: 17 });
        } else {
          map.setView([hit.lat, hit.lon], hit.zoom || 16);
        }
        if (spot) map.removeLayer(spot);
        spot = L.circleMarker([hit.lat, hit.lon], {
          radius: 14,
          color: "#2456c9",
          weight: 2,
          dashArray: "4 3",
          fill: false,
          interactive: false,
        }).addTo(map);
        say("Showing " + hit.label + ". Click the map to drop the pin.");
      })
      .catch(function () {
        say("The address lookup did not answer. Try again, or move the map by hand.");
      });
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
