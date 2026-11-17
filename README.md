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

## Layout
    data/raw/         original PDFs (never edited)
    data/processed/   cleaned, reviewed extraction
    ingestion/        extraction, cleaning, chunking
    rag/              retrieval and answer generation
    app/              Streamlit UI
    eval/             evaluation questions and results
    docs/             plans and review notes
