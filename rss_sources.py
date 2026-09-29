"""
Источники RSS и ключевые слова для фильтрации.
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

# Ключевые слова для фильтрации релевантных новостей
RELEVANCE_KEYWORDS = [
    # Английские
    "smartphone", "phone", "mobile", "android", "ios",
    "iphone", "samsung", "xiaomi", "huawei", "google pixel",
    "oneplus", "snapdragon", "mediatek", "exynos", "dimensity",
    "camera", "battery", "chipset", "foldable", "5g",
    # Русские
    "смартфон", "телефон", "андроид", "айфон",
    "процессор", "камера", "аккумулятор", "зарядка",
    "флагман", "раскладушка", "бюджетник",
]