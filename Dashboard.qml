import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

FocusScope {
    id: root
    property var snapshot: ({})
    property var preferences: ({collapsed: [], favorites: []})
    property bool busy: false
    property color foreground: "#d8dee9"
    property color background: "#171b24"
    property color accent: "#8abeb7"
    property color urgent: "#e88989"
    property string fontFamily: "sans-serif"
    property real uiScale: 1
    property double now: Date.now() / 1000
    property bool attentionOnly: false
    readonly property color muted: Qt.alpha(foreground, 0.62)
    readonly property color line: Qt.alpha(foreground, 0.12)
    readonly property bool stale: !!snapshot.checked_at && now - snapshot.checked_at > (snapshot.interval_minutes || 60) * 60 + 300
    readonly property var rows: makeRows(snapshot, search.text, attentionOnly, preferences)
    signal refreshRequested()
    signal setupRequested()
    signal closeRequested()
    signal openRequested(string url)
    signal preferencesEdited(var value)

    function focusSearch() { search.forceActiveFocus() }
    function age(ts) {
        if (!ts) return "Unavailable"
        var minutes = Math.max(0, Math.floor((now - ts) / 60))
        return minutes < 1 ? "just now" : minutes < 60 ? minutes + "m ago" : minutes < 1440 ? Math.floor(minutes / 60) + "h ago" : Math.floor(minutes / 1440) + "d ago"
    }
    function dateText(ts) { return ts ? new Date(ts * 1000).toLocaleString() : "Unavailable" }
    function hasIssues(o) { return (o.issues || []).length > 0 || (o.unavailable || []).length > 0 }
    function favorite(id) { return (preferences.favorites || []).indexOf(id) >= 0 }
    function collapsed(id) { return (preferences.collapsed || []).indexOf(id) >= 0 }
    function togglePreference(key, id) {
        var p = {collapsed: (preferences.collapsed || []).slice(), favorites: (preferences.favorites || []).slice()}
        var index = p[key].indexOf(id)
        if (index < 0) p[key].push(id); else p[key].splice(index, 1)
        preferences = p
        preferencesEdited(p)
    }
    function makeRows(data, query, attention, prefs) {
        var servers = (data.servers || []).slice()
        var favorites = prefs.favorites || []
        var closed = prefs.collapsed || []
        function sort(a, b) {
            return Number(favorites.indexOf(b.id) >= 0) - Number(favorites.indexOf(a.id) >= 0) || a.name.localeCompare(b.name)
        }
        servers.sort(sort)
        var q = query.trim().toLowerCase(), result = []
        servers.forEach(function(s) {
            var serverMatch = s.name.toLowerCase().indexOf(q) >= 0
            var sites = (s.sites || []).filter(function(site) {
                return (!q || serverMatch || site.name.toLowerCase().indexOf(q) >= 0) && (!attention || root.hasIssues(site))
            }).sort(sort)
            if (sites.length || ((!q || serverMatch) && (!attention || root.hasIssues(s)))) {
                result.push(s)
                if (q || attention || closed.indexOf(s.id) < 0) sites.forEach(function(site) { result.push(site) })
            }
        })
        return result
    }
    function backupText(o) {
        var b = o.backup || {}
        if (b.state === "not_supported") return "Not supported / disabled"
        if (b.state === "unavailable") return "Unavailable"
        if (!b.last_success) return "No successful backup found"
        return age(b.last_success)
    }
    function updatesText(o) {
        if (o.type !== "wordpress") return "App updates: not exposed"
        var u = o.updates || {}, s = u.summary || {}
        if (typeof s.total_pending !== "number") return "Updates unavailable"
        return s.total_pending === 0 ? "No updates reported" : s.total_pending + " updates · " + (s.security_pending || 0) + " security"
    }
    function openSelected() { if (rows[list.currentIndex]) openRequested(rows[list.currentIndex].url) }

    Timer { interval: 60000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }
    Keys.onEscapePressed: root.closeRequested()
    Shortcut { sequence: "Ctrl+R"; enabled: root.visible; onActivated: root.refreshRequested() }
    Shortcut { sequence: "Ctrl+F"; enabled: root.visible; onActivated: root.focusSearch() }

    component Label: Text {
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: 13 * root.uiScale
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }
    component Action: Button {
        id: action
        hoverEnabled: true
        padding: 9 * root.uiScale
        font.family: root.fontFamily
        font.pixelSize: 12 * root.uiScale
        contentItem: Label {
            text: action.text
            color: action.enabled ? (action.checked ? root.accent : root.foreground) : root.muted
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6 * root.uiScale
            color: action.checked || action.hovered || action.activeFocus ? Qt.alpha(root.accent, 0.13) : "transparent"
            border.color: action.activeFocus ? root.accent : root.line
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 14 * root.uiScale
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                spacing: 3 * root.uiScale
                Label { text: "OMAXCLOUD"; color: root.accent; font.pixelSize: 11 * root.uiScale; font.letterSpacing: 2 * root.uiScale; font.bold: true }
                Label { text: "Your hosting, at a glance"; font.pixelSize: 23 * root.uiScale; font.bold: true }
            }
            Item { Layout.fillWidth: true }
            Action { text: "Setup"; onClicked: root.setupRequested(); Accessible.name: "Configure xCloud API token" }
            Action { text: root.busy ? "Checking…" : "Refresh"; enabled: !root.busy; onClicked: root.refreshRequested() }
        }
        RowLayout {
            spacing: 20 * root.uiScale
            Label { text: (root.snapshot.servers || []).length + " servers"; font.pixelSize: 16 * root.uiScale; font.bold: true }
            Label { text: (root.snapshot.site_count || 0) + " sites"; font.pixelSize: 16 * root.uiScale; color: root.muted }
            Item { Layout.fillWidth: true }
            Label {
                text: root.snapshot.issue_count ? root.snapshot.issue_count + (root.snapshot.issue_count === 1 ? " issue" : " issues") : "No issues reported"
                color: root.snapshot.issue_count ? root.urgent : root.accent
                visible: !!root.snapshot.checked_at
            }
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: banner.implicitHeight + 22 * root.uiScale
            radius: 8 * root.uiScale
            color: Qt.alpha(root.snapshot.error || root.stale ? root.urgent : root.accent, 0.09)
            Label {
                id: banner
                anchors.fill: parent; anchors.margins: 11 * root.uiScale
                wrapMode: Text.Wrap
                elide: Text.ElideNone
                color: root.snapshot.error || root.stale ? root.urgent : root.muted
                text: root.snapshot.error || (!root.snapshot.checked_at ? "Welcome. Connect a read-only xCloud API token to see your servers and sites." :
                    (root.stale ? "Cached data · " : "Checked ") + root.age(root.snapshot.checked_at) + " · Every " + (root.snapshot.interval_minutes || 60) + " minutes while your laptop is on")
            }
        }
        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: search
                objectName: "search"
                Layout.fillWidth: true
                placeholderText: "Search servers or sites…"
                color: root.foreground
                placeholderTextColor: root.muted
                selectionColor: root.accent
                selectedTextColor: root.background
                font.family: root.fontFamily
                font.pixelSize: 13 * root.uiScale
                padding: 11 * root.uiScale
                background: Rectangle { color: Qt.alpha(root.foreground, 0.035); radius: 6 * root.uiScale; border.color: search.activeFocus ? root.accent : root.line }
                Keys.onDownPressed: { list.forceActiveFocus(); list.currentIndex = 0 }
                Keys.onEscapePressed: { if (text) text = ""; else root.closeRequested() }
            }
            Action { text: "Attention needed"; checkable: true; checked: root.attentionOnly; onClicked: root.attentionOnly = checked }
        }
        ListView {
            id: list
            objectName: "resourceList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8 * root.uiScale
            model: root.rows
            boundsBehavior: Flickable.StopAtBounds
            currentIndex: -1
            ScrollBar.vertical: ScrollBar { }
            Keys.onReturnPressed: root.openSelected()
            Keys.onEnterPressed: root.openSelected()
            Keys.onRightPressed: { var o = root.rows[currentIndex]; if (o && o.kind === "server" && root.collapsed(o.id)) root.togglePreference("collapsed", o.id) }
            Keys.onLeftPressed: { var o = root.rows[currentIndex]; if (o && o.kind === "server" && !root.collapsed(o.id)) root.togglePreference("collapsed", o.id) }
            Keys.onPressed: function(event) {
                if (event.text === "j") { incrementCurrentIndex(); event.accepted = true }
                else if (event.text === "k") { decrementCurrentIndex(); event.accepted = true }
                else if (event.text === "f" && root.rows[currentIndex]) { root.togglePreference("favorites", root.rows[currentIndex].id); event.accepted = true }
            }
            delegate: Rectangle {
                id: card
                required property var modelData
                required property int index
                readonly property bool server: modelData.kind === "server"
                x: server ? 0 : 14 * root.uiScale
                width: list.width - x - 12 * root.uiScale
                height: details.implicitHeight + 24 * root.uiScale
                radius: 9 * root.uiScale
                color: Qt.alpha(root.foreground, server ? 0.055 : 0.025)
                border.color: ListView.isCurrentItem && list.activeFocus ? root.accent : root.line
                ColumnLayout {
                    id: details
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 12 * root.uiScale
                    spacing: 8 * root.uiScale
                    RowLayout {
                        Layout.fillWidth: true
                        Action {
                            visible: card.server
                            text: root.collapsed(card.modelData.id) ? "+" : "−"
                            onClicked: root.togglePreference("collapsed", card.modelData.id)
                            Accessible.name: "Expand or collapse " + card.modelData.name
                        }
                        Button {
                            id: nameButton
                            Layout.fillWidth: true
                            padding: 0
                            hoverEnabled: true
                            contentItem: Label { text: card.modelData.name; font.bold: true; font.pixelSize: (card.server ? 16 : 14) * root.uiScale; color: nameButton.hovered || nameButton.activeFocus ? root.accent : root.foreground }
                            background: Item {}
                            onClicked: root.openRequested(card.modelData.url)
                            Accessible.name: "Open " + card.modelData.name + " in xCloud"
                        }
                        Label { text: card.server ? card.modelData.status : card.modelData.type; color: root.muted; Layout.maximumWidth: 110 * root.uiScale }
                        Action { text: root.favorite(card.modelData.id) ? "★" : "☆"; onClicked: root.togglePreference("favorites", card.modelData.id); Accessible.name: "Pin " + card.modelData.name }
                        Action { visible: !card.server; text: "↗"; enabled: !!card.modelData.public_url; onClicked: root.openRequested(card.modelData.public_url); Accessible.name: "Open public website" }
                    }
                    Label {
                        visible: card.server
                        Layout.fillWidth: true
                        text: (card.modelData.provider || "") + " · " + (card.modelData.location || "") + " · " + (card.modelData.sites || []).length + " sites"
                        color: root.muted
                    }
                    RowLayout {
                        visible: card.server
                        Layout.fillWidth: true
                        spacing: 16 * root.uiScale
                        Repeater {
                            model: ["cpu", "memory", "disk"]
                            delegate: ColumnLayout {
                                id: metric
                                required property string modelData
                                readonly property var value: (card.modelData.metrics || {})[modelData + "_usage"]
                                Layout.fillWidth: true
                                spacing: 5 * root.uiScale
                                Label { text: metric.modelData.toUpperCase() + "  " + (typeof metric.value === "number" ? Math.round(metric.value) + "%" : "—"); color: root.muted; font.pixelSize: 11 * root.uiScale }
                                Rectangle {
                                    Layout.fillWidth: true; height: 3 * root.uiScale; radius: height / 2; color: root.line
                                    Rectangle { width: parent.width * (typeof metric.value === "number" ? Math.max(0, Math.min(100, metric.value)) / 100 : 0); height: parent.height; radius: parent.radius; color: metric.value >= 85 ? root.urgent : root.accent }
                                }
                            }
                        }
                    }
                    Label {
                        visible: card.server
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        elide: Text.ElideNone
                        color: root.muted
                        font.pixelSize: 11 * root.uiScale
                        text: {
                            var m = card.modelData.maintenance
                            var source = (card.modelData.metrics || {}).recorded_at
                            return (source ? "Metrics sampled " + root.age(Date.parse(source) / 1000) + " · " : "") +
                                (m ? (m.reboot_required ? "Reboot required" : "No reboot flag") + " · OS updates: " + (typeof m.updates === "number" ? m.updates : "unavailable") + " · Package cache: " + root.age(m.apt_checked_at) : "Reboot / OS updates: optional SSH check") + "\nServer backups: " + (card.modelData.server_backup || "Unavailable")
                        }
                    }
                    RowLayout {
                        visible: !card.server
                        Layout.fillWidth: true
                        spacing: 18 * root.uiScale
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3 * root.uiScale
                            Label { text: "LAST SUCCESSFUL BACKUP"; font.pixelSize: 9 * root.uiScale; font.letterSpacing: 0.6; color: root.muted }
                            Label { text: root.backupText(card.modelData); Layout.fillWidth: true; color: root.accent }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3 * root.uiScale
                            Label { text: "MAINTENANCE"; font.pixelSize: 9 * root.uiScale; font.letterSpacing: 0.6; color: root.muted }
                            Label { text: root.updatesText(card.modelData); Layout.fillWidth: true }
                        }
                    }
                    Label {
                        visible: !card.server
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        elide: Text.ElideNone
                        font.pixelSize: 11 * root.uiScale
                        color: root.muted
                        text: {
                            var b = card.modelData.backup || {}, s = card.modelData.ssl || {}
                            return "Latest attempt: " + (b.attempt_status || "unavailable") + (b.last_attempt ? " · " + root.age(b.last_attempt) : "") +
                                "\nSSL: " + (s.expires_ts ? "expires " + new Date(s.expires_ts * 1000).toLocaleDateString() : "unavailable") + " · Website: " + (card.modelData.http || "Not checked")
                        }
                    }
                    Repeater {
                        model: card.modelData.issues || []
                        delegate: Label { required property var modelData; Layout.fillWidth: true; wrapMode: Text.Wrap; elide: Text.ElideNone; text: (modelData.severity === "critical" ? "!  " : "△  ") + modelData.message; color: root.urgent; font.pixelSize: 12 * root.uiScale }
                    }
                    Label {
                        visible: (card.modelData.unavailable || []).length > 0
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        elide: Text.ElideNone
                        text: "?  " + (card.modelData.unavailable || []).join(" · ")
                        color: root.muted
                        font.pixelSize: 11 * root.uiScale
                    }
                }
            }
            Label {
                anchors.centerIn: parent
                width: parent.width - 24 * root.uiScale
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                visible: root.rows.length === 0
                color: root.muted
                text: root.busy ? "Checking your servers…" : !root.snapshot.checked_at ? "Use Setup to connect your xCloud account." : search.text || root.attentionOnly ? "No matching servers or sites." : "No servers returned for this team."
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: "Read-only · Enter opens xCloud · Ctrl+R refreshes"; color: root.muted; font.pixelSize: 10 * root.uiScale; Layout.fillWidth: true }
            Label { text: root.snapshot.unavailable_count ? root.snapshot.unavailable_count + " checks unavailable" : ""; color: root.muted; font.pixelSize: 10 * root.uiScale }
        }
    }
}
