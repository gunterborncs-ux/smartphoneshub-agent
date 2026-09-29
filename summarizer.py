"""
Суммаризация новостей через OpenRouter.

Использует structured output (JSON schema) для гарантированного формата.
Парсит ответ через json.loads(strict=False) + JSONDecoder.raw_decode —
это устойчиво к control-символам (неэкранированные \n, \t, \r),
которые часто встречаются в ответах LLM.
"""
import json
import logging
import re

from openai import AsyncOpenAI

from config import (
    OPENROUTER_KEY,
    OPENROUTER_BASE,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url=OPENROUTER_BASE,
    api_key=OPENROUTER_KEY,
)

SYSTEM_PROMPT = """Ты — редактор Telegram-канала @smartphoneshub о смартфонах и мобильных технологиях.

Твоя задача — из предоставленного списка новостей создать от 3 до 5 отдельных постов.

Требования к каждому посту:
- Эмодзи в начале (📱, 🔥, 💡, 📸, ⚡, 🚀, 🎧, 📺 и т.п.)
- Заголовок на русском языке (до 80 символов)
- Краткое описание в 2–3 предложениях: только факты, цифры, названия моделей
- В конце — ссылка в формате <a href="URL">Источник</a>
- Длина поста — до 600 символов
- Тон: информативный, нейтральный, без кликбейта
- Все новости переводи на русский язык

ФОРМАТ ОТВЕТА — строго JSON-объект вида:
{"posts": [{"text": "полный текст поста 1"}, {"text": "полный текст поста 2"}]}

ЗАПРЕЩЕНО:
- Добавлять размышления, chain-of-thought, анализ, "Here's a thinking process"
- Писать что-либо на английском языке
- Использовать markdown-обёртки ```json
- Добавлять комментарии до или после JSON
"""

# --- JSON Schema для structured output ---
POSTS_SCHEMA = {
    "name": "posts_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"}
                    },
                    "required": ["text"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["posts"],
        "additionalProperties": False
    }
}


def _strip_thinking(text: str) -> str:
    """Убирает возможные блоки размышлений модели."""
    patterns = [
        re.compile(r"[\s\S]*?", re.IGNORECASE),
        re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE),
        re.compile(r"<reasoning>[\s\S]*?</reasoning>", re.IGNORECASE),
    ]
    for pat in patterns:
        text = pat.sub("", text)
    return text.strip()


def _extract_json(text: str) -> dict | None:
    """
    Извлекает JSON-объект из ответа модели.

    Три стратегии:
    1. json.loads с strict=False — терпит неэкранированные control-символы.
    2. raw_decode — парсит первый валидный JSON и игнорирует хвост.
    3. Поиск { ... } в тексте — на случай преамбулы до JSON.
    """
    text = _strip_thinking(text)
    if not text:
        return None

    # Стратегия 1: прямой парсинг с strict=False
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass

    # Стратегия 2: raw_decode — парсит первый объект и игнорирует остаток
    try:
        decoder = json.JSONDecoder(strict=False)
        obj, _ = decoder.raw_decode(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Стратегия 3: ищем {...} внутри текста (если была преамбула)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1], strict=False)
        except json.JSONDecodeError as exc:
            logger.error("JSONDecodeError (fragment %d..%d): %s", start, end, exc)
            return None

    return None


def _is_emoji(ch: str) -> bool:
    """Грубая проверка на эмодзи по Unicode-диапазонам."""
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x1F000 <= cp <= 0x1F2FF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
    )


def _is_valid_post(text: str, max_len: int = 600) -> bool:
    """Проверяет, что пост соответствует требованиям канала."""
    if not text or len(text) > max_len:
        return False
    stripped = text.strip()
    if not stripped or not _is_emoji(stripped[0]):
        return False
    if "<a href=" not in stripped:
        return False
    if not re.search(r"[А-Яа-яЁё]", stripped):
        return False
    return True


async def summarize_news(articles: list[dict]) -> list[str]:
    """
    Превращает сырые новости в готовые посты для Telegram.
    Возвращает только валидные посты.
    """
    if not articles:
        return []

    news_text = "\n\n".join(
        f"Заголовок: {a['title']}\n"
        f"Описание: {a.get('summary', '')[:300]}\n"
        f"Ссылка: {a['link']}"
        for a in articles[:30]
    )

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            logger.info("Запрос к модели: %s", model)
            response = await _client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": news_text},
                ],
                temperature=0.5,
                max_tokens=2500,
                response_format={
                    "type": "json_schema",
                    "json_schema": POSTS_SCHEMA
                },
            )

            raw = response.choices[0].message.content or ""
            # Логируем ПОЛНЫЙ ответ, чтобы при сбое видеть реальный JSON
            logger.info("Сырой ответ (%d символов):\n%s", len(raw), raw)

            data = _extract_json(raw)
            if not data:
                logger.warning("Модель %s: не удалось распарсить JSON", model)
                continue

            if "posts" not in data:
                logger.warning(
                    "Модель %s: нет поля 'posts'. Ключи: %s",
                    model, list(data.keys()),
                )
                continue

            posts_raw = data["posts"]
            if not isinstance(posts_raw, list):
                logger.warning("Модель %s: 'posts' не список, а %s",
                               model, type(posts_raw).__name__)
                continue

            valid_posts: list[str] = []
            for item in posts_raw:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                text = text.strip()
                if _is_valid_post(text):
                    valid_posts.append(text)
                else:
                    logger.warning("Отклонён пост: %s...", text[:80])

            if valid_posts:
                logger.info(
                    "Модель %s: валидных постов %d из %d",
                    model, len(valid_posts), len(posts_raw),
                )
                return valid_posts

            logger.warning("Модель %s: ни один пост не прошёл валидацию", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc, exc_info=True)

    logger.error("Все модели недоступны, посты не сгенерированы")
    return []
