"""
Smartphones News Agent.

Медиа (фото + видео) — только из RSS.
Ссылка в посте — <a href="URL">Источник</a>.
Фото дедуплицируются по нормализованному URL.
"""
import asyncio
import logging
import re
import traceback
from urllib.parse import parse_qs, urlparse, urlunparse

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.types import InputMediaPhoto

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

_HREF_RE = re.compile(r'<a\s+href="([^"]+)"', re.IGNORECASE)
_MAX_PHOTOS_PER_POST = 10


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Нормализация URL картинок ----------

_SIZE_QUERY_KEYS = {
    "w", "h", "width", "height", "q", "quality",
    "resize", "fit", "crop", "auto", "dpr", "scale",
    "size", "v", "ver", "version",
}

_SIZE_SUFFIX_RE = re.compile(
    r"""
    (?:[-_@](?:
        \d+x\d+
        |\d+w
        |\d+h
        |\d{2,4}
        |thumb|thumbnail
        |small|medium|large|big|mini
        |preview|scaled|resized
        |2x|3x
    ))+
    (?=\.(?:jpe?g|png|webp|gif|avif)$)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _normalize_image_url(url: str) -> str:
    """Канонический вид URL для дедупликации одинаковых фото в разных размерах."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower()

    qs = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in qs.items()
        if k.lower() not in _SIZE_QUERY_KEYS
    }
    new_query = "&".join(
        f"{k}={v[0]}" for k, v in sorted(filtered.items())
    )
    path = _SIZE_SUFFIX_RE.sub("", parsed.path).lower()

    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        new_query,
        "",
    ))


# ---------- Медиа из RSS ----------

def _extract_media(entry: dict) -> tuple[list[str], str | None]:
    """
    Собирает уникальные фото и (первое найденное) видео из полей RSS:
    media_content, media_thumbnail, enclosures, <img> в summary.
    """
    images: list[str] = []
    seen_norm: set[str] = set()
    video: str | None = None

    def _add(url: str) -> None:
        if not url:
            return
        norm = _normalize_image_url(url)
        if norm and norm not in seen_norm:
            seen_norm.add(norm)
            images.append(url)

    for mc in (entry.get("media_content") or []):
        url = mc.get("url", "")
        mtype = mc.get("type", "")
        if not url:
            continue
        if mtype.startswith("video/") and not video:
            video = url
        elif mtype.startswith("image/"):
            _add(url)

    for mt in (entry.get("media_thumbnail") or []):
        _add(mt.get("url", ""))

    for enc in (entry.get("enclosures") or []):
        url = enc.get("href") or enc.get("url", "")
        mtype = enc.get("type", "")
        if not url:
            continue
        if mtype.startswith("video/") and not video:
            video = url
        elif mtype.startswith("image/"):
            _add(url)

    summary = entry.get("summary", "") or ""
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', summary):
        _add(m.group(1))

    return images[:_MAX_PHOTOS_PER_POST], video


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
            images, video = _extract_media(entry)
            articles.append({
                "title": title,
                "link": link,
                "summary": (entry.get("summary") or "").strip(),
                "images": images,
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


# ---------- Матчинг пост → статья ----------

def _match_article(post: str, articles: list[dict]) -> dict | None:
    hrefs = set(_HREF_RE.findall(post))
    if not hrefs:
        return None
    for article in articles:
        if article.get("link") in hrefs:
            return article
    for article in articles:
        norm = (article.get("link") or "").rstrip("/")
        if not norm:
            continue
        for url in hrefs:
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


# ---------- Публикация ----------

async def _send_single_post(
    bot: Bot, post: str, images: list[str], video: str | None,
) -> bool:
    """
    Приоритет:
      1. Видео (sendVideo) — фото игнорируются.
      2. Альбом 2+ фото (sendMediaGroup).
      3. Одно фото (sendPhoto).
      4. Текст (sendMessage).
    """
    if video:
        try:
            await bot.send_video(
                chat_id=CHANNEL_ID, video=video, caption=post, parse_mode="HTML",
            )
            logger.info("Пост с видео: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("Видео не отправилось, fallback: %s", exc)

    photos = images[:_MAX_PHOTOS_PER_POST]
    if len(photos) >= 2:
        try:
            media = [
                InputMediaPhoto(
                    media=url,
                    caption=post if i == 0 else None,
                    parse_mode="HTML" if i == 0 else None,
                )
                for i, url in enumerate(photos)
            ]
            await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
            logger.info("Пост-альбом (%d фото): %s...", len(photos), post[:60])
            return True
        except Exception as exc:
            logger.warning("Альбом не отправился, fallback: %s", exc)

    if photos:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=photos[0], caption=post, parse_mode="HTML",
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
    """Медиа берётся из RSS-записи, соответствующей посту."""
    published: list[str] = []
    for post in posts:
        article = _match_article(post, articles)
        images = list(article.get("images", [])) if article else []
        video = article.get("video") if article else None

        logger.info(
            "Медиа для поста: фото=%d, видео=%s",
            len(images), "да" if video else "нет",
        )

        if await _send_single_post(bot, post, images, video):
            published.append(post)
            await asyncio.sleep(POST_DELAY_SECONDS)
    return published


def extract_urls(posts: list[str]) -> set[str]:
    urls: set[str] = set()
    for post in posts:
        for m in _HREF_RE.findall(post):
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
    main()
