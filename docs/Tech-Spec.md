# Duty Reminder Technical Spec

## 2026-07-21 Roster Editing and Template Import

### Goals
- Allow confirmed roster months to be edited with row additions and deletions, not only cell edits.
- Keep image import fast by using the fixed roster template parser only.
- Avoid full-image OCR during upload because it is slow and consumes too many CPU and memory resources.

### Non-Goals
- No new OCR engine is introduced.
- No Excel/CSV import is added in this change.
- No database schema change is required.

### Acceptance Criteria
- In the confirmed roster view, edit mode exposes an add-person action.
- In the confirmed roster table, edit mode exposes a delete action per row.
- Saved roster edits still use the existing overwrite save path and create normal roster versions.
- Uploading a template-like roster image returns `ocr_status: "template_ok"` with parsed shifts.
- Uploading a non-template image returns the existing unavailable fallback instead of invoking RapidOCR or PaddleOCR.
- Template import no longer attempts to OCR names or year/month from the full image; users can edit year/month and names in the review UI.

### Risks
- Without OCR name/month merging, users must review and fill placeholder names before confirming import.
- Template detection remains tied to the current table structure; screenshots with substantially different line spacing may need manual correction.

### Verification
- Add focused tests proving template import does not call the OCR reader.
- Add focused tests proving non-template images do not fall back to OCR.
- Run syntax checks and available tests before delivery.
