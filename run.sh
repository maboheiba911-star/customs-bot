#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -r requirements.txt; }
[ -f .env ] || { cp .env.example .env; echo "Edit .env (token + admin id) then run again"; exit 1; }
exec .venv/bin/python bot.py
