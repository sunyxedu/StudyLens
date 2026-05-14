# StudyLens

StudyLens is a learning assistant for Imperial Computing courses. It ingests course pages, materials, exercises, tutorials, video transcripts, EdStem scope notes, and past exams, then builds a retrieval layer for Q&A, cheatsheets, and predicted exam papers.

The repository is organized as a Python backend plus a TypeScript browser extension:

- `src/studylens`: domain models, ingestion adapters, retrieval, generation, API, and CLI.
- `extension`: browser extension shell that calls the backend for page-level Q&A.
- `tests`: parser, retrieval, generation, API, and config tests.
- `data`: local runtime data. The contents are intentionally ignored by git.

Qdrant is the default vector store. Local development uses an embedded Qdrant database under `data/vector/qdrant`; production can point `STUDYLENS_QDRANT_URL` and `STUDYLENS_QDRANT_API_KEY` at a managed or self-hosted Qdrant instance.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set credentials in `.env`. Do not hard-code Imperial, EdStem, or exam credentials.

## Run the API

```bash
uvicorn studylens.api.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

## Run Tests

```bash
pytest
```

## CLI

```bash
studylens --help
studylens inspect-scientia path/to/course.html
studylens index-text COMP70001 notes.md
studylens ask COMP70001 "What is dynamic programming?"
```

## Extension

```bash
cd extension
npm install
npm run build
```

Load `extension/dist` as an unpacked extension in a Chromium browser.
