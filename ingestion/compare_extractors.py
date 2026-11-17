"""Compare PyPDFLoader and pdfplumber on one PDF, page by page.

Writes each page's text from both extractors side by side, plus a
summary CSV, so you can open the PDF next to the output and judge
which extractor keeps reading order, rule numbers and tables intact.

Usage:
    python ingestion/compare_extractors.py data/raw/Academic_Rules_Handbook_2026.pdf
    python ingestion/compare_extractors.py data/raw/Academic_Rules_Handbook_2026.pdf --pages 3,7,12
"""
import argparse
import csv
import json
from pathlib import Path

import pdfplumber
from langchain_community.document_loaders import PyPDFLoader
from pypdf import PdfReader


def flag_page(pypdf_text: str, plumber_text: str, n_tables: int) -> str:
    """Return a short hint about what to inspect on this page."""
    a, b = len(pypdf_text.strip()), len(plumber_text.strip())
    if a == 0 and b == 0:
        return "NO_TEXT (scanned? needs OCR)"
    if n_tables:
        return "HAS_TABLE (compare table output)"
    if min(a, b) / max(a, b) < 0.8:
        return "LENGTH_MISMATCH (check what is missing)"
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="path to the PDF")
    parser.add_argument("--out", default="data/processed/extraction_compare")
    parser.add_argument("--pages", help="1-based pages to dump, e.g. 3,7,12 (default: all)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_dir = Path(args.out) / pdf_path.stem
    for sub in ("pypdf", "pdfplumber", "tables"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    wanted = {int(p) for p in args.pages.split(",")} if args.pages else None

    # Printed page labels ('iv', '12', ...) differ from physical page numbers.
    labels = PdfReader(pdf_path).page_labels
    # One Document per page; metadata['page'] is 0-based physical index.
    lc_pages = PyPDFLoader(str(pdf_path)).load()

    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for doc, page in zip(lc_pages, pdf.pages):
            physical = doc.metadata["page"] + 1  # 1-based, as you see it in a PDF viewer
            pypdf_text = doc.page_content or ""
            plumber_text = page.extract_text() or ""
            tables = page.extract_tables()

            if wanted is None or physical in wanted:
                name = f"page_{physical:03d}"
                (out_dir / "pypdf" / f"{name}.txt").write_text(pypdf_text, encoding="utf-8")
                (out_dir / "pdfplumber" / f"{name}.txt").write_text(plumber_text, encoding="utf-8")
                if tables:
                    (out_dir / "tables" / f"{name}.json").write_text(
                        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
                    )

            rows.append({
                "physical_page": physical,
                "printed_label": labels[physical - 1] if physical - 1 < len(labels) else "",
                "pypdf_chars": len(pypdf_text),
                "pdfplumber_chars": len(plumber_text),
                "pdfplumber_tables": len(tables),
                "flag": flag_page(pypdf_text, plumber_text, len(tables)),
            })

    summary = out_dir / "summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    flagged = [r for r in rows if r["flag"]]
    print(f"{len(rows)} pages processed -> {out_dir}")
    print(f"{len(flagged)} pages flagged for inspection (see {summary})")
    for r in flagged[:20]:
        print(f"  page {r['physical_page']} (printed '{r['printed_label']}'): {r['flag']}")


if __name__ == "__main__":
    main()
