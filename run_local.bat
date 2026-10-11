@echo off
REM One-time setup for Windows. Then start the two services shown at the end.
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if not exist .env copy .env.example .env
echo Terminal 1: uvicorn api.main:app --reload --port 8000
echo Terminal 2: streamlit run app/streamlit_app.py
pause
