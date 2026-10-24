"""
Smartphones News Agent.

Фото берётся из HTML статьи (og:image / JSON-LD / первое <img>).
Если HTML не дал фото (сайт блокирует GitHub Actions) и в config.py
USE_RSS_MEDIA_FALLBACK=True — берём картинку из RSS (media:content).
Ссылка — <a href="URL">Источник</a>.
"""
import asyncio
import json
import logging
import re
import traceback
from urllib.parse import urljoin, urlparse

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
    USE_RSS_MEDIA_FALLBACK,
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


# ---------- Утилиты ----------

def _is_valid_image_url(url: str) -> bool:
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


def _normalize_url_for_match(url: str) -> str:
    """Нормализует URL для сравнения: без схемы, www, trailing slash, query."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower().strip()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{netloc}{path}"


# ---------- Фото из HTML ----------

async def fetch_article_image(
    session: aiohttp.ClientSession, url: str,
) -> str | None:
    """
    Загружает HTML статьи и возвращает ОДНО фото.
    Приоритет: og:image → twitter:image → JSON-LD → первое <img>.
    Логирует HTTP-статус для диагностики.
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
                logger.warning(
                    "[HTML] %s → HTTP %s (возможно, сайт блокирует GitHub Actions)",
                    url, resp.status,
                )
                return None
            html = await resp.text(errors="ignore")
    except asyncio.TimeoutError:
        logger.warning("[HTML] %s → timeout", url)
        return None
    except Exception as exc:
        logger.warning("[HTML] %s → ошибка: %s", url, exc)
        return None

    logger.info("[HTML] %s → %d символов", url, len(html))
    soup = BeautifulSoup(html, "html.parser")

    # 1. og:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            content = tag.get("content", "").strip()
            if content:
                abs_url = urljoin(url, content)
                if _is_valid_image_url(abs_url):
                    logger.info("[HTML] %s → og:image: %s", url, abs_url[:100])
                    return abs_url

    # 2. twitter:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("twitter:image", "twitter:image:src"):
            content = tag.get("content", "").strip()
            if content:
                abs_url = urljoin(url, content)
                if _is_valid_image_url(abs_url):
                    logger.info("[HTML] %s → twitter:image: %s", url, abs_url[:100])
                    return abs_url

    # 3. JSON-LD
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
                    logger.info("[HTML] %s → JSON-LD: %s", url, abs_url[:100])
                    return abs_url

    # 4. Первое достаточно крупное <img>
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
                        p.strip().split(" ")
                        for p in srcset.split(",")
                        if p.strip()
                    ]
                    if candidates:
                        src = candidates[-1][0]
            if not src:
                continue
            abs_url = urljoin(url, src.strip())
            if _is_valid_image_url(abs_url):
                logger.info("[HTML] %s → <img>: %s", url, abs_url[:100])
                return abs_url

    logger.warning("[HTML] %s → фото не найдено", url)
    return None


# ---------- RSS (заголовки, ссылки, медиа для fallback) ----------

def _extract_rss_images(entry: dict) -> list[str]:
    """Собирает фото из media_content, media_thumbnail, enclosures."""
    images: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            images.append(url)

    for mc in (entry.get("media_content") or []):
        url = mc.get("url", "")
        mtype = mc.get("type", "")
        if url and (mtype.startswith("image/") or not mtype):
            _add(url)

    for mt in (entry.get("media_thumbnail") or []):
        _add(mt.get("url", ""))

    for enc in (entry.get("enclosures") or []):
        url = enc.get("href") or enc.get("url", "")
        mtype = enc.get("type", "")
        if url and mtype.startswith("image/"):
            _add(url)

    return images


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
                "rss_images": _extract_rss_images(entry),
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
    """Ищет статью по URL из <a href> с несколькими уровнями нормализации."""
    hrefs = set(_HREF_RE.findall(post))
    if not hrefs:
        return None

    # 1. Точный match
    for article in articles:
        if article.get("link") in hrefs:
            return article

    # 2. Нормализованный match (без схемы, www, trailing slash)
    post_norms = {_normalize_url_for_match(u) for u in hrefs}
    for article in articles:
        if _normalize_url_for_match(article.get("link", "")) in post_norms:
            return article

    logger.warning(
        "Статья не найдена по URL. hrefs=%s, articles=%d",
        list(hrefs)[:3], len(articles),
    )
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
    """Фото → текст."""
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
            logger.warning(
                "send_photo не удалось (%s). URL фото: %s",
                exc, image[:120],
            )

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
    """
    Приоритет фото:
      1. og:image из HTML
      2. Если HTML не дал и USE_RSS_MEDIA_FALLBACK=True — первое фото из RSS
    """
    published: list[str] = []
    async with aiohttp.ClientSession() as session:
        for post in posts:
            article = _match_article(post, articles)
            image: str | None = None
            source = "none"

            if article:
                image = await fetch_article_image(session, article["link"])
                if image:
                    source = "HTML"

                if not image and USE_RSS_MEDIA_FALLBACK:
                    rss_images = article.get("rss_images", [])
                    if rss_images:
                        image = rss_images[0]
                        source = "RSS-fallback"
                        logger.info(
                            "[MEDIA] HTML не дал → RSS-fallback: %s",
                            image[:100],
                        )
            else:
                logger.warning(
                    "Статья не сопоставлена — фото не будет",
                )

            logger.info(
                "[MEDIA] итог: source=%s, image=%s",
                source, (image[:80] + "...") if image else "None",
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
