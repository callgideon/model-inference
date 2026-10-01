# Live audit evidence

Captured from the live App/Lab on 2026-10-01 and inspected from the saved image bytes. These are current-product screenshots, not proposed designs. The [audit](../01-audit.md) maps each step and finding to the numbered PNG. Files are unedited; no crops or synthetic overlays were used.

- `01`–`07`, `16`: Lab access/overview/control/requests/evaluations/datasets/settings.
- `08`–`15`: Consumer access/models/keys/usage/credits/docs/settings.
- `17`: live false zero-hour physical-deletion statement.
- `18`–`19`: mobile layout and menu remaining open after Escape.

Read-only interactions used the existing provisioned test account. Its business email/workspace name appears in some captures. Passwords, API secrets, customer media and real inference results were not captured. Account mutations, live inference and worker enablement were not performed by this UX audit.

The public catalog's retention object was read without credentials during the same audit. Relevant values: `physical_deletion_bound_s: null`, result 86400s, stream journal 3600s, idempotency 86400s and processing cache 604800s. The live Docs image records the resulting incorrect text. No inference about actual object deletion was made.

Mobile keyboard reproduction at 390×844: Open menu → Escape left the menu visible and focus on Open menu. Close menu → focus Open menu → Tab focused the hidden `infrx` link with bounds x=-224, right=-190.421875, y=12, height=24. Source uses offscreen translation without a modal focus lifecycle. This is separately captured in the audit text because a screenshot alone cannot prove focus order.

`manifest.json` lists file dimensions, hashes and filesystem capture/write times. UI source release is recorded in the audit, not inferred from image metadata. Proposed designs and their own verification live under [previews](../previews/README.md).
