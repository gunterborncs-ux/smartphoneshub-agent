"""
Суммаризация новостей через OpenRouter.

Формат ответа — обычный текст с разделителем ===POST=== между постами.
Никакого JSON: устойчиво к BOM, unpaired surrogates в эмодзи, Unicode-пробелам,
неэкранированным control-символам и прочим «прелестям» LLM-вывода.
"""
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

Создай от 3 до 5 постов из новостей ниже.

Требования к каждому посту:
- Эмодзи в начале (📱, 🔥, 💡, 📸, ⚡, 🚀, 🎧, 📺)
- Заголовок на русском (до 80 символов)
- 2–3 предложения: факты, цифры, названия моделей
- В конце — ссылка точно в формате <a href="URL">Источник</a>
- Длина — до 600 символов
- Тон: информативный, нейтральный
- Всё на русском языке

ФОРМАТ ОТВЕТА:

===POST===

📱 Заголовок первого поста
Текст первого поста с фактами.
<a href="https://example.com/1">Источник</a>

===POST===

🔥 Заголовок второго поста
Текст второго поста.
<a href="https://example.com/2">Источник</a>

ПРАВИЛА:
- Между постами — строго строка ===POST=== (три знака =, слово POST, три знака =)
- НЕ используй JSON, markdown, нумерацию
- НЕ добавляй пояснений до или после постов
- Начни сразу с первого поста
- URL в ссылке должен быть из исходных данных (не выдумывай)
"""


# ---------- Утилиты очистки ----------

def _clean_unicode(text: str) -> str:
    """
    Нормализует строку:
    - собирает surrogate pairs в code points (📱 и т.п.)
    - удаляет lone surrogates, BOM, zero-width символы
    - заменяет нестандартные Unicode-пробелы на обычные
    """
    if not text:
        return text

    # Surrogate pairs → нормальные code points
    try:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")
    except (UnicodeDecodeError, UnicodeEncodeError):
        text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")

    # Удаляем BOM, zero-width, line/paragraph separators
    text = re.sub(r"[\u200b-\u200f\ufeff\u2028\u2029]", "", text)
    # Нестандартные пробелы → обычный пробел
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]", " ", text)

    return text


def _strip_thinking(text: str) -> str:
    """Убирает блоки размышлений, если модель их всё-таки вывела."""
    patterns = [
        re.compile(r"[\s\S]*?", re.IGNORECASE),
        re.compile(r"<thinking>[\s\S]*?</thinking>", re.IGNORECASE),
        re.compile(r"<reasoning>[\s\S]*?</reasoning>", re.IGNORECASE),
    ]
    for pat in patterns:
        text = pat.sub("", text)
    return text.strip()


def _clean_post(text: str) -> str:
    """Убирает служебные обёртки вокруг одного поста."""
    text = text.strip()

    # Markdown-обёртки
    text = re.sub(r"^```[\w]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)

    # JSON-остатки: "text": "..." или {"text": "..."
    text = re.sub(r'^\s*["\']?text["\']?\s*:\s*["\']?', "", text)
    text = re.sub(r'^\s*[\{\[]\s*["\']?text["\']?\s*:\s*["\']?', "", text)

    # Ведущие и хвостовые кавычки/скобки/запятые
    text = re.sub(r'^[\s"\'`\[\{\},:]+', "", text)
    text = re.sub(r'[\s"\'`\[\{\},:]+$', "", text)

    # Схлопываем множественные пробелы
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


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


# ---------- Разбор ответа на посты ----------

_DELIMITERS = [
    re.compile(r"===+\s*POST\s*===+", re.IGNORECASE),
    re.compile(r"---+\s*POST\s*---+", re.IGNORECASE),
    re.compile(r"---+\s*ПОСТ\s*---+", re.IGNORECASE),
    re.compile(r"\n\s*===+\s*\n"),
    re.compile(r"\n\s*---+\s*\n"),
]

_ANCHOR_RE = re.compile(r'<a\s+href="[^"]+"\s*>\s*Источник\s*</a>')


def _split_by_delimiter(raw: str) -> list[str] | None:
    """Пробует разбить текст по одному из разделителей."""
    for delim in _DELIMITERS:
        parts = delim.split(raw)
        if len(parts) >= 2:  # хотя бы один разделитель найден
            logger.info("Сработал разделитель: %s (частей: %d)",
                        delim.pattern, len(parts))
            return parts
    return None


def _split_by_anchors(raw: str) -> list[str]:
    """
    Fallback: разбивает текст по ссылкам <a href="...">Источник</a>.
    Каждый пост заканчивается такой ссылкой.
    """
    parts = _ANCHOR_RE.split(raw)
    posts: list[str] = []
    buffer = ""

    for i, part in enumerate(parts):
        if _ANCHOR_RE.fullmatch(part.strip()):
            buffer += part
            posts.append(buffer)
            buffer = ""
        else:
            buffer += part

    # Хвост без якоря — не пост, игнорируем
    return posts


def _parse_posts(raw: str) -> list[str]:
    """Полный конвейер: очистка → разбиение → валидация."""
    if not raw:
        return []

    raw = _clean_unicode(raw)
    logger.info("После _clean_unicode: %d символов", len(raw))
    logger.info("Первые 200 символов (repr): %r", raw[:200])

    raw = _strip_thinking(raw)
    if not raw:
        logger.error("После _strip_thinking текст пуст")
        return []

    parts = _split_by_delimiter(raw)
    if parts is None:
        logger.warning("Разделители не найдены — пробую anchor-fallback")
        parts = _split_by_anchors(raw)
        if not parts:
            logger.warning("Anchor-fallback не нашёл ни одного поста")
            return []

    posts: list[str] = []
    for i, part in enumerate(parts):
        cleaned = _clean_post(part)
        if _is_valid_post(cleaned):
            posts.append(cleaned)
        elif cleaned:
            logger.warning(
                "Пост #%d отклонён. Первые 120 символов: %r",
                i, cleaned[:120],
            )

    return posts


# ---------- Публичный API ----------

async def summarize_news(articles: list[dict]) -> list[str]:
    """Превращает новости в готовые посты через OpenRouter."""
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
            )

            raw = response.choices[0].message.content or ""
            logger.info("Сырой ответ (%d символов):\n%s", len(raw), raw)

            posts = _parse_posts(raw)
            if posts:
                logger.info("Модель %s: валидных постов %d", model, len(posts))
                return posts

            logger.warning("Модель %s: ни один пост не прошёл валидацию", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc, exc_info=True)

    logger.error("Все модели недоступны, посты не сгенерированы")
    return []
