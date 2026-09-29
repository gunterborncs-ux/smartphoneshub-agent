"""
Суммаризация новостей через OpenRouter.

Отправляет заголовки и описания в LLM, получает готовые посты
для Telegram-канала на русском языке.
"""
import logging
from openai import AsyncOpenAI

from config import (
    OPENROUTER_KEY,
    OPENROUTER_BASE,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url=OPENROUTER_BASE,
    api_key=OPENROUTER_KEY,
)

SYSTEM_PROMPT = """Ты — редактор Telegram-канала @smartphoneshub о смартфонах и мобильных технологиях.

Твоя задача — из предоставленного списка новостей создать от 3 до 5 отдельных постов.

Требования к каждому посту:
- Эмодзи в начале (📱, 🔥, 💡, 📸, ⚡, 🚀 и т.п.)
- Заголовок на русском языке (до 80 символов)
- Краткое описание в 2–3 предложениях: факты, цифры, названия моделей
- В конце — ссылка на источник в формате <a href="URL">Источник</a>
- Длина поста — до 600 символов
- Тон: информативный, нейтральный, без кликбейта
- Если новость не на русском, переведи её

Формат ответа: посты разделены строкой с тремя дефисами (---).
Не добавляй нумерацию, заголовки секций или пояснения до/после постов."""


async def summarize_news(articles: list[dict]) -> list[str]:
    """
    Превращает сырые новости в готовые посты для Telegram.

    Пробует основную модель, при ошибке — fallback.
    :param articles: список статей с ключами title, link, summary
    :return: список готовых постов (строки)
    """
    if not articles:
        return []

    # Формируем входной текст для LLM
    news_text = "\n\n".join(
        f"Заголовок: {a['title']}\n"
        f"Описание: {a.get('summary', '')[:300]}\n"
        f"Ссылка: {a['link']}"
        for a in articles[:30]
    )

    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            logger.info("Отправка запроса к модели: %s", model)
            response = await _client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": news_text},
                ],
                temperature=0.7,
                max_tokens=2000,
            )
            raw = response.choices[0].message.content or ""
            posts = [p.strip() for p in raw.split("---") if p.strip()]

            if posts:
                logger.info("Модель %s сгенерировала %d постов", model, len(posts))
                return posts

            logger.warning("Модель %s вернула пустой ответ", model)

        except Exception as exc:
            logger.error("Ошибка модели %s: %s", model, exc)
            continue

    logger.error("Все модели недоступны, посты не сгенерированы")
    return []