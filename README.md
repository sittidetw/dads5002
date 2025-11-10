# Simple Student/Teacher Q&A Checker (Streamlit)

This is a minimal Streamlit app for classroom Q&A.
- Student: answer questions
- Teacher: manage questions per Date/Week + check answers

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud
1. Push this repo to GitHub.
2. On https://share.streamlit.io, deploy the repo.
3. The app URL will be accessible publicly on mobile/desktop.

> Note: The app writes to a local SQLite file (`answers.db`). On Streamlit Cloud this storage is ephemeral (will reset on redeploy/restart).
