import pytest
import requests

import tools
from conftest import fixture_html
from web_fetch import FetchError, get_source, load_registry, safe_get
from web_store import ingest_html


class FakeResp:
    def __init__(self, status=200, text="", headers=None, url=""):
        self.status_code, self.text, self.headers, self.url = status, text, headers or {}, url
        self.content, self.encoding, self.apparent_encoding = text.encode(), "utf-8", "utf-8"
        self.is_redirect = status in (301, 302, 303, 307, 308)


class FakeSession:
    def __init__(self, pages):
        self.pages, self.headers, self.calls = pages, {}, []

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(404)
        if url not in self.pages:
            raise requests.ConnectionError("offline")
        return self.pages[url]


def test_redirect_to_unapproved_host_is_refused():
    s = FakeSession({"https://www.nu.edu.pk/Admissions/FAQ":
                     FakeResp(302, headers={"Location": "https://evil.example.com/x"})})
    with pytest.raises(FetchError, match="not approved"):
        safe_get("https://www.nu.edu.pk/Admissions/FAQ", load_registry()["allowed_hosts"], s)


def test_unregistered_source_is_rejected():
    assert tools.check_official_page("https://example.com")["status"] == "unknown_source"


def test_current_changed_and_failed(tmp_path):
    src = get_source("nu_admissions_schedule")
    html = fixture_html("schedule")
    ingest_html(src, html.encode(), src["url"], "2026-11-10T00:00:00+00:00", tmp_path, tmp_path / "raw")

    ok = tools.check_official_page(src["source_id"], tmp_path, FakeSession({src["url"]: FakeResp(text=html)}))
    assert ok["status"] == "current" and ok["page_updated"] == "2026-11-17"

    changed = html.replace("Jul 22 (Wed)", "Jul 29 (Wed)")
    r = tools.check_official_page(src["source_id"], tmp_path, FakeSession({src["url"]: FakeResp(text=changed)}))
    assert r["status"] == "changed" and "Jul 29" in r["sections"][0]["text"]

    down = tools.check_official_page(src["source_id"], tmp_path, FakeSession({}))
    assert down["status"] == "failed" and down["last_verified"].startswith("2026-11-10")
    assert "do not present it as current" in down["message"] and down["sections"]
