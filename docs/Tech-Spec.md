# Duty Reminder Technical Spec

## 2026-07-21 Roster Editing and Template Import

### Goals
- Allow confirmed roster months to be edited with row additions and deletions, not only cell edits.
- Keep image import fast by using the fixed roster template parser for shifts and local OCR for the name column only.
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
- Template import may run OCR only on the name-column crop, not on the full image.
- Uploading a non-template image returns the existing unavailable fallback instead of invoking RapidOCR or PaddleOCR.
- Template import no longer attempts to OCR names or year/month from the full image; users can edit year/month and any missed names in the review UI.

### Risks
- If name-column OCR misses a row, users must review and fill placeholder names before confirming import.
- Template detection remains tied to the current table structure; screenshots with substantially different line spacing may need manual correction.

### Verification
- Add focused tests proving template import does not call the full-image OCR reader.
- Add focused tests proving non-template images do not fall back to OCR.
- Run syntax checks and available tests before delivery.

## 2026-07-22 Custom Shift Reminders

### Goals
- Add a custom reminder settings view for person-specific, shift-specific reminder rules.
- Support examples such as reminding 商邱宏 at 21:00 on night shift to close tunnel lights, and at 07:50 on early shift to open tunnel lights.
- Store a person's mobile number with the shared personnel list after any reminder configuration saves a name and mobile.
- Reuse one combo-style text input for name selection and manual entry instead of separate dropdown and manual inputs.

### Data Model
- `personnel_names` stores `name` and optional `mention_mobile`.
- `custom_reminders` stores person name, optional mobile override, shift code, reminder time, message, and enabled state.
- Shift code values follow the existing roster model: `early`, `middle`, `night`.

### Behavior
- Custom reminder rules match confirmed roster assignments by exact person name and shift.
- The reminder send date is the roster work date combined with the configured reminder time.
- Webhook @ mobile is resolved from the custom reminder mobile first, then the shared personnel list, then monitored people.
- Saving a monitored person or custom reminder upserts the shared personnel mobile cache.

### Acceptance Criteria
- Users can create and list custom shift reminders in the UI.
- Users can delete custom reminders.
- Reminder preview includes matching custom reminder events.
- Due reminder sending includes custom reminders and records them as `custom`.
- Name inputs for monitored reminders, custom reminders, and driver assignment use the same editable datalist-style input pattern.

### Verification
- API tests cover custom reminder CRUD, personnel mobile autofill, preview generation, and due sending.
- Frontend script syntax passes.
- Full pytest passes.
