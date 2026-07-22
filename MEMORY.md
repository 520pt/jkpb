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
- 2026-07-22: Monitored reminder editing uses optional `original_name` on `POST /api/people` to support renaming without leaving the old monitored row. `DELETE /api/people/{name}` removes monitored reminder configs only; shared personnel contacts are preserved.
- 2026-07-22: Template shift recognition must keep white or near-white single `中` cells as empty (`-` in the UI). Colored shift cells, regardless of fill color, should become `中`/`早`/`晚`; automatic recheck should reclassify each current grid cell by its stored image box.
- 2026-07-22: Template `出差` should only come from a white cell with two stacked text groups; do not use high ink density as a global `出差` fallback. Day-column detection should prefer the real 32-column shift grid if stray left-side vertical lines could shift dates by one.
- 2026-07-22: Automatic recheck should reparse the fixed template grid before diffing current cells so stale shifted boxes can be corrected. The source image view should show the active cell's row name and day near the highlighted cell and in the image header.
