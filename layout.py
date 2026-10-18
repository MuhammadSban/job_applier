"""Documents, as lists of Boxes for paper.py to draw.

Two things get built here and they are deliberately built the same way: a résumé, straight
from the model in generate_resume, and an application form, straight from the question rows
the cache holds. Neither goes near HTML. The résumé in particular is drawn from the model
rather than from the compiled PDF, so the preview is instant and still works on a machine
with no TeX engine installed — the PDF stays the truth for Export, not for looking at.

form_doc is the one the macro walks. Every question becomes a control Box carrying the key
its answer is cached under (`qid`), so filling one in is enough to save it.
"""

import os

import theme
from paper import Box

# A file upload is answered by the document the app just built, not by typing, so the macro
# attaches it and moves on instead of stopping to ask. The set lives in paper, which draws
# those values in mono; it is re-exported here because that is where the macro reads it.
from paper import FILE_KINDS  # noqa: F401  (re-exported for macro.py)

SECTION_TITLES = {"application": "Application", "demographic": "Demographic — optional",
                  "location": "Location", "eeoc": "EEOC", "compliance": "Compliance"}


# ── résumé and letter ────────────────────────────────────────────────────────────────

def _joined(*parts, sep=" · "):
    return sep.join(str(p).strip() for p in parts if p and str(p).strip())


def resume_doc(resume):
    """The résumé as the model has it — the same sections, in the same order, that the
    LaTeX renderer prints."""
    doc = [Box("title", resume.name or "Your name")]
    contact = _joined(resume.email, resume.phone, resume.location)
    if contact:
        doc.append(Box("sub", contact))
    links = _joined(*(f"{label}: {url}" for label, url in resume.links or ()))
    if links:
        doc.append(Box("sub", links))

    sections = list(resume.sections())  # a generator, and it is read twice below
    for label, entries in sections:
        doc += [Box("head", label.upper(), space=16), Box("rule")]
        if label.lower().startswith("technical skills"):
            doc += [Box("body", f"{group}: {items}") for group, items in entries]
            continue
        for entry in entries:
            doc += _entry(entry)
    if not sections:
        doc.append(Box("note", "Nothing filled in yet — start on the Profile tab.", space=20))
    return doc


def _entry(entry):
    """One dated block. Each model carries its own shape, so each gets its own two lines."""
    name = type(entry).__name__
    if name == "Experience":
        head, aside, when = entry.company, _joined(entry.title, entry.location), entry.period
    elif name == "Project":
        head, aside, when = entry.name, entry.aside, entry.period
    elif name == "Education":
        head = entry.school
        aside = _joined(entry.degree, entry.concentration,
                        f"GPA {entry.gpa}" if entry.gpa else "", entry.location)
        when = entry.awarded
    elif name == "Leadership":
        head, aside, when = entry.org, _joined(entry.role, entry.location), entry.period
    else:
        head, aside, when = entry.heading, "", entry.period

    out = [Box("strong", head, right=when, space=8)]
    if aside:
        out.append(Box("sub", aside))
    out += [Box("bullet", str(b)) for b in entry.bullets or ()]
    courses = getattr(entry, "courses", ())
    if courses:
        out.append(Box("sub", "Coursework: " + ", ".join(
            c.name + (" †" if c.graduate else "") for c in courses)))
    return out


def letter_doc(name, body):
    doc = [Box("title", name or "Cover letter")]
    paragraphs = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    doc += [Box("body", p, space=10) for p in paragraphs]
    if not paragraphs:
        doc.append(Box("note", "No letter written yet — the Letter tab is where it goes.",
                       space=16))
    return doc


# ── the application form ─────────────────────────────────────────────────────────────

def _spec(row):
    """A row's control kind and options. scout.Question keeps both on its first field, and
    asdict() drops the properties that read them, so they are read back off the field here."""
    fields = row.get("fields") or ()
    first = fields[0] if fields else {}
    return (first.get("kind") or "text"), tuple(first.get("options") or ())


def form_doc(job, rows, answers, attachment=""):
    """The listing's application, as a form to walk.

    `answers` lines up with `rows` — each one the cache's verdict on that question: an exact
    answer, one borrowed from a question worded like it (which carries `from`), or None,
    which is what makes the macro stop.
    """
    company = job.get("company") or "this company"
    doc = [Box("title", job.get("title") or "Application"),
           Box("sub", _joined(company, job.get("location"), job.get("source")))]
    doc.append(Box("note", f"Simulation — this form is drawn from the cached listing and "
                           f"nothing is ever sent to {company}.", fill=theme.CAUTION,
                   space=6))

    rows = list(rows or ())
    if not rows:
        doc.append(Box("note", "No form cached for this listing yet.", space=20))
        return doc

    section, number = None, 0
    for row, found in zip(rows, answers or [None] * len(rows)):
        if row.get("section") != section:
            section = row.get("section")
            doc += [Box("head", SECTION_TITLES.get(section, str(section or "").title()),
                        space=18), Box("rule")]
        number += 1
        kind, options = _spec(row)
        label = row.get("label") or "(unlabelled)"
        aside = _joined(kind if kind != "text" else "", "required" if row.get("required") else "")
        doc.append(Box("strong", f"{number}. {label}", right=aside, space=12))
        if row.get("description"):
            doc.append(Box("sub", row["description"][:400]))

        control = Box("control", qid=row.get("id") or label, label=label,
                      control=kind, options=options, required=bool(row.get("required")))
        if kind in FILE_KINDS:
            control.value = os.path.basename(attachment) if attachment else ""
            control.state = "filled" if control.value else "empty"
            control.source = "attached by the app" if control.value else ""
        elif found and found.get("answer"):
            control.value = found["answer"]
            borrowed = bool(found.get("from")) and found.get("score", 1.0) < 1.0
            control.state = "borrowed" if borrowed else "filled"
            control.source = found.get("from") or ""
        doc.append(control)

    doc.append(Box("button", "Submit application (simulated)", space=22))
    return doc
