# Proposed design previews and checks

These screenshots show [design-reference.html](../design-reference.html), a standalone illustrative design document. No server, database, auth or inference integration is represented by a successful interaction here.

| File | State | Viewport |
|---|---|---|
| [Lab overview](01-lab-overview.png) | Empty workspace with guided setup | 1440×1000 |
| [Registration](02-lab-registration.png) | Read-only illustrative revision fields | 1440×1000 |
| [Consumer first call](03-consumer-first-call.png) | Key → connectivity → result | 1440×1000 |
| [Expired result](04-consumer-expired-result.png) | Access expired, accounting retained | 1440×1000 |
| [Content access](05-lab-content-access.png) | Metadata-only provider record | 1440×1000 |
| [Consumer mobile](06-consumer-mobile.png) | First-call hierarchy | 390×844 |
| [Lab mobile](07-lab-mobile.png) | Workspace setup | 390×844 |

Screenshots were saved and reopened without editing. [Browser checks](browser-checks.json) record page width and title for all 11 screens at 1440px, App at 390/320px and Lab at 390/768px; no whole-page horizontal overflow was observed in those cases. Code/table regions may scroll internally.

Interactive checks performed: registration → review → explicit no-mutation receipt; App key dialog → Escape → focus returns to Create API key; mobile navigation → Escape → focus returns to Menu; request metadata-access selector; expired-result selector. Select controls retain focus after state changes. Reference JavaScript syntax and local documentation links were checked.

This is not a full WCAG conformance assessment, real-service test or user study. See [implementation verification](../07-handoff.md) for required product acceptance. The existing task graph/tracker validation passed; new UX work is still proposed and has not been promoted to implemented.
