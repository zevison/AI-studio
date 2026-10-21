# R3.16 plugin candidate — release gates remain open

## Actual dependency graph

`Chinese Inno installer -> directory EXE -> launcher -> Python backend -> SQLite + local web UI`

`Hypit -> verified bundled SDK -> bundled Node + FFmpeg -> optional render browser -> output/FFprobe`

`local transcription -> bundled native ASR engine -> optional multilingual small model`

`chat / video / collection -> configured OpenLux or Bright Data remote API`

| Class | Components | Distribution decision |
| --- | --- | --- |
| A core | Python/native libraries, WebView2, Node, FFmpeg, OCR, SDK archive, UI, migrations, configuration | Bundled; no pip/npm/download at each startup. SDK is verified/extracted at first actual use. |
| B deferred | Pinned Chrome headless shell, four ASR model files | Explicit per-component consent, exact hashes, version directory, executable health check before activation. |
| C optional | Local transcription / browser rendering | Missing optional component prevents that operation, not base UI startup. |
| D remote | OpenLux/model APIs, Bright Data, user OSS | User-configured online services; no large local plugin or credentials in installer. |
| E external gate | Production signing, owned CDN/OSS, redistribution clearance | Not provisioned or declared complete by this build. |

## User behavior

Settings > application maintenance > plugins and components. First use explains
purpose, version, download/install sizes and sources. User approval is tied to
the exact descriptor. Automatic on-demand download and interrupted-job resume
are separate opt-ins, both off by default. Pause, resume, retry, verified repair,
reversible removal, logs and offline ZIP import use authenticated local-owner APIs.
Other workspaces can view status but cannot mutate global components.

Offline imports accept only bytes matching this application's embedded pins.
Interrupted uploads require re-import; interrupted verified imports retain the
managed ZIP for local resume, never silently fall back to the internet. Original
user packages are not deleted. Cache cleanup removes known managed artifact files
only, never installed components, source, old installers or user data.

Downloads are serial, bounded and cancellable. HTTPS transport rejects private
addresses, credentials, unknown redirects and environment proxies; response
validators, Range, exact length and full SHA-256 protect resumed bytes. Primary
source changes restart a partial download conservatively. Disk reserve is checked
before and during transfer. Native health runs in an isolated process. Shared
browser/read and exclusive activation leases protect in-use files; an activation
journal recovers interruption between directory renames. Old bytes are retained.
Actual Windows testing exposed a short sharing violation after native browser
exit. R3.13 retries only the corresponding Windows rename errors under the writer
lease for at most five seconds; permanent denial still fails visibly. Native
browser checks plus activation passed three consecutive current-host trials,
each exercising six retries. Cleanup preserves occupied staging files and cannot
erase the original error or leave an unhandled worker-thread exception.

## Sources and update boundaries

npmmirror mappings follow cnpm's maintained binary-mirror configuration:
https://github.com/cnpm/binary-mirror-config . ASR uses the ModelScope
Systran/faster-whisper-small repository with the original pinned file hashes.
These are live-tested on the current host, not a nationwide availability SLA.
Official upstream sources remain fallbacks. A mirror cannot select newer bytes.

Application and plugin updates use separate TUF policies. Independent plugin
checking verifies fresh timestamp/snapshot/targets metadata, a continuous root
rotation chain, target length/hash, monotonically admitted release sequence, exact
host ABI, Windows x64 and reviewed install/health interfaces. The SHA-addressed
proof is reverified offline before executing a selected version; the application
never trusts an unsigned mutable catalog. Native health must pass before the
atomic active selection changes. A manual rollback validates the retained files
and runs native health again. All download/update/rollback actions require the
local owner's session and explicit installation confirmation.

Installed approved bytes remain usable offline after repository metadata expiry;
SDK payload ZIPs use stable ordering, timestamps and regular-file metadata so
an unchanged dependency tree retains its archive hash and host ABI across rebuilds.
This guarantee concerns the SDK ZIP under the locked toolchain, not a claim that
the entire Windows installer is bit-for-bit reproducible across operating systems.
new admissions must use unexpired metadata and match the highest admitted release.
Tampered optional records are rejected and cannot prevent base application startup.
The bundled policy is disabled. No production public root, signing service, owned
repository or domain is fabricated. Local actual Ed25519/TUF protocol tests and
native Windows checks are evidence of implementation, not production publication.
TUF guidance: https://theupdateframework.io/docs/overview/ .

The signed target paths are `SeedanceStudio/plugins/asr.json` and
`SeedanceStudio/plugins/browser.json`. A target contains format
`seedance.plugin-release@1`, product_id `SeedanceStudio`, id, positive sequence,
host_abi_sha256 (from `core.component_updates.abi` for the reviewed host), notes,
and a complete @2 component descriptor. Artifact and installed-file hashes must
come from actual verified build inputs. Publish with a separately reviewed TUF
signing workflow; keep root keys offline, preserve rotation history, and never
include private keys in the app or repository. Feed URLs and the bootstrap root
are embedded only after release-owner authorization. An asset template with
enabled=false is intentionally insufficient to enable networking.

`tools/build_component_repository.py` produces a local immutable repository plus
offline packs from verified cached artifacts. `Publish_ComponentRepository.ps1`
defaults to dry-run and requires explicit execution and redistribution clearance.
It does not acquire cloud resources, create credentials, configure a CDN or sign
metadata. Nothing has been published to a user domain or cloud account.

## Licensing and evidence

FFmpeg obligations depend on actual build options; bundled Gyan build is subject
to its included license. Verify corresponding-source availability and notices
before public redistribution: https://www.ffmpeg.org/legal.html . Chromium source
license does not by itself establish every redistribution term for Google's
Chrome-for-Testing binary; preserve third-party notices and verify that artifact's
terms: https://github.com/chromium/chromium/blob/main/LICENSE . Hypit vendor skill
license and npm dependency licenses remain included. Full redistribution clearance
and Authenticode are pending; do not label an unsigned candidate signed/stable.

Current-host native tests, simulated failures, static tests, clean-Windows install,
OS reboot and real paid business tests are distinct gates. See the delivery report
for actual evidence. Missing external gates cannot be substituted with mocks.
