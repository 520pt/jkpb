# Project Memory

## Long-Term Context
- 2026-07-21: Project uses FastAPI with local static UI, SQLite data under `data/`, and uploaded roster images under `uploads/`.
- 2026-07-21: Roster image import relies on OCR plus image/template parsing using OpenCV/Pillow/RapidOCR.

## Operational Notes
- Do not store secret values here. Only document where configuration is expected.
- Deployment/runtime secrets are configured through environment variables or `.env`-style files, not committed project memory.

## Recent Work
- 2026-07-21: Added project instruction and memory files because the repository did not contain `AGENTS.md` or `MEMORY.md`, and no `~/.codex/templates/` directory was available.
- 2026-07-21: Roster template parsing must derive personnel row count from detected horizontal grid lines instead of assuming exactly 15 people; screenshots can contain 16 people and therefore 17 row boundary lines.
- 2026-07-21: Image import should use fixed-template parsing only and avoid full-image OCR on upload. Users review or manually fill names/year/month instead of spending resources on RapidOCR/PaddleOCR for the whole screenshot.
