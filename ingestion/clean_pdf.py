"""Clean a handbook PDF into one record per page, ready for chunking.

What it does on every page:
  1. Drops text placed outside the visible page (this PDF hides the
     neighbouring page's text off-page, which made pages repeat).
  2. Cuts off the footer bar (handbook title + page no.; see FOOTER_TEXTS).
  3. Removes the repeated header line.
  4. Turns real tables into one readable line per row, e.g.
     "Grade: A+, Points: 4.00", and splits side-by-side tables into pairs.
  5. Records the physical page and the printed page number for citations.

Output (in data/processed/<pdf name>/):
  pages.jsonl   one JSON record per page (used by the chunking step)
  preview.md    all pages in one file, for checking by eye

Usage:
    python ingestion/clean_pdf.py data/raw/Academic_Rules_Handbook_2026.pdf
"""
import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

HEADER_RE = re.compile(r"^\s*NATIONAL UNIVERSITY OF COMPUTER AND EMERGING SCIENCES\s*$", re.M)
# Footer bar text, one entry per handbook
FOOTER_TEXTS = (
    "ndergraduate Academic Rules and Regulations",  # Academic Rules handbook (matches "U ndergraduate" too)
    "Student Handbook 2026",                        # Student Handbook
)
EXTRACTOR_VERSION = "pdfplumber-clean-v1"


def is_footer(table) -> bool:
    return any(ft in (cell or "") for ft in FOOTER_TEXTS for row in table for cell in row)


def looks_like_header(row: list[str]) -> bool:
    """A real table header has 2+ short labels, not numbered list items."""
    cells = [c for c in row if c]
    return (
        len(cells) >= 2
        and all(len(c) <= 25 for c in cells)
        and not any(re.match(r"^\(?\d+[.)]", c) for c in cells)
    )


def table_to_text(rows: list[list[str]]) -> str:
    """Render a table as text.

    Real tables -> one line per row: 'Grade: A+, Points: 4.00'.
    Boxed lists/paragraphs -> cells read column by column (left column first),
    because items 1-4 sit in the left column and 5-7 in the right.
    """
    rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]  # drop empty border rows
    if not rows:
        return ""

    if not looks_like_header(rows[0]):
        n_cols = max(len(r) for r in rows)
        cells = [r[i] for i in range(n_cols) for r in rows if i < len(r) and r[i]]
        return "\n".join(cells)

    header, body = rows[0], rows[1:]
    # Header split over two rows, e.g. "Documents" / "Required"
    while body and not body[0][0] and all(len(c) <= 25 for c in body[0]):
        header = [f"{h} {c}".strip() for h, c in zip(header, body.pop(0))]

    half = len(header) // 2
    # Side-by-side table such as Grade|Points|Grade|Points -> two Grade|Points tables
    if half and len(header) % 2 == 0 and header[:half] == header[half:]:
        body = [r[:half] for r in body] + [r[half:] for r in body]
        header = header[:half]

    # Merged cells leave empty columns; line up the non-empty labels with the used columns
    used = [i for i in range(len(header)) if any(i < len(r) and r[i] for r in body)]
    labels = [h for h in header if h]
    labels = labels if len(labels) == len(used) else [header[i] for i in used]

    lines = []
    for r in body:
        values = [r[i] if i < len(r) else "" for i in used]
        if any(values):
            lines.append(", ".join(f"{h}: {v}" if h else v for h, v in zip(labels, values) if v))
    return "\n".join(lines)


def clean_text(text: str) -> str:
    text = HEADER_RE.sub("", text)
    text = re.sub(r"Error! Bookmark not\s+defined\.", "", text)   # Word cross-reference leftover (Student Handbook)
    text = re.sub(r"[ \t]+", " ", text)          # collapse runs of spaces
    text = re.sub(r"\n{3,}", "\n\n", text)       # collapse blank lines
    return text.strip()


def page_content(page) -> tuple[str, int]:
    """Return cleaned text of one page and the number of real tables on it."""
    # step 1: drop off-page hidden text (keep lines/rects so the footer box is still found)
    width = page.width
    page = page.filter(lambda o: o.get("object_type") != "char" or (o["x0"] >= 0 and o["x1"] <= width))

    tables = page.find_tables()
    footers = [t for t in tables if is_footer(t.extract())]
    real = sorted((t for t in tables if not is_footer(t.extract())), key=lambda t: t.bbox[1])

    bottom = min((t.bbox[1] for t in footers), default=page.height)  # step 2: cut footer
    parts, cursor = [], 0.0
    for t in real:                                # step 4: text above each table, then table
        x0, top, x1, tbottom = t.bbox
        if top > cursor:
            parts.append(page.crop((0, cursor, page.width, top)).extract_text() or "")
        parts.append(table_to_text(t.extract()))
        cursor = tbottom
    if bottom > cursor:
        parts.append(page.crop((0, cursor, page.width, bottom)).extract_text() or "")

    return clean_text("\n".join(p for p in parts if p)), len(real)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--out", default="data/processed")
    parser.add_argument("--source-id", default=None, help="stable source ID (default: file name)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    source_id = args.source_id or pdf_path.stem.lower()
    out_dir = Path(args.out) / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    labels = PdfReader(pdf_path).page_labels

    records = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            physical = i + 1
            label = labels[i] if i < len(labels) else ""
            printed = int(label) if label.isdigit() else None   # front matter has junk labels
            text, n_tables = page_content(page)
            if not text:
                continue                                        # blank pages
            records.append({
                "source_id": source_id,
                "title": pdf_path.stem.replace("_", " "),
                "physical_page": physical,
                "printed_page": printed,
                "front_matter": printed is None,
                "n_tables": n_tables,
                "text": text,
                "text_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
                "raw_file_hash": raw_hash[:16],
                "extractor": EXTRACTOR_VERSION,
                "extracted_on": date.today().isoformat(),
            })

    with (out_dir / "pages.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with (out_dir / "preview.md").open("w", encoding="utf-8") as f:
        for r in records:
            label = r["printed_page"] if r["printed_page"] else "front matter"
            f.write(f"\n\n---\n## Physical page {r['physical_page']} (printed: {label})\n\n{r['text']}\n")

    body = [r for r in records if not r["front_matter"]]
    print(f"{len(records)} pages kept ({len(body)} body, {len(records) - len(body)} front matter)")
    print(f"pages with real tables: {[r['physical_page'] for r in records if r['n_tables']]}")
    print(f"-> {out_dir / 'pages.jsonl'}\n-> {out_dir / 'preview.md'}")


if __name__ == "__main__":
    main()
