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

# Модели с гарантированной поддержкой структурированного вывода (JSON schema)
# Primary: Cohere North Mini Code — 30B MoE, 256K контекст, JSON schema
PRIMARY_MODEL = "cohere/north-mini-code:free"
# Fallback: NVIDIA Nemotron 3 Super — 120B MoE, 1M контекст, schema
FALLBACK_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

# --- Агент ---
MAX_ARTICLES_PER_RUN = 30          # максимум статей на цикл
MAX_POSTS_PER_RUN = 5              # максимум постов за один запуск
SIMILARITY_THRESHOLD = 0.82        # порог семантической дедупликации
HISTORY_FILE = "published.json"    # файл истории опубликованных URL
POST_DELAY_SECONDS = 3             # пауза между постами (rate-limit Telegram)
