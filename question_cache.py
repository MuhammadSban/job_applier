"""On-disk cache for the questions application forms ask, and the answers written for them.

The record here is the question, not the listing that asked it: one entry per distinct
thing a form wants to know, holding the answer once so the next form asking the same thing
can reuse the words. Entries are found by tag or by keyword when the wording is not
identical, the way prompt_cache finds a prompt. A listing is only a back-reference onto
that bank, which is why clearing the scrape does not throw an answer away.
"""

import hashlib
import json
import os
import re
import threading
import time

import prompt_cache

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_data",
                    "questions.json")

MATCH = 0.34  # keyword overlap two questions need before they count as the same ask
_SPACE = re.compile(r"\s+")
_TAIL = " \t?:.*—-"  # punctuation a form hangs off a label, never part of the question
_LOCK = threading.RLock()  # forms are read off-thread; the file is read-modify-write


# ── text ─────────────────────────────────────────────────────────────────────────────

def _text(value):
    """One line, no double spaces: the same label however the board laid it out."""
    return _SPACE.sub(" ", str(value or "")).strip()


def _fingerprint(label):
    """What makes two labels the same question: case, spacing and trailing marks don't."""
    return _text(label).lower().strip(_TAIL)


def _id(label):
    """Content hash of the fingerprint, so one question keeps one entry across boards."""
    return hashlib.sha1(_fingerprint(label).encode("utf-8")).hexdigest()[:12]


def normalize(labels):
    """Tags go through one spelling, so lookup matches however they were typed."""
    return sorted({str(x).strip().lower() for x in labels if str(x).strip()})


# ── the file ─────────────────────────────────────────────────────────────────────────

def _empty():
    return {"questions": {}, "listings": {}, "index": {"tags": {}, "keywords": {}}}


def _read():
    """None, never an exception: a missing or half-written file is simply a miss."""
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load():
    raw = _read()
    if not isinstance(raw, dict):
        return _empty()
    if "questions" not in raw:
        return _migrate(raw)
    index = raw.get("index") if isinstance(raw.get("index"), dict) else {}
    return {"questions": raw.get("questions") if isinstance(raw.get("questions"), dict) else {},
            "listings": raw.get("listings") if isinstance(raw.get("listings"), dict) else {},
            "index": {"tags": index.get("tags") or {}, "keywords": index.get("keywords") or {}}}


def _migrate(old):
    """The first shape of this file was url -> rows, which cached links and little else.
    Read it forward: every row becomes a question, every key a listing pointing at them."""
    store = _empty()
    for url, rows in old.items():
        if isinstance(rows, list):
            _put_form(store, str(url), rows, ())
    return store


def _write(store):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True, ensure_ascii=False)
    return store


# ── writing ──────────────────────────────────────────────────────────────────────────

def _record(store, row, tags, url=""):
    """One scraped row merged onto whatever is already known about that question.

    A re-read is the same question seen again, so it never drops an answer, a tag or a
    listing that an earlier read put there."""
    fields = [{"name": _text(f.get("name")), "kind": f.get("kind") or "text",
               "options": [_text(o) for o in f.get("options") or ()]}
              for f in row.get("fields") or () if isinstance(f, dict)]
    label = _text(row.get("label")) or (fields[0]["name"] if fields else "")
    if not label:
        return None  # a control with neither label nor name asks nothing this side can name
    section = _text(row.get("section")) or "application"
    old = store["questions"].get(_id(label)) or {}
    return {"id": _id(label),
            "label": label,
            "section": section,
            "kind": fields[0]["kind"] if fields else old.get("kind", "text"),
            "options": fields[0]["options"] if fields else old.get("options", []),
            "required": bool(row.get("required")) or bool(old.get("required")),
            "description": _text(row.get("description")) or old.get("description", ""),
            "fields": fields or old.get("fields", []),
            "tags": normalize(list(tags) + [section] + list(old.get("tags") or ())),
            "keywords": prompt_cache.keywords(label),
            "answer": old.get("answer", ""),
            "answered": old.get("answered"),
            "asked_by": _joined(old.get("asked_by"), url),
            "seen": old.get("seen") or time.time()}


def _joined(urls, url):
    """The listings that have asked this, oldest first, each of them once."""
    out = [u for u in urls or () if isinstance(u, str)]
    if url and url not in out:
        out.append(url)
    return out


def _add(index, kind, keys, qid):
    for key in keys:
        ids = index[kind].setdefault(key, [])
        if qid not in ids:
            ids.append(qid)


def _index_in(store, question):
    _add(store["index"], "tags", question.get("tags") or (), question["id"])
    _add(store["index"], "keywords", question.get("keywords") or (), question["id"])


def _put_form(store, url, rows, tags):
    """Fold one form into the bank and point the listing at the questions it asked."""
    ids = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        question = _record(store, row, tags, url)
        if question is None:
            continue
        store["questions"][question["id"]] = question
        _index_in(store, question)
        if question["id"] not in ids:  # one form can ask the same thing twice; the bank can't
            ids.append(question["id"])
    store["listings"][url] = ids
    return ids


def save_form(url, rows, tags=()):
    """Cache one listing's form. Returns the question ids, in the order the form asks them.

    An empty form still records the listing: read-and-found-nothing is an answer, and a
    different one from never having looked."""
    with _LOCK:
        store = _load()
        ids = _put_form(store, url, rows, tags)
        _write(store)
        return ids


def save_answer(question, text, tags=()):
    """Write the answer for a question, by id or by label. A label the bank has not seen
    is added to it, so an answer can be written before any form asks for it."""
    with _LOCK:
        store = _load()
        found = _find(store, question)
        if found is None:
            found = _record(store, {"label": question}, tags)
            if found is None:
                return None
            store["questions"][found["id"]] = found
        found["answer"] = _clean_answer(text)
        found["answered"] = time.time() if found["answer"] else None
        if tags:
            found["tags"] = normalize(list(tags) + list(found.get("tags") or ()))
        _index_in(store, found)
        _write(store)
        return found["id"]


def save_answers(pairs, tags=()):
    """Many answers in one pass over the file. pairs is (question, text) or a dict."""
    items = pairs.items() if isinstance(pairs, dict) else pairs
    with _LOCK:
        return [qid for qid in (save_answer(q, t, tags) for q, t in items) if qid]


def _clean_answer(text):
    return str(text or "").strip()


# ── reading ──────────────────────────────────────────────────────────────────────────

def _find(store, question):
    """Resolve an id or a label onto its entry, or None."""
    key = str(question or "").strip()
    if not key:
        return None
    return store["questions"].get(key) or store["questions"].get(_id(key))


def load(question):
    """One question by id or by label, with its answer. None when the bank has neither."""
    return _find(_load(), question)


def _row(question):
    """The shape scout.questions() hands back, plus what this cache adds to it."""
    return {"label": question.get("label", ""),
            "fields": [dict(f) for f in question.get("fields") or ()],
            "required": bool(question.get("required")),
            "section": question.get("section", "application"),
            "description": question.get("description", ""),
            "id": question.get("id", ""),
            "answer": question.get("answer", "")}


def form(url):
    """One listing's cached form, in the order it asks. None means it has never been read,
    which is not the same answer as [], meaning it was read and asks nothing visible."""
    store = _load()
    ids = store["listings"].get(url)
    if not isinstance(ids, list):
        return None
    return [_row(store["questions"][i]) for i in ids if i in store["questions"]]


def listings():
    """Every listing whose form has been read, and how many questions it asked."""
    store = _load()
    return {url: len(ids or ()) for url, ids in store["listings"].items()}


def answer(question):
    """The answer written for exactly this question, or None."""
    found = load(question)
    return (found or {}).get("answer") or None


def answer_for(question):
    """The words to reuse here: this question's own answer, or the closest answered
    question's when it has none. None when nothing in the bank comes near it.

    `from` is empty on an exact hit and carries the other question's label otherwise, so a
    caller can say where a borrowed answer came from instead of pretending it was written
    for this form."""
    return _answer_for(_load(), question)


def answers_for(questions):
    """answer_for over a whole form, in one pass over the file rather than one per row."""
    store = _load()
    return [_answer_for(store, q) for q in questions or ()]


def _answer_for(store, question):
    found = _find(store, question)
    if found and found.get("answer"):
        return {"answer": found["answer"], "from": "", "score": 1.0}
    label = found["label"] if found else str(question or "")
    near = _similar(store, label, limit=1, answered_only=True)
    if not near:
        return None
    return {"answer": near[0]["answer"], "from": near[0]["label"], "score": near[0]["score"]}


def similar(label, limit=5, answered_only=False):
    """Questions whose wording overlaps this one, closest first — the cheap stand-in for
    knowing that two boards are asking the same thing in different words. The question
    itself is never its own match, so a caller can ask what else is like it."""
    return _similar(_load(), label, limit, answered_only)


def _similar(store, label, limit=5, answered_only=False):
    want = set(prompt_cache.keywords(label))
    if not want:
        return []
    here = _fingerprint(label)
    out = []
    for question in _all(store):
        if answered_only and not question.get("answer"):
            continue
        have = set(question.get("keywords") or ())
        if not have or _fingerprint(question.get("label")) == here:
            continue
        score = len(want & have) / len(want | have)
        if score >= MATCH:
            out.append(dict(question, score=round(score, 3)))
    return sorted(out, key=lambda q: (-q["score"], q["label"]))[:limit]


def _hits(store, ids):
    """Ids whose entry has since gone drop out; nothing left is a miss, so None."""
    found = [store["questions"][i] for i in ids if i in store["questions"]]
    return found or None


def find_by_tag(tag):
    store = _load()
    return _hits(store, store["index"]["tags"].get(str(tag).strip().lower(), ()))


def find_by_keyword(word):
    store = _load()
    return _hits(store, store["index"]["keywords"].get(str(word).strip().lower(), ()))


def search(tags=(), keywords=()):
    """Questions ordered by how many of the asked-for tags and keywords they carry."""
    store = _load()
    score = {}
    for kind, wanted in (("tags", normalize(tags)), ("keywords", normalize(keywords))):
        for key in wanted:
            for qid in store["index"][kind].get(key, ()):
                score[qid] = score.get(qid, 0) + 1
    return _hits(store, sorted(score, key=lambda i: -score[i]))


def all_questions():
    """Every question ever read, newest first."""
    return sorted(_all(_load()), key=lambda q: q.get("seen") or 0, reverse=True)


def answered():
    """Only the questions with an answer written — the part worth keeping."""
    return [q for q in all_questions() if q.get("answer")]


def _all(store):
    return [q for q in store["questions"].values() if isinstance(q, dict) and q.get("id")]


# ── upkeep ───────────────────────────────────────────────────────────────────────────

def forget_listings():
    """Drop the scrape: the listing back-references and every question nobody answered.
    Answered questions are the user's own writing, so they stay."""
    with _LOCK:
        store = _load()
        keep = {qid: q for qid, q in store["questions"].items() if q.get("answer")}
        for question in keep.values():
            question["asked_by"] = []
        return _write({"questions": keep, "listings": {},
                       "index": _rebuilt(keep.values())})


def rebuild_index():
    """The entries are the record; the index is only a shortcut, so it can be redrawn."""
    with _LOCK:
        store = _load()
        store["index"] = _rebuilt(_all(store))
        _write(store)
        return store["index"]


def _rebuilt(questions):
    index = {"tags": {}, "keywords": {}}
    for question in questions:
        _add(index, "tags", question.get("tags") or (), question["id"])
        _add(index, "keywords", question.get("keywords") or (), question["id"])
    return index


if __name__ == "__main__":
    import tempfile

    PATH = os.path.join(tempfile.mkdtemp(), "questions.json")  # never the real archive

    save_form("https://example.com/jobs/1",
              [{"label": "Why do you want to work here?", "section": "application",
                "required": True, "fields": [{"name": "why", "kind": "textarea"}]},
               {"label": "Years of Python experience", "section": "application",
                "fields": [{"name": "py", "kind": "number"}]}],
              tags=("greenhouse", "Example Co"))
    save_form("https://example.com/jobs/2",
              [{"label": "Why do you want to work at Example?", "section": "application",
                "fields": [{"name": "why", "kind": "textarea"}]}],
              tags=("greenhouse", "Example Co"))
    save_answer("Why do you want to work here?", "Because the latency work is the fun part.")

    print("form 1     ->", [(q["label"], q["answer"]) for q in form("https://example.com/jobs/1")])
    print("by tag     ->", [q["label"] for q in find_by_tag("greenhouse")])
    print("by keyword ->", [q["label"] for q in find_by_keyword("python")])
    print("borrowed   ->", answer_for("Why do you want to work at Example?"))
    print("miss       ->", answer_for("Do you need visa sponsorship?"))
    print("unread     ->", form("https://example.com/jobs/9"))
    print("kept       ->", [q["label"] for q in forget_listings()["questions"].values()])
