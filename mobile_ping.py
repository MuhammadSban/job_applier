"""Ping a phone the moment cache_mem_access.prompt() starts returning something.

That function is temporary in the sense that matters here: it is a thin seam over a
prompt store that may not exist yet, and it answers None for an absent store, a missing
file, and a half-written one alike. So the only way to know whether there is a prompt to
act on is to call it and look at what comes back.

This module does that looking. It calls the function, decides whether the return value
is a prompt structure - a record with real text in it, not an index, not a miss, not a
stack trace - and if it is, puts the text on a phone.

Nothing here imports the store at module scope. It is allowed to be absent, to raise, or
to keep answering None, and each of those means "no prompt yet", not "error".

    python mobile_ping.py               # every demo, printed to the terminal
    python mobile_ping.py cache_hit     # one demo
    python mobile_ping.py --send        # route through the real phone transport
"""

import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass

TEMP_FN = "cache_mem_access.prompt"  # the temporary function this module watches
TIMEOUT = 10       # seconds, HTTP
LOCK_SCREEN = 240  # characters of prompt text a notification is worth


# --------------------------------------------------------------------------- structure

@dataclass(frozen=True)
class Prompt:
    """A prompt_cache record, in the one shape the rest of this module handles.

    Mirrors what save_prompt() writes: id, text, tags, keywords, created. Everything but
    the text is optional, because a store still being written may not fill it all in."""

    text: str
    id: str = ""
    tags: tuple = ()
    keywords: tuple = ()
    created: float = 0.0
    source: str = ""  # which call produced it; for the notification, not the record

    def title(self):
        label = ", ".join(self.tags) if self.tags else "Saved prompt"
        return f"{label} · {self.source}" if self.source else label

    def body(self):
        text = self.text.strip()
        if len(text) > LOCK_SCREEN:
            text = text[:LOCK_SCREEN - 1].rstrip() + "…"
        return f"{text}\n[{self.id}]" if self.id else text

    def fingerprint(self):
        """Dedupe key. The store already ids records by content hash, so honour it."""
        return self.id or _content_id(self.text)


def _content_id(text):
    """prompt_cache._id's rule, recomputed rather than imported: this module has to be
    able to judge a record even when the store it came from will not import."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


TEXT_KEYS = ("text", "prompt", "body", "content")
TAG_KEYS = ("tags", "labels")


def _get(obj, names, default=None):
    read = obj.get if isinstance(obj, Mapping) else lambda n, d=None: getattr(obj, n, d)
    for name in names:
        value = read(name, None)
        if value not in (None, "", (), [], {}):
            return value
    return default


def _strs(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(v) for v in value)
    except TypeError:
        return ()


def as_prompt(value, source="", allow_text=True):
    """Coerce one return value into a Prompt, or None if it is not a prompt structure.

    Truthiness is not the test. A store's index file is a fat, entirely true dict with no
    text in it; a miss is None; a corrupt record can be anything json parses. The test is
    that there is prompt text to put in front of a human.

    allow_text=False refuses a bare string. Callers use it for members of a sequence,
    where a loose string is far likelier to be a tag or a keyword than a whole prompt."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Prompt):
        return value if value.text.strip() else None
    if isinstance(value, str):
        text = value.strip()
        if not text or not allow_text:
            return None
        return Prompt(text=text, id=_content_id(text), source=source)
    if not isinstance(value, Mapping) and not hasattr(value, "__dict__") \
            and not hasattr(value, "_fields"):
        return None  # numbers, bare sequences, None-likes: not a structure at all

    text = _get(value, TEXT_KEYS)
    if not isinstance(text, str) or not text.strip():
        return None

    created = _get(value, ("created", "saved", "time"), 0.0)
    return Prompt(
        text=text.strip(),
        id=str(_get(value, ("id", "hash"), "") or _content_id(text.strip())),
        tags=_strs(_get(value, TAG_KEYS, ())),
        keywords=_strs(_get(value, ("keywords",), ())),
        created=float(created) if isinstance(created, (int, float)) else 0.0,
        source=source,
    )


def as_prompts(value, source=""):
    """One prompt or many, always a tuple. search() and prompts() return lists or None,
    load_prompt() returns one or None, and unreadable members drop out quietly."""
    if value is None:
        return ()
    if isinstance(value, (str, Mapping, Prompt)):
        one = as_prompt(value, source)
        return (one,) if one else ()
    if hasattr(value, "__iter__"):
        members = (as_prompt(v, source, allow_text=False) for v in value)
        return tuple(p for p in members if p)
    one = as_prompt(value, source)
    return (one,) if one else ()


def is_prompt(value):
    """The predicate on its own, for callers that only want the yes/no."""
    return as_prompt(value) is not None


# --------------------------------------------------------------------------- transports

class Pinger:
    """A phone, or a stand-in for one. send() returns True if the ping went out."""

    name = "pinger"

    def send(self, prompt):
        raise NotImplementedError


class Console(Pinger):
    """The default everywhere. Sends nothing anywhere; shows what would have gone out."""

    name = "console"

    def __init__(self, stream=None):
        self.stream = stream or sys.stdout

    def send(self, prompt):
        print(f"      \033[7m ping \033[0m {prompt.title()}", file=self.stream)
        for line in prompt.body().splitlines():
            print(f"             {line}", file=self.stream)
        return True


class Recording(Pinger):
    """Keeps prompts in a list instead of sending them, so a test can count them."""

    name = "recording"

    def __init__(self):
        self.sent = []

    def send(self, prompt):
        self.sent.append(prompt)
        return True


def _ascii(s):
    """HTTP headers are latin-1; an em dash in a tag should not fail the ping."""
    return s.encode("ascii", "ignore").decode().strip() or "Saved prompt"


class Ntfy(Pinger):
    """ntfy.sh - the phone app subscribes to a topic, any POST to that topic buzzes it.

    No account and no API key, which also means the topic name is the only secret: pick
    an unguessable one, or self-host and set MOBILE_PING_NTFY_SERVER."""

    name = "ntfy"

    def __init__(self, topic, server="https://ntfy.sh"):
        self.topic = topic
        self.server = server.rstrip("/")

    def send(self, prompt):
        import requests

        r = requests.post(f"{self.server}/{self.topic}", timeout=TIMEOUT,
                          data=prompt.body().encode("utf-8"),
                          headers={"Title": _ascii(prompt.title()), "Tags": "briefcase"})
        r.raise_for_status()
        return True


class Pushover(Pinger):
    """Costs once, but delivery is acknowledged and the key is not a public topic name."""

    name = "pushover"
    API = "https://api.pushover.net/1/messages.json"

    def __init__(self, token, user):
        self.token, self.user = token, user

    def send(self, prompt):
        import requests

        r = requests.post(self.API, timeout=TIMEOUT,
                          data={"token": self.token, "user": self.user,
                                "title": prompt.title(), "message": prompt.body()})
        r.raise_for_status()
        return True


class Desktop(Pinger):
    """macOS Notification Center, for before the phone side is set up. Leaves no machine."""

    name = "desktop"

    def send(self, prompt):
        def q(s):
            return s.replace("\\", "").replace('"', "'")

        subprocess.run(["osascript", "-e", f'display notification "{q(prompt.body())}" '
                                           f'with title "{q(prompt.title())}"'], check=False)
        return True


def pinger_from_env(default=None):
    """Real transports are opt-in: with nothing configured, no packet leaves the machine."""
    topic = os.environ.get("MOBILE_PING_NTFY_TOPIC")
    if topic:
        return Ntfy(topic, os.environ.get("MOBILE_PING_NTFY_SERVER", "https://ntfy.sh"))
    token, user = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if token and user:
        return Pushover(token, user)
    if os.environ.get("MOBILE_PING_DESKTOP") and sys.platform == "darwin":
        return Desktop()
    return default or Console()


# --------------------------------------------------------------------------- the check

def resolve(dotted=TEMP_FN, on_error=None):
    """Import 'module.attr' late and forgivingly. The target is unfinished code, so even
    the import is a maybe - a half-written module raises SyntaxError, not ImportError."""
    module, _, attr = dotted.rpartition(".")
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        if on_error:
            on_error(exc)
        return None
    fn = getattr(mod, attr, None)
    if not callable(fn):
        if on_error:
            on_error(AttributeError(f"{dotted} is not callable"))
        return None
    return fn


def check(fn, *args, on_error=None, **kwargs):
    """Call fn and return the prompts it handed back, or () for 'nothing there'.

    fn may be a dotted path. It is allowed to be missing, to raise, or to return junk;
    all three mean no prompt, so none of them reach the caller as exceptions."""
    source = fn if isinstance(fn, str) else getattr(fn, "__name__", "")
    if isinstance(fn, str):
        fn = resolve(fn, on_error)
    if fn is None:
        return ()
    try:
        value = fn(*args, **kwargs)
    except Exception as exc:
        if on_error:
            on_error(exc)
        return ()
    return as_prompts(value, source)


def ping(prompts, pinger=None, seen=None):
    """Send each prompt once. `seen` is a fingerprint set carried across calls, so a loop
    that finds the same cached prompt every 30s buzzes the phone exactly once."""
    pinger = pinger or Console()
    sent = []
    for prompt in prompts:
        mark = prompt.fingerprint()
        if seen is not None and mark in seen:
            continue
        if pinger.send(prompt):
            sent.append(prompt)
            if seen is not None:
                seen.add(mark)
    return tuple(sent)


def check_and_ping(fn, *args, pinger=None, seen=None, on_error=None, **kwargs):
    """The whole module in one line: call it, judge it, ping it."""
    return ping(check(fn, *args, on_error=on_error, **kwargs), pinger, seen)


def watch(fn, *args, pinger=None, interval=30.0, timeout=None, seen=None,
          clock=time.monotonic, sleep=time.sleep, on_error=None, **kwargs):
    """Poll until the function has a prompt, or until timeout. Returns what was sent.
    clock and sleep are injected so a demo can run the loop without living through it."""
    seen = set() if seen is None else seen
    deadline = None if timeout is None else clock() + timeout
    while True:
        sent = check_and_ping(fn, *args, pinger=pinger, seen=seen, on_error=on_error, **kwargs)
        if sent:
            return sent
        if deadline is not None and clock() >= deadline:
            return ()
        sleep(interval)


# --------------------------------------------------------------------------- demos

DEMOS = []


def demo(fn):
    DEMOS.append(fn)
    return fn


def _say(text):
    print(f"      {text}")


@contextmanager
def _store():
    """The demos save prompts. They should not land in the user's real archive_data, so
    the store's paths are pointed at a temp dir for the duration. Yields None when
    prompt_cache is not importable, which is itself one of the cases being demoed."""
    try:
        import prompt_cache
    except Exception as exc:
        _say(f"prompt_cache unavailable ({type(exc).__name__}: {exc})")
        yield None
        return

    tmp = tempfile.mkdtemp(prefix="mobile_ping_")
    was = (prompt_cache.DIR, prompt_cache.INDEX)
    prompt_cache.DIR = tmp
    prompt_cache.INDEX = os.path.join(tmp, "index.json")
    try:
        yield prompt_cache
    finally:
        prompt_cache.DIR, prompt_cache.INDEX = was
        shutil.rmtree(tmp, ignore_errors=True)


SAMPLE = ("Dear hiring manager - I build data-heavy Python services and enjoy the "
          "arguments between correctness and latency.")


@demo
def cache_miss(pinger):
    """The everyday answer. Nothing stored under that id, so the phone stays quiet."""
    with _store() as store:
        if store is None:
            return
        fn = resolve(TEMP_FN)
        sent = check_and_ping(fn, "000000000000", pinger=pinger)
        _say(f"{len(sent)} pings - a miss is None, and None is not a prompt")


@demo
def cache_hit(pinger):
    """The moment this module exists for: the temporary function returns a real record
    and the text of it lands on a phone. Nothing is faked here but the store's location."""
    with _store() as store:
        if store is None:
            return
        prompt_id = store.save_prompt(SAMPLE, tags=("cover-letter", "backend"))
        fn = resolve(TEMP_FN)
        sent = check_and_ping(fn, prompt_id, pinger=pinger)
        _say(f"{len(sent)} pings for id {prompt_id}")


@demo
def the_shape(pinger):
    """What a record looks like on the way in, and the Prompt it becomes on the way out."""
    with _store() as store:
        if store is None:
            return
        record = store.load_prompt(store.save_prompt(SAMPLE, tags=("cover-letter",)))
        _say(f"returned keys: {sorted(record)}")
        prompt = as_prompt(record, source="prompt")
        for name in ("id", "text", "tags", "keywords", "created"):
            value = getattr(prompt, name)
            shown = f"{value[:52]}…" if name == "text" else value
            _say(f"  .{name:<9} {shown!r}")


@demo
def search_returns_many(pinger):
    """cache_mem_access.prompts() answers with a list, or None for no hits. Both go
    through the same check; a list simply becomes several pings."""
    with _store() as store:
        if store is None:
            return
        store.save_prompt(SAMPLE, tags=("cover-letter",))
        store.save_prompt("Writing sample: how contract tests ended weekly rollbacks.",
                          tags=("writing-sample",))
        store.save_prompt("Find remote Python roles at small teams, no on-call.",
                          tags=("job-search",))

        fn = resolve("cache_mem_access.prompts")
        sent = check_and_ping(fn, keywords=("python",), pinger=pinger)
        _say(f"{len(sent)} pings from one search")
        empty = check_and_ping(fn, tags=("kayaking",), pinger=pinger)
        _say(f"{len(empty)} pings from a search that missed")


@demo
def not_a_prompt(pinger):
    """Everything a half-built store can hand back that is not a prompt. The index file
    is the interesting one: a big, valid, entirely truthy dict with no text in it."""
    index = {"tags": {"backend": ["a1b2c3"]}, "keywords": {"python": ["a1b2c3"]}}
    cases = [None, False, True, 0, 42, 3.14, "", "   ", {}, [], (), index,
             {"text": ""}, {"text": None}, {"id": "a1b2c3", "tags": ["backend"]},
             ["backend", "python"],  # a list of loose strings is tags, not prompts
             Prompt(text="  ")]
    for value in cases:
        sent = check_and_ping(lambda i, v=value: v, "x", pinger=pinger)
        _say(f"{'PINGED' if sent else 'quiet '}  {str(value)[:44]}")


@demo
def bare_text(pinger):
    """A bare string counts as a prompt with no metadata - the shape a store returns
    before anyone wraps it in a record. The id is recomputed by content hash."""
    sent = check_and_ping(lambda i: f"  {SAMPLE}  ", "x", pinger=pinger)
    _say(f"{len(sent)} pings, id derived as {sent[0].id}")


@demo
def object_shape(pinger):
    """Records need not be dicts. Anything with the attributes reads the same, so a store
    that grows a class later does not break this side."""
    from types import SimpleNamespace

    record = SimpleNamespace(id="deadbeef1234", text=SAMPLE, tags=["cover-letter"],
                             keywords=["python", "latency"], created=time.time())
    sent = check_and_ping(lambda i: record, "x", pinger=pinger)
    _say(f"{len(sent)} pings from an attribute-only record")


@demo
def corrupt_record(pinger):
    """Half-written json on disk. prompt_cache swallows it into None, so the phone never
    hears about a file that was being saved while this ran."""
    with _store() as store:
        if store is None:
            return
        prompt_id = store.save_prompt(SAMPLE)
        with open(os.path.join(store.DIR, prompt_id + ".json"), "w") as f:
            f.write('{"id": "' + prompt_id + '", "text": "half a rec')

        fn = resolve(TEMP_FN)
        sent = check_and_ping(fn, prompt_id, pinger=pinger)
        _say(f"{len(sent)} pings from a truncated file")


@demo
def explodes(pinger):
    """Unfinished code raises. Still 'no prompt', but the reason is worth seeing, so
    on_error hands it to the caller instead of the check swallowing it in silence."""
    def temp(prompt_id):
        raise KeyError(prompt_id)

    sent = check_and_ping(temp, "a1b2c3", pinger=pinger,
                          on_error=lambda exc: _say(f"caught {type(exc).__name__}: {exc}"))
    _say(f"{len(sent)} pings - the poller outlives its own dependency")


@demo
def deduped(pinger):
    """Ten polls, one stored prompt, one buzz. Without the shared `seen` set this is a
    phone that vibrates every thirty seconds about something already read."""
    with _store() as store:
        if store is None:
            return
        prompt_id = store.save_prompt(SAMPLE, tags=("cover-letter",))
        fn = resolve(TEMP_FN)
        seen, total = set(), 0
        for _ in range(10):
            total += len(check_and_ping(fn, prompt_id, pinger=pinger, seen=seen))
        _say(f"{total} pings across 10 polls")


@demo
def deduped_by_content(pinger):
    """Re-saving the same text lands on the same content hash, so a prompt edited into
    existence twice is still one notification."""
    box = Recording()
    seen = set()
    for text in (SAMPLE, SAMPLE.strip(), "A different prompt entirely."):
        check_and_ping(lambda i, t=text: t, "x", pinger=box, seen=seen)
    _say(f"3 returns, {len(box.sent)} pings, ids {[p.id for p in box.sent]}")


@demo
def polling(pinger):
    """watch() sitting on the store until a prompt shows up in it. The clock and sleep
    are fakes, so this costs no wall time; in production it is interval=30, time.sleep."""
    with _store() as store:
        if store is None:
            return
        prompt_id = _content_id(SAMPLE)
        fn = resolve(TEMP_FN)
        polls = []

        def tick(seconds):
            now[0] += seconds
            polls.append(seconds)
            if len(polls) == 3:  # something else finally saves it
                store.save_prompt(SAMPLE, tags=("cover-letter",))

        now = [0.0]
        sent = watch(fn, prompt_id, pinger=pinger, interval=30,
                     clock=lambda: now[0], sleep=tick)
        _say(f"{len(sent)} pings after {len(polls) + 1} polls "
             f"({now[0]:.0f}s of simulated waiting)")


@demo
def gives_up(pinger):
    """The same loop against a store that never fills. Bounded, so a caller can poll
    during one scrape pass and then get on with the scrape."""
    now = [0.0]
    sent = watch(lambda i: None, "x", pinger=pinger, interval=30, timeout=120,
                 clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    _say(f"{len(sent)} pings, gave up after {now[0]:.0f}s")


@demo
def counted(pinger):
    """Recording stands in for a phone when what you want is an assertion, not a buzz."""
    box = Recording()
    check_and_ping(lambda i: {"id": "a1b2c3", "text": SAMPLE}, "x", pinger=box)
    check_and_ping(lambda i: None, "x", pinger=box)
    _say(f"recorded {len(box.sent)} of 2 calls: {[p.id for p in box.sent]}")


@demo
def against_the_real_store(pinger):
    """The live wiring, read-only, against whatever is actually in archive_data today."""
    fn = resolve("cache_mem_access.prompts",
                 on_error=lambda exc: _say(f"unavailable ({type(exc).__name__}: {exc})"))
    if fn is None:
        _say("nothing to poll yet; this demo wakes up when the store does")
        return
    sent = check_and_ping(fn, keywords=("python", "backend"), pinger=pinger)
    _say(f"the real archive answered with {len(sent)} prompt(s)")


@demo
def transport_check(pinger):
    """Which phone the environment points at, and one prompt through it. Without --send
    that is the console pinger, so this stays on the machine."""
    configured = pinger_from_env()
    _say(f"env selects: {configured.name}   in use now: {pinger.name}")
    ping([Prompt(text="Test ping from the job automator.", id="selftest",
                 tags=("mobile_ping",))], pinger)


def main(argv):
    flags = {a for a in argv if a.startswith("-")}
    names = [a for a in argv if not a.startswith("-")]
    catalogue = "demos: " + ", ".join(d.__name__ for d in DEMOS)

    if flags & {"-h", "--help"}:
        print(__doc__)
        print(catalogue)
        return 0

    unknown = set(names) - {d.__name__ for d in DEMOS}
    if unknown:
        print(f"no such demo: {', '.join(sorted(unknown))}\n{catalogue}")
        return 1

    pinger = pinger_from_env() if "--send" in flags else Console()
    if "--send" in flags and pinger.name == "console":
        print("--send given but no transport configured; set MOBILE_PING_NTFY_TOPIC, or "
              "PUSHOVER_TOKEN with PUSHOVER_USER. Printing instead.\n")

    for d in (x for x in DEMOS if not names or x.__name__ in names):
        print(f"\n\033[1m{d.__name__}\033[0m  {d.__doc__.strip().splitlines()[0]}")
        d(pinger)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
