"""
Суммаризация новостей о смартфонах через OpenRouter.

SYSTEM_PROMPT написан на английском для лучшего следования инструкциям,
но с жёстким требованием: OUTPUT MUST BE IN RUSSIAN.
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

SYSTEM_PROMPT = """You are the editor of a Russian-language Telegram channel @smartphoneshub about smartphones.

═══════════════════════════════════════════════════════════
🔴 CRITICAL LANGUAGE RULE — READ BEFORE ANYTHING ELSE 🔴
═══════════════════════════════════════════════════════════
OUTPUT LANGUAGE: RUSSIAN ONLY.

- Even if the input news is in English (or any other language),
  you MUST translate it and write the post in RUSSIAN.
- Do NOT output English sentences. Do NOT mix languages.
- Brand names and model names (iPhone 17 Pro, Samsung Galaxy S25,
  Xiaomi 15 Ultra, Pixel 9) MAY stay in Latin script.
- Everything else — headlines, descriptions, verbs, adjectives,
  numbers-as-words — MUST be in Russian Cyrillic.
- If you are unsure how to translate a word, use a Russian
  equivalent or transliteration, never leave it in English.

A post written in English will be automatically REJECTED
and not published. There is no reward for English output.

═══════════════════════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════════════════════
From the news list below, produce 3 to 5 separate Telegram posts.

TOPIC — SMARTPHONES ONLY:
- New smartphone models, launches, announcements
- Leaks, rumors, renders of upcoming phones
- Prototypes and concept devices
- Reviews, comparisons, benchmarks
- Specs: chipset, camera, battery, display, price

IGNORE (do not create posts for):
- Headphones, earbuds, speakers, audio gear
- Smartwatches, bands, wearables
- Tablets, laptops, PCs
- Smart home, security cameras, robot vacuums
- Carrier tariffs, subscriptions, insurance
- Android Auto, CarPlay, automotive
- TVs, consoles, games
- Software (unless directly about a smartphone feature)

If fewer than 3 items match the topic, produce as many as you can.
NEVER pad the list with off-topic news just to reach 5 posts.

═══════════════════════════════════════════════════════════
FORMAT REQUIREMENTS (per post, all fields in RUSSIAN)
═══════════════════════════════════════════════════════════
1. First line: one emoji + space + bold headline wrapped in <b>...</b>
   - Headline: RUSSIAN, max 80 characters
   - Emoji stays OUTSIDE the <b> tag
2. Second line: empty line (blank separator)
3. Then 2–3 sentences IN RUSSIAN with facts, numbers, model names
4. Last line: bare URL (raw link, no wrapper)
   - Do NOT wrap URL in <a href="...">
   - Do NOT add the word "Источник" or any label
   - Do NOT put the URL in brackets
5. Total post length: up to 600 characters
6. Tone: neutral, informative, no clickbait

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════
Posts are separated by the exact line: ===POST===
Do NOT output JSON, markdown, numbering, or explanations.
Start directly with the first post.

═══════════════════════════════════════════════════════════
✅ CORRECT EXAMPLE (study the language and format)
═══════════════════════════════════════════════════════════

===POST===

📱 <b>Samsung Galaxy S25 Ultra получил обновление One UI 7.1</b>

Прошивка добавляет новые функции камеры и улучшенную работу ИИ-ассистента. Обновление уже распространяется в Европе, в России ожидается до конца ноября.

https://example.com/article-1

===POST===

🔥 <b>Xiaomi анонсировала Redmi Note 14 Pro</b>

Смартфон получил процессор Snapdragon 7s Gen 3, батарею 5500 мА·ч и 200-мегапиксельную камеру. Старт продаж — 15 ноября 2026 года.

https://example.com/article-2

═══════════════════════════════════════════════════════════
❌ FORBIDDEN
═══════════════════════════════════════════════════════════
- Writing posts in English (or any language other than Russian)
- Using **markdown bold** instead of <b>...</b>
- Wrapping the URL in <a href="...">
- Adding "Источник", "Source", or any label before the URL
- Mixing English and Russian in body text
- Adding comments before, between, or after posts
- Inventing URLs — use ONLY URLs from the input data

Remember: OUTPUT MUST BE IN RUSSIAN. Non-Russian posts are rejected.
"""


def _clean_unicode(text: str) -> str:
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
    text = text.strip()
    text = re.sub(r"^```[\w]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)

    # <a href="URL">текст</a> → голый URL
    text = re.sub(
        r'<a\s+href="([^"]+)"[^>]*>[^<]*</a>',
        r"\1",
        text,
        flags=re.IGNORECASE,
    )

    # **жирный** → <b>жирный</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Убираем случайный префикс "Источник: " перед голым URL
    text = re.sub(
        r"(?i)(источник|source)\s*:\s*(https?://)",
        r"\2",
        text,
    )

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x1F000 <= cp <= 0x1F2FF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
    )


def _cyrillic_ratio(text: str) -> float:
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    total = cyr + lat
    return cyr / total if total else 0.0


def _is_valid_post(text: str, max_len: int = 600) -> bool:
    """
    Пост валиден, если:
    - начинается с эмодзи
    - содержит <b>...</b> (заголовок)
    - содержит голый URL
    - кириллицы >= 40%
    - длина <= max_len
    """
    if not text or len(text) > max_len:
        return False
    stripped = text.strip()
    if not stripped or not _is_emoji(stripped[0]):
        return False
    if "<b>" not in stripped:
        return False
    if not re.search(r"https?://", stripped):
        return False

    ratio = _cyrillic_ratio(stripped)
    if ratio < 0.40:
        logger.warning(
            "Пост отклонён: кириллицы %.0f%% (<40%%). Начало: %r",
            ratio * 100, stripped[:100],
        )
        return False
    return True


_DELIMITER = re.compile(r"===+\s*POST\s*===+", re.IGNORECASE)


def _parse_posts(raw: str) -> list[str]:
    if not raw or len(raw) < 50:
        logger.warning("Ответ модели слишком короткий (%d символов)", len(raw))
        return []
    raw = _clean_unicode(raw)

    if not _DELIMITER.search(raw):
        logger.warning("Разделитель ===POST=== не найден")
        return []

    parts = _DELIMITER.split(raw)
    logger.info("Разделитель сработал, частей: %d", len(parts))

    posts: list[str] = []
    for i, part in enumerate(parts):
        cleaned = _clean_post(part)
        if _is_valid_post(cleaned):
            posts.append(cleaned)
        elif cleaned:
            logger.warning(
                "Пост #%d отклонён (len=%d, кириллица=%.0f%%): %r",
                i, len(cleaned), _cyrillic_ratio(cleaned) * 100,
                cleaned[:100],
            )
    return posts


async def summarize_news(articles: list[dict]) -> list[str]:
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
            choice = response.choices[0]
            finish = getattr(choice, "finish_reason", "unknown")
            raw = choice.message.content or ""
            logger.info("Сырой ответ (%d символов, finish=%s)", len(raw), finish)

            posts = _parse_posts(raw)
            if posts:
                logger.info("Модель %s: валидных постов %d", model, len(posts))
                return posts

            logger.warning("Модель %s: ни один пост не прошёл валидацию", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc, exc_info=True)

    logger.error("Все модели недоступны или вернули невалидные посты")
    return []
