# Brand assets for the Emporia EV Charger integration

Home Assistant does NOT serve integration logos from this repo — it fetches
them from the central [`home-assistant/brands`](https://github.com/home-assistant/brands)
repository, keyed by the integration `domain` (`emporia_ev`). These files are
staged here so they're version-controlled alongside the integration; they must
be submitted to `home-assistant/brands` to actually appear in HA.

## Files (spec: PNG, transparent background, trimmed, square icon)
- `custom_integrations/emporia_ev/icon.png`     — 256×256 (square app icon)
- `custom_integrations/emporia_ev/icon@2x.png`  — 512×512 (@2x)

(A separate horizontal `logo.png`/`logo@2x.png`, max 256px tall, is optional —
not provided yet.)

## Submitting to home-assistant/brands
1. Fork https://github.com/home-assistant/brands
2. Copy this repo's `brands/custom_integrations/emporia_ev/` into the fork's
   `custom_integrations/emporia_ev/` (same relative path).
3. Run the brands repo's checks locally if possible (it has an image validator
   that enforces size/transparency/trim).
4. Open a PR. Once merged, HA shows the icon on the Integrations page for every
   user of this integration.

Note: only submit imagery you have the right to publish; a third-party brand's
trademarked logo requires their asset/permission.
