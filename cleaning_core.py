"""
Dictionary-based cleaning/translation core for the Bakery Form Response Cleaner.

This is the module app.py imports (`clean_dataframe`, `DEFAULT_DICTIONARIES`).
It reuses the low-level, dictionary-free cleaning helpers already written in
pipeline.py (whitespace/diacritics/digit normalization, phone-number fixing,
multiselect tidying, and branching-question column merging) and adds a fully
offline, dictionary-driven translation layer on top of them:

  1. Column headers are translated via `header_map` (exact match), falling
     back to a word-by-word translation for any question not yet in the map.
  2. Known categorical answers (governorate, area, yes/no, bakery type,
     yeast product, company) are translated via their dedicated maps, chosen
     per-column using simple keyword heuristics on the Arabic header text.
  3. Anything left over (personal names, free-text addresses, distributor
     names, or any value not found in a dictionary) falls back to a
     word-by-word lookup in `common_words_map`, and finally to a simple
     phonetic Arabic -> Latin transliteration so nothing is ever left in
     Arabic script, and nothing ever crashes on an unseen value.

No internet access / translation API is used anywhere in this module, so it
works fully offline and never depends on a third-party translation service
being reachable.
"""
import re
from collections import OrderedDict

import pandas as pd

from pipeline import (
    clean_headers,
    clean_multiselect,
    clean_whitespace,
    contains_arabic,
    detect_branching_groups,
    fix_phone_number,
    get_text_columns,
    is_phone_column,
    merge_branching_group,
    normalize_arabic_letters,
    remove_diacritics,
    to_western_digits,
)

# Inserted between a digit and an Arabic letter (or vice versa) that got
# typed with no space, e.g. "٩ل ١٢" ("9 to 12") -> "٩ ل ١٢", so word-by-word
# fallback translation can pick up the Arabic word ("ل" = "to") instead of
# gluing it onto the number.
_DIGIT_ARABIC_BOUNDARY = re.compile(r'(?<=[0-9\u0660-\u0669])(?=[\u0600-\u06FF])|(?<=[\u0600-\u06FF])(?=[0-9\u0660-\u0669])')


def space_digit_arabic_boundary(text):
    if not isinstance(text, str):
        return text
    return _DIGIT_ARABIC_BOUNDARY.sub(' ', text)

# ---------------------------------------------------------------------------
# Default dictionaries (all editable from the Streamlit sidebar as JSON)
# ---------------------------------------------------------------------------

# Exact-match translations for cleaned column headers (i.e. after clean_headers
# has already stripped embedded newlines / non-breaking spaces / extra
# whitespace). Add new Forms questions here as they show up.
HEADER_MAP = {
    "ID": "ID",
    "Start time": "Start time",
    "Completion time": "Completion time",
    "Email": "Email",
    "Name": "Name",
    "Last modified time": "Last modified time",
    "email": "email",
    "اسم مالك المخبز ثلاثي": "Bakery Owner Full Name",
    "رقم تليفون": "Phone Number",
    "العنوان بالتفصيل": "Detailed Address",
    "المحافظه": "Governorate",
    "المنطقه": "Area / District",
    "المنتج المستخدم": "Product",
    "اسم الموزع": "Distributor Name",
    "اسم الشركه": "Company Name",
    "الاستهلاك اليومي (خميره فريش) بالعبوه": "Daily Consumption (Fresh Yeast, packs)",
    "الاستهلاك اليومي (خميره الجافه) بالعبوه": "Daily Consumption (Dry Yeast, packs)",
    "نوع المخبز": "Bakery Type",
    "بيستخدم محسن عيش ؟": "Uses Bread Improver?",
    "نوع المحسن": "Improver Type",
}

GOVERNORATE_MAP = {
    "القاهرة": "Cairo",
    "القاهره": "Cairo",
    "الجيزة": "Giza",
    "الجيزه": "Giza",
    "الاسكندرية": "Alexandria",
    "الإسكندرية": "Alexandria",
    "الدقهلية": "Dakahlia",
    "البحر الاحمر": "Red Sea",
    "البحر الأحمر": "Red Sea",
    "البحيرة": "Beheira",
    "البحيره": "Beheira",
    "الفيوم": "Fayoum",
    "الغربية": "Gharbia",
    "الاسماعيلية": "Ismailia",
    "الإسماعيلية": "Ismailia",
    "المنوفية": "Monufia",
    "المنيا": "Minya",
    "القليوبية": "Qalyubia",
    "الوادي الجديد": "New Valley",
    "السويس": "Suez",
    "اسوان": "Aswan",
    "أسوان": "Aswan",
    "اسيوط": "Asyut",
    "أسيوط": "Asyut",
    "بني سويف": "Beni Suef",
    "بورسعيد": "Port Said",
    "دمياط": "Damietta",
    "الشرقية": "Sharqia",
    "جنوب سيناء": "South Sinai",
    "كفر الشيخ": "Kafr El Sheikh",
    "مطروح": "Matrouh",
    "الاقصر": "Luxor",
    "الأقصر": "Luxor",
    "قنا": "Qena",
    "شمال سيناء": "North Sinai",
    "سوهاج": "Sohag",
}

# Areas/districts seen so far. This list will keep growing as new areas show
# up in responses; anything missing falls back to transliteration.
AREA_MAP = {
    "الهرم": "Al Haram",
    "الحوامديه": "Al-Hawamdeya",
    "الحوامدية": "Al-Hawamdeya",
    "فيصل": "Faisal",
    "البدرشين": "Al-Badrashin",
    "اوسيم": "Awsim",
    "أوسيم": "Awsim",
    "الدقي": "Dokki",
    "العجوزة": "Agouza",
    "امبابة": "Imbaba",
    "إمبابة": "Imbaba",
    "الوراق": "Al-Warraq",
    "٦ اكتوبر": "6th of October",
    "6 اكتوبر": "6th of October",
    "السادس من اكتوبر": "6th of October",
    "الشيخ زايد": "Sheikh Zayed",
    "حدائق الاهرام": "Hadayek El Ahram",
    "صفط اللبن": "Saft El-Laban",
    "كرداسة": "Kerdasa",
    "العياط": "Al-Ayyat",
}

YES_NO_MAP = {
    "نعم": "Yes",
    "ايوه": "Yes",
    "أيوه": "Yes",
    "اه": "Yes",
    "لا": "No",
    "لأ": "No",
}

BAKERY_TYPE_MAP = {
    "فينو": "Fino",
    "بلدي": "Baladi",
    "شامي": "Shami",
    "افرنجي": "Franji",
    "أفرنجي": "Franji",
    "حلواني": "Confectionery",
    "معجنات": "Pastries",
    "سياحي": "Tourist Bread",
    "عربي": "Arabi",
}

PRODUCT_MAP = {
    "خميره فريش": "Fresh Yeast",
    "خميرة فريش": "Fresh Yeast",
    "خميره الجافه": "Dry Yeast",
    "خميرة الجافة": "Dry Yeast",
    "خميره جافه": "Dry Yeast",
    "خميرة جافة": "Dry Yeast",
}

COMPANY_MAP = {
    "لوسافر": "Lesaffre",
    "انجل": "Angel Yeast",
    "أنجل": "Angel Yeast",
    "شركه اخرى": "Other Company",
    "شركة أخرى": "Other Company",
    "اخرى": "Other",
    "أخرى": "Other",
}

# Fallback: common connector words / frequent Egyptian first names, used
# word-by-word whenever a full value isn't found in a more specific map
# above (e.g. inside free-text names, addresses, or odd phrasings).
COMMON_WORDS_MAP = {
    "شركه": "Company",
    "شركة": "Company",
    "اخرى": "Other",
    "أخرى": "Other",
    "خميره": "Yeast",
    "خميرة": "Yeast",
    "فريش": "Fresh",
    "جافه": "Dry",
    "جافة": "Dry",
    "الجافه": "Dry",
    "الجافة": "Dry",
    "من": "From",
    "الي": "To",
    "إلى": "To",
    "ل": "to",
    "و": "and",
    "بن": "Ibn",
    "ابن": "Ibn",
    "احمد": "Ahmed",
    "أحمد": "Ahmed",
    "محمد": "Mohamed",
    "علي": "Ali",
    "حسن": "Hassan",
    "حسين": "Hussein",
    "مصطفى": "Mostafa",
    "ابراهيم": "Ibrahim",
    "إبراهيم": "Ibrahim",
    "عبدالله": "Abdullah",
    "عبد الله": "Abdullah",
    "يوسف": "Youssef",
    "خالد": "Khaled",
    "عمر": "Omar",
    "سعيد": "Saeed",
    "محمود": "Mahmoud",
    "عبدالرحمن": "Abdelrahman",
    "عبد الرحمن": "Abdelrahman",
    "طارق": "Tarek",
    "كريم": "Karim",
    "شارع": "Street",
    "ميدان": "Square",
    "المنطقه": "Area",
    "المنطقة": "Area",
    "ايميل": "email",
    "أيميل": "email",
    "الايميل": "email",
    "امام": "In front of",
    "أمام": "In front of",
    "بجوار": "Next to",
    "خلف": "Behind",
}

DEFAULT_DICTIONARIES = {
    "header_map": HEADER_MAP,
    "governorate_map": GOVERNORATE_MAP,
    "area_map": AREA_MAP,
    "yes_no_map": YES_NO_MAP,
    "bakery_type_map": BAKERY_TYPE_MAP,
    "product_map": PRODUCT_MAP,
    "company_map": COMPANY_MAP,
    "common_words_map": COMMON_WORDS_MAP,
}

# Which dictionaries apply to a column, keyed by a keyword found in the
# column's (original, Arabic) header. Order matters: first match wins.
_COLUMN_TYPE_RULES = OrderedDict([
    ("governorate", ["محافظ"]),
    ("area", ["منطق"]),
    ("bakery_type", ["نوع المخبز"]),
    ("improver_type", ["نوع المحسن"]),
    ("product", ["المنتج", "منتج"]),
    ("company", ["الشركه", "الشركة", "شركه", "شركة"]),
    ("distributor", ["الموزع", "موزع"]),
    ("name_address", ["اسم", "عنوان"]),
])

_TYPE_TO_MAPS = {
    "governorate": ["governorate_map"],
    "area": ["area_map"],
    "bakery_type": ["bakery_type_map"],
    "improver_type": ["bakery_type_map"],
    "product": ["product_map", "company_map"],
    "company": ["company_map"],
    "distributor": ["company_map"],
}

# Column types whose ';'-joined multi-select answers get split out into their
# own numbered columns (e.g. "Product 1", "Product 2", ...) instead of being
# kept as a single "A; B" string. A respondent who ticked one box just gets
# "Product 1" filled and "Product 2" left blank.
SPLIT_COLUMN_TYPES = {"product"}

# Basic phonetic Arabic -> Latin transliteration, used only as a last resort
# for words that appear in no dictionary at all (e.g. an uncommon name).
_TRANSLIT_TABLE = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "aa", "ب": "b", "ت": "t", "ث": "th",
    "ج": "g", "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z",
    "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "aa",
    "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "و": "w", "ي": "y", "ى": "a", "ة": "a", "ء": "a", "ئ": "e",
    "ؤ": "o",
}


def _normalize_key(text: str) -> str:
    """Apply the same normalization the data goes through (diacritics
    stripped, alef/yaa variants unified, whitespace collapsed) so dictionary
    keys match regardless of which spelling variant was typed."""
    return clean_whitespace(normalize_arabic_letters(remove_diacritics(text)))


def _normalize_dicts(dicts):
    """Return a copy of the dictionary set with every key normalized, so
    lookups against already-normalized cell text always succeed regardless
    of which Arabic spelling variant (أ/إ/آ vs ا, ى vs ي) was used when the
    dictionary entry was written."""
    normalized = {}
    for map_name, mapping in dicts.items():
        normalized[map_name] = {_normalize_key(str(k)): v for k, v in mapping.items()}
    return normalized


def detect_column_type(header) -> str:
    """Classify a (cleaned, still-Arabic) header so we know which dictionary/
    dictionaries to try first for values in that column."""
    h = str(header)
    for col_type, keywords in _COLUMN_TYPE_RULES.items():
        if any(k in h for k in keywords):
            return col_type
    return "generic"


def transliterate_word(word: str) -> str:
    """Best-effort phonetic transliteration for a single Arabic word."""
    out = "".join(_TRANSLIT_TABLE.get(ch, ch) for ch in word)
    return out.capitalize() if out else out


def _all_word_maps(dicts):
    combined = {}
    for key in (
        "yes_no_map", "governorate_map", "area_map", "bakery_type_map",
        "product_map", "company_map", "common_words_map",
    ):
        combined.update(dicts.get(key, {}))
    return combined


def translate_words_fallback(text: str, dicts) -> str:
    """Word-by-word translation: dictionary lookup per word, else transliterate.

    Words are normalized (diacritics stripped, أ/إ/آ->ا, ى->ي) before lookup
    to match the already-normalized dictionary keys -- otherwise a word typed
    with e.g. a hamza (أيميل) silently misses an entry written without one
    (ايميل) and falls through to transliteration instead of translation.
    """
    word_maps = _all_word_maps(dicts)
    out = []
    for raw_word in text.split(" "):
        word = raw_word.strip("،,.؛:؛()")
        if not word:
            continue
        word_norm = _normalize_key(word)
        if word_norm in word_maps:
            out.append(word_maps[word_norm])
        elif contains_arabic(word):
            out.append(transliterate_word(word))
        else:
            out.append(word)
    return " ".join(out)


def translate_segment(segment: str, col_type: str, dicts) -> str:
    """Translate one (already-split-on-';') value for a column of a given type."""
    seg = segment.strip()
    if not seg:
        return seg
    # Yes/No answers are unambiguous regardless of column type.
    if seg in dicts.get("yes_no_map", {}):
        return dicts["yes_no_map"][seg]
    for map_name in _TYPE_TO_MAPS.get(col_type, []):
        m = dicts.get(map_name, {})
        if seg in m:
            return m[seg]
    if seg in dicts.get("common_words_map", {}):
        return dicts["common_words_map"][seg]
    return translate_words_fallback(seg, dicts)


def translate_cell(value, col_type: str, dicts):
    """Translate one cell, handling ';'-separated multi-select answers."""
    if not isinstance(value, str) or not value.strip():
        return value
    if not contains_arabic(value):
        return value
    if ";" in value:
        parts = [p.strip() for p in value.split(";") if p.strip()]
        return "; ".join(translate_segment(p, col_type, dicts) for p in parts)
    return translate_segment(value, col_type, dicts)


def split_multiselect_column(df, col, prefix):
    """Split a ';'-joined multiselect column into `prefix 1`, `prefix 2`, ...
    columns (one per selected option), placed where the original column was.

    Rows with only one selection get `prefix 1` filled and the rest left
    blank (NaN). If nothing in the column actually has more than one
    selection, the dataframe is returned unchanged (no point splitting a
    single-value column into "Product 1").
    """
    if col not in df.columns:
        return df
    idx = df.columns.get_loc(col)
    parts = df[col].apply(
        lambda v: [p.strip() for p in str(v).split(";") if p.strip()]
        if isinstance(v, str) and v.strip()
        else []
    )
    max_n = int(parts.map(len).max()) if len(parts) else 0
    if max_n <= 1:
        return df
    new_cols = OrderedDict()
    for i in range(max_n):
        new_cols[f"{prefix} {i + 1}"] = parts.apply(lambda lst, i=i: lst[i] if i < len(lst) else pd.NA)
    df = df.drop(columns=[col])
    for offset, (name, series) in enumerate(new_cols.items()):
        df.insert(idx + offset, name, series)
    return df


def translate_header(header: str, dicts) -> str:
    h = str(header).strip()
    header_map = dicts.get("header_map", {})
    if h in header_map:
        return header_map[h]
    h_norm = _normalize_key(h)
    if h_norm in header_map:
        return header_map[h_norm]
    if not contains_arabic(h):
        return h
    # Bilingual "<English> / <Arabic word/phrase meaning the same thing>"
    # labels (Forms authors sometimes add an Arabic gloss for clarity) ->
    # once translated, if the Arabic side just repeats the English side,
    # collapse to the English side alone instead of "email / email".
    bilingual = re.match(r'^([A-Za-z][A-Za-z0-9 _-]*)\s*/\s*(.+)$', h)
    if bilingual:
        english_part, arabic_part = bilingual.group(1).strip(), bilingual.group(2).strip()
        if contains_arabic(arabic_part) and not contains_arabic(english_part):
            translated_arabic = translate_words_fallback(arabic_part, dicts)
            if translated_arabic.strip().lower() == english_part.lower():
                return english_part
    return translate_words_fallback(h, dicts)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Forms metadata column that's near-useless for this survey: when the form
# is set to collect anonymous responses, this column is just the literal
# string "anonymous" for every row -- it is NOT the respondent's email (the
# separate lowercase "email" question column is). Dropped by default; pass
# drop_columns=() to clean_dataframe to keep it.
DEFAULT_DROP_COLUMNS = ("Email",)


def clean_dataframe(df, dictionaries=None, drop_empty_cols=True, drop_columns=DEFAULT_DROP_COLUMNS):
    """Clean + fully translate a raw Microsoft Forms export.

    `drop_columns`: header names (matched after header whitespace-cleanup,
    before translation) to remove outright, e.g. the Forms system "Email"
    column that just reads "anonymous". Defaults to DEFAULT_DROP_COLUMNS;
    pass () or [] to keep every column.

    Returns (cleaned_df, merge_info) where merge_info is a list of dicts
    describing every group of branching-question columns (e.g. the 10
    المنطقه/المنطقه2/.../المنطقه10 columns) that got coalesced into one,
    each with the merged column's translated name, how many source columns
    it came from, and how many rows had more than one of them filled in
    (a conflict, where the first non-empty value was kept).
    """
    dicts = dict(DEFAULT_DICTIONARIES)
    if dictionaries:
        for key, default_map in DEFAULT_DICTIONARIES.items():
            dicts[key] = dictionaries.get(key, default_map)
    dicts = _normalize_dicts(dicts)

    cleaned = clean_headers(df)

    if drop_columns:
        cleaned = cleaned.drop(columns=[c for c in drop_columns if c in cleaned.columns])

    # 1. Merge Forms' branching-question column groups (e.g. المنطقه..المنطقه10)
    #    into a single column each, before any translation happens.
    merge_info = []
    groups = detect_branching_groups(cleaned)
    for base, cols in groups.items():
        merged_series, conflicts = merge_branching_group(cleaned, cols)
        first_pos = cleaned.columns.get_loc(cols[0])
        cleaned = cleaned.drop(columns=cols)
        cleaned.insert(min(first_pos, len(cleaned.columns)), base, merged_series)
        merge_info.append({
            "column": translate_header(base, dicts),
            "source_header": base,
            "merged_from": len(cols),
            "conflicts": conflicts,
        })

    # 2. Cell-level cleaning (whitespace, diacritics, digits, multiselect lists).
    text_cols = get_text_columns(cleaned)
    for col in text_cols:
        cleaned[col] = cleaned[col].apply(clean_whitespace)
        cleaned[col] = cleaned[col].apply(remove_diacritics)
        cleaned[col] = cleaned[col].apply(normalize_arabic_letters)
        cleaned[col] = cleaned[col].apply(clean_whitespace)
        cleaned[col] = cleaned[col].apply(to_western_digits)
        cleaned[col] = cleaned[col].apply(space_digit_arabic_boundary)
        cleaned[col] = cleaned[col].apply(clean_multiselect)

    # 3. Fix phone numbers that lost a leading 0 (Excel stored them as numbers).
    for col in cleaned.columns:
        if is_phone_column(col):
            cleaned[col] = cleaned[col].apply(fix_phone_number)

    # 4. Translate cell values, choosing dictionaries by column type. Track
    #    which columns are multiselect-splittable (e.g. Product) while we
    #    still have the original Arabic header to classify by.
    split_targets = [c for c in cleaned.columns if detect_column_type(c) in SPLIT_COLUMN_TYPES]
    for col in cleaned.columns:
        col_type = detect_column_type(col)
        cleaned[col] = cleaned[col].apply(lambda v: translate_cell(v, col_type, dicts))

    # 5. Translate headers last (so column-type detection above still saw Arabic).
    header_translation = {c: translate_header(c, dicts) for c in cleaned.columns}
    cleaned = cleaned.rename(columns=header_translation)

    # 5b. Split multiselect answers (e.g. "Fresh Yeast; Dry Yeast") into their
    #     own "Product 1", "Product 2", ... columns rather than one joined string.
    for orig_col in split_targets:
        cleaned = split_multiselect_column(cleaned, header_translation[orig_col], header_translation[orig_col])

    # 6. Drop fully empty columns.
    if drop_empty_cols:
        cleaned = cleaned.dropna(axis=1, how="all")

    return cleaned, merge_info
