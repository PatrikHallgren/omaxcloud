.pragma library

function copy(prefs) {
    var p = JSON.parse(JSON.stringify(prefs || {}))
    p.collapsed = p.collapsed || []
    p.favorites = p.favorites || []
    p.server_order = p.server_order || []
    p.site_order = p.site_order || {}
    p.muted_issues = p.muted_issues || {}
    return p
}
function isMuted(prefs, id, key) {
    return (((prefs || {}).muted_issues || {})[id] || []).indexOf(key) >= 0
}
function notices(o) {
    return (o.issues || []).concat((o.unavailable || []).map(function(message) {
        return {key: "unavailable:" + message.split(":")[0], message: message, severity: "unknown"}
    }))
}
function active(o, prefs) {
    return notices(o).filter(function(n) { return !isMuted(prefs, o.id, n.key) })
}
function counts(snapshot, prefs) {
    var result = {issues: 0, unavailable: 0, muted: 0}
    ;(snapshot.servers || []).forEach(function(s) {
        ;[s].concat(s.sites || []).forEach(function(o) {
            notices(o).forEach(function(n) {
                if (isMuted(prefs, o.id, n.key)) result.muted++
                else if (n.severity === "unknown") result.unavailable++
                else result.issues++
            })
        })
    })
    return result
}
function sorted(items, order, prefs) {
    order = order || []
    var favorites = prefs.favorites || []
    return items.slice().sort(function(a, b) {
        var ai = order.indexOf(a.id), bi = order.indexOf(b.id)
        if (ai >= 0 || bi >= 0) return (ai < 0 ? order.length : ai) - (bi < 0 ? order.length : bi)
        return Number(favorites.indexOf(b.id) >= 0) - Number(favorites.indexOf(a.id) >= 0) || a.name.localeCompare(b.name)
    })
}
function siblings(snapshot, prefs, obj) {
    if (obj.kind === "server") return sorted(snapshot.servers || [], prefs.server_order, prefs)
    var parent = (snapshot.servers || []).filter(function(s) {
        return (s.sites || []).some(function(site) { return site.id === obj.id })
    })[0]
    return parent ? sorted(parent.sites || [], (prefs.site_order || {})[parent.id], prefs) : []
}
function move(snapshot, prefs, obj, direction) {
    var p = copy(prefs), items = siblings(snapshot, p, obj)
    var ids = items.map(function(item) { return item.id }), index = ids.indexOf(obj.id)
    if (index < 0 || index + direction < 0 || index + direction >= ids.length) return p
    var temp = ids[index + direction]; ids[index + direction] = ids[index]; ids[index] = temp
    if (obj.kind === "server") p.server_order = ids
    else {
        var parent = (snapshot.servers || []).filter(function(s) { return (s.sites || []).some(function(site) { return site.id === obj.id }) })[0]
        p.site_order[parent.id] = ids
    }
    return p
}
function muteLabel(key) {
    var labels = {"backup.missing": "No successful site backup", "backup.failed": "Latest backup attempt failed", "backup.overdue": "Site backup overdue", "backup": "Backup alerts", "ssl": "SSL expiry", "ssl-status": "SSL status", "wp-updates": "WordPress updates", "deployment": "Site / deployment status", "http": "Website availability"}
    return labels[key] || key.replace("unavailable:", "Unavailable: ")
}
function mutedNotices(o, prefs) {
    var current = notices(o)
    return (((prefs || {}).muted_issues || {})[o.id] || []).map(function(key) {
        var existing = current.filter(function(n) { return n.key === key })[0]
        return {key: key, message: existing ? existing.message : muteLabel(key) + " (not currently reported)"}
    })
}
