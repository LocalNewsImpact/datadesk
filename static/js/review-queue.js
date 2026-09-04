/* A queue worked as a session: mark decisions on the way down, send the lot.
 *
 * Both review queues behave this way, and only one of them used to. The
 * proposals page carried this as an inline script; the extraction queue was
 * built with the same markup -- `.prop` rows, `.verb` buttons, a hidden
 * `d-<id>` field per row, a dock with tallies -- and no script at all, so its
 * buttons recorded nothing, its tallies stayed at zero and Submit was disabled
 * with nothing able to enable it. The queue could be read and not worked.
 *
 * One file, so a change to how a decision is recorded cannot apply to one
 * queue and not the other.
 *
 * The markup contract:
 *
 *   form#queue-form            wraps the rows; submits them
 *     .prop[data-id]           one decidable thing
 *       .verb[data-verb]       a button; pressing it records that verb
 *       input[type=hidden]     where the verb is recorded (name="d-<id>")
 *       .fixval                optional -- a value the verb writes
 *       .qualval               optional -- something said ALONGSIDE the
 *                              verb, not instead of it. Choosing one is
 *                              not a decision and does not make the row
 *                              submittable on its own.
 *   .queue-dock
 *     [data-tally="<verb>"]    a count of rows carrying that verb
 *     #q-incomplete            rows decided but missing a required value
 *     #q-clear, #q-submit
 *
 * A page may register `window.reviewQueueDescribe`, called with each row after
 * it changes, to write its own outcome line. Nothing here requires it.
 */
(function () {
  "use strict";

  const form = document.getElementById("queue-form");
  if (!form) return;

  const dock = document.querySelector(".queue-dock");
  const submit = document.getElementById("q-submit");
  const clear = document.getElementById("q-clear");
  const incompleteNote = document.getElementById("q-incomplete");

  const store = (row) => row.querySelector('input[type="hidden"]');
  const fixval = (row) => row.querySelector(".fixval");
  const rows = () => form.querySelectorAll(".prop");

  function describe(row) {
    if (typeof window.reviewQueueDescribe === "function") {
      window.reviewQueueDescribe(row, form);
    }
  }

  function recount() {
    const counts = {};
    let incomplete = 0;
    rows().forEach((row) => {
      const field = store(row);
      if (!field || !field.value) return;
      counts[field.value] = (counts[field.value] || 0) + 1;
      // A verb that writes a value is not a decision until the value is
      // there. Counted separately and named in the dock, because the row
      // otherwise submits as nothing and comes back looking undecided.
      //
      // Only the verb that writes one. Every proposal row carries a fix
      // box, so testing the box alone would call an `accept` incomplete
      // and refuse to submit it.
      // A verb that cannot be carried out without the queue's qualifier
      // is not a decision until the qualifier is answered. On a row
      // enrichment has finished with, Reject means "the type is wrong"
      // and says nothing about which type -- so it waits.
      const chosen = row.querySelector(`.verb[data-verb="${field.value}"]`);
      const qualifier = row.querySelector(".qualval");
      if (chosen && chosen.dataset.needsValue === "1") {
        if (!qualifier || !qualifier.value) incomplete += 1;
      }
      const value = fixval(row);
      if (value && field.value === (row.dataset.fixVerb || "fix") && !value.value.trim()) {
        incomplete += 1;
      }
    });

    let decided = 0;
    if (dock) {
      dock.querySelectorAll("[data-tally]").forEach((el) => {
        const n = counts[el.dataset.tally] || 0;
        el.textContent = n;
        decided += n;
      });
    }

    const ready = decided - incomplete;
    if (submit) {
      submit.disabled = ready === 0;
      submit.textContent = ready
        ? "Submit " + ready + (ready > 1 ? " decisions" : " decision")
        : "Submit";
    }
    if (incompleteNote) {
      incompleteNote.textContent = incomplete
        ? incomplete +
          (incomplete > 1 ? " decisions have" : " decision has") +
          " no value and will stay in the queue"
        : "";
    }
  }

  function mark(row, verb) {
    const field = store(row);
    if (!field) return;
    field.value = verb;
    row.dataset.verb = verb;
    row.classList.toggle("decided", Boolean(verb));
    row.querySelectorAll(".verb").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.verb === verb));
    });
    describe(row);
  }

  form.addEventListener("click", (event) => {
    const button = event.target.closest(".verb");
    if (!button || !form.contains(button)) return;
    const row = button.closest(".prop");
    if (!row) return;
    const field = store(row);
    // Pressing the chosen verb again withdraws the decision. A queue with
    // no way to undo a click is one people work slowly and carefully
    // rather than quickly and reversibly.
    const chosen = field.value === button.dataset.verb ? "" : button.dataset.verb;
    mark(row, chosen);
    const value = fixval(row);
    if (chosen && value && !value.value.trim()) value.focus();
    recount();
  });

  form.addEventListener("input", (event) => {
    // A qualifier answers a second, independent question -- what the
    // thing actually is -- and a row carrying one with no verb has not
    // been decided. Setting a verb here would submit rows nobody judged.
    if (event.target.classList.contains("qualval")) {
      const row = event.target.closest(".prop");
      if (row) {
        // Answering the qualifier can complete a verb that was waiting
        // for it, so the dock and the outcome line both have to look
        // again.
        row.classList.toggle(
          "decided",
          Boolean(store(row) && store(row).value)
        );
        describe(row);
      }
      recount();
      return;
    }
    if (!event.target.classList.contains("fixval")) return;
    const row = event.target.closest(".prop");
    if (!row) return;
    const typed = event.target.value.trim();
    const field = store(row);
    // The verb this value belongs to, named on the input by the template.
    // It used to default to "fix" -- a name the extraction queue does not
    // offer -- so choosing a category set the row's verb to something no
    // tally counted and no submit path accepted, and Submit stayed
    // disabled with a decision visibly made.
    const verb = event.target.dataset.verb || row.dataset.fixVerb;
    if (!verb) return;
    // Typing the value is the decision; clearing it withdraws the
    // decision rather than leaving one with nothing to write.
    if (typed) {
      mark(row, verb);
    } else if (field.value === verb) {
      mark(row, "");
    } else {
      describe(row);
    }
    recount();
  });

  function reset() {
    // A browser restores form fields across a refresh, so a session that
    // looks new would carry decisions nobody made in it.
    rows().forEach((row) => {
      const value = fixval(row);
      if (value) value.value = "";
      const qualifier = row.querySelector(".qualval");
      if (qualifier) qualifier.value = "";
      mark(row, "");
    });
    recount();
  }

  if (clear) clear.addEventListener("click", reset);

  // The extraction queue swaps its results in place, which replaces every
  // row. Decisions marked before a filter changed are gone with them, so
  // the dock has to stop claiming they are there.
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (form.contains(event.target) || event.target === form) reset();
  });

  reset();
})();
