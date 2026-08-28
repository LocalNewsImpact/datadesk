/* A date field that opens a calendar and only a calendar.
 *
 * The native control differs by browser and none of it can be styled. In
 * Firefox the month and year in its header are a second view: clicking
 * them replaces the day grid with a scrolling list, and getting back to
 * the days means clicking again. That is two controls where the job needs
 * one, and it is the only view anybody wanted.
 *
 * So the input keeps its value and its name -- the form still submits
 * yyyy-mm-dd, the server is unchanged -- and the picker is ours. Where
 * this script does not run the field stays a native date input, which is
 * worse but never nothing.
 */
(function (global) {
  "use strict";

  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  function iso(date) {
    var m = String(date.getMonth() + 1).padStart(2, "0");
    var d = String(date.getDate()).padStart(2, "0");
    return date.getFullYear() + "-" + m + "-" + d;
  }

  function parse(value) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
    if (!m) return null;
    var date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return isNaN(date.getTime()) ? null : date;
  }

  // Monday first: the weeks in this corpus are reported that way, and a
  // grid whose columns do not match the labels above them is worse than
  // either convention.
  function leading(year, month) {
    return (new Date(year, month, 1).getDay() + 6) % 7;
  }

  function upgrade(input) {
    if (input.dataset.calendarReady) return;
    input.dataset.calendarReady = "1";
    // Stops the native picker. The value format is unchanged, so anything
    // reading the field -- including the form post -- sees what it saw.
    input.type = "text";
    input.autocomplete = "off";
    input.setAttribute("inputmode", "numeric");
    input.setAttribute("placeholder", "yyyy-mm-dd");

    var wrap = document.createElement("span");
    wrap.className = "cal-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var pop = document.createElement("div");
    pop.className = "cal-pop";
    pop.hidden = true;
    wrap.appendChild(pop);

    var shown = parse(input.value) || new Date();
    shown = new Date(shown.getFullYear(), shown.getMonth(), 1);

    function close() {
      pop.hidden = true;
    }

    function choose(date) {
      input.value = iso(date);
      input.dispatchEvent(new Event("change", { bubbles: true }));
      close();
      input.focus();
    }

    function draw() {
      pop.textContent = "";

      var head = document.createElement("div");
      head.className = "cal-head";
      var back = document.createElement("button");
      back.type = "button";
      back.className = "cal-step";
      back.setAttribute("aria-label", "Previous month");
      back.textContent = "‹";
      var title = document.createElement("span");
      title.className = "cal-title";
      title.textContent = MONTHS[shown.getMonth()] + " " + shown.getFullYear();
      var next = document.createElement("button");
      next.type = "button";
      next.className = "cal-step";
      next.setAttribute("aria-label", "Next month");
      next.textContent = "›";
      back.addEventListener("click", function () {
        shown = new Date(shown.getFullYear(), shown.getMonth() - 1, 1);
        draw();
      });
      next.addEventListener("click", function () {
        shown = new Date(shown.getFullYear(), shown.getMonth() + 1, 1);
        draw();
      });
      head.appendChild(back);
      head.appendChild(title);
      head.appendChild(next);
      pop.appendChild(head);

      var grid = document.createElement("div");
      grid.className = "cal-grid";
      DAYS.forEach(function (name) {
        var cell = document.createElement("span");
        cell.className = "cal-day";
        cell.textContent = name[0];
        cell.title = name;
        grid.appendChild(cell);
      });

      var chosen = parse(input.value);
      var today = iso(new Date());
      var year = shown.getFullYear();
      var month = shown.getMonth();
      for (var blank = 0; blank < leading(year, month); blank++) {
        grid.appendChild(document.createElement("span"));
      }
      var last = new Date(year, month + 1, 0).getDate();
      for (var day = 1; day <= last; day++) {
        var date = new Date(year, month, day);
        var cell = document.createElement("button");
        cell.type = "button";
        cell.className = "cal-date";
        cell.textContent = String(day);
        if (iso(date) === today) cell.classList.add("is-today");
        if (chosen && iso(date) === iso(chosen)) {
          cell.classList.add("is-chosen");
          cell.setAttribute("aria-current", "date");
        }
        cell.addEventListener("click", choose.bind(null, date));
        grid.appendChild(cell);
      }
      pop.appendChild(grid);

      var foot = document.createElement("div");
      foot.className = "cal-foot";
      var clear = document.createElement("button");
      clear.type = "button";
      clear.className = "linklike";
      clear.textContent = "Clear";
      clear.addEventListener("click", function () {
        input.value = "";
        input.dispatchEvent(new Event("change", { bubbles: true }));
        close();
      });
      foot.appendChild(clear);
      pop.appendChild(foot);
    }

    function open() {
      var at = parse(input.value);
      if (at) shown = new Date(at.getFullYear(), at.getMonth(), 1);
      draw();
      pop.hidden = false;
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", open);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });
    // On mousedown, not click. Paging a month redraws the popup, which
    // removes the very button that was pressed -- so by the time the
    // click reaches the document its target is detached, `contains` is
    // false, and the calendar closes. Pressing back or forward redrew
    // the month and dismissed the picker in the same gesture, which
    // reads as a calendar that will not page, and leaves typing the date
    // by hand as the only way to reach another month.
    //
    // mousedown is dispatched before the redraw, while the target is
    // still where it was pressed.
    document.addEventListener("mousedown", function (e) {
      if (!pop.hidden && !wrap.contains(e.target)) close();
    });
  }

  function upgradeAll(root) {
    (root || document)
      .querySelectorAll('input[type="date"]')
      .forEach(upgrade);
  }

  document.addEventListener("DOMContentLoaded", function () {
    upgradeAll(document);
  });

  global.DatadeskCalendar = { upgrade: upgrade, upgradeAll: upgradeAll, _iso: iso };
})(window);
