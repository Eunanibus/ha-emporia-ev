# Runbook: Release mechanism & brand iconset for a HACS custom integration

Audience: an agent or developer building a HACS custom integration similar to
`ha-emporia-ev`. Written 2026-08-14. Everything below was verified against this
repo, the live `home-assistant/brands` repo, and a HAR capture of a real HA
2026.8.1 frontend. Where something is **unverified**, it says so.

Replace `emporia_ev` / `Emporia EV Charger` / `Eunanibus` with your own values.

## Part 1: Brand icons (read this before you spend time on assets)

### The one-paragraph summary

Ship your icons in `custom_components/<domain>/brand/`. That makes them appear
on **Settings → Devices & Services** and the device page on HA 2026.3+. It does
**not** make them appear in the **HACS store / downloads list**, which still
resolves icons via the `brands.home-assistant.io` CDN, and the CDN no longer
accepts new custom integrations. There is currently no way for a custom
integration to get an icon in the HACS store view. Budget zero effort for it.

### What changed, and when

| Date       | Event                                                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-02-24 | Brands Proxy API announced. Custom integrations can ship a local `brand/` folder. HA 2026.3.0+.                                                         |
| 2026-03-03 | `home-assistant/brands` adds `.github/workflows/close-new-custom-integrations.yml`, which **auto-closes any PR adding a new `custom_integrations/` folder**. |
| now        | `custom_integrations/` is labelled a "Legacy folder" in the brands README.                                                                              |

### Do NOT open a PR against home-assistant/brands

It will be closed by a bot, with no maintainer involvement. The workflow
triggers on `pull_request_target` for `paths: custom_integrations/**` and
classifies a folder as _new_ when every touched file has `status === 'added'`:

```js
if (file.status !== "added") {
  existingFolders.add(match[1]);
}
const newFolders = [...allFolders].filter((f) => !existingFolders.has(f));
```

A brand-new folder is pure additions, so it matches, gets a canned comment, and
`pulls.update({state: 'closed'})`.

You will see recent merged commits under `custom_integrations/` and conclude
submissions are still open. They are not. Sorted by kind, those commits are:

- **moves out** to `core_integrations` (delete from `custom_integrations`),
- **moves in** from `core_integrations` (e.g. #10626, 2026-06-27, `aten_pe`),
- **deletions**,
- **updates to already-existing folders**.

None is a pure addition: a move reports as `renamed`, and edits/removals report
as `modified`/`removed`, so the folder lands in `existingFolders` and the bot
passes it over. Two worked examples, both verified with `git show --name-status`:

- #10958 (2026-08-13, the most recent at time of writing): "Move Monzo assets
  to core integrations". All four files are `R100`, pure renames out of
  `custom_integrations/monzo/`. Nothing was added.
- #10626 (2026-06-27): added `custom_integrations/aten_pe/`, but as a move from
  `core_integrations`, so the diff also deleted the originals.

The last additions of genuinely new custom folders by outside contributors
cluster on 2026-02-16 → 2026-02-28, i.e. before the workflow landed.

Also note `home-assistant/brands` ships an `AI_POLICY.md`: _"We do not allow
autonomous agents to be used for contributing to our projects."_ Don't have an
agent open PRs or write maintainer replies there.

### What to actually do

Create `custom_components/<domain>/brand/`. No `manifest.json` change, no
config. Local images take priority over the CDN.

```text
custom_components/<domain>/brand/
├── icon.png          256×256      (required)
├── icon@2x.png       512×512
├── logo.png          shortest side 128-256
├── logo@2x.png       shortest side 256-512
├── dark_icon.png     256×256      (ONLY if genuinely dark-optimised)
├── dark_icon@2x.png  512×512
├── dark_logo.png
└── dark_logo@2x.png
```

Rules that matter, from the brands README spec:

- **PNG only.** Transparency preferred. Interlaced/progressive preferred.
- **Icons must be exactly 256×256 and 512×512.** 1:1 aspect ratio.
- **Logos:** shortest side 128-256 (normal) and 256-512 (@2x). Landscape
  preferred. Largest permitted shortest side is preferred.
- **Trim the images.** Minimum empty space on the edges, no transparent
  padding, no borders. Padding is the most common real-world defect; it renders
  your logo small and off-centre.
- **Omit `dark_*` unless actually different.** If missing, the non-prefixed
  file is served automatically. Byte-identical `dark_*` copies are dead weight,
  and the brands validator explicitly errors on them: _"dark_icon.png is
  identical to icon.png. Please remove…"_
- **If your logo is square, ship only the icons.** The icon is used as the logo
  fallback. Identical `icon.png`/`logo.png` is also a validator error.
- **Don't use Home Assistant branding**: it implies your integration is official.

Verify your assets locally before committing:

```bash
python - <<'PY'
from PIL import Image
import os, hashlib
D = "custom_components/<domain>/brand"
for f in sorted(os.listdir(D)):
    p = os.path.join(D, f)
    im = Image.open(p); im.load()
    w, h = im.size
    line = f"{f:22s} {w}x{h} {im.mode}"
    if im.mode in ("RGBA", "LA"):
        bbox = im.getchannel("A").getbbox()
        if bbox != (0, 0, w, h):
            line += f"  PADDED L={bbox[0]} T={bbox[1]} R={w-bbox[2]} B={h-bbox[3]}"
    print(line, hashlib.md5(open(p,'rb').read()).hexdigest()[:8])
PY
```

Any `PADDED` line means trim it. Two matching hashes mean delete the redundant file.

### Where the icon will and won't show

| Surface                         | Works?  | Why                                                                        |
| ------------------------------- | ------- | -------------------------------------------------------------------------- |
| Settings → Devices & Services   | **Yes** | Core's `brandsUrl()` → `/api/brands/integration/<domain>/icon.png?token=…` |
| Device page                     | **Yes** | same                                                                       |
| **HACS store / downloads list** | **No**  | HACS supplies a CDN URL over the WebSocket; CDN has no entry for you       |

Evidence for the HACS row, from a real 2026.8.1 capture (worth knowing so you
don't re-debug it):

- Core's `app.js` `brandsUrl` emits **only** local paths:

  ```js
  MR: (e, t) => {
    if (!o) return "";
    t = t ?? location.origin;
    const a = `/api/brands/integration/${e.domain}/${e.darkOptimized ? "dark_" : ""}${e.type}.png`,
      r = new URL(a, t);
    return (r.searchParams.set("token", o), r.toString());
  };
  ```

  (`o` is a WS-fetched access token; the sole `brands.home-assistant.io`
  reference in 559 KB is inside a URL _validator_, not a builder.)

- HACS's own bundle contains the string `brands` **zero** times; the CDN URL
  arrives as WebSocket data and is rendered via Lit's generic attribute setter.
- On the HACS dashboard, **28 of 31** brand requests returned the identical
  3039-byte "icon not available" placeholder for every custom integration not
  already in the CDN. This is the normal appearance now, not a defect in your repo.

Diagnostic shortcut: is a CDN entry present at all?

```bash
# strict URL: 404 = not in the brands CDN
curl -o /dev/null -w '%{http_code} %{size_download}\n' \
  https://brands.home-assistant.io/<domain>/icon.png
# /_/ URL: always 200, serves a placeholder when missing. Do not trust it
curl -o /dev/null -w '%{http_code} %{size_download}\n' \
  https://brands.home-assistant.io/_/<domain>/icon.png
```

A ~3039-byte response from the `/_/` form is the placeholder, not your icon.

### Upstream tracking (don't open a duplicate)

Open issues on `hacs/integration`, all unresolved as of 2026-08-14:

- **#5402** HA 2026.3 local branding works, but HACS still shows "Icon not available" (2026-07-20). Closest match.
- **#5223** HACS downloads panel shows "icon not available" for integrations shipping inline brand icons (2026-04-15)
- **#5179** HACS should use the Brands Proxy API for installed custom integrations (2026-03-20)
- **#5171** HACS dashboard doesn't show local brand icons (2026-03-17)

`hacs/frontend` #936 requested this and was closed as completed 2026-04-15, but
the symptom persists (the backend half appears to be the gap).
Add a 👍 to issue `#5402` rather than filing a fifth report.

## Part 2: Release mechanism

### Repo layout

```text
custom_components/<domain>/          # the integration (HACS ships this dir)
├── manifest.json                    # version lives here; CI rewrites it
├── brand/                           # icons (Part 1)
├── icons.json, strings.json, translations/
└── client/                          # bundled API client, if vendoring
hacs.json                            # HACS metadata
pyproject.toml                       # dev tooling only, NOT runtime deps
.github/workflows/{ci,validate,release}.yml
tests/
```

`hacs.json` stays minimal:

```json
{ "name": "<Friendly Name>", "render_readme": true }
```

There is no file allow/deny list, so `brand/` ships automatically. Don't add
filtering unless you intend to.

### Runtime dependencies: the trap

`manifest.json` keeps `"requirements": []` when the API client is **bundled**
(vendored under `client/`) and its deps (`aiohttp`, `pycognito`) already ship
with HA. Runtime libs are declared in `pyproject.toml` `[project.dependencies]`
**only** so the dev venv and CI can import them. Putting them in
`manifest.json` would make HA pip-install them needlessly at startup.

### Three workflows

**`ci.yml`**: on push to `main` + PRs. Three parallel jobs: `ruff check` +
`ruff format --check`; `mypy` (strict); `pytest`. The pytest job tolerates
exit code 5 (no tests collected) with a warning; useful early, worth removing
once you have tests.

**`validate.yml`**: on push/PR + weekly cron (`0 6 * * 1`). Runs
`home-assistant/actions/hassfest@master` and `hacs/action@main` with
`category: integration`. The cron matters: hassfest/HACS rules change under you,
so a repo that was green can go red without any commit.

**`release.yml`**: `workflow_dispatch` only, input `version` as `X.Y.Z` (no
leading `v`). Requires `permissions: contents: write`. Three jobs:

1. `gate`: validates the version format with `grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'`, then ruff + mypy + pytest.
2. `validate`: hassfest + HACS.
3. `release`: `needs: [gate, validate]`, so it only runs if **both** pass. It:
   - fails if the tag already exists (`git rev-parse "v$VERSION"`),
   - rewrites `manifest.json`'s `version` in place with a small Python step,
   - commits as `github-actions[bot]` with `chore: release vX.Y.Z` and pushes to `main`,
   - `gh release create "vX.Y.Z" --target "$(git rev-parse HEAD)" --title "vX.Y.Z" --generate-notes`.

Key design points to copy:

- **`manifest.json` is the single source of version truth**, bumped by CI, not
  by hand. (`pyproject.toml`'s `version` is unrelated packaging metadata and
  drifts (ours still says `0.1.0`). Harmless, but don't be confused by it.)
- **Quality gate before tag.** A tag is what HACS installs; never create one
  from unvalidated code.
- **The tag must contain the files you expect.** HACS installs a released
  version rather than the tip of `main`, so an asset committed after a tag is
  not in an install of that tag. Verify before announcing:

  ```bash
  git ls-tree -r --name-only vX.Y.Z -- custom_components/<domain>/brand/
  ```

  If a user reports a correct `brand/` folder not working, check which version
  they installed before debugging anything else. (Unverified: the exact HACS
  download mechanics (release zip vs. tag archive vs. default branch) were
  not confirmed while writing this. The ordering advice holds either way; if you
  need the precise behaviour, read the HACS docs rather than trusting this line.)

### Cutting a release

1. Merge everything to `main`; confirm CI is green.
2. Actions → **Release** → _Run workflow_ → enter `0.2.0`.
3. Confirm the bot's `chore: release v0.2.0` commit and the `v0.2.0` release.
4. `git pull` locally (CI pushed a commit to `main`).
5. Verify the tag contains `brand/` (command above).

### Local dev loop

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/mypy custom_components/<domain>
./.venv/bin/pytest -q
```

Tooling config worth copying from `pyproject.toml`: ruff `line-length = 100`,
`target-version = "py312"`, lint set `["E","F","W","I","UP","B","C4","SIM","ASYNC","RUF"]`,
isort `force-sort-within-sections`; mypy `strict` + `warn_unreachable` with
`ignore_missing_imports` for `homeassistant.*` and friends; pytest
`asyncio_mode = "auto"`.

**A local `.venv` HA version is NOT your instance's version.** This repo's venv
pins `homeassistant>=2024.8` and resolved to 2025.1.4 while the real instance
ran 2026.8.1. Don't infer runtime behaviour from the venv; check the actual
instance.

### Fixture capture, if you talk to a cloud API

Pattern in `scripts/`: `capture_emporia.py` dumps **unscrubbed** responses to a
gitignored `tests/library/fixtures/raw/`; `scrub_fixtures.py` replaces
secret-bearing keys (`IdToken`, `AccessToken`, `RefreshToken`, `password`,
`email`, …) and identifiers (`deviceGid`, `serialNumber`, `customerGid`, …)
with stable fakes and writes the sanitized copy to `tests/library/fixtures/`.
**Only scrubbed output is ever committed.** Credentials come from an
env file that must be gitignored.

## Checklist for a new integration

- [ ] `manifest.json`: `domain` matches the folder name; `version` present; `requirements: []` if the client is bundled
- [ ] `hacs.json` with `name` + `render_readme`
- [ ] `brand/` with trimmed `icon.png` (256²) and `icon@2x.png` (512²); logos only if non-square; `dark_*` only if genuinely different
- [ ] Asset check script reports no `PADDED` lines and no duplicate hashes
- [ ] `ci.yml`, `validate.yml` (with cron), `release.yml` (gated, `workflow_dispatch`)
- [ ] Green ruff / mypy / pytest / hassfest / HACS
- [ ] Release cut via workflow; tag verified to contain `brand/`
- [ ] **Do not** open a `home-assistant/brands` PR
- [ ] Expect no icon in the HACS store list; 👍 `hacs/integration` #5402 instead
