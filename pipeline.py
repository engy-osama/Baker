"""Cleaning and translation helpers for the Microsoft Forms response pipeline.

Kept separate from the Streamlit UI (app.py) so the logic can be unit-tested
and reused outside the app if needed (e.g. in a notebook or batch script).
"""
import re
import time
from collections import defaultdict

import pandas as pd
from deep_translator import GoogleTranslator

ARABIC_DIACRITICS = re.compile(r'[\u0617-\u061A\u064B-\u0652\u0670\u0640]')
ARABIC_RANGE = re.compile(r'[\u0600-\u06FF]')
ARABIC_INDIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
PHONE_KEYWORDS = ['تليفون', 'هاتف', 'موبايل', 'جوال', 'phone', 'mobile', 'tel']
NAME_ADDRESS_KEYWORDS = ['اسم', 'عنوان']


# ---------------------------------------------------------------------------
# Cell-level helpers
# ---------------------------------------------------------------------------

def contains_arabic(text) -> bool:
    """True if the value is a string containing at least one Arabic character."""
    return isinstance(text, str) and bool(ARABIC_RANGE.search(text))


def remove_diacritics(text):
    """Strip Arabic diacritics (tashkeel) and the tatweel elongation character."""
    if not isinstance(text, str):
        return text
    return ARABIC_DIACRITICS.sub('', text)


def normalize_arabic_letters(text):
    """Unify common Arabic spelling variants: أ/إ/آ -> ا and ى -> ي.

    These are safe, meaning-preserving normalizations for informal survey
    text (they don't touch ة, which does carry grammatical meaning).
    """
    if not isinstance(text, str):
        return text
    text = re.sub(r'[إأآ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    return text


def to_western_digits(text):
    """Convert Arabic-Indic digits (٠-٩) to Western digits (0-9).

    Common in addresses/quantities typed on an Arabic keyboard.
    """
    if not isinstance(text, str):
        return text
    return text.translate(ARABIC_INDIC_DIGITS)


def clean_whitespace(text):
    """Collapse repeated whitespace and trim the ends."""
    if not isinstance(text, str):
        return text
    return re.sub(r'\s+', ' ', text.strip())


def clean_multiselect(text):
    """Tidy semicolon-separated multi-select answers (Forms' checkbox-question format).

    Trims each item and drops empties left by a trailing ';', e.g.
    'أ;ب ;' -> 'أ; ب'. Leaves single-answer text untouched.
    """
    if not isinstance(text, str) or ';' not in text:
        return text
    parts = [p.strip() for p in text.split(';') if p.strip()]
    return '; '.join(parts)


def is_phone_column(colname) -> bool:
    name = str(colname).lower()
    return any(k in name for k in PHONE_KEYWORDS)


def is_name_or_address_column(colname) -> bool:
    """True for columns like 'الاسم الكامل' or 'العنوان بالتفصيل' — names and
    addresses are proper nouns/practical directions, not really 'content' to
    translate, so these are excluded from the default translation selection."""
    name = str(colname)
    return any(k in name for k in NAME_ADDRESS_KEYWORDS)


def fix_phone_number(value):
    """Best-effort fix for Egyptian phone numbers that lost a leading 0.

    This happens when Excel/Forms stores a numeric-looking answer as an
    actual number. Only acts when the digits unambiguously match a mobile
    number missing that leading zero (10 digits, starting with 1 — i.e. the
    010/011/012/015 prefix with the 0 dropped); anything else is returned
    as a plain string rather than guessed at.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return value
    digits = re.sub(r'\D', '', str(value))
    if not digits:
        return str(value)
    if len(digits) == 11 and digits.startswith('0'):
        return digits
    if len(digits) == 10 and digits[0] == '1':
        return '0' + digits
    return str(value)


# ---------------------------------------------------------------------------
# Column / dataframe-level helpers
# ---------------------------------------------------------------------------

def get_text_columns(df):
    """Return column names holding text, regardless of pandas' object/string dtype flavor."""
    return [c for c in df.columns if pd.api.types.is_string_dtype(df[c])]


def clean_headers(df):
    """Strip embedded newlines / non-breaking spaces / extra whitespace from column names.

    Microsoft Forms export headers sometimes carry a literal newline or a
    non-breaking space copied over from the question text formatting.
    """
    def _clean(name):
        name = str(name).replace('\n', ' ').replace('\xa0', ' ')
        return re.sub(r'\s+', ' ', name).strip()
    return df.rename(columns={c: _clean(c) for c in df.columns})


def detect_arabic_columns(df, threshold: float = 0.3):
    """Return column names where at least `threshold` share of non-null values contain Arabic."""
    arabic_cols = []
    for col in get_text_columns(df):
        sample = df[col].dropna().astype(str)
        if len(sample) == 0:
            continue
        ratio = sample.apply(contains_arabic).mean()
        if ratio >= threshold:
            arabic_cols.append(col)
    return arabic_cols


def detect_branching_groups(df):
    """Detect column groups that look like Microsoft Forms branching-logic artifacts.

    A conditional question (e.g. "which area?", where the options depend on
    an earlier "which governorate?" answer) makes Forms write one column per
    branch: المنطقه, المنطقه1, المنطقه2, ... with only one filled per row.
    Returns {base_name: [col, col1, col2, ...]} for groups of 2+ columns,
    in first-seen order.
    """
    groups = defaultdict(list)
    order = []
    for col in df.columns:
        base = re.sub(r'\d+$', '', str(col)).strip()
        if base not in groups:
            order.append(base)
        groups[base].append(col)
    return {b: groups[b] for b in order if len(groups[b]) > 1}


def merge_branching_group(df, cols):
    """Coalesce a set of mutually-exclusive-per-row columns into one series.

    Returns (merged_series, conflict_row_count). A conflict is a row where
    more than one column in the group is filled — meaning these columns
    probably aren't pure branching artifacts, so the caller should surface
    the count as a warning rather than merge silently.
    """
    sub = df[cols]
    conflicts = int((sub.notna().sum(axis=1) > 1).sum())
    merged = sub.bfill(axis=1).iloc[:, 0]
    return merged, conflicts


def clean_dataframe(df, drop_empty_rows=True, drop_duplicates=True, duplicate_subset=None,
                     strip_whitespace=True, normalize_arabic=True, normalize_digits=True,
                     clean_multiselect_lists=True, fix_phones=True):
    """Apply the selected cleaning steps and return (cleaned_df, duplicates_removed).

    `duplicate_subset`: None checks the full row; pass a list of column names
    (e.g. ["Email"]) to instead flag duplicates by those columns only. This
    matters for form exports, where a real full-row duplicate is rare (every
    submission gets its own ID/timestamp) but a repeat submission by the same
    respondent is common and worth catching.
    """
    cleaned = clean_headers(df)  # always tidy header text — cheap, safe, never surprising

    if fix_phones:
        for col in cleaned.columns:
            if is_phone_column(col):
                cleaned[col] = cleaned[col].apply(fix_phone_number)

    if drop_empty_rows:
        cleaned = cleaned.dropna(how='all')

    text_cols = get_text_columns(cleaned)
    if strip_whitespace:
        for col in text_cols:
            cleaned[col] = cleaned[col].apply(clean_whitespace)
    if normalize_arabic:
        for col in text_cols:
            cleaned[col] = cleaned[col].apply(remove_diacritics)
            cleaned[col] = cleaned[col].apply(normalize_arabic_letters)
            cleaned[col] = cleaned[col].apply(clean_whitespace)  # re-collapse spaces after normalization
    if normalize_digits:
        for col in text_cols:
            cleaned[col] = cleaned[col].apply(to_western_digits)
    if clean_multiselect_lists:
        for col in text_cols:
            cleaned[col] = cleaned[col].apply(clean_multiselect)

    removed = 0
    if drop_duplicates:
        before = len(cleaned)
        cleaned = cleaned.drop_duplicates(subset=duplicate_subset, keep='first')
        removed = before - len(cleaned)

    return cleaned, removed


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def translate_value(text, translator=None):
    """Translate a single Arabic string to English.

    Returns the original text unchanged on failure or non-Arabic input,
    so a translation hiccup never crashes the pipeline or blanks a cell.
    """
    if not isinstance(text, str) or not text.strip() or not contains_arabic(text):
        return text
    translator = translator or GoogleTranslator(source='ar', target='en')
    try:
        return translator.translate(text)
    except Exception:
        return text


def translate_column(series, progress_callback=None, delay=0.05):
    """Translate a pandas Series, caching by unique value to minimize API calls.

    `progress_callback(fraction_done)` is called after each unique value if provided.
    `delay` adds a small pause between calls to be gentle on the free endpoint.
    """
    translator = GoogleTranslator(source='ar', target='en')
    unique_vals = [v for v in series.dropna().unique().tolist() if contains_arabic(v)]
    cache = {}
    for i, val in enumerate(unique_vals):
        cache[val] = translate_value(val, translator)
        if delay:
            time.sleep(delay)
        if progress_callback:
            progress_callback((i + 1) / max(len(unique_vals), 1))
    return series.apply(lambda x: cache.get(x, x))
