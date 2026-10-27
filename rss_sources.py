"""
Источники RSS и правила фильтрации для смартфонов.
Только российские сайты.
"""

RSS_FEEDS = [
    "https://www.ixbt.com/export/news/rss.xml",
    "https://4pda.to/feed/",
    "https://hi-news.ru/feed",
    "https://3dnews.ru/news/rss/",
    "https://mobile-review.com/rss/news.xml",
    "https://www.ferra.ru/export/news.xml",
    "https://androidinsider.ru/feed",
    "https://rozetked.me/rss.xml",
    "https://club.dns-shop.ru/rss/",
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
    # АУДИО
    "headphone", "headphones", "earbuds", "earbud", "earphone",
    "tws", "airpods", "speaker", "soundbar",
    "наушники", "гарнитура", "колонка", "аудиосистема",
    # НОСИМЫЕ
    "smartwatch", "apple watch", "galaxy watch", "fitness tracker",
    "умные часы", "фитнес-браслет", "mi band", "smart band",
    # КОМПЬЮТЕРЫ
    "tablet", "ipad", "laptop", "notebook", "macbook", "chromebook",
    "планшет", "ноутбук", "макбук", "компьютер", "системный блок",
    "монитор", "клавиатура", "мышь компьютерная",
    # САНТЕХНИКА
    "душ", "shower", "душевой", "лейка душа", "душевая кабина",
    "ванна", "bathroom", "bath tub",
    "унитаз", "toilet", "биде", "bidet",
    "кран", "faucet", "смеситель", "смесител для",
    "раковина", "умывальник",
    # КУХОННАЯ ТЕХНИКА
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
    # БЫТОВАЯ ТЕХНИКА
    "утюг", "iron для", "отпаривател",
    "фен", "hairdryer", "hair dryer",
    "зубная щетк", "electric toothbrush",
    "бритв", "razor", "электробритв",
    "массажер", "massager", "массажн",
    # КЛИМАТ
    "обогревател", "heater", "кондиционер", "air conditioner",
    "вентилятор", "fan комнатный",
    "очистител воздуха", "air purifier",
    "увлажнител", "humidifier", "мойка воздуха",
    # ОСВЕЩЕНИЕ
    "светильник", "умная лампочк", "light bulb",
    "розетк", "smart plug", "умная розетка",
    "выключател", "smart switch",
    # БЕЗОПАСНОСТЬ
    "camera видеонаблюден", "security camera", "видеонаблюден",
    "дверной звонок", "doorbell", "умный звонок",
    "умный замок", "smart lock",
    # ПЫЛЕСОСЫ
    "робот-пылесос", "robot vacuum", "пылесос", "vacuum cleaner",
    # ТВ / КОНСОЛИ
    "телевизор", "television", "playstation", "xbox",
    "nintendo switch", "игровая консоль",
    # АВТО
    "android auto", "carplay", "электромобил", "автомобил",
    "автопилот для авто",
    # ТАРИФЫ
    "тариф", "tariff", "подписк", "subscription",
    "оператор связи", "carrier plan",
    # ============ ИНСТРУКЦИИ / HOW-TO ============
"как сбросить", "как разблокировать", "как настроить",
"как восстановить", "как включить", "как выключить",
"как перенести", "как очистить", "как ускорить",
"инструкция", "пошагово", "how to", "guide",
"заводских настроек", "заводские настройки",

# ============ КОМПЛЕКТУЮЩИЕ ДЛЯ ПК ============
"видеокарта", "gpu", "rtx", "geforce", "radeon",
"материнская плата", "блок питания", "процессор для пк",
"оперативная память", "ssd", "hdd", "кулер", "термопаста",
]
