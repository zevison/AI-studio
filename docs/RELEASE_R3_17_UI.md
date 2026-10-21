# R3.17 UI candidate

This candidate fixes presentation and empty-state controls. It does not change
provider calls, budgets, database schemas, dependency versions or production feeds.
Existing clean-Windows, reboot, signing, licensing and paid-provider release gates
remain open. No deployment or paid test is part of this change.

## Root causes and corrections

- Prompt buttons were shrinking inside a height-limited flex list. They now have
  non-shrinking natural height, bounded descriptions and a scrollable list. The
  list/detail layout responds to its card width, not only the window width.
- The generic radar form label set flex-direction:column on evaluation checkboxes.
  Scoped styles place an 18px checkbox beside wrapping text in a clickable row.
  Without a completed local video, scores and acknowledgements are disabled;
  backend acceptance conditions and explicit human confirmation remain intact.
- Settings paired unequal cards into rows. Existing whole cards now flow in two
  content-balanced columns, collapsing to one on narrower windows. Controls, IDs,
  values and listeners are moved intact; no fields are removed or duplicated.

## Further package reduction: assessment, not implemented capabilities

Exact R3.16 installed payload bytes (not compressed installer savings):

| Group | Bytes | Decision |
| --- | ---: | --- |
| WebView2 | 705207356 | Needed to open the UI. Keep bundled in this complete candidate. |
| FFmpeg | 317626054 | Shared by analysis, output, audio and validation. Keep bundled. |
| OCR | 115254179 | Best next independent optional tool candidate; needs pinned distribution and actual import/health/rollback tests. |
| Node | 87268557 | Required by official local SDK execution. Keep bundled. |
| Hypit SDK ZIP | 46138904 | Already verified/extracted lazily; downloads would add source and licensing gates. |

ASR model weights and the rendering browser are already deferred with explicit
consent; R3.16 core installer was 728999902 bytes versus 1293583235 in R3.10.
Do not add installed byte counts together and promise that amount of compression
savings. Full installation of all plugins may use more total transfer than the
original monolithic installer due to separate packaging.

A separate online-only edition could detect an installed Evergreen WebView2
Runtime and silently install it if missing during setup, as Microsoft documents:
https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
That is installer-time provisioning, not a plugin which can wait until the UI
opens. It requires independent compatibility, signature, Windows lifecycle and
domestic-network acceptance; it has not been enabled or advertised as tested here.
Keep a complete offline-capable core installer available.

Native ASR Python extensions cannot simply be removed from the frozen Python
tree. A separate signed, ABI-compatible worker is a possible future boundary,
with its own dependency and rollback acceptance. No such worker is claimed by
this candidate. Do not turn base Python into a runtime pip installation flow.
