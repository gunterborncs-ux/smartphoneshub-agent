"""
Суммаризация новостей через OpenRouter.

Использует JSON structured output для гарантированного формата
и жёсткую валидацию каждого поста перед возвратом.
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

SYSTEM_PROMPT = """Ты — редактор Telegram-канала @smartphoneshub о смартфонах.

Твоя задача — из списка новостей создать от 3 до 5 отдельных постов.

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
- Использовать какой-либо разделитель вместо JSON

Если нужно «подумать» — делай это во внутреннем скрытом канале,
в финальном ответе оставь только чистый JSON.
"""

# --- Регулярки для очистки возможных «утечек» ---

_THINKING_PATTERNS = [
    re.compile(r"[\s\S]*?", re.IGNORECASE),
    re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE),
    re.compile(r"<reasoning>[\s\S]*?</reasoning>", re.IGNORECASE),
]

# Предисловия вроде "Here's a thinking process:" до первого {
_PREAMBLE_RE = re.compile(r"^[\s\S]*?(?=\{)", re.MULTILINE)


def _strip_thinking(text: str) -> str:
    """Убирает блоки размышлений модели из ответа."""
    for pat in _THINKING_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


def _extract_json(text: str) -> dict | None:
    """
    Извлекает JSON-объект из ответа модели.
    Пытается найти первую { и последнюю } и распарсить.
    """
    text = _strip_thinking(text)

    # Прямая попытка
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Поиск JSON-объекта внутри текста
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        logger.error("Не удалось распарсить JSON: %s", exc)
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
    # Хотя бы одно слово на кириллице
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
                response_format={"type": "json_object"},
            )

            raw = response.choices[0].message.content or ""
            logger.debug("Сырой ответ модели (%d символов):\n%s", len(raw), raw[:1000])

            data = _extract_json(raw)
            if not data or "posts" not in data:
                logger.warning("Модель %s: нет поля 'posts' в ответе", model)
                continue

            posts_raw = data["posts"]
            if not isinstance(posts_raw, list):
                logger.warning("Модель %s: 'posts' не список", model)
                continue

            valid_posts: list[str] = []
            for item in posts_raw:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                text = text.strip()
                if _is_valid_post(text):
                    valid_posts.append(text)
                else:
                    logger.warning(
                        "Отклонён невалидный пост (первые 100 символов): %s",
                        text[:100],
                    )

            if valid_posts:
                logger.info(
                    "Модель %s: валидных постов %d из %d",
                    model, len(valid_posts), len(posts_raw),
                )
                return valid_posts

            logger.warning("Модель %s: ни один пост не прошёл валидацию", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc, exc_info=True)
            continue

    logger.error("Все модели недоступны, посты не сгенерированы")
    return []
