# NAS smoke test — Emporia EV Charger (manual acceptance)

Final acceptance gate before tagging a release. Run against the **real** NAS
Home Assistant instance and **real** Emporia credentials. Not automated by
design (CI never uses live cloud creds). Record PASS/FAIL + HA/firmware
versions at the bottom.

> Reflects the as-built integration (post live-API-capture reconciliation):
> the status endpoint drives charge state/rate/status; energy comes from a
> separate per-minute usage call; **Power (W)** is the primary energy signal
> and **Energy (last minute)** is a per-minute kWh reading (NOT a direct
> Energy-dashboard source — a Riemann helper on Power is the dashboard path).

## Pre-flight

- [ ] CI is green on the commit under test (lint, mypy, pytest, hassfest, HACS).
- [ ] You have the Emporia app email/password and can log into the mobile app.
- [ ] You can reach the NAS HA `config/` directory (Samba/SSH/File editor).
- [ ] Note the current HA version (must be **2024.8+**) and charger firmware.

## 1. Deploy onto the NAS

- [ ] Copy `custom_components/emporia_ev/` (the whole dir, INCLUDING the
      `client/` sub-package) into `config/custom_components/emporia_ev/` on the
      NAS. Do NOT copy the repo root, `tests/`, `.github/`, `scripts/`, or
      `.superpowers/`.
- [ ] Confirm on the NAS: `config/custom_components/emporia_ev/manifest.json`
      and `config/custom_components/emporia_ev/client/__init__.py` exist.
- [ ] **Restart Home Assistant.** Wait for a full restart.
- [ ] **Settings → System → Logs**: no `emporia_ev` import/setup errors.

## 2. Add via the UI

- [ ] **Settings → Devices & Services → Add Integration** → "Emporia EV
      Charger" appears.
- [ ] Enter real email + password → flow **succeeds**, creates an entry.
- [ ] (Optional) Remove + retry with a wrong password → "Invalid email or
      password" shows; re-add correctly.

## 3. Verify devices and entities

- [ ] **Every** charger on the account appears as its own device (count matches
      the Emporia app).
- [ ] Each charger device shows real, plausible values (not unknown/unavailable):
  - [ ] **Charging** switch — reflects charging enabled/disabled.
  - [ ] **Charge rate** number — value within min/max (6–48 A fallback).
  - [ ] **Power** sensor (W) — 0 when idle; rises when a car is actively charging.
  - [ ] **Energy (last minute)** sensor (kWh) — small per-minute value; 0 when idle.
  - [ ] **Status** sensor — Charging / Plugged in (idle) / Not plugged in / Error.
  - [ ] **Plugged in** binary sensor — reflects whether a car is connected.
  - [ ] **Vehicle battery** sensor — present ONLY if a vehicle is linked in the
        Emporia app (may be absent — that's expected if no vehicle/car connected).
- [ ] Entity names compose as "<Charger name> <Entity>".

## 4. Command round-trip (the key control test)

- [ ] Toggle the **Charging** switch in HA:
  - [ ] UI flips **immediately** (optimistic).
  - [ ] Stays consistent within a poll cycle or two (no flicker back-and-forth).
  - [ ] Change is **reflected in the Emporia mobile app** (real cloud round-trip).
- [ ] Toggle back — same clean round-trip.
- [ ] Nudge **Charge rate** by 1–2 A → holds and (where the charger honours it)
      shows in the Emporia app.

## 5. Power / Energy while charging (if a car is available)

- [ ] Plug in and start a charge. Within a poll cycle, **Power (W)** shows a
      real charging figure and **Status** shows Charging.
- [ ] (Optional) Create a **Riemann-sum integral helper** on the Power sensor
      (per the README) and add THAT to the Energy dashboard; confirm it
      accumulates kWh over the session. (The raw Energy (last minute) sensor is
      NOT added directly — it's per-minute, not a lifetime total.)
- [ ] Verify the charging-state adaptive polling feels responsive (~15 s while
      charging) vs. idle (~30 s).

## 6. Reload / restart resilience

- [ ] **Reload** the config entry (⋮ → Reload) → entities return, no duplicate
      devices, no leaked-session errors in the log.
- [ ] **Restart HA** → the entry loads **without** re-prompting for credentials
      (refresh token persisted); same entity ids (stable unique_ids — history
      preserved).
- [ ] (Optional) **Download diagnostics** (device ⋮ → Download diagnostics) →
      confirm secrets (password, tokens, serial, name) are redacted before
      attaching to any issue.

## Result

- HA version: **\_\_\_\_** Charger firmware: **\_\_\_\_** Date: **\_\_\_\_**
- Chargers discovered: \_\_\_\_ (matches app? Y / N)
- Charging switch round-trip (HA ↔ app): PASS / FAIL
- Power sensor showed real charging figure: PASS / FAIL / N/A (no car)
- Reload + restart clean, no re-auth prompt: PASS / FAIL
- **Overall: PASS / FAIL**
- Notes / follow-ups:
