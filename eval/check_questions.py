"""Check that every evidence quote in the question set really is on the page it claims.

For each evidence item it looks up data/processed/<handbook>/pages.jsonl (matched by
source_id), finds the page by physical_page, and checks:
  - the printed_page label matches
  - the quote appears in that page's text (spaces/line breaks ignored)

Run this after re-cleaning a PDF too: if cleaning changes the text, it shows
which labels need fixing.

Usage:
    python eval/check_questions.py eval/dev_questions.jsonl
"""
import argparse
import json
import re
from pathlib import Path


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def load_pages(processed: Path) -> dict:
    """{(source_id, physical_page): page record} for every cleaned handbook."""
    pages = {}
    for f in processed.glob("*/pages.jsonl"):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            pages[(r["source_id"], r["physical_page"])] = r
    return pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("questions")
    parser.add_argument("--processed", default="data/processed")
    args = parser.parse_args()

    pages = load_pages(Path(args.processed))
    questions = [json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip()]

    problems = 0
    for q in questions:
        if q["answerable"] != bool(q["evidence"]):
            print(f"{q['id']}: 'answerable' does not match whether evidence is given")
            problems += 1
        for ev in q["evidence"]:
            page = pages.get((ev["source_id"], ev["physical_page"]))
            if page is None:
                print(f"{q['id']}: no page {ev['physical_page']} for {ev['source_id']}")
                problems += 1
                continue
            if page["printed_page"] != ev["printed_page"]:
                print(f"{q['id']}: printed page is {page['printed_page']}, label says {ev['printed_page']}")
                problems += 1
            if norm(ev["quote"]) not in norm(page["text"]):
                print(f"{q['id']}: quote not found on page {ev['physical_page']}: {ev['quote'][:60]}...")
                problems += 1

    n_ans = sum(q["answerable"] for q in questions)
    types = sorted({q["type"] for q in questions})
    print(f"{len(questions)} questions ({n_ans} answerable, {len(questions) - n_ans} unanswerable)")
    print(f"types: {', '.join(types)}")
    print("all evidence checks passed" if problems == 0 else f"{problems} problem(s) found")


if __name__ == "__main__":
    main()
