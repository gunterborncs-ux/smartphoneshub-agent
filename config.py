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

# Primary: NVIDIA Nemotron 3 Super — отрабатывает быстрее
PRIMARY_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
# Fallback: Cohere North Mini Code
FALLBACK_MODEL = "cohere/north-mini-code:free"

# --- Агент ---
MAX_ARTICLES_PER_RUN = 30          # максимум статей на цикл
MAX_POSTS_PER_RUN = 5              # максимум постов за один запуск
SIMILARITY_THRESHOLD = 0.82        # порог семантической дедупликации
HISTORY_FILE = "published.json"    # файл истории опубликованных URL
POST_DELAY_SECONDS = 3             # пауза между постами (rate-limit Telegram)
MAX_ARTICLES_PER_RUN = 30          # максимум статей на цикл
MAX_POSTS_PER_RUN = 5              # максимум постов за один запуск
SIMILARITY_THRESHOLD = 0.82        # порог семантической дедупликации
HISTORY_FILE = "published.json"    # файл истории опубликованных URL
POST_DELAY_SECONDS = 3             # пауза между постами (rate-limit Telegram)
HISTORY_RETENTION_DAYS = 30        # сколько дней хранить историю (0 = бессрочно)

# --- Уведомления об ошибках ---
# Ваш Telegram user ID (узнать у @userinfobot). 0 = уведомления отключены.
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
