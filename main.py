"""
Smartphones News Agent.

Трёхуровневая логика фото:
  1. og:image из HTML статьи
  2. RSS-медиа (media:content / media:thumbnail / enclosure / <img>)
  3. Placeholder (brand + заголовок) при USE_PLACEHOLDER_IF_NO_PHOTO=True

Ссылка — <a href="URL">Источник</a>.
"""
import asyncio
import json
import logging
import re
import traceback
from urllib.parse import quote, urljoin, urlparse

import aiohttp
import feedparser
from aiogram import Bot
from bs4 import BeautifulSoup

from config import (
    BOT_TOKEN, CHANNEL_ID, ADMIN_CHAT_ID,
    MAX_ARTICLES_PER_RUN, MAX_POSTS_PER_RUN,
    SIMILARITY_THRESHOLD, HISTORY_FILE,
    POST_DELAY_SECONDS, HISTORY_RETENTION_DAYS,
    USE_RSS_MEDIA_FALLBACK, USE_PLACEHOLDER_IF_NO_PHOTO,
    PLACEHOLDER_IMAGE_TEMPLATE,
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


def _make_placeholder(title: str) -> str:
    title_lower = title.lower()
    text = None
    for brand in BRAND_MARKERS:
        if brand in title_lower:
            text = brand.upper()
            break
    if not text:
        text = title[:25].strip() or "PHONE"
    return PLACEHOLDER_IMAGE_TEMPLATE.format(text=quote(text))


async def _try_fetch_html(
    session: aiohttp.ClientSession, url: str,
) -> str | None:
    variants = [url]
    if "://www." in url:
        variants.append(url.replace("://www.", "://"))
    if url.startswith("http://"):
        variants.append(url.replace("http://", "https://", 1))

    headers_sets = [
        {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru,en;q=0.5",
        },
        {
            "User-Agent": _UA,
            "Accept": "*/*",
            "Accept-Language": "ru",
            "Referer": "https://yandex.ru/",
        },
    ]

    for variant in variants:
        for headers in headers_sets:
            try:
                async with session.get(
                    variant, timeout=aiohttp.ClientTimeout(total=12),
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        return await resp.text(errors="ignore")
                    logger.warning("[HTML] %s → HTTP %s", variant, resp.status)
            except Exception as exc:
                logger.warning("[HTML] %s → %s", variant, exc)
    return None


async def fetch_article_image(
    session: aiohttp.ClientSession, url: str,
) -> str | None:
    html = await _try_fetch_html(session, url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("og:image", "og:image:url", "og:image:secure_url"):
            c = tag.get("content", "").strip()
            if c:
                u = urljoin(url, c)
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → og:image", url)
                    return u

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in ("twitter:image", "twitter:image:src"):
            c = tag.get("content", "").strip()
            if c:
                u = urljoin(url, c)
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → twitter:image", url)
                    return u

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
            cands: list[str] = []
            if isinstance(img, str):
                cands = [img]
            elif isinstance(img, list):
                for x in img:
                    if isinstance(x, str):
                        cands.append(x)
                    elif isinstance(x, dict) and x.get("url"):
                        cands.append(x["url"])
            elif isinstance(img, dict) and img.get("url"):
                cands = [img["url"]]
            for c in cands:
                u = urljoin(url, c.strip())
                if _is_valid_image_url(u):
                    logger.info("[HTML] %s → JSON-LD", url)
                    return u

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


def _extract_rss_images(entry: dict, base_url: str) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
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
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "User-Agent": _UA,
                "Accept": "application/rss+xml,application/xml,text/xml,*/*",
            },
        ) as resp:
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
                "rss_images": _extract_rss_images(entry, link),
            })
        logger.info(
            "RSS %s → %d статей (с фото: %d)",
            url, len(articles),
            sum(1 for a in articles if a["rss_images"]),
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
    logger.info(
        "Собрано: %d, релевантных: %d (с RSS-фото: %d)",
        len(all_articles), len(relevant), with_photo,
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


async def _send_single_post(
    bot: Bot, post: str, image: str | None, title: str,
) -> bool:
    if image:
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=image, caption=post, parse_mode="HTML",
            )
            logger.info("[SEND] фото OK: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning(
                "[SEND] send_photo упал: %s | URL: %s", exc, image[:120],
            )

    if USE_PLACEHOLDER_IF_NO_PHOTO:
        placeholder = _make_placeholder(title)
        try:
            await bot.send_photo(
                chat_id=CHANNEL_ID, photo=placeholder,
                caption=post, parse_mode="HTML",
            )
            logger.info("[SEND] placeholder OK: %s...", post[:60])
            return True
        except Exception as exc:
            logger.warning("[SEND] placeholder тоже упал: %s", exc)

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
            title = article.get("title", "") if article else post[:50]
            image: str | None = None
            source = "none"

            if article:
                rss_imgs = article.get("rss_images", [])
                logger.info("[MEDIA] RSS-фото: %d", len(rss_imgs))

                image = await fetch_article_image(session, article["link"])
                if image:
                    source = "HTML"

                if not image and USE_RSS_MEDIA_FALLBACK and rss_imgs:
                    image = rss_imgs[0]
                    source = "RSS"
                    logger.info("[MEDIA] HTML не дал → RSS: %s", image[:100])
            else:
                logger.warning("[MEDIA] статья не сопоставлена")

            logger.info(
                "[MEDIA] итог: source=%s, image=%s",
                source, (image[:80] + "...") if image else "None",
            )

            if await _send_single_post(bot, post, image, title):
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
