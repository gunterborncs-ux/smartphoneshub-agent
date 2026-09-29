"""
Хранение истории опубликованных URL.

Используется JSON-файл, который коммитится обратно в репозиторий
GitHub Actions, чтобы сохранять состояние между запусками.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)


def load_history(path: str) -> set[str]:
    """Загружает множество опубликованных URL из JSON-файла."""
    if not os.path.exists(path):
        logger.info("Файл истории не найден: %s", path)
        return set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        logger.warning("Неожиданный формат истории: %s", type(data))
        return set()
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Ошибка чтения истории: %s", exc)
        return set()


def save_history(path: str, links: set[str]) -> None:
    """Сохраняет множество URL в JSON-файл."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(links), f, ensure_ascii=False, indent=2)
        logger.info("История сохранена: %d URL", len(links))
    except OSError as exc:
        logger.error("Ошибка сохранения истории: %s", exc)


def filter_unpublished(articles: list[dict], history: set[str]) -> list[dict]:
    """Убирает статьи, URL которых уже есть в истории."""
    return [a for a in articles if a.get("link") not in history]