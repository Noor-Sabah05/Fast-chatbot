"""Check that running ingestion twice does not create duplicate chunks (Week 4).

Checks:
  1. Web: re-ingest every stored raw snapshot twice into a temporary folder.
     The second run must report "unchanged", create no new revision, and the
     chunk IDs must be identical.
  2. Web: the active chunk set has no repeated chunk_id and no repeated
     (source_id, text) pair, and each source has exactly one active revision.
  3. PDF: rebuild the chunks from pages.jsonl twice; chunk IDs must be
     identical, and no chunk file contains a repeated chunk_id.
     (Needs tiktoken's cl100k_base, i.e. run it on your machine.)

Usage:
    python ingestion/check_duplicates.py
Exit code 1 on any failure.
"""
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_fetch import load_registry  # noqa: E402
from web_store import OUT_DIR, RAW_DIR, ROOT, ingest_html, read_json, read_jsonl  # noqa: E402

failures = []
checks_run = 0


def check(ok: bool, label: str):
    global checks_run
    checks_run += 1
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def web_reingest():
    reg = load_registry()
    for src in reg["sources"]:
        snaps = sorted((RAW_DIR / src["source_id"]).glob("*.html"))
        if not snaps:
            print(f"skip  {src['source_id']}: no snapshot yet (run scrape_web.py)")
            continue
        html = snaps[-1].read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out, raw = Path(tmp) / "out", Path(tmp) / "raw"
            r1 = ingest_html(src, html, src["url"], "2026-01-01T00:00:00+00:00", out, raw)
            ids1 = [c["chunk_id"] for c in _chunks(out, src["source_id"])]
            r2 = ingest_html(src, html, src["url"], "2026-01-08T00:00:00+00:00", out, raw)
            ids2 = [c["chunk_id"] for c in _chunks(out, src["source_id"])]
            n_revs = len(list((out / src["source_id"] / "revisions").glob("*")))
            check(r2["status"] == "unchanged" and n_revs == 1 and ids1 == ids2,
                  f"{src['source_id']}: second ingest unchanged, 1 revision, {len(ids1)} identical chunk IDs")


def _chunks(out_dir: Path, source_id: str):
    active = read_json(out_dir / source_id / "ACTIVE.json")
    if not active:
        return []
    return read_jsonl(out_dir / source_id / "revisions" / active["revision_id"].split(":")[1] / "chunks.jsonl")


def web_active_set():
    rows = []
    for active_path in OUT_DIR.glob("*/ACTIVE.json"):
        sid = active_path.parent.name
        n_active = sum(read_json(p)["status"] == "active" for p in active_path.parent.glob("revisions/*/revision.json"))
        check(n_active == 1, f"{sid}: exactly one active revision (found {n_active})")
        rows.extend(_chunks(OUT_DIR, sid))
    if not rows:
        print("skip  no active web chunks yet")
        return
    dup_ids = [k for k, v in Counter(r["chunk_id"] for r in rows).items() if v > 1]
    # same text under a DIFFERENT heading is legitimate (pages repeat notes); same heading + text is a duplicate
    dup_text = [k for k, v in Counter((r["source_id"], r["retrieval_text"]) for r in rows).items() if v > 1]
    for sid, text in dup_text:
        print(f"      repeated in {sid}: {text[:120]!r}")
    check(not dup_ids, f"active web chunks: no repeated chunk_id ({len(rows)} chunks)")
    check(not dup_text, f"active web chunks: no repeated (source, heading + text) ({len(dup_text)} found)")


def pdf_rebuild():
    pages_files = sorted((ROOT / "data" / "processed").glob("*/pages.jsonl"))
    if not pages_files:
        print("skip  no PDF pages.jsonl found")
        return
    try:
        import chunk as pdf_chunk
    except Exception as e:                      # tiktoken encoding not downloadable, etc.
        print(f"skip  PDF rebuild check: {e}")
        return
    for pages_path in pages_files:
        doc, pages, starts = pdf_chunk.load_document(pages_path, include_front_matter=False)
        headings = pdf_chunk.find_headings(doc)
        for name, (size, overlap) in pdf_chunk.EXPERIMENTS.items():
            def ids():
                spans = (pdf_chunk.structural_spans(doc, headings, size, overlap) if name.startswith("S")
                         else pdf_chunk.recursive_spans(doc, size, overlap))
                return [c["chunk_id"] for c in pdf_chunk.build_chunks(name, spans, doc, pages, starts, headings)]
            a, b = ids(), ids()
            stored = [c["chunk_id"] for c in read_jsonl(pages_path.parent / "chunks" / f"{name}.jsonl")]
            check(a == b and len(set(a)) == len(a),
                  f"{pages_path.parent.name} {name}: rebuild gives the same {len(a)} unique IDs")
            if stored:
                check(len(set(stored)) == len(stored), f"{pages_path.parent.name} {name}: stored file has no repeated IDs")


if __name__ == "__main__":
    print("== web: re-ingest the same snapshot twice")
    web_reingest()
    print("\n== web: active chunk set")
    web_active_set()
    print("\n== PDF: rebuild chunks twice")
    pdf_rebuild()
    if not checks_run:
        print("\nNOTHING CHECKED - run scrape_web.py / chunk.py first")
        sys.exit(2)
    print(f"\n{f'ALL {checks_run} PASSED' if not failures else f'{len(failures)} of {checks_run} FAILED'}")
    sys.exit(1 if failures else 0)
