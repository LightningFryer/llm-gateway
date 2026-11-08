@echo off
title Relay Gateway (Port 8000)
echo Starting Relay LLM Gateway on http://localhost:8000 ...
set PYTHONPATH=backend\src
.venv\Scripts\uvicorn relay.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
pause

