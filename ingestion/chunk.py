"""Build the four chunking variants from cleaned pages (PREPROCESSING_PLAN step 4).

  R300 / R500 / R800 : recursive token splitting of the whole document
  S500               : split by section/sub-heading/rule first; keep each rule
                       whole; only split a rule that is itself over 500 tokens

All sizes are TOKENS (tiktoken cl100k_base, the encoding used by
text-embedding-3-small). Every chunk records its page span, so a chunk that
runs from page 38 onto page 39 cites both.

Each chunk has:
  text            the unmodified evidence (shown as the citation)
  retrieval_text  "Title > Section > Sub-heading" + text (this gets embedded)

Usage:
    python ingestion/chunk.py data/processed/Academic_Rules_Handbook_2026/pages.jsonl
    python ingestion/chunk.py data/processed/Student_Handbook_2026/pages.jsonl
Output:
    data/processed/<name>/chunks/R300.jsonl, R500.jsonl, R800.jsonl, S500.jsonl
"""
import argparse
import bisect
import hashlib
import json
import re
from pathlib import Path

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

ENCODING = "cl100k_base"
ENC = tiktoken.get_encoding(ENCODING)
EXPERIMENTS = {"R300": (300, 45), "R500": (500, 75), "R800": (800, 120), "S500": (500, 75)}

SECTION_RE = re.compile(r"^(\d{1,2})\.\s+([A-Z][^:;=]{2,70})$")   # "9. Policy on Retake Examination"
RULE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\s")                        # "2.3 The points for ..."
# Student Handbook style: "II - FINANCIAL RULES", "VI – SEMESTER RULES", "IX .STUDENT SOCIETIES", "XI. Policy on ..."
ROMAN_SECTION_RE = re.compile(r"^([IVX]{1,4})\s*[-–.]\s*\.?\s*([A-Z].{2,70})$")
ROMAN = {"I": 1, "V": 5, "X": 10}


def roman_to_int(r: str) -> int:
    total = 0
    for a, b in zip(r, r[1:] + " "):
        total += -ROMAN[a] if ROMAN.get(b, 0) > ROMAN[a] else ROMAN[a]
    return total


def n_tokens(text: str) -> int:
    return len(ENC.encode(text))


# ---------- 1. Join pages into one document, remembering where each page starts ----------

def load_document(pages_path: Path, include_front_matter: bool):
    pages = [json.loads(line) for line in pages_path.open(encoding="utf-8")]
    if not include_front_matter:
        pages = [p for p in pages if not p["front_matter"]]
    starts, parts, pos = [], [], 0
    for p in pages:
        starts.append(pos)
        parts.append(p["text"])
        pos += len(p["text"]) + 1          # +1 for the "\n" joining pages
    return "\n".join(parts), pages, starts


def pages_for_span(start: int, end: int, pages, starts):
    first = bisect.bisect_right(starts, start) - 1
    last = bisect.bisect_right(starts, max(start, end - 1)) - 1
    span = pages[first:last + 1]
    return [p["physical_page"] for p in span], [p["printed_page"] for p in span]


# ---------- 2. Find headings (with their character position) ----------

def find_headings(doc: str):
    """Return a sorted list of (position, level, title). level 1 = section, 2 = sub-heading."""
    headings, section_no, pos = [], 0, 0
    lines = doc.split("\n")
    # A document numbers its sections either "9. Title" or "IX - TITLE". In the Roman
    # style, "1. ..." lines are list items, so the Arabic pattern is switched off.
    roman = any((m := ROMAN_SECTION_RE.match(l.strip())) and roman_to_int(m.group(1)) == 1 for l in lines)
    prev = "."
    for i, line in enumerate(lines):
        s = line.strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        m = None if roman else SECTION_RE.match(s)
        rm = ROMAN_SECTION_RE.match(s) if roman else None
        if rm and roman_to_int(rm.group(1)) == section_no + 1:
            section_no = roman_to_int(rm.group(1))
            headings.append((pos, 1, s))
        elif roman and section_no == 0 and s.isupper() and len(s) >= 8 and s.replace(" ", "").isalpha():
            headings.append((pos, 1, s))     # unnumbered opening section, e.g. "CODE OF CONDUCT"
        elif m and int(m.group(1)) == section_no + 1 and not s.endswith("."):
            section_no = int(m.group(1))
            title = s
            first_word = nxt.split()[0] if nxt else ""
            if len(nxt) < 30 and (first_word[:1].islower() or (len(first_word) > 1 and first_word.isupper())):
                title = f"{s} {nxt}"        # wrapped title, e.g. "10. Policy on Generative" + "AI Based Tools"
            headings.append((pos, 1, title))
        elif (
            3 <= len(s) <= 70 and s[0].isupper()
            and not RULE_RE.match(s) and not re.match(r"^\(?\d", s)
            and s[-1] not in ".,;:" and ":" not in s and "=" not in s
            and len(s.split()) <= 8 and "," not in s     # headings are short; wrapped sentences are not
            and (prev.rstrip()[-1:] in ".:;" or re.search(r"\w: \S", prev))  # sentence or table row finished
            and not (nxt[:1].islower())                # next line isn't a continuation
        ):
            headings.append((pos, 2, s))
        if s:
            prev = s
        pos += len(line) + 1
    return headings


def breadcrumb_at(position: int, headings, title: str) -> list[str]:
    section = sub = None
    for pos, level, text in headings:
        if pos > position:
            break
        if level == 1:
            section, sub = text, None
        else:
            sub = text
    return [x for x in (title, section, sub) if x]


# ---------- 3. Splitting strategies ----------

def splitter(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=ENCODING, chunk_size=size, chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""], add_start_index=True,
    )


def recursive_spans(doc: str, size: int, overlap: int):
    """R-variants: (start, end) character spans over the whole document."""
    spans = []
    for d in splitter(size, overlap).create_documents([doc]):
        start = d.metadata["start_index"]
        spans.append((start, start + len(d.page_content)))
    return spans


def structural_spans(doc: str, headings, size: int, overlap: int):
    """S500: blocks between headings -> rules -> pack whole rules up to `size` tokens."""
    # Block boundaries: every heading position.
    cuts = sorted({0, len(doc), *[h[0] for h in headings]})
    spans = []
    for b_start, b_end in zip(cuts, cuts[1:]):
        block = doc[b_start:b_end]
        if not block.strip():
            continue
        # Units = rules: split before each line starting with "N.N "
        unit_starts = [0] + [m.start() + 1 for m in re.finditer(r"\n(?=\d{1,2}\.\d{1,2}\s)", block)]
        units = [(b_start + a, b_start + b) for a, b in zip(unit_starts, unit_starts[1:] + [len(block)])]

        current = None
        for u_start, u_end in units:
            if n_tokens(doc[u_start:u_end]) > size:            # one oversized rule: split it
                if current:
                    spans.append(current)
                    current = None
                rule = doc[u_start:u_end]
                for d in splitter(size, overlap).create_documents([rule]):
                    s = u_start + d.metadata["start_index"]
                    spans.append((s, s + len(d.page_content)))
            elif current and n_tokens(doc[current[0]:u_end]) <= size:
                current = (current[0], u_end)                    # add rule to current chunk
            else:
                if current:
                    spans.append(current)
                current = (u_start, u_end)
        if current:
            spans.append(current)

    # Safety pass: anything still over the limit gets split recursively.
    final = []
    for a, b in spans:
        if n_tokens(doc[a:b]) <= size:
            final.append((a, b))
            continue
        for d in splitter(size, overlap).create_documents([doc[a:b]]):
            s = a + d.metadata["start_index"]
            final.append((s, s + len(d.page_content)))
    return final


# ---------- 4. Build chunk records ----------

def build_chunks(name, spans, doc, pages, starts, headings):
    first = pages[0]
    title = first["title"]
    revision = f"{first['raw_file_hash']}:{first['extractor']}"
    size, overlap = EXPERIMENTS[name]
    config = f"{name}:{ENCODING}:{size}/{overlap}"
    chunks = []
    for ordinal, (start, end) in enumerate(spans):
        # trim whitespace but keep the span accurate
        raw = doc[start:end]
        start += len(raw) - len(raw.lstrip())
        text = raw.strip()
        if not text:
            continue
        end = start + len(text)
        path = breadcrumb_at(start, headings, title)
        retrieval_text = " > ".join(path) + "\n" + text
        physical, printed = pages_for_span(start, end, pages, starts)
        chunk_id = hashlib.sha256(f"{revision}|{config}|{ordinal}|{text}".encode()).hexdigest()[:20]
        chunks.append({
            "chunk_id": chunk_id,
            "experiment": name,
            "config": config,
            "source_id": first["source_id"],
            "title": title,
            "ordinal": ordinal,
            "section_path": path[1:],
            "physical_pages": physical,
            "printed_pages": printed,
            "char_start": start,
            "char_end": end,
            "text": text,
            "retrieval_text": retrieval_text,
            "n_tokens_text": n_tokens(text),
            "n_tokens_retrieval": n_tokens(retrieval_text),
            "revision": revision,
        })
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pages", help="pages.jsonl from clean_pdf.py")
    parser.add_argument("--include-front-matter", action="store_true")
    parser.add_argument("--show-headings", action="store_true", help="print detected headings")
    args = parser.parse_args()

    pages_path = Path(args.pages)
    doc, pages, starts = load_document(pages_path, args.include_front_matter)
    headings = find_headings(doc)
    if args.show_headings:
        for pos, level, text in headings:
            print(("  " if level == 2 else "") + text)

    out_dir = pages_path.parent / "chunks"
    out_dir.mkdir(exist_ok=True)

    print(f"{'experiment':<11}{'chunks':>7}{'avg tok':>9}{'max tok':>9}{'multi-page':>12}")
    for name, (size, overlap) in EXPERIMENTS.items():
        spans = (structural_spans(doc, headings, size, overlap) if name.startswith("S")
                 else recursive_spans(doc, size, overlap))
        chunks = build_chunks(name, spans, doc, pages, starts, headings)
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        toks = [c["n_tokens_text"] for c in chunks]
        multi = sum(len(c["physical_pages"]) > 1 for c in chunks)
        print(f"{name:<11}{len(chunks):>7}{sum(toks) / len(toks):>9.0f}{max(toks):>9}{multi:>12}")
    print(f"-> {out_dir}")


if __name__ == "__main__":
    main()
