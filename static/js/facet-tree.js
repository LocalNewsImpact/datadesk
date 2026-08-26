/* The facet tree's behaviour, in one place.
 *
 * State, then county, then the things themselves. A parent checkbox is a
 * shortcut, not a value: it never submits, it sets the leaves under it, and
 * its own state is read back from them -- so "some of these" shows as
 * indeterminate rather than as a parent claiming its children are all
 * chosen.
 *
 * This existed twice, once in the newsroom step and once in the fields
 * step, character for character apart from the leaf name being hardcoded
 * in one of them. The explorer wanted it a third time, which is what made
 * the copying worth stopping.
 */
(function (global) {
  "use strict";

  function wire(tree, name) {
    if (!tree) return;
    var selector = 'input[name="' + name + '"]';
    var leavesUnder = function (el) {
      return [].slice.call(el.querySelectorAll(selector));
    };

    function refresh(el) {
      var box = el.querySelector("summary > .branch");
      if (!box) return;
      var leaves = leavesUnder(el);
      var on = leaves.filter(function (l) { return l.checked; }).length;
      box.checked = on === leaves.length && leaves.length > 0;
      box.indeterminate = on > 0 && on < leaves.length;
    }

    var refreshAll = function () {
      tree.querySelectorAll("details").forEach(refresh);
    };

    tree.addEventListener("change", function (e) {
      if (e.target.classList.contains("branch")) {
        leavesUnder(e.target.closest("details")).forEach(function (l) {
          l.checked = e.target.checked;
        });
      }
      refreshAll();
    });

    // A click on the parent box should tick it, not open the disclosure.
    tree.querySelectorAll(".branch").forEach(function (b) {
      b.addEventListener("click", function (e) { e.stopPropagation(); });
    });

    refreshAll();
  }

  global.DatadeskFacetTree = { wire: wire };
})(window);
