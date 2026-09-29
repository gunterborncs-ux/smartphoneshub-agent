# 📱 Smartphones News Agent 

ИИ-агент для автоматического мониторинга мировых СМИ о смартфонах
и публикации новостей в Telegram-канал [@smartphoneshub](https://t.me/smartphoneshub).

## Как это работает

1. **GitHub Actions** запускает агента 4 раза в день (6:00, 10:00, 14:00, 18:00 UTC).
2. Агент **параллельно собирает** RSS-ленты 11 мировых источников.
3. **Фильтрует** нерелевантные новости по ключевым словам.
4. **Дедуплицирует** семантически близкие заголовки через `sentence-transformers`.
5. **Суммаризирует** через бесплатную модель OpenRouter (`qwen/qwen3.6-plus:free`).
6. **Публикует** 3–5 постов в Telegram-канал.
7. **Сохраняет** историю URL в `published.json` и коммитит обратно в репозиторий.

## Стоимость

- **LLM:** $0 (бесплатные модели OpenRouter)
- **Инфраструктура:** $0 (GitHub Actions, 2000 минут/мес на приватных репо)
- **Telegram Bot API:** $0

## Настройка

### 1. Создайте Telegram-бота

1. Откройте [@BotFather](https://t.me/BotFather), отправьте `/newbot`.
2. Получите токен для `@SmartphonesHubNewsBot`.
3. Добавьте бота в канал `@smartphoneshub` как **администратора** с правом публикации.

### 2. Получите API-ключ OpenRouter

1. Зарегистрируйтесь на [openrouter.ai](https://openrouter.ai).
2. Создайте ключ на [openrouter.ai/keys](https://openrouter.ai/keys).

### 3. Добавьте секреты в GitHub

Перейдите в **Settings → Secrets and variables → Actions** и добавьте:

| Секрет | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather |
| `TELEGRAM_CHANNEL_ID` | `@smartphoneshub` или `-100...` |
| `OPENROUTER_API_KEY` | Ключ OpenRouter |

### 4. Запустите workflow

Перейдите во вкладку **Actions → Smartphones News Agent → Run workflow**.
Первый автоматический запуск произойдёт по расписанию.

## Локальный запуск

```bash
cp .env.example .env
# Заполните .env своими ключами
pip install -r requirements.txt
python main.py
