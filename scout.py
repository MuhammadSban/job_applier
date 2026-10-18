"""Job sources normalised to one record type, so sinks never learn where a listing came from.

Two capabilities live here. `scout()` finds listings; `questions()` opens a listing's
application form and returns every field it asks for, normalised the same way.
"""

import html as html_lib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 15


@dataclass(frozen=True)
class Query:
    """limit is per source, not overall. keywords match a title when every word appears,
    in any order; location matches any one place the board lists, on word boundaries, and
    understands a few short forms (US, UK, NYC, SF). Both are ignored when blank."""

    keywords: str
    location: str = ""
    limit: int = 25


@dataclass(frozen=True)
class Listing:
    source: str
    title: str
    company: str
    location: str
    url: str
    ref: str = ""  # source-local id ("board/job" on Greenhouse, the urn on LinkedIn)


@dataclass(frozen=True)
class Field:
    """One control. kind is normalised: text, email, tel, url, number, date, textarea,
    file, select, multiselect, radio, checkbox."""

    name: str
    kind: str = "text"
    options: tuple = ()


@dataclass(frozen=True)
class Question:
    """One thing the form asks. A question can span several fields — Greenhouse offers
    resume-as-upload and resume-as-text under a single label."""

    label: str
    fields: tuple = ()
    required: bool = False
    section: str = "application"  # application, eeoc, demographic, location
    description: str = ""

    @property
    def kind(self):
        return self.fields[0].kind if self.fields else "text"

    @property
    def options(self):
        return self.fields[0].options if self.fields else ()


class NoForm(Exception):
    """The application could not be reached from here, and why.

    Different from an empty list, which means a form was read and asks nothing. The
    difference is the whole contract with the cache: "read it, there is nothing on it" is an
    answer worth keeping, and "could not get at it" is not, because a browser-built portal
    stays unreadable while a board that was merely down will answer tomorrow."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class JobSource:
    name = "source"

    def fetch(self, query):
        raise NotImplementedError

    def questions(self, listing):
        """Default path: whatever the listing's own apply page serves as HTML."""
        return from_url(listing.url)


class Greenhouse(JobSource):
    """Official JSON API. Per-company: there is no cross-board search, so the reach of a
    search is exactly the board list below -- a name missing from it can never be found."""

    name = "greenhouse"
    API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    JOB = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"
    WORKERS = 12  # one board is one request, and they only wait on the network
    BOARDS = [
        "spacex", "databricks", "stripe", "anthropic", "datadog", "mongodb", "cloudflare",
        "elastic", "relativity", "waymo", "braze", "verkada", "samsara", "brex", "gitlab",
        "scaleai", "coinbase", "affirm", "fivetran", "flexport", "lyft", "remotecom",
        "pinterest", "figma", "reddit", "robinhood", "klaviyo", "instacart", "nuro",
        "asana", "gusto", "mixpanel", "vercel", "hightouch", "duolingo", "chime", "faire",
        "mercury", "carta", "sigmacomputing", "sofi", "twitch", "discord", "dropbox",
        "amplitude", "stockx", "attentive", "komodohealth", "webflow", "betterment",
        "alloy", "pendo", "coursera", "khanacademy", "airtable", "truebill", "iterable",
        "storyblok", "modernhealth", "lithic", "honeycomb", "contentful", "lattice",
        "udemy", "netlify",
    ]

    def __init__(self, boards=None):
        self.boards = boards or self.BOARDS

    def fetch(self, query):
        """Boards are read in parallel, then interleaved, so a 2000-job board cannot crowd
        a 20-job one out of the limit -- you get a spread of companies, not the biggest."""
        with ThreadPoolExecutor(self.WORKERS) as pool:
            per_board = list(pool.map(lambda b: self._board(b, query), self.boards))
        return _interleave([m for m in per_board if m])[:query.limit]

    def _board(self, board, query):
        try:
            r = requests.get(self.API.format(board=board), timeout=TIMEOUT)
            r.raise_for_status()
            jobs = r.json().get("jobs", [])
        except Exception:
            return []
        out = []
        for j in jobs:
            title = j.get("title", "")
            loc = (j.get("location") or {}).get("name", "")
            if _title_matches(query.keywords, title) and _place_matches(query.location, loc):
                out.append(Listing(self.name, title, j.get("company_name") or board,
                                   loc, j.get("absolute_url", ""), f"{board}/{j.get('id')}"))
        return out

    def questions(self, listing):
        """The board API returns the built form, so no browser and no HTML guessing."""
        board, job = _greenhouse_ref(listing.ref or listing.url)
        found = _greenhouse_api(board, job) if job else None
        return found if found else from_url(listing.url)


class LinkedIn(JobSource):
    """No public jobs API exists. This is the logged-out guest endpoint, which returns HTML."""

    name = "linkedin"
    API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    PAGE = 10
    DELAY = 1.0

    def fetch(self, query):
        out, seen = [], set()
        for start in range(0, query.limit + self.PAGE, self.PAGE):
            if len(out) >= query.limit:
                break
            try:
                r = requests.get(self.API, timeout=TIMEOUT, headers={"User-Agent": UA},
                                 params={"keywords": query.keywords,
                                         "location": query.location, "start": start})
                if r.status_code != 200:
                    break
            except Exception:
                break

            cards = BeautifulSoup(r.text, "html.parser").find_all("div", class_="base-card")
            if not cards:
                break
            for c in cards:
                urn = (c.get("data-entity-urn") or "").rsplit(":", 1)[-1]
                if not urn or urn in seen:
                    continue
                seen.add(urn)
                out.append(Listing(
                    self.name,
                    _text(c, "base-search-card__title"),
                    _text(c, "base-search-card__subtitle"),
                    _text(c, "job-search-card__location"),
                    f"https://www.linkedin.com/jobs/view/{urn}",
                    urn,
                ))
                if len(out) >= query.limit:
                    break
            time.sleep(self.DELAY)
        return out

    def questions(self, listing):
        """Guests never see the form: a logged-out job page carries no apply URL at all,
        only a signup link, so there is nothing to read and this says so rather than
        guessing. Sign in through session.py and the page names where the application
        really lives — usually an ATS form from_url() already knows how to read.

        Easy Apply still returns nothing: it is a modal built in JavaScript, so no apply
        URL exists to follow and there is no browser here to render it."""
        jar = _linkedin_cookies()
        if not jar:
            raise NoForm("LinkedIn shows a posting's apply link only to a signed-in "
                         "session. Use Log in to LinkedIn on the Jobs tab, then click the "
                         "listing again.")
        try:
            r = requests.get(listing.url, headers={"User-Agent": UA}, cookies=jar,
                             timeout=TIMEOUT)
            r.raise_for_status()
        except Exception as exc:
            raise NoForm(f"LinkedIn did not answer for this posting — {exc}") from exc

        apply_url = _linkedin_apply_url(r.text)
        if apply_url:
            return from_url(apply_url)
        if LINKEDIN_ONSITE.search(r.text):
            raise NoForm("Easy Apply posting: the form is a LinkedIn modal built in the "
                         "browser, so there is nothing here to read. Apply on the posting.")
        raise NoForm("This posting carries no apply link that can be followed from here. "
                     "Open it to see where it applies.")


# ── the form readers ─────────────────────────────────────────────────────────────────

CONTROLS = ("input", "textarea", "select")
SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}
SKIP_NAMES = {"utf8", "authenticity_token", "csrf_token", "_token", "g-recaptcha-response"}
HEADINGS = ["legend", "label", "h1", "h2", "h3", "h4", "h5", "h6", "strong"]
FIELD_HINTS = ("question", "field", "form-group", "form-row", "application-")
MARKERS = " *\u2731\uff0a\u2022\u00b7:"
DEMOGRAPHIC = ("gender", "race", "ethnic", "veteran", "disability", "hispanic", "eeo",
               "self-identification", "self identification")
GREENHOUSE_KINDS = {"input_text": "text", "textarea": "textarea", "input_file": "file",
                    "multi_value_single_select": "select",
                    "multi_value_multi_select": "multiselect"}
# ── where the application actually lives ─────────────────────────────────────────────
# A posting is almost never the application. On an ATS the form sits one path segment
# further on; on a company's own careers site the posting is a description with an Apply
# button, and the form is on whichever ATS that button points at. Getting this wrong is not
# harmless: a reader that gives up and parses whatever form is on the page comes back with
# the site's job search widget, and "Search by Keyword" is then the first question of your
# application.

APPLY_PATH = {"jobs.lever.co": "apply",
              "jobs.ashbyhq.com": "application",
              "apply.workable.com": "apply",
              "jobs.workable.com": "apply"}

# The ATSes whose forms this reader can parse. A link to one of these is the way through
# from a careers page, and worth preferring over anything else that merely says "apply".
ATS_HOST = re.compile(r"""(?:
      (?:job-)?boards\.greenhouse\.io | greenhouse\.io/embed
    | jobs\.lever\.co | ashbyhq\.com | \.workable\.com
    | jobs\.smartrecruiters\.com | \.breezy\.hr | \.recruitee\.com
    | \.teamtailor\.com | jobs\.jobvite\.com | \.applytojob\.com
    | \.bamboohr\.com | \.rippling\.com | \.pinpointhq\.com
)""", re.X | re.I)

# Portals that build their form in the browser, or keep it behind a portal account. There
# is no browser here and no account, so these are named rather than guessed at.
OPAQUE = (
    (re.compile(r"myworkday(jobs|site)\.com|\.wd\d+\.myworkday", re.I),
     "Workday builds its form in the browser"),
    (re.compile(r"brassring\.com|kenexa\.com", re.I),
     "BrassRing serves its form from a script"),
    (re.compile(r"\.icims\.com", re.I),
     "iCIMS keeps its form behind a portal sign-in"),
    (re.compile(r"taleo\.net|oraclecloud\.com", re.I),
     "Taleo builds its form in the browser"),
    (re.compile(r"successfactors\.(com|eu)|\.sapsf\.|career\d*\.sap", re.I),
     "SuccessFactors builds its form in the browser"),
    (re.compile(r"avature\.net", re.I), "Avature builds its form in the browser"),
    (re.compile(r"eightfold\.ai|phenompeople\.com", re.I),
     "this portal builds its form in the browser"),
)

HOPS = 2  # a careers page, then the ATS it points at. Further than that is a maze.


def questions(listing, sources=None):
    """Every question the listing's application asks, read over HTTP.

    Raises NoForm, carrying the reason, when the application cannot be reached from here: a
    JavaScript portal, a sign-in wall, a board that did not answer. An empty list is the
    other answer — a form was read and asks nothing — and the two are not interchangeable."""
    sources = sources if sources is not None else [Greenhouse(), LinkedIn()]
    for src in sources:
        if src.name == listing.source:
            return src.questions(listing)
    return from_url(listing.url)


def from_url(url, hops=HOPS):
    """Questions for any apply URL, whoever the board is.

    Walks towards the form rather than assuming it has arrived: the Greenhouse API when the
    URL names a Greenhouse job, the known apply path for an ATS that has one, and otherwise
    the page itself — and when that page turns out to be a description rather than a form,
    the apply link out of it, up to `hops` times."""
    seen, target, why = set(), _apply_url(url), ""
    for _ in range(max(hops, 0) + 1):
        if not target or target in seen:
            break
        seen.add(target)

        board, job = _greenhouse_ref(target)
        if job:
            found = _greenhouse_api(board, job)
            if found:
                return found

        stop = _opaque(target)
        if stop:
            raise NoForm(f"{stop}, so there is no form here to read. "
                         f"Open the posting to apply on the board itself.")

        markup = _get(target)
        if not markup:
            why = why or f"{urlparse(target).netloc or 'the board'} did not answer"
            break
        found = parse_form(markup)
        if found:
            return found

        why = f"{urlparse(target).netloc} serves a description, not a form"
        target = _apply_link(markup, target)
        if target:
            target = _apply_url(target)

    if why:
        pages = f"{len(seen)} page" + ("s" if len(seen) != 1 else "")
        raise NoForm(f"Followed {pages} from this listing and {why}. "
                     f"Open the posting to apply on the board itself.")
    return []


def parse_form(markup):
    """Reads a rendered form: labels, required flags, option lists, grouped radios."""
    if not markup:
        return []
    soup = BeautifulSoup(markup, "html.parser")
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()

    scope = _application_form(soup)
    if scope is None:
        return []  # a page with a search box on it is not a page with a form on it

    out, groups, seen = [], {}, set()
    for control in scope.find_all(CONTROLS):
        kind = _kind(control)
        name = (control.get("name") or control.get("id") or "").strip()
        if kind is None or name.lower() in SKIP_NAMES:
            continue

        if kind in ("radio", "checkbox") and name:
            label = _option_label(control, soup)
            if name in groups:
                q = out[groups[name]]
                out[groups[name]] = _with_option(q, label)
                continue
            groups[name] = len(out)
            heading = _group_label(control, soup, name)
            out.append(Question(heading, (Field(name, kind, (label,) if label else ()),),
                                _required(control, heading), _section(name, heading)))
            continue

        label = _label_for(control, soup) or _pretty(name)
        key = (label.lower(), name.lower(), kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(Question(_clean_label(label),
                            (Field(name, kind, _options(control)),),
                            _required(control, label), _section(name, label)))
    return out


def questions_text(qs):
    """Flat text for a prompt or a file — the shape generate_resume.py wants to read."""
    lines = []
    section = None
    for q in qs:
        if q.section != section:
            section = q.section
            lines.append(f"[{section}]")
        mark = " *" if q.required else ""
        detail = f" ({q.kind})" if q.kind != "text" else ""
        lines.append(f"- {q.label}{mark}{detail}")
        if q.options:
            lines.append("    " + " | ".join(q.options))
        if q.description:
            lines.append(f"    {q.description[:300]}")
    return "\n".join(lines)


# ── helpers ──────────────────────────────────────────────────────────────────────────

ALIASES = {
    "us": ("united states", "usa", "u.s."), "usa": ("united states", "us"),
    "uk": ("united kingdom", "england"), "nyc": ("new york",),
    "sf": ("san francisco",), "bay area": ("san francisco", "palo alto", "mountain view"),
}


def _title_matches(keywords, title):
    """Every word has to appear, in any order, so "software engineer" also reaches
    "Engineer, Software Infrastructure" -- which a whole-phrase substring test misses."""
    low = title.lower()
    return all(word in low for word in keywords.lower().split())


def _place_matches(where, location):
    """Boards pack several places into one string ("San Francisco, CA | New York City, NY"),
    so each part is tested separately, and on word boundaries: a bare substring quietly
    lets "US" match "Austin" and "IN" match "Singapore"."""
    want = where.lower().strip()
    if not want:
        return True
    parts = [p.strip() for p in re.split(r"[|•·/;,]|\s+-\s+", location.lower()) if p.strip()]
    for candidate in (want,) + ALIASES.get(want, ()):
        pattern = re.compile(rf"\b{re.escape(candidate)}\b")
        if any(pattern.search(part) for part in parts):
            return True
    return False


def _linkedin_cookies():
    """Empty unless the user has signed in. session.py is optional, and so is Selenium
    underneath it, so a missing module just means no session."""
    try:
        import session
    except ImportError:
        return {}
    return session.cookies()


# The signed-in page is a client-rendered app: the apply target is not an anchor but a
# value in an escaped JSON payload, and the key LinkedIn actually uses is offsiteApplyUrl.
# The older names are kept as fallbacks for whatever variant a given posting is served.
LINKEDIN_APPLY = tuple(re.compile(pattern) for pattern in (
    r'\\?"offsiteApplyUrl\\?"\s*:\s*\\?"((?:[^"\\]|\\[^"])*)',
    r'\\?"companyApplyUrl\\?"\s*:\s*\\?"((?:[^"\\]|\\[^"])*)',
    r'\\?"applyUrl\\?"\s*:\s*\\?"((?:[^"\\]|\\[^"])*)',
))

# applyMethod/isOnsiteApply mark Easy Apply, which has no offsite URL to follow at all.
LINKEDIN_ONSITE = re.compile(r'\\?"isOnsiteApply\\?"\s*:\s*true')


def _linkedin_apply_url(page):
    """Where a signed-in LinkedIn page says the real application lives. The value is JSON
    inside HTML, so it arrives escaped twice over. Named for its board: _apply_url below is
    a different thing entirely, the guess that a form sits one path past the posting."""
    for pattern in LINKEDIN_APPLY:
        found = pattern.search(page)
        if found:
            url = found.group(1).encode().decode("unicode_escape")
            url = html_lib.unescape(url).replace("\\/", "/")
            if url.startswith("http"):
                return url
    return ""


def _interleave(groups):
    out = []
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out


def _text(node, cls):
    el = node.find(class_=cls)
    return el.get_text(strip=True) if el else ""


def _get(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def _plain(markup):
    """Board copy arrives as HTML, sometimes escaped twice over."""
    text = html_lib.unescape(markup or "")
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def _pretty(name):
    return re.sub(r"[_\-\[\]]+", " ", name).strip().capitalize()


def _clean_label(label):
    label = re.sub(r"\s*\(\s*required\s*\)\s*$", "", _first_line(label), flags=re.I)
    return label.strip(" \t:").rstrip(MARKERS).strip() or label.strip()


def _first_line(markup):
    """Board markup packs helper text, upload widgets and error strings into the same
    label element; the question itself is the first line of it."""
    text = html_lib.unescape(markup or "")
    if "<" in text:
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" " + MARKERS)
        if line:
            return line[:300]
    return ""


def _section(name, label):
    blob = f"{name} {label}".lower()
    return "demographic" if any(w in blob for w in DEMOGRAPHIC) else "application"


# Greenhouse ------------------------------------------------------------------------

def _greenhouse_ref(text):
    """board and job id out of a ref or any Greenhouse URL shape."""
    if not text:
        return "", ""
    m = re.match(r"^([\w.-]+)/(\d+)$", text.strip())
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"greenhouse\.io/embed/job_app\?for=([\w.-]+).*?[?&]token=(\d+)", text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"greenhouse\.io/([\w.-]+)/jobs/(\d+)", text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"[?&]gh_jid=(\d+)", text)
    if m:  # board name is not in the URL; the company's own domain is the usual guess
        host = urlparse(text).netloc.lower().replace("www.", "").split(".")[0]
        return host, m.group(1)
    return "", ""


def _greenhouse_questions(data):
    out = [_greenhouse_q(q, "application") for q in data.get("questions") or []]
    out = [q for q in out if q.label]
    for block in data.get("compliance") or []:
        section = block.get("type") or "compliance"
        note = _plain(block.get("description"))
        for q in block.get("questions") or []:
            out.append(_greenhouse_q(q, section, note))
    demo = data.get("demographic_questions") or {}
    for q in demo.get("questions") or []:
        out.append(_greenhouse_q(q, "demographic", _plain(demo.get("description"))))
    for q in data.get("location_questions") or []:
        out.append(_greenhouse_q(q, "location"))
    return out


def _greenhouse_q(q, section, note=""):
    fields = []
    for f in q.get("fields") or []:
        if (f.get("type") or "").startswith("input_hidden"):
            continue
        kind = GREENHOUSE_KINDS.get(f.get("type"), f.get("type") or "text")
        options = tuple(_plain(v.get("label")) for v in f.get("values") or [])
        fields.append(Field(f.get("name", ""), kind, options))
    if not fields:  # demographic questions carry their options under another key
        options = tuple(_plain(o.get("label")) for o in q.get("answer_options") or [])
        fields.append(Field(str(q.get("id", "")), q.get("type") or "select", options))
    return Question(_clean_label(q.get("label", "")), tuple(fields), bool(q.get("required")),
                    section, _plain(q.get("description")) or note)


# HTML forms ------------------------------------------------------------------------

def _apply_url(url):
    """Most ATSes keep the form one path segment past the posting."""
    host = urlparse(url).netloc.lower()
    leaf = APPLY_PATH.get(host)
    if leaf and not url.rstrip("/").endswith(leaf):
        return url.rstrip("/") + "/" + leaf
    return url


def _opaque(url):
    """Why this host cannot be read, when it is one of the ones that cannot be."""
    for pattern, why in OPAQUE:
        if pattern.search(url or ""):
            return why
    return ""


def _greenhouse_api(board, job):
    """The board API's own copy of the form. None when the API has nothing to say — a
    stale id, a board that has been renamed, a posting that has closed."""
    try:
        r = requests.get(Greenhouse.JOB.format(board=board, job=job),
                         params={"questions": "true"}, timeout=TIMEOUT)
        r.raise_for_status()
        return _greenhouse_questions(r.json())
    except Exception:
        return None


APPLY_HINT = re.compile(r"/apply|/application|apply_?now|[?&](mode|action|step)=apply", re.I)
LINK_REJECT = re.compile(r"signup|sign-up|/login|signin|sign-in|/uas/|checkpoint"
                         r"|/search|privacy|cookie|/terms|mailto:|javascript:", re.I)


def _apply_link(markup, base):
    """The way through from a job description to the form itself.

    Scored rather than first-match: a careers page carries a dozen links with "apply"
    somewhere in them — job alerts, share buttons, other postings — and the one that
    matters usually points at an ATS on another host entirely."""
    best, best_score = None, 0
    for a in BeautifulSoup(markup, "html.parser").find_all("a", href=True):
        href = urljoin(base, (a["href"] or "").strip())
        if not href.lower().startswith("http") or LINK_REJECT.search(href):
            continue
        if href.rstrip("/") == base.rstrip("/"):
            continue
        text = a.get_text(" ", strip=True).lower()[:60]
        score = 6 if ATS_HOST.search(href) else 0
        if APPLY_HINT.search(href):
            score += 3
        if text.startswith("apply"):
            score += 3
        elif "apply" in text:
            score += 1
        if score > best_score:
            best, best_score = href, score
    return best if best_score >= 3 else None


# ── telling an application apart from a search box ───────────────────────────────────
# Both are a form with text inputs in it. These are the words that separate them: what a
# field calls itself, what it asks you to type into it, and what its label says.

SEARCH_WORDS = ("search", "keyword", "query", "distance", "radius", "sort", "filter",
                "department", "function", "category", "postal", "zipcode", "within",
                "relevance", "newsletter", "subscribe", "alert", "job title or")
APPLY_WORDS = ("resume", "cv", "cover", "first name", "last name", "full name", "surname",
               "given name", "email", "phone", "linkedin", "github", "portfolio",
               "website", "salary", "authoriz", "authoris", "sponsor", "visa", "notice",
               "start date", "gender", "race", "ethnic", "veteran", "disability",
               "pronoun", "referral", "hear about", "why ", "experience", "graduat")
FORM_REJECT = re.compile(r"search|login|log-in|signin|sign-in|subscribe|newsletter"
                         r"|cookie|consent|locale|language", re.I)
APPLICATION = 5  # what a form has to score before it is believed to be an application


def _blob(control):
    """Everything a control says about itself, plus whatever is written next to it."""
    said = " ".join(control.get(attr) or "" for attr in
                    ("name", "id", "placeholder", "aria-label", "autocomplete"))
    label = control.find_parent("label") or control.parent
    near = label.get_text(" ", strip=True)[:120] if label is not None else ""
    return f"{said} {near}".lower()


def _form_score(form):
    """How much this form looks like a job application rather than a search box.

    A file upload is the strongest signal there is — nothing but an application asks you to
    attach a document — and a password field is proof it is a sign-in, whatever else it
    holds. Everything else is weighed: the words a field uses are what it is for."""
    controls = [c for c in form.find_all(CONTROLS) if _kind(c)]
    if len(controls) < 2:
        return 0
    if form.name == "form":
        if (form.get("role") or "").lower() == "search":
            return 0
        own = " ".join([form.get("action") or "", form.get("id") or "",
                        " ".join(form.get("class") or [])])
        if FORM_REJECT.search(own):
            return 0

    score = 0
    for control in controls:
        if (control.get("type") or "").lower() == "password":
            return 0
        kind, blob = _kind(control), _blob(control)
        if kind == "file":
            score += 6
        elif kind in ("email", "tel"):
            score += 2
        elif kind == "textarea":
            score += 1
        if any(word in blob for word in SEARCH_WORDS):
            score -= 4
        elif any(word in blob for word in APPLY_WORDS):
            score += 2
    return max(score, 0)


def _application_form(soup):
    """The form on this page that is an application, or None if there isn't one.

    A board ships a search widget, a sign-in box and a newsletter signup alongside the
    posting, and the application is not reliably the biggest of them — on a company careers
    site the search widget usually is, which is how a reader that takes the largest form
    ends up offering "Search by Keyword" as question one of a job application."""
    forms = [f for f in soup.find_all("form") if f.find_all(CONTROLS)]
    best, score = None, 0
    for form in forms or [soup]:  # some boards ship controls with no <form> around them
        value = _form_score(form)
        if value > score:
            best, score = form, value
    return best if score >= APPLICATION else None


def _kind(control):
    if control.name == "textarea":
        return "textarea"
    if control.name == "select":
        return "multiselect" if control.has_attr("multiple") else "select"
    kind = (control.get("type") or "text").lower()
    if kind in SKIP_TYPES or kind == "search":
        return None
    return {"file": "file", "email": "email", "tel": "tel", "url": "url",
            "number": "number", "date": "date", "radio": "radio",
            "checkbox": "checkbox"}.get(kind, "text")


def _options(control):
    if control.name != "select":
        return ()
    return tuple(o.get_text(" ", strip=True) for o in control.find_all("option")
                 if o.get("value") not in (None, "") and o.get_text(strip=True))


def _container(control, span=1):
    """The card a board wraps one question in — Lever's li.application-question,
    Greenhouse's div.field, a plain fieldset. The outermost such card wins, since inner
    ones hold only the control; span allows the "other, please specify" box that often
    rides along with a choice, and stops the walk before the next question."""
    found = None
    for parent in list(control.parents)[:7]:
        if parent.name in ("form", "body", "html"):
            break
        if parent.name == "label":
            continue
        classes = " ".join(parent.get("class") or []).lower()
        if parent.name not in ("fieldset", "li") and not any(h in classes for h in FIELD_HINTS):
            continue
        if len({c.get("name") for c in parent.find_all(CONTROLS)}) <= span:
            found = parent
    return found


def _label_for(control, soup):
    cid = control.get("id")
    if cid:
        tag = soup.find("label", attrs={"for": cid})
        if tag and tag.get_text(strip=True):
            return tag.get_text("\n", strip=True)
    parent = control.find_parent("label")
    if parent and parent.get_text(strip=True):
        return parent.get_text("\n", strip=True)
    box = _container(control) or _container(control, span=2)
    if box and box.get_text(strip=True):
        return box.get_text("\n", strip=True)
    for attr in ("aria-label", "title", "placeholder"):
        if control.get(attr):
            return control[attr]
    ref = (control.get("aria-labelledby") or "").split()
    if ref:
        tag = soup.find(id=ref[0])
        if tag:
            return tag.get_text("\n", strip=True)
    near = control.find_previous(HEADINGS)
    text = near.get_text(" ", strip=True) if near else ""
    return text if 0 < len(text) <= 200 else ""


def _option_label(control, soup):
    cid = control.get("id")
    if cid:
        tag = soup.find("label", attrs={"for": cid})
        if tag and tag.get_text(strip=True):
            return _clean_label(tag.get_text(" ", strip=True))
    parent = control.find_parent("label")
    if parent and parent.get_text(strip=True):
        return _clean_label(parent.get_text(" ", strip=True))
    return _clean_label(control.get("aria-label") or control.get("value") or "")


def _group_label(control, soup, name):
    """Radios and checkboxes share one question; their own labels are the options, so the
    question comes from the card that wraps the whole set."""
    box = control.find_parent("fieldset")
    legend = box.find("legend") if box else None
    if legend and legend.get_text(strip=True):
        return _clean_label(legend.get_text("\n", strip=True))
    for parent in list(control.parents)[:4]:
        if parent.get("role") == "group" and parent.get("aria-label"):
            return _clean_label(parent["aria-label"])
    option = _option_label(control, soup).lower()
    for span in (1, 2):  # a card that holds nothing but the options is not the question
        box = _container(control, span)
        text = _clean_label(box.get_text("\n", strip=True)) if box else ""
        if text and text.lower() != option:
            return text
    near = control.find_previous(["legend", "h1", "h2", "h3", "h4", "h5", "h6", "strong"])
    text = near.get_text(" ", strip=True) if near else ""
    return _clean_label(text) if 0 < len(text) <= 200 else _pretty(name)


def _with_option(q, label):
    if not label or label in q.fields[0].options:
        return q
    grown = Field(q.fields[0].name, q.fields[0].kind, q.fields[0].options + (label,))
    return Question(q.label, (grown,), q.required, q.section, q.description)


def _required(control, label):
    if control.has_attr("required") or control.get("aria-required") == "true":
        return True
    classes = list(control.get("class") or [])
    for parent in list(control.parents)[:3]:
        classes += list(parent.get("class") or [])
    if any("required" in c.lower() and "not-required" not in c.lower() for c in classes):
        return True
    return _plain(label).rstrip().endswith("*")


# ── sinks ────────────────────────────────────────────────────────────────────────────

def scout(query, sources=None):
    sources = sources if sources is not None else [Greenhouse(), LinkedIn()]
    out, seen = [], set()
    for src in sources:
        try:
            found = src.fetch(query)
        except Exception:
            continue
        for listing in found:
            if listing.url in seen:
                continue
            seen.add(listing.url)
            out.append(listing)
    return out


def write_text(listings, path="listings.txt"):
    """Temporary sink. The GUI sink replaces only this function."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{len(listings)} listings\n\n")
        for x in listings:
            f.write(f"{x.title}\n  {x.company} - {x.location}\n  {x.url}\n  [{x.source}]\n\n")
    return path


if __name__ == "__main__":
    results = scout(Query("software engineer", limit=100))
    print(f"{len(results)} listings -> {write_text(results)}")
    for job in results:
        found = questions(job)
        if found:
            print(f"\n{job.title} — {job.company}\n{job.url}\n{questions_text(found)}")
            break
