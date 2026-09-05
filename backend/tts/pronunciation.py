"""
pronunciation.py — TTS pronunciation overrides
================================================
Maps words Pocket TTS mispronounces to phonetic spellings the model reads correctly.
"""

import re

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven",
         "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def _number_words(value: int) -> str:
    if value < 20:
        return _ONES[value]
    if value < 100:
        return _TENS[value // 10] + (f"-{_ONES[value % 10]}" if value % 10 else "")
    if value < 1000:
        rest = f" {_number_words(value % 100)}" if value % 100 else ""
        return f"{_ONES[value // 100]} hundred{rest}"
    if value < 1000000:
        rest = f" {_number_words(value % 1000)}" if value % 1000 else ""
        return f"{_number_words(value // 1000)} thousand{rest}"
    return str(value)


def _ordinal_words(value: int) -> str:
    special = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth",
               9: "ninth", 12: "twelfth"}
    if value in special:
        return special[value]
    if value < 20:
        return _ONES[value] + "th"
    if value < 100 and value % 10 == 0:
        return _TENS[value // 10][:-1] + "ieth"
    if value < 100:
        return _TENS[value // 10] + "-" + _ordinal_words(value % 10)
    return _number_words(value) + "th"


def apply_number_pronunciation(text: str) -> str:
    """Convert display numerals into natural spoken words for Pocket TTS."""
    # Times: 07:32 PM -> seven thirty-two P M.
    def time_repl(match):
        hour = int(match.group(1))
        minute = int(match.group(2))
        meridiem = match.group(3).replace(".", " ").upper()
        minute_words = "o'clock" if minute == 0 else _number_words(minute)
        return f"{_number_words(hour)} {minute_words} {meridiem}"

    text = re.sub(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)\b", time_repl, text, flags=re.I)
    text = re.sub(r"\b([AP])\.?M\.?\b", r"\1 M", text, flags=re.I)

    # Dates: August 13, 2026 -> August thirteenth, two thousand twenty-six.
    months = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    text = re.sub(
        rf"\b({months})\s+(\d{{1,2}}),\s*(\d{{4}})\b",
        lambda m: f"{m.group(1)} {_ordinal_words(int(m.group(2)))}, {_number_words(int(m.group(3)))}",
        text, flags=re.I,
    )

    def integer_repl(match):
        raw = match.group(0)
        try:
            return _number_words(int(raw))
        except ValueError:
            return raw

    return re.sub(r"\b\d{1,6}\b", integer_repl, text)

# Longer phrases first when applying replacements.
PRONUNCIATION_MAP: dict[str, str] = {
    # Indian cuisine (common TTS misreads)
    "chole bhature": "cholay bahtooray",
    "chole bhatura": "cholay bahtooray",
    "chole bature": "cholay bahtooray",
    "bhature": "bahtooray",
    "bhatura": "bahtooray",
    "bature": "bahtooray",
    "chole": "cholay",
    "chana": "chunna",
    "masala": "mahsala",
    "biryani": "beer-yah-nee",
    "paneer": "pah-neer",
    "tikka": "tikah",
    "naan": "nahn",
    "roti": "roh-tee",
    "paratha": "prah-tah",
    "samosa": "sah-moh-sah",
    "pakora": "pah-korah",
    "rajma": "rahj-mah",
    "dosa": "doh-sah",
    "idli": "id-lee",
    "vada": "vah-dah",
    "pav": "pahv",
    "bhaji": "bah-jee",
    "dal": "dahl",
    "daal": "dahl",
    "ghee": "ghee",
    "chai": "chy",
    "lassi": "lah-see",
    "kulcha": "kool-chah",
    "keema": "kee-mah",
    "korma": "kor-mah",
    "saag": "sahg",
    "palak": "pah-luck",
    "aloo": "ah-loo",
    "gobi": "goh-bee",
    "matar": "mah-tar",
    "chutney": "chut-nee",
    "raita": "ry-tah",
    "papad": "pah-pud",
    "puri": "poo-ree",
    "kachori": "kah-chor-ee",
    "jalebi": "jah-lay-bee",
    "gulab": "goo-lub",
    "jamun": "jah-mun",
    "halwa": "hul-wah",
    "kheer": "keer",
    "tandoori": "tan-door-ee",
    "seekh": "seek",
    "kebab": "keh-bob",
    "hummus": "hoo-mus",
    "tzatziki": "zaht-zee-kee",
    "quinoa": "keen-wah",
    "gyro": "yee-roh",
    "pho": "fuh",
    "sriracha": "see-rah-chah",
    "chipotle": "chi-poht-lay",
    "cilantro": "sih-lan-tro",
    "jalapeno": "hah-lah-pay-nyo",
    "jalapeño": "hah-lah-pay-nyo",
    "aioli": "ay-oh-lee",
    "niche": "neesh",
    "segue": "seg-way",
    "cache": "cash",
    "router": "row-ter",
    "nginx": "engine-x",
    "kubectl": "kube-control",
    "linux": "linn-ux",
    "ubuntu": "oo-boon-too",
    "debian": "deb-ee-an",
    "archlinux": "ark linux",
}

_SORTED_ENTRIES = sorted(PRONUNCIATION_MAP.items(), key=lambda item: len(item[0]), reverse=True)
_COMPILED_PATTERNS = [
    (re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", re.IGNORECASE), spoken)
    for source, spoken in _SORTED_ENTRIES
]


def apply_pronunciation_fixes(text: str) -> str:
    """Rewrite known mispronounced tokens to phonetic spellings for TTS."""
    if not text:
        return text

    for pattern, spoken in _COMPILED_PATTERNS:
        text = pattern.sub(spoken, text)
    return apply_number_pronunciation(text)
