"""
Smartphones News Agent.
Медиа (фото/видео) берётся ТОЛЬКО из RSS.
Никаких HTML-запросов, impit и плейсхолдеров.
"""
import asyncio
import logging
import re
import traceback
from urllib.parse import urljoin, urlparse

import aiohttp
import feedparser
from aiogram import Bot
from aiogram.types import InputMediaPhoto
from bs4 import BeautifulSoup

from config import (
    BOT_TOKEN, CHANNEL_ID, ADMIN_CHAT_ID,
    MAX_ARTICLES_PER_RUN, MAX_POSTS_PER_RUN,
    SIMILARITY_THRESHOLD, HISTORY_FILE,
    POST_DELAY_SECONDS, HISTORY_RETENTION_DAYS,
)
from dedup import deduplicate
from rss_sources import RSS_FEEDS, BRAND_MARKERS, PHONE_KEYWORDS, STOP_WORDS
from storage import (
    load_history, save_history, filter_unpublished, cleanup_old, add_urls,
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

_HREF_RE = re.compile(
    r'<a\s+href\s*=\s*["\']?([^"\'\s>]+)["\']?',
    re.IGNORECASE,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_JUNK_MARKERS = (
    "/pixel", "1x1", "spacer", "tracking", "beacon",
    "favicon", "/icon/", "avatar", "logo.svg", "/emoji/",
)

_MAX_PHOTOS_PER_POST = 10


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_valid_image_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    low = url.lower()
    if low.startswith(("data:", "blob:", "javascript:")):
        return False
    if any(m in low for m in _JUNK_MARKERS):
        return False
    if low.endswith(".svg") or ".svg?" in low:
        return False
    if not (low.startswith("http://") or low.startswith("https://")):
        return False
    return True


def _normalize_url_for_match(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return url.lower().strip()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{netloc}{p.path.rstrip('/')}"


def _url_fingerprint(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    segments = [s for s in p.path.split("/") if s]
    if not segments:
        return netloc
    return f"{netloc}/{segments[-1].split('.')[0]}"


# ---------- Извлечение медиа из RSS ----------

def _extract_rss_media(entry: dict, base_url: str) -> tuple[list[str], str | None]:
    """
    Собирает уникальные фото и видео из полей RSS:
    media_content, media_thumbnail, enclosures, <img> в summary/content.
    """
    images: list[str] = []
    seen: set[str] = set()
    video: str | None = None

    def _add_image(url: str) -> None:
        if not url or not isinstance(url, str):
            return
        url = url.strip()
        if not url:
            return
        if url.startswith("//"):
            url = "https:" + url
        elif not (url.startswith("http://") or url.startswith("https://")):
            url = urljoin(base_url, url)
        if url in seen:
            return
        if not _is_valid_image_url(url):
            return
        seen.add(url)
        images.append(url)

    def _set_video(url: str) -> None:
        nonlocal video
        if video or not url or not isinstance(url, str):
            return
        url = url.strip()
        if not url:
            return
        if url.startswith("//"):
            url = "https:" + url
        elif not (url.startswith("http://") or url.startswith("https://")):
            url = urljoin(base_url, url)
        video = url

    # media_content
    for mc in (entry.get("media_content") or []):
        url = mc.get("url", "")
        mtype = mc.get("type", "")
        if url and mtype.startswith("video/") and not video:
            _set_video(url)
        elif url and mtype.startswith("image/"):
            _add_image(url)

    # media_thumbnail
    for mt in (entry.get("media_thumbnail") or []):
        _add_image(mt.get("url", ""))

    # enclosures
    for enc in (entry.get("enclosures") or []):
        url = enc.get("href") or enc.get("url", "")
        mtype = enc.get("type", "")
        if url and mtype.startswith("video/") and not video:
            _set_video(url)
        elif url and mtype.startswith("image/"):
            _add_image(url)

    # <img> в summary
    summary = entry.get("summary", "") or ""
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', summary):
        _add_image(m.group(1))

    # <img> в content:encoded
    content = entry.get("content", "")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                val = c.get("value", "") or ""
                for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', val):
                    _add_image(m.group(1))

    return images[:_MAX_PHOTOS_PER_POST], video


async def fetch_rss(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "User-Agent": _UA,
                "Accept": "application/rss+xml,application/xml,text/xml,*/*",
            },
        ) as resp:
            if resp.status != 200:
                logger.warning("RSS %s → HTTP %s", url, resp.status)
                return []
            raw = await resp.read()
        feed = feedparser.parse(raw)
        articles: list[dict] = []
        for entry in feed.entries[:15]:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            images, video = _extract_rss_media(entry, link)
            articles.append({
                "title": title,
                "link": link,
                "summary": (entry.get("summary") or "").strip(),
                "rss_images": images,
                "rss_video": video,
            })
        logger.info(
            "RSS %s → %d статей (с фото: %d, с видео: %d)",
            url, len(articles),
            sum(1 for a in articles if a["rss_images"]),
            sum(1 for a in articles if a.get("rss_video")),
        )
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
    with_photo = sum(1 for a in relevant if a.get("rss_images"))
    with_video = sum(1 for a in relevant if a.get("rss_video"))
    logger.info(
        "Собрано: %d, релевантных: %d (с фото: %d, с видео: %d)",
        len(all_articles), len(relevant), with_photo, with_video,
    )
    return relevant


def _match_article(post: str, articles: list[dict]) -> dict | None:
    hrefs = set(_HREF_RE.findall(post))
    if not hrefs:
        logger.warning("[MATCH] нет <a href> в посте")
        return None

    for a in articles:
        if a.get("link") in hrefs:
            logger.info("[MATCH] точно: %s", a["link"][:80])
            return a

    post_norms = {_normalize_url_for_match(u) for u in hrefs}
    for a in articles:
        if _normalize_url_for_match(a.get("link", "")) in post_norms:
            logger.info("[MATCH] нормализованно: %s", a["link"][:80])
            return a

    post_fps = {_url_fingerprint(u) for u in hrefs}
    for a in articles:
        if _url_fingerprint(a.get("link", "")) in post_fps:
            logger.info("[MATCH] по отпечатку: %s", a["link"][:80])
            return a

    logger.warning(
        "[MATCH] не найдено. hrefs=%s, в статьях=%s",
        [h[:60] for h in list(hrefs)[:3]],
        [a.get("link", "")[:60] for a in articles[:3]],
    )
    return None


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


# ---------- Публикация (только RSS-медиа) ----------

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
            logger.info("[SEND] видео OK: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("[SEND] send_video упал: %s | URL: %s", exc, video[:120])

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
            logger.info("[SEND] альбом (%d фото): %s...", len(photos), post[:60])
            return True
        except Exception as exc:
            logger.warning("[SEND] альбом упал: %s", exc)

    if photos:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=photos[0], caption=post, parse_mode="HTML",
            )
            logger.info("[SEND] фото OK: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("[SEND] send_photo упал: %s | URL: %s", exc, photos[0][:120])

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID, text=post,
            parse_mode="HTML", disable_web_page_preview=True,
        )
        logger.info("[SEND] текст OK: %s...", post[:60])
        return True
    except Exception as exc:
        logger.error("[SEND] ошибка: %s", exc)
        return False


async def publish_posts(
    bot: Bot, posts: list[str], articles: list[dict],
) -> list[str]:
    published: list[str] = []
    for post in posts:
        article = _match_article(post, articles)
        images = article.get("rss_images", []) if article else []
        video = article.get("rss_video") if article else None

        logger.info(
            "[MEDIA] RSS: фото=%d, видео=%s",
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


async def run_cycle() -> None:
    logger.info("=== Старт цикла ===")
    bot = Bot(token=BOT_TOKEN)
    try:
        articles = await collect_all_news()
        if not articles:
            logger.warning("Нет релевантных статей")
            await notify_admin(bot, "🚨 <b>Не собрано релевантных статей</b>")
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
            if not any(m in p.lower() for m in _LEAK_MARKERS)
        ]
        posts = clean_posts[:MAX_POSTS_PER_RUN]

        if not posts:
            logger.error("Нет валидных постов")
            await notify_admin(
                bot, "🚨 <b>LLM не сгенерировала валидных постов</b>",
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

        logger.info("=== Цикл завершён: опубликовано %d ===", len(published))

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
