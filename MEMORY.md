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
- 2026-07-21: Image import should use fixed-template parsing for shifts and avoid full-image OCR on upload. Name recognition may use local OCR on the cropped name column only; users review or manually fill missed names/year/month instead of spending resources on OCR for the whole screenshot.
- 2026-07-22: Custom shift reminders are stored in `custom_reminders` and are matched by roster date, person name, and shift code. Webhook @ mobile resolution is custom reminder mobile, then shared personnel contact, then monitored person contact.
- 2026-07-22: `personnel_names` now stores optional `mention_mobile`; saving monitored people or custom reminders upserts that shared contact cache so future name inputs can autofill mobiles.
