"""Turn one official web page (HTML) into clean, tagged sections.

Pure functions only (no network), so the scraper, the weekly refresh and the
live `check_official_page` tool all clean pages the same way.

What it does:
  1. Keeps only the page's main content (the selector from sources.json) and
     throws away scripts, styles, HTML comments, the title banner, breadcrumbs,
     nav and footer. (The Schedule page hides an OLD schedule inside an HTML
     comment - it must never be indexed.)
  2. Reads the "Updated on: 03-Jun-2026" stamp the nu.edu.pk pages show.
  3. Splits the page into sections:
       - FAQ pages: one section per question+answer card (kept whole)
       - other pages: a new section at every heading
  4. Writes tables as one readable line per row ("Program: BBA / BS; Fee: Rs. 12,000").
     Tables that use check-mark icons (Programs Offered) become
     "Bachelor of ... - offered at: Islamabad, Lahore".
  5. Tags every section with campus + programme level (see scope.py).
"""
import hashlib
import re
from datetime import datetime

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from scope import tag_campus, tag_programme

EXTRACTOR_VERSION = "web-clean-v1"
DROP_SELECTORS = [".page-title", ".breadcrumb", "nav", "footer", "header", ".footer-main", "form"]
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = {"p", "ul", "ol", "table", "div", "section", "article", "main", "blockquote", "dl"}
UPDATED_RE = re.compile(r"(?:Last\s+)?Updated\s+on\s*:?\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.I)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def clean_space(text: str) -> str:
    text = text.replace("\xa0", " ").replace("🔗", "")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def inline_text(el) -> str:
    """Text of an element on one line, keeping link targets that aren't just the visible text."""
    parts = []
    for node in el.descendants:
        if isinstance(node, Comment):
            continue
        if isinstance(node, NavigableString):
            parts.append(str(node))
        elif node.name == "br":
            parts.append(" ")
    return re.sub(r"\s+", " ", clean_space(" ".join(parts))).strip()


def links_in(el) -> list[dict]:
    out = []
    for a in el.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:")) or not href:
            continue
        out.append({"text": inline_text(a), "url": href})
    return out


# ---------- tables ----------

def _cells(tr):
    return [c for c in tr.find_all(["th", "td"], recursive=False)]


def _is_check(cell) -> bool:
    return cell.find(class_=re.compile(r"fa-check")) is not None


def table_to_lines(table) -> list[str]:
    """One line per row. Group rows (a single label across the table) come back as '## label'."""
    rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
    if not rows:
        return []
    header = []
    first = _cells(rows[0])
    if first and all(c.name == "th" for c in first):
        header = [inline_text(c) for c in first]
        rows = rows[1:]
    uses_checks = table.find(class_=re.compile(r"fa-check")) is not None

    lines = []
    if len(header) > 2 and header[0] and not uses_checks:
        lines.append(header[0] + ":")                 # e.g. "Tentative Admission Schedule 2026:"
    for tr in rows:
        cells = _cells(tr)
        texts = [inline_text(c) for c in cells]
        if not any(texts) and not any(_is_check(c) for c in cells):
            continue
        filled = [t for t in texts if t]
        if len(cells) == 1 or (len(filled) == 1 and len(header) > 2 and not uses_checks):
            lines.append(f"## {filled[0]}")
            continue
        if uses_checks and header and len(cells) == len(header):
            offered = [header[i] for i, c in enumerate(cells) if i > 0 and _is_check(c)]
            lines.append(f"{texts[0]} - offered at: {', '.join(offered) if offered else 'none listed'}")
        elif header and len(cells) == len(header) and len(header) > 2:
            rest = "; ".join(f"{h}: {t}" for h, t in zip(header[1:], texts[1:]) if t)
            lines.append(f"{texts[0]} - {rest}" if texts[0] else rest)
        elif header and len(cells) == len(header):
            lines.append("; ".join(f"{h}: {t}" if h else t for h, t in zip(header, texts) if t))
        else:
            lines.append(" | ".join(filled))
    return lines


# ---------- walking the content into blocks ----------

INLINE = {"a", "span", "strong", "b", "em", "i", "u", "sup", "sub", "small", "code", "abbr", "font", "label"}
BLOCK_LIKE = list(BLOCKS | HEADINGS | {"li", "tr"})


def _heading_like(el) -> str | None:
    """Real headings, accordion titles, and paragraphs that are only a short bold label."""
    if el.name in HEADINGS:
        return inline_text(el)
    cls = " ".join(el.get("class", []))
    if "panel-title" in cls:
        return inline_text(el)
    if el.name == "p":
        text = inline_text(el)
        bold = el.find(["strong", "b"])
        if bold and inline_text(bold) == text and 2 < len(text) <= 80 and text[-1] not in ".:":
            return text
    return None


def walk(el, out: list):
    """Append ("heading", text) / ("text", text) items in reading order.
    Runs of inline content (text, <strong>, <a>...) are joined into one line."""
    run = []

    def flush():
        t = re.sub(r"\s+", " ", clean_space(" ".join(run))).strip()
        if t:
            out.append(("text", t))
        run.clear()

    for child in el.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            run.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        if child.name == "br":
            flush()
            continue
        if child.name in INLINE and not child.find(BLOCK_LIKE):
            run.append(inline_text(child))
            continue
        flush()
        h = _heading_like(child)
        if h:
            out.append(("heading", h))
        elif child.name == "table":
            for line in table_to_lines(child):
                out.append(("heading", line[3:]) if line.startswith("## ") else ("text", line))
        elif child.name in {"ul", "ol"}:
            for li in child.find_all("li", recursive=False):
                out.append(("text", "- " + inline_text(li)))
        elif child.name == "p":
            t = inline_text(child)
            if t:
                out.append(("text", t))
        elif child.find(BLOCK_LIKE):
            walk(child, out)
        else:
            t = inline_text(child)
            if t:
                out.append(("text", t))
    flush()


# ---------- main entry ----------

def main_content(html: str, selector: str):
    soup = BeautifulSoup(html, "lxml")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for t in soup(["script", "style", "noscript", "svg", "img", "iframe"]):
        t.decompose()
    root = soup.select_one(selector)
    if root is None:
        raise ValueError(f"selector {selector!r} matched nothing - page layout changed?")
    for sel in DROP_SELECTORS:
        for t in root.select(sel):
            t.decompose()
    return soup, root


def page_updated(root) -> str | None:
    m = UPDATED_RE.search(root.get_text(" "))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _faq_sections(root):
    sections = []
    for card in root.select("div.faq-card"):
        q = card.select_one(".question")
        a = card.select_one(".answer")
        question = re.sub(r"^Q:\s*", "", inline_text(q)) if q else ""
        items = []
        if a:
            walk(a, items)
        body = "\n".join(t if k == "text" else t + ":" for k, t in items)
        body = re.sub(r"^A:\s*", "", body)
        sections.append({"heading_path": [question], "text": f"Q: {question}\nA: {body}".strip(),
                         "links": links_in(card)})
    return sections


def _heading_sections(root, title):
    items = []
    walk(root, items)
    sections, path, buf = [], [], []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append({"heading_path": list(path), "text": text, "links": []})
        buf.clear()

    for kind, text in items:
        if UPDATED_RE.fullmatch(text.strip()) or UPDATED_RE.match(text) and len(text) < 40:
            continue
        if kind == "heading":
            flush()
            if text.lower() == title.lower():
                path = []
                continue
            # a numbered sub-heading ("1. Merit ...") nests under a lettered/plain one ("A. Internal ...")
            if path and re.match(r"^\d+\.", text) and not re.match(r"^\d+\.", path[0]):
                path = [path[0], text]
            else:
                path = [text]
        else:
            buf.append(text)
    flush()
    return sections


def extract(html: str, source: dict) -> dict:
    """Return {page_updated, sections[...], clean_text, clean_hash} for one page."""
    _, root = main_content(html, source.get("selector", "body"))
    updated = page_updated(root)
    if source.get("kind") == "web_faq" and root.select("div.faq-card"):
        sections = _faq_sections(root)
    else:
        sections = _heading_sections(root, source["title"])
        # attach links per section by matching link text inside the section text (cheap, good enough)
        all_links = links_in(root)
        for s in sections:
            s["links"] = [l for l in all_links if l["text"] and l["text"] in s["text"]][:20]

    page_text = "\n".join(s["text"] for s in sections)
    page_campus = tag_campus(page_text, source.get("default_campus", "unknown"))
    for i, s in enumerate(sections):
        context = " ".join(s["heading_path"])
        s["section_index"] = i
        s["campus"] = tag_campus(context + "\n" + s["text"], source.get("default_campus", "unknown"))
        heading_levels = tag_programme(context, inherited=None, default="unknown")
        if heading_levels != ["unknown"]:
            s["programme"] = heading_levels            # "MS Admission ..." -> graduate, even if the body says "Bachelor"
        else:
            inherited = None if source.get("kind") == "web_faq" else _inherited_programme(sections, i)
            s["programme"] = tag_programme(s["text"], inherited=inherited,
                                           default=source.get("default_programme", "unknown"))
        s["text_hash"] = sha(s["text"])
        # years the section itself states (e.g. "Fall 2026", "w.e.f. Fall 2026") - used for freshness checks
        s["years_mentioned"] = sorted(set(re.findall(r"(?<!\d)(20\d\d)(?!\d)", context + " " + s["text"])))
    clean_text = "\n\n".join(" > ".join(s["heading_path"]) + "\n" + s["text"] for s in sections)
    return {
        "page_updated": updated,
        "page_campus": page_campus,
        "sections": sections,
        "clean_text": clean_text,
        "clean_hash": sha(clean_text),
        "extractor": EXTRACTOR_VERSION,
    }


def _inherited_programme(sections, i):
    """Programme level stated by the nearest earlier top-level heading (e.g. 'Undergraduate Programs')."""
    for s in reversed(sections[:i]):
        if s["heading_path"]:
            p = tag_programme(s["heading_path"][0], inherited=None, default="unknown")
            if p != ["unknown"]:
                return p
    return None
