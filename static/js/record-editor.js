/* Editing a publisher without leaving the page that asks about it.
 *
 * The review queue asks four questions about one publisher; the paywalls
 * page asks whether it is worth subscribing to. Both are often answered
 * by editing the record, and leaving the page to do it loses the work in
 * progress -- the queue's decisions, or the reader's place in a list of
 * fifty-seven.
 *
 * The dialog holds the same edit page the link points at, asked for
 * bare. Where a dialog is unavailable or this file has not run, the link
 * is a link and goes to the page it names.
 */
(function () {
// Editing the publisher without leaving the queue.
//
// The dialog holds the same edit page the link points at, asked for
// bare. Saving it reloads the queue, because the questions on the card
// are about the values that were just changed -- and a card still
// asking about a value somebody has corrected is the thing that makes
// a queue untrustworthy.
const editor = document.createElement("dialog");
editor.className = "rec-editor";
document.body.appendChild(editor);

// `.rec-edit` opens a record to change it; `.rec-read` opens a page to
// read it. The extraction queue needs the second: deciding whether a
// classification is wrong means reading the text, and leaving the page
// to do it loses every decision marked on the way down.
//
// One dialog and one fetch for both. The difference is that a read has
// no form to wire, which `wire` already handles by returning.
document.addEventListener("click", async (event) => {
  const link = event.target.closest(".rec-edit, .rec-read");
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
  // Only when a dialog is available. Where it is not, the link is a
  // link and goes to the page it names.
  if (typeof editor.showModal !== "function") return;
  event.preventDefault();
  editor.innerHTML = '<p class="notice">Opening…</p>';
  editor.showModal();
  try {
    const response = await fetch(link.href + "?bare=1", {
      credentials: "same-origin",
    });
    editor.innerHTML = await response.text();
    wire(link.href);
  } catch (err) {
    editor.innerHTML =
      '<p class="notice bad">It could not be opened: ' + err + "</p>";
  }
});

function wire(href) {
  const close = document.createElement("button");
  close.type = "button";
  close.className = "linklike rec-editor-close";
  close.textContent = "Close";
  close.onclick = () => editor.close();
  editor.prepend(close);
  const form = editor.querySelector("form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const response = await fetch(href + "?bare=1", {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
    });
    const text = await response.text();
    if (!response.ok) {
      // The form comes back with what it refused and why, which is the
      // same answer the full page gives.
      editor.innerHTML = text;
      wire(href);
      return;
    }
    // Saved. The queue is reloaded rather than patched, so what it
    // asks and what the record says cannot disagree.
    window.location.reload();
  });
}
})();
