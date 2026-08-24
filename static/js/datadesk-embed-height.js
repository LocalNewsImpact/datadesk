/*
 * The framed half of the embed handshake (ROADMAP item 22).
 *
 * Measures the document and tells the parent. A ResizeObserver rather than
 * a timer, so a chart that redraws -- a legend wrapping, a font arriving,
 * a table toggled open -- reports the new height at the moment it changes
 * rather than up to a second later.
 */
(function () {
  "use strict";
  if (window.parent === window) return;

  var last = 0;

  function height() {
    var body = document.body;
    var html = document.documentElement;
    return Math.max(
      body.scrollHeight, body.offsetHeight,
      html.clientHeight, html.scrollHeight, html.offsetHeight
    );
  }

  function report() {
    var now = height();
    // A pixel of jitter is not a resize; reporting it would loop with a
    // parent whose own layout shifts by a pixel in response.
    if (Math.abs(now - last) < 2) return;
    last = now;
    window.parent.postMessage({ type: "datadesk:height", height: now }, "*");
  }

  window.addEventListener("load", report);
  window.addEventListener("resize", report);
  if (window.ResizeObserver) {
    new ResizeObserver(report).observe(document.body);
  }
  report();
})();
