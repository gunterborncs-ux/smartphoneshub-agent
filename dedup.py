"""
Семантическая дедупликация новостей.

Использует sentence-transformers (all-MiniLM-L6-v2) для поиска
семантически близких заголовков и удаления дубликатов.
"""
import logging

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def deduplicate(articles: list[dict], threshold: float = 0.82) -> list[dict]:
    if not articles:
        return []

    seen_urls: set[str] = set()
    unique: list[dict] = []
    for a in articles:
        url = a.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(a)

    if len(unique) <= 1:
        return unique

    model = _get_model()
    titles = [a["title"] for a in unique]
    embeddings = model.encode(
        titles, normalize_embeddings=True, show_progress_bar=False,
    )

    keep: list[dict] = []
    kept_embeddings: list[np.ndarray] = []

    for i, article in enumerate(unique):
        emb = embeddings[i]
        is_dup = False
        for kept in kept_embeddings:
            if float(np.dot(emb, kept)) > threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(article)
            kept_embeddings.append(emb)

    return keep
