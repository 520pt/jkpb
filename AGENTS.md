# Project Instructions

## Project
- Name: duty-reminder
- Created from local repository inspection on 2026-07-21.
- Purpose: Monitoring duty reminder service with roster image import and WeCom notifications.

## Stack
- Python >= 3.10
- FastAPI / Uvicorn
- SQLite-backed local data storage
- OpenCV, Pillow, RapidOCR for roster image parsing
- pytest for tests
- Docker and docker compose for deployment

## Main Directories
- `app/`: application code and static UI.
- `tests/`: pytest test suite.
- `data/`: local SQLite data. Do not delete casually.
- `uploads/`: uploaded roster images. Do not delete casually.
- `work/`: local test artifacts and screenshots.

## Common Commands
```powershell
python -m pytest
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

## Coding Rules
- Keep changes focused on the user request.
- Prefer existing helpers and project patterns before adding new abstractions.
- Avoid hardcoded credentials, tokens, passwords, and private URLs.
- Do not read, print, or store secret values from `.env` or credential files.
- For roster parsing changes, add or update focused tests where practical.

## Verification
- Run the relevant pytest tests before reporting completion.
- For image parsing or UI changes, use representative input images or the strongest available local substitute.
