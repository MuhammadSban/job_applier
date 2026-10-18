"""Whatever the user typed, kept on disk so the next run starts where the last one stopped."""

import json
import os

ARCHIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_data")
BIO = os.path.join(ARCHIVE, "bio.json")
JOBS = os.path.join(ARCHIVE, "jobs.json")


def _read(path):
    """Missing file, unreadable file, and malformed JSON are all the same answer: None."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def save_bio(fields):
    """Merge the GUI's field dict into whatever is stored; blanks never overwrite.

    The merge goes one level in. The GUI saves under a namespace — {"resume": {...}} — so a
    shallow update would hand the whole résumé over as a single non-blank value and every
    blank field inside it would land, wiping what was there. That is a real way to lose the
    lot: launch with the tabs somehow unpopulated, close the window, and the save on exit
    writes the empty ones over the full ones."""
    bio = _read(BIO) or {}
    for key, value in fields.items():
        if isinstance(value, dict) and isinstance(bio.get(key), dict):
            bio[key] = {**bio[key],
                        **{k: v for k, v in value.items() if v not in ("", None)}}
        elif value not in ("", None):
            bio[key] = value
    return _write(BIO, bio)


def bio_info(field=None):
    """The whole bio dict, or one field of it. None when there is nothing cached."""
    bio = _read(BIO)
    if bio is None:
        return None
    if field is None:
        return bio
    return bio.get(field)


def forget_bio():
    try:
        os.remove(BIO)
    except OSError:
        pass


# ── listings ─────────────────────────────────────────────────────────────────────────

def save_jobs(rows):
    """Merge a scrape into the archive, keyed by url, newest first. Returns the archive.

    A search adds to what is already there rather than replacing it, so the GUI's list is
    everything ever scraped and not just the last query."""
    jobs = {j["url"]: j for j in _jobs_list() if j.get("url")}
    for row in rows:
        if row.get("url"):
            jobs[row["url"]] = {**jobs.get(row["url"], {}), **row}
    return _write(JOBS, sorted(jobs.values(), key=lambda j: j.get("seen") or 0, reverse=True))


def jobs():
    """Every listing ever scraped, newest first. [] when nothing has been."""
    return _jobs_list()


def _jobs_list():
    found = _read(JOBS)
    return [j for j in found if isinstance(j, dict)] if isinstance(found, list) else []


def forget_jobs():
    """Drops the listing archive and the forms read against it. Answers survive it: they
    are the user's own writing, and the next scrape will ask for them again."""
    try:
        os.remove(JOBS)
    except OSError:
        pass
    store = _question_cache()
    if store is not None:
        store.forget_listings()


def _job(url):
    for job in _jobs_list():
        if job.get("url") == url:
            return job
    return {}


# ── application questions and their answers ──────────────────────────────────────────

def save_questions(url, rows):
    """Cache one listing's form. The questions are what is stored, tagged with the board
    and company that asked them; the listing only points at them, so the same question on
    the next posting arrives with the answer already written."""
    store = _question_cache()
    if store is None:
        return rows
    job = _job(url)
    store.save_form(url, rows, tags=(job.get("source", ""), job.get("company", "")))
    return rows


def questions(url):
    """The cached form for one listing, each row carrying its answer. None means it has
    never been read, which is not the same answer as [], meaning it was read and asks
    nothing this side can see."""
    store = _question_cache()
    return None if store is None else store.form(url)


def save_answer(question, text, tags=()):
    """Write the answer for a question, by id or by label. Returns its id, or None."""
    store = _question_cache()
    return None if store is None else store.save_answer(question, text, tags)


def save_answers(pairs, tags=()):
    """Many answers at once: (question, text) pairs, or a dict of them."""
    store = _question_cache()
    return None if store is None else store.save_answers(pairs, tags)


def answer(question):
    """The answer written for exactly this question, or None."""
    store = _question_cache()
    return None if store is None else store.answer(question)


def answer_for(question):
    """The words to reuse for this question: its own answer, or the nearest answered
    question's. A dict with `answer`, `from` and `score`, or None for no match."""
    store = _question_cache()
    return None if store is None else store.answer_for(question)


def answers_for(questions):
    """answer_for over a whole form at once — one pass over the file, not one per row."""
    store = _question_cache()
    return [None] * len(list(questions or ())) if store is None else store.answers_for(questions)


def question(question_id):
    """One cached question by id or by label, with its answer, or None."""
    store = _question_cache()
    return None if store is None else store.load(question_id)


def find_questions(tags=(), keywords=()):
    """Cached questions matching the tags/keywords, best match first, or None."""
    store = _question_cache()
    return None if store is None else store.search(tags, keywords)


def similar_questions(label, limit=5, answered_only=False):
    """Questions worded close enough to this one to be the same ask, closest first."""
    store = _question_cache()
    return [] if store is None else store.similar(label, limit, answered_only)


def all_questions():
    """Every question ever read, newest first, or None."""
    store = _question_cache()
    return None if store is None else store.all_questions()


def answered_questions():
    """Only the questions with an answer written, newest first, or None."""
    store = _question_cache()
    return None if store is None else store.answered()


def _question_cache():
    """Imported late, for the same reason the prompt store is: this module's bio half has
    to keep working when the rest of the archive is not there yet."""
    try:
        import question_cache
    except ImportError:
        return None
    return question_cache


# ── prompts ──────────────────────────────────────────────────────────────────────────

def save_prompt(text, tags=()):
    """Hand a free-text prompt to the tag/keyword store. None if that store is absent."""
    store = _prompt_cache()
    if store is None:
        return None
    return store.save_prompt(text, tags)


def prompt(prompt_id):
    """One cached prompt by id, or None."""
    store = _prompt_cache()
    if store is None:
        return None
    return store.load_prompt(prompt_id)


def prompts(tags=(), keywords=()):
    """Cached prompts matching the tags/keywords, best match first, or None."""
    store = _prompt_cache()
    if store is None:
        return None
    return store.search(tags, keywords)


def all_prompts():
    """Every cached prompt, newest first, or None."""
    store = _prompt_cache()
    if store is None:
        return None
    return store.all_prompts()


def _prompt_cache():
    """Imported late so the bio half of this module works before prompt_cache exists."""
    try:
        import prompt_cache
    except ImportError:
        return None
    return prompt_cache


if __name__ == "__main__":
    save_bio({"Full Name": "Dana Okonkwo", "Email": "dana.okonkwo@example.com", "Phone": ""})
    print("all      ->", bio_info())
    print("one      ->", bio_info("Email"))
    print("missing  ->", bio_info("Nickname"))
    forget_bio()
    print("forgotten->", bio_info())
