@echo off
title KosFlow AI - Local Ollama Bridge
cd /d "%~dp0"
echo Memeriksa Ollama...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
  echo [INFO] Ollama belum aktif. Menjalankan ollama serve...
  start "" ollama serve
  timeout /t 3 /nobreak >nul
)
python ollama_bridge.py
pause
