import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingestion"))
sys.path.insert(0, str(ROOT / "rag"))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No tokenizer download and no waiting between requests in tests."""
    import web_fetch
    import web_store
    monkeypatch.setattr(web_store, "n_tokens", lambda t: len(t.split()))
    monkeypatch.setattr(web_fetch, "DELAY_SECONDS", 0)
    web_fetch._robots.clear()


def fixture_html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
