from conftest import fixture_html
from scope import tag_campus, tag_programme
from web_extract import extract
from web_fetch import get_source


def run(sid, fixture):
    return extract(fixture_html(fixture), get_source(sid))


def test_faq_one_section_per_card_and_updated_date():
    r = run("nu_admissions_faq", "faq")
    assert r["page_updated"] == "2026-06-03"
    assert [s["heading_path"][0] for s in r["sections"]] == [
        "How Can I Apply for Admission?", "What are the campus contact emails?",
        "Which admission tests are accepted for Fall 2026?"]
    first = r["sections"][0]["text"]
    assert first.startswith("Q: How Can I Apply") and "Important: Admission of a student" in first
    assert "Footer" not in r["clean_text"] and "Menu" not in r["clean_text"]


def test_commented_out_old_schedule_is_ignored():
    r = run("nu_admissions_schedule", "schedule")
    assert "Jun 30 (Tue)" in r["clean_text"]
    assert "Jun 26 (Fri)" not in r["clean_text"]          # only exists inside an HTML comment
    assert "Admission Application Submission - Undergraduate Programs: May 20" in r["clean_text"]
    assert r["sections"][0]["years_mentioned"] == ["2026"]


def test_check_mark_table_lists_campuses_and_splits_groups():
    r = run("nu_programmes_offered", "programmes")
    by_heading = {tuple(s["heading_path"]): s for s in r["sections"]}
    ug = by_heading[("Under-Graduate Programs",)]
    assert "Bachelor of Business Administration - offered at: Chiniot-Faisalabad, Islamabad, Lahore" in ug["text"]
    assert ug["programme"] == ["undergraduate"]
    assert by_heading[("Graduate Programs",)]["programme"] == ["graduate"]


def test_numbered_headings_nest_under_lettered_ones():
    r = run("nu_scholarships", "scholarship")
    paths = [s["heading_path"] for s in r["sections"]]
    assert ["A. Internal Sponsorships", "2. Study Loan"] in paths
    assert ["B. External Sponsorships", "5. HEC Scholarships for MS and PhD students"] in paths
    hec = r["sections"][-1]
    assert hec["programme"] == ["graduate", "phd"] and hec["campus"] == ["all"]


def test_lahore_page_keeps_contact_block():
    r = run("lhr_about_contacts", "lhr_about")
    assert "Tel: (042) 111 128 128" in r["clean_text"]
    assert all(s["campus"] == ["Lahore"] for s in r["sections"])


def test_scope_rules():
    assert tag_campus("Lahore and Islamabad campuses") == ["Lahore", "Islamabad"]
    assert tag_campus("applies to all campuses") == ["all"]
    assert tag_campus("no campus here") == ["unknown"]
    assert tag_programme("a graduate of this University is admitted") == ["unknown"]
    assert tag_programme("Under-Graduate Programs") == ["undergraduate"]
    assert tag_programme("BS and MS students") == ["undergraduate", "graduate"]
    assert tag_programme("the ms word in prose") == ["unknown"]
    assert tag_programme("fee rules", inherited=["phd"]) == ["phd"]


def test_heading_level_wins_and_faq_cards_do_not_inherit():
    html = """<div class='main-content'>
    <div class='faq-card'><div class='question'>Is HAT accepted for Graduate and Post Graduate admissions?</div>
      <div class='answer'>Yes: HAT-1 for MS, HAT-Subject for PhD</div></div>
    <div class='faq-card'><div class='question'>Can pre-medical students apply?</div>
      <div class='answer'>Yes. if Additional Mathematics is passed.</div></div>
    <div class='faq-card'><div class='question'>MS Admission Test (Computing Domain)</div>
      <div class='answer'>Selection: 50% Admission Test + 50% Bachelor<br>
        (Applicant must select <strong>only one</strong>
        option)</div></div></div>"""
    r = extract(html, get_source("nu_admissions_faq"))
    hat, premed, ms = r["sections"]
    assert hat["programme"] == ["graduate", "phd"]
    assert premed["programme"] == ["unknown"]
    assert ms["programme"] == ["graduate"]
    assert "(Applicant must select only one option)" in ms["text"]
    assert tag_programme("Post Graduate Admission") == ["phd"]
