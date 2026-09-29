"""
Smartphones News Agent — точка входа.

Запускается по расписанию GitHub Actions, выполняет один цикл:
сбор RSS → фильтрация → дедупликация → суммаризация → публикация → сохранение истории.

Тематика: только смартфоны, ожидаемые новинки, прототипы.
"""
import asyncio
import logging
import re
import traceback

import aiohttp
import feedparser
from aiogram import Bot

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    ADMIN_CHAT_ID,
    MAX_ARTICLES_PER_RUN,
    MAX_POSTS_PER_RUN,
    SIMILARITY_THRESHOLD,
    HISTORY_FILE,
    POST_DELAY_SECONDS,
    HISTORY_RETENTION_DAYS,
)
from dedup import deduplicate
from rss_sources import (
    RSS_FEEDS,
    BRAND_MARKERS,
    PHONE_KEYWORDS,
    STOP_WORDS,
)
from storage import (
    load_history,
    save_history,
    filter_unpublished,
    cleanup_old,
    add_urls,
)
from summarizer import summarize_news

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent")

# --- Маркеры «утечки» reasoning-модели (страховка) ---
_LEAK_MARKERS = (
    "thinking process",
    "chain of thought",
    "analyze user input",
    "analyze the request",
    "here's a",
    "let me think",
    "мысленный процесс",
    "размышляю",
)

# --- Извлечение URL из HTML-ссылки в посте ---
_URL_RE = re.compile(r'<a\s+href="([^"]+)"')


# ---------- Уведомления админу ----------

async def notify_admin(bot: Bot, reason: str, details: str = "") -> None:
    """
    Отправляет уведомление админу в личку.
    Молча игнорирует ошибки — уведомление не должно ронять цикл.
    """
    if not ADMIN_CHAT_ID:
        logger.info("ADMIN_CHAT_ID не задан — уведомление пропущено")
        return

    text = f"⚠️ <b>Smartphones News Agent</b>\n\n{reason}"
    if details:
        text += f"\n\n<code>{details[:800]}</code>"

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("Уведомление отправлено админу (%s)", ADMIN_CHAT_ID)
    except Exception as exc:
        logger.error("Не удалось отправить уведомление админу: %s", exc)


# ---------- RSS ----------

async def fetch_rss(session: aiohttp.ClientSession, url: str) -> list[dict]:
    """Асинхронно забирает и парсит одну RSS-ленту."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            raw = await resp.read()
        feed = feedparser.parse(raw)
        return [
            {
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "").strip(),
                "summary": entry.get("summary", "").strip(),
            }
            for entry in feed.entries[:15]
            if entry.get("title") and entry.get("link")
        ]
    except Exception as exc:
        logger.warning("RSS %s: %s", url, exc)
        return []


def is_relevant(article: dict) -> bool:
    """
    Проверяет, относится ли статья к смартфонам/ожидаемым новинкам/прототипам.

    Логика:
    - Есть хотя бы один стоп-ворд → отклонить.
    - Есть маркер бренда смартфонов → принять.
    - Либо есть >= 1 маркер из PHONE_KEYWORDS → принять.
    - Иначе отклонить.
    """
    title = article.get("title", "").lower()
    summary = article.get("summary", "").lower()
    text = title + " " + summary

    # 1. Стоп-слова — жёсткое отклонение
    if any(stop in text for stop in STOP_WORDS):
        return False

    # 2. Маркеры брендов (Samsung, Xiaomi, ...)
    if any(brand in text for brand in BRAND_MARKERS):
        return True

    # 3. Общие ключевые слова про смартфоны / утечки / прототипы
    if any(kw in text for kw in PHONE_KEYWORDS):
        return True

    return False


async def collect_all_news() -> list[dict]:
    """Параллельно собирает все RSS-ленты и фильтрует релевантные."""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_rss(session, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    all_articles = [a for feed in results for a in feed]
    relevant = [a for a in all_articles if is_relevant(a)]
    logger.info("Собрано статей: %d, релевантных: %d", len(all_articles), len(relevant))
    return relevant


# ---------- Публикация ----------

async def publish_posts(bot: Bot, posts: list[str]) -> list[str]:
    """Публикует посты в Telegram-канал."""
    published: list[str] = []
    for post in posts:
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )
            published.append(post)
            logger.info("Опубликован пост: %s...", post[:60])
            await asyncio.sleep(POST_DELAY_SECONDS)
        except Exception as exc:
            logger.error("Ошибка публикации: %s", exc)
    return published


def extract_urls(posts: list[str]) -> set[str]:
    """Извлекает URL источников из опубликованных постов."""
    urls: set[str] = set()
    for post in posts:
        for match in _URL_RE.findall(post):
            urls.add(match)
    return urls


# ---------- Основной цикл ----------

async def run_cycle() -> None:
    """Один полный цикл агента."""
    logger.info("=== Старт цикла ===")

    bot = Bot(token=BOT_TOKEN)
    try:
        # --- 1. Сбор новостей ---
        articles: list[dict] = await collect_all_news()
        if not articles:
            logger.warning("RSS не вернули ни одной релевантной статьи.")
            await notify_admin(
                bot,
                "🚨 <b>Не собрано ни одной релевантной статьи</b>",
                "Проверь RSS-источники: возможно, ленты недоступны "
                "или ключевые слова не совпали.",
            )
            return

        # --- 2. Ограничение по количеству ---
        articles = articles[:MAX_ARTICLES_PER_RUN]
        logger.info("После ограничения по количеству: %d", len(articles))

        # --- 3. Фильтр по истории опубликованного ---
        history = load_history(HISTORY_FILE)
        articles = filter_unpublished(articles, history)
        logger.info("После фильтра по истории: %d", len(articles))

        if not articles:
            logger.info("Все статьи уже были опубликованы ранее.")
            await notify_admin(
                bot,
                "ℹ️ <b>Все статьи уже опубликованы</b>",
                f"RSS вернули только уже опубликованные URL "
                f"(в истории {len(history)} записей).",
            )
            return

        # --- 4. Дедупликация ---
        articles = deduplicate(articles, threshold=SIMILARITY_THRESHOLD)
        logger.info("После дедупликации: %d", len(articles))

        if not articles:
            logger.warning("После дедупликации не осталось статей.")
            await notify_admin(
                bot,
                "🚨 <b>После дедупликации не осталось статей</b>",
                "Все статьи похожи друг на друга. Попробуй понизить "
                "SIMILARITY_THRESHOLD в config.py (сейчас "
                f"{SIMILARITY_THRESHOLD}).",
            )
            return

        # --- 5. Суммаризация через OpenRouter ---
        posts: list[str] = await summarize_news(articles)
        logger.info("Сгенерировано постов: %d", len(posts))

        # --- 6. Страховка: отсекаем «утечки» reasoning-модели ---
        clean_posts: list[str] = []
        for post in posts:
            low = post.lower()
            if any(marker in low for marker in _LEAK_MARKERS):
                logger.warning("Отброшен пост с утечкой reasoning: %s", post[:80])
                continue
            clean_posts.append(post)

        posts = clean_posts[:MAX_POSTS_PER_RUN]
        logger.info("Постов после финального фильтра: %d", len(posts))

        if not posts:
            logger.error("LLM не сгенерировала ни одного валидного поста.")
            await notify_admin(
                bot,
                "🚨 <b>LLM не сгенерировала ни одного валидного поста</b>",
                f"Статей на входе: {len(articles)}. "
                f"Проверь логи GitHub Actions — вероятно, обе модели вернули "
                f"некорректный формат или упали с ошибкой.",
            )
            return

        # --- 7. Публикация ---
        published = await publish_posts(bot, posts)

        if not published:
            logger.error("Ни один пост не опубликован — Telegram вернул ошибки.")
            await notify_admin(
                bot,
                "🚨 <b>Ни один пост не удалось опубликовать</b>",
                f"Сгенерировано {len(posts)} постов, но Telegram вернул ошибку "
                f"на каждый. Проверь права бота в канале {CHANNEL_ID}.",
            )
            return

        # --- 8. Сохранение истории ---
        new_urls = extract_urls(published)
        logger.info("Новых URL для истории: %d", len(new_urls))

        # Добавляем новые URL с текущим таймстампом
        add_urls(history, new_urls)
        logger.info("История до очистки: %d записей", len(history))

        # Удаляем записи старше N дней
        history = cleanup_old(history, days=HISTORY_RETENTION_DAYS)
        logger.info("История после очистки: %d записей", len(history))

        save_history(HISTORY_FILE, history)

        logger.info("=== Цикл завершён: опубликовано %d постов ===", len(published))

    except Exception as exc:
        # Непредвиденная ошибка — тоже уведомляем
        tb = traceback.format_exc()
        logger.error("Необработанное исключение: %s", exc, exc_info=True)
        await notify_admin(
            bot,
            "🚨 <b>Необработанное исключение в цикле</b>",
            tb,
        )
    finally:
        await bot.session.close()


def main() -> None:
    """Синхронная обёртка для asyncio.run()."""
    asyncio.run(run_cycle())


if __name__ == "__main__":
    main()
