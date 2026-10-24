"""
Smartphones News Agent.

Фото: HTML (og:image) → RSS-fallback (media:content) при блокировке HTML.
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

# Универсальный regex для <a href="URL">, <a href='URL'>, <a href=URL>
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


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- Утилиты ----------

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


# ---------- Фото из HTML ----------

async def fetch_article_image(
    session: aiohttp.ClientSession, url: str,
) -> str | None:
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
                logger.warning("[HTML] %s → HTTP %s", url, resp.status)
                return None
            html = await resp.text(errors="ignore")
    except Exception as exc:
        logger.warning("[HTML] %s → %s", url, exc)
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1. og:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            c = tag.get("content", "").strip()
            if c:
                u = urljoin(url, c)
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → og:image", url)
                    return u

    # 2. twitter:image
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("twitter:image", "twitter:image:src"):
            c = tag.get("content", "").strip()
            if c:
                u = urljoin(url, c)
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → twitter:image", url)
                    return u

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
                u = urljoin(url, c.strip())
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → JSON-LD", url)
                    return u

    # 4. Первое <img>
    for container in (soup.find_all("article") or soup.find_all("main") or []):
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
                    parts = [p.strip().split(" ") for p in srcset.split(",") if p.strip()]
                    if parts:
                        src = parts[-1][0]
            if not src:
                continue
            u = urljoin(url, src.strip())
            if _is_valid_image_url(u):
                logger.info("[HTML] %s → <img>", url)
                return u

    logger.warning("[HTML] %s → фото не найдено", url)
    return None


# ---------- RSS: медиа для fallback ----------

def _extract_rss_images(entry: dict) -> list[str]:
    """
    Собирает все возможные фото из RSS: media_content, media_thumbnail,
    enclosures, <img> в summary, <img> в content.
    """
    images: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if not url or not isinstance(url, str):
            return
        url = url.strip()
        if not url or url in seen:
            return
        low = url.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            return
        if any(m in low for m in _JUNK_MARKERS):
            return
        if low.endswith(".svg") or ".svg?" in low:
            return
        seen.add(url)
        images.append(url)

    for mc in (entry.get("media_content") or []):
        _add(mc.get("url", ""))

    for mt in (entry.get("media_thumbnail") or []):
        _add(mt.get("url", ""))

    for enc in (entry.get("enclosures") or []):
        _add(enc.get("href") or enc.get("url", ""))

    summary = entry.get("summary", "") or ""
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', summary):
        _add(m.group(1))

    content = entry.get("content", "")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                val = c.get("value", "") or ""
                for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', val):
                    _add(m.group(1))

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
    hrefs = set(_HREF_RE.findall(post))
    logger.info(
        "[MATCH] hrefs из поста: %s",
        [h[:80] for h in list(hrefs)[:3]],
    )

    if not hrefs:
        logger.warning("[MATCH] В посте нет <a href> — фото не будет")
        return None

    # 1. Точный match
    for a in articles:
        if a.get("link") in hrefs:
            logger.info("[MATCH] точное совпадение: %s", a["link"][:80])
            return a

    # 2. Нормализованный match
    post_norms = {_normalize_url_for_match(u) for u in hrefs}
    for a in articles:
        if _normalize_url_for_match(a.get("link", "")) in post_norms:
            logger.info("[MATCH] нормализованное совпадение: %s", a["link"][:80])
            return a

    # 3. Match по path (без схемы, домена, query)
    for href in hrefs:
        try:
            p_href = urlparse(href)
            for a in articles:
                p_art = urlparse(a.get("link", ""))
                if p_href.path and p_href.path == p_art.path:
                    logger.info("[MATCH] совпадение по path: %s", a["link"][:80])
                    return a
        except Exception:
            continue

    logger.warning(
        "[MATCH] Не найдено. hrefs=%s, articles links=%s",
        [h[:60] for h in list(hrefs)[:3]],
        [a.get("link", "")[:60] for a in articles[:3]],
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

async def _send_single_post(bot: Bot, post: str, image: str | None) -> bool:
    if image:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=image, caption=post, parse_mode="HTML",
            )
            logger.info("[SEND] фото OK: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("[SEND] send_photo упал: %s | URL: %s", exc, image[:120])

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
    async with aiohttp.ClientSession() as session:
        for post in posts:
            article = _match_article(post, articles)
            image: str | None = None
            source = "none"

            if article:
                rss_imgs = article.get("rss_images", [])
                logger.info(
                    "[MEDIA] статья найдена, RSS-фото: %d",
                    len(rss_imgs),
                )

                image = await fetch_article_image(session, article["link"])
                if image:
                    source = "HTML"

                if not image and USE_RSS_MEDIA_FALLBACK and rss_imgs:
                    image = rss_imgs[0]
                    source = "RSS-fallback"
                    logger.info("[MEDIA] HTML не дал → RSS-fallback: %s", image[:100])
            else:
                logger.warning("[MEDIA] статья не сопоставлена → без фото")

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
