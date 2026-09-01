# Bakery Form Response Cleaner

Streamlit app that takes the raw Arabic Excel export from your Microsoft
Forms bakery survey and returns a cleaned, fully English version — dictionary
based, fully offline (no external translation API calls).

## Files
- `app.py` — the Streamlit UI
- `cleaning_core.py` — dictionary-based cleaning/translation logic
- `pipeline.py` — low-level cleaning helpers used by `cleaning_core.py`
- `requirements.txt` — dependencies

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy: GitHub + Streamlit Community Cloud

**1. Create the GitHub repo**
- Go to https://github.com/new
- Name it (e.g. `bakery-form-cleaner`), set Public or Private, click **Create repository**

**2. Upload the files** (no git needed)
- On the new repo's page, click **Add file → Upload files**
- Drag in `app.py`, `cleaning_core.py`, `pipeline.py`, `requirements.txt`
- Commit directly to `main`

*(Or, if you prefer the command line, from this folder:)*
```bash
git init
git add app.py cleaning_core.py pipeline.py requirements.txt README.md
git commit -m "Bakery form cleaner"
git branch -M main
git remote add origin https://github.com/<your-username>/bakery-form-cleaner.git
git push -u origin main
```

**3. Deploy on Streamlit Community Cloud**
- Go to https://share.streamlit.io and sign in with GitHub
- Click **Create app** → pick your repo, branch `main`, main file `app.py`
- Click **Deploy** — you'll get a public URL in a couple minutes

That's it — every time you push a change to the repo, the deployed app updates automatically.
