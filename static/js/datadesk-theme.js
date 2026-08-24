/*
 * The colour theme, in three states.
 *
 * "auto" follows the operating system and is what somebody gets before
 * they choose. The other two are a decision that outlives the machine's:
 * a person who wants dark all day on a laptop set to light should not have
 * to change the laptop.
 *
 * The choice is stamped on <html> as data-theme and remembered in
 * localStorage. Nothing is sent anywhere -- it is a preference about this
 * browser, not a fact about the account.
 */
(function () {
  "use strict";
  var KEY = "datadesk-theme";
  var root = document.documentElement;

  function read() {
    try {
      return localStorage.getItem(KEY) || "system";
    } catch (e) {
      // Private windows and blocked site data throw on access rather than
      // returning null. The page still has to render.
      return "system";
    }
  }

  function apply(choice) {
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    var buttons = document.querySelectorAll("[data-theme-choice]");
    for (var i = 0; i < buttons.length; i++) {
      var mine = buttons[i].dataset.themeChoice === choice;
      buttons[i].classList.toggle("on", mine);
      buttons[i].setAttribute("aria-pressed", mine ? "true" : "false");
    }
  }

  function choose(choice) {
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch (e) {
      // Unremembered is better than unusable: apply it for this page.
    }
    apply(choice);
  }

  apply(read());
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-theme-choice]");
    if (button) choose(button.dataset.themeChoice);
  });
})();
