# API contract and limits

Reviewed against the public `https://app.xcloud.host/api/v1/openapi.json` on 2026-10-03 UTC. Runtime base URL is fixed to `https://app.xcloud.host/api/v1`; credentials are not forwarded through HTTP redirects. All xCloud requests made by this plugin are GETs.

| Endpoint | Use |
| --- | --- |
| `/servers` | Paginated inventory, state, dashboard URL |
| `/servers/{uuid}/sites` | Paginated site inventory, deploy state, dashboard URLs |
| `/servers/{uuid}/monitoring` | CPU, memory, disk, source timestamp |
| `/servers/{uuid}/services` | Required services that are not active |
| `/sites/{uuid}/backups` | All available pages of standard backup history |
| `/sites/{uuid}/docker/backups` | Docker / one-click app backup history |
| `/sites/{uuid}/ssl` | Certificate status and expiry |
| `/sites/{uuid}/wordpress/updates` | WordPress pending updates and scan age |

Lists use `data.items` and `data.pagination`; the alternative `data.data` / `data.meta` described in the reference introduction is also supported. Missing pagination metadata produces an explicit error, not silently truncated results. Backup timestamps are sorted locally across all retrieved pages. `created_at` takes precedence over the legacy timezone-free `date` field; a timezone-free fallback is interpreted as UTC. An unparseable date makes backup status unknown.

Reboot capability is not the same as reboot necessity. The existence of a reboot endpoint is never used to infer that a server needs a reboot. Similarly, PHP version availability is not treated as a general OS update count. The optional SSH probe supplies those two maintenance signals separately.

Alerts history is intentionally not treated as current health: xCloud's schema says recovery records are observations, not current incident state. This release derives current reported issues from inventory, monitoring, services, backup history, SSL, and update summaries.

No live account responses were available during development. Tests exercise published response examples and negative cases; unusual account-specific shapes should show unavailable/error rather than invented healthy values. Site type to backup endpoint selection can be overridden in local settings.
