"""
Источники RSS и правила фильтрации для смартфонов.
Только российские сайты.
"""

RSS_FEEDS = [
    # iXBT — крупнейшее технологическое СМИ
    "https://www.ixbt.com/export/news/rss.xml",
    # 4PDA — мобильные устройства и технологии
    "https://4pda.to/feed/",
    # Hi-News — новости высоких технологий
    "https://hi-news.ru/feed",
    # 3DNews — новости Hardware и Software
    "https://3dnews.ru/news/rss/",
    # Mobile-review — обзоры мобильной техники
    "https://mobile-review.com/rss/news.xml",
    # Ferra — гаджеты и технологии
    "https://www.ferra.ru/export/news.xml",
    # AndroidInsider — всё об Android
    "https://androidinsider.ru/feed",

    # --- Новые источники по запросу ---
    # Rozetked — RSS подтверждён
    "https://rozetked.me/rss.xml",
    # DNS Club (Клуб DNS) — раздел новостей DNS
    # Если лента не работает, проверьте альтернативный адрес:
    # https://club.dns-shop.ru/rss/ или https://club.dns-shop.ru/feed/
    "https://club.dns-shop.ru/rss/",
    # Wylsacom — сайт Wylsa.com
    # Если лента не работает, проверьте альтернативный адрес:
    # https://wylsa.com/rss/ или https://wylsa.com/feed/
    "https://wylsa.com/feed/",
]

BRAND_MARKERS = [
    "samsung", "galaxy", "iphone", "apple", "google pixel", "pixel",
    "xiaomi", "redmi", "poco", "huawei", "honor", "oppo", "vivo",
    "oneplus", "realme", "motorola", "moto", "nothing phone",
    "asus zenfone", "rog phone", "sony xperia", "xperia",
    "infinix", "tecno", "itel", "lava", "micromax", "fairphone",
    "cubot", "ulefone", "doogee", "blackview", "umidigi",
    "fold", "flip", "razr", "find n", "mix fold",
]

PHONE_KEYWORDS = [
    "smartphone", "phone", "handset", "flagship",
    "chipset", "snapdragon", "mediatek", "dimensity",
    "exynos", "tensor", "kirin", "unisoc",
    "camera phone", "selfie camera", "main camera",
    "leak", "leaks", "rumor", "rumors", "rumour", "rumours",
    "expected", "upcoming", "prototype", "concept",
    "launch", "unveil", "announce", "release date",
    "hands-on", "first look", "render", "renders", "teaser",
    "specs leak", "specs", "benchmark", "geekbench",
    "смартфон", "телефон", "флагман", "раскладушка",
    "процессор", "камера смартфона",
    "утечка", "утечки", "слух", "слухи",
    "ожидается", "прототип", "концепт",
    "анонс", "представят", "выпустят", "рендер", "рендеры",
    "характеристики", "бенчмарк", "дата выхода",
]

STOP_WORDS = [
    # ============ АУДИО ============
    "headphone", "headphones", "earbuds", "earbud", "earphone",
    "tws", "airpods", "speaker", "soundbar",
    "наушники", "гарнитура", "колонка", "аудиосистема",

    # ============ НОСИМЫЕ ============
    "smartwatch", "apple watch", "galaxy watch", "fitness tracker",
    "умные часы", "фитнес-браслет", "mi band", "smart band",

    # ============ КОМПЬЮТЕРЫ ============
    "tablet", "ipad", "laptop", "notebook", "macbook", "chromebook",
    "планшет", "ноутбук", "макбук", "компьютер", "системный блок",
    "монитор", "клавиатура", "мышь компьютерная",

    # ============ САНТЕХНИКА ============
    "душ", "shower", "душевой", "лейка душа", "душевая кабина",
    "ванна", "bathroom", "bath tub",
    "унитаз", "toilet", "биде", "bidet",
    "кран", "faucet", "смеситель", "смесител для",
    "раковина", "умывальник",

    # ============ КУХОННАЯ ТЕХНИКА ============
    "стиральн машин", "washing machine",
    "посудомоечн", "dishwasher",
    "холодильник", "refrigerator", "fridge",
    "кофеварк", "кофемашин", "coffee maker", "coffee machine",
    "чайник", "kettle", "тостер", "toaster",
    "блендер", "blender", "миксер", "mixer",
    "мультиварк", "multicooker", "скороварк",
    "микроволнов", "microwave",
    "аэрогриль", "air fryer", "духовк", "духовой шкаф", "oven",
    "плита", "варочная панель", "stove", "cooktop",
    "соковыжималк", "juicer",

    # ============ БЫТОВАЯ ТЕХНИКА ============
    "утюг", "iron для", "отпаривател",
    "фен", "hairdryer", "hair dryer",
    "зубная щетк", "electric toothbrush",
    "бритв", "razor", "электробритв",
    "массажер", "massager", "массажн",

    # ============ КЛИМАТ ============
    "обогревател", "heater", "кондиционер", "air conditioner",
    "вентилятор", "fan комнатный",
    "очистител воздуха", "air purifier",
    "увлажнител", "humidifier", "мойка воздуха",

    # ============ ОСВЕЩЕНИЕ / ЭЛЕКТРИКА ============
    "светильник", "умная лампочк", "light bulb",
    "розетк", "smart plug", "умная розетка",
    "выключател", "smart switch",

    # ============ БЕЗОПАСНОСТЬ / УМНЫЙ ДОМ ============
    "camera видеонаблюден", "security camera", "видеонаблюден",
    "дверной звонок", "doorbell", "умный звонок",
    "умный замок", "smart lock",

    # ============ ПЫЛЕСОСЫ / РОБОТЫ ============
    "робот-пылесос", "robot vacuum", "пылесос", "vacuum cleaner",

    # ============ ТВ / КОНСОЛИ ============
    "телевизор", "television", "playstation", "xbox",
    "nintendo switch", "игровая консоль",

    # ============ АВТО ============
    "android auto", "carplay", "электромобил", "автомобил",
    "автопилот для авто",

    # ============ ТАРИФЫ / ФИНАНСЫ ============
    "тариф", "tariff", "подписк", "subscription",
    "оператор связи", "carrier plan",
]
