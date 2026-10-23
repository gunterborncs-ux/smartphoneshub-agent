"""
Smartphones News Agent — точка входа.

Публикует посты с медиа (фото/видео из RSS), источник — голой ссылкой.
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
from rss_sources import RSS_FEEDS, BRAND_MARKERS, PHONE_KEYWORDS, STOP_WORDS
from storage import (
    load_history,
    save_history,
    filter_unpublished,
    cleanup_old,
    add_urls,
)
from summarizer import summarize_news

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agent")

_LEAK_MARKERS = (
    "thinking process", "chain of thought", "analyze user input",
    "here's a", "let me think", "мысленный процесс", "размышляю",
)

# Извлекаем URL из поста — теперь и из голых ссылок
_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Извлечение медиа из RSS-записей ----------

def _extract_media(entry: dict) -> tuple[str | None, str | None]:
    """
    Возвращает (image_url, video_url).
    Пробует media_content, media_thumbnail, enclosures, <img> в summary.
    """
    image: str | None = None
    video: str | None = None

    for mc in (entry.get("media_content") or []):
        url = mc.get("url", "")
        mtype = mc.get("type", "")
        if not url:
            continue
        if mtype.startswith("video/") and not video:
            video = url
        elif mtype.startswith("image/") and not image:
            image = url

    if not image:
        for mt in (entry.get("media_thumbnail") or []):
            url = mt.get("url", "")
            if url:
                image = url
                break

    for enc in (entry.get("enclosures") or []):
        url = enc.get("href") or enc.get("url", "")
        mtype = enc.get("type", "")
        if not url:
            continue
        if mtype.startswith("video/") and not video:
            video = url
        elif mtype.startswith("image/") and not image:
            image = url

    if not image:
        summary = entry.get("summary", "") or ""
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
        if m:
            image = m.group(1)

    return image, video


# ---------- RSS ----------

async def fetch_rss(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            raw = await resp.read()
        feed = feedparser.parse(raw)
        articles: list[dict] = []
        for entry in feed.entries[:15]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            image, video = _extract_media(entry)
            articles.append({
                "title": title,
                "link": link,
                "summary": (entry.get("summary") or "").strip(),
                "image": image,
                "video": video,
            })
        return articles
    except Exception as exc:
        logger.warning("RSS %s: %s", url, exc)
        return []


def is_relevant(article: dict) -> bool:
    text = (article.get("title", "") + " " + article.get("summary", "")).lower()
    if any(stop in text for stop in STOP_WORDS):
        return False
    if any(brand in text for brand in BRAND_MARKERS):
        return True
    if any(kw in text for kw in PHONE_KEYWORDS):
        return True
    return False


async def collect_all_news() -> list[dict]:
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_rss(session, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    all_articles = [a for feed in results for a in feed]
    relevant = [a for a in all_articles if is_relevant(a)]
    logger.info("Собрано статей: %d, релевантных: %d", len(all_articles), len(relevant))
    return relevant


# ---------- Сопоставление поста с исходной статьёй ----------

def _match_article(post: str, articles: list[dict]) -> dict | None:
    """Находит исходную статью по URL из поста (для получения медиа)."""
    urls_in_post = set(_URL_RE.findall(post))
    if not urls_in_post:
        return None
    for article in articles:
        if article.get("link") in urls_in_post:
            return article
    # Нормализация: убираем trailing slash
    for article in articles:
        norm = (article.get("link") or "").rstrip("/")
        if not norm:
            continue
        for url in urls_in_post:
            if url.rstrip("/") == norm:
                return article
    return None


# ---------- Уведомления ----------

async def notify_admin(bot: Bot, reason: str, details: str = "") -> None:
    if not ADMIN_CHAT_ID:
        return
    text = f"⚠️ <b>Smartphones Hub</b>\n\n{reason}"
    if details:
        text += f"\n\n<code>{_html_escape(details[:800])}</code>"
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        logger.info("Уведомление отправлено админу")
    except Exception as exc:
        logger.error("Не удалось отправить уведомление: %s", exc)


# ---------- Публикация с медиа ----------

async def _send_single_post(
    bot: Bot, post: str, image: str | None, video: str | None,
) -> bool:
    """Отправляет пост: видео → фото → текст (с фолбэками)."""
    if video:
        try:
            await bot.send_video(
                chat_id=CHANNEL_ID, video=video, caption=post, parse_mode="HTML",
            )
            logger.info("Пост с видео: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("Видео не отправилось, fallback: %s", exc)

    if image:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=image, caption=post, parse_mode="HTML",
            )
            logger.info("Пост с фото: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("Фото не отправилось, fallback: %s", exc)

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID, text=post,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        logger.info("Текстовый пост: %s...", post[:60])
        return True
    except Exception as exc:
        logger.error("Ошибка публикации: %s", exc)
        return False


async def publish_posts(
    bot: Bot, posts: list[str], articles: list[dict],
) -> list[str]:
    published: list[str] = []
    for post in posts:
        article = _match_article(post, articles)
        image = article.get("image") if article else None
        video = article.get("video") if article else None
        if await _send_single_post(bot, post, image, video):
            published.append(post)
            await asyncio.sleep(POST_DELAY_SECONDS)
    return published


def extract_urls(posts: list[str]) -> set[str]:
    urls: set[str] = set()
    for post in posts:
        for m in _URL_RE.findall(post):
            urls.add(m)
    return urls


# ---------- Основной цикл ----------

async def run_cycle() -> None:
    logger.info("=== Старт цикла ===")
    bot = Bot(token=BOT_TOKEN)
    try:
        articles = await collect_all_news()
        if not articles:
            logger.warning("Нет релевантных статей")
            await notify_admin(bot, "🚨 <b>Не собрано ни одной релевантной статьи</b>")
            return

        articles = articles[:MAX_ARTICLES_PER_RUN]
        history = load_history(HISTORY_FILE)
        articles = filter_unpublished(articles, history)
        logger.info("После фильтра по истории: %d", len(articles))

        if not articles:
            logger.info("Все статьи уже опубликованы")
            return

        articles = deduplicate(articles, threshold=SIMILARITY_THRESHOLD)
        logger.info("После дедупликации: %d", len(articles))
        if not articles:
            return

        posts = await summarize_news(articles)
        logger.info("Сгенерировано постов: %d", len(posts))

        clean_posts = [
            p for p in posts
            if not any(marker in p.lower() for marker in _LEAK_MARKERS)
        ]
        posts = clean_posts[:MAX_POSTS_PER_RUN]

        if not posts:
            logger.error("Нет валидных постов")
            await notify_admin(
                bot,
                "🚨 <b>LLM не сгенерировала валидных постов</b>",
                f"Статей на входе: {len(articles)}",
            )
            return

        published = await publish_posts(bot, posts, articles)
        if not published:
            await notify_admin(bot, "🚨 <b>Ни один пост не опубликован</b>")
            return

        new_urls = extract_urls(published)
        add_urls(history, new_urls)
        history = cleanup_old(history, days=HISTORY_RETENTION_DAYS)
        save_history(HISTORY_FILE, history)

        logger.info("=== Цикл завершён: опубликовано %d постов ===", len(published))

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Необработанное исключение: %s", exc, exc_info=True)
        await notify_admin(bot, "🚨 <b>Необработанное исключение</b>", tb)
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run_cycle())


if __name__ == "__main__":
    main()ы
