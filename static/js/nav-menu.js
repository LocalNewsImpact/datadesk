/* The site nav, closed on a phone.

   Below 60rem the sidebar is already a wrapped row rather than a column,
   and it is still six groups and about 330px of an 844px screen before
   the page starts -- so every queue opened on a phone begins with a
   scroll past the nav to reach the work.

   The nav is open in the markup and this closes it. That order matters:
   a script that fails leaves the menu as the list it has always been,
   where the other order would leave somebody with no navigation at all.

   The breakpoint is the one the sidebar already changes shape at, read
   from a media query rather than from window.innerWidth, so the two
   cannot drift apart and a rotation is handled by the browser. */
(function () {
  "use strict";

  var button = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (!button || !nav) return;

  var narrow = window.matchMedia("(max-width: 60rem)");

  function show(open) {
    nav.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }

  /* Wide screens have no button and must never be left with a hidden
     nav -- including when a phone-width window is dragged wider with the
     menu closed. */
  function fit() {
    show(!narrow.matches);
  }

  button.addEventListener("click", function () {
    show(nav.hidden);
  });

  /* A link inside the menu closes it on the way out. Without this the
     menu is still open behind the new page for the moment before it
     paints, which reads as a click that did nothing. */
  nav.addEventListener("click", function (event) {
    if (narrow.matches && event.target.closest("a")) show(false);
  });

  if (narrow.addEventListener) narrow.addEventListener("change", fit);
  else narrow.addListener(fit);

  fit();
})();
