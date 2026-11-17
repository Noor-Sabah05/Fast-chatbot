"""Weekly refresh (run by hand once a week during the pilot).

  1. Re-scrape every registered official page.
       unchanged cleaned text -> nothing new, just bump last_verified
       changed               -> new revision + chunks, activated if it passes checks
       failed / suspicious   -> old revision stays active; flagged in the log
  2. Rebuild the past-paper catalogue from the latest Academic Time Machine commit.
  3. Write data/processed/web/to_embed.jsonl: ONLY the chunks of revisions that
     became active in this run (so only changed content gets re-embedded), plus
     the list of revision IDs that were retired (their old vectors should be
     deactivated).
  4. Append a summary to data/logs/refresh_log.jsonl.

Usage:
    python ingestion/refresh.py              # pages + catalogue
    python ingestion/refresh.py --skip-papers
Exit code 1 if any page failed or needs review, so you notice.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape_web import scrape  # noqa: E402
from web_store import OUT_DIR, ROOT, now_iso, read_json, read_jsonl  # noqa: E402

LOG = ROOT / "data" / "logs" / "refresh_log.jsonl"


def collect_to_embed(reports):
    new_chunks, retired = [], []
    for r in reports:
        if r["status"] not in ("new", "changed"):
            continue
        rev_dir = OUT_DIR / r["source_id"] / "revisions" / r["revision_id"].split(":")[1]
        new_chunks.extend(read_jsonl(rev_dir / "chunks.jsonl"))
        for rev_json in (OUT_DIR / r["source_id"] / "revisions").glob("*/revision.json"):
            rev = read_json(rev_json)
            if rev["status"] == "retired" and rev.get("retired_at") == r["fetched_at"]:
                retired.append(rev["revision_id"])
    return new_chunks, retired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-papers", action="store_true")
    args = ap.parse_args()
    started = now_iso()

    print("== official pages")
    reports = scrape()
    new_chunks, retired = collect_to_embed(reports)
    to_embed = OUT_DIR / "to_embed.jsonl"
    with to_embed.open("w", encoding="utf-8") as f:
        for c in new_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (OUT_DIR / "retired_revisions.json").write_text(json.dumps(retired, indent=2), encoding="utf-8")
    print(f"-> {len(new_chunks)} chunks to embed, {len(retired)} revisions retired")

    papers = None
    if not args.skip_papers:
        print("\n== past-paper catalogue")
        res = subprocess.run([sys.executable, str(Path(__file__).with_name("build_paper_catalogue.py"))])
        papers = "ok" if res.returncode == 0 else "failed"

    summary = {
        "started": started, "finished": now_iso(),
        "pages": {r["source_id"]: r["status"] for r in reports},
        "problems": {r["source_id"]: r.get("error") or r.get("problems") for r in reports
                     if r["status"] in ("failed", "needs_review")},
        "chunks_to_embed": len(new_chunks), "retired_revisions": retired, "papers": papers,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")
    if summary["problems"]:
        print("\nNeeds attention:", json.dumps(summary["problems"], indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
