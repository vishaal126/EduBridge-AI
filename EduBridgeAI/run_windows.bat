@echo off
cd /d %~dp0
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if "%ANTHROPIC_API_KEY%"=="" if not exist .streamlit\secrets.toml (
  echo.
  echo AI Courses and Mind Maps need an Anthropic API key. You can also paste it inside the app.
  set /p ANTHROPIC_API_KEY=Paste your key now, or just press Enter to skip: 
)
python -m streamlit run app.py
pause
