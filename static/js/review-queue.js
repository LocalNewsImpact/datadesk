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

  // BY NAME, not "the first hidden input in the row".
  //
  // The decision field is `name="d-<id>"` -- the line above has said so
  // since this was written -- and the selector looked for a TYPE instead.
  // That held only while no other hidden input existed in a `.prop`. The
  // geography queue's chip field hides the value boxes, which made one of
  // them the first hidden input: the pressed verb was written into
  // `v-<id>-set_place` and `d-<id>` was never set at all. `posted()`
  // reads only `d-` keys, so every submission posted no decisions,
  // returned 302, and wrote nothing -- three times in production before
  // the form data was read rather than the code.
  const store = (row) => row.querySelector('input[name^="d-"]');
  // The box belonging to the verb that was pressed, where the row has
  // one box per verb. A row whose verbs take different vocabularies has
  // two, and testing the first one asked about a list the reviewer was
  // not answering.
  const fixval = (row, verb) =>
    (verb && row.querySelector(`.fixval[data-verb="${verb}"]`)) ||
    row.querySelector(".fixval");
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
      // A verb that writes a value is not a decision until it has one.
      // Rejecting says the call was wrong and the list says what it
      // should have been; half an answer waits rather than submitting.
      const chosen = row.querySelector(`.verb[data-verb="${field.value}"]`);
      const value = fixval(row, field.value);
      if (value && field.value === (row.dataset.fixVerb || "fix") && !value.value.trim()) {
        incomplete += 1;
      }
    });

    // How many decisions are marked, counted from the ROWS.
    //
    // This used to be summed from the dock's tallies, which made the
    // submit button depend on the dock listing every verb. The extraction
    // queue gained `restore` and the dock did not, so a restored row
    // counted zero: the decision was recorded, the row showed as decided,
    // and Submit stayed disabled with nothing on the page explaining why.
    //
    // The dock is a display of the count, never its source. A verb it has
    // no tally for is now an unlabelled decision rather than a lost one.
    let decided = 0;
    Object.keys(counts).forEach((verb) => {
      decided += counts[verb];
    });
    if (dock) {
      dock.querySelectorAll("[data-tally]").forEach((el) => {
        el.textContent = counts[el.dataset.tally] || 0;
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
    // Only the control this verb writes into. The row holds one value
    // control and it belongs to a single verb -- the template names that
    // verb on it -- so focusing it on any press put the reject list up on
    // Accept and on Restore too. On a phone that is a native picker
    // covering the row being decided, opened by a button that has nothing
    // to do with it.
    const value = fixval(row, chosen);
    const itsOwn = value && value.dataset.verb === chosen;
    if (chosen && itsOwn && !value.value.trim()) value.focus();
    recount();
  });

  form.addEventListener("input", (event) => {
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
      // Every box on the row, not the first: a row whose verbs take
      // different vocabularies carries one each, and clearing only the
      // first left the other holding an answer nobody could see.
      row.querySelectorAll(".fixval").forEach((value) => {
        value.value = "";
      });
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
