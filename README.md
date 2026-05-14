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
uv run studylens auto-index COMP70001 --course-title "Advanced Algorithms"
uv run studylens index-exams COMP70001
uv run studylens index-edstem COMP70001 "Advanced Algorithms"
uv run studylens index-text COMP70001 notes.md --title "Lecture 1 Notes"
uv run studylens ask "What is dynamic programming?" --course-id COMP70001
```

`auto-index` runs the Scientia, Panopto, past-exams, and EdStem stages in one pass. Each stage is skipped cleanly when its credentials aren't configured (`STUDYLENS_BROWSER_STORAGE_STATE` for Scientia / Panopto / EdStem, `STUDYLENS_IMPERIAL_USERNAME` + `STUDYLENS_IMPERIAL_PASSWORD` for past exams). `index-exams` and `index-edstem` run those stages in isolation. Cheatsheet and predicted-paper generation auto-include any indexed EdStem scope notes; explicit `scope_notes` in the API request still override.

## Web UI

```bash
cd web
npm install
npm run build
npm run dev
```

The web UI runs at `http://127.0.0.1:5173` and calls the backend at `http://localhost:8000`.
After `npm run build`, the API also serves the built UI from `http://localhost:8000/app`.

In the UI, use `Index` to sync a course automatically. It downloads and indexes supported Scientia materials, exercises, and tutorials, then indexes Panopto video captions/transcripts. Captions are kept with timestamps and linked back to the video URL. `studylens index-text` remains available as a fallback for local notes or transcripts.

Scientia, Panopto, and EdStem all sit behind Imperial SSO, so auto-indexing requires `STUDYLENS_BROWSER_STORAGE_STATE` to point at an authenticated Playwright storage state file (refresh with `studylens-save-browser-state` as below). All three ingestion paths share a single browser context built from that state, so you only authenticate once per session.

## Extension

```bash
cd extension
npm install
npm run build
```

Load `extension/dist` as an unpacked extension in a Chromium browser. On Panopto / YouTube pages the popup switches into video mode automatically and restricts retrieval to indexed `transcript` chunks, so questions land against the lecture audio rather than slides or exercises.
