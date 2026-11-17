"""Build the past-paper catalogue for PF, OOP, DS, DLD and Databases (Week 3).

Source: the Academic Time Machine GitHub repo (student-maintained, not official).
We only read the repo's FILE LIST (git ls-tree) - no paper is downloaded or
read. Each paper becomes one catalogue row with a link to the original file.

For every file we work out, from its folder and file name:
  course, component (course / lab), exam_type (final, mid1, mid2, mid, quiz,
  assignment), exam_number, term (spring/fall/summer), year, has_solution.
Anything we can't tell stays "unknown"/null - never guessed. Campus is always
"unknown" because the repo doesn't record it.

Files that are clearly not papers (slides, books, outlines, code, practice
problems, student answer-script "Samples", unnamed photos) are skipped and
counted in the report so you can check them.

Usage:
    python ingestion/build_paper_catalogue.py                 # clones/updates the repo into data/raw/atm
    python ingestion/build_paper_catalogue.py --repo-dir PATH # use an existing clone
Output:
    data/catalogue/past_papers.jsonl
    data/catalogue/past_papers_report.json   (counts + skipped files, for review)
"""
import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/saleha-muzammil/Academic-Time-Machine"
BRANCH = "main"
DEFAULT_CLONE = ROOT / "data" / "raw" / "atm"
OUT_DIR = ROOT / "data" / "catalogue"

COURSES = {
    # code: (full name, {repo folder: component})
    "PF": ("Programming Fundamentals", {"PF": "course", "PF Lab": "lab"}),
    "OOP": ("Object Oriented Programming", {"OOP": "course", "OOP Lab": "lab"}),
    "DS": ("Data Structures", {"Data Structures": "course", "Data Structures Lab": "lab"}),
    "DLD": ("Digital Logic Design", {"DLD": "course", "DLD Lab": "lab"}),
    "DB": ("Database Systems", {"DB": "course", "DB Lab": "lab"}),
}
PAPER_EXT = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".pptx"}
SKIP_PATH = re.compile(r"\b(samples?|slides?|lectures?|lec pngs|books?|notes|outline|course outline|"
                       r"problems?#?\d*|practice|tutorial|manual)\b", re.I)

TERM_WORDS = {"spring": "spring", "sp": "spring", "s": "spring", "fall": "fall", "f": "fall",
              "summer": "summer", "su": "summer"}
TERM_RE = re.compile(r"(?<![a-z])(spring|fall|summer|sp|su|f|s)[\s_\-]*'?(\d{4}|\d{2})(?!\d)", re.I)
YEAR_TERM_RE = re.compile(r"(?<!\d)(20\d\d)[\s_\-]*(spring|fall|summer|sp|su)(?![a-z])", re.I)   # "2013 Sp Mid2"
YEAR_RE = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
SOLUTION_RE = re.compile(r"(?<![a-z])sol(ution|n)?s?\b|soultion|solved|\+\s*sol|answer key|_sol\b", re.I)


def classify_exam(text: str):
    """Return (exam_type, number) from a lower-cased path, or (None, None)."""
    t = text.lower()
    m = re.search(r"(?:(?<![a-z])|(?<=sp))(mid|midterm|sessional)[\s_\-]*(term)?[\s_\-]*(ii|i|1|2)(?![\d])", t)
    if m:
        return ("mid1" if m.group(3) in ("1", "i") else "mid2"), None
    if re.search(r"\bfinal|\bfinals\b|\bfin\b", t):
        return "final", None
    if re.search(r"\b(mid|midterm|mid-term|sessional)", t):
        return "mid", None
    m = re.search(r"qu?iz[\s_\-#]*(\d{1,2}(?!\d))?", t)
    if m:
        return "quiz", int(m.group(1)) if m.group(1) else None
    m = re.search(r"\b(assignment|assignemnt|homework|hw)[\s_\-#]*(\d{1,2}(?!\d))?", t)
    if m:
        return "assignment", int(m.group(2)) if m.group(2) else None
    return None, None


def term_and_year(text: str):
    m = TERM_RE.search(text)
    if m:
        term = TERM_WORDS[m.group(1).lower()]
        y = int(m.group(2))
        year = y if y > 100 else 2000 + y
        if 1995 <= year <= datetime.now().year + 1:
            return term, year
    m = YEAR_TERM_RE.search(text)
    if m:
        return TERM_WORDS[m.group(2).lower()], int(m.group(1))
    m = YEAR_RE.search(text)
    if m:
        y = int(m.group(1))
        if 1995 <= y <= datetime.now().year + 1:
            return None, y
    return None, None


def parse_path(path: str, code: str, folder: str, component: str):
    rel = path[len(folder) + 1:]
    name = Path(path).name
    ext = Path(path).suffix.lower()
    if ext not in PAPER_EXT:
        return None, "not a paper file type"
    if SKIP_PATH.search(rel.replace("_", " ")):
        return None, "slides/notes/outline/samples"
    # the file name says most (e.g. "Quiz 1"); fall back to the folders ("Final/2018/...")
    exam_type, number = classify_exam(name.replace("_", " "))
    if not exam_type:
        exam_type, number = classify_exam(rel.replace("_", " "))
    if not exam_type:
        return None, "no exam type in name"
    term, year = term_and_year(rel.replace("_", " "))
    lab = component == "lab" or bool(re.search(r"\blab\b", rel, re.I))
    return {
        "paper_id": hashlib.sha1(path.encode()).hexdigest()[:12],
        "course": code,
        "course_name": COURSES[code][0],
        "component": "lab" if lab else "course",
        "exam_type": exam_type,
        "exam_number": number,
        "term": term,
        "year": year,
        "campus": "unknown",
        "has_solution": bool(SOLUTION_RE.search(rel.replace("_", " "))),
        "legacy_course": "Computer Programming (old syllabus)" if "old syllabus" in rel.lower() else None,
        "file_name": name,
        "file_type": ext.lstrip("."),
        "repo_path": path,
        "url": f"{REPO_URL}/blob/{BRANCH}/{quote(path)}",
        "source": "Academic Time Machine (student-maintained, unofficial)",
    }, None


def ensure_clone(repo_dir: Path) -> None:
    if (repo_dir / ".git").exists():
        subprocess.run(["git", "-C", str(repo_dir), "fetch", "--depth", "1", "origin", BRANCH], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "update-ref", "HEAD", "FETCH_HEAD"], check=True)
    else:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        # --filter=blob:none --no-checkout: only the file list, not the GBs of files
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout",
                        "--branch", BRANCH, REPO_URL, str(repo_dir)], check=True)


def list_files(repo_dir: Path, folders) -> tuple[list[str], str]:
    # "<mode> blob <object id>\t<path>" - the object id lets us spot identical files without downloading them
    out = subprocess.run(["git", "-C", str(repo_dir), "-c", "core.quotepath=off", "ls-tree", "-r",
                          "HEAD", "--", *folders], capture_output=True, text=True, check=True)
    commit = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    files = []
    for line in out.stdout.splitlines():
        meta, path = line.split("\t", 1)
        files.append((meta.split()[2], path))
    return files, commit


def build(files: list[tuple[str, str]], commit: str):
    """files = [(git object id, path)]. Identical files stored twice become one row."""
    rows, skipped, by_blob = [], [], {}
    folder_of = {f: (code, comp) for code, (_, fs) in COURSES.items() for f, comp in fs.items()}
    for oid, p in files:
        folder = next((f for f in sorted(folder_of, key=len, reverse=True) if p.startswith(f + "/")), None)
        if not folder:
            continue
        code, comp = folder_of[folder]
        row, why = parse_path(p, code, folder, comp)
        if row and oid in by_blob:
            by_blob[oid]["duplicate_paths"].append(p)
        elif row:
            row["source_commit"] = commit
            row["duplicate_paths"] = []
            by_blob[oid] = row
            rows.append(row)
        else:
            skipped.append({"path": p, "reason": why})
    rows.sort(key=lambda r: (r["course"], r["component"], -(r["year"] or 0), r["exam_type"], r["file_name"]))
    return rows, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", type=Path, default=DEFAULT_CLONE)
    ap.add_argument("--no-update", action="store_true", help="don't fetch, use the clone as it is")
    args = ap.parse_args()
    if not args.no_update:
        ensure_clone(args.repo_dir)
    folders = [f for _, fs in COURSES.values() for f in fs]
    files, commit = list_files(args.repo_dir, folders)
    rows, skipped = build(files, commit)
    paths = [p for _, p in files]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "past_papers.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_repo": REPO_URL, "source_commit": commit,
        "files_seen": len(paths), "papers": len(rows), "skipped": len(skipped),
        "by_course": Counter(f"{r['course']} {r['component']}" for r in rows),
        "by_exam_type": Counter(r["exam_type"] for r in rows),
        "year_unknown": sum(r["year"] is None for r in rows),
        "skipped_reasons": Counter(s["reason"] for s in skipped),
        "skipped_files": skipped,
    }
    (OUT_DIR / "past_papers_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(rows)} papers from {len(paths)} files (commit {commit[:8]})")
    for k, v in sorted(report["by_course"].items()):
        print(f"  {k:<14}{v}")
    print(f"  year unknown: {report['year_unknown']}   skipped: {len(skipped)} -> see past_papers_report.json")


if __name__ == "__main__":
    main()
