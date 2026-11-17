import json

from build_paper_catalogue import build, classify_exam, term_and_year
from tools import find_papers


def test_classify_and_dates():
    assert classify_exam("ds mid 1 fall 2020.pdf") == ("mid1", None)
    assert classify_exam("cp mid ii - fall 2018 + solution.pdf") == ("mid2", None)
    assert classify_exam("sessional 2 spring 2014.pdf") == ("mid2", None)
    assert classify_exam("db quiz 2 spring 2023.pdf") == ("quiz", 2)
    assert classify_exam("lec 3.jpg") == (None, None)
    assert term_and_year("F22 PF Mid1+Solution") == ("fall", 2022)
    assert term_and_year("DB-SP14-Final") == ("spring", 2014)
    assert term_and_year("2013 Sp Mid2") == ("spring", 2013)
    assert term_and_year("18L-1051 final") == (None, None)       # a roll number is not a year
    assert term_and_year("PF-Final Paper") == (None, None)


def test_build_skips_non_papers_and_merges_identical_files():
    files = [("a1", "PF/Past Papers/PF Final Fall 19.pdf"),
             ("a1", "PF/Copy/PF Final Fall 19.pdf"),
             ("b2", "PF/Lec-Fetch and Execute Cycle.pptx"),
             ("c3", "Data Structures/Finals/DS Final Spring 2020/Samples/Section A/x final.pdf"),
             ("d4", "PF Lab/Quizzes/Quiz 1 J.docx"),
             ("e5", "DB/Final/2024/DB Final Sol Spring 2024.pdf")]
    rows, skipped = build(files, "abc123")
    assert [(r["course"], r["component"], r["exam_type"], r["year"]) for r in rows] == [
        ("DB", "course", "final", 2024), ("PF", "course", "final", 2019), ("PF", "lab", "quiz", None)]
    assert rows[0]["has_solution"] and rows[1]["duplicate_paths"] == ["PF/Copy/PF Final Fall 19.pdf"]
    assert all(r["campus"] == "unknown" for r in rows) and len(skipped) == 2
    assert "%20" in rows[0]["url"]


def write_catalogue(tmp_path):
    rows, _ = build([
        ("1", "OOP/OOP (NEW Syllabus)/Final/OOP Final Spring 2024.pdf"),
        ("2", "OOP/OOP (NEW Syllabus)/Mid/OOP Mid II Spring-2024.pdf"),
        ("3", "OOP/OOP (NEW Syllabus)/Mid/OOP Mid I Solution Spring-2023.pdf"),
        ("4", "OOP/Mid/OOP mid paper.pdf"),
        ("5", "OOP Lab/OOP Lab/OOP Lab Final Spring 2019.pdf")], "abc")
    p = tmp_path / "cat.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_find_papers_filters(tmp_path):
    cat = write_catalogue(tmp_path)
    r = find_papers("Object Oriented Programming", "midterm", catalogue_path=cat)
    assert r["status"] == "found" and [x["exam_type"] for x in r["results"]] == ["mid2", "mid1", "mid"]
    assert [x["year"] for x in r["results"]] == [2024, 2023, "unknown"]
    r = find_papers("oop", "mid", year="Spring 2024", catalogue_path=cat)
    assert [x["file_name"] for x in r["results"]] == ["OOP Mid II Spring-2024.pdf"]
    assert any("no recorded year" in n for n in r["notes"])
    assert find_papers("OOP lab", "final", catalogue_path=cat)["results"][0]["component"] == "lab"
    assert find_papers("oop", "final", 2010, catalogue_path=cat)["status"] == "no_match"
    assert find_papers("Calculus", catalogue_path=cat)["status"] == "unsupported_course"
    assert find_papers("oop", "viva", catalogue_path=cat)["status"] == "clarification_needed"
