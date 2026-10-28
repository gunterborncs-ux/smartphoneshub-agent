"""
Суммаризация новостей о смартфонах через OpenRouter.
Формат: заголовок <b>, текст, ссылка <a href="URL">Источник</a>.
Жёсткая языковая валидация: кириллицы >= 40%.
"""
import logging
import re

import openai
from openai import AsyncOpenAI

from config import (
    OPENROUTER_KEY,
    OPENROUTER_BASE,
    MODELS_CHAIN,
)

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url=OPENROUTER_BASE,
    api_key=OPENROUTER_KEY,
)

SYSTEM_PROMPT = """You are the editor of a Russian-language Telegram channel @smartphoneshub about smartphones.

═══════════════════════════════════════════════════════════
🔴 TWO CRITICAL RULES — READ BEFORE ANYTHING ELSE 🔴
═══════════════════════════════════════════════════════════

RULE 1. OUTPUT LANGUAGE: RUSSIAN ONLY.
- Everything (headline, body) MUST be in Russian.
- If input news is English — translate it.
- Brand/model names (iPhone 17, Galaxy S25, Xiaomi 15) stay in Latin script.

RULE 2. HEADLINE MUST BE IN RUSSIAN.
- The <b>...</b> headline MUST be translated to Russian.
- DO NOT copy the source title verbatim.
- A post with an English headline will be automatically REJECTED.

═══════════════════════════════════════════════════════════
⚠️ HEADLINE TRANSLATION EXAMPLES ⚠️
═══════════════════════════════════════════════════════════

Source: "iQOO Neo 12 Roundup: Everything you need to know before launch"
❌ WRONG: <b>iQOO Neo 12 Roundup: Everything you need to know before launch</b>
✅ CORRECT: <b>iQOO Neo 12: всё, что известно перед запуском</b>

Source: "Samsung Galaxy S25 Ultra gets One UI 7.1 update"
❌ WRONG: <b>Samsung Galaxy S25 Ultra gets One UI 7.1 update</b>
✅ CORRECT: <b>Samsung Galaxy S25 Ultra получил One UI 7.1</b>

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
- INSTRUCTIONS / HOW-TO GUIDES (e.g. "How to reset...", "How to unlock...")
- PC HARDWARE / COMPONENTS (GPUs, CPUs, RAM, SSDs, graphics cards)

If fewer than 3 items match the topic, produce as many as you can.
NEVER pad the list with off-topic news.

═══════════════════════════════════════════════════════════
FORMAT REQUIREMENTS (per post)
═══════════════════════════════════════════════════════════
1. First line: one emoji + space + bold headline in <b>...</b>
   - Headline: RUSSIAN, max 80 chars
   - Emoji stays OUTSIDE <b>
2. Second line: empty
3. Then 2–3 sentences IN RUSSIAN with facts, numbers, model names
4. Last line: <a href="URL">Источник</a>
   - Label MUST be exactly "Источник" (Russian word)
   - Do NOT use bare URLs
5. Total post: up to 600 chars

═══════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════
Posts are separated by the exact line: ===POST===
Do NOT output JSON, markdown, numbering, or explanations.

═══════════════════════════════════════════════════════════
✅ CORRECT EXAMPLE
═══════════════════════════════════════════════════════════

===POST===

📱 <b>Samsung Galaxy S25 Ultra получил обновление One UI 7.1</b>

Прошивка добавляет новые функции камеры и улучшенную работу ИИ-ассистента. Обновление уже распространяется в Европе, в России ожидается до конца ноября.

<a href="https://example.com/article-1">Источник</a>

===POST===

🔥 <b>Xiaomi анонсировала Redmi Note 14 Pro</b>

Смартфон получил процессор Snapdragon 7s Gen 3, батарею 5500 мА·ч и 200-мегапиксельную камеру. Старт продаж — 15 ноября 2026 года.

<a href="https://example.com/article-2">Источник</a>

═══════════════════════════════════════════════════════════
❌ FORBIDDEN
═══════════════════════════════════════════════════════════
- English headline or body
- Copying the source title into <b>...</b>
- **markdown bold** instead of <b>...</b>
- Bare URLs without <a href="...">Источник</a> wrapper
- Any label other than "Источник" for the link
- Inventing URLs — use ONLY URLs from the input

Remember: HEADLINE IN RUSSIAN, link = <a href="URL">Источник</a>.
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


def _normalize_source_link(text: str) -> str:
    text = re.sub(
        r'<a\s+href\s*=\s*["\']?([^"\'\s>]+)["\']?[^>]*>[^<]*</a>',
        r'<a href="\1">Источник</a>',
        text,
        flags=re.IGNORECASE,
    )
    if "<a href=" not in text.lower():
        matches = list(re.finditer(r'https?://[^\s<>"\']+', text))
        if matches:
            last = matches[-1]
            url = last.group(0).rstrip(".,;:)")
            text = (
                text[:last.start()]
                + f'<a href="{url}">Источник</a>'
                + text[last.end():]
            )
    return text


def _clean_post(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[\w]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?i)(источник|source)\s*:\s*(https?://)", r"\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _normalize_source_link(text)
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


def _headline_is_russian(text: str) -> tuple[bool, str, float]:
    m = re.search(r'<b>(.+?)</b>', text, re.DOTALL)
    if not m:
        return False, "", 0.0
    headline = m.group(1).strip()
    ratio = _cyrillic_ratio(headline)
    return ratio >= 0.40, headline, ratio


def _is_valid_post(text: str, max_len: int = 600) -> bool:
    if not text or len(text) > max_len:
        return False
    stripped = text.strip()
    if not stripped or not _is_emoji(stripped[0]):
        return False

    headline_ok, headline, ratio = _headline_is_russian(stripped)
    if not headline_ok:
        logger.warning(
            "Пост отклонён: заголовок на английском (кириллица %.0f%%): %r",
            ratio * 100, headline[:100],
        )
        return False

    if '<a href=' not in stripped.lower():
        return False
    if "Источник" not in stripped:
        return False

    if _cyrillic_ratio(stripped) < 0.40:
        logger.warning(
            "Пост отклонён: кириллицы %.0f%% (<40%%): %r",
            _cyrillic_ratio(stripped) * 100, stripped[:100],
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
                "Пост #%d отклонён (len=%d): %r",
                i, len(cleaned), cleaned[:150],
            )
    return posts


async def _try_model(
    model: str,
    news_text: str,
    use_reasoning_off: bool,
) -> str | None:
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": news_text},
        ],
        "temperature": 0.3,
        "max_tokens": 8192,
    }
    if use_reasoning_off:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}

    try:
        logger.info("[LLM] %s (reasoning=%s)", model,
                    "off" if use_reasoning_off else "default")
        response = await _client.chat.completions.create(**kwargs)
    except openai.BadRequestError as exc:
        if use_reasoning_off:
            logger.warning("[LLM] %s: reasoning-параметр отклонён, повтор", model)
            return await _try_model(model, news_text, use_reasoning_off=False)
        logger.error("[LLM] %s: BadRequest: %s", model, exc)
        return None
    except openai.NotFoundError as exc:
        logger.error("[LLM] %s: 404: %s", model, exc)
        return None
    except openai.RateLimitError as exc:
        logger.warning("[LLM] %s: 429: %s", model, exc)
        return None
    except openai.APIError as exc:
        logger.error("[LLM] %s: API error: %s", model, exc)
        return None

    choice = response.choices[0]
    finish = getattr(choice, "finish_reason", "unknown")
    content = (choice.message.content or "").strip()

    if finish == "length" and not content:
        logger.warning("[LLM] %s: finish=length + пустой content", model)
        return None
    if not content:
        logger.warning("[LLM] %s: пустой content (finish=%s)", model, finish)
        return None

    logger.info("[LLM] %s: OK, %d символов", model, len(content))
    return content


async def summarize_news(articles: list[dict]) -> list[str]:
    if not articles:
        return []

    news_text = "\n\n".join(
        f"ИСХОДНАЯ НОВОСТЬ (перевести на русский, заголовок тоже!):\n"
        f"  Оригинальный заголовок: {a['title']}\n"
        f"  Описание: {a.get('summary', '')[:250]}\n"
        f"  Ссылка: {a['link']}"
        for a in articles[:30]
    )

    for model in MODELS_CHAIN:
        raw = await _try_model(model, news_text, use_reasoning_off=True)
        if not raw:
            continue

        posts = _parse_posts(raw)
        if posts:
            logger.info("[LLM] %s: валидных постов %d", model, len(posts))
            return posts

        logger.warning(
            "[LLM] %s: ответ есть (%d символов), но постов не получилось",
            model, len(raw),
        )

    logger.error("Все модели недоступны или вернули невалидные посты")
    return []
