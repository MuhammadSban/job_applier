"""One résumé model and one LaTeX design, compiled to PDF by tectonic or pdflatex.

The design is the Jake Gutierrez one-page template: `PREAMBLE` is its macro set, kept
verbatim so the spacing hacks it relies on stay exactly as the template author tuned
them, and this module's job is to feed those macros. There is no second design and no
HTML path — a PDF comes from a TeX compiler or it does not come at all.
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NamedTuple


def _month(s):
    """'2026-08' -> 'August 2026'. Anything else passes through, so 'Summer 2026' works."""
    try:
        return datetime.strptime(s, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return s


def _upcoming(s, today=None):
    """True for a 'YYYY-MM' still ahead of us. Unparseable dates are no evidence either way."""
    try:
        when = datetime.strptime(s, "%Y-%m")
    except ValueError:
        return False
    now = today or datetime.now()
    return (when.year, when.month) > (now.year, now.month)


class Course(NamedTuple):
    """One line of relevant coursework. Graduate ones carry the template's dagger."""

    name: str
    graduate: bool = False


def _course(c):
    if isinstance(c, Course):
        return c
    if isinstance(c, str):
        return Course(c)
    return Course(*tuple(c)[:2])


@dataclass(frozen=True, kw_only=True)
class Entry:
    """Fields every dated block shares. kw_only so subclasses can add required fields."""

    start: str = ""
    end: str = ""
    url: str = ""
    bullets: tuple = ()
    tags: tuple = ()  # ranking signal for the matcher; never rendered

    @property
    def period(self):
        if not self.start:
            return _month(self.end) if self.end else ""
        if not self.end:
            return f"{_month(self.start)} -- Present"
        start, end = _month(self.start), _month(self.end)
        # A one-month span is one month, not a range from itself to itself.
        return start if start == end else f"{start} -- {end}"

    def text(self):
        """Everything the ranker reads."""
        return " ".join((*self.labels(), *self.bullets, *self.tags))

    def labels(self):
        """The entry's own words, for the ranker. Renderers ask for fields by name."""
        raise NotImplementedError

    @property
    def heading(self):
        """What to call this entry in a list: the company, project or school."""
        return self.labels()[0]


@dataclass(frozen=True, kw_only=True)
class Experience(Entry):
    company: str
    title: str = ""
    location: str = ""

    def labels(self):
        return (self.company, self.title, self.location)


@dataclass(frozen=True, kw_only=True)
class Project(Entry):
    name: str
    blurb: str = ""
    stack: tuple = ()

    def labels(self):
        return (self.name, self.blurb, *self.stack)

    @property
    def aside(self):
        """The template's italic half-line after the name: the stack, or the blurb."""
        return ", ".join(self.stack) if self.stack else self.blurb


@dataclass(frozen=True, kw_only=True)
class Education(Entry):
    school: str
    degree: str = ""
    concentration: str = ""
    gpa: str = ""
    location: str = ""
    coursework: tuple = ()  # Course, or bare strings for undergraduate ones

    def labels(self):
        return (self.school, self.degree, self.concentration, self.location,
                *(c.name if isinstance(c, Course) else str(c) for c in self.coursework))

    @property
    def courses(self):
        """Coursework as Course rows.

        A caller may pass Course, a bare name, or the ["name", true] pair a Course turns
        into once it has been through JSON and back out of the archive."""
        return tuple(_course(c) for c in self.coursework)

    @property
    def awarded(self):
        """Education shows one date — when the degree lands — not a range."""
        return _month(self.end) if self.end else self.period


@dataclass(frozen=True, kw_only=True)
class Leadership(Entry):
    org: str
    role: str = ""
    location: str = ""

    def labels(self):
        return (self.org, self.role, self.location)


@dataclass(frozen=True, kw_only=True)
class Resume:
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""  # the address line under the name
    links: tuple = ()  # (label, url)
    summary: str = ""  # carried for the letter and the matcher; this design prints no summary
    experience: tuple = ()
    projects: tuple = ()
    education: tuple = ()
    leadership: tuple = ()
    skills: tuple = ()  # (group, "comma, separated")

    @property
    def student(self):
        """Still earning a degree, so the degree leads the page.

        A blank end means enrolled; an end still in the future means the same thing, which
        is the case that matters here — an intended degree carries its award date and no
        start. A date already past, or one too freeform to parse, is not evidence."""
        return any(not e.end or _upcoming(e.end) for e in self.education)

    def sections(self):
        """Non-empty sections in print order.

        Students lead with the degree they are still earning; everyone else leads with
        work. Technical Skills travel with Education, and leadership always closes."""
        school = (("Education", self.education), ("Technical Skills", self.skills))
        work = (("Experience", self.experience), ("Projects", self.projects))
        order = (*school, *work) if self.student else (*work, *school)
        for label, entries in (*order, ("Leadership / Extracurricular", self.leadership)):
            if entries:
                yield label, entries


PREAMBLE = r"""%-------------------------
% Resume in LaTeX - 1 Page Formatting Optimized
% Author : Jake Gutierrez
%-------------------------
\documentclass[letterpaper,9pt]{extarticle}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{amsmath}
\usepackage{tabularx}
\usepackage{fontawesome5}
\usepackage{multicol}
\setlength{\multicolsep}{-3.0pt}
\setlength{\columnsep}{-1pt}

% glyphtounicode makes the finished PDF's text copy out as real characters, but it is a
% pdfTeX feature and tectonic runs XeTeX, where the primitive does not exist. Guarded
% rather than dropped, so pdflatex still gets it and tectonic still compiles.
\ifdefined\pdfglyphtounicode
 \input{glyphtounicode}
 \pdfgentounicode=1
\fi

% Fonts / Formatting
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

% Tighter margins
\addtolength{\oddsidemargin}{-0.62in}
\addtolength{\evensidemargin}{-0.52in}
\addtolength{\textwidth}{1.24in}
\addtolength{\topmargin}{-.78in}
\addtolength{\textheight}{1.5in}

\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

% Tighter section formatting
\titleformat{\section}{
 \vspace{-5pt}\scshape\raggedright\large\bfseries
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

% Custom commands - DEFAULT FONT SIZE (no \small)
\newcommand{\resumeItem}[1]{\item{{#1 \vspace{-1.5pt}}}}
\newcommand{\resumeSubheading}[4]{
 \vspace{-1.5pt}\item
   \begin{tabular*}{1.0\textwidth}[t]{l@{\extracolsep{\fill}}r}
     \textbf{#1} & \textbf{\small #2} \\
     \textit{\small#3} & \textit{\small #4} \\
   \end{tabular*}\vspace{-6.5pt}
}

\newcommand{\resumeProjectHeading}[2]{
   \item
   \begin{tabular*}{1.001\textwidth}{l@{\extracolsep{\fill}}r}
     \small#1 & \textbf{\small #2}\\
   \end{tabular*}\vspace{-6.5pt}
}

\newcommand{\resumeItemListStart}{\begin{itemize}[label=\textbullet]}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-4pt}}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.0in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}"""


# The GUI collects prose, not LaTeX, so every character a compiler would act on is escaped.
# The typographic entries matter because the template's preamble sets no inputenc/fontenc.
_TEX = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "°": r"\textdegree{}", "·": r"$\cdot$", "–": "--", "—": "---",
        "‘": "`", "’": "'", "“": "``", "”": "''",
        "≈": r"$\approx$", "×": r"$\times$", "±": r"$\pm$", "µ": r"$\mu$"}

def _t(s):
    """Escape text so LaTeX prints it instead of running it."""
    return "".join(_TEX.get(c, c) for c in str(s))


def _u(url):
    """hyperref takes URLs nearly raw, but % and # still end the argument early."""
    return str(url).replace("%", r"\%").replace("#", r"\#")


def _cmd(name, *args):
    r"""Build \name{a}{b}. Beats f-strings, where every brace has to be doubled."""
    return "\\" + name + "".join("{" + a + "}" for a in args)


def _href(url, label):
    return _cmd("href", _u(url), label) if url else label


# Icon per link, by what the URL says it is; anything unrecognised gets the globe.
_ICONS = ((r"linkedin\.", "faLinkedin"), (r"github\.", "faGithub"), (r"gitlab\.", "faGitlab"),
          (r"orcid\.", "faOrcid"), (r"(?:^|\W)x\.com|twitter\.", "faTwitter"))


def _icon(url):
    for pattern, icon in _ICONS:
        if re.search(pattern, url, re.I):
            return icon
    return "faGlobe"


def _raised(icon, body, depth="0.2"):
    r"""The template's icon-then-text idiom: \raisebox{-0.2\height}\faIcon\ text."""
    return r"\raisebox{-" + depth + r"\height}" + "\\" + icon + r"\ " + body


class Latex:
    """Feeds the template's macros. One renderer, because there is one design."""

    ext = "tex"

    def render(self, r):
        out = [PREAMBLE, "", r"%----------- RESUME STARTS -----------",
               r"\begin{document}", "", self._heading(r)]
        for label, entries in r.sections():
            out.append(self._section(label, entries))
        out += ["", r"\end{document}", ""]
        return "\n".join(out)

    # -- header ----------------------------------------------------------------------
    def _heading(self, r):
        """Only the parts that have something in them.

        The break belongs *between* two lines, never after the last one: `\\` with nothing
        in front of it is 'There's no line here to end', which is what a half-filled form
        used to compile into."""
        parts = []
        if r.name:
            parts.append(r"{\Huge \scshape " + _t(r.name) + "}")
        if r.location:
            parts.append(_t(r.location))
        contacts = self._contacts(r)
        if contacts:
            parts.append(r"\small " + (" ~" + "\n   ").join(contacts))
        if not parts:
            return ""  # nothing to head the page with; the sections still print
        body = [f"   {part}" + (r" \\ \vspace{1pt}" if index < len(parts) - 1 else "")
                for index, part in enumerate(parts)]
        return "\n".join([r"%----------HEADING----------", r"\begin{center}", *body,
                          r"   \vspace{-8pt}", r"\end{center}", ""])

    def _contacts(self, r):
        out = []
        if r.phone:
            out.append(_raised("faPhone", _t(r.phone), depth="0.1"))
        if r.email:
            out.append(_href(f"mailto:{r.email}",
                             _raised("faEnvelope", _cmd("underline", _t(r.email)))))
        for label, url in r.links:
            out.append(_href(url, _raised(_icon(url), _cmd("underline", _t(label)))))
        return out

    # -- sections --------------------------------------------------------------------
    def _section(self, label, entries):
        body = {"Technical Skills": self._skills,
                "Education": self._education,
                "Projects": self._projects}.get(label, self._subheadings)(entries)
        tail = {"Technical Skills": "-14pt", "Education": "-8pt", "Experience": "-12pt",
                "Projects": "-11pt"}.get(label)
        head = [f"%-----------{label.upper()}-----------", _cmd("section", _t(label))]
        if label != "Leadership / Extracurricular":  # the template gives this one no lead-in
            head.append(r"\vspace{" + ("-6pt" if label == "Projects" else "-2pt") + "}")
        out = [*head, body]
        if tail:
            out.append(r"\vspace{" + tail + "}")
        return "\n".join(out) + "\n"

    def _items(self, bullets, indent="   "):
        if not bullets:
            return []
        return [indent + r"\resumeItemListStart",
                *[indent + "  " + _cmd("resumeItem", _t(b)) for b in bullets],
                indent + r"\resumeItemListEnd"]

    def _subheadings(self, entries):
        """Experience and Leadership share the template's four-slot subheading."""
        out = [r"\resumeSubHeadingListStart"]
        for e in entries:
            left, role = (e.company, e.title) if isinstance(e, Experience) else (e.org, e.role)
            out += ["", " " + r"\resumeSubheading",
                    "   {" + _t(left) + "}{" + _t(e.period) + "}",
                    "   {" + _t(role) + "}{" + _t(e.location) + "}",
                    *self._items(e.bullets)]
        out += ["", r"\resumeSubHeadingListEnd"]
        return "\n".join(out)

    def _projects(self, entries):
        out = [r"\resumeSubHeadingListStart"]
        for index, p in enumerate(entries):
            if index:
                out += ["", r"\vspace{-16pt}"]  # the template's gap between project blocks
            title = _cmd("textbf", _href(p.url, _cmd("underline", _t(p.name))))
            if p.aside:
                title += r" $|$ " + _cmd("emph", _t(p.aside))
            out += ["", r"\resumeProjectHeading",
                    " {" + title + "}{" + _t(p.period) + "}",
                    *self._items(p.bullets, indent="")]
        out += ["", r"\resumeSubHeadingListEnd"]
        return "\n".join(out)

    def _skills(self, groups):
        rows = [_cmd("textbf", _t(group)) + "{: " + _t(items) + "}" for group, items in groups]
        return "\n".join([r"\begin{itemize}[leftmargin=0.15in, label={}]",
                          r"   \small{\item{",
                          *["    " + row + r" \\" for row in rows],
                          "   }}",
                          r"\end{itemize}"])

    # -- education, the one section whose rows are not one-per-entry -------------------
    def _education(self, entries):
        out = [r"\resumeSubHeadingListStart"]
        for group in _by_school(entries):
            out.append(self._school(group))
        out.append(r"\resumeSubHeadingListEnd")
        return "\n".join(out)

    def _school(self, group):
        """Every degree from one school shares its name, and the coursework below it."""
        rows = [r"     \textbf{" + _t(group[0].school) + r"} & "
                + (_cmd("textbf", r"\small " + _t(group[0].location))
                   if group[0].location else "") + r" \\"]
        for index, e in enumerate(group):
            last = index == len(group) - 1
            rows.append(r"     \textit{\small " + _t(e.degree) + r"} & "
                        + r"\textbf{\small " + _t(e.awarded) + r"} \\")
            if e.concentration or e.gpa:
                rows.append(r"     \textit{\fontsize{7.5pt}{9pt}\selectfont "
                            + _t(e.concentration) + r"} & "
                            + (r"\textit{\small GPA: " + _t(e.gpa) + "}" if e.gpa else "")
                            + (r" \\" if last else r" \\[1.5pt]"))

        out = [" " + r"\vspace{-1.5pt}\item",
               r"   \begin{tabular*}{1.0\textwidth}[t]{l@{\extracolsep{\fill}}r}",
               *rows,
               r"   \end{tabular*}"]
        courses = tuple(c for e in group for c in e.courses)
        if courses:
            out += self._coursework(courses)
        return "\n".join(out)

    def _coursework(self, courses):
        rows = [r"   \item " + _t(c.name) + (r"$^{\dagger}$" if c.graduate else "")
                for c in courses]
        out = [" " + r"\vspace{2pt}",
               " " + r"\textbf{\small Relevant Coursework:}",
               " " + r"\begin{multicols}{4}",
               " " + r"\begin{itemize}[itemsep=1.0pt, leftmargin=0.15in, topsep=0pt, parsep=0pt]",
               r"   \footnotesize",
               *rows,
               " " + r"\end{itemize}",
               " " + r"\end{multicols}"]
        if any(c.graduate for c in courses):
            out += [" " + r"\vspace{1pt}",
                    " " + r"{\footnotesize $^{\dagger}$Graduate-level course}"]
        return out


def _by_school(entries):
    """Consecutive degrees from one school become one block, in the order given."""
    groups = []
    for e in entries:
        if groups and groups[-1][0].school == e.school:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


RENDERER = Latex()


def render(resume, fmt="tex"):
    """The signature keeps its `fmt` so callers read the same; tex is the only answer."""
    if fmt not in ("tex", "latex"):
        raise ValueError(f"this résumé has one design and it is LaTeX, not {fmt!r}")
    return RENDERER.render(resume)


ENGINES = ("tectonic", "pdflatex", "lualatex", "xelatex")

# Launched from Finder or an IDE, a GUI inherits a login PATH that often has neither
# Homebrew nor MacTeX on it, so the usual install prefixes are searched as well.
PREFIXES = ("/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin", "/usr/bin")


def engine():
    """The TeX compiler to use, or None. tectonic first: it fetches its own packages."""
    for exe in ENGINES:
        found = shutil.which(exe)
        if found:
            return found
        for prefix in PREFIXES:
            candidate = os.path.join(prefix, exe)
            if os.access(candidate, os.X_OK):
                return candidate
    return None


def engine_name():
    """Just the compiler's name, for a status line that should not be a path."""
    exe = engine()
    return os.path.basename(exe) if exe else ""


class NoEngine(RuntimeError):
    """Raised instead of a stack trace, because the fix is an install and not a code change."""

    def __init__(self):
        super().__init__("no TeX engine found — install one with `brew install tectonic` "
                         "(a single binary that downloads the packages this template needs)")


def _compile(source, tmp, path, timeout):
    exe = engine()
    if not exe:
        raise NoEngine()
    # engine() may hand back a full path, so the flags are chosen by name, not by the path.
    cmd = ([exe, "--outdir", tmp, "--keep-logs", source]
           if os.path.basename(exe) == "tectonic"
           else [exe, "-interaction=nonstopmode", "-halt-on-error", source])
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, timeout=timeout)
    built = os.path.join(tmp, "resume.pdf")
    if not os.path.exists(built):
        raise RuntimeError(f"{exe} wrote no PDF:\n{_why(done, tmp)}")
    shutil.move(built, path)
    return path


def _why(done, tmp):
    """The last words of the log, which is where LaTeX says what it actually objected to.

    Without this the caller gets the package-loading dump, which says nothing at all."""
    log = os.path.join(tmp, "resume.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            complaints = [l for l in f
                          if l.startswith("!") or "Error" in l or "No pages of output" in l]
        if complaints:
            return "".join(complaints[-8:]).strip()
    return (done.stderr or done.stdout or "no output").strip()[-1500:]


def to_pdf(resume, path, fmt="tex", timeout=120):
    """Render and compile. Raises NoEngine when there is no compiler to run."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "resume.tex")
        with open(source, "w", encoding="utf-8") as f:
            f.write(render(resume, fmt))
        return _compile(source, tmp, path, timeout)


_WORD = re.compile(r"[a-z0-9+#.]+")


def tailor(resume, posting, score=None, keep=None):
    """Rank entries against a posting, keep the best `keep`, then restore date order.

    `score(entry, posting) -> float` is the seam the NLP model drops into; the default
    is a bag-of-words baseline, good enough to prove the plumbing and nothing more."""
    score = score or _overlap

    def pick(entries):
        best = sorted(entries, key=lambda e: -score(e, posting))[:keep or len(entries)]
        return tuple(sorted(best, key=lambda e: e.start, reverse=True))

    return replace(resume, experience=pick(resume.experience), projects=pick(resume.projects))


def _overlap(entry, posting):
    want = set(_WORD.findall(posting.lower()))
    return len(want & set(_WORD.findall(entry.text().lower()))) / (len(want) or 1)
