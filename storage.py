"""
Хранение истории опубликованных URL.

Формат published.json:
{
    "https://example.com/news-1": "2026-09-29T09:15:00+00:00",
    "https://example.com/news-2": "2026-09-29T13:00:00+00:00"
}

Старый формат (список URL) поддерживается — при загрузке он
автоматически конвертируется в словарь с текущей датой.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# ---------- Загрузка / сохранение ----------

def load_history(path: str) -> dict[str, str]:
    """
    Загружает историю из JSON-файла.

    Возвращает словарь {url: iso_timestamp}.
    Поддерживает миграцию из старого формата (список URL).
    """
    if not os.path.exists(path):
        logger.info("Файл истории не найден: %s", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Ошибка чтения истории: %s", exc)
        return {}

    # --- Новый формат: dict ---
    if isinstance(data, dict):
        # Валидация: только строковые ключи и строковые значения
        clean: dict[str, str] = {}
        for url, ts in data.items():
            if isinstance(url, str) and isinstance(ts, str):
                clean[url] = ts
        logger.info("История загружена: %d записей", len(clean))
        return clean

    # --- Старый формат: list[str] ---
    if isinstance(data, list):
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        migrated = {url: now_iso for url in data if isinstance(url, str)}
        logger.info(
            "Миграция старого формата: %d URL получили текущую дату",
            len(migrated),
        )
        return migrated

    logger.warning("Неожиданный формат истории: %s", type(data).__name__)
    return {}


def save_history(path: str, history: dict[str, str]) -> None:
    """Сохраняет историю в JSON-файл (сортировка по URL для стабильных diff'ов)."""
    try:
        # Сортировка ключей: стабильные коммиты в GitHub
        ordered = {k: history[k] for k in sorted(history)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ordered, f, ensure_ascii=False, indent=2)
        logger.info("История сохранена: %d записей", len(ordered))
    except OSError as exc:
        logger.error("Ошибка сохранения истории: %s", exc)


# ---------- Фильтрация ----------

def filter_unpublished(articles: list[dict], history: dict[str, str]) -> list[dict]:
    """Убирает статьи, URL которых уже есть в истории."""
    return [a for a in articles if a.get("link") not in history]


# ---------- Очистка по возрасту ----------

def cleanup_old(
    history: dict[str, str],
    days: int = 30,
) -> dict[str, str]:
    """
    Удаляет записи старше `days` дней.

    :param history: словарь {url: iso_timestamp}
    :param days: срок хранения в днях (0 или отрицательное = не чистить)
    :return: отфильтрованный словарь
    """
    if days <= 0:
        logger.info("HISTORY_RETENTION_DAYS=%d — очистка отключена", days)
        return history

    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    logger.info("Порог очистки: %s (старше %d дней)",
                threshold.isoformat(timespec="seconds"), days)

    kept: dict[str, str] = {}
    removed = 0
    skipped = 0

    for url, ts in history.items():
        try:
            # Парсим ISO-8601. Заменяем 'Z' на '+00:00' для совместимости.
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            # Наивные даты (без tz) считаем UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            # Невалидный timestamp — считаем запись «свежей» (не удаляем)
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


def add_urls(
    history: dict[str, str],
    urls: set[str] | list[str],
) -> dict[str, str]:
    """
    Добавляет URL в историю с текущим UTC-таймстампом.

    Существующие записи не перезаписываются (сохраняют исходную дату).
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for url in urls:
        if url and url not in history:
            history[url] = now_iso
    return history
