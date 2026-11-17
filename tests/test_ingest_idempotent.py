import web_store
from conftest import fixture_html
from web_fetch import get_source
from web_store import export_active, ingest_html, read_json, read_jsonl


def active_chunks(out, sid):
    a = read_json(out / sid / "ACTIVE.json")
    return read_jsonl(out / sid / "revisions" / a["revision_id"].split(":")[1] / "chunks.jsonl")


def test_same_page_twice_creates_no_new_revision_or_chunks(tmp_path):
    src = get_source("nu_admissions_faq")
    html = fixture_html("faq").encode()
    out, raw = tmp_path / "out", tmp_path / "raw"
    r1 = ingest_html(src, html, src["url"], "2026-11-10T00:00:00+00:00", out, raw)
    ids1 = [c["chunk_id"] for c in active_chunks(out, src["source_id"])]
    # raw HTML differs (extra whitespace/script) but cleaned text is the same
    r2 = ingest_html(src, html.replace(b"<script>", b"<script> var y=2;"), src["url"],
                     "2026-11-17T00:00:00+00:00", out, raw)
    assert r1["status"] == "new" and r2["status"] == "unchanged"
    assert len(list((out / src["source_id"] / "revisions").iterdir())) == 1
    assert ids1 == [c["chunk_id"] for c in active_chunks(out, src["source_id"])]
    assert read_json(out / src["source_id"] / "ACTIVE.json")["last_verified"].startswith("2026-11-17")
    assert len(list((raw / src["source_id"]).glob("*.html"))) == 2      # both dated snapshots kept


def test_changed_page_replaces_and_retires_old_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "OUT_DIR", tmp_path / "out")
    src = get_source("nu_admissions_schedule")
    out, raw = tmp_path / "out", tmp_path / "raw"
    html = fixture_html("schedule")
    ingest_html(src, html.encode(), src["url"], "2026-11-10T00:00:00+00:00", out, raw)
    r = ingest_html(src, html.replace("Aug 17 (Mon)", "Aug 24 (Mon)").encode(), src["url"],
                    "2026-11-17T00:00:00+00:00", out, raw)
    assert r["status"] == "changed"
    statuses = sorted(read_json(p)["status"] for p in (out / src["source_id"]).glob("revisions/*/revision.json"))
    assert statuses == ["active", "retired"]
    rows = read_jsonl(export_active(out))
    assert any("Aug 24" in c["text"] for c in rows) and not any("Aug 17" in c["text"] for c in rows)
    assert len({c["chunk_id"] for c in rows}) == len(rows)


def test_broken_extraction_is_not_activated(tmp_path):
    src = get_source("nu_fee_structure")
    out, raw = tmp_path / "out", tmp_path / "raw"
    good = "<div class='main-content'><h2>Admission Fee</h2><p>" + "Admission fee is Rs 30,000. " * 20 + "</p></div>"
    ingest_html(src, good.encode(), src["url"], "2026-11-10T00:00:00+00:00", out, raw)
    r = ingest_html(src, b"<div class='main-content'><p>Maintenance</p></div>", src["url"],
                    "2026-11-17T00:00:00+00:00", out, raw)
    assert r["status"] == "needs_review"
    assert "30,000" in active_chunks(out, src["source_id"])[0]["text"]   # old one still live


def test_chunks_carry_scope_and_dates(tmp_path):
    src = get_source("nu_programmes_offered")
    out, raw = tmp_path / "out", tmp_path / "raw"
    ingest_html(src, fixture_html("programmes").encode(), src["url"], "2026-11-17T00:00:00+00:00", out, raw)
    c = [c for c in active_chunks(out, src["source_id"]) if c["section_path"] == ["Graduate Programs"]][0]
    assert c["programme"] == ["graduate"] and "Lahore" in c["campus"]
    assert c["retrieval_text"].startswith("Programs Offered > Graduate Programs\n")
    assert c["fetched_at"].startswith("2026-11-17") and c["url"] == src["url"]
