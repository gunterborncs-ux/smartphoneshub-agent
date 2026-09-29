"""
Семантическая дедупликация новостей.

Использует sentence-transformers (all-MiniLM-L6-v2) для поиска
семантически близких заголовков и удаления дубликатов.
"""
import numpy as np
from sentence_transformers import SentenceTransformer

_MODEL_NAME = "all-MiniLM-L6-v2"
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Ленивая инициализация модели (загружается один раз)."""
    global _model
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def deduplicate(articles: list[dict], threshold: float = 0.82) -> list[dict]:
    """
    Убирает дубликаты статей.

    Этап 1: точная дедупликация по URL.
    Этап 2: семантическая дедупликация по заголовкам (косинусная близость).

    :param articles: список статей с ключами title, link, summary
    :param threshold: порог близости (0.82 — оптимально для новостей)
    :return: отфильтрованный список
    """
    if not articles:
        return []

    # --- Этап 1: дедупликация по URL ---
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for a in articles:
        url = a.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(a)

    if len(unique) <= 1:
        return unique

    # --- Этап 2: семантическая дедупликация ---
    model = _get_model()
    titles = [a["title"] for a in unique]
    embeddings = model.encode(
        titles,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    keep: list[dict] = []
    kept_embeddings: list[np.ndarray] = []

    for i, article in enumerate(unique):
        emb = embeddings[i]
        is_dup = False
        for kept in kept_embeddings:
            # Косинусная близость = скалярное произведение
            # (эмбеддинги нормализованы)
            similarity = float(np.dot(emb, kept))
            if similarity > threshold:
                is_dup = True
                break
        if not is_dup:
            keep.append(article)
            kept_embeddings.append(emb)

    return keep