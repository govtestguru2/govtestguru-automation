"""hindi_pronunciation.py — TTS se pehle script text ko saaf karne wala module.

Guide (Section 4.4) ke niyam:
  * Saal shabdon me bole jayenge: 2026 -> "दो हज़ार छब्बीस",
    1857 -> "अठारह सौ सत्तावन", session 2027-28 -> "दो हज़ार सत्ताईस–अट्ठाईस".
  * Roman ank (i), (ii), (iii) sirf AAWAAZ me ek, do, teen bante hain
    (screen par Roman hi dikhta hai — ye function sirf narration text par lage).
  * "BC" ka matlab context se: saal ke saath (563 BC) = "ईसा पूर्व",
    naukri/shreni wale sandarbh me bacha hua BC = "Backward Classes".
    Isiliye pehle saal-wala pattern badla jata hai, phir bacha hua BC.
  * BCE -> "ईसा पूर्व", CE -> "Common Era" (poora naam).
  * Baaki abbreviations config/pronunciation.json dictionary se badle jate hain.

Fix ki pushti hamesha GENERATED narration text par karo — is file ke
__main__ demo se sample chala kar dekho.
"""

from __future__ import annotations

import json
import os
import re

# ---------------------------------------------------------------------------
# Hindi number words (0-99)
# ---------------------------------------------------------------------------
_ONES = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह",
    "अठारह", "उन्नीस", "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस",
    "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस", "तीस", "इकतीस", "बत्तीस",
    "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस",
    "उनतालीस", "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चौंतालीस",
    "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास", "पचास",
    "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन",
    "अट्ठावन", "उनसठ", "साठ", "इकसठ", "बासठ", "तिरसठ", "चौंसठ",
    "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर", "सत्तर", "इकहत्तर",
    "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर",
    "अठहत्तर", "उनासी", "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी",
    "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी", "नब्बे", "इक्यानवे",
    "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे",
    "अट्ठानवे", "निन्यानवे",
]


def num_to_hindi(n: int) -> str:
    """0-99 tak ki sankhya ko Hindi shabdon me badlo."""
    if not 0 <= n <= 99:
        raise ValueError(f"num_to_hindi sirf 0-99 support karta hai, mila: {n}")
    return _ONES[n]


def year_to_hindi(year: int) -> str:
    """Saal ko boli-wale Hindi shabdon me badlo.

    2026 -> दो हज़ार छब्बीस | 2000 -> दो हज़ार
    1857 -> अठारह सौ सत्तावन | 563 -> पाँच सौ तिरसठ | 1900 -> उन्नीस सौ
    """
    if year >= 2000 and year <= 2099:
        rest = year - 2000
        return "दो हज़ार" + (f" {num_to_hindi(rest)}" if rest else "")
    if year >= 100:
        hundreds, rest = divmod(year, 100)
        base = f"{num_to_hindi(hundreds)} सौ"
        return base + (f" {num_to_hindi(rest)}" if rest else "")
    return num_to_hindi(year)


# ---------------------------------------------------------------------------
# Pronunciation dictionary
# ---------------------------------------------------------------------------
_DEFAULT_DICT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "pronunciation.json"
)


def load_dictionary(dict_path: str | None = None) -> dict:
    """config/pronunciation.json se ucharan dictionary load karo."""
    path = dict_path or _DEFAULT_DICT_PATH
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------------------
# Roman ank (narration only)
# ---------------------------------------------------------------------------
_ROMAN = {
    "i": "एक", "ii": "दो", "iii": "तीन", "iv": "चार", "v": "पाँच",
    "vi": "छह", "vii": "सात", "viii": "आठ", "ix": "नौ", "x": "दस",
}
_ROMAN_RE = re.compile(
    r"\(\s*(viii|vii|iii|ix|iv|vi|ii|i|v|x)\s*\)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------
def prepare_tts_text(text: str, dict_path: str | None = None) -> str:
    """Script text ko TTS-ready narration text me badlo.

    Kram (order) bahut zaroori hai — pehle saal/context wale pattern,
    phir aam abbreviations, aakhir me dictionary.
    """
    if not text:
        return text
    out = text

    # 1) Saal ki range: 2027-28 / 2027–28 / 2027-2028
    def _range_repl(m: re.Match) -> str:
        first = year_to_hindi(int(m.group(1)))
        tail = m.group(2)
        second = year_to_hindi(int(tail)) if len(tail) == 4 else num_to_hindi(int(tail))
        # poore saal wali range me dusra saal bhi poora bolo, chhoti range me sirf ank
        if len(tail) == 4:
            return f"{first} से {second}"
        return f"{first}–{second}"

    out = re.sub(r"\b((?:19|20)\d{2})\s*[-–]\s*(\d{2}|\d{4})\b", _range_repl, out)

    # 2) Saal + BC/BCE  ->  "<saal shabdon me> ईसा पूर्व"  (context rule, pehle!)
    out = re.sub(
        r"\b(\d{3,4})\s*(?:BC|BCE)\b",
        lambda m: f"{year_to_hindi(int(m.group(1)))} ईसा पूर्व",
        out,
    )
    #    Saal + CE  ->  "<saal> Common Era"
    out = re.sub(
        r"\b(\d{3,4})\s*CE\b",
        lambda m: f"{year_to_hindi(int(m.group(1)))} Common Era",
        out,
    )

    # 3) Baaki bache 4-ank wale saal shabdon me
    out = re.sub(
        r"\b(\d{4})\b", lambda m: year_to_hindi(int(m.group(1))), out
    )

    # 4) Roman ank — sirf narration me: (i) -> एक
    out = _ROMAN_RE.sub(lambda m: _ROMAN[m.group(1).lower()], out)

    # 5) BCE / CE (bina saal ke aaye hon to)
    out = re.sub(r"\bBCE\b", "ईसा पूर्व", out)
    out = re.sub(r"\bCE\b", "Common Era", out)

    # 6) Ab bacha hua BC = naukri/shreni wala sandarbh -> Backward Classes
    out = re.sub(r"\bBC\b", "Backward Classes", out)

    # 7) Dictionary (lambi key pehle, taaki chhoti key aadha shabd na kaate)
    dictionary = load_dictionary(dict_path)
    for key in sorted(dictionary, key=len, reverse=True):
        out = re.sub(
            r"\b" + re.escape(key) + r"\b",
            dictionary[key].replace("\\", r"\\"),
            out,
        )

    # 8) Dohri spaces saaf karo
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


if __name__ == "__main__":
    samples = [
        "UGC NET 2026 की परीक्षा दिसंबर में होगी।",
        "1857 का विद्रोह भारतीय इतिहास का महत्वपूर्ण मोड़ था।",
        "गौतम बुद्ध का जन्म 563 BC में हुआ था।",
        "इस भर्ती में BC वर्ग के अभ्यर्थियों को आरक्षण मिलेगा।",
        "मुख्य बिंदु: (i) पाठ्यक्रम, (ii) परीक्षा पैटर्न, (iii) योग्यता।",
        "यह घटना 563 BCE की है और दूसरी 200 CE की।",
        "सत्र 2027-28 के लिए JTET और NTA की तैयारी शुरू करें।",
    ]
    for s in samples:
        print(f"IN : {s}")
        print(f"OUT: {prepare_tts_text(s)}\n")
