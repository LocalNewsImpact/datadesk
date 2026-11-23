/* The coder's screen for the geography question (G-WP2).
 *
 * The coder says what kind of place the story is about, then marks it:
 *
 *   P  one place: a town or an address, typed, or one pin on the map
 *   R  towns and counties, typed, or clicked and pinned on the map
 *   S  a state, from the menu
 *   U  N  nothing to mark
 *
 * TYPING IS THE MAIN WAY TO ANSWER. The first practice round showed the
 * map working better as a picture of the answer than as the way to give
 * it, so the add box comes first: a town chosen by name is pinned at a
 * point inside its boundary, an address at the geocoded point, a county
 * by its GEOID. Every choice goes in the list under the box, and the map
 * fits itself to the list.
 *
 * The map is the second way. A pin dropped on it is accepted only when
 * the view is zoomed in far enough that one town fills it
 * (data-pin-view-km): dropped on a whole-state view it lands wherever
 * the cursor was and resolves to a town nobody chose. The rule is for
 * clicks; a typed town lands where it is.
 *
 * Every pin is resolved by the server (data-where-url) and the result is
 * what the list shows, so what the coder sees is what will be recorded.
 * The server resolves it again on save; nothing here is trusted.
 *
 * Leaflet (vendored) over CARTO Voyager tiles (data-tile-key; OpenStreetMap
 * where there is none), in two layers so the county outlines sit between
 * the streets and the names. County outlines are the TIGER files in
 * static/geo, because a click on one is an answer and has to be the
 * county the server will resolve.
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
  var errorNote = document.getElementById("geo-error");
  var answerList = document.getElementById("geo-answer");
  var pickWrap = document.querySelector(".geo-pick");
  var findLabel = document.querySelector(".geo-find-label");
  var find = document.getElementById("geo-find");
  var suggest = document.getElementById("geo-suggest");
  var findNote = document.getElementById("geo-find-note");
  var mapWrap = document.querySelector(".geo-map-wrap");
  var stateLabel = document.querySelector(".geo-state");
  var modeGroup = document.querySelector(".geo-mode");
  var modeCounties = document.querySelector(".geo-mode-counties");
  var modePins = document.querySelector(".geo-mode-pins");

  var answerClass = "";
  var clickMode = "counties"; // R only: what a click on the map does
  var pins = []; // {lat, lon, view_km, how, label, marker}
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
    // CARTO Voyager in two layers, the names above the county outlines:
    // major roads in colour, so a street can be found without zooming.
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

  function clickPins() {
    return answerClass === "P" || (answerClass === "R" && clickMode === "pins");
  }

  function redrawScale() {
    root.classList.toggle("geo-pinning", clickPins());
    var km = viewKm();
    if (clickPins()) {
      scaleNote.textContent =
        km <= pinViewKm
          ? "Click the map to drop a pin (" + Math.round(km) + " km across)."
          : "To drop a pin by clicking, zoom in until the map is " +
            pinViewKm +
            " km across or less (now " +
            Math.round(km) +
            ").";
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

  function getJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url);
      return r.json();
    });
  }

  var stateBounds = {};
  var ready = getJson(base + "counties-10m.json").then(function (us) {
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
        return getJson(base + "counties/" + state + ".json")
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
      render();
    });
  });

  function showState() {
    if (stateBounds[startState]) map.fitBounds(stateBounds[startState]);
  }

  // The map follows the answer: it fits to what has been chosen.
  function fitToAnswer() {
    var box = L.latLngBounds([]);
    pins.forEach(function (p) {
      box.extend([p.lat, p.lon]);
    });
    counties.forEach(function (name, geoid) {
      var layer = countyLayers.get(geoid);
      if (layer) box.extend(layer.getBounds());
    });
    if (box.isValid()) map.fitBounds(box, { maxZoom: 13, padding: [28, 28] });
    else showState();
  }

  // ------------------------------------------------------- the answer

  // A double click zooms, as on every other map: a single click waits
  // long enough to tell the two apart.
  var pending = null;
  map.on("click", function (event) {
    if (pending) return;
    pending = setTimeout(function () {
      pending = null;
      clickPin(event.latlng);
    }, 260);
  });
  map.on("dblclick", function () {
    clearTimeout(pending);
    pending = null;
  });

  function clickPin(latlng) {
    if (!clickPins()) return;
    var km = viewKm();
    if (km > pinViewKm) {
      scaleNote.textContent =
        "Zoom in further before clicking a pin: one town should fill the map. " +
        "Or type the town in the box above.";
      return;
    }
    addPin({ lat: latlng.lat, lon: latlng.lng, view_km: km, how: "map" }, false);
  }

  function addPin(pin, fit) {
    if (answerClass === "P") clearPins();
    pin.label = pin.label || "…";
    pins.push(pin);
    pin.marker = L.circleMarker([pin.lat, pin.lon], {
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
    if (fit) fitToAnswer();
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
    getJson(whereUrl + "?lat=" + pin.lat + "&lon=" + pin.lon).then(function (where) {
      if (!where.county) {
        pin.label = "Not in a U.S. county: take it off and add it again";
      } else if (!where.place_name && countyStates.indexOf(where.state) === -1) {
        // No town boundaries for this state: the county is what is kept.
        pin.label = where.county_name + " County (towns in this state are recorded by county)";
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

  function addCounty(geoid, name) {
    counties.set(geoid, name);
    render();
    fitToAnswer();
  }

  function toggleCounty(geoid, name) {
    if (counties.has(geoid)) counties.delete(geoid);
    else counties.set(geoid, name);
    render();
  }

  function chip(text, remove) {
    var li = document.createElement("li");
    var span = document.createElement("span");
    span.textContent = text;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "linklike geo-remove";
    button.textContent = "Remove";
    button.setAttribute("aria-label", "Remove " + text);
    button.addEventListener("click", remove);
    li.appendChild(span);
    li.appendChild(button);
    return li;
  }

  function render() {
    countyLayers.forEach(function (layer, geoid) {
      layer.setStyle(counties.has(geoid) ? chosenStyle : countyStyle);
    });
    redrawScale();

    answerList.replaceChildren();
    pins.forEach(function (pin) {
      var what = answerClass === "P" ? "Place: " : "Town: ";
      answerList.appendChild(
        chip(what + pin.label, function () {
          removePin(pin);
        })
      );
    });
    counties.forEach(function (name, geoid) {
      answerList.appendChild(
        chip("County: " + name, function () {
          counties.delete(geoid);
          render();
        })
      );
    });
    pinsField.value = JSON.stringify(
      pins.map(function (p) {
        return { lat: p.lat, lon: p.lon, view_km: p.view_km, how: p.how };
      })
    );
    countiesField.value = JSON.stringify(Array.from(counties.keys()));
  }

  // -------------------------------------------------------- the add box

  var items = []; // what the list under the box offers
  var active = -1;
  var asked = 0; // the newest request; older answers are dropped
  var timer = null;

  function say(text) {
    findNote.textContent = text;
  }

  function offer(list) {
    items = list;
    active = list.length ? 0 : -1;
    suggest.replaceChildren();
    list.forEach(function (item, i) {
      var li = document.createElement("li");
      li.id = "geo-suggest-" + i;
      li.setAttribute("role", "option");
      li.className = "geo-suggest-item";
      var main = document.createElement("span");
      main.textContent = item.text;
      li.appendChild(main);
      if (item.hint) {
        var hint = document.createElement("span");
        hint.className = "quiet";
        hint.textContent = item.hint;
        li.appendChild(hint);
      }
      // mousedown, not click: a click would blur the box and close the
      // list before it landed.
      li.addEventListener("mousedown", function (event) {
        event.preventDefault();
        pick(item);
      });
      suggest.appendChild(li);
    });
    suggest.hidden = !list.length;
    find.setAttribute("aria-expanded", String(!!list.length));
    highlight();
  }

  function highlight() {
    Array.prototype.forEach.call(suggest.children, function (li, i) {
      li.setAttribute("aria-selected", String(i === active));
    });
    if (active >= 0) find.setAttribute("aria-activedescendant", "geo-suggest-" + active);
    else find.removeAttribute("aria-activedescendant");
  }

  function name(h) {
    return h.name + (h.kind === "county" ? " County" : "") + ", " + h.state;
  }

  function hintFor(h) {
    if (h.kind === "county") {
      return answerClass === "R" ? "add the county" : "show on the map";
    }
    return answerClass === "P" ? "use this town" : "add the town";
  }

  function addressItem(typed) {
    return { kind: "address", text: "Look up “" + typed + "” as an address", typed: typed };
  }

  find.addEventListener("input", function () {
    clearTimeout(timer);
    var typed = find.value.trim();
    if (typed.length < 2) {
      offer([]);
      return;
    }
    timer = setTimeout(function () {
      var mine = ++asked;
      getJson(findUrl + "?q=" + encodeURIComponent(typed) + "&state=" + startState)
        .then(function (data) {
          if (mine !== asked) return;
          var list = (data.results || []).map(function (h) {
            return { kind: h.kind, text: name(h), hint: hintFor(h), hit: h };
          });
          // A number in it reads as an address: offer that first.
          if (typed.length >= 3) {
            if (/\d/.test(typed)) list.unshift(addressItem(typed));
            else list.push(addressItem(typed));
          }
          offer(list);
        })
        .catch(function () {
          if (mine === asked) offer(typed.length >= 3 ? [addressItem(typed)] : []);
        });
    }, 180);
  });

  find.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!items.length) return;
      event.preventDefault();
      var step = event.key === "ArrowDown" ? 1 : -1;
      active = (active + step + items.length) % items.length;
      highlight();
    } else if (event.key === "Enter") {
      event.preventDefault(); // never submit the form from the box
      if (active >= 0 && items[active]) pick(items[active]);
      else if (find.value.trim().length >= 3) lookUp(find.value.trim(), false);
    } else if (event.key === "Escape") {
      offer([]);
    }
  });
  find.addEventListener("blur", function () {
    offer([]);
  });

  function added() {
    find.value = "";
    offer([]);
    say("");
    errorNote.textContent = "";
  }

  function pick(item) {
    if (item.kind === "address") {
      lookUp(item.typed, false);
    } else if (item.kind === "found") {
      addPin({ lat: item.hit.lat, lon: item.hit.lon, view_km: null, how: "address" }, true);
      added();
    } else if (item.kind === "place") {
      var h = item.hit;
      if (h.point) {
        addPin({ lat: h.point[0], lon: h.point[1], view_km: null, how: "search" }, true);
        added();
      } else {
        // A town in a state whose boundaries are not built: found by name.
        lookUp(h.name + ", " + h.state, true);
      }
    } else if (item.kind === "county") {
      if (answerClass === "R") {
        addCounty(item.hit.geoid, item.hit.name);
        added();
      } else {
        var b = item.hit.bbox;
        if (b) map.fitBounds([[b[1], b[0]], [b[3], b[2]]]);
        offer([]);
        say(
          name(item.hit) +
            " is on the map. A story about the county itself is “A county, or an area wider than one town”."
        );
      }
    }
  }

  // An address, through the server: the Census geocoder, then
  // OpenStreetMap's. `take` adds the first match without asking.
  function lookUp(typed, take) {
    var mine = ++asked;
    say("Looking up “" + typed + "”…");
    getJson(addressUrl + "?q=" + encodeURIComponent(typed))
      .then(function (data) {
        if (mine !== asked) return;
        var found = (data.results || []).map(function (r) {
          return { kind: "found", text: r.label, hint: r.source, hit: r };
        });
        if (!found.length) {
          say("Nothing found for “" + typed + "”. Try a town, or a street with a house number.");
          return;
        }
        if (take) {
          pick(found[0]);
          return;
        }
        say("Choose the match to add it.");
        find.focus();
        offer(found);
      })
      .catch(function () {
        if (mine === asked) say("The address lookup did not answer. Try again, or click the map.");
      });
  }

  // ----------------------------------------------------- the controls

  var fitted = false;
  form.querySelectorAll("input[name=answer_class]").forEach(function (radio) {
    radio.addEventListener("change", function () {
      answerClass = radio.value;
      // A change of class starts the marking again: a P answer is one
      // place, and an R answer's towns and counties are not a P answer.
      clearPins();
      counties.clear();
      errorNote.textContent = "";
      var marks = answerClass === "P" || answerClass === "R";
      pickWrap.hidden = !marks;
      mapWrap.hidden = !marks;
      stateLabel.hidden = answerClass !== "S";
      modeGroup.hidden = answerClass !== "R";
      findLabel.textContent =
        answerClass === "P"
          ? "Type the place: a town, or a street address"
          : "Add the towns and counties, one at a time";
      find.placeholder =
        answerClass === "P" ? "e.g. Ashland, or 100 E Broadway, Columbia" : "e.g. Boone County, or Ashland";
      // The box has a size only once shown: open on the story's state
      // the first time, and leave the map where it is after that.
      if (marks) {
        map.invalidateSize();
        if (!fitted) {
          fitted = true;
          ready.then(showState);
        }
      }
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

  // ------------------------------------------------- form and guide

  var opened = Date.now();
  form.addEventListener("submit", function (event) {
    document.getElementById("opened-at").value = Math.round((Date.now() - opened) / 1000);
    if (answerClass === "P" && pins.length !== 1) {
      event.preventDefault();
      errorNote.textContent = "Add the place: type the town or address above, or click the map.";
    } else if (answerClass === "R" && !pins.length && !counties.size) {
      event.preventDefault();
      errorNote.textContent = "Add the towns or counties: type them above, or click the map.";
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
