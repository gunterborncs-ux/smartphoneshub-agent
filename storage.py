"""
Хранение истории опубликованных URL с датами.

Формат published.json:
{
    "https://example.com/news-1": "2026-10-24T09:15:00+00:00",
    "https://example.com/news-2": "2026-10-24T13:00:00+00:00"
}

Старый формат (список URL) мигрирует автоматически при загрузке.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def load_history(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        logger.info("Файл истории не найден: %s", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Ошибка чтения истории: %s", exc)
        return {}

    if isinstance(data, dict):
        clean: dict[str, str] = {}
        for url, ts in data.items():
            if isinstance(url, str) and isinstance(ts, str):
                clean[url] = ts
        logger.info("История загружена: %d записей", len(clean))
        return clean

    if isinstance(data, list):
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        migrated = {url: now_iso for url in data if isinstance(url, str)}
        logger.info("Миграция старого формата: %d URL", len(migrated))
        return migrated

    logger.warning("Неожиданный формат истории: %s", type(data).__name__)
    return {}


def save_history(path: str, history: dict[str, str]) -> None:
    try:
        ordered = {k: history[k] for k in sorted(history)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ordered, f, ensure_ascii=False, indent=2)
        logger.info("История сохранена: %d записей", len(ordered))
    except OSError as exc:
        logger.error("Ошибка сохранения истории: %s", exc)


def filter_unpublished(articles: list[dict], history: dict[str, str]) -> list[dict]:
    return [a for a in articles if a.get("link") not in history]


def cleanup_old(history: dict[str, str], days: int = 30) -> dict[str, str]:
    if days <= 0:
        return history

    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    kept: dict[str, str] = {}
    removed = 0
    skipped = 0

    for url, ts in history.items():
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            skipped += 1
            kept[url] = ts
            continue

        if dt >= threshold:
            kept[url] = ts
        else:
            removed += 1

    logger.info(
        "Очистка истории: удалено %d, оставлено %d (невалидных: %d)",
        removed, len(kept), skipped,
    )
    return kept


def add_urls(history: dict[str, str], urls: set[str] | list[str]) -> dict[str, str]:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for url in urls:
        if url and url not in history:
            history[url] = now_iso
    return history
