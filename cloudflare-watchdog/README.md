# ES90 Cloudflare watchdog

This Worker is an external scheduler for `volvo-es90-matrix/es90-matrix`.
It is independent of the user's Windows PC and the GitHub Actions `schedule`
event.

## Schedule and decisions

- Runs every five minutes at `:03`, `:08`, ..., `:58`.
- From 08:00 through 18:59 Asia/Seoul, dispatches the reservation workflow when
  `reservationUpdatedAt` is older than the current required hour. After 19:00,
  it keeps checking the final 18:00 slot until that slot is confirmed.
- After 06:20 Asia/Seoul, dispatches the charger workflow when either
  `chargerCheckedAt` or `tmapCheckedAt` is not today.
- After 07:17 Asia/Seoul, dispatches the Getcha workflow when
  `competitorPriceCheckedAt` is not today.
- Skips a healthy active workflow and applies a 15-minute cooldown after a run
  that started for the same target slot. A queued/in-progress run is treated as
  stuck after 20 minutes for reservations/Getcha or 35 minutes for charger/TMAP;
  the Worker cancels the stuck run and dispatches a replacement.
- Reads both repository and GitHub Pages `version.json`; the repository is the
  authoritative dispatch source and a Pages lag is logged as a warning.
- Verifies GitHub Actions API access on every five-minute check, even when all
  data is already fresh, so a revoked or broken token fails visibly before the
  next required update slot.

## Required Cloudflare secrets

- `GITHUB_WORKFLOW_TOKEN`: fine-grained GitHub token scoped only to
  `volvo-es90-matrix/es90-matrix` with **Actions: read and write**.
- `WATCHDOG_SHARED_SECRET`: random bearer value that protects `POST /run`.

Never commit either value. They must be stored as encrypted Cloudflare Worker
secrets.

## Verification

```text
GET  /health             public health response
POST /run?dry_run=true   protected live decision check without dispatch
POST /run                protected live decision check with dispatch
```

The GitHub Action does the Sales-DMS, charger, TMAP, and Getcha collection. The
Worker only verifies freshness and starts the necessary workflow.
