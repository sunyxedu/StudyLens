from __future__ import annotations

from studylens.config import Settings, get_settings
from studylens.errors import ConfigurationError
from studylens.retrieval.embeddings import HashEmbeddingClient, OpenAIEmbeddingClient
from studylens.retrieval.qa import OpenAIChatClient, RAGService, TemplateLLM
from studylens.retrieval.vector_store import QdrantVectorStore, SQLiteVectorStore, VectorStore


def build_vector_store(settings: Settings, *, dimensions: int) -> VectorStore:
    if settings.vector_store == "sqlite":
        return SQLiteVectorStore(settings.vector_db_path)
    if settings.vector_store == "qdrant":
        return QdrantVectorStore(
            collection_name=settings.qdrant_collection,
            dimensions=dimensions,
            path=settings.qdrant_path,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    raise ConfigurationError(f"Unsupported vector store: {settings.vector_store}")


def build_rag_service(settings: Settings | None = None) -> RAGService:
    settings = settings or get_settings()
    if settings.openai_api_key:
        embeddings = OpenAIEmbeddingClient(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
        )
        llm = OpenAIChatClient(api_key=settings.openai_api_key, model=settings.openai_chat_model)
    else:
        embeddings = HashEmbeddingClient()
        llm = TemplateLLM()

    vector_store = build_vector_store(settings, dimensions=embeddings.dimensions)
    return RAGService(embeddings=embeddings, vector_store=vector_store, llm=llm)
