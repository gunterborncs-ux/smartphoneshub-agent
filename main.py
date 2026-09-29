"""
Smartphones News Agent — точка входа.

Запускается по расписанию GitHub Actions, выполняет один цикл:
сбор RSS → фильтрация → дедупликация → суммаризация → публикация → сохранение истории.
"""
import asyncio
import logging
import re

import aiohttp
import feedparser
from aiogram import Bot

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    MAX_ARTICLES_PER_RUN,
    MAX_POSTS_PER_RUN,
    SIMILARITY_THRESHOLD,
    HISTORY_FILE,
    POST_DELAY_SECONDS,
)
from dedup import deduplicate
from rss_sources import RSS_FEEDS, RELEVANCE_KEYWORDS
from storage import load_history, save_history, filter_unpublished
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
    """Проверяет, относится ли статья к мобильной тематике."""
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    return any(kw.lower() in text for kw in RELEVANCE_KEYWORDS)


async def collect_all_news() -> list[dict]:
    """Параллельно собирает все RSS-ленты и фильтрует релевантные."""
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_rss(session, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    all_articles = [a for feed in results for a in feed]
    relevant = [a for a in all_articles if is_relevant(a)]
    logger.info("Собрано статей: %d, релевантных: %d", len(all_articles), len(relevant))
    return relevant


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


async def run_cycle() -> None:
    """Один полный цикл агента."""
    logger.info("=== Старт цикла ===")

    # --- 1. Сбор новостей ---
    articles: list[dict] = await collect_all_news()
    if not articles:
        logger.info("Нет новых статей, выход.")
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
        return

    # --- 4. Дедупликация ---
    articles = deduplicate(articles, threshold=SIMILARITY_THRESHOLD)
    logger.info("После дедупликации: %d", len(articles))

    if not articles:
        logger.info("После дедупликации не осталось статей.")
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
        logger.info("Нет валидных постов для публикации. История НЕ обновляется.")
        return

    # --- 7. Публикация ---
    bot = Bot(token=BOT_TOKEN)
    try:
        published = await publish_posts(bot, posts)
    finally:
        await bot.session.close()

    if not published:
        logger.warning("Ни один пост не опубликован — история не обновляется.")
        return

    # --- 8. Сохранение истории ---
    # Сохраняем ТОЛЬКО URL из реально опубликованных постов.
    # Это позволяет переработать «отброшенные» статьи в следующих циклах.
    new_urls = extract_urls(published)
    logger.info("Новых URL для истории: %d", len(new_urls))
    history.update(new_urls)
    save_history(HISTORY_FILE, history)

    logger.info("=== Цикл завершён ===")


def main() -> None:
    """Синхронная обёртка для asyncio.run()."""
    asyncio.run(run_cycle())


if __name__ == "__main__":
    main()
