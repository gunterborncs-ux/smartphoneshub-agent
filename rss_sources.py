"""
Источники RSS и правила фильтрации для узкой тематики:
только смартфоны, ожидаемые новинки и прототипы.
"""

RSS_FEEDS = [
    # --- Англоязычные ---
    "https://www.gsmarena.com/rss-news-reviews.php",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/category/mobile/feed/",
    "https://www.androidauthority.com/feed/",
    "https://9to5google.com/feed/",
    "https://www.phonearena.com/news/rss",
    "https://www.gizmochina.com/feed/",
    "https://www.xda-developers.com/feed/",
    # --- Русскоязычные ---
    "https://www.ixbt.com/export/news.rss",
    "https://mobile-review.com/feed/",
    "https://4pda.to/feed/",
]

# ---------------------------------------------------------------------------
# Правило фильтрации:
#   статья проходит, если содержит >= 1 маркер из BRAND_MARKERS
#   ИЛИ >= 1 маркер из PHONE_KEYWORDS,
#   И НЕ содержит ни одного стоп-слова из STOP_WORDS.
# ---------------------------------------------------------------------------

# Марки производителей смартфонов (в нижнем регистре)
BRAND_MARKERS = [
    # Крупные бренды
    "samsung", "galaxy", "iphone", "apple", "google pixel", "pixel",
    "xiaomi", "redmi", "poco", "huawei", "honor", "oppo", "vivo",
    "oneplus", "realme", "motorola", "moto", "nothing phone",
    "asus zenfone", "rog phone", "sony xperia", "xperia",
    # Китайские и нишевые
    "infinix", "tecno", "itel", "lava", "micromax", "fairphone",
    "cubot", "ulefone", "doogee", "blackview", "umidigi",
    # Игровые и складные
    "fold", "flip", "razr", "find n", "mix fold",
]

# Общие слова про смартфоны
PHONE_KEYWORDS = [
    # Английские
    "smartphone", "phone", "handset", "flagship",
    "chipset", "snapdragon", "mediatek", "dimensity",
    "exynos", "tensor", "kirin", "unisoc",
    "camera phone", "selfie camera", "main camera",
    # Ожидаемые новинки / прототипы
    "leak", "leaks", "rumor", "rumors", "rumour", "rumours",
    "expected", "upcoming", "prototype", "concept",
    "launch", "unveil", "announce", "release date",
    "hands-on", "first look", "render", "renders", "teaser",
    "specs leak", "specs", "benchmark", "geekbench",
    # Русские
    "смартфон", "телефон", "флагман", "раскладушка",
    "процессор", "камера смартфона",
    "утечка", "утечки", "слух", "слухи",
    "ожидается", "прототип", "концепт",
    "анонс", "представят", "выпустят", "рендер", "рендеры",
    "характеристики", "бенчмарк", "дата выхода",
]

# Стоп-слова: если статья содержит хотя бы одно — отбрасываем
STOP_WORDS = [
    # Аудио
    "headphone", "headphones", "earbuds", "earbud", "earphone",
    "tws", "airpods", "speaker", "soundbar", "наушники", "гарнитура",
    "колонка", "аудио", "auracast", "ldac", "anc-наушники",
    # Носимые устройства
    "smartwatch", "watch", "band", "fitness tracker",
    "умные часы", "фитнес-браслет", "браслет",
    # Планшеты, ноутбуки, десктопы
    "tablet", "ipad", "laptop", "notebook", "macbook",
    "chromebook", "desktop", "pc", "планшет", "ноутбук",
    # Умный дом и камеры
    "smart home", "camera", "webcam", "doorbell", "robot vacuum",
    "vacuum", "light bulb", "умный дом", "камера видеонаблюдения",
    "робот-пылесос", "пылесос", "лампочка",
    # Тарифы, операторы, финансы
    "tariff", "tariffs", "carrier", "operator", "price hike",
    "subscription", "plan", "тариф", "оператор", "подписка",
    # Софт и сервисы (не про смартфон как устройство)
    "android auto", "carplay", "google wallet", "gemini app",
    # ТВ, консоли
    "tv", "television", "console", "playstation", "xbox", "nintendo",
    "телевизор", "консоль",
]
