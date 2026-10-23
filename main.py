"""
Smartphones News Agent.

Медиа — ровно одно фото из HTML статьи (приоритет og:image).
RSS — только источник заголовков и ссылок.
Ссылка в посте — <a href="URL">Источник</a>.
"""
import asyncio
import json
import logging
import re
import traceback
from urllib.parse import urljoin

import aiohttp
import feedparser
from aiogram import Bot
from bs4 import BeautifulSoup

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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_JUNK_MARKERS = (
    "/pixel", "1x1", "spacer", "tracking", "beacon",
    "favicon", "/icon/", "avatar", "logo.svg", "/emoji/",
)


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Одно фото из HTML ----------

def _is_valid_image_url(url: str) -> bool:
    """Отсеивает мусор: пиксели, иконки, data-URL, SVG."""
    if not url or not isinstance(url, str):
        return False
    low = url.lower()
    if low.startswith(("data:", "blob:", "javascript:")):
        return False
    if any(marker in low for marker in _JUNK_MARKERS):
        return False
    if low.endswith(".svg") or ".svg?" in low:
        return False
    return True


async def fetch_article_image(
    session: aiohttp.ClientSession, url: str,
) -> str | None:
    """
    Загружает HTML статьи и возвращает ОДНО изображение.

    Приоритет:
      1. og:image
      2. twitter:image
      3. JSON-LD image
      4. Первое достаточно крупное <img> из <article>/<main>
    """
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en;q=0.5",
            },
        ) as resp:
            if resp.status != 200:
                logger.warning("HTML %s → статус %s", url, resp.status)
                return None
            html = await resp.text(errors="ignore")
    except Exception as exc:
        logger.warning("Не удалось загрузить статью %s: %s", url, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1) og:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            content = tag.get("content", "")
            if content:
                abs_url = urljoin(url, content.strip())
                if _is_valid_image_url(abs_url):
                    return abs_url

    # 2) twitter:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("twitter:image", "twitter:image:src"):
            content = tag.get("content", "")
            if content:
                abs_url = urljoin(url, content.strip())
                if _is_valid_image_url(abs_url):
                    return abs_url

    # 3) JSON-LD
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            img = item.get("image")
            candidates: list[str] = []
            if isinstance(img, str):
                candidates = [img]
            elif isinstance(img, list):
                for x in img:
                    if isinstance(x, str):
                        candidates.append(x)
                    elif isinstance(x, dict) and x.get("url"):
                        candidates.append(x["url"])
            elif isinstance(img, dict) and img.get("url"):
                candidates = [img["url"]]

            for c in candidates:
                abs_url = urljoin(url, c.strip())
                if _is_valid_image_url(abs_url):
                    return abs_url

    # 4) Первое <img> в <article>/<main>
    containers = soup.find_all("article") or soup.find_all("main") or []
    for container in containers:
        for img in container.find_all("img"):
            w, h = img.get("width"), img.get("height")
            try:
                if w and int(w) < 300:
                    continue
                if h and int(h) < 200:
                    continue
            except (ValueError, TypeError):
                pass

            src = (
                img.get("src")
                or img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
            )
            if not src:
                srcset = img.get("srcset") or img.get("data-srcset") or ""
                if srcset:
                    candidates = [
                        part.strip().split(" ")
                        for part in srcset.split(",")
                        if part.strip()
                    ]
                    if candidates:
                        src = candidates[-1][0]

            if not src:
                continue
            abs_url = urljoin(url, src.strip())
            if _is_valid_image_url(abs_url):
                return abs_url

    return None


# ---------- RSS (только заголовки и ссылки) ----------

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
            articles.append({
                "title": title,
                "link": link,
                "summary": (entry.get("summary") or "").strip(),
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
    bot: Bot, post: str, image: str | None,
) -> bool:
    """Одно фото (sendPhoto) → текст (sendMessage)."""
    if image:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=image,
                caption=post,
                parse_mode="HTML",
            )
            logger.info("Пост с фото: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("Фото не отправилось, fallback на текст: %s", exc)

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
    """Для каждого поста загружает HTML и берёт ОДНО фото оттуда."""
    published: list[str] = []
    async with aiohttp.ClientSession() as session:
        for post in posts:
            article = _match_article(post, articles)
            image: str | None = None

            if article:
                logger.info("Загружаю HTML статьи %s", article["link"])
                image = await fetch_article_image(session, article["link"])
                if image:
                    logger.info("Из HTML извлечено фото: %s", image[:120])
                else:
                    logger.warning("HTML не дал фото: %s", article["link"])
            else:
                logger.warning(
                    "Не удалось сопоставить пост со статьёй — публикую без фото",
                )

            if await _send_single_post(bot, post, image):
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
