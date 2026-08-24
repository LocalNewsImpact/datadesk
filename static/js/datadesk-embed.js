/*
 * The embed loader (ROADMAP item 22).
 *
 * A publisher pastes a placeholder and this script. The script builds the
 * frame, and the framed page tells it how tall it is, so nothing carries a
 * fixed height. That is the whole point: 480px was wrong for every visual,
 * and the person embedding cannot know the right number because it depends
 * on the reader's screen.
 *
 * It runs on somebody else's page. So: no cookies, no analytics, no globals
 * beyond one guard, and it does nothing at all if the placeholder is absent.
 */
(function () {
  "use strict";
  if (window.__datadeskEmbed) return;
  window.__datadeskEmbed = true;

  var ORIGIN = new URL(document.currentScript.src).origin;

  function mount(node) {
    var slug = node.getAttribute("data-visual");
    if (!slug || node.dataset.mounted) return;
    node.dataset.mounted = "1";

    // Both are optional and both are pins. Absent, the embed follows what
    // is published and what the reader's own device asks for; named, it
    // holds still, which is what a publisher pasting into a fixed page
    // needs -- a dark chart landing in a light article is the failure.
    var params = [];
    var version = node.getAttribute("data-version");
    if (version) params.push("v=" + encodeURIComponent(version));
    var theme = node.getAttribute("data-theme");
    if (theme === "light" || theme === "dark") params.push("theme=" + theme);

    var src = ORIGIN + "/embed/" + encodeURIComponent(slug) + "/";
    if (params.length) src += "?" + params.join("&");

    var frame = document.createElement("iframe");
    frame.src = src;
    frame.title = node.getAttribute("data-title") || "Chart";
    frame.setAttribute("scrolling", "no");
    frame.setAttribute("loading", "lazy");
    frame.style.cssText =
      "width:100%;border:0;display:block;height:" +
      (node.getAttribute("data-height") || "300") +
      "px";
    node.appendChild(frame);
    node.__frame = frame;

    // A frame that never reports back leaves the fallback height, which is
    // wrong but readable. A frame that never loads leaves whatever the
    // publisher put in the placeholder -- usually a link to the visual --
    // which is why this appends rather than replaces.
  }

  function onMessage(event) {
    if (event.origin !== ORIGIN) return;
    var data = event.data;
    if (!data || data.type !== "datadesk:height") return;
    var frames = document.querySelectorAll(".datadesk-visual");
    for (var i = 0; i < frames.length; i++) {
      var frame = frames[i].__frame;
      if (frame && frame.contentWindow === event.source) {
        frame.style.height = Math.ceil(data.height) + "px";
        return;
      }
    }
  }

  function scan() {
    var nodes = document.querySelectorAll(".datadesk-visual");
    for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
  }

  window.addEventListener("message", onMessage);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  // A placeholder added after load -- a lazy-loaded article body, a single
  // page app -- still gets a frame.
  if (window.MutationObserver) {
    new MutationObserver(scan).observe(document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
})();
