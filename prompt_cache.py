"""On-disk cache for the prompts a user types, found again by tag or keyword rather than by id."""

import hashlib
import json
import os
import re
import time

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive_data", "prompts")
INDEX = os.path.join(DIR, "index.json")

_WORD = re.compile(r"[a-z0-9+#.]+")
_STOP = {"the", "and", "for", "with", "that", "this", "you", "your", "are", "was",
         "have", "has", "had", "not", "but", "from", "they", "their", "our", "who",
         "what", "when", "where", "how", "all", "any", "can", "will", "would", "about",
         "into", "than", "then", "there", "been", "were", "its", "his", "her"}


def keywords(text):
    """The résumé ranker's tokenizer, minus the words too common to narrow anything down."""
    # the shared pattern keeps dots for "node.js"; sentence-final ones are not part of the word
    words = (w.rstrip(".") for w in _WORD.findall(text.lower()))
    return sorted({w for w in words if len(w) > 2 and w not in _STOP})


def normalize(labels):
    """Tags and query words go through one spelling, so lookup matches however they were typed."""
    return sorted({str(x).strip().lower() for x in labels if str(x).strip()})


def _id(text):
    """Content hash, so re-saving the same text lands on its own file instead of a duplicate."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _path(prompt_id):
    return os.path.join(DIR, prompt_id + ".json")


def _read(path):
    """None, never an exception: a missing or half-written file is simply a miss."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    os.makedirs(DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def _empty():
    return {"tags": {}, "keywords": {}}


def _index():
    """A missing or corrupt index reads as an empty one; rebuild_index() puts it back."""
    index = _read(INDEX)
    if not isinstance(index, dict):
        return _empty()
    return {"tags": index.get("tags") or {}, "keywords": index.get("keywords") or {}}


def _add(index, kind, keys, prompt_id):
    for key in keys:
        ids = index[kind].setdefault(key, [])
        if prompt_id not in ids:
            ids.append(prompt_id)


def _hits(ids):
    """Ids whose file has since vanished drop out; nothing left means a miss, so None."""
    found = [p for p in (load_prompt(i) for i in ids) if p]
    return found or None


def save_prompt(text, tags=()):
    """Store text under its content hash, indexed by the caller's tags and derived keywords."""
    prompt_id = _id(text)
    old = load_prompt(prompt_id) or {}
    prompt = {"id": prompt_id,
              "text": text,
              # a re-save is the same prompt with maybe more labels, not a newer one
              "tags": normalize(list(tags) + list(old.get("tags", ()))),
              "keywords": keywords(text),
              "created": old.get("created") or time.time()}
    _write(_path(prompt_id), prompt)

    index = _index()
    _add(index, "tags", prompt["tags"], prompt_id)
    _add(index, "keywords", prompt["keywords"], prompt_id)
    _write(INDEX, index)
    return prompt_id


def load_prompt(prompt_id):
    prompt = _read(_path(prompt_id))
    return prompt if isinstance(prompt, dict) else None


def find_by_tag(tag):
    return _hits(_index()["tags"].get(str(tag).strip().lower(), ()))


def find_by_keyword(word):
    return _hits(_index()["keywords"].get(str(word).strip().lower(), ()))


def search(tags=(), keywords=()):
    """Prompts ordered by how many of the asked-for tags and keywords they carry."""
    index = _index()
    score = {}
    for kind, wanted in (("tags", normalize(tags)), ("keywords", normalize(keywords))):
        for key in wanted:
            for prompt_id in index[kind].get(key, ()):
                score[prompt_id] = score.get(prompt_id, 0) + 1
    return _hits(sorted(score, key=lambda i: -score[i]))


def all_prompts():
    """Every stored prompt, newest first. The index is a shortcut; the files are the record."""
    return sorted(_all(), key=lambda p: p.get("created") or 0, reverse=True)


def _all():
    try:
        names = sorted(os.listdir(DIR))
    except OSError:
        return []
    found = (load_prompt(n[:-5]) for n in names if n.endswith(".json") and n != "index.json")
    return [p for p in found if p and p.get("id")]


def rebuild_index():
    """The prompt files are the record; the index is only a shortcut, so it can be redrawn."""
    index = _empty()
    for prompt in _all():
        _add(index, "tags", prompt.get("tags", ()), prompt["id"])
        _add(index, "keywords", prompt.get("keywords", ()), prompt["id"])
    _write(INDEX, index)
    return index


if __name__ == "__main__":
    save_prompt("Dear hiring manager, I build data-heavy Python services and like latency work.",
                tags=("cover-letter", "backend"))
    save_prompt("Writing sample: how contract tests ended our weekly deploy rollbacks.",
                tags=("writing-sample",))
    save_prompt("Find remote Python roles at small teams with no on-call rotation.",
                tags=("job-search", "Backend"))

    print("tag backend    ->", [p["id"] for p in find_by_tag("backend")])
    print("keyword python ->", [p["id"] for p in find_by_keyword("python")])
    print("search         ->", [p["id"] for p in search(tags=("backend",), keywords=("remote",))])
    print("miss tag       ->", find_by_tag("kayaking"))
    print("miss id        ->", load_prompt("000000000000"))
    print("rebuilt tags   ->", sorted(rebuild_index()["tags"]))
    print("all            ->", [p["id"] for p in all_prompts()])
