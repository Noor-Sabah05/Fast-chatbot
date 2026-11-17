"""Scrape the registered official pages and save dated snapshots (Week 3).

For each page in ingestion/sources.json:
  fetch (approved hosts only, robots.txt respected, 2 s between requests)
  -> save the raw HTML snapshot with its date and hash
  -> clean it into sections tagged with campus + programme
  -> create a new revision only if the cleaned text changed
  -> activate it if it passes the checks (else leave it staged for review)

Usage:
    python ingestion/scrape_web.py                      # all sources
    python ingestion/scrape_web.py nu_fee_structure     # just one
    python ingestion/scrape_web.py --from-file nu_admissions_faq saved_page.html
                                                        # use an HTML file you saved yourself
    python ingestion/scrape_web.py --show nu_fee_structure   # print the active sections
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from web_fetch import FetchError, get_source, load_registry, safe_get  # noqa: E402
from web_store import OUT_DIR, export_active, ingest_html, now_iso, read_json, read_jsonl  # noqa: E402


def scrape(source_ids=None) -> list[dict]:
    reg = load_registry()
    sources = [get_source(s, reg) for s in source_ids] if source_ids else reg["sources"]
    reports = []
    for src in sources:
        try:
            r = safe_get(src["url"], reg["allowed_hosts"])
            report = ingest_html(src, r.content, r.url, now_iso())
        except (FetchError, ValueError) as e:
            report = {"source_id": src["source_id"], "status": "failed", "error": str(e)}
        reports.append(report)
        print_report(report)
    export_active()
    return reports


def print_report(r: dict):
    extra = r.get("error") or "; ".join(r.get("problems", [])) or \
        f"{r.get('n_sections')} sections, page updated {r.get('page_updated') or 'n/a'}"
    print(f"{r['source_id']:<28}{r['status']:<14}{extra}")


def show(source_id: str):
    active = read_json(OUT_DIR / source_id / "ACTIVE.json")
    if not active:
        print("no active revision yet")
        return
    rev = active["revision_id"].split(":")[1]
    print(f"# {source_id}  revision {rev}  last verified {active['last_verified']}\n")
    for s in read_jsonl(OUT_DIR / source_id / "revisions" / rev / "sections.jsonl"):
        print(f"[{' > '.join(s['heading_path']) or '(top)'}]  campus={s['campus']} programme={s['programme']}")
        print(s["text"][:600] + ("..." if len(s["text"]) > 600 else ""), "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source_ids", nargs="*")
    ap.add_argument("--from-file", nargs=2, metavar=("SOURCE_ID", "HTML_FILE"))
    ap.add_argument("--show", metavar="SOURCE_ID")
    args = ap.parse_args()
    if args.show:
        show(args.show)
        return
    if args.from_file:
        sid, path = args.from_file
        src = get_source(sid)
        print_report(ingest_html(src, Path(path).read_bytes(), src["url"], now_iso()))
        export_active()
        return
    reports = scrape(args.source_ids or None)
    if any(r["status"] in ("failed", "needs_review") for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()
