# StudyLens

StudyLens is a learning assistant for Imperial Computing courses. It ingests course pages, materials, exercises, tutorials, video transcripts, EdStem scope notes, and past exams, then builds a retrieval layer for Q&A, cheatsheets, and predicted exam papers.

The repository is organized as a Python backend, a TypeScript web app, and a TypeScript browser extension:

- `src/studylens`: domain models, ingestion adapters, retrieval, generation, API, and CLI.
- `web`: StudyLens web workspace for indexing, retrieval, Q&A, cheatsheets, and predicted papers.
- `extension`: browser extension shell that calls the backend for page-level Q&A.
- `tests`: parser, retrieval, generation, API, and config tests.
- `data`: local runtime data. The contents are intentionally ignored by git.

Qdrant is the default vector store. Local development uses an embedded Qdrant database under `data/vector/qdrant`; production can point `QDRANT_URL` and `QDRANT_API_KEY` at a managed or self-hosted Qdrant instance. If the configured embedding dimensions change, StudyLens recreates the Qdrant collection because vectors from different embedding sizes cannot share one collection.

## Setup

```bash
uv sync --extra dev --extra browser --extra documents
cp .env.example .env
```

Set credentials in `.env`. Do not hard-code Imperial, EdStem, or exam credentials. `ANTHROPIC_API_KEY` is required for the Scientia timeline lookup — auto-index uses Claude (default `claude-sonnet-4-6`) to find your course on the live timeline HTML using only the course ID and title you provide.

StudyLens login accounts live in the configured SQLite database. Passwords are stored as PBKDF2 hashes, and the per-user Playwright browser state captured by the web setup flow is encrypted before it is written to SQLite. In local mode, StudyLens creates `data/auth/secret.key` if `AUTH_SECRET_KEY` is unset; production deployments must set `AUTH_SECRET_KEY` explicitly and use precise `ALLOWED_ORIGINS` values because session cookies are credentialed.

Panopto video discovery (and any future agent flow we add) is driven by a Claude Agent SDK loop that drives a Playwright `Page` through small tools — `goto`, `list_links`, `click_text`, etc. The SDK wraps the locally installed `claude` CLI, so make sure `claude` is on your PATH and logged in. Tune the loop with `AGENT_MODEL` and `AGENT_MAX_TURNS`; both Panopto navigation and any other crawl agent share the same settings.

To refresh the legacy file-based browser login state used by the CLI:

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
uv run studylens list-courses
uv run studylens auto-index COMP70001 "Advanced Algorithms"
uv run studylens index-exams COMP70001
uv run studylens index-edstem COMP70001 "Advanced Algorithms"
uv run studylens index-text COMP70001 notes.md --title "Lecture 1 Notes"
uv run studylens ask "What is dynamic programming?" --course-id COMP70001
```

`list-courses` runs an agent against the EdStem dashboard and prints the courses you're enrolled in this term (filtered to titles like `COMP 50002: ...`). Use those as inputs to `auto-index`.

`auto-index` runs the Scientia, Panopto, past-exams, and EdStem stages in one pass. Each stage is skipped cleanly when its credentials aren't configured (`BROWSER_STORAGE_STATE` for Scientia / Panopto / EdStem, `IMPERIAL_USERNAME` + `IMPERIAL_PASSWORD` for past exams). `index-exams` and `index-edstem` run those stages in isolation. Cheatsheet and predicted-paper generation auto-include any indexed EdStem scope notes; explicit `scope_notes` in the API request still override.

## Web UI

```bash
cd web
npm install
npm run build
npm run dev
```

The web UI runs at `http://127.0.0.1:5173` and calls the backend at `http://localhost:8000` by default.
After `npm run build`, the API also serves the built UI from `http://localhost:8000/app`.
For Vercel deployments, set `STUDYLENS_BACKEND_URL` to your Railway API origin, for example `https://your-api.up.railway.app`. On Railway, set `AUTH_SECRET_KEY` and include the exact Vercel origin in `ALLOWED_ORIGINS`; wildcard origins are rejected outside local mode because browser sessions use HttpOnly cookies.

The UI starts with Register and Login tabs. Register asks for username, grade, course, and password; Login asks for username and password only. After registration, it opens a browser setup flow for Scientia, Panopto, and EdStem; sign into each site in the opened browser window, then StudyLens saves the resulting cookies for that user. Once setup is complete, use `Process selected` to sync a course automatically. It downloads and indexes supported Scientia materials, exercises, and tutorials, then indexes Panopto video captions/transcripts. Captions are kept with timestamps and linked back to the video URL. `studylens index-text` remains available as a fallback for local notes or transcripts.

Scientia, Panopto, and EdStem all sit behind Imperial SSO. In the web UI, auto-indexing uses the encrypted per-user browser state saved in the database. In the CLI, auto-indexing still requires `BROWSER_STORAGE_STATE` to point at an authenticated Playwright storage state file. All three ingestion paths share a single browser context built from that state, so you only authenticate once per session.

## Extension

```bash
cd extension
npm install
npm run build
```

Load `extension/dist` as an unpacked extension in a Chromium browser. On Panopto / YouTube pages the popup switches into video mode automatically and restricts retrieval to indexed `transcript` chunks, so questions land against the lecture audio rather than slides or exercises.
