# StudyLens Architecture

StudyLens is split into four bounded areas:

1. Ingestion adapters collect Imperial course artifacts. The auto-index pipeline discovers Scientia courses, downloads supported materials/exercises/tutorials, extracts text, and indexes chunks. Site-specific login and browser automation stays in `studylens.ingestion`.
2. Document processing extracts text and creates stable `DocumentChunk` objects.
3. Retrieval embeds chunks and stores them in Qdrant by default, with course/resource metadata as payload filters.
4. Generation uses retrieved context to answer questions, create two-page LaTeX cheatsheets, and draft predicted exam papers.
5. The web UI in `web/` provides the main local workspace; the browser extension stays focused on page-level Q&A.

The browser extension should never scrape or store credentials. It sends page context and questions to the backend API.
