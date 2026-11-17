"""Polite, locked-down HTTP fetching for registered official pages.

- Only URLs on the approved hosts in sources.json are fetched.
- Redirects are followed by hand (max 3) and every hop must stay on an approved host.
- robots.txt is respected; requests are spaced out (DELAY_SECONDS).
Used by scrape_web.py, refresh.py and the check_official_page tool.
"""
import json
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

REGISTRY_PATH = Path(__file__).with_name("sources.json")
USER_AGENT = "FAST-Student-Assistant/0.1 (university student project; low-rate, weekly refresh)"
TIMEOUT = 20
MAX_REDIRECTS = 3
DELAY_SECONDS = 2.0

_last_request = 0.0
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


class FetchError(Exception):
    pass


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    reg = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [s["source_id"] for s in reg["sources"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source_id in sources.json")
    for s in reg["sources"]:
        if urlparse(s["url"]).hostname not in reg["allowed_hosts"]:
            raise ValueError(f"{s['source_id']}: host not in allowed_hosts")
    return reg


def get_source(source_id: str, registry: dict | None = None) -> dict:
    registry = registry or load_registry()
    for s in registry["sources"]:
        if s["source_id"] == source_id:
            return s
    raise KeyError(f"unknown source_id {source_id!r} (only registered sources can be fetched)")


def _robots_ok(url: str, session: requests.Session) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=TIMEOUT, allow_redirects=False)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
                _robots[base] = rp
            else:
                _robots[base] = None          # no robots.txt -> allowed
        except requests.RequestException:
            _robots[base] = None
    rp = _robots[base]
    return rp is None or rp.can_fetch(USER_AGENT, url)


def _wait():
    global _last_request
    gap = time.monotonic() - _last_request
    if gap < DELAY_SECONDS:
        time.sleep(DELAY_SECONDS - gap)
    _last_request = time.monotonic()


def safe_get(url: str, allowed_hosts: list[str], session: requests.Session | None = None) -> requests.Response:
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    for _ in range(MAX_REDIRECTS + 1):
        host = urlparse(url).hostname
        if urlparse(url).scheme != "https" or host not in allowed_hosts:
            raise FetchError(f"refusing to fetch {url}: host not approved")
        if not _robots_ok(url, session):
            raise FetchError(f"robots.txt disallows {url}")
        _wait()
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as e:
            raise FetchError(f"request failed: {e}") from e
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            continue
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code} for {url}")
        r.encoding = r.encoding or r.apparent_encoding
        return r
    raise FetchError(f"too many redirects for {url}")
