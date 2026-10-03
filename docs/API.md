# API contract and limits

Reviewed against the public `https://app.xcloud.host/api/v1/openapi.json` on 2026-10-03 UTC. Runtime base URL is fixed to `https://app.xcloud.host/api/v1`; credentials are not forwarded through HTTP redirects. All xCloud requests made by this plugin are GETs.

| Endpoint | Use |
| --- | --- |
| `/servers` | Paginated inventory, state, dashboard URL |
| `/servers/{uuid}/sites` | Paginated site inventory, deploy state, dashboard URLs |
| `/servers/{uuid}/monitoring` | CPU, memory, disk, source timestamp |
| `/servers/{uuid}/monitoring/history?range=24h` | Latest dated sample fallback for missing metrics; may require Pro |
| `/servers/{uuid}/services` | Required services that are not active |
| `/sites/{uuid}/backups` | All available pages of standard backup history |
| `/sites/{uuid}/docker/backups` | Docker / one-click app backup history |
| `/sites/{uuid}/ssl` | Certificate status and expiry |
| `/sites/{uuid}/wordpress/updates` | WordPress pending updates and scan age |

Lists use `data.items` and `data.pagination`; the alternative `data.data` / `data.meta` described in the reference introduction is also supported. Missing pagination metadata produces an explicit error, not silently truncated results. Backup timestamps are sorted locally across all retrieved pages. `created_at` takes precedence over the legacy timezone-free `date` field; a timezone-free fallback is interpreted as UTC. An unparseable date makes backup status unknown.

Reboot capability is not the same as reboot necessity. The existence of a reboot endpoint is never used to infer that a server needs a reboot. Similarly, PHP version availability is not treated as a general OS update count. The optional SSH probe supplies those two maintenance signals separately.

Alerts history is intentionally not treated as current health: xCloud's schema says recovery records are observations, not current incident state. This release derives current reported issues from inventory, monitoring, services, backup history, SSL, and update summaries.

No live account responses were available during development. Tests exercise published response examples and negative cases; unusual account-specific shapes should show unavailable/error rather than invented healthy values. Site type to backup endpoint selection can be overridden in local settings.

## v0.2 compatibility and alert preferences

Numeric strings and percentage strings are normalized to finite 0–100 numbers. `ram_usage` from the documented history schema maps to memory. Explicit nested percentage fields are accepted defensively, but bytes and load averages are not treated as percentages. The history fallback uses the latest dated sample and records its source separately; it does not search older samples to make a missing newest value look current. If both sources fail, the affected metric and error are displayed.

This improves coverage but the user's live monitoring response has not been available for verification. `diagnose-metrics` can report field types without raw values to diagnose additional shapes.

Per-site mute rules live in UI preferences and are applied to both UI counts and notification comparisons. Raw collected status is retained. Missing, failed, and overdue backups now use `backup.missing`, `backup.failed`, and `backup.overdue` keys so users can suppress one condition without suppressing the others. The cache schema version forces one fresh collection after upgrading.

## v0.3 website status and compact cards

Website checks now default to enabled, with a one-time migration of the old disabled default. A `website_check_policy: 1` marker preserves subsequent opt-outs. HTTP status is independent of xCloud's deployment state. Requests never contain the xCloud token. Redirect destinations must remain public HTTPS, are checked at each hop and are bounded; restricted responses are not labeled outages. These checks run only with collection while the laptop is on.

The panel hides the SSH placeholder and full-server backup line, and offers `setup-ssh` for locally configured, verified SSH access. This release does not provision SSH keys or change servers.
