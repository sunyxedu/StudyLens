# StudyLens

StudyLens is a learning assistant for Imperial Computing courses. It ingests course pages, materials, exercises, tutorials, video transcripts, EdStem scope notes, and past exams, then builds a retrieval layer for Q&A, cheatsheets, and predicted exam papers.

The repository is organized as a Python backend, a TypeScript web app, and a TypeScript browser extension:

- `src/studylens`: domain models, ingestion adapters, retrieval, generation, API, and CLI.
- `web`: StudyLens web workspace for indexing, retrieval, Q&A, cheatsheets, and predicted papers.
- `extension`: browser extension shell that calls the backend for page-level Q&A.
- `tests`: parser, retrieval, generation, API, and config tests.
- `data`: local runtime data. The contents are intentionally ignored by git.

Qdrant is the default vector store. Local development uses an embedded Qdrant database under `data/vector/qdrant`; production can point `STUDYLENS_QDRANT_URL` and `STUDYLENS_QDRANT_API_KEY` at a managed or self-hosted Qdrant instance.

## Setup

```bash
uv sync --extra dev --extra browser --extra documents
cp .env.example .env
```

Set credentials in `.env`. Do not hard-code Imperial, EdStem, or exam credentials.

To refresh browser login state:

```bash
uv run --extra browser playwright install chromium
uv run --extra browser studylens-save-browser-state
```

## Run the API

```bash
uv run uvicorn studylens.api.main:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

## Run Tests

```bash
uv run --extra dev pytest
```

## CLI

```bash
uv run studylens --help
uv run studylens inspect-scientia path/to/course.html
uv run studylens index-text COMP70001 notes.md
uv run studylens ask "What is dynamic programming?" --course-id COMP70001
```

## Web UI

```bash
cd web
npm install
npm run build
npm run dev
```

The web UI runs at `http://127.0.0.1:5173` and calls the backend at `http://localhost:8000`.
After `npm run build`, the API also serves the built UI from `http://localhost:8000/app`.

## Extension

```bash
cd extension
npm install
npm run build
```

Load `extension/dist` as an unpacked extension in a Chromium browser.
