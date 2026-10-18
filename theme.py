"""Every colour, font and pad in one place, so the widgets stay about behaviour.

Tk's native macOS theme ignores most styling, so this switches to `clam` and paints it.
Nothing here imports the app; the app imports this and never names a colour itself.

Four presets ship, and a preset is the whole surface. Two of its colours carry meaning and
the rest are furniture: ACCENT marks something the app did by itself, CAUTION marks
something it has stopped and needs you to do. They differ in lightness as well as hue, so
they stay apart in a screenshot, in greyscale, and for anyone who reads hue poorly.

`Daylight` is the palette this app shipped with — the same hexes, kept intact, so switching
to it is a way back rather than an approximation of one.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ── the presets ──────────────────────────────────────────────────────────────────────
# INK/MUTED/FAINT are text, brightest first. BG is the ground, CARD a panel on it, ROW a
# field sunk into the panel, RAISE a panel lifted off it (hover, the selected tab). MAT is
# the bench a document lies on, and PAPER* is the document itself, which is never themed:
# paper is paper, and a résumé has to photocopy.

PRESETS = {
    "Midnight": {
        "INK": "#e8ebf2", "MUTED": "#9aa3b4", "FAINT": "#7b8494",
        "BG": "#0b0d12", "CARD": "#13161d", "ROW": "#0e1118", "RAISE": "#1c212b",
        "LINE": "#242a35", "MAT": "#07090e",
        "ACCENT": "#5b8cff", "ACCENT_SOFT": "#1d263d",
        "ACCENT_HOVER": "#7aa0ff", "ACCENT_PRESS": "#4477e8", "ON_ACCENT": "#0b0d12",
        "CAUTION": "#f0b254", "CAUTION_SOFT": "#2d2924",
        "PAPER": "#ffffff", "PAPER_INK": "#16181d",
        "PAPER_DIM": "#515a66", "PAPER_LINE": "#cfd4dc",
    },
    "Graphite": {
        "INK": "#ecedef", "MUTED": "#a1a4ab", "FAINT": "#80858e",
        "BG": "#101113", "CARD": "#191b1f", "ROW": "#131519", "RAISE": "#22252a",
        "LINE": "#2a2d33", "MAT": "#0a0b0d",
        "ACCENT": "#7fc8f8", "ACCENT_SOFT": "#27333d",
        "ACCENT_HOVER": "#9bd6fb", "ACCENT_PRESS": "#65b4e8", "ON_ACCENT": "#101113",
        "CAUTION": "#e8a44c", "CAUTION_SOFT": "#322b24",
        "PAPER": "#ffffff", "PAPER_INK": "#16181d",
        "PAPER_DIM": "#515a66", "PAPER_LINE": "#cfd4dc",
    },
    "Oxide": {
        "INK": "#f2ebe3", "MUTED": "#b4a899", "FAINT": "#938678",
        "BG": "#120f0c", "CARD": "#1c1814", "ROW": "#15110e", "RAISE": "#262019",
        "LINE": "#2e2822", "MAT": "#0c0a08",
        "ACCENT": "#6fd3c7", "ACCENT_SOFT": "#27302b",
        "ACCENT_HOVER": "#8bdfd5", "ACCENT_PRESS": "#57bdb1", "ON_ACCENT": "#120f0c",
        "CAUTION": "#f4a261", "CAUTION_SOFT": "#36291d",
        "PAPER": "#fffdf9", "PAPER_INK": "#1a1613",
        "PAPER_DIM": "#5a5048", "PAPER_LINE": "#d6cfc4",
    },
    "Daylight": {
        "INK": "#16181d", "MUTED": "#4b5563", "FAINT": "#6b7280",
        "BG": "#f4f5f7", "CARD": "#ffffff", "ROW": "#fafbfc", "RAISE": "#eef1f6",
        "LINE": "#d8dbe2", "MAT": "#e3e6ec",
        "ACCENT": "#2f6df6", "ACCENT_SOFT": "#ecf2fe",
        "ACCENT_HOVER": "#3f79f8", "ACCENT_PRESS": "#2455c8", "ON_ACCENT": "#ffffff",
        "CAUTION": "#8a6d1f", "CAUTION_SOFT": "#f4f2eb",
        "PAPER": "#ffffff", "PAPER_INK": "#16181d",
        "PAPER_DIM": "#515a66", "PAPER_LINE": "#cfd4dc",
    },
}

DEFAULT = "Midnight"
PRESET = DEFAULT

# The tokens themselves. Bound by name rather than by globals().update() so that a reader
# — and a type checker — can see what exists; use() rebinds them when the preset changes.
_D = PRESETS[DEFAULT]
INK, MUTED, FAINT = _D["INK"], _D["MUTED"], _D["FAINT"]
BG, CARD, ROW, RAISE = _D["BG"], _D["CARD"], _D["ROW"], _D["RAISE"]
LINE, MAT = _D["LINE"], _D["MAT"]
ACCENT, ACCENT_SOFT = _D["ACCENT"], _D["ACCENT_SOFT"]
ACCENT_HOVER, ACCENT_PRESS = _D["ACCENT_HOVER"], _D["ACCENT_PRESS"]
ON_ACCENT = _D["ON_ACCENT"]
CAUTION, CAUTION_SOFT = _D["CAUTION"], _D["CAUTION_SOFT"]
PAPER, PAPER_INK = _D["PAPER"], _D["PAPER_INK"]
PAPER_DIM, PAPER_LINE = _D["PAPER_DIM"], _D["PAPER_LINE"]

PAD, GAP = 14, 8
RADIUS, PANEL_RADIUS = 9, 14  # the canvas rounds its own corners; ttk frames cannot

FAMILY, MONO, SERIF, SIZE = "TkDefaultFont", "TkFixedFont", "TkDefaultFont", 12

# Whatever the machine actually has, in order of preference. apply() picks the first.
FACES = ("SF Pro Text", "Helvetica Neue", "Segoe UI", "DejaVu Sans")
MONO_FACES = ("SF Mono", "JetBrains Mono", "Menlo", "Consolas", "DejaVu Sans Mono",
              "Courier New")
SERIF_FACES = ("Charter", "Source Serif 4", "Source Serif Pro", "Georgia", "Palatino",
               "DejaVu Serif", "Times New Roman")

# Widgets painted by hand rather than by a ttk style — a Text, a Listbox, a Canvas. ttk
# restyles itself when a style is reconfigured; these have to be told, so they are kept
# here and repainted when the preset changes.
_painted = []


def bold():
    return (FAMILY, SIZE, "bold")


def small():
    return (FAMILY, SIZE - 1)


def mono(delta=0):
    """For anything a machine wrote: ids, refs, dates typed as YYYY-MM, filenames."""
    return (MONO, SIZE + delta)


def serif(delta=0):
    """For prose on paper. The canvas uses it when it is drawing a document."""
    return (SERIF, SIZE + delta)


def _family(root, faces, fallback):
    have = set(tkfont.families(root))
    return next((f for f in faces if f in have), fallback)


def register(widget, kind):
    """Remember a hand-painted widget so a preset change can reach it."""
    _painted.append((widget, kind))
    return widget


def apply(root, preset=None):
    """Paint the whole app. Call once, on the root, before any widget is built."""
    global FAMILY, MONO, SERIF
    FAMILY = _family(root, FACES, "TkDefaultFont")
    MONO = _family(root, MONO_FACES, "TkFixedFont")
    SERIF = _family(root, SERIF_FACES, FAMILY)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=FAMILY, size=SIZE)
        except tk.TclError:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # aqua silently drops colour options; clam honours them
    except tk.TclError:
        pass  # an unusual Tk build: unstyled beats not starting

    use(preset or PRESET, root)
    return style


def use(name, root=None):
    """Switch preset. Returns the preset in force, which is the old one if `name` is junk.

    ttk picks the new colours up as soon as its styles are reconfigured, so every button,
    tab and scrollbar changes in place. The hand-painted widgets are repainted from the
    register, and the canvas redraws itself."""
    global PRESET
    if name not in PRESETS:
        return PRESET
    PRESET = name
    globals().update(PRESETS[name])
    if root is not None:
        root.configure(bg=BG)
        _styles(ttk.Style(root))
    repaint()
    return PRESET


def repaint():
    """Re-colour what ttk does not own. Destroyed widgets are dropped on the way past."""
    alive = []
    for widget, kind in _painted:
        try:
            if not widget.winfo_exists():
                continue
        except tk.TclError:
            continue
        alive.append((widget, kind))
        try:
            if kind == "text":
                widget.configure(background=CARD, foreground=INK, insertbackground=INK,
                                 highlightbackground=LINE, highlightcolor=ACCENT)
            elif kind == "listbox":
                widget.configure(background=CARD, foreground=INK, selectbackground=ACCENT,
                                 selectforeground=ON_ACCENT, highlightbackground=LINE)
            elif kind == "canvas":
                widget.configure(background=BG)
            elif kind == "paper":
                widget.restyle()
        except tk.TclError:
            pass
    _painted[:] = alive


def _styles(style):
    """Every ttk style the app uses, in one pass, so switching preset is one call."""
    style.configure(".", background=BG, foreground=INK, borderwidth=0, focuscolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=small())
    style.configure("Caution.TLabel", background=BG, foreground=CAUTION, font=small())
    style.configure("Head.TLabel", background=BG, foreground=INK, font=bold())

    style.configure("TButton", background=CARD, foreground=INK, bordercolor=LINE,
                    borderwidth=1, relief="flat", padding=(11, 7))
    style.map("TButton", background=[("pressed", LINE), ("active", RAISE)],
              bordercolor=[("active", MUTED)], foreground=[("disabled", FAINT)])
    style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT,
                    bordercolor=ACCENT)
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_PRESS), ("active", ACCENT_HOVER),
                          ("disabled", RAISE)],
              bordercolor=[("disabled", LINE)],
              foreground=[("disabled", FAINT)])

    for widget in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(widget, fieldbackground=ROW, background=CARD, foreground=INK,
                        bordercolor=LINE, borderwidth=1, relief="flat", padding=6,
                        arrowcolor=MUTED, insertcolor=INK, selectbackground=ACCENT_SOFT,
                        selectforeground=INK)
        style.map(widget, bordercolor=[("focus", ACCENT)],
                  foreground=[("disabled", FAINT)])
    style.map("TCombobox", fieldbackground=[("readonly", ROW)])
    style.configure("Mono.TEntry", fieldbackground=ROW, foreground=INK, font=mono(-1))
    style.map("Mono.TEntry", bordercolor=[("focus", ACCENT)])

    style.configure("TCheckbutton", background=BG, foreground=MUTED, font=small(),
                    indicatorcolor=ROW, indicatorbackground=ROW, bordercolor=LINE)
    style.map("TCheckbutton", indicatorcolor=[("selected", ACCENT)],
              foreground=[("active", INK)])

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, font=small(),
                    padding=(13, 8), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", CARD), ("active", RAISE)],
              foreground=[("selected", INK)], expand=[("selected", (0, 0, 0, 0))])

    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK,
                    rowheight=30, borderwidth=1, bordercolor=LINE, relief="flat")
    style.configure("Treeview.Heading", background=BG, foreground=FAINT, font=small(),
                    relief="flat", padding=(7, 6), borderwidth=0)
    style.map("Treeview.Heading", background=[("active", RAISE)])
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", ON_ACCENT)])

    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG,
                    arrowcolor=MUTED, arrowsize=11, width=11, relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", MUTED)])
    return style


def text(parent, height=6):
    """A tk.Text dressed to match the ttk widgets around it."""
    return register(tk.Text(parent, height=height, wrap="word", relief="flat",
                            borderwidth=0, highlightthickness=1, highlightbackground=LINE,
                            highlightcolor=ACCENT, background=CARD, foreground=INK,
                            insertbackground=INK, padx=9, pady=7), "text")


def listbox(parent, height=6):
    return register(tk.Listbox(parent, height=height, exportselection=False,
                               activestyle="none", relief="flat", borderwidth=0,
                               highlightthickness=1, highlightbackground=LINE,
                               background=CARD, foreground=INK, selectbackground=ACCENT,
                               selectforeground=ON_ACCENT), "listbox")


# Row striping is deliberately absent: on Tk 8.6 a Treeview tag's background wins over the
# selected state, and a selected row that does not look selected is worse than a flat list.
