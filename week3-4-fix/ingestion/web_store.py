"""Save web snapshots, build revisions/chunks, and stage -> activate them.

Layout (all under data/):
  raw/web/<source_id>/<YYYY-MM-DD>_<rawhash>.html        exact bytes we fetched (never edited)
  raw/web/<source_id>/<YYYY-MM-DD>_<rawhash>.meta.json   url, final url, fetched_at, hashes
  processed/web/<source_id>/revisions/<clean_hash>/
        revision.json   source + dates + extractor + status (staged/active/retired/rejected)
        sections.jsonl  one record per section, tagged with campus/programme
        chunks.jsonl    what gets embedded (same fields as the PDF chunks + scope)
  processed/web/<source_id>/ACTIVE.json                  which revision is live + last_verified
  processed/web/active_chunks.jsonl                      all active web chunks (for embedding)

A revision is identified by the hash of the CLEANED text, so fetching an
unchanged page again (even if ads/timestamps in the raw HTML changed) creates
no new revision and no new chunks - it only updates last_verified.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from web_extract import extract, sha

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "web"
OUT_DIR = ROOT / "data" / "processed" / "web"
CHUNK_SIZE, CHUNK_OVERLAP, ENCODING = 500, 75, "cl100k_base"
CONFIG = f"W500:{ENCODING}:{CHUNK_SIZE}/{CHUNK_OVERLAP}"
MIN_CHARS = 150          # a page with less clean text than this is treated as a failed extraction
MAX_SHRINK = 0.5         # new revision < 50% of the active one's size -> stage only, needs review

_enc = None


def n_tokens(text: str) -> int:
    global _enc
    if _enc is None:
        import tiktoken
        _enc = tiktoken.get_encoding(ENCODING)
    return len(_enc.encode(text))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- raw snapshot ----------

def save_snapshot(source: dict, html: bytes, final_url: str, fetched_at: str, raw_dir: Path = RAW_DIR) -> dict:
    raw_hash = hashlib.sha256(html).hexdigest()[:16]
    d = raw_dir / source["source_id"]
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{fetched_at[:10]}_{raw_hash}"
    html_path = d / f"{stem}.html"
    if not html_path.exists():
        html_path.write_bytes(html)
    meta = {"source_id": source["source_id"], "url": source["url"], "final_url": final_url,
            "fetched_at": fetched_at, "raw_hash": raw_hash, "raw_file": str(html_path.relative_to(ROOT))
            if html_path.is_relative_to(ROOT) else str(html_path)}
    write_json(d / f"{stem}.meta.json", meta)
    return meta


# ---------- chunks ----------

def _split(text: str) -> list[str]:
    if n_tokens(text) <= CHUNK_SIZE:
        return [text]
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    sp = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""])
    return sp.split_text(text)


def build_chunks(source: dict, revision: dict, sections: list[dict]) -> list[dict]:
    chunks, ordinal, seen = [], 0, set()
    for s in sections:
        for part in _split(s["text"]):
            path = [source["title"], *s["heading_path"]]
            retrieval_text = " > ".join(p for p in path if p) + "\n" + part
            if retrieval_text in seen:          # the page repeats an identical block: index it once
                continue
            seen.add(retrieval_text)
            chunk_id = hashlib.sha256(
                f"{revision['revision_id']}|{CONFIG}|{ordinal}|{part}".encode()).hexdigest()[:20]
            chunks.append({
                "chunk_id": chunk_id,
                "content_hash": sha(part),
                "experiment": "W500",
                "config": CONFIG,
                "source_id": source["source_id"],
                "revision_id": revision["revision_id"],
                "title": source["title"],
                "url": source["url"],
                "kind": source["kind"],
                "ordinal": ordinal,
                "section_index": s["section_index"],
                "section_path": s["heading_path"],
                "campus": s["campus"],
                "programme": s["programme"],
                "fetched_at": revision["fetched_at"],
                "page_updated": revision["page_updated"],
                "time_sensitive": source.get("time_sensitive", False),
                "text": part,
                "retrieval_text": retrieval_text,
                "n_tokens_retrieval": n_tokens(retrieval_text),
            })
            ordinal += 1
    return chunks


# ---------- ingest one fetched page ----------

def ingest_html(source: dict, html: bytes, final_url: str, fetched_at: str | None = None,
                out_dir: Path = OUT_DIR, raw_dir: Path = RAW_DIR) -> dict:
    """Snapshot + extract + (maybe) new revision + activate. Returns a small report."""
    fetched_at = fetched_at or now_iso()
    meta = save_snapshot(source, html, final_url, fetched_at, raw_dir)
    result = extract(html.decode("utf-8", errors="replace"), source)
    src_dir = out_dir / source["source_id"]
    active = read_json(src_dir / "ACTIVE.json")
    rev_id = f"{source['source_id']}:{result['clean_hash']}"
    rev_dir = src_dir / "revisions" / result["clean_hash"]

    report = {"source_id": source["source_id"], "revision_id": rev_id, "fetched_at": fetched_at,
              "page_updated": result["page_updated"], "n_sections": len(result["sections"])}

    if active and active["revision_id"] == rev_id:            # unchanged page
        active["last_verified"] = fetched_at
        active["last_raw_hash"] = meta["raw_hash"]
        write_json(src_dir / "ACTIVE.json", active)
        return {**report, "status": "unchanged"}

    problems = []
    if not result["sections"] or len(result["clean_text"]) < MIN_CHARS:
        problems.append(f"only {len(result['clean_text'])} characters extracted")
    if active:
        old = read_json(out_dir / source["source_id"] / "revisions" / active["revision_id"].split(":")[1] / "revision.json")
        if old and len(result["clean_text"]) < MAX_SHRINK * old["clean_chars"]:
            problems.append(f"text shrank from {old['clean_chars']} to {len(result['clean_text'])} characters")

    existing = read_json(rev_dir / "revision.json")
    revision = existing or {
        "revision_id": rev_id, "source_id": source["source_id"], "url": source["url"],
        "title": source["title"], "clean_hash": result["clean_hash"], "raw_hash": meta["raw_hash"],
        "raw_file": meta["raw_file"], "extractor": result["extractor"], "fetched_at": fetched_at,
        "page_updated": result["page_updated"], "page_campus": result["page_campus"],
        "clean_chars": len(result["clean_text"]), "n_sections": len(result["sections"]),
        "status": "staged",
    }
    if not existing:
        sections = [{**s, "source_id": source["source_id"], "revision_id": rev_id} for s in result["sections"]]
        _write_jsonl(rev_dir / "sections.jsonl", sections)
        _write_jsonl(rev_dir / "chunks.jsonl", build_chunks(source, revision, result["sections"]))
        (rev_dir / "clean.md").write_text(result["clean_text"], encoding="utf-8")

    if problems:
        revision["status"] = "rejected"
        revision["problems"] = problems
        write_json(rev_dir / "revision.json", revision)
        return {**report, "status": "needs_review", "problems": problems}

    # activate: retire the old one, point ACTIVE at the new one
    if active:
        old_path = src_dir / "revisions" / active["revision_id"].split(":")[1] / "revision.json"
        old = read_json(old_path)
        if old:
            old["status"] = "retired"
            old["retired_at"] = fetched_at
            write_json(old_path, old)
    revision["status"] = "active"
    revision.pop("problems", None)
    revision["activated_at"] = fetched_at
    write_json(rev_dir / "revision.json", revision)
    write_json(src_dir / "ACTIVE.json", {"revision_id": rev_id, "activated_at": fetched_at,
                                         "last_verified": fetched_at, "page_updated": result["page_updated"],
                                         "last_raw_hash": meta["raw_hash"]})
    return {**report, "status": "changed" if active else "new"}


def export_active(out_dir: Path = OUT_DIR) -> Path:
    """Write every active web chunk into one file for the embedding step."""
    rows = []
    for active_path in sorted(out_dir.glob("*/ACTIVE.json")):
        active = read_json(active_path)
        rev_dir = active_path.parent / "revisions" / active["revision_id"].split(":")[1]
        rows.extend(read_jsonl(rev_dir / "chunks.jsonl"))
    path = out_dir / "active_chunks.jsonl"
    _write_jsonl(path, rows)
    return path
