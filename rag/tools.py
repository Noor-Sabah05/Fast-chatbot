"""Tool functions for the assistant (Week 4): find_papers and check_official_page.

Both return plain dicts (no Streamlit, no LangGraph) so they can be called
directly, from the basic RAG path, or wrapped as LangGraph tools later.

find_papers(course, exam_type=None, year=None)
    Search the past-paper LINK catalogue. Never invents a paper: every result
    is a row from data/catalogue/past_papers.jsonl with its original link.

check_official_page(source_id)
    Fetch a REGISTERED official page right now (approved hosts only, redirects
    must stay on approved hosts), clean it the same way as ingestion, and say
    whether it still matches our stored snapshot. If the fetch fails, return
    the stored snapshot with its last-verified date and a warning not to call
    it current.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingestion"))

from web_extract import extract  # noqa: E402
from web_fetch import FetchError, get_source, load_registry, safe_get  # noqa: E402
from web_store import OUT_DIR, now_iso, read_json, read_jsonl  # noqa: E402

CATALOGUE = ROOT / "data" / "catalogue" / "past_papers.jsonl"
MAX_RESULTS = 15

COURSE_ALIASES = {
    "PF": ["pf", "programming fundamentals", "programming fundamental", "fundamentals of programming", "cs118", "cs1002"],
    "OOP": ["oop", "object oriented programming", "object-oriented programming", "oops", "cp", "computer programming"],
    "DS": ["ds", "data structures", "data structure", "dsa"],
    "DLD": ["dld", "digital logic design", "digital logic", "digital logic and design"],
    "DB": ["db", "dbs", "dbms", "database", "databases", "database systems", "database system", "cs2005"],
}
EXAM_ALIASES = {
    "mid1": ["mid1", "mid 1", "mid-1", "mid i", "mid-i", "midterm 1", "midterm-1", "midterm i", "sessional 1", "sessional i", "first mid"],
    "mid2": ["mid2", "mid 2", "mid-2", "mid ii", "mid-ii", "midterm 2", "midterm-2", "midterm ii", "sessional 2", "sessional ii", "second mid"],
    "mid": ["mid", "mids", "midterm", "midterms", "mid term", "sessional", "sessionals"],
    "final": ["final", "finals", "final exam", "final term", "finalterm"],
    "quiz": ["quiz", "quizzes", "quizes"],
    "assignment": ["assignment", "assignments", "homework", "hw"],
}


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s.strip().lower())


def normalise_course(course: str) -> str | None:
    c = _norm(course)
    c_lab = re.sub(r"\s*lab$", "", c)
    for code, names in COURSE_ALIASES.items():
        if c_lab in names or c_lab.replace(" ", "") in [n.replace(" ", "") for n in names]:
            return code
    return None


def normalise_exam(exam_type: str) -> list[str] | None:
    e = _norm(exam_type)
    for key, names in EXAM_ALIASES.items():
        if e in names:
            return ["mid", "mid1", "mid2"] if key == "mid" else [key]
    return None


def _load_catalogue(path: Path = CATALOGUE):
    return read_jsonl(path)


def find_papers(course: str, exam_type: str | None = None, year: int | str | None = None,
                component: str | None = None, catalogue_path: Path = CATALOGUE) -> dict:
    """component: "course", "lab" or None (both). "PF lab" as the course also means lab."""
    query = {"course": course, "exam_type": exam_type, "year": year, "component": component}
    code = normalise_course(course or "")
    if not code:
        return {"status": "unsupported_course", "query": query, "results": [],
                "message": "The paper catalogue currently covers: " + ", ".join(
                    f"{c} ({COURSE_ALIASES[c][1].title()})" for c in COURSE_ALIASES) + "."}
    if component is None and re.search(r"\blab\s*$", _norm(course)):
        component = "lab"

    exam_types = None
    if exam_type:
        exam_types = normalise_exam(exam_type)
        if exam_types is None:
            return {"status": "clarification_needed", "query": query, "results": [],
                    "message": f"Unknown exam type {exam_type!r}. Try: final, mid, mid1, mid2, quiz or assignment."}

    term, year_int = None, None
    if year is not None and str(year).strip():
        m = re.search(r"(spring|fall|summer)?\s*'?(\d{4}|\d{2})\b", str(year).lower())
        if not m:
            return {"status": "clarification_needed", "query": query, "results": [],
                    "message": f"Couldn't read the year {year!r}. Use e.g. 2023 or 'Fall 2023'."}
        term = m.group(1)
        year_int = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)

    rows = [r for r in _load_catalogue(catalogue_path) if r["course"] == code]
    if component:
        rows = [r for r in rows if r["component"] == component]
    if exam_types:
        rows = [r for r in rows if r["exam_type"] in exam_types]
    unknown_year = 0
    if year_int:
        unknown_year = sum(r["year"] is None for r in rows)
        rows = [r for r in rows if r["year"] == year_int and (not term or r["term"] in (term, None))]

    rows.sort(key=lambda r: (-(r["year"] or 0), r["exam_type"], not r["has_solution"], r["file_name"]))
    results = [{
        "paper_id": r["paper_id"],
        "course": r["course_name"], "component": r["component"],
        "exam_type": r["exam_type"], "exam_number": r["exam_number"],
        "term": r["term"] or "unknown", "year": r["year"] if r["year"] else "unknown",
        "campus": r["campus"], "has_solution": r["has_solution"],
        "file_name": r["file_name"], "url": r["url"], "source": r["source"],
    } for r in rows[:MAX_RESULTS]]

    notes = ["Links come from Academic Time Machine, a student-maintained (unofficial) collection; "
             "campus and section are usually not recorded."]
    if unknown_year:
        notes.append(f"{unknown_year} other matching paper(s) have no recorded year and were left out.")
    if len(rows) > MAX_RESULTS:
        notes.append(f"Showing {MAX_RESULTS} of {len(rows)} matches; narrow by exam type or year.")
    return {"status": "found" if results else "no_match", "query": query, "total": len(rows),
            "results": results, "notes": notes}


# ---------------------------------------------------------------- official pages

def _stored(source_id: str, out_dir: Path = OUT_DIR):
    active = read_json(out_dir / source_id / "ACTIVE.json")
    if not active:
        return None, []
    rev = active["revision_id"].split(":")[1]
    return active, read_jsonl(out_dir / source_id / "revisions" / rev / "sections.jsonl")


def _public_sections(sections):
    return [{"heading_path": s["heading_path"], "text": s["text"], "campus": s["campus"],
             "programme": s["programme"], "years_mentioned": s.get("years_mentioned", [])} for s in sections]


def check_official_page(source_id: str, out_dir: Path = OUT_DIR, session=None) -> dict:
    try:
        registry = load_registry()
        source = get_source(source_id, registry)
    except KeyError as e:
        return {"status": "unknown_source", "source_id": source_id, "message": str(e)}

    active, stored_sections = _stored(source_id, out_dir)
    base = {"source_id": source_id, "title": source["title"], "url": source["url"],
            "last_verified": active["last_verified"] if active else None}
    checked_at = now_iso()
    try:
        resp = safe_get(source["url"], registry["allowed_hosts"], session=session)
        live = extract(resp.text, source)
    except (FetchError, ValueError) as e:
        msg = (f"Could not check the official page right now ({e}). "
               + (f"Showing the stored copy last verified on {active['last_verified'][:10]}; "
                  "do not present it as current." if active else "No stored copy is available."))
        return {**base, "status": "failed", "checked_at": checked_at, "error": str(e), "message": msg,
                "page_updated": active.get("page_updated") if active else None,
                "sections": _public_sections(stored_sections)}

    matches = bool(active) and active["revision_id"].endswith(":" + live["clean_hash"])
    years = sorted({y for s in live["sections"] for y in s.get("years_mentioned", [])})
    notes = []
    if live["page_updated"]:
        notes.append(f"The page says it was updated on {live['page_updated']}.")
    if years and int(years[-1]) < date.today().year:
        notes.append(f"The newest year this page mentions is {years[-1]}; it may describe an earlier cycle.")
    if not matches:
        notes.append("The live page differs from our stored snapshot; answer from the live text below "
                     "and run the weekly refresh to update the index.")
    return {**base, "status": "current" if matches else "changed", "checked_at": checked_at,
            "page_updated": live["page_updated"], "years_mentioned": years, "notes": notes,
            "sections": _public_sections(live["sections"])}
