"""Job sources normalised to one record type, so sinks never learn where a listing came from."""

import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 15


@dataclass(frozen=True)
class Query:
    """limit is per source, not overall. location is a literal substring match on the
    board's own location string, so prefer "" or a city over a country name."""

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


class JobSource:
    name = "source"

    def fetch(self, query):
        raise NotImplementedError


class Greenhouse(JobSource):
    """Official JSON API. Per-company: there is no cross-board search, so you name the boards."""

    name = "greenhouse"
    API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    BOARDS = ["stripe", "figma", "anthropic", "databricks"]

    def __init__(self, boards=None):
        self.boards = boards or self.BOARDS

    def fetch(self, query):
        needle = query.keywords.lower()
        where = query.location.lower()
        per_board = []
        for board in self.boards:
            try:
                r = requests.get(self.API.format(board=board), timeout=TIMEOUT)
                r.raise_for_status()
                jobs = r.json().get("jobs", [])
            except Exception:
                continue
            matches = []
            for j in jobs:
                title = j.get("title", "")
                loc = (j.get("location") or {}).get("name", "")
                if needle and needle not in title.lower():
                    continue
                if where and where not in loc.lower():
                    continue
                matches.append(Listing(self.name, title, j.get("company_name") or board,
                                       loc, j.get("absolute_url", "")))
            per_board.append(matches)
        return _interleave(per_board)[:query.limit]


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
                ))
                if len(out) >= query.limit:
                    break
            time.sleep(self.DELAY)
        return out


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
