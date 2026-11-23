/* One story's answers on one map, for the geography disagreements page
 * (G-WP9). Each coder's counties are shaded and their pins drawn in
 * their own colour; the pipeline's point is a black square and its
 * counties are outlined. The map zooms to whatever was drawn.
 */
(function () {
  "use strict";

  var root = document.getElementById("geo-dis-map");
  var data = document.getElementById("geo-dis-data");
  if (!root || !data || !window.d3 || !window.topojson) return;
  var d3 = window.d3;
  var topojson = window.topojson;
  var rows = JSON.parse(data.textContent);
  var caption = document.getElementById("geo-dis-caption");
  var base = root.dataset.geoBase;
  var placeStates = (root.dataset.placeStates || "").split(",").filter(Boolean);

  var width = root.clientWidth || 600;
  var height = Math.round(width * 0.8);
  var svg = d3.select(root).append("svg")
    .attr("viewBox", [0, 0, width, height]).attr("width", "100%").attr("height", height);
  var gCounties = svg.append("g").attr("class", "geo-counties");
  var gPlaces = svg.append("g").attr("class", "geo-places");
  var gStates = svg.append("g").attr("class", "geo-states");
  var gMarks = svg.append("g").attr("class", "geo-marks");
  var projection = d3.geoMercator();
  var path = d3.geoPath(projection);
  var counties = new Map();
  var states = [];

  function idOf(f) { return String(f.id).padStart(5, "0"); }

  d3.json(base + "counties-10m.json").then(function (us) {
    states = topojson.feature(us, us.objects.states).features;
    topojson.feature(us, us.objects.counties).features.forEach(function (f) {
      counties.set(idOf(f), f);
    });
    projection.fitSize([width, height], topojson.feature(us, us.objects.nation));
    gStates.append("path")
      .datum(topojson.mesh(us, us.objects.states, function (a, b) { return a !== b; }))
      .attr("d", path);
    return Promise.all(placeStates.map(function (s) {
      return d3.json(base + "places/" + s + ".json").then(function (topo) {
        gPlaces.append("g").selectAll("path")
          .data(topojson.feature(topo, topo.objects.places).features)
          .join("path");
      });
    }));
  }).then(function () {
    document.querySelectorAll(".geo-dis-show").forEach(function (button) {
      button.addEventListener("click", function () { draw(+button.dataset.index); });
    });
    if (rows.length) draw(0);
  });

  function draw(index) {
    var row = rows[index];
    var features = [];
    var shaded = [];
    var points = [];
    row.answers.forEach(function (answer, i) {
      answer.counties.forEach(function (c) {
        if (counties.has(c)) shaded.push({ f: counties.get(c), cls: "coder-" + i });
      });
      if (answer["class"] === "S" && answer.state) {
        var st = states.find(function (s) { return String(s.id).padStart(2, "0") === answer.state; });
        if (st) shaded.push({ f: st, cls: "coder-" + i });
      }
      answer.pins.forEach(function (p) { points.push({ p: p, cls: "coder-" + i, shape: "circle" }); });
    });
    var outlined = [];
    if (row.pipeline) {
      row.pipeline.counties.forEach(function (c) {
        if (counties.has(c)) outlined.push(counties.get(c));
      });
      if (row.pipeline.point) points.push({ p: row.pipeline.point, cls: "pipeline", shape: "square" });
    }
    shaded.forEach(function (s) { features.push(s.f); });
    outlined.forEach(function (f) { features.push(f); });
    points.forEach(function (m) {
      features.push({ type: "Feature", geometry: { type: "Point", coordinates: [m.p.lon, m.p.lat] } });
    });
    if (features.length) {
      var collection = { type: "FeatureCollection", features: features };
      projection.fitExtent([[30, 30], [width - 30, height - 30]], collection);
      // A single point has no extent: show about 60 km around it.
      var b = path.bounds(collection);
      if (b[1][0] - b[0][0] < 40) {
        var c = projection.invert([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2]);
        projection.fitExtent([[30, 30], [width - 30, height - 30]],
          d3.geoCircle().center(c).radius(0.3)());
      }
    }
    gStates.selectAll("path").attr("d", path);
    gPlaces.selectAll("path").attr("d", path);
    gCounties.selectAll("path").data(shaded).join("path")
      .attr("d", function (s) { return path(s.f); })
      .attr("class", function (s) { return "shade " + s.cls; });
    gMarks.selectAll("path.outline").data(outlined).join("path")
      .attr("class", "outline pipeline").attr("d", path);
    gMarks.selectAll("path.mark").data(points).join("path")
      .attr("class", function (m) { return "mark " + m.cls; })
      .attr("transform", function (m) {
        var xy = projection([m.p.lon, m.p.lat]);
        return "translate(" + xy[0] + "," + xy[1] + ")";
      })
      .attr("d", function (m) {
        return d3.symbol().type(m.shape === "square" ? d3.symbolSquare : d3.symbolCircle).size(90)();
      });
    caption.textContent = row.title || row.article_id;
  }
})();
