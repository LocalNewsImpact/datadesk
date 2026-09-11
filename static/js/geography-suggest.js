/* Place suggestions for the geography queue.
 *
 * A typed place name is worth nothing until it resolves, and a name that
 * resolves to the WRONG place is worse than one that does not resolve at
 * all: a story about the sewer trustees of Freeburg -- a village in Osage
 * County, Missouri -- was extracted as "Freeburg, IL", the lookup
 * succeeded, and the story shaded a county three hundred miles away.
 * Nothing downstream could catch it, because the answer was internally
 * valid.
 *
 * So suggestions RANK the publisher's own state first and still offer
 * everywhere else. Filtering would be wrong and this corpus proves it:
 * Whiteman Air Force Base, Nashville, Wichita State and Seattle are all
 * places Missouri outlets genuinely covered in one month, and a
 * Missouri-only list makes real coverage unenterable -- which is how a
 * queue teaches people to work around it.
 *
 * The names come from the endpoint that reads `lnic_contracts.geography`,
 * the same table the write path resolves against, so what is offered is
 * what will resolve.
 */
(function () {
  "use strict";

  var url =
    (document.currentScript && document.currentScript.dataset.suggestUrl) ||
    "/review/geography/suggest/";

  /* Several places, separated by ";" -- a story mentions several and the
   * pipeline records several, so only the LAST fragment is being typed. */
  function typing(value) {
    var parts = value.split(";");
    return { head: parts.slice(0, -1), tail: parts[parts.length - 1].trim() };
  }

  /* The list needs the INPUT as its positioning context, not the cell.
   * `.geo-prop` is a flex container, so an absolutely-positioned child
   * with no offsets takes its static position at the container's content
   * box -- the top -- and the list rendered over the box being typed
   * into. Wrapping the input gives `top: 100%` something to mean.
   *
   * The wrapper also carries the pressed-verb reveal, so it has to sit
   * exactly where the input was: immediately after its own button. */
  function box(input) {
    var field = document.createElement("span");
    field.className = "geo-field";
    input.insertAdjacentElement("beforebegin", field);
    field.appendChild(input);

    var list = document.createElement("ul");
    list.className = "geo-suggest";
    list.hidden = true;
    field.appendChild(list);
    return list;
  }

  function attach(input, state) {
    var list = box(input);
    var timer = null;
    var active = -1;

    function close() {
      list.hidden = true;
      list.innerHTML = "";
      active = -1;
    }

    function choose(item) {
      var split = typing(input.value);
      var picked =
        item.name + (item.kind === "county" ? " County" : "") + ", " + item.state;
      input.value = split.head
        .concat(picked)
        .map(function (s) {
          return s.trim();
        })
        .join("; ");
      close();
      /* The dock counts a row as answered from its input event; setting
       * `value` in script does not fire one. */
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }

    function render(results) {
      list.innerHTML = "";
      if (!results.length) {
        close();
        return;
      }
      results.forEach(function (item, i) {
        var li = document.createElement("li");
        li.tabIndex = -1;
        li.dataset.i = i;
        /* An exact match and a correction are different answers and the
         * reviewer has to be able to tell: "Westphalya" offering
         * "Westphalia" is a fix, not a confirmation. */
        /* The county rung is written back with its suffix, which is what
         * the write path reads to know which rung it is -- "Osage" and
         * "Osage County" are different codes and a reviewer picking one
         * must not get the other. */
        var county = item.kind === "county";
        li.innerHTML =
          "<b>" + item.name + (county ? " County" : "") + "</b> <span>" +
          item.state + "</span>" +
          (county ? ' <i class="geo-rung">county</i>' : "") +
          (item.exact || county ? "" : ' <i class="geo-fix">did you mean</i>') +
          (state && item.state === state ? ' <i class="geo-home">this state</i>' : "");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          choose(results[i]);
        });
        list.appendChild(li);
      });
      list.hidden = false;
      list._results = results;
    }

    function highlight(step) {
      var items = list.querySelectorAll("li");
      if (!items.length) return;
      if (active >= 0) items[active].classList.remove("on");
      active = (active + step + items.length) % items.length;
      items[active].classList.add("on");
    }

    input.addEventListener("input", function () {
      var tail = typing(input.value).tail;
      window.clearTimeout(timer);
      if (tail.length < 2) {
        close();
        return;
      }
      timer = window.setTimeout(function () {
        var q = url + "?q=" + encodeURIComponent(tail);
        if (state) q += "&state=" + encodeURIComponent(state);
        fetch(q, { credentials: "same-origin" })
          .then(function (r) {
            return r.ok ? r.json() : { results: [] };
          })
          .then(function (data) {
            render(data.results || []);
          })
          .catch(close);
      }, 160);
    });

    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlight(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlight(-1);
      } else if (e.key === "Enter" && active >= 0) {
        /* Enter inside a queue form submits it. A reviewer picking a
         * suggestion means to pick the suggestion. */
        e.preventDefault();
        choose(list._results[active]);
      } else if (e.key === "Escape") {
        close();
      }
    });

    input.addEventListener("blur", function () {
      window.setTimeout(close, 120);
    });
  }

  document.querySelectorAll(".geo-prop").forEach(function (prop) {
    var state = prop.dataset.state || "";
    prop.querySelectorAll('input.fixval[type="text"]').forEach(function (input) {
      attach(input, state);
    });
  });
})();
