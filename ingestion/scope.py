"""Campus and programme tags for a section of text (PREPROCESSING_PLAN step 6).

Rules (deliberately simple and explainable):
  campus
    - every campus the text names explicitly -> those campuses
    - "all campuses" / "each campus" / all six named -> ["all"]
    - nothing named -> the source's default from sources.json
      ("all" for university-wide nu.edu.pk pages, "Lahore" for lhr.nu.edu.pk,
       "unknown" when we can't say)
  programme level (undergraduate / graduate / phd)
    - levels the text itself names
    - else the level of the heading it sits under (passed in as `inherited`)
    - else the source default, normally "unknown"

We never guess Lahore or undergraduate just because that is the chatbot's
default audience - unknown stays unknown.
"""
import re

CAMPUSES = {
    "Lahore": r"\blahore\b|\blhr\b",
    "Islamabad": r"\bislamabad\b|\bisb\b",
    "Karachi": r"\bkarachi\b|\bkhi\b",
    "Peshawar": r"\bpeshawar\b|\bpwr\b",
    "Chiniot-Faisalabad": r"\bchiniot\b|\bfaisalabad\b|\bcfd\b",
    "Multan": r"\bmultan\b|\bmtn\b",
}
ALL_CAMPUS_RE = re.compile(r"\b(all|each|every)\s+(of\s+the\s+\w+\s+)?campus(es)?\b", re.I)

# (case-insensitive words, case-sensitive codes) per level
PROGRAMMES = {
    "undergraduate": (r"\bunder[\s-]?graduate\b|\bbachelor|\bHSSC\b|\bintermediate\b|\bA[\s-]?levels?\b",
                      r"\bBS\b|\bBBA\b|\bBS\(|\bBSc\b"),
    "graduate": (r"(?<!under-)(?<!under)(?<!under )(?<!post-)(?<!post)(?<!post )\bgraduate\b(?![\s-]*(of|from)\b)|\bmaster'?s?\b|\bm\.?phil\b|\bHAT[\s-]*(1|2|general)\b",
                 r"\bMS\b|\bMBA\b|\bMS\("),
    "phd": (r"\bph\.?\s?d\b|\bdoctoral\b|\bpost[\s-]?graduate\b|\bHAT[\s-]*subject\b", r"(?!x)x"),
}


def tag_campus(text: str, default: str = "unknown") -> list[str]:
    found = [name for name, rx in CAMPUSES.items() if re.search(rx, text, re.I)]
    if len(found) == len(CAMPUSES) or (not found and ALL_CAMPUS_RE.search(text)):
        return ["all"]
    if found:
        return found
    return [default] if isinstance(default, str) else list(default)


def tag_programme(text: str, inherited: list[str] | None = None, default: str = "unknown") -> list[str]:
    found = [level for level, (words, codes) in PROGRAMMES.items()
             if re.search(words, text, re.I) or re.search(codes, text)]
    if found:
        return found
    if inherited:
        return list(inherited)
    return [default]
