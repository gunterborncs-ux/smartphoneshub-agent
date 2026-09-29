"""
Конфигурация агента. Читает переменные окружения,
с фолбэком на значения по умолчанию.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@smartphoneshub")

# --- OpenRouter ---
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Основная модель: Qwen 3.6 Plus (бесплатная, 1M контекст)
PRIMARY_MODEL = "qwen/qwen3.6-plus:free"
# Fallback: Free Models Router (автовыбор случайной бесплатной модели)
FALLBACK_MODEL = "openrouter/free"

# --- Агент ---
MAX_ARTICLES_PER_RUN = 30          # максимум статей на цикл
MAX_POSTS_PER_RUN = 5              # максимум постов за один запуск
SIMILARITY_THRESHOLD = 0.82        # порог семантической дедупликации
HISTORY_FILE = "published.json"    # файл истории опубликованных URL
POST_DELAY_SECONDS = 3             # пауза между постами (rate-limit Telegram)