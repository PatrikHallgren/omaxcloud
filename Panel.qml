import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
    id: root
    moduleName: "patrikhallgren.omaxcloud"
    manageIpc: false
    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    property var snapshot: ({})
    property var preferences: ({collapsed: [], favorites: []})
    readonly property string collector: decodeURIComponent(Qt.resolvedUrl("collector.py").toString().replace(/^file:\/\//, ""))
    property var pendingPreferences: null
    readonly property var alertCounts: Model.counts(snapshot, preferences)

    function refresh() {
        if (!worker.running) { worker.command = ["python3", collector, "refresh"]; worker.running = true }
    }
    function tick() {
        if (!prefsReader.running && !saveWorker.running && root.pendingPreferences === null) prefsReader.running = true
        if (!worker.running) { worker.command = ["python3", collector, "tick"]; worker.running = true }
    }
    function savePreferences(value) {
        root.preferences = value
        root.pendingPreferences = value
        if (!saveWorker.running) saveWorker.running = true
    }
    function setup() { Quickshell.execDetached(["omarchy-launch-terminal", "python3", collector, "setup"]) }
    function launch(url) { if (String(url).indexOf("https://") === 0) Qt.openUrlExternally(url) }

    Component.onCompleted: { cached.running = true; prefsReader.running = true }
    onOpenedChanged: if (opened) Qt.callLater(function() { dashboard.focusSearch() })

    Process {
        id: cached
        command: ["python3", root.collector, "cached"]
        stdout: StdioCollector { onStreamFinished: { try { root.snapshot = JSON.parse(text) } catch (e) {} root.tick() } }
    }
    Process {
        id: worker
        stdout: StdioCollector {
            onStreamFinished: {
                try { root.snapshot = JSON.parse(text) }
                catch (e) { root.snapshot = Object.assign({}, root.snapshot, {error: "Collector could not run. Check that Python 3.10+ is installed."}) }
            }
        }
    }
    Process {
        id: prefsReader
        command: ["python3", root.collector, "ui-state"]
        stdout: StdioCollector { onStreamFinished: { try { root.preferences = JSON.parse(text) } catch (e) {} } }
    }
    Process {
        id: saveWorker
        command: ["python3", root.collector, "save-ui"]
        stdinEnabled: true
        onStarted: { write(JSON.stringify(root.pendingPreferences) + "\n"); root.pendingPreferences = null }
        onExited: { if (root.pendingPreferences !== null) Qt.callLater(function() { saveWorker.running = true }) }
    }
    // A cheap local due check each minute; network collection is hourly.
    // The collector lock/cache prevents duplicate polling on multiple monitors.
    Timer { interval: 60000; repeat: true; running: true; onTriggered: root.tick() }

    WidgetButton {
        id: button
        bar: root.bar
        text: "☁ " + (root.alertCounts.issues ? root.alertCounts.issues : root.snapshot.error || root.alertCounts.unavailable || !root.snapshot.checked_at ? "?" : "✓")
        active: !!root.alertCounts.issues || !!root.snapshot.error
        tooltipText: "OmaXCloud · " + (root.alertCounts.issues || 0) + " issues reported"
        onPressed: function(mouseButton) { if (mouseButton === Qt.MiddleButton) root.refresh(); else root.toggle() }
    }

    KeyboardPanel {
        id: popup
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: dashboard
        contentWidth: fittedContentWidth(Style.space(660))
        contentHeight: fittedContentHeight(Style.space(720))
        Dashboard {
            id: dashboard
            anchors.fill: parent
            snapshot: root.snapshot
            preferences: root.preferences
            busy: worker.running || !!root.snapshot.refreshing
            foreground: Color.popups.text
            background: Color.popups.background
            accent: Color.accent
            urgent: Color.urgent
            fontFamily: Style.font.family
            uiScale: Style.space(100) / 100
            onRefreshRequested: root.refresh()
            onSetupRequested: root.setup()
            onCloseRequested: root.close()
            onOpenRequested: function(url) { root.launch(url) }
            onPreferencesEdited: function(value) { root.savePreferences(value) }
        }
    }
}
