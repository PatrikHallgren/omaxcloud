#!/usr/bin/env python3
"""Read-only xCloud collector. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import html
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE = "https://app.xcloud.host/api/v1"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omaxcloud"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "omaxcloud"
DEFAULTS = {"interval_minutes": 60, "backup_max_age_hours": 36,
            "ssl_warning_days": 14, "notifications": True,
            "http_checks": True, "website_check_policy": 1, "team_id": "", "ssh_hosts": {},
            "site_options": {}}


class DataError(Exception):
    """Safe, deliberately credential-free diagnostic."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return fallback


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # xCloud's legacy date field has no zone; prefer created_at at call sites.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def percent(value):
    """Accept numbers and numeric percentage strings, never bytes or load averages."""
    if isinstance(value, str):
        value = value.strip().removesuffix("%").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", value):
            return None
        value = float(value)
    return float(value) if numeric(value) and math.isfinite(value) and 0 <= value <= 100 else None


def normalize_metrics(payload):
    if not isinstance(payload, dict):
        return {}
    # Preserve only explicitly named percentage fields. No arbitrary recursive
    # search: a load average or used-byte count must not become a percentage.
    source = payload
    for wrapper in ("stats", "metrics", "monitoring", "data"):
        if isinstance(source.get(wrapper), dict):
            source = source[wrapper]
            break
    result = {}
    for name, aliases in {"cpu": ("cpu_usage",), "memory": ("memory_usage", "ram_usage"), "disk": ("disk_usage",)}.items():
        candidates = [source.get(key) for key in aliases]
        nested = source.get(name, source.get("ram") if name == "memory" else None)
        if isinstance(nested, dict):
            candidates.extend(nested.get(key) for key in ("usage_percent", "percentage", "percent"))
        value = next((percent(v) for v in candidates if percent(v) is not None), None)
        if value is not None:
            result[name + "_usage"] = value
    result["recorded_at"] = source.get("recorded_at") or source.get("sampled_at")
    return result


def server_metrics(api, prefix, server):
    errors = []
    try:
        metrics = normalize_metrics(api.get(prefix + "/monitoring"))
    except DataError as error:
        metrics = {}
        errors.append(str(error))
    keys = ("cpu_usage", "memory_usage", "disk_usage")
    missing = [key for key in keys if key not in metrics]
    if missing:
        try:
            history = api.get(prefix + "/monitoring/history", {"range": "24h"})
            samples = history.get("samples", []) if isinstance(history, dict) else []
            dated = [r for r in samples if isinstance(r, dict) and timestamp(r.get("sampled_at")) is not None]
            if dated:
                latest = normalize_metrics(max(dated, key=lambda r: timestamp(r["sampled_at"])))
                for key in missing:
                    if key in latest:
                        metrics[key] = latest[key]
                        metrics.setdefault("history_fields", []).append(key.removesuffix("_usage").upper())
                if metrics.get("history_fields"):
                    metrics["history_recorded_at"] = latest.get("recorded_at")
        except DataError as error:
            errors.append("History: " + str(error))
    missing = [key.removesuffix("_usage").upper() for key in keys if key not in metrics]
    if missing:
        server["unavailable"].append("Resource usage: " + ", ".join(missing) + " unavailable" + (" (" + "; ".join(errors) + ")" if errors else " — no usable percentages returned"))
    return metrics


def clean_preferences(data):
    if not isinstance(data, dict):
        raise DataError("Invalid UI state")
    def strings(values):
        return list(dict.fromkeys(v for v in values if isinstance(v, str) and len(v) <= 500))[:500] if isinstance(values, list) else []
    result = {k: strings(data.get(k)) for k in ("collapsed", "favorites", "server_order")}
    for key in ("site_order", "muted_issues"):
        mapping = data.get(key, {})
        result[key] = {k: strings(v) for k, v in mapping.items() if isinstance(k, str) and len(k) <= 500} if isinstance(mapping, dict) else {}
    return result


def load_config():
    saved = read_json(CONFIG / "config.json", {})
    cfg = DEFAULTS | saved
    # v0.3 enables the website checks requested by the user, including existing
    # installations whose setup wrote the old false default. One-time migration;
    # subsequent explicit false settings remain respected.
    migrate = "website_check_policy" not in saved and (CONFIG / "config.json").exists()
    if migrate:
        cfg["http_checks"] = True
    for key, minimum, maximum in [("interval_minutes", 5, 1440),
                                  ("backup_max_age_hours", 1, 8760),
                                  ("ssl_warning_days", 1, 365)]:
        if not numeric(cfg[key]) or not minimum <= cfg[key] <= maximum:
            raise DataError(f"Invalid configuration: {key}")
    if not isinstance(cfg["ssh_hosts"], dict) or not isinstance(cfg["site_options"], dict):
        raise DataError("ssh_hosts and site_options must be objects")
    if migrate:
        atomic_json(CONFIG / "config.json", cfg)
    return cfg


def read_token():
    path = CONFIG / "token"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise DataError("Token permissions must be 600. Run setup again.")
            token = stream.read(8192).strip()
    except OSError:
        raise DataError("Set up your xCloud read-only API token to begin.") from None
    if not token or any(c.isspace() for c in token):
        raise DataError("Invalid token. Run setup again.")
    return token


class API:
    def __init__(self, token, team="", spacing=1.1):
        self.token, self.team, self.spacing = token, team, spacing
        self.next_request = 0.0
        self.opener = build_opener(NoRedirect())
        self.unavailable_until = 0.0

    def get(self, path, params=None):
        if self.unavailable_until > time.time():
            raise DataError("API rate limited; retry after the cooldown")
        time.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + self.spacing
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json",
                   "User-Agent": "OmaXCloud/0.1"}
        if self.team:
            headers["X-Team-Id"] = str(self.team)
        url = BASE + path + ("?" + urlencode(params) if params else "")
        try:
            with self.opener.open(Request(url, headers=headers), timeout=15) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code == 429:
                try:
                    delay = max(60, float(error.headers.get("Retry-After", "60")))
                except ValueError:
                    delay = 300
                self.unavailable_until = time.time() + delay
            messages = {401: "API token rejected; run setup again", 403: "Permission unavailable",
                        404: "Not available for this resource", 422: "Not supported for this resource",
                        429: "API rate limited; retry after the cooldown"}
            raise DataError(messages.get(error.code, f"xCloud API returned HTTP {error.code}")) from None
        except (URLError, OSError, ValueError):
            raise DataError("Could not reach xCloud or read its response") from None
        if not isinstance(payload, dict) or payload.get("success") is not True or "data" not in payload:
            raise DataError("Unexpected xCloud API response")
        return payload["data"]

    def items(self, path):
        result = []
        for page in range(1, 101):
            data = self.get(path, {"page": page, "per_page": 100})
            if not isinstance(data, dict):
                raise DataError("Unexpected list response")
            rows = data.get("items", data.get("data"))
            meta = data.get("pagination", data.get("meta", {}))
            if not isinstance(rows, list) or not isinstance(meta, dict):
                raise DataError("Unexpected pagination response")
            result.extend(rows)
            last = meta.get("last_page")
            if not numeric(last):
                raise DataError("Pagination metadata is missing")
            if page >= last:
                return result
        raise DataError("Pagination exceeded the safety limit")


def safe_url(value, management=False):
    try:
        parsed = urlsplit(str(value or ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        if management and parsed.hostname != "app.xcloud.host":
            return ""
        return str(value)
    except ValueError:
        return ""


def resource(row, kind):
    uid = str(row.get("uuid", ""))
    url = safe_url(row.get("dashboard_url"), True)
    if not url:
        url = f"https://app.xcloud.host/{kind}/{quote(uid, safe='')}/dashboard"
    return {"id": uid, "name": str(row.get("name") or row.get("domain_name") or uid),
            "kind": kind, "url": url, "issues": [], "unavailable": []}


def issue(obj, key, message, severity="warning"):
    obj["issues"].append({"key": key, "message": message, "severity": severity})


def fetch_optional(api, path, obj, label, listing=False):
    try:
        return api.items(path) if listing else api.get(path)
    except DataError as error:
        obj["unavailable"].append(f"{label}: {error}")
        return None


def backup_summary(rows, now, limit):
    if rows is None:
        return {"state": "unavailable", "last_success": None, "last_attempt": None}
    dated = [(timestamp(r.get("created_at") or r.get("date")), r) for r in rows]
    dated = sorted([(t, r) for t, r in dated if t is not None], key=lambda pair: pair[0], reverse=True)
    success = next((t for t, r in dated if r.get("status") == "completed"), None)
    latest = dated[0] if dated else None
    state = "ok"
    if rows and len(dated) != len(rows):
        state = "unknown"
    elif not rows:
        state = "missing"
    elif latest and latest[1].get("status") == "failed":
        state = "failed"
    elif success is None:
        state = "missing"
    elif now - success > limit * 3600:
        state = "overdue"
    return {"state": state, "last_success": success,
            "last_attempt": latest[0] if latest else None,
            "attempt_status": str(latest[1].get("status", "unknown")) if latest else "none"}


# Opt-in only. No sudo, installation, apt refresh, reboot or remote mutation.
SSH_PROBE = """import json,os
r={'reboot_required': os.path.exists('/var/run/reboot-required'), 'updates': None, 'apt_checked_at': None}
try:
 import apt
 c=apt.Cache(); r['updates']=sum(1 for p in c if p.is_upgradable)
except Exception: pass
try: r['apt_checked_at']=os.path.getmtime('/var/lib/apt/periodic/update-success-stamp')
except OSError: pass
print(json.dumps(r))
"""


def ssh_health(host):
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
        raise DataError("Invalid SSH host or SSH config alias")
    try:
        completed = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=8", host, "python3 -c " + shlex.quote(SSH_PROBE)],
            capture_output=True, text=True, timeout=15, check=True)
        data = json.loads(completed.stdout)
        if not isinstance(data.get("reboot_required"), bool):
            raise ValueError()
        return data
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        raise DataError("SSH check unavailable; verify the host, key and Python installation") from None


def http_check(url):
    """Public HTTPS GET, bounded redirects; no credentials, cookies or body reads."""
    deadline = time.monotonic() + 20
    visited = set()
    try:
        for _ in range(6):
            if not safe_url(url):
                return "Unverified redirect (HTTPS required)"
            if url in visited:
                return "Redirect loop"
            visited.add(url)
            host = urlsplit(url).hostname
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return "Skipped (private address)"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "Timed out"
            try:
                with build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": "OmaXCloud/0.3"}), timeout=min(10, remaining)) as response:
                    return f"Online ({response.status})"
            except HTTPError as error:
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    if not location:
                        return f"Unverified redirect ({error.code})"
                    url = urljoin(url, location)
                elif error.code in {401, 403}:
                    return f"Access restricted ({error.code})"
                else:
                    return f"HTTP error {error.code}"
        return "Too many redirects"
    except (OSError, URLError, ValueError):
        return "Unreachable from laptop"


def collect(api, cfg, now):
    servers = []
    for raw in api.items("/servers"):
        server = resource(raw, "server")
        prefix = "/servers/" + quote(server["id"], safe="")
        server.update(status=str(raw.get("status_readable") or raw.get("status") or "Unknown"),
                      provider=str(raw.get("provider") or ""), location=str(raw.get("location") or ""),
                      sites=[], maintenance=None, server_backup="Not exposed by the public API")
        state = str(raw.get("status", ""))
        if state in {"disconnected", "error", "suspended", "low_storage"} or state.endswith("failed"):
            issue(server, "status", server["status"], "critical")
        elif state not in {"provisioned", "modified", "created"}:
            server["unavailable"].append("Server is not in a ready state: " + (state or "unknown"))
        server["metrics"] = server_metrics(api, prefix, server)
        for name, threshold in [("cpu", 90), ("memory", 90), ("disk", 85)]:
            value = server["metrics"].get(name + "_usage")
            if numeric(value) and value >= threshold:
                issue(server, name, f"{name.upper()} usage {value:.0f}%", "critical" if name == "disk" and value >= 95 else "warning")
        services = fetch_optional(api, prefix + "/services", server, "Services")
        for service in (services or {}).get("items", []):
            if service.get("is_required") and service.get("status") not in {"active", "running"}:
                issue(server, "service:" + str(service.get("name")), str(service.get("label") or service.get("name")) + " is not active", "critical")
        if server["id"] in cfg["ssh_hosts"]:
            try:
                server["maintenance"] = ssh_health(cfg["ssh_hosts"][server["id"]])
                maintenance = server["maintenance"]
                if maintenance["reboot_required"]:
                    issue(server, "reboot", "Server reboot required")
                if numeric(maintenance.get("updates")) and maintenance["updates"] > 0:
                    issue(server, "os-updates", f"{maintenance['updates']} OS updates in local package cache")
            except DataError as error:
                server["unavailable"].append(str(error))
        sites = fetch_optional(api, prefix + "/sites", server, "Site inventory", True)
        for raw_site in sites or []:
            site = resource(raw_site, "site")
            site["server_id"] = server["id"]
            site["type"] = str(raw_site.get("type") or "unknown")
            site["status"] = str(raw_site.get("deploy_state") or "unknown")
            site["public_url"] = safe_url("https://" + str(raw_site.get("domain_name") or ""))
            p = "/sites/" + quote(site["id"], safe="")
            options = cfg["site_options"].get(site["id"], {})
            if site["status"] in {"failed", "cancelled"} or raw_site.get("status") in {"suspended", "disconnected"}:
                issue(site, "deployment", "Deployment / site status: " + str(raw_site.get("status") or site["status"]), "critical")
            elif site["status"] != "deployed":
                site["unavailable"].append("Deployment: " + site["status"])
            mode = options.get("backup_mode", "docker" if site["type"] == "oneclick" else "standard")
            if mode == "off" or (raw_site.get("is_backup_supported") is False and mode != "docker"):
                site["backup"] = {"state": "not_supported", "last_success": None, "last_attempt": None}
            else:
                suffix = "/docker/backups" if mode == "docker" else "/backups"
                backups = fetch_optional(api, p + suffix, site, "Backups", True)
                site["backup"] = backup_summary(backups, now, options.get("backup_max_age_hours", cfg["backup_max_age_hours"]))
                if site["backup"]["state"] in {"missing", "failed", "overdue"}:
                    issue(site, "backup." + site["backup"]["state"], {"missing": "No successful site backup", "failed": "Latest backup attempt failed", "overdue": "Site backup overdue"}[site["backup"]["state"]])
                elif site["backup"]["state"] == "unknown":
                    site["unavailable"].append("Backup dates could not be interpreted")
            ssl = fetch_optional(api, p + "/ssl", site, "SSL")
            site["ssl"] = ssl if isinstance(ssl, dict) else {}
            expiry = timestamp(site["ssl"].get("expires_at"))
            site["ssl"]["expires_ts"] = expiry
            if expiry is not None and expiry - now <= cfg["ssl_warning_days"] * 86400:
                issue(site, "ssl", "SSL expired" if expiry < now else "SSL expires soon", "critical" if expiry < now else "warning")
            elif not site["ssl"].get("status"):
                site["unavailable"].append("SSL certificate unavailable")
            elif site["ssl"].get("status") != "installed":
                issue(site, "ssl-status", "SSL: " + str(site["ssl"].get("status")))
            site["updates"] = None
            if site["type"] == "wordpress":
                updates = fetch_optional(api, p + "/wordpress/updates", site, "WordPress updates")
                site["updates"] = updates if isinstance(updates, dict) else None
                summary = (site["updates"] or {}).get("summary", {})
                if numeric(summary.get("total_pending")) and summary["total_pending"] > 0:
                    security = summary.get("security_pending", 0)
                    issue(site, "wp-updates", f"{summary['total_pending']} WordPress updates ({security} security)", "critical" if numeric(security) and security > 0 else "warning")
                scan = timestamp(summary.get("last_scanned_at"))
                if scan is None or now - scan > 86400:
                    site["unavailable"].append("WordPress update scan is missing or older than 24 hours")
            site["http"] = "Checks disabled"
            if cfg["http_checks"]:
                site["http"] = http_check(site["public_url"])
                site["http_checked_at"] = time.time()
                if not site["http"].startswith("Online"):
                    issue(site, "http", "Website check: " + site["http"])
            server["sites"].append(site)
        servers.append(server)
    objects = [obj for s in servers for obj in [s] + s["sites"]]
    return {"snapshot_version": 3, "servers": servers, "checked_at": now, "last_attempt": now,
            "next_check": now + cfg["interval_minutes"] * 60,
            "interval_minutes": cfg["interval_minutes"], "error": "", "configured": True,
            "issue_count": sum(len(o["issues"]) for o in objects),
            "unavailable_count": sum(len(o["unavailable"]) for o in objects),
            "site_count": sum(len(s["sites"]) for s in servers)}


def notification_changes(previous, current, preferences=None):
    preferences = preferences if preferences is not None else read_json(CONFIG / "ui-state.json", {})
    muted = clean_preferences(preferences)["muted_issues"]
    def keys(snapshot):
        return {o["id"] + ":" + i["key"] for s in snapshot.get("servers", [])
                for o in [s] + s.get("sites", []) for i in o.get("issues", []) if i["key"] not in muted.get(o["id"], [])}
    old, new = keys(previous), keys(current)
    added = len(new - old)
    # Missing/partial data cannot prove recovery.
    def inventory(snapshot):
        return {o["id"] for s in snapshot.get("servers", []) for o in [s] + s.get("sites", [])}
    same_inventory = inventory(previous) == inventory(current)
    resolved = len(old - new) if not current.get("unavailable_count") and same_inventory else 0
    if not added and not resolved:
        return
    body = f"{added} new issue(s), {resolved} resolved. Open OmaXCloud for details."
    try:
        subprocess.run(["notify-send", "--app-name=OmaXCloud", "--", "xCloud status changed", html.escape(body)], timeout=3, check=False, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pass


def tick(force=False):
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(CACHE / "refresh.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return read_json(CACHE / "snapshot.json", {}) | {"refreshing": True}
        previous = read_json(CACHE / "snapshot.json", {})
        now = time.time()
        if now < previous.get("retry_after", 0) or (not force and previous.get("snapshot_version") == 3 and now < previous.get("next_check", 0)):
            return previous
        try:
            cfg = load_config()
            api = API(read_token(), cfg["team_id"])
            current = collect(api, cfg, now)
            if api.unavailable_until > now:
                current["retry_after"] = api.unavailable_until
                current["next_check"] = max(now + 300, api.unavailable_until)
            if cfg["notifications"]:
                notification_changes(previous, current)
        except (DataError, TypeError, KeyError, ValueError, AttributeError) as error:
            # Keep the previous successful snapshot, including its old timestamp.
            message = str(error) if isinstance(error, DataError) else "Unexpected data or configuration; see README troubleshooting"
            current = previous | {"snapshot_version": 3, "error": message, "last_attempt": now, "next_check": now + 300,
                                  "configured": (CONFIG / "token").exists()}
        atomic_json(CACHE / "snapshot.json", current)
        return current


def setup():
    print("OmaXCloud setup\nCreate an xCloud API token with read:servers and read:sites only.\nThe token is stored locally with permissions 600; never sent to GitHub.\n")
    token = getpass.getpass("API token (hidden): ").strip()
    if not token or any(c.isspace() for c in token):
        raise DataError("Token is empty or contains whitespace")
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG, 0o700)
    fd, name = tempfile.mkstemp(dir=CONFIG, prefix=".token-")
    with os.fdopen(fd, "w") as stream:
        stream.write(token)
    os.replace(name, CONFIG / "token")
    if not (CONFIG / "config.json").exists():
        atomic_json(CONFIG / "config.json", DEFAULTS)
    print("Checking xCloud…")
    result = tick(True)
    print(result.get("error") or f"Connected: {len(result.get('servers', []))} servers, {result.get('site_count', 0)} sites.")
    input("Press Enter to close. ")


def diagnose_metrics():
    """Shareable shape diagnostics: no tokens, server names, UUIDs or raw values."""
    cfg = load_config()
    api = API(read_token(), cfg["team_id"])
    def shape(value, depth=0):
        if depth > 4:
            return type(value).__name__
        if isinstance(value, dict):
            return {str(k): shape(v, depth + 1) for k, v in list(value.items())[:40]}
        if isinstance(value, list):
            return [shape(value[0], depth + 1)] if value else []
        return type(value).__name__
    result = []
    for index, row in enumerate(api.items("/servers"), 1):
        entry = {"server_number": index}
        path = "/servers/" + quote(str(row["uuid"]), safe="")
        for label, suffix in [("latest", "/monitoring"), ("history", "/monitoring/history")]:
            try:
                data = api.get(path + suffix, {"range": "24h"} if label == "history" else None)
                entry[label] = shape(data)
            except DataError as error:
                entry[label] = str(error)
        result.append(entry)
    return result


def setup_ssh():
    cfg = load_config()
    api = API(read_token(), cfg["team_id"])
    print("SSH maintenance checks\nUse an SSH alias or user@host that already works with a key.\nNo passwords, sudo, or server changes are used.\n")
    for server in api.items("/servers"):
        uid = str(server["uuid"])
        current = cfg["ssh_hosts"].get(uid, "not configured")
        print(f"\n{server.get('name', uid)} — {current}")
        host = input("SSH alias/user@host (Enter keeps current; - disables): ").strip()
        if not host:
            continue
        if host == "-":
            cfg["ssh_hosts"].pop(uid, None)
        else:
            try:
                result = ssh_health(host)
            except DataError as error:
                print(f"Not saved: {error}")
                continue
            cfg["ssh_hosts"][uid] = host
            print("Connected. Reboot needed: " + str(result["reboot_required"]) + "; pending updates: " + str(result.get("updates")))
        atomic_json(CONFIG / "config.json", cfg)
    print("Saved. Click Refresh in OmaXCloud to show the results.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["tick", "refresh", "cached", "setup", "setup-ssh", "ui-state", "save-ui", "diagnose-metrics"])
    args = parser.parse_args()
    if args.command == "setup":
        setup()
    elif args.command == "setup-ssh":
        setup_ssh()
    elif args.command == "diagnose-metrics":
        print(json.dumps(diagnose_metrics(), indent=2))
    elif args.command == "save-ui":
        data = json.loads(sys.stdin.readline(16384))
        if not isinstance(data, dict):
            raise DataError("Invalid UI state")
        atomic_json(CONFIG / "ui-state.json", clean_preferences(data))
    else:
        value = (read_json(CONFIG / "ui-state.json", {}) if args.command == "ui-state" else
                 read_json(CACHE / "snapshot.json", {}) if args.command == "cached" else tick(args.command == "refresh"))
        print(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (DataError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, DataError) else "Could not read or write local settings"}))
        sys.exit(1)
