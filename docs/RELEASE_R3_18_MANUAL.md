# R3.18 EXE candidate: manual downloads and in-app handbook

Scope: preserve R3.17 UI fixes and bundled OCR, FFmpeg, Python/native libraries,
Node, WebView2 and official SDK. No new dependency versions, paid calls, cloud
publication, application update feed or database schema changes.

ASR weights and render browser stay optional. Their component cards now show
clickable pinned domestic/official URLs, byte sizes and SHA-256 values. The new
owner-only `/api/components/import-file` accepts original downloaded files under
the same origin/session/confirmation controls as existing offline import, with a
bounded body, timeout, cancellation and local disk check. Uploaded names cannot
select paths. Embedded identity remains authoritative. A partial set waits for
the remaining manual files and cannot silently download missing files. A complete
set is exported to the existing trusted offline container and passes unchanged
hash, staging, health and atomic activation checks before use.

The existing `/api/components/import` continues to accept only application-format
offline ZIPs. Automatic downloads still use existing reviewed sources and hashes;
this release does not promise future mirror availability or install arbitrary
latest versions. OCR was deliberately kept bundled: an unverified domestic
download route is not a reason to force users through an unreliable first-run
installation.

In-app `使用指南` embeds a self-contained local HTML handbook. Fifteen chapters
cover installation, credentials, balance, each business menu, product/hotspot/
script/video handoffs, Hypit, references/audio, chat attachments, manual component
downloads, billing/unknown states, backup, troubleshooting and limitations. Search,
chapter links, browser print and offline HTML saving are included. No account or
private data are injected into this document. Existing narrower workflow guide
is retained. Source and EXE acceptance evidence must identify their scope.

Required focused evidence: component unit/fault tests, frontend tests, installed
EXE payload validation, actual localhost guarded original-file import of the
pinned render browser and ASR set with real health checks, and actual in-app
guide/manual links. Tests with fake small bytes must not be called real browser
or model execution. Clean-Windows/reboot, production signing/licensing and paid
provider release gates remain pending; this is not a formal stable release.
