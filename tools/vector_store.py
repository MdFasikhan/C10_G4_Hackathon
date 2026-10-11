"""Runbook retrieval: markdown runbooks embedded locally and stored in ChromaDB."""
from __future__ import annotations

import re
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from tools.config import settings
from tools.logging_config import logger


class RunbookVectorStore:
    def __init__(self):
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(settings.chroma_path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        # We pass our own embeddings, so no default (ONNX) embedding function is needed.
        self.collection = self.client.get_or_create_collection(
            name="runbooks", metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        self._embedder = None

    @property
    def embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer  # heavy import (torch); load on first use

            logger.info("Loading embedding model: %s", settings.embedding_model)
            try:
                # Use the cached copy without contacting the Hugging Face Hub: its update check has no
                # timeout, and a slow Hub would otherwise freeze startup.
                self._embedder = SentenceTransformer(settings.embedding_model, local_files_only=True)
            except Exception:
                logger.info("Embedding model not cached yet; downloading it once")
                self._embedder = SentenceTransformer(settings.embedding_model)
        return self._embedder

    def sync_runbooks(self, runbook_dir: Path) -> int:
        """Index every *.md runbook (upsert), and remove chunks of runbooks that were deleted."""
        ids, documents, metadatas = [], [], []
        for path in sorted(runbook_dir.glob("*.md")):
            for i, chunk in enumerate(chunk_markdown(path.read_text(encoding="utf-8"), settings.runbook_chunk_chars)):
                ids.append(f"{path.stem}-{i}")
                documents.append(chunk)
                metadatas.append({"source": path.name, "chunk": i, "runbook": path.stem})

        if ids:
            embeddings = self.embedder.encode(documents, normalize_embeddings=True).tolist()
            self.collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
        stale = set(self.collection.get(include=[])["ids"]) - set(ids)
        if stale:
            self.collection.delete(ids=sorted(stale))
        logger.info("Runbook index synced: %d chunks from %s (%d stale removed)", len(ids), runbook_dir, len(stale))
        return len(ids)

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        count = self.collection.count()
        if count == 0:
            return []
        embedding = self.embedder.encode([query], normalize_embeddings=True).tolist()[0]
        result = self.collection.query(query_embeddings=[embedding], n_results=min(top_k or settings.retrieval_top_k, count))
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {"text": d, **m, "distance": round(dist, 4)}
            for d, m, dist in zip(docs, metas, distances)
            if dist <= settings.retrieval_max_distance
        ]


def chunk_markdown(content: str, size: int) -> list[str]:
    """Keep short runbooks whole (better context); split long ones by `## ` section, each prefixed with the title."""
    content = content.strip()
    if not content:
        return []
    if len(content) <= size:
        return [content]
    title_match = re.match(r"#\s+.+", content)
    title = title_match.group(0) if title_match else ""
    chunks = []
    for section in re.split(r"\n(?=## )", content):
        section = section.strip()
        if not section or section == title:
            continue
        text = section if section.startswith("# ") or not title else f"{title}\n\n{section}"
        chunks.extend(text[i:i + size] for i in range(0, len(text), size))
    return chunks


_store: RunbookVectorStore | None = None


def get_vector_store() -> RunbookVectorStore:
    global _store
    if _store is None:
        _store = RunbookVectorStore()
    return _store
