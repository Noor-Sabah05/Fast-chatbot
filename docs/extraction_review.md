# Extraction review: Academic Rules Handbook 2026

| Physical page | Printed page | Content | PyPDF | pdfplumber | Notes |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

**Decision:** <which extractor, and for which pages>
**Page mapping:** physical page N = printed page N - <offset>
**Problems to clean:** <repeated headers/footers, broken lines, ...>
| 16 | 10 | Paragraph rules 3.18–3.24, sub-list a/b/c | Broken words ("r elevant", "earn ed", "c an", "b y"), footer at top | Clean, correct order | Footer page number extracted as "1 0" |

**Decision:** pdfplumber for all pages (text and tables). PyPDF splits words and puts the footer first.
**Page mapping:** printed page = physical page − 6 (confirmed on p.12 and p.16). Do not read page numbers from footer text ("1 0").
**Footer table:** footer bar is detected as a table on most pages; filter it out. HAS_TABLE flags are mostly false positives.
**Hidden text:** the PDF places the neighbouring page's text off-page (negative x). Odd pages in the comparison output were contaminated; clean_pdf.py keeps only on-page characters.