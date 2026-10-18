"""Every field the résumé model carries gets a prompt here; the GUI is its only input.

Layout is a preview pane on the left and a notebook of forms on the right. The forms are
built from the specs below rather than by hand, so a new field in generate_resume.py costs
one line here and no new widget code. Colours and fonts live in theme.py; the listing and
form archives live in cache_mem_access.py.
"""

import html
import os
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import asdict
from tkinter import messagebox, ttk
from typing import NamedTuple
from urllib.request import pathname2url

import cache_mem_access as store
import generate_resume as gr
import layout
import paper
import scout
import session
import theme
from macro import DEFAULT_SPEED, SPEEDS, Macro

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BIO_KEY = "resume"  # namespaced, so an older flat bio.json stays out of the way


# ── field specs ──────────────────────────────────────────────────────────────────────

class Field(NamedTuple):
    """One prompt. `kind` decides both the widget and how its text becomes a model value."""

    key: str
    label: str
    kind: str = "line"  # line | text | list | csv | pairs
    rows: int = 3


class Section(NamedTuple):
    """A repeating part of the résumé: many entries of one dataclass."""

    key: str  # the Resume attribute it fills
    label: str
    cls: type
    fields: tuple


PROFILE = (
    Field("name", "Full name"),
    Field("email", "Email"),
    Field("phone", "Phone"),
    Field("location", "Address — the line printed under your name"),
    Field("summary", "Summary — used by the letter and the matcher; this design prints none",
          "text", 4),
    Field("links", "Links — one per line, 'label | url'", "pairs", 3),
    Field("skills", "Skills — one group per line, 'Languages | Python, Go, SQL'", "pairs", 5),
)

_DATES = (Field("start", "Started — YYYY-MM"),
          Field("end", "Ended — YYYY-MM, blank means present"))
_TAGS = Field("tags", "Tags — comma separated; what the matcher ranks against", "csv", 2)

SECTIONS = (
    Section("experience", "Experience", gr.Experience, (
        Field("company", "Company"),
        Field("title", "Job title"),
        Field("location", "Location"),
        *_DATES,
        Field("url", "Company or posting link"),
        Field("bullets", "What you did — one bullet per line, numbers wherever you have them",
              "list", 8),
        _TAGS)),
    Section("projects", "Projects", gr.Project, (
        Field("name", "Project name"),
        Field("blurb", "One line on what it is"),
        *_DATES,
        Field("url", "Repository or demo link"),
        Field("stack", "Stack — comma separated", "csv", 2),
        Field("bullets", "What it does and what it proves — one bullet per line", "list", 7),
        _TAGS)),
    Section("education", "Education", gr.Education, (
        Field("school", "School — repeat it to put two degrees in one block"),
        Field("degree", "Degree and field"),
        Field("concentration", "Concentration — the small line under the degree"),
        Field("gpa", "GPA — blank to leave it off"),
        Field("location", "Location"),
        Field("start", "Started — YYYY-MM, optional"),
        Field("end", "Awarded — YYYY-MM; a date still ahead puts Education first"),
        Field("coursework", "Relevant coursework — one per line, trailing * for graduate-level",
              "courses", 8),
        Field("url", "School or thesis link"),
        Field("bullets", "Honours or thesis — one per line", "list", 4),
        _TAGS)),
    Section("leadership", "Leadership", gr.Leadership, (
        Field("org", "Organisation"),
        Field("role", "Role"),
        Field("location", "Location — blank in this design's template"),
        *_DATES,
        Field("url", "Link"),
        Field("bullets", "What you did — one bullet per line", "list", 5),
        _TAGS)),
)


# ── text ↔ value ─────────────────────────────────────────────────────────────────────

def _lines(text):
    return tuple(s.strip() for s in text.splitlines() if s.strip())


def _csv(text):
    return tuple(s.strip() for s in text.replace("\n", ",").split(",") if s.strip())


def _pairs(text):
    """'label | url' per line. A line with no bar pairs with itself, which is what a bare
    URL or an ungrouped skill line means."""
    out = []
    for line in _lines(text):
        left, _, right = line.partition("|")
        out.append((left.strip(), right.strip() or left.strip()))
    return tuple(out)


def _courses(text):
    """One course per line; a trailing * marks it graduate-level, which earns the dagger."""
    return tuple(gr.Course(line.rstrip("*").strip(), line.endswith("*"))
                 for line in _lines(text))


def _course_text(course):
    """A Course, or the [name, graduate] pair JSON hands back, as one editable line."""
    name, graduate = (course, False) if isinstance(course, str) else (
        course[0], len(course) > 1 and course[1])
    return f"{name}*" if graduate else str(name)


def _freeze(row):
    """JSON hands lists back where the model wants tuples; frozen entries want them frozen."""
    return {k: tuple(v) if isinstance(v, list) else v for k, v in row.items()}


PARSE = {"line": str.strip, "text": str.strip, "list": _lines, "csv": _csv, "pairs": _pairs,
         "courses": _courses}
FORMAT = {"list": "\n".join, "csv": ", ".join,
          "pairs": lambda v: "\n".join(f"{a} | {b}" for a, b in v),
          "courses": lambda v: "\n".join(_course_text(c) for c in v)}
BOXES = {"text", "list", "pairs", "courses"}  # kinds needing a Text widget, not an Entry


DATES = {"start", "end"}  # typed as YYYY-MM, so they are set in mono like every other id


def _widget(parent, field):
    if field.kind in BOXES:
        return theme.text(parent, height=field.rows)
    return ttk.Entry(parent, style="Mono.TEntry" if field.key in DATES else "TEntry")


def _read(widget, kind):
    raw = widget.get("1.0", "end") if kind in BOXES else widget.get()
    return PARSE[kind](raw)


def _fill(widget, kind, value):
    text = FORMAT[kind](value or ()) if kind in FORMAT else str(value or "")
    if kind in BOXES:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
    else:
        widget.delete(0, "end")
        widget.insert(0, text)


# ── generic widgets ──────────────────────────────────────────────────────────────────

def _scrollable(parent):
    """Tk has no scrolling frame; this is the canvas-plus-window idiom, written once."""
    canvas = theme.register(tk.Canvas(parent, highlightthickness=0, background=theme.BG),
                            "canvas")
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
    canvas.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    return inner


def _table(parent, columns, height=10, on_sort=None):
    """A Treeview with its scrollbar, and headings that sort when on_sort is given."""
    box = ttk.Frame(parent)
    tree = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings",
                        height=height, selectmode="browse")
    for index, (key, title, width) in enumerate(columns):
        tree.heading(key, text=title,
                     command=(lambda k=key: on_sort(k)) if on_sort else "")
        tree.column(key, width=width, minwidth=50, anchor="w", stretch=(index == 0))
    bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    tree.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    return box, tree


def _buttons(parent, *pairs):
    """A row of equal-width buttons: (label, command), left to right."""
    row = ttk.Frame(parent)
    for column, (label, command) in enumerate(pairs):
        ttk.Button(row, text=label, command=command).grid(row=0, column=column, sticky="ew",
                                                          padx=(0, 4))
        row.columnconfigure(column, weight=1)
    return row


def run_async(post, message, work):
    """Network calls and LaTeX both block, and neither may block the Tk loop.

    `post` takes either a status line or a function to run back on the Tk thread; a worker
    that touches a widget directly trips over Tk's one-thread rule sooner or later."""
    post(message)

    def task():
        try:
            post(work() or "Done.")
        except Exception as exc:
            post(f"Failed: {exc}")

    threading.Thread(target=task, daemon=True).start()


def notify(title, body):
    """Buzz a phone through mobile_ping, if the environment names a transport. Silent and
    harmless otherwise: no transport configured means no packet leaves the machine."""
    try:
        import mobile_ping

        pinger = mobile_ping.pinger_from_env(mobile_ping.Recording())
        if pinger.name != "recording":
            mobile_ping.ping([mobile_ping.Prompt(text=body, tags=(title,))], pinger)
    except Exception:
        pass


# ── the preview surface ─────────────────────────────────────────────────────────────────────

def _open_externally(target):
    """A real browser window on a local page or a posting. Blocking, so callers hand it to
    run_async. A machine whose default browser refuses gets told where the file is."""
    url = target if "://" in target else "file://" + pathname2url(os.path.abspath(target))
    if webbrowser.open(url, new=1):
        return f"Opened {target} in your browser."
    return f"No browser would open. The page is at {target}."


def _pane(parent, on_commit):
    """The preview surface. One canvas draws everything the window shows — the résumé and
    letter from the model, the application form from the cached questions — so there is no
    second engine to fall out of step with and nothing to fail to start."""
    widget = paper.Paper(parent, on_commit=on_commit)
    widget.pack(fill="both", expand=True)
    return widget


class Form(ttk.Frame):
    """A column of prompts bound to one dataclass's fields."""

    def __init__(self, parent, fields):
        super().__init__(parent)
        self.fields = fields
        self.widgets = {}
        for field in fields:
            ttk.Label(self, text=field.label, style="Muted.TLabel", wraplength=420).pack(
                fill="x", pady=(10, 3))
            widget = _widget(self, field)
            widget.pack(fill="x")
            self.widgets[field.key] = widget

    def get(self):
        return {f.key: _read(self.widgets[f.key], f.kind) for f in self.fields}

    def set(self, values):
        for field in self.fields:
            _fill(self.widgets[field.key], field.kind, values.get(field.key, ""))


class SectionEditor(ttk.Frame):
    """A list of entries beside the form that edits whichever one is selected.

    The list of dicts is the truth; the form is a view onto one of them, written back
    whenever the selection moves or anyone asks for the data."""

    def __init__(self, parent, section):
        super().__init__(parent)
        self.section = section
        self.rows = []
        self.index = None

        self.list = theme.listbox(self, height=6)
        self.list.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.GAP))
        self.list.bind("<<ListboxSelect>>", self._select)
        _buttons(self, (f"New {section.label.rstrip('s').lower()}", self.new),
                 ("Delete", self.delete), ("Move up", lambda: self.move(-1)),
                 ("Move down", lambda: self.move(1))).pack(fill="x", padx=theme.PAD)

        self.form = Form(_scrollable(self), section.fields)
        self.form.pack(fill="both", expand=True, padx=theme.PAD)

    # -- data ------------------------------------------------------------------------
    def load(self, rows):
        """Rows are squared up against the spec, so a cache written before a field existed
        (or after one was dropped) still loads."""
        keys = [f.key for f in self.section.fields]
        self.rows = [{k: row.get(k, "") for k in keys} for row in rows or ()
                     if isinstance(row, dict)]
        self.index = None
        self._refresh()
        if self.rows:
            self._show(0)

    def data(self):
        """The rows as plain dicts, current form included. What gets cached to disk."""
        self._commit()
        return [row for row in self.rows if row.get(self.section.fields[0].key)]

    def entries(self):
        """The rows as model objects. Anything without its first field is a blank draft."""
        return tuple(self.section.cls(**_freeze(row)) for row in self.data())

    # -- list ------------------------------------------------------------------------
    def _label(self, row):
        """The model's own header line, so the list reads like the résumé it will print."""
        try:
            entry = self.section.cls(**row)
        except TypeError:
            return "(untitled)"
        return " · ".join(x for x in (entry.heading or "(untitled)", entry.period) if x)

    def _refresh(self):
        self.list.delete(0, "end")
        for row in self.rows:
            self.list.insert("end", self._label(row))

    def _show(self, index):
        self.index = index
        self.list.selection_clear(0, "end")
        self.list.selection_set(index)
        self.form.set(self.rows[index])

    def _commit(self):
        if self.index is not None and self.index < len(self.rows):
            self.rows[self.index] = self.form.get()
            self.list.delete(self.index)
            self.list.insert(self.index, self._label(self.rows[self.index]))
            self.list.selection_set(self.index)

    def _select(self, _event=None):
        selection = self.list.curselection()
        if selection and selection[0] != self.index:
            self._commit()
            self._show(selection[0])

    # -- commands --------------------------------------------------------------------
    def new(self):
        self._commit()
        self.rows.append({f.key: "" for f in self.section.fields})
        self.list.insert("end", "(untitled)")
        self._show(len(self.rows) - 1)

    def delete(self):
        if self.index is None:
            return
        self.rows.pop(self.index)
        self.index = None
        self._refresh()
        if self.rows:
            self._show(0)
        else:
            self.form.set({})

    def move(self, step):
        if self.index is None:
            return
        target = self.index + step
        if not 0 <= target < len(self.rows):
            return
        self._commit()
        self.rows[self.index], self.rows[target] = self.rows[target], self.rows[self.index]
        self.index = None
        self._refresh()
        self._show(target)


# ── tabs that are not just a form ────────────────────────────────────────────────────

class LetterTab(ttk.Frame):
    """The cover letter, plus the writing samples the letter is meant to sound like."""

    def __init__(self, parent, post, on_preview):
        super().__init__(parent)
        self.post = post
        self.samples = []

        ttk.Label(self, text="Cover letter, or a sample of your own writing",
                  style="Muted.TLabel").pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 3))
        self.text = theme.text(self, height=14)
        self.text.pack(fill="both", expand=True, padx=theme.PAD)

        ttk.Label(self, text="Tags — comma separated; how you find this again",
                  style="Muted.TLabel").pack(fill="x", padx=theme.PAD, pady=(10, 3))
        self.tags = ttk.Entry(self)
        self.tags.pack(fill="x", padx=theme.PAD)

        _buttons(self, ("Preview letter", on_preview),
                 ("Save as sample", self.save)).pack(fill="x", padx=theme.PAD,
                                                     pady=(theme.PAD, theme.GAP))

        find = ttk.Frame(self)
        find.pack(fill="x", padx=theme.PAD)
        ttk.Label(find, text="Saved samples", style="Head.TLabel").pack(side="left")
        self.query = ttk.Entry(find, width=18)
        self.query.pack(side="right")
        self.query.bind("<KeyRelease>", lambda _e: self.refresh())
        ttk.Label(find, text="filter", style="Muted.TLabel").pack(side="right", padx=theme.GAP)

        self.list = theme.listbox(self, height=6)
        self.list.pack(fill="x", padx=theme.PAD, pady=(3, theme.PAD))
        self.list.bind("<Double-Button-1>", self._load_sample)
        self.refresh()

    def body(self):
        return self.text.get("1.0", "end").strip()

    def set_body(self, value):
        _fill(self.text, "text", value)

    def save(self):
        if not self.body():
            self.post("Nothing to save.")
            return
        store.save_prompt(self.body(), _csv(self.tags.get()))
        self.refresh()
        self.post("Sample saved.")

    def refresh(self):
        """Empty filter lists everything; anything typed is used as both tags and keywords,
        which is how the store ranks a sample that matches on either."""
        words = _csv(self.query.get())
        found = store.prompts(words, words) if words else store.all_prompts()
        self.samples = found or []
        self.list.delete(0, "end")
        for sample in self.samples:
            tags = ", ".join(sample.get("tags") or ()) or "untagged"
            self.list.insert("end", f"[{tags}] {sample.get('text', '')[:70]}")
        if words and not self.samples:
            self.post(f"No sample matches {', '.join(words)}.")

    def _load_sample(self, _event=None):
        selection = self.list.curselection()
        if selection:
            sample = self.samples[selection[0]]
            self.set_body(sample.get("text", ""))
            _fill(self.tags, "csv", tuple(sample.get("tags") or ()))


class JobsTab(ttk.Frame):
    """Everything scraped so far, and the posting the résumé is ranked against.

    The archive on disk is the list, so a search adds to it rather than replacing it.
    Selecting a row asks the window for that listing's application questions."""

    COLUMNS = (("title", "Position", 190), ("company", "Company", 106),
               ("location", "Location", 96), ("source", "Board", 60))

    def __init__(self, parent, post, on_tailor, on_questions):
        super().__init__(parent)
        self.post = post
        self.on_questions = on_questions
        self.jobs = []
        self.order = (None, False)  # (column, reversed); None is the archive's own order

        query = ttk.Frame(self)
        query.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.GAP))
        self.keywords = self._input(query, "Keywords", 0, 22)
        self.location = self._input(query, "Location", 1, 12)
        self.limit = self._input(query, "Limit", 2, 5)
        _fill(self.limit, "line", "25")
        ttk.Button(query, text="Search", style="Accent.TButton",
                   command=self.search).grid(row=1, column=3)
        for entry in (self.keywords, self.location, self.limit):
            entry.bind("<Return>", lambda _e: self.search())

        box, self.tree = _table(self, self.COLUMNS, height=11, on_sort=self.sort)
        box.pack(fill="both", expand=True, padx=theme.PAD)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.tree.bind("<Double-Button-1>", lambda _e: self.open_selected())

        # Two rows. Five of these labels want 546px and the side panel is 500: ttk has no
        # ellipsis, so it squeezes the last button until nobody can read it.
        _buttons(self, ("Open posting", self.open_selected),
                 ("Use as target", self.use_selected)).pack(fill="x", padx=theme.PAD,
                                                            pady=(theme.GAP, 0))
        _buttons(self, ("Save questions", self.save_questions),
                 ("Load answers", self.load_answers),
                 ("Clear list", self.clear)).pack(fill="x", padx=theme.PAD, pady=theme.GAP)

        sign = ttk.Frame(self)
        sign.pack(fill="x", padx=theme.PAD, pady=(0, theme.GAP))
        self.session_label = ttk.Label(sign, style="Muted.TLabel")
        self.session_label.pack(side="left")
        ttk.Button(sign, text="Sign out", command=self.linkedin_signout).pack(side="right")
        ttk.Button(sign, text="Log in to LinkedIn",
                   command=self.linkedin_login).pack(side="right", padx=(0, 4))
        self._show_session()

        ttk.Label(self, text="Job posting — entries are ranked against this text",
                  style="Muted.TLabel").pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 3))
        self.posting = theme.text(self, height=6)
        self.posting.pack(fill="both", expand=True, padx=theme.PAD)

        keep = ttk.Frame(self)
        keep.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        ttk.Label(keep, text="Keep best", style="Muted.TLabel").pack(side="left")
        self.keep = ttk.Spinbox(keep, from_=1, to=20, width=4)
        _fill(self.keep, "line", "3")
        self.keep.pack(side="left", padx=theme.GAP)
        ttk.Button(keep, text="Preview tailored résumé", style="Accent.TButton",
                   command=on_tailor).pack(side="left", fill="x", expand=True)

        self.show(store.jobs())

    @staticmethod
    def _input(parent, label, column, width):
        parent.columnconfigure(column, weight=width)
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(row=0, column=column,
                                                                 sticky="w")
        entry = ttk.Entry(parent, width=width)
        entry.grid(row=1, column=column, sticky="ew", padx=(0, 6))
        return entry

    # -- the list --------------------------------------------------------------------
    def show(self, jobs):
        self.jobs = [j for j in jobs if isinstance(j, dict) and j.get("url")]
        self.tree.delete(*self.tree.get_children())
        for index, job in enumerate(self.jobs):
            self.tree.insert("", "end", iid=str(index),
                             values=tuple(job.get(key, "") for key, _t, _w in self.COLUMNS))

    def sort(self, key):
        """Click a heading to sort by it, again to reverse. Order is a view, not the record:
        the archive on disk keeps its own newest-first order either way."""
        self.order = (key, self.order[0] == key and not self.order[1])
        self.jobs.sort(key=lambda j: str(j.get(key, "")).lower(), reverse=self.order[1])
        self.show(self.jobs)
        for column, title, _width in self.COLUMNS:
            arrow = " ↓" if self.order[1] else " ↑"
            self.tree.heading(column, text=title + (arrow if column == key else ""))

    def selected(self):
        picked = self.tree.selection()
        index = int(picked[0]) if picked and picked[0].isdigit() else -1
        return self.jobs[index] if 0 <= index < len(self.jobs) else None

    def _select(self, _event=None):
        job = self.selected()
        if job:
            self.on_questions(job)

    def target(self):
        """The posting to rank against, and how many entries per section to keep."""
        keep = self.keep.get().strip()
        return self.posting.get("1.0", "end").strip(), int(keep) if keep.isdigit() else None

    # -- commands --------------------------------------------------------------------
    def search(self):
        limit = self.limit.get().strip()
        query = scout.Query(self.keywords.get().strip(), self.location.get().strip(),
                            int(limit) if limit.isdigit() else 25)

        def work():
            known = {j["url"] for j in store.jobs() if j.get("url")}
            found = [dict(asdict(x), seen=time.time()) for x in scout.scout(query)]
            archive = store.save_jobs(found)
            self.post(lambda: self.show(archive))
            fresh = [j for j in found if j["url"] not in known]
            if fresh:
                notify(f"{len(fresh)} new listings",
                       "\n".join(f"{j['title']} — {j['company']}" for j in fresh[:5]))
            return f"{len(found)} found, {len(fresh)} new — {len(archive)} in the list."

        run_async(self.post, "Searching...", work)

    def open_selected(self):
        job = self.selected()
        if not job:
            self.post("Select a listing first.")
            return
        run_async(self.post, "Opening the posting...",
                  lambda: _open_externally(job["url"]))

    def use_selected(self):
        job = self.selected()
        if not job:
            self.post("Select a listing first.")
            return
        header = "{title}\n{company} — {location}\n{url}\n\n"
        _fill(self.posting, "text", header.format_map(_Blank(job)))
        self.post("Paste the posting body under the header, then preview.")

    def save_questions(self):
        """The form as a sheet to fill in: every question, with whatever answer is already
        cached for it. Answers typed into it come back through Load answers."""
        job = self.selected()
        rows = store.questions(job["url"]) if job else None
        if not rows:
            self.post("Select a listing whose questions have loaded.")
            return
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, "questions.txt")
        _write_file(path, _answer_sheet(job, rows))
        self.post(f"Wrote {path} — type under each A: and load it back.")

    def load_answers(self):
        """Read that sheet back in. An answer is cached against the question, not the
        listing, so every other posting that asks the same thing opens with it filled in."""
        path = os.path.join(OUT, "questions.txt")
        try:
            with open(path, encoding="utf-8") as f:
                pairs = _read_answers(f.read())
        except OSError:
            self.post("No out/questions.txt yet — save a listing's questions first.")
            return
        saved = store.save_answers(pairs) or []
        self.post(f"Cached {len(saved)} answer(s)." if saved
                  else f"Nothing filled in under an A: in {path}.")
        job = self.selected()
        if job and saved:
            self.on_questions(job)  # redraw the preview with what was just written

    def _show_session(self):
        self.session_label.configure(
            text="LinkedIn: signed in" if session.signed_in()
            else "LinkedIn: signed out — its forms stay unreadable")

    def linkedin_login(self):
        """Opens a real Chrome window and waits there while you sign in by hand. The saved
        session is what lets scout follow a LinkedIn posting to the form behind it."""
        run_async(self.post, "Opening a browser window — sign in there, then it closes...",
                  lambda: self._after_login(session.login()))

    def _after_login(self, message):
        self.post(self._show_session)
        return message

    def linkedin_signout(self):
        message = session.forget()
        self._show_session()
        self.post(message)

    def clear(self):
        if messagebox.askyesno("Clear list", "Forget every scraped listing and the questions "
                                             "nobody answered? Your answers are kept."):
            store.forget_jobs()
            self.show([])
            self.post("List cleared.")


# ── the window ───────────────────────────────────────────────────────────────────────

def _anything(state):
    """True if the tabs hold anything at all. Empty strings and empty lists both count as
    nothing, which is exactly the state that must never be written over a real résumé."""
    return any(bool(v) for v in state.values())


def _write_file(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# A writer takes the model and the out dir. There is one design, so there is nothing to
# choose between but how far down the pipeline to stop: the source, or the compiled page.
EXPORTS = {
    "PDF": lambda r, out: gr.to_pdf(r, os.path.join(out, "resume.pdf")),
    "LaTeX": lambda r, out: _write_file(os.path.join(out, "resume.tex"), gr.render(r)),
}

SECTION_TITLES = layout.SECTION_TITLES
ANSWER_CSS = "margin:3pt 0 0 14pt"
SHEET_ID = re.compile(r"\bid ([0-9a-f]{12})\b")
SHEET_HELP = ("# Type your answer under each A:. Blank ones are left alone; the rest are cached\n"
              "# against the question itself, so the next form that asks it opens filled in.\n"
              "# Load answers reads this file back.")
LISTING_KEYS = ("source", "title", "company", "location", "url", "ref")


class _Blank(dict):
    """format_map over this fills a missing key with "" instead of raising, so a row
    written by an older version of the archive still pastes."""

    def __missing__(self, key):
        return ""


def _listing(job):
    """A stored row back into the record scout.questions() expects."""
    return scout.Listing(**{k: job.get(k, "") for k in LISTING_KEYS})


def _rows(questions):
    """Questions as JSON-able data. Full fidelity, so the cache is as good as a fresh read."""
    return [asdict(q) for q in questions]


def _questions(rows):
    """And back again, forgivingly: a row written by an older version still reads."""
    out = []
    for row in rows or ():
        fields = tuple(scout.Field(f.get("name", ""), f.get("kind", "text"),
                                   tuple(f.get("options") or ()))
                       for f in row.get("fields") or ())
        out.append(scout.Question(row.get("label", ""), fields, bool(row.get("required")),
                                  row.get("section", "application"),
                                  row.get("description", "")))
    return out


# The letter and the question sheets are still HTML: they are not the résumé, and a browser
# pane shows them without a TeX round trip. The résumé's own design left with generate_resume.
SHEET = """<html><head><meta charset="utf-8"><title>__TITLE__</title><style>
@page { size: letter; margin: 0.6in; }
body { font: 11pt/1.5 Charter, Georgia, serif; color: #16181d; margin: 0; padding: 24px; }
h1 { font-size: 19pt; font-weight: 600; margin: 0 0 10pt; }
h2 { font-size: 10pt; text-transform: uppercase; letter-spacing: .09em; color: #6b7280;
     border-bottom: 1px solid #d8dbe2; padding-bottom: 3pt; margin: 16pt 0 7pt; }
p { margin: 0 0 9pt; }
.q { font-weight: 600; margin: 10pt 0 2pt; }
.a { white-space: pre-wrap; margin: 0 0 8pt; }
.muted { color: #6b7280; }
</style></head><body>__BODY__</body></html>"""


def _page(title, body):
    return SHEET.replace("__TITLE__", html.escape(title)).replace("__BODY__", body)


def _letter_html(name, body):
    """Reuses the résumé stylesheet so the letter previews in the same typeface."""
    paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in body.split("\n\n") if p.strip())
    return _page(name, f"<h1>{html.escape(name)}</h1>{paragraphs}")


def _questions_html(job, rows, note=""):
    """The application form as a printed sheet, in the résumé's own stylesheet."""
    esc = html.escape
    body = [f"<h1>{esc(job.get('title', 'Listing'))}</h1>",
            '<div class="contact">'
            + " &nbsp;·&nbsp; ".join(esc(x) for x in
                                    (job.get("company"), job.get("location"),
                                     job.get("source")) if x)
            + ' &nbsp;·&nbsp; <a href="%s">open posting</a></div>' % esc(job.get("url", ""))]
    if note:
        body.append(f'<p class="r">{esc(note)}</p>')

    section, number = None, 0
    rows = list(rows or ())
    for row, q, found in zip(rows, _questions(rows), _answers(rows)):
        if q.section != section:
            section = q.section
            body.append(f"<h2>{esc(SECTION_TITLES.get(section, section.title()))}</h2>")
        number += 1
        aside = " · ".join(x for x in (q.kind if q.kind != "text" else "",
                                       "required" if q.required else "") if x)
        body.append('<div class="entry"><table class="hd"><tr>'
                    f'<td><b>{number}. {esc(q.label or "(unlabelled)")}</b></td>'
                    f'<td class="r">{esc(aside)}</td></tr></table>')
        if q.options:
            body.append(f'<div class="r">{esc(" | ".join(q.options))}</div>')
        if q.description:
            body.append(f'<div class="sub">{esc(q.description[:400])}</div>')
        if found:
            body.append(f'<div style="{ANSWER_CSS}">{esc(found["answer"]).replace(chr(10), "<br>")}'
                        '</div>')
            if found["from"]:
                body.append(f'<div class="r" style="{ANSWER_CSS}">answered as '
                            f'“{esc(found["from"])}”</div>')
        body.append("</div>")
    return _page(job.get("title", "Listing"), "\n".join(body))


def _answers(rows):
    """Each row's answer, aligned with it: the one written for that question, or the one
    written for the nearest question worded like it. Rows fresh off a board carry no id,
    so the bank is asked by label as well."""
    return store.answers_for([r.get("id") or r.get("label", "") for r in rows or ()])


def _answer_sheet(job, rows):
    """The form as a file to fill in and read straight back: one Q: per question, one A:
    per answer, prefilled with whatever the bank already holds. The id line is how a sheet
    finds its question again when the label on it has been edited."""
    rows = list(rows or ())
    out = ["{title}".format_map(_Blank(job)), "{company} — {location}".format_map(_Blank(job)),
           job.get("url", ""), "", SHEET_HELP, ""]
    section = None
    for row, q, found in zip(rows, _questions(rows), _answers(rows)):
        if q.section != section:
            section = q.section
            out.append(f"[{section}]")
        out.append(f"Q: {q.label}")
        note = " · ".join(x for x in (q.kind if q.kind != "text" else "",
                                      "required" if q.required else "",
                                      f"id {row['id']}" if row.get("id") else "") if x)
        if note:
            out.append(f"#  {note}")
        if q.options:
            out.append("#  " + " | ".join(q.options))
        if found and found["from"]:
            out.append(f'#  prefilled from “{found["from"]}” — edit it or leave it')
        out.append("A: " + (found["answer"] if found else ""))
        out.append("")
    return "\n".join(out)


def _read_answers(text):
    """A filled-in sheet back into (question, answer) pairs. The question is its id while
    the sheet still carries one and its label otherwise, so an answer lands on the question
    it was written for even when the wording has been tidied up.

    An answer runs to the next Q: or section heading, so it can hold blank lines."""
    pairs, key, answer, taking = [], None, [], False

    def close():
        body = "\n".join(answer).strip()
        if key and taking and body:
            pairs.append((key, body))

    for line in text.splitlines():
        if line.startswith("Q:"):
            close()
            key, answer, taking = line[2:].strip(), [], False
        elif line.startswith("A:"):
            answer, taking = [line[2:].strip()], True
        elif taking and line.startswith("["):
            close()
            key, answer, taking = None, [], False
        elif line.startswith("#") and not taking:
            found = SHEET_ID.search(line)
            if found:
                key = found.group(1)  # the id is the surer key, so it wins over the label
        elif taking:
            answer.append(line)
    close()
    return pairs


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Job Application Automator")
        self.root.geometry("1400x860")
        self.root.minsize(1040, 660)
        theme.apply(self.root, store.bio_info("theme"))  # whichever preset you left on

        self.messages = queue.SimpleQueue()
        self.showing = None   # the listing the preview is currently about
        self.pending = set()  # urls whose form is being read right now
        self.page = None      # the file the last preview was written to

        pane = ttk.Frame(self.root, style="Card.TFrame")
        pane.pack(side="left", fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)
        bar = ttk.Frame(pane, style="Card.TFrame")  # the preview's own control, not the
        bar.pack(side="top", fill="x", padx=theme.GAP, pady=(theme.GAP, 0))  # export row's
        ttk.Button(bar, text="Open in browser", command=self.open_page).pack(side="right")
        self.preview = _pane(pane, self._committed)
        self.macro = Macro(self.preview, on_status=self.post, on_answer=self._save_answer,
                           on_state=self._macro_state)
        self._macro_bar(bar)

        side = ttk.Frame(self.root, width=500)
        side.pack(side="right", fill="y", padx=(0, theme.PAD), pady=theme.PAD)
        side.pack_propagate(False)

        bottom = ttk.Frame(side)
        bottom.pack(side="bottom", fill="x", pady=(theme.GAP, 0))
        self.fmt = ttk.Combobox(bottom, values=tuple(EXPORTS), state="readonly", width=11)
        self.fmt.set("PDF")
        self.fmt.pack(side="right", padx=(theme.GAP, 0))
        _buttons(bottom, ("Preview résumé", self.preview_resume), ("Export", self.export),
                 ("Save", self.save)).pack(side="left", fill="x", expand=True)

        self.status = ttk.Label(side, text="", style="Muted.TLabel", anchor="w",
                                wraplength=480, justify="left")
        self.status.pack(side="bottom", fill="x", pady=(theme.GAP, 0))

        self.tabs = ttk.Notebook(side)
        self.tabs.pack(fill="both", expand=True)
        self.profile = Form(_scrollable(self._add(ttk.Frame(self.tabs), "Profile")), PROFILE)
        self.profile.pack(fill="both", expand=True, padx=theme.PAD)
        self.editors = {s.key: self._add(SectionEditor(self.tabs, s), s.label) for s in SECTIONS}
        self.letter = self._add(LetterTab(self.tabs, self.post, self.preview_letter), "Letter")
        self.jobs = self._add(
            JobsTab(self.tabs, self.post, self.preview_tailored, self.show_questions), "Jobs")

        self.load()
        self._drain()
        for key in ("<Command-s>", "<Control-s>"):  # whichever the platform sends
            self.root.bind_all(key, lambda _e: self.save())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def _add(self, tab, label):
        self.tabs.add(tab, text=label)
        return tab

    # -- the macro -------------------------------------------------------------------
    def _macro_bar(self, bar):
        """The macro's own controls, beside the preview it drives, and the theme beside them.

        The run controls are live only while the pane holds a form. Pressing Apply over a
        résumé could never do anything but answer "pick a listing first", so the buttons
        say that by going grey instead of by waiting to be pressed."""
        self.apply_button = ttk.Button(bar, text="Apply (simulate)", style="Accent.TButton",
                                       command=self.run_macro)
        self.apply_button.pack(side="left")
        self.hold_button = ttk.Button(bar, text="Pause", command=self.hold_macro)
        self.hold_button.pack(side="left", padx=(theme.GAP, 0))
        self.run_buttons = [self.apply_button, self.hold_button]
        for label, command in (("Step", self.step_macro), ("Stop", self.macro.stop)):
            button = ttk.Button(bar, text=label, command=command)
            button.pack(side="left", padx=(theme.GAP, 0))
            self.run_buttons.append(button)
        self.speed = ttk.Combobox(bar, values=tuple(SPEEDS), state="readonly", width=8)
        self.speed.set(DEFAULT_SPEED)
        self.speed.pack(side="left", padx=(theme.GAP, 0))

        self.preset = ttk.Combobox(bar, values=tuple(theme.PRESETS), state="readonly",
                                   width=10)
        self.preset.set(theme.PRESET)
        self.preset.pack(side="right", padx=theme.GAP)
        self.preset.bind("<<ComboboxSelected>>", self.set_preset)

    def run_macro(self):
        if self.macro.state == "paused":
            self.macro.resume()
            return
        if not self.preview.controls():
            self.post("Pick a listing on the Jobs tab first — the macro fills in its form.")
            return
        self.macro.start(self.speed.get())

    def hold_macro(self):
        self.macro.resume() if self.macro.state == "paused" else self.macro.pause()

    def step_macro(self):
        if not self.preview.controls():
            self.post("Pick a listing on the Jobs tab first.")
            return
        self.macro.step(self.speed.get())

    def _macro_state(self, state):
        """Both buttons say what pressing them will do, so a paused run is unmistakable.

        The status line turns amber with them: a pause is the one thing in this window that
        is waiting on you, and it is the same colour as the field it stopped on."""
        self.hold_button.configure(text="Resume" if state == "paused" else "Pause")
        self.apply_button.configure(text="Resume" if state == "paused" else "Apply (simulate)")
        self.status.configure(style="Caution.TLabel" if state == "paused" else "Muted.TLabel")

    def _run_controls(self, live):
        """Grey the run controls out when there is no form under the pointer to walk."""
        for button in self.run_buttons:
            button.configure(state="normal" if live else "disabled")
        self.speed.configure(state="readonly" if live else "disabled")

    def set_preset(self, _event=None):
        """Switch the whole surface, and remember it for next launch.

        ttk restyles in place as soon as its styles are reconfigured, so the buttons, tabs
        and scrollbars change under your hands; theme's register catches the few widgets
        painted by hand, and the canvas redraws itself."""
        name = theme.use(self.preset.get(), self.root)
        self.preset.set(name)
        store.save_bio({"theme": name})
        self.post(f"Theme — {name}.")

    def _committed(self, box):
        """Return in a field, or a click on the simulated Submit: both mean carry on."""
        if self.macro.state == "paused":
            self.macro.resume()

    def _save_answer(self, box):
        """An answer typed during a pause, kept against the question rather than the listing
        — which is what makes the next form that asks it open already filled in."""
        if store.save_answer(box.qid, box.value):
            self.post(f"Cached “{box.label[:50]}” — every later form that asks it "
                      "now opens with this answer.")

    # -- state -----------------------------------------------------------------------
    def resume(self):
        return gr.Resume(**self.profile.get(),
                         **{key: editor.entries() for key, editor in self.editors.items()})

    def state(self):
        """Everything typed, as JSON-able data. Tuples land as lists and come back fine."""
        return dict(self.profile.get(), letter=self.letter.body(),
                    **{key: editor.data() for key, editor in self.editors.items()})

    def load(self):
        """Fills the tabs, then draws the page. The draw is the point: a canvas that stays
        blank until someone finds the right button reads as a window that failed to start,
        and resume_doc has its own empty state for a first run, so there is always
        something on it. No PDF is compiled here — launching should not wait on TeX."""
        saved = store.bio_info(BIO_KEY)
        if isinstance(saved, dict) and saved:
            self.profile.set(saved)
            for key, editor in self.editors.items():
                editor.load(saved.get(key))
            self.letter.set_body(saved.get("letter", ""))
            self.post("Loaded your saved résumé.")
        else:
            self.post("No saved résumé yet — fill the tabs and hit Save.")
        self._draw(layout.resume_doc(self.resume()), "page")

    def save(self):
        """An empty set of tabs never overwrites a stored résumé. Closing the window saves,
        so anything that leaves the tabs blank — a load that failed, a launch against the
        wrong archive — would otherwise write the blanks over the lot on the way out."""
        state = self.state()
        if not _anything(state) and store.bio_info(BIO_KEY):
            self.post("Tabs are empty — your saved résumé was left alone, not overwritten.")
            return
        store.save_bio({BIO_KEY: state})
        self.post("Saved.")

    def post(self, item):
        """Callable from any thread. A string is shown as the status; a function is run."""
        self.messages.put(item)

    def _drain(self):
        """Tk is not thread-safe, so its widgets are touched on its own thread and no other.
        One bad message must not stop the pump, or the window quietly stops updating."""
        try:
            while True:
                item = self.messages.get_nowait()
                try:
                    if callable(item):
                        item()
                    else:
                        self.status.configure(text=str(item))
                except Exception as exc:
                    self.status.configure(text=f"Failed: {exc}")
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain)

    # -- previews --------------------------------------------------------------------
    def _draw(self, doc, surface="form"):
        """Everything the pane shows goes through here, and every one of them ends whatever
        run was under way — the macro holds Boxes from the page it started on.

        `surface` is what is being drawn: "form" puts it straight onto the window's panel,
        "page" lays it on a sheet of paper. It also settles whether the run controls have
        anything to run."""
        self.macro.stop(quiet=True)
        self.preview.show(doc, surface)
        self._run_controls(surface == "form")

    def _keep(self, page, name):
        """A copy on disk. The canvas is what you look at; this is what Open in browser and
        the answer sheet reach for."""
        os.makedirs(OUT, exist_ok=True)
        self.page = _write_file(os.path.join(OUT, f"{name}.html"), page)
        return self.page

    def _attachment(self):
        """What a file question gets answered with: the résumé this app last built."""
        path = os.path.join(OUT, "resume.pdf")
        return path if os.path.exists(path) else ""

    def _show_pdf(self, resume, name):
        """The canvas draws the model at once and the PDF compiles behind it.

        Drawing from the model rather than from the compiled file is what makes the preview
        instant, and what lets it appear at all on a machine with no TeX engine. The PDF is
        still the document — it is what Export and Open in browser hand over — and it is
        compiled off the Tk thread because it blocks for seconds."""
        self.showing = None
        self._draw(layout.resume_doc(resume), "page")
        if not gr.engine():
            self.post(f"Drawn from your model. No PDF to open — {gr.NoEngine()}")
            return
        os.makedirs(OUT, exist_ok=True)
        target = os.path.join(OUT, f"{name}.pdf")

        def work():
            self.page = gr.to_pdf(resume, target)
            return f"Compiled with {gr.engine_name()} — {self.page}"

        run_async(self.post, f"Drawn. Compiling {name}.pdf with {gr.engine_name()}...", work)

    def open_page(self):
        """The last page previewed, in a real browser window — the true styled page at full
        size, whatever the pane managed to draw. Opening blocks, so it goes off-thread."""
        if not self.page:
            self.post("Preview something first.")
            return
        page = self.page
        run_async(self.post, "Opening your browser...", lambda: _open_externally(page))

    def preview_resume(self):
        self._show_pdf(self.resume(), "resume")

    def preview_tailored(self):
        posting, keep = self.jobs.target()
        if not posting:
            self.post("Paste a job posting on the Jobs tab first.")
            return
        self._show_pdf(gr.tailor(self.resume(), posting, keep=keep), "tailored")

    def preview_letter(self):
        self.showing = None
        name, body = self.profile.get()["name"], self.letter.body()
        self._draw(layout.letter_doc(name, body), "page")
        self.post(f"Previewing the letter — a copy is at {self._keep(_letter_html(name, body), 'letter')}.")

    def show_questions(self, job):
        """Selecting a listing draws its application form, read once and then cached.

        A read is slow and can fail, so it happens off-thread and is only drawn if that
        listing is still the selected one when it lands."""
        url = job.get("url", "")
        self.showing = url
        cached = store.questions(url)
        if cached:
            # Only a form with questions on it is taken from the cache. An empty entry is
            # worth another look: it may have been written while the board was down, or by
            # a reader that gave up where this one now follows the apply link through.
            self._draw_form(job, cached)
            self.post(f"{len(cached)} questions, cached. Apply (simulate) walks the form.")
            return
        if url in self.pending:
            return

        self.pending.add(url)
        self._draw([paper.Box("title", job.get("title") or "Listing"),
                    paper.Box("note", "Looking for the application form\u2026", space=12)])

        def work():
            try:
                rows = _rows(scout.questions(_listing(job)))
            except scout.NoForm as exc:
                # Deliberately not cached: this is "could not get at it", and the next try
                # may well do better \u2014 a sign-in, a board back up, a reader that knows
                # one more portal. Caching it would make one bad afternoon permanent.
                self.post(lambda: self._no_form(job, exc.reason))
                return exc.reason
            finally:
                self.post(lambda: self.pending.discard(url))
            store.save_questions(url, rows)
            self.post(lambda: self._questions_ready(job, rows))
            if rows:
                notify(job.get("title", "Application"),
                       f"{len(rows)} questions \u00b7 {job.get('company', '')}")
                return f"{len(rows)} questions."
            return "The form was read and asks nothing this side can see."

        run_async(self.post, f"Reading {job.get('title', 'the form')}...", work)

    def _no_form(self, job, reason):
        """What the pane shows when the application cannot be reached: the reason it could
        not be, rather than an empty form implying the job asks nothing."""
        if self.showing != job.get("url"):
            return  # the selection moved on while we were reading
        where = " \u00b7 ".join(x for x in (job.get("company"), job.get("location"),
                                           job.get("source")) if x)
        self._draw([paper.Box("title", job.get("title") or "Listing"),
                    paper.Box("sub", where),
                    paper.Box("note", reason, fill=theme.CAUTION, space=16),
                    paper.Box("note", "Nothing was cached, so clicking this listing again "
                                      "tries afresh.", space=10)])

    def _draw_form(self, job, rows):
        """The form as the macro will walk it: one control per question, carrying whatever
        the cache holds for it — an exact answer, one borrowed from a question worded like
        it, or nothing, which is what the macro stops on."""
        self._draw(layout.form_doc(job, rows, _answers(rows), self._attachment()))
        self._keep(_questions_html(job, rows), "questions")

    def _questions_ready(self, job, rows):
        if self.showing != job.get("url"):
            return  # the selection moved on while we were reading; leave the preview alone
        self._draw_form(job, rows)

    # -- output ----------------------------------------------------------------------
    def export(self):
        """Exports the same document the preview compiled, from the same model."""
        posting, keep = self.jobs.target()
        resume = gr.tailor(self.resume(), posting, keep=keep) if posting else self.resume()
        fmt = self.fmt.get() if self.fmt.get() in EXPORTS else "PDF"
        if fmt == "PDF" and not gr.engine():
            self.post(f"Cannot write a PDF — {gr.NoEngine()} Export LaTeX to keep the source.")
            return
        os.makedirs(OUT, exist_ok=True)
        run_async(self.post, f"Writing {fmt}...",
                  lambda: f"Wrote {EXPORTS[fmt](resume, OUT)}")

    def quit(self):
        try:
            self.save()
        except Exception as exc:
            print(f"could not save: {exc}")  # closing the window still has to work
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    main()
