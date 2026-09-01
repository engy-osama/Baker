"""
Bakery Form Response Cleaner (English output)
-----------------------------------------------
Streamlit app: upload the raw Excel export from the Microsoft Forms bakery
form (Arabic questions/answers) and get back a cleaned, fully English
version, using dictionaries to translate:

  1. Column headers          (Arabic Forms question -> English label)
  2. Known categorical answers (governorate, yes/no, bakery type,
                                 yeast product, company, area)
  3. Common connector words / Egyptian names, as a fallback for anything
     not in a dictionary (personal names, addresses, free text)

Digits are also converted from Arabic-Indic numerals to Western numerals,
and the 10 duplicate "region" columns Forms exports for a single branching
question are merged into one Area/District column.

Every dictionary is editable from the sidebar (JSON), so translations can
be extended as new responses come in without touching the code.

Run with:
    pip install streamlit pandas openpyxl
    streamlit run app.py
"""
import json
from io import BytesIO

import pandas as pd
import streamlit as st

from cleaning_core import clean_dataframe, DEFAULT_DICTIONARIES, DEFAULT_DROP_COLUMNS

st.set_page_config(page_title="Bakery Form Response Cleaner", layout="wide")

st.title("🧹 Bakery Form Response Cleaner")
st.caption(
    "Upload the Excel file exported from Microsoft Forms and get back a cleaned, "
    "fully English version. Headers and known answers are translated via dictionaries; "
    "personal names and addresses are transliterated (phonetic spelling), since no fixed "
    "dictionary can cover every possible name."
)

# ---------------------------------------------------------------------------
# Sidebar: editable dictionaries
# ---------------------------------------------------------------------------
st.sidebar.header("⚙️ Cleaning Settings")

drop_empty = st.sidebar.checkbox("Drop fully empty columns", value=True)
drop_system_email = st.sidebar.checkbox(
    "Remove system 'Email' column (usually just \"anonymous\")", value=True
)

st.sidebar.subheader("Translation Dictionaries")
st.sidebar.caption(
    "Edit any dictionary below (JSON). Unknown Arabic values automatically fall back to "
    "transliteration rather than being left untranslated."
)

dict_labels = {
    "header_map": "Column headers",
    "governorate_map": "Governorate names",
    "area_map": "Area / district names",
    "yes_no_map": "Yes / No answers",
    "bakery_type_map": "Bakery type",
    "product_map": "Yeast product",
    "company_map": "Company / brand names",
    "common_words_map": "Common words & names (fallback)",
}

dictionaries = {}
for key, label in dict_labels.items():
    with st.sidebar.expander(label, expanded=False):
        text = st.text_area(
            label,
            value=json.dumps(DEFAULT_DICTIONARIES[key], ensure_ascii=False, indent=2),
            height=180,
            label_visibility="collapsed",
            key=f"dict_{key}",
        )
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError
            dictionaries[key] = parsed
        except (json.JSONDecodeError, ValueError):
            st.error(f"Invalid JSON in '{label}' — using the default dictionary instead.")
            dictionaries[key] = DEFAULT_DICTIONARIES[key]

# ---------------------------------------------------------------------------
# Main: upload + clean + preview + download
# ---------------------------------------------------------------------------
uploaded = st.file_uploader("Upload response file (Excel)", type=["xlsx", "xls"])

if uploaded is not None:
    try:
        raw_df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Couldn't read the file: {e}")
        st.stop()

    cleaned_df, merged_regions = clean_dataframe(
        raw_df,
        dictionaries=dictionaries,
        drop_empty_cols=drop_empty,
        drop_columns=DEFAULT_DROP_COLUMNS if drop_system_email else (),
    )

    st.success(
        f"Cleaned {len(cleaned_df)} row(s). "
    )

    if merged_regions:
        with st.expander("🔀 Merged branching-question columns", expanded=False):
            for m in merged_regions:
                note = (
                    f" — {m['conflicts']} row(s) had more than one filled in; "
                    "the first value was kept."
                    if m["conflicts"]
                    else ""
                )
                st.write(
                    f"**{m['column']}**: merged from {m['merged_from']} "
                    f"columns (`{m['source_header']}` + variants){note}"
                )

    tab1, tab2 = st.tabs(["📄 Original data", "✨ Cleaned data (English)"])
    with tab1:
        st.dataframe(raw_df, width='stretch')
    with tab2:
        st.dataframe(cleaned_df, width='stretch')

    # Build downloadable Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        cleaned_df.to_excel(writer, index=False, sheet_name="Cleaned")
    buffer.seek(0)

    st.download_button(
        label="⬇️ Download cleaned English file",
        data=buffer,
        file_name="bakery_form_cleaned_en.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.info("Waiting for a file to be uploaded.")
