"""Render the actual Dashboard.qml with synthetic data. Optional PySide6 dependency."""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQml import QQmlApplicationEngine

ROOT = Path(__file__).resolve().parents[1]
now = time.time()


def site(uid, name, issue=False, kind="wordpress"):
    return {"id": uid, "kind": "site", "name": name, "type": kind, "status": "deployed",
            "url": "https://app.xcloud.host/site/" + uid + "/dashboard", "public_url": "https://example.com",
            "backup": {"state": "failed" if issue else "ok", "last_success": now - 7200, "last_attempt": now - 3600, "attempt_status": "failed" if issue else "completed"},
            "ssl": {"expires_ts": now + 55 * 86400}, "http": "Not checked",
            "updates": {"summary": {"total_pending": 3 if issue else 0, "security_pending": 0}},
            "issues": [{"key": "backup", "message": "Latest backup attempt failed", "severity": "warning"}] if issue else [], "unavailable": []}


snapshot = {"checked_at": now - 480, "interval_minutes": 60, "issue_count": 1, "site_count": 5, "unavailable_count": 0,
            "servers": [
                {"id": "demo-us", "kind": "server", "name": "US · Production", "status": "Provisioned", "provider": "Hetzner", "location": "Hillsboro", "url": "https://app.xcloud.host", "metrics": {"cpu_usage": 14, "memory_usage": 48, "disk_usage": 36}, "issues": [], "unavailable": [], "sites": [site("a", "garden-shop.example", True), site("b", "wholesale.example"), site("c", "automation.example", kind="oneclick")]},
                {"id": "demo-eu", "kind": "server", "name": "EU · Services", "status": "Provisioned", "provider": "Hetzner", "location": "Helsinki", "url": "https://app.xcloud.host", "metrics": {"cpu_usage": 8, "memory_usage": 32, "disk_usage": 21}, "issues": [], "unavailable": [], "sites": [site("d", "europe.example"), site("e", "staging.example")]}]}

app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("previewSnapshot", snapshot)
qml = '''import QtQuick
import QtQuick.Window
import "."
Window {
    width: 710; height: 820; visible: true; color: "#171b24"
    Dashboard { id: dashboard; objectName: "dashboard"; anchors.fill: parent; anchors.margins: 24; snapshot: previewSnapshot }
}'''
engine.loadData(qml.encode(), QUrl.fromLocalFile(str(ROOT / "Preview.qml")))
if not engine.rootObjects():
    raise SystemExit("QML failed to load")


def capture():
    window = engine.rootObjects()[0]
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    dash = window.findChild(QObject, "dashboard")
    search = window.findChild(QObject, "search")
    rows = window.findChild(QObject, "resourceList")
    assert rows.property("count") == 7
    search.setProperty("text", "wholesale")
    app.processEvents()
    assert rows.property("count") == 2, "Search must preserve parent server"
    search.setProperty("text", "")
    dash.setProperty("attentionOnly", True)
    app.processEvents()
    assert rows.property("count") == 2, "Attention filter must include only affected site and parent"
    dash.setProperty("attentionOnly", False)
    dash.setProperty("preferences", {"collapsed": ["demo-eu"], "favorites": ["a"]})
    app.processEvents()
    assert rows.property("count") == 5, "Collapsing server hides its sites"
    QTest.qWait(200)
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs/preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    assert window.grabWindow().save(str(output))
    print("QML loaded; search, attention and collapse checks passed; saved", output)
    app.quit()


QTimer.singleShot(700, capture)
app.exec()
