# GitHub private repository and Codex Cloud

This repository contains the authoritative Seedance Studio source. `implementation`,
`studio`, `stage4`, `work`, prior release ZIPs and the Windows user profile are not
required sibling directories. Do not upload those directories wholesale.

## Publish the repository

The migration commits only to local `master`. In GitHub Desktop, publish this
existing repository with **Keep this code private** checked. No remote or public
repository is created by the migration. Do not publish user backup ZIPs as source.

## Prepare Codex Cloud

Use a development-only environment, Python 3.12 and Node 22.16.0. Select only the
new private repository. Ask setup to run `bash .codex/setup.sh`, then validate with
`.venv/bin/python tools/dev_check.py`. The script's exported variables are not
needed after setup; always invoke the repository virtualenv explicitly.

In the current Cloud experience: Work in > Cloud > Create environment, select the
repository, review dependency installation and test results, then publish the
prepared environment. Publishing the environment is a user operation after GitHub
publication, not an action performed by the local migration. Existing legacy
Code Review/GitHub integrations have a separate setup-script interface.

Start instructions: this is an offline source-development environment. Run the
free checks above; do not start production services. A disposable UI preview can
use `HOST=0.0.0.0 PORT=8765 OPEN_BROWSER=0 .venv/bin/python app.py` only in the
isolated development environment, with no account keys or production data.

Use package-download network access for `registry.npmjs.org`, `pypi.org` and
`files.pythonhosted.org` as needed. Windows artifact builds also fetch pinned
public files from `nodejs.org`, `storage.googleapis.com` and `www.gyan.dev`.
Do not enable model-provider, OSS, production-domain or private VPN access for
ordinary source work. No API keys are needed for setup or offline checks. The
Windows DPAPI vault cannot be migrated by copying it to another account/Cloud VM.

Official documentation checked 2026-10-20:

- [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)
- [Repository instructions](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

## Rebuild a complete Windows candidate

On Windows x64 with Git, Python 3.12, Node 22.16.0/npm installed:

```text
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts --prefix tools/hypit_provider --no-audit --no-fund
npm run build:offline --prefix tools/hypit_provider
python tools/dev_check.py
python tools/repository_safety.py
python tools/build_windows_release.py --output build/SeedanceStudio_Candidate
```

Run from a clone containing the committed source. The builder includes Git-tracked
files only, checks public runtime archive SHA256 pins, builds the SDK payload from
the npm lock and validates source/archive hashes. It never reads the account vault
or calls a paid model. It refuses existing build destinations. Build output and
download caches are ignored; previous colleague packages remain untouched.

The optional `.github/workflows` files are **manual-dispatch only**, read-only
repository permissions, no publishing or production secrets. Do not enable them
unless their hosted-runner usage is acceptable. They have not run on GitHub yet.

`tools/release_candidate.py` remains a compatibility tool for historical audit
baselines. The canonical new-clone build is `tools/build_windows_release.py` and
does not require an old binary candidate ZIP. `.gitignore` intentionally excludes
downloaded Node/browser/FFmpeg, node_modules, profiles, secrets and generated
manifests. It retains fonts/licenses, models, fixtures, official Skill, provider
source, scripts, lockfile, compiled dist and all application modules.

## Acceptance limits

Cloud execution is not Windows installation acceptance. Keep status B until real
clean Windows install/reboot/reinstall/upgrade/rollback/uninstall and remaining
external business gates have evidence. Paid grants in old reports are historical
and must never authorize new calls. A rebuilt archive can have different ZIP/npm
metadata hashes while every committed application source byte still matches.

The Git commit changes source/build tooling, so earlier R3.8 installation ZIPs
remain historical immutable artifacts. Use the repository builder for the exact
new commit and its `REPOSITORY_BUILD.json`/receipt for file mapping.
