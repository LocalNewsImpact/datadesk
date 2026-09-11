/* Place entry for the geography queue: suggestions, and chips.
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
 * WHY CHIPS. Several mentions used to be one text box and a ";" the
 * reviewer typed themselves. Two things went wrong with that and both
 * were reported: the lookup on the second name behaved differently from
 * the first because it depended on splitting a string the reviewer was
 * still editing, and a name that never resolved could be submitted --
 * "Fatima, MO", a real community in Osage County and in no gazetteer,
 * being unincorporated -- which the server then refused.
 *
 * A place becomes a chip only when it has resolved. The box clears after
 * each one, so every entry is the first entry; nothing unresolved can be
 * submitted, because only chips are written to the field that submits.
 */
(function () {
  "use strict";

  var url =
    (document.currentScript && document.currentScript.dataset.suggestUrl) ||
    "/review/geography/suggest/";

  function attach(store, state) {
    /* `store` is the input `_verbs.html` rendered: it carries the name
     * the submit path reads. It becomes hidden and holds the chips'
     * canonical text; the reviewer types into a new box beside it. */
    var field = document.createElement("span");
    field.className = "geo-field";
    store.insertAdjacentElement("beforebegin", field);

    var chips = document.createElement("span");
    chips.className = "geo-chips";
    field.appendChild(chips);

    var entry = document.createElement("input");
    entry.type = "text";
    entry.className = "geo-entry";
    entry.autocomplete = "off";
    entry.setAttribute("aria-label", "A place this story names");
    entry.placeholder = "type a place…";
    field.appendChild(entry);

    var list = document.createElement("ul");
    list.className = "geo-suggest";
    list.hidden = true;
    field.appendChild(list);

    field.appendChild(store);
    store.type = "hidden";

    var picked = [];
    var timer = null;
    var active = -1;

    function close() {
      list.hidden = true;
      list.innerHTML = "";
      active = -1;
    }

    function sync() {
      store.value = picked.join("; ");
      /* The dock counts a row as answered from the store's input event,
       * and setting `value` in script does not fire one. Removing the
       * last chip fires it too, with an empty value, which is what
       * withdraws the decision rather than leaving one with nothing to
       * write. */
      store.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function draw() {
      chips.innerHTML = "";
      picked.forEach(function (name, i) {
        var chip = document.createElement("span");
        chip.className = "geo-chip";
        chip.textContent = name;
        var drop = document.createElement("button");
        drop.type = "button";
        drop.className = "geo-chip-x";
        drop.setAttribute("aria-label", "Remove " + name);
        drop.textContent = "×";
        drop.addEventListener("click", function () {
          picked.splice(i, 1);
          draw();
          sync();
          entry.focus();
        });
        chip.appendChild(drop);
        chips.appendChild(chip);
      });
    }

    function add(item) {
      var name =
        item.name + (item.kind === "county" ? " County" : "") + ", " + item.state;
      if (picked.indexOf(name) === -1) picked.push(name);
      /* One centre per article -- the table's partial unique index says
       * so, and two chips would be refused by the server after the fact
       * rather than prevented here. */
      if (store.dataset.verb === "set_place") picked = [name];
      entry.value = "";
      close();
      draw();
      sync();
      entry.focus();
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
        /* The county rung carries its suffix, which is what the write
         * path reads to know which rung it is -- "Osage" and "Osage
         * County" are different codes and a reviewer picking one must
         * not get the other. */
        var county = item.kind === "county";
        li.innerHTML =
          "<b>" + item.name + (county ? " County" : "") + "</b> <span>" +
          item.state + "</span>" +
          (county ? ' <i class="geo-rung">county</i>' : "") +
          (item.exact || county ? "" : ' <i class="geo-fix">did you mean</i>') +
          (state && item.state === state ? ' <i class="geo-home">this state</i>' : "");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          add(results[i]);
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

    entry.addEventListener("input", function () {
      var typed = entry.value.trim();
      window.clearTimeout(timer);
      if (typed.length < 2) {
        close();
        return;
      }
      timer = window.setTimeout(function () {
        var q = url + "?q=" + encodeURIComponent(typed);
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

    entry.addEventListener("keydown", function (e) {
      if (e.key === "Backspace" && !entry.value && picked.length) {
        /* The ordinary behaviour of a chip field, and the only way back
         * from a mistake without reaching for the mouse. */
        picked.pop();
        draw();
        sync();
        return;
      }
      if (list.hidden) {
        /* Enter in a queue form submits it. Inside a place box that is
         * never what was meant. */
        if (e.key === "Enter") e.preventDefault();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlight(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlight(-1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        add(list._results[active >= 0 ? active : 0]);
      } else if (e.key === "Escape") {
        close();
      }
    });

    entry.addEventListener("blur", function () {
      window.setTimeout(close, 120);
    });
  }

  document.querySelectorAll(".geo-prop").forEach(function (prop) {
    var state = prop.dataset.state || "";
    prop.querySelectorAll('input.fixval[type="text"]').forEach(function (store) {
      attach(store, state);
    });
  });
})();
