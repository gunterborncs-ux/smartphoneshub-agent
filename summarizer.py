"""
Суммаризация новостей через OpenRouter.

Формат ответа — обычный текст с разделителем ===POST===.
Тематика: только смартфоны, ожидаемые новинки, прототипы.
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

# --- Компактный промпт: только смартфоны ---

SYSTEM_PROMPT = """Ты — редактор Telegram-канала @smartphoneshub.

Создай 3–5 постов ТОЛЬКО о смартфонах: анонсы, утечки, обзоры,
ожидаемые новинки, прототипы, даты презентаций, характеристики.

ИГНОРИРУЙ новости про: наушники, часы, планшеты, ноутбуки,
умный дом, камеры, тарифы операторов, Android Auto, ТВ, игры,
софт (если он не про смартфон напрямую).

Если подходящих новостей меньше 3 — сделай сколько есть.
Не добавляй посторонние новости ради количества.

Требования к каждому посту:
- Эмодзи в начале (📱, 🔥, 💡, 📸, ⚡, 🚀, 🎯, 🆕)
- Заголовок на русском (до 80 символов)
- 2–3 предложения: модели, характеристики, цифры, сроки
- В конце — ссылка: <a href="URL">Источник</a>
- До 600 символов
- Тон информативный, всё на русском

ФОРМАТ ОТВЕТА:

===POST===

📱 Заголовок первого поста
Текст первого поста.
<a href="URL1">Источник</a>

===POST===

🔥 Заголовок второго поста
Текст второго поста.
<a href="URL2">Источник</a>

Без JSON, без markdown, без пояснений до/после постов.
URL брать из исходных данных, не выдумывать.
"""


def _clean_unicode(text: str) -> str:
    """Убирает BOM, zero-width, surrogate pairs."""
    if not text:
        return text
    try:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")
    except (UnicodeDecodeError, UnicodeEncodeError):
        text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    text = re.sub(r"[\u200b-\u200f\ufeff\u2028\u2029]", "", text)
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]", " ", text)
    return text


def _clean_post(text: str) -> str:
    """Убирает служебные обёртки вокруг одного поста."""
    text = text.strip()
    text = re.sub(r"^```[\w]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_emoji(ch: str) -> bool:
    """Проверка на эмодзи по Unicode-диапазонам."""
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x1F000 <= cp <= 0x1F2FF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
    )


def _is_valid_post(text: str, max_len: int = 600) -> bool:
    """Пост должен начинаться с эмодзи, содержать <a href= и кириллицу."""
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


_DELIMITER = re.compile(r"===+\s*POST\s*===+", re.IGNORECASE)
_ANCHOR_RE = re.compile(r'<a\s+href="[^"]+"\s*>\s*Источник\s*</a>')


def _split_by_anchors(raw: str) -> list[str]:
    """Fallback: разбивает по <a>...</a>."""
    parts = _ANCHOR_RE.split(raw)
    posts: list[str] = []
    buffer = ""
    for part in parts:
        if _ANCHOR_RE.fullmatch(part.strip()):
            buffer += part
            posts.append(buffer)
            buffer = ""
        else:
            buffer += part
    return posts


def _parse_posts(raw: str) -> list[str]:
    """Очистка → разбиение → валидация."""
    if not raw or len(raw) < 30:
        logger.warning("Ответ модели слишком короткий (%d символов)", len(raw))
        return []

    raw = _clean_unicode(raw)
    logger.info("После _clean_unicode: %d символов", len(raw))

    if _DELIMITER.search(raw):
        parts = _DELIMITER.split(raw)
        logger.info("Разделитель ===POST=== сработал, частей: %d", len(parts))
    else:
        logger.warning("Разделитель не найден, anchor-fallback")
        parts = _split_by_anchors(raw)
        logger.info("Anchor-fallback вернул частей: %d", len(parts))

    posts: list[str] = []
    for i, part in enumerate(parts):
        cleaned = _clean_post(part)
        if _is_valid_post(cleaned):
            posts.append(cleaned)
        elif cleaned:
            logger.warning(
                "Пост #%d отклонён (len=%d): %r",
                i, len(cleaned), cleaned[:100],
            )

    return posts


async def summarize_news(articles: list[dict]) -> list[str]:
    """Превращает новости в готовые посты через OpenRouter."""
    if not articles:
        return []

    news_text = "\n\n".join(
        f"Заголовок: {a['title']}\n"
        f"Описание: {a.get('summary', '')[:250]}\n"
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
                temperature=0.3,
                max_tokens=4096,
            )

            # --- Диагностика: finish_reason и usage ---
            choice = response.choices[0]
            finish = getattr(choice, "finish_reason", "unknown")
            usage = getattr(response, "usage", None)
            if usage:
                logger.info(
                    "usage: prompt=%s, completion=%s, total=%s, finish=%s",
                    getattr(usage, "prompt_tokens", "?"),
                    getattr(usage, "completion_tokens", "?"),
                    getattr(usage, "total_tokens", "?"),
                    finish,
                )
            else:
                logger.info("finish_reason=%s (usage отсутствует)", finish)

            raw = choice.message.content or ""
            logger.info("Сырой ответ (%d символов):\n%s", len(raw), raw[:800])

            if finish == "length":
                logger.error(
                    "Модель %s упёрлась в max_tokens — увеличь лимит "
                    "или сократи входные данные", model,
                )

            posts = _parse_posts(raw)
            if posts:
                logger.info("Модель %s: валидных постов %d", model, len(posts))
                return posts

            logger.warning("Модель %s: ни один пост не прошёл валидацию", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc, exc_info=True)

    logger.error("Все модели недоступны, посты не сгенерированы")
    return []
