# StudyLens Architecture

StudyLens is split into four bounded areas:

1. Ingestion adapters collect Imperial course artifacts. Scientia, Panopto, and EdStem all sit behind Imperial SSO; all three share a single `BrowserSession` (a Playwright `BrowserContext` loaded from a saved storage state). Static HTML / PDF fetches go through the context's `request` API; DOM-driven flows open a `Page` from the same context so cookies and redirects stay consistent. The auto-index pipeline discovers Scientia courses, downloads supported materials/exercises/tutorials, extracts text, and indexes chunks. It also searches Panopto (caption/subtitle indexing with timestamps, falling back to video transcription when captions are unavailable and a downloadable video plus OpenAI key are available) and pulls past exam papers from `exams.doc.ic.ac.uk` over HTTP Basic auth (using `STUDYLENS_IMPERIAL_USERNAME` / `STUDYLENS_IMPERIAL_PASSWORD`). Each ingestion stage skips cleanly when its credentials aren't configured.
2. Document processing extracts text and creates stable `DocumentChunk` objects.
3. Retrieval embeds chunks and stores them in Qdrant by default, with course/resource metadata as payload filters.
4. Generation uses retrieved context to answer questions, create two-page LaTeX cheatsheets, and draft predicted exam papers.
5. The web UI in `web/` provides the main local workspace; the browser extension stays focused on page-level Q&A.

The ingestion pipeline is async end-to-end; the API and CLI cross the sync/async boundary with FastAPI async handlers and `asyncio.run` respectively. The browser extension never scrapes or stores credentials; it sends page context and questions to the backend API.
