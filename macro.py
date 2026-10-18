"""The macro: a pointer that walks an application form the way a person would.

It moves to a field, clicks it, types the answer the cache holds, and goes on to the next
one — and when it reaches a question the cache has no answer for, it stops. That pause is
the point of the whole thing. The answer typed during it is saved against the question
rather than the listing, so the next form that asks the same thing is already filled in and
the macro runs further before it has to stop again.

Everything here is driven by root.after on Tk's own thread. The pane this replaced spawned
a thread per click and swallowed the exception; a macro that types into widgets has to run
where the widgets live, and a state machine on a timer is both simpler and observable.
"""

from collections import namedtuple

from layout import FILE_KINDS
from paper import PICKERS

TAG = "macro-cursor"
ARROW = (0, 0, 0, 17, 4, 13, 7, 19, 10, 18, 7, 12, 12, 12)

Speed = namedtuple("Speed", "tick move chars")
SPEEDS = {"Slow": Speed(45, 26, 1),
          "Normal": Speed(24, 15, 1),
          "Fast": Speed(12, 8, 3),
          "Instant": Speed(1, 1, 500)}
DEFAULT_SPEED = "Normal"


class Macro:
    """on_status(text) writes the status line, on_answer(box) persists an answer the person
    typed during a pause, and on_state(state) lets the buttons follow along."""

    def __init__(self, paper, on_status=None, on_answer=None, on_state=None):
        self.paper = paper
        self.on_status = on_status or (lambda _t: None)
        self.on_answer = on_answer or (lambda _b: None)
        self.on_state = on_state or (lambda _s: None)

        self.state = "idle"
        self.speed = SPEEDS[DEFAULT_SPEED]
        self.plan = []
        self.index = 0
        self.waiting = None   # the box a cache miss stopped on, if that is why we are held
        self.single = False
        self.pos = (40.0, 40.0)
        self._job = None
        self._frame = 0
        self._from = self.pos
        self._to = self.pos
        self._typed = 0
        self.filled = self.borrowed = self.by_hand = 0

    # ── running ─────────────────────────────────────────────────────────────────────
    def start(self, speed=DEFAULT_SPEED):
        """Blank every field first, then fill them back in.

        The cache has already put its answers on the controls, but a form that is complete
        before the macro touches it shows nothing; emptying them is what makes the run
        legible, and the answer to type is kept here rather than on the page."""
        self.stop(quiet=True)
        self.speed = SPEEDS.get(speed, SPEEDS[DEFAULT_SPEED])
        self.plan = []
        for box in self.paper.controls():
            self.plan.append({"box": box, "answer": box.value,
                              "state": box.state, "source": box.source})
            box.value, box.state = "", "empty"
            self.paper.refresh(box)
        self.filled = self.borrowed = self.by_hand = 0
        self.index = 0
        self.waiting = None
        self.single = False
        if not self.plan:
            self.on_status("Nothing to apply to — pick a listing whose form has loaded.")
            return
        self._draw_cursor()
        self._begin()

    def step(self, speed=DEFAULT_SPEED):
        """One question, then hold — the same walk, stopped at the end of the item."""
        if self.state in ("idle", "done"):
            self.start(speed)
        elif self.state == "paused":
            self.resume()
        self.single = True

    def pause(self):
        if self.state in ("moving", "clicking", "typing"):
            self._halt("paused")
            self.on_status(f"Held at question {self.index + 1} of {len(self.plan)}.")

    def resume(self):
        """Carry on. If the run stopped for an answer, this is where that answer is kept.

        Which box we stopped on is remembered rather than read back off the page: typing an
        answer into a waiting field marks it filled the moment the first character lands, so
        by the time anyone presses Resume the field no longer looks like the one we held
        for."""
        if self.state != "paused":
            return
        box, self.waiting = self.waiting, None
        if box is not None:
            if box.required and not box.answered:
                self.waiting = box
                self.on_status(f"Question {self.index + 1} is required — it needs an answer "
                               "before the run can go on.")
                return
            if box.answered:
                box.state = "filled"
                self.paper.refresh(box)
                self.on_answer(box)      # cached against the question, for every later form
                self.by_hand += 1
                self.plan[self.index]["answer"] = box.value
            self.index += 1
        self.single = False
        self._begin()

    def stop(self, quiet=False):
        if self._job is not None:
            try:
                self.paper.after_cancel(self._job)
            except Exception:
                pass
        self._job = None
        self.state = "idle"
        self.paper.delete(TAG)
        if not quiet:
            self.on_state("idle")
            self.on_status("Macro stopped.")

    # ── the walk ────────────────────────────────────────────────────────────────────
    def _begin(self):
        """Aim at the next field, or at the button when the questions run out."""
        if self.index >= len(self.plan):
            target = self.paper.button()
            if target is None:
                return self._finish()
            self.paper.scroll_to(target)
            return self._aim(target, "finishing")
        box = self.plan[self.index]["box"]
        self.paper.scroll_to(box)
        self.on_status(f"Question {self.index + 1} of {len(self.plan)} — {box.label[:60]}")
        self._aim(box, "clicking")

    def _aim(self, box, after):
        self._from = self.pos
        self._to = self.paper.center_of(box)
        self._frame = 0
        self._after_move = after
        self._go("moving")

    def _go(self, state):
        self.state = state
        self.on_state(state)
        if self._job is None:
            self._job = self.paper.after(self.speed.tick, self._tick)

    def _halt(self, state):
        if self._job is not None:
            try:
                self.paper.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self.state = state
        self.on_state(state)

    def _tick(self):
        self._job = None
        if self.state in ("idle", "paused", "done"):
            return
        self._draw_cursor()
        getattr(self, "_do_" + self.state, self._finish)()
        # A handler that moved the run on to the next field has already booked the next
        # tick through _go; booking a second one here would leave two timers running and
        # the macro would quietly speed up every time it changed fields.
        if self.state not in ("idle", "paused", "done") and self._job is None:
            self._job = self.paper.after(self.speed.tick, self._tick)

    def _do_moving(self):
        self._frame += 1
        span = max(self.speed.move, 1)
        t = min(self._frame / span, 1.0)
        ease = t * t * (3 - 2 * t)  # slow out of one field and into the next
        x = self._from[0] + (self._to[0] - self._from[0]) * ease
        y = self._from[1] + (self._to[1] - self._from[1]) * ease
        self._move_cursor(x, y)
        if t >= 1.0:
            self.state = self._after_move

    def _do_clicking(self):
        """The click lands: the field takes focus, and what happens next depends entirely on
        whether the cache had anything for it."""
        item = self.plan[self.index]
        box, answer = item["box"], item["answer"]
        self.paper.focus_box(box)

        if not answer.strip() and box.control not in FILE_KINDS:
            box.state = "waiting"
            self.waiting = box
            self.paper.refresh(box)
            self.paper.scroll_to(box)
            self._halt("paused")
            self.on_status(f"Paused — question {self.index + 1} of {len(self.plan)} is not in "
                           f"the cache: “{box.label[:70]}”. Type an answer, "
                           "then Resume.")
            return

        if box.control in PICKERS or box.control in FILE_KINDS:
            box.value = answer                       # a pick is one gesture, not typing
            box.state, box.source = item["state"] or "filled", item["source"]
            self.paper.refresh(box)
            self._tally(item)
            self._advance()
            return
        self._typed = 0
        self.state = "typing"

    def _do_typing(self):
        item = self.plan[self.index]
        box, answer = item["box"], item["answer"]
        take = answer[len(box.value):len(box.value) + max(self.speed.chars, 1)]
        if not take:
            box.state, box.source = item["state"] or "filled", item["source"]
            self.paper.refresh(box)
            self._tally(item)
            return self._advance()
        box.value += take
        self.paper.refresh(box)

    def _tally(self, item):
        if item["state"] == "borrowed":
            self.borrowed += 1
        else:
            self.filled += 1

    def _advance(self):
        self.index += 1
        if self.single:
            self._halt("paused")
            self.on_status(f"Stepped — {self.index} of {len(self.plan)} done.")
            return
        self._begin()

    def _do_finishing(self):
        button = self.paper.button()
        if button is not None:
            button.state = "done"
            self.paper.refresh(button)
        self._finish()

    def _finish(self):
        self._halt("done")
        self.paper.focus_box(None)
        self.paper.delete(TAG)
        parts = [f"{self.filled} from the cache"]
        if self.borrowed:
            parts.append(f"{self.borrowed} borrowed from questions worded like them")
        if self.by_hand:
            parts.append(f"{self.by_hand} answered by you and saved")
        self.on_status(f"Simulated the application — {', '.join(parts)}. Nothing was sent.")

    # ── the pointer ─────────────────────────────────────────────────────────────────
    def _draw_cursor(self):
        """Recreated whenever it is missing: a field whose answer changed height forces the
        page to lay out again, and that clears every item on the canvas, this one included."""
        if not self.paper.find_withtag(TAG):
            self._move_cursor(*self.pos)

    def _move_cursor(self, x, y):
        self.pos = (x, y)
        self.paper.delete(TAG)
        points = [x + ARROW[i] if i % 2 == 0 else y + ARROW[i] for i in range(len(ARROW))]
        fill, edge = self.paper.cursor_colours()  # a pointer has to show on its own surface
        self.paper.create_polygon(points, fill=fill, outline=edge, width=1, tags=TAG)
