# FAST Chatbot

RAG assistant for FAST NUCES Lahore undergraduates: answers university questions
with page-level citations and helps students find past papers.

## Setup
    python -m venv .venv
    .venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt
    copy .env.example .env           # then fill in your keys

## Extraction (Week 1)
    python ingestion/compare_extractors.py data/raw/Academic_Rules_Handbook_2026.pdf

## Data sources

Everything the assistant can cite comes from one of these. Nothing else is indexed.

| Source | What we keep | Where it's listed | Refreshed |
|---|---|---|---|
| Undergraduate Academic Rules Handbook 2026 (PDF) | Cleaned page text + chunks with physical and printed page numbers | `data/raw/` | When a new edition is published |
| Student Handbook 2026 (PDF) | Same as above | `data/raw/` | When a new edition is published |
| 8 official web pages: admissions FAQ, admission schedule, programmes offered, eligibility, fee structure, scholarships, student financial rules (nu.edu.pk), and the Lahore campus about/contact page (lhr.nu.edu.pk) | Dated raw HTML snapshots, cleaned sections tagged with campus + programme level, chunks | `ingestion/sources.json` | Weekly, by hand (`refresh.py`) |
| Past papers for PF, OOP, Data Structures, DLD and Databases | A **link catalogue only** (course, lab/course, exam type, term, year, solution yes/no, link). Papers are not downloaded, read or solved | Built from [Academic Time Machine](https://github.com/saleha-muzammil/Academic-Time-Machine) | Weekly (`refresh.py`) |
| Flex, Slate, timetable generator, teacher ratings, library, news | Labelled links only | the team's `useful_links` list (not in the repo yet) | n/a |

Rules we stick to:
- **Only registered pages are fetched.** `sources.json` lists the exact URLs and the approved hosts
  (`www.nu.edu.pk`, `nu.edu.pk`, `lhr.nu.edu.pk`). Redirects to any other host are refused.
  We respect robots.txt and wait 2 seconds between requests.
- **Unknown stays unknown.** A section is tagged Lahore or undergraduate only if it says so
  (or sits under a heading that does, or the whole page is the Lahore site). University-wide
  nu.edu.pk pages default to campus `all`; the programmes table lists the campuses per programme.
  Past papers always have campus `unknown` because the repo doesn't record it; year/term are
  `unknown` when the file name doesn't say.
- **Fetched date is not the same as "current".** Every chunk stores `fetched_at`, plus the
  page's own "Updated on" date (`page_updated`) and the years the section mentions.
  For deadlines and fees the assistant re-checks the live page with `check_official_page`.
- The past-paper collection is student-maintained and **unofficial**; the assistant says so.

## Ingestion

All commands run from the repo root.

### PDFs (weeks 1-2)
    python ingestion/clean_pdf.py data/raw/Academic_Rules_Handbook_2026.pdf
    python ingestion/chunk.py data/processed/Academic_Rules_Handbook_2026/pages.jsonl

### Official web pages (week 3)
    python ingestion/scrape_web.py                 # all registered pages
    python ingestion/scrape_web.py nu_fee_structure   # one page
    python ingestion/scrape_web.py --show nu_fee_structure   # print what was extracted - check it by eye!

For each page this: fetches it → saves `data/raw/web/<source_id>/<date>_<hash>.html` → keeps only the
main content (drops menus, footer, title banner, breadcrumbs and **HTML comments** - the schedule page
hides an old schedule in a comment) → splits it into sections (one per FAQ question, otherwise one per
heading; tables become one line per row) → tags campus/programme → chunks (≈500 tokens, FAQ answers kept
whole) into `data/processed/web/<source_id>/revisions/<clean_hash>/`.

A revision is named by the hash of the **cleaned** text, so:
- same content as last time → no new revision, no new chunks, only `last_verified` is updated;
- changed content → a new revision is staged, checked (not empty, not less than half the old size) and
  only then made active; the old one is marked `retired`;
- failed fetch or a suspicious result → the old revision stays active and the page is flagged.

`data/processed/web/active_chunks.jsonl` always holds every active web chunk (same fields as the PDF
chunks plus `url`, `campus`, `programme`, `fetched_at`, `page_updated`, `time_sensitive`).

If a page can't be fetched from your network, save it from the browser and use
`python ingestion/scrape_web.py --from-file <source_id> saved.html`.

To add a page: add an entry to `ingestion/sources.json` (new unique `source_id`, the CSS `selector`
for its main content, default campus/programme), run `scrape_web.py <source_id> --show`, check the output.

### Past-paper catalogue (week 3)
    python ingestion/build_paper_catalogue.py

Reads only the file list of the Academic Time Machine repo (a blob-less clone in `data/raw/atm/`,
git-ignored), classifies each file from its folder and name, merges identical files stored twice,
and writes `data/catalogue/past_papers.jsonl`. Slides, books, outlines, code, lab tasks, student
answer-script samples and unnamed photos are skipped; every skipped file and the reason is in
`data/catalogue/past_papers_report.json` for review.

### Weekly refresh (week 3)
    python ingestion/refresh.py

Re-scrapes every page and rebuilds the catalogue. Writes `data/processed/web/to_embed.jsonl`
(**only** chunks from revisions that changed this week - embed just these) and
`retired_revisions.json` (revisions whose old vectors should be deactivated), and appends a
summary to `data/logs/refresh_log.jsonl`. Exits with an error if anything needs attention.

### Checks (week 4)
    python ingestion/check_duplicates.py   # re-ingesting the same data creates no duplicate chunks
    python -m pytest tests                  # extraction, tagging, catalogue, tools (offline)

`check_duplicates.py` re-ingests every stored snapshot twice and rebuilds every PDF chunk
variant twice, and fails if a second run creates a new revision, changes a chunk ID, or if the
active set contains a repeated chunk ID or repeated text.

### Tools for the assistant (week 4)
`rag/tools.py` (plain functions, no Streamlit):
- `find_papers(course, exam_type=None, year=None)` - e.g. `find_papers("OOP", "mid 2", "Spring 2024")`.
  Understands course names/abbreviations (`"Programming Fundamentals"`, `"DS"`, `"DBMS"`, `"PF lab"`)
  and exam names (`"midterm"`, `"sessional 1"`, `"finals"`). Returns `status`
  (`found` / `no_match` / `unsupported_course` / `clarification_needed`), up to 15 results with links, and notes.
- `check_official_page(source_id)` - fetches a registered page now and returns `status`
  `current` (matches our snapshot), `changed` (with the live sections) or `failed` (with the stored
  sections, the last-verified date, and a message not to present it as current).

## Layout
    data/raw/         original PDFs (never edited)
    data/processed/   cleaned, reviewed extraction
    data/raw/web/     dated raw HTML snapshots of official pages (never edited)
    data/processed/web/  cleaned web sections, revisions and chunks
    data/catalogue/   past-paper link catalogue
    ingestion/        extraction, cleaning, chunking, scraping, refresh, checks
    tests/            offline tests (HTML fixtures in tests/fixtures)
    rag/              retrieval, answer generation, tool functions (tools.py)
    app/              Streamlit UI
    eval/             evaluation questions and results
    docs/             plans and review notes
