# Emporia API — pinned facts (from live capture 2026-07-20)

Captured against a real account with one charger (model **VVDN01**). Raw
responses are gitignored (`tests/library/fixtures/raw/`); scrubbed fixtures are
committed under `tests/library/fixtures/`.

## Auth (Cognito)

- Region: `us-east-2`
- User Pool ID: `us-east-2_ghlOXVLi1`
- App Client ID: `4qte47jbstod8apnfic0bunmrq`
- Token endpoint: `https://cognito-idp.us-east-2.amazonaws.com/`
- REST auth header: `authtoken: <IdToken>` (the **id** token, not access token).

## Base URL

`https://api.emporiaenergy.com`

## Endpoints and real shapes

### `GET customers/devices` (fixture: `devices.json`)

Top-level object:

- `customerGid` (int) — **the account id** used as the config-entry `unique_id`.
- `email`, `firstName`, `lastName`, `createdAt`.
- `devices`: list. Each device:
  - `deviceGid` (int), `manufacturerDeviceId` (str, = serial), `model` (str,
    e.g. `VVDN01`), `firmware` (str), `locationProperties` (dict, has
    `deviceName`), `channels` (list; charger's is `channelNum` `"1,2,3"`),
    and `evCharger` (dict) when the device is a charger — see fields below.

So `Charger.from_device` reads the **devices** payload: `device["deviceGid"]`,
`device["manufacturerDeviceId"]`, `device["model"]`,
`device["locationProperties"]["deviceName"]`, and `device["evCharger"]`.

### `GET customers/devices/status` (fixture: `device_status.json`)

Top-level object with **separate typed lists** (NOT a `devices` list):

- `outlets`, `batteries`, `loads`, **`evChargers`**, `devicesConnected`.
- `evChargers`: list; each entry is the charger status **flat** (not nested):
  - `deviceGid` (int), `loadGid` (int)
  - `chargerOn` (bool), `chargingRate` (int A), `maxChargingRate` (int A)
  - `status` (str, e.g. `"Standby"`), `message` (str, e.g. `"Ready"`)
  - `icon` (str, e.g. `"CarNotConnected"`), `iconLabel` (str), `iconDetailText`
  - `faultText` (str|null), `debugCode`, `breakerPIN`, `proControlCode`
  - `loadManagementEnabled` (bool), `hideChargeRateSliderText` (str|null)
- **No `minChargingRate`** → use the 6 A fallback.
- **No power/energy fields here.**
- **No vehicle block observed** (no car connected at capture time;
  `icon == "CarNotConnected"`). Vehicle-battery support is best-effort / TBD —
  capture again with a car plugged in to pin the field, or omit for v1.

Observed status vocabulary (car not connected): `status="Standby"`,
`message="Ready"`, `icon="CarNotConnected"`, `iconLabel="Ready"`,
`chargerOn=True`, `chargingRate=40`, `maxChargingRate=40`. The `icon` field is
the most reliable state discriminator: `CarNotConnected` ⇒ not plugged in.
(Charging / plugged-idle icons must be pinned from a session with a car — until
then, derive from `chargerOn` + a non-`CarNotConnected` icon.)

### `GET AppAPI?apiMethod=getDeviceListUsages&deviceGids={gid}&instant={iso}&scale={scale}&energyUnit={unit}`

(fixtures: `usage_kwh.json` scale=1H, `usage_1min.json` scale=1MIN)

- Valid `energyUnit`: `[KilowattHours, Dollars, AmpHours, Trees, GallonsOfGas,
MilesDriven, Carbon, Voltage]` — **`WATTS` is NOT valid** (returns HTTP 400,
  fixture `usage_watts.json` holds that error body).
- Response: `deviceListUsages.devices[].channelUsages[]` each with `name`,
  `channelNum`, `percentage`, `usage` (float, **energy in the requested unit
  over the `scale` window** — kWh here), `nestedDevices`. Match the charger's
  channel by `channelNum == "1,2,3"` (or take the `"Main"` channel).

So energy is **per-time-bucket kWh**, not a lifetime counter and not watts.

## Design consequences (v1)

- **Account id** = `customerGid` from `customers/devices`.
- **Status parse** comes from `evChargers[]` on the status payload (flat fields).
- **Charge state / rate / max** available directly.
- **Energy**: coordinator calls `getDeviceListUsages` at `scale=1MIN`,
  `energyUnit=KilowattHours`; Energy sensor = that bucket usage
  (`state_class=measurement`, NOT `total_increasing`). Document `utility_meter`
  / Riemann helper for a dashboard lifetime total.
- **Power (W)**: derived = `kWh_1min * 60 * 1000` (energy per minute → average W
  over that minute); `state_class=measurement`.
- **Vehicle battery**: field not observed with no car connected — treat as
  best-effort / defer until re-captured with a vehicle plugged in.

## Local env note (dev only)

The throwaway capture scripts force `aiohttp.ThreadedResolver()` — the venv's
`aiodns 3.2.0` / `pycares 5.0.1` pair has an incompatible
`Channel.getaddrinfo()` signature. The HA integration is unaffected (it uses
HA's own client session).
