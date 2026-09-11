/* The filter bar, for the queues that post it as a plain form.
 *
 * The extraction queue drives the same controls with htmx
 * (`hx-trigger="change, submit"`), so picking a dataset there filters
 * immediately. Discovery and geography post an ordinary GET form and had
 * nothing bound to it at all: every control was inert, and the only way
 * to filter was the button inside <noscript> -- which, JavaScript being
 * on, nobody could see. A reviewer changed the dataset and the page did
 * not move.
 *
 * `.js-custom-range` was dead the same way. It ships with `hidden` unless
 * the window is already "custom", and only a script removes that -- so
 * choosing "Custom…" revealed nothing and the two date fields could not
 * be reached except by typing the query string.
 */
(function () {
  "use strict";

  var bar = document.querySelector(".filter-bar");
  if (!bar) return;

  var windowSelect = bar.querySelector(".js-window");
  var range = bar.querySelector(".js-custom-range");
  var since = bar.querySelector('input[name="since"]');
  var until = bar.querySelector('input[name="until"]');

  /* Picking "From" places "To" the default window later, so a custom
   * range starts as a window beginning where the reviewer pointed and
   * only the second date needs touching when that is not what they
   * meant. A "To" the reviewer typed is theirs and is never moved; a
   * "To" the script placed follows "From" until they edit it. */
  var placed = null;

  function showRange() {
    if (!range || !windowSelect) return;
    range.classList.toggle("hidden", windowSelect.value !== "custom");
  }

  if (windowSelect && range) {
    windowSelect.addEventListener("change", showRange);
    showRange();
  }

  function placeUntil() {
    if (!since || !until || !since.value) return;
    if (until.value && until.value !== placed) return;
    var days = parseInt(bar.dataset.defaultDays || "30", 10);
    var end = new Date(since.value + "T00:00:00Z");
    end.setUTCDate(end.getUTCDate() + days);
    placed = end.toISOString().slice(0, 10);
    until.value = placed;
  }

  bar.addEventListener("change", function (event) {
    var control = event.target;
    if (!control.name) return;

    /* Choosing "Custom…" is not a filter yet -- it asks for two dates
     * that have not been given. Submitting here would reload the page
     * with an empty range and take the reviewer's rows away before they
     * could say which ones they wanted. */
    if (control === windowSelect && control.value === "custom") {
      showRange();
      if (since) since.focus();
      return;
    }

    if (control === since) placeUntil();

    /* CASCADING IS THE SERVER'S. It recomputes which counties this
     * dataset has and which newsrooms that county has on every request,
     * and drops a selection the new scope cannot offer. Submitting is
     * all the page has to do. */
    bar.submit();
  });
})();
