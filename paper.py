"""The app's one display: a canvas that draws documents, and the fields a macro can fill.

There is no browser here and no HTML engine. Everything the window shows is a list of Boxes
— a résumé built from the model, an application form built from cached questions — flowed
down the page in a single pass and painted onto a tk.Canvas.

Laying out is "draw, measure, advance": create_text wraps its own words when handed a width,
and bbox reports the height it actually took, so the flow needs no font arithmetic of its
own. That one trick is what keeps a real renderer down to a few hundred lines.

Control boxes are the interactive part. A person clicks and types into them, and macro.py
aims at them by asking for center_of(); both write the same `value`, so a field the macro
filled and a field filled by hand are indistinguishable afterwards — which is what lets the
macro stop in the middle of a form, hand it over, and pick up again.
"""

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import font as tkfont

import theme

MARGIN = 30       # the page's own margin, inside the widget
LEAD = 4          # the space under an ordinary line
PADX, PADY = 9, 6  # inside a field's border
GUTTER = 15       # bullet and option text hang this far in
CARET = "▏"  # drawn by appending it to the text, so it lands after the last character

PICKERS = {"select", "radio", "checkbox", "multiselect"}
FILE_KINDS = {"file", "upload", "attachment"}  # layout and macro read this one

MAT = 22          # the bench left visible around a document
SHEET = 4         # the paper's own corner

# state -> (the theme token its border is drawn in, what the caption under the field says).
# Tokens rather than colours, because the preset can change while the page is on screen.
#
# ACCENT and CAUTION are the whole point of this table. Blue means the app answered the
# question by itself; amber means it has stopped and cannot go on without you. They used to
# be the same colour, which made the field you could ignore look like the field you could
# not.
STATES = {
    "empty": ("line", ""),
    "filled": ("line", ""),
    "borrowed": ("ACCENT", 'answered as “{source}”'),
    "waiting": ("CAUTION", "not in the cache yet — type an answer, then Resume"),
}


@dataclass
class Box:
    """One thing on the page. Geometry is filled in by the render pass, not by the caller."""

    kind: str = "body"  # title | head | sub | body | bullet | note | rule | gap | control | button
    text: str = ""
    right: str = ""     # a second column, set flush right on the same line
    indent: int = 0
    fill: str = ""      # "" means the kind's own colour
    space: int = 0      # extra room above

    # -- control state ----------------------------------------------------------------
    qid: str = ""       # what save_answer() is keyed on: the question's id, or its label
    label: str = ""
    control: str = "text"  # text | textarea | select | radio | checkbox | multiselect
    options: tuple = ()
    value: str = ""
    source: str = ""    # the question a borrowed answer was taken from
    required: bool = False
    state: str = "empty"

    # -- geometry, set while rendering --------------------------------------------------
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    items: list = field(default_factory=list)
    hits: list = field(default_factory=list)  # (y0, y1, option) for the pickers

    @property
    def is_control(self):
        return self.kind == "control"

    @property
    def answered(self):
        return bool(self.value.strip())


class Paper(tk.Canvas):
    """A scrolling page of Boxes.

    on_change(box) fires whenever a person edits a control by hand; on_commit(box) fires on
    Return. The macro listens to neither — it drives set_value/type_into directly — so the
    two paths never fight over the same field.
    """

    def __init__(self, parent, on_change=None, on_commit=None):
        super().__init__(parent, background=theme.CARD, highlightthickness=0, takefocus=True)
        theme.register(self, "paper")  # so a preset change reaches the canvas too
        self.surface = "form"
        self.doc = []
        self.focused = None
        self.on_change = on_change
        self.on_commit = on_commit
        self._fonts = {}
        self._width = 0
        self._height = 0

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Key>", self._on_key)
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind(seq, self._on_wheel)

    # ── what the page holds ─────────────────────────────────────────────────────────
    def show(self, doc, surface="form"):
        """`surface` is what the doc is: "form" draws it into the window's own panel,
        "page" draws it on a sheet of paper lying on a mat. A form is software and should
        read as software; a résumé is a document and has to look like the thing that comes
        out of the printer."""
        self.surface = surface if surface in ("form", "page") else "form"
        self.doc = list(doc or ())
        self.focused = None
        self.configure(background=self._skin()["ground"])
        self._render()
        self.yview_moveto(0)

    # ── colour ──────────────────────────────────────────────────────────────────────
    def _skin(self):
        """What this surface draws with. A document takes none of the preset's ink: paper
        is paper under every theme, because a résumé has to print and to photocopy."""
        if self.surface == "page":
            return {"ground": theme.MAT, "sheet": theme.PAPER, "field": theme.PAPER,
                    "ink": theme.PAPER_INK, "dim": theme.PAPER_DIM, "line": theme.PAPER_LINE}
        return {"ground": theme.CARD, "sheet": None, "field": theme.ROW,
                "ink": theme.INK, "dim": theme.MUTED, "line": theme.LINE}

    def restyle(self):
        """Called by theme.repaint() when the preset changes: reground, then redraw."""
        self.configure(background=self._skin()["ground"])
        self._render(keep=True)

    def cursor_colours(self):
        """What a pointer drawn over this surface has to be, to be visible on it."""
        skin = self._skin()
        return skin["ink"], skin["ground"]

    def _state(self, box):
        token, caption = STATES.get(box.state, STATES["empty"])
        colour = self._skin()["line"] if token == "line" else getattr(theme, token)
        return colour, caption

    # ── rounded corners, which tk.Canvas has no primitive for ───────────────────────
    @staticmethod
    def _round_points(x0, y0, x1, y1, r):
        """A rectangle as a spline: the corner points doubled up so `smooth` bends them
        and leaves the straights alone."""
        r = max(min(r, (x1 - x0) / 2, (y1 - y0) / 2), 0)
        return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
                x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        return self.create_polygon(self._round_points(x0, y0, x1, y1, r),
                                   smooth=True, splinesteps=12, **kw)

    def controls(self):
        return [b for b in self.doc if b.is_control]

    def button(self):
        """The page's own button, if it drew one — what the macro finishes on."""
        return next((b for b in self.doc if b.kind == "button"), None)

    # ── fonts and colour ────────────────────────────────────────────────────────────
    def _font(self, kind):
        """Sans for every label, date and aside; serif for prose — but only on paper.

        A form is software and reads as software. A document is prose, and prose on a page
        wants a serif, so the preview looks like what the PDF will be."""
        family, size = theme.FAMILY, theme.SIZE
        prose = theme.SERIF if self.surface == "page" else family
        return {"title": (prose, size + 9, "bold"),
                "head": (family, size + 1, "bold"),
                "strong": (prose, size, "bold"),
                "sub": (family, size - 1),
                "note": (family, size - 1),
                "mono": (theme.MONO, size - 1),
                "button": (family, size, "bold")}.get(kind, (prose, size))

    def _measure(self, text, spec):
        got = self._fonts.get(spec)
        if got is None:
            got = self._fonts[spec] = tkfont.Font(root=self, font=spec)
        return got.measure(text), got.metrics("linespace")

    def _colour(self, box):
        if box.fill:
            return box.fill
        skin = self._skin()
        return skin["dim"] if box.kind in ("sub", "note") else skin["ink"]

    # ── the render pass ─────────────────────────────────────────────────────────────
    def _on_configure(self, event):
        if abs(event.width - self._width) > 1:
            self._render(keep=True)

    def _render(self, keep=False):
        """Draw the whole page top to bottom. Re-entered on every resize, so it has to be
        cheap enough to run on a drag and exact enough that nothing shifts when it does."""
        was = self.canvasy(0)
        self.delete("all")
        for box in self.doc:
            box.items = []
            box.hits = []

        skin = self._skin()
        self._width = width = max(self.winfo_width(), 240)

        # On paper the sheet is drawn first so it sits under everything, then stretched to
        # whatever the content turned out to be — its height is not known until the flow is.
        inset = MAT if skin["sheet"] else 0
        sheet = None
        if skin["sheet"]:
            sheet = self._round_rect(inset, inset, width - inset, inset + 10, SHEET,
                                     fill=skin["sheet"], outline=skin["sheet"])

        left = inset + MARGIN
        inner = max(width - 2 * left, 160)
        y = inset + MARGIN
        for box in self.doc:
            y += box.space
            y = self._draw(box, left + box.indent, y, max(inner - box.indent, 80))
        y += MARGIN

        if sheet is not None:
            self.coords(sheet, *self._round_points(inset, inset, width - inset, y, SHEET))
            self.tag_lower(sheet)
            y += inset

        self._height = total = y
        self.configure(scrollregion=(0, 0, width, total))
        if keep and total > 0:
            self.yview_moveto(min(was, total) / total)

    def _draw(self, box, x, y, w):
        if box.kind == "gap":
            return y
        if box.kind == "rule":
            box.items.append(self.create_line(x, y + 2, x + w, y + 2,
                                              fill=self._skin()["line"]))
            return y + LEAD + 3
        if box.kind == "control":
            return self._draw_control(box, x, y, w)
        if box.kind == "button":
            return self._draw_button(box, x, y, w)
        return self._draw_text(box, x, y, w)

    def _draw_text(self, box, x, y, w):
        spec, colour = self._font(box.kind), self._colour(box)
        left, width = x, w

        if box.kind == "bullet":
            box.items.append(self.create_text(x, y, text="•", font=spec, fill=colour,
                                              anchor="nw"))
            left, width = x + GUTTER, w - GUTTER

        if box.right:  # a flush-right second column on the same line, e.g. a date range
            wide, _line = self._measure(box.right, self._font("sub"))
            box.items.append(self.create_text(x + w, y, text=box.right, font=self._font("sub"),
                                              fill=self._skin()["dim"], anchor="ne"))
            width = max(width - wide - 14, 60)

        item = self.create_text(left, y, text=box.text, font=spec, fill=colour,
                                width=width, anchor="nw")
        box.items.append(item)
        x0, y0, x1, y1 = self.bbox(item)
        box.x, box.y, box.w, box.h = left, y, width, y1 - y0
        return y + box.h + LEAD

    # ── controls ────────────────────────────────────────────────────────────────────
    def _draw_control(self, box, x, y, w):
        border, caption = self._state(box)
        if box is self.focused:
            border = theme.ACCENT
        top = y
        y = (self._draw_picker(box, x, y, w, border) if box.control in PICKERS and box.options
             else self._draw_field(box, x, y, w, border))

        if caption:
            # Drawn in the border's own colour, so the ring around the field and the words
            # under it are saying the same thing rather than two different things.
            note = Box("note", caption.format(source=box.source), fill=border)
            y = self._draw_text(note, x + 2, y + 2, w - 2)
            box.items += note.items
        box.y, box.x, box.w = top, x, w
        return y + LEAD

    def _draw_field(self, box, x, y, w, border):
        """A text field. The caret is appended to the string rather than drawn, so it lands
        after the last character without this having to know where Tk wrapped the line."""
        skin = self._skin()
        spec = self._font("mono" if box.control in FILE_KINDS else "body")
        _wide, line = self._measure("Ag", spec)
        shown = box.value + (CARET if box is self.focused else "")
        item = self.create_text(x + PADX + 1, y + PADY, text=shown, font=spec,
                                fill=skin["ink"], width=w - 2 * PADX - 2, anchor="nw")
        _x0, y0, _x1, y1 = self.bbox(item)
        rows = 3 if box.control == "textarea" else 1
        height = max(y1 - y0, line * rows) + 2 * PADY

        rect = self._round_rect(x, y, x + w, y + height, theme.RADIUS, fill=skin["field"],
                                outline=border, width=1 if border == skin["line"] else 2)
        self.tag_lower(rect, item)
        box.items += [rect, item]
        box.h = height
        return y + height

    def _draw_picker(self, box, x, y, w, border):
        """Radios, checkboxes and selects all read the same way once drawn: one option per
        row with a mark beside it, so there is a single hit box per option to click."""
        skin = self._skin()
        spec = self._font("body")
        _wide, line = self._measure("Ag", spec)
        chosen = {v.strip() for v in box.value.split("\n") if v.strip()}
        square = box.control in ("checkbox", "multiselect")
        top = y
        y += PADY
        for option in box.options:
            mark = option in chosen
            cy = y + line / 2
            fill = theme.ACCENT if mark else skin["field"]
            edge = theme.ACCENT if mark else skin["dim"]
            shape = (self._round_rect(x + PADX, cy - 6, x + PADX + 12, cy + 6, 3,
                                      outline=edge, fill=fill)
                     if square else
                     self.create_oval(x + PADX, cy - 6, x + PADX + 12, cy + 6,
                                      outline=edge, fill=fill))
            item = self.create_text(x + PADX + 12 + GUTTER, y, text=option, font=spec,
                                    fill=skin["ink"], width=w - PADX * 2 - 12 - GUTTER,
                                    anchor="nw")
            _x0, y0, _x1, y1 = self.bbox(item)
            box.items += [shape, item]
            box.hits.append((y, y + max(y1 - y0, line), option))
            y += max(y1 - y0, line) + LEAD
        y += PADY - LEAD
        rect = self._round_rect(x, top, x + w, y, theme.RADIUS, fill=skin["field"],
                                outline=border, width=1 if border == skin["line"] else 2)
        self.tag_lower(rect, box.items[0])
        box.items.insert(0, rect)
        box.h = y - top
        return y

    def _draw_button(self, box, x, y, w):
        spec = self._font("button")
        wide, line = self._measure(box.text, spec)
        width, height = wide + 44, line + 20
        done = box.state == "done"
        rect = self._round_rect(x, y, x + width, y + height, theme.RADIUS,
                                fill=theme.ACCENT if done else self._skin()["field"],
                                outline=theme.ACCENT, width=2)
        item = self.create_text(x + width / 2, y + height / 2, text=box.text, font=spec,
                                fill=theme.ON_ACCENT if done else theme.ACCENT)
        box.items += [rect, item]
        box.x, box.y, box.w, box.h = x, y, width, height
        return y + height + LEAD

    # ── editing ─────────────────────────────────────────────────────────────────────
    def refresh(self, box):
        """Redraw one box in place, falling back to the whole page when its height moved —
        which is what happens when an answer wraps onto another line."""
        before = box.h
        for item in box.items:
            self.delete(item)
        box.items, box.hits = [], []
        self._draw(box, box.x, box.y, box.w)
        if abs(box.h - before) > 0.5:
            self._render(keep=True)

    def set_value(self, box, value):
        box.value = value
        if box.state in ("empty", "waiting") and value.strip():
            box.state = "filled"
        self.refresh(box)

    def type_into(self, box, char):
        """One character, the way the macro spends them."""
        box.value += char
        self.refresh(box)

    def set_state(self, box, state):
        box.state = state
        self.refresh(box)

    def focus_box(self, box):
        was, self.focused = self.focused, box
        self.focus_set()
        if was is not None and was is not box:
            self.refresh(was)
        if box is not None:
            self.refresh(box)

    def next_control(self, box):
        controls = self.controls()
        if box in controls and controls.index(box) + 1 < len(controls):
            return controls[controls.index(box) + 1]
        return None

    # ── pointing ────────────────────────────────────────────────────────────────────
    def center_of(self, box):
        """Where a pointer should land on this box, in canvas coordinates. Tall fields are
        aimed near their top, where a person would click, not at their middle."""
        return box.x + min(box.w / 2, 150), box.y + min(box.h / 2, 16)

    def scroll_to(self, box, margin=60):
        if self._height <= 0:
            return
        top, bottom = self.canvasy(0), self.canvasy(0) + self.winfo_height()
        if box.y >= top + margin and box.y + box.h <= bottom - margin:
            return
        self.yview_moveto(max(0.0, (box.y - margin * 2) / self._height))

    def at(self, x, y):
        """The control or button under a point in canvas coordinates."""
        for box in self.doc:
            if box.kind in ("control", "button") and box.y <= y <= box.y + box.h \
                    and box.x <= x <= box.x + box.w:
                return box
        return None

    # ── events ──────────────────────────────────────────────────────────────────────
    def _on_wheel(self, event):
        if getattr(event, "num", 0) in (4, 5):
            step = -1 if event.num == 4 else 1
        elif abs(event.delta) >= 120:
            step = -event.delta // 120
        else:
            step = -event.delta
        self.yview_scroll(int(step), "units")

    def _on_click(self, event):
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        box = self.at(x, y)
        self.focus_set()
        if box is None or box.kind == "button":
            self.focus_box(None)
            if box is not None and self.on_commit:
                self.on_commit(box)
            return
        for y0, y1, option in box.hits:  # a picker: the click lands on one option
            if y0 <= y <= y1:
                self._pick(box, option)
                break
        self.focus_box(box)

    def _pick(self, box, option):
        chosen = [v for v in box.value.split("\n") if v.strip()]
        if box.control in ("checkbox", "multiselect"):
            chosen = [v for v in chosen if v != option] if option in chosen else chosen + [option]
        else:
            chosen = [] if chosen == [option] else [option]
        box.value = "\n".join(chosen)
        if box.state in ("empty", "waiting") and box.value:
            box.state = "filled"
        if self.on_change:
            self.on_change(box)

    def _on_key(self, event):
        box = self.focused
        if box is None:
            return
        if event.keysym in ("Tab", "ISO_Left_Tab"):
            self.focus_box(self.next_control(box))
            return "break"
        if event.keysym == "BackSpace":
            box.value = box.value[:-1]
        elif event.keysym == "Return":
            if box.control == "textarea":
                box.value += "\n"
            else:
                if self.on_commit:
                    self.on_commit(box)
                return "break"
        elif event.char and event.char.isprintable():
            box.value += event.char
        else:
            return
        if box.state in ("empty", "waiting") and box.value.strip():
            box.state = "filled"
        self.refresh(box)
        self.scroll_to(box)
        if self.on_change:
            self.on_change(box)
        return "break"
