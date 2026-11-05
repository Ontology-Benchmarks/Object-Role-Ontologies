var eventObjectSidebar = document.getElementById('event-object-sidebar');
var eventObjectToggle = document.getElementById('event-object-toggle');
var eventObjectTitle = document.getElementById('event-object-details-title');
var eventObjectTableBody = document.getElementById('event-object-table-body');
var eventObjectEmpty = document.getElementById('event-object-empty');
var activityFilterList = document.getElementById('event-object-activity-filter-list');
var objectFilterList = document.getElementById('event-object-object-filter-list');
var clearFiltersButton = document.getElementById('event-object-clear-filters');
var physicsSettleToken = 0;
var filterState = {
    activities: [],
    objects: []
};

function configureDynamicNetwork() {
    if (typeof network === 'undefined' || !network || typeof network.setOptions !== 'function') {
        return;
    }

    network.setOptions({
        physics: {
            enabled: false,
            solver: 'barnesHut',
            barnesHut: {
                gravitationalConstant: -5200,
                centralGravity: 0.26,
                springLength: 95,
                springConstant: 0.07,
                damping: 0.84,
                avoidOverlap: 0.65
            },
            maxVelocity: 4,
            minVelocity: 0.45,
            timestep: 0.3,
            stabilization: {
                enabled: true,
                iterations: 240,
                updateInterval: 25,
                onlyDynamicEdges: false,
                fit: true
            }
        },
        interaction: {
            dragNodes: true,
            dragView: true,
            zoomView: true
        },
        edges: {
            arrows: {
                to: { enabled: false },
                from: { enabled: false }
            },
            smooth: {
                enabled: false
            }
        }
    });
}

function runPhysicsSettleBurst() {
    if (typeof network === 'undefined' || !network || typeof network.setOptions !== 'function') {
        return;
    }

    physicsSettleToken += 1;
    var currentToken = physicsSettleToken;

    network.setOptions({ physics: { enabled: true } });

    if (typeof network.stabilize === 'function') {
        network.stabilize(180);
    }

    if (typeof network.once === 'function') {
        network.once('stabilized', function() {
            if (currentToken !== physicsSettleToken) return;
            network.setOptions({ physics: { enabled: false } });
        });
    }

    setTimeout(function() {
        if (currentToken !== physicsSettleToken) return;
        network.setOptions({ physics: { enabled: false } });
    }, 1200);
}

function normalizeRoleNodeAppearance() {
    if (
        typeof network === 'undefined' ||
        !network ||
        !network.body ||
        !network.body.data ||
        !network.body.data.nodes
    ) {
        return;
    }

    var nodeIds = network.body.data.nodes.getIds();
    var updates = [];

    nodeIds.forEach(function(nodeId) {
        var nodeIdStr = String(nodeId);
        if (!nodeIdStr.startsWith('role::')) return;

        var parsedRole = parseRoleKey(nodeIdStr.replace('role::', ''));
        var roleLabel = parsedRole ? formatRoleDisplay(parsedRole.activity, parsedRole.otype) : nodeIdStr;

        updates.push({
            id: nodeId,
            shape: 'circle',
            label: roleLabel,
            margin: 2,
            font: { size: 8, face: 'Arial', color: '#1f4f2b' }
        });
    });

    if (updates.length > 0) {
        network.body.data.nodes.update(updates);
    }
}

function getRoleKey(activity, otype) {
    return String(activity) + '::' + String(otype);
}

function parseRoleKey(roleKey) {
    if (!roleKey) return null;
    var splitIdx = roleKey.lastIndexOf('::');
    if (splitIdx < 0) return null;
    return {
        activity: roleKey.substring(0, splitIdx),
        otype: roleKey.substring(splitIdx + 2)
    };
}

function normalizeRoleToken(value) {
    return String(value || '').trim();
}

function formatRoleDisplay(activity, otype) {
    var activityTokens = normalizeRoleToken(activity)
        .split(/[^A-Za-z0-9]+/)
        .filter(function(token) { return token.length > 0; });
    var objectTokens = normalizeRoleToken(otype)
        .split(/[^A-Za-z0-9]+/)
        .filter(function(token) { return token.length > 0; });

    var activityCode = '';
    if (activityTokens.length >= 2) {
        activityCode = activityTokens[0].charAt(0) + activityTokens[1].charAt(0);
    } else if (activityTokens.length === 1) {
        activityCode = activityTokens[0].slice(0, 2);
    }
    if (activityCode.length < 2) {
        activityCode = (activityCode + 'X').slice(0, 2);
    }

    var objectCode = objectTokens.length > 0 ? objectTokens[0].charAt(0) : 'X';
    return 'Role_' + activityCode.toUpperCase() + '_' + objectCode.toUpperCase();
}

function labelSizeForHex(label, fontSize, padding, minWidth, minHeight) {
    var lines = String(label || '').split('\n');
    if (lines.length === 0) {
        lines = [''];
    }

    var longestLine = lines.reduce(function(maxLen, line) {
        return Math.max(maxLen, String(line).length);
    }, 0);
    var lineCount = Math.max(lines.length, 1);

    var approxCharWidth = fontSize * 0.72;
    var approxLineHeight = fontSize * 1.35;

    var width = Math.ceil((longestLine * approxCharWidth) + (padding * 2) + 24);
    var height = Math.ceil((lineCount * approxLineHeight) + (padding * 2) + 10);

    return {
        widthConstraint: { minimum: Math.max(width, minWidth) },
        heightConstraint: { minimum: Math.max(height, minHeight) }
    };
}

function hideAllGraphElements() {
    if (
        typeof network === 'undefined' ||
        !network.body ||
        !network.body.data ||
        !network.body.data.edges ||
        !network.body.data.nodes
    ) {
        return;
    }

    var allNodeIds = network.body.data.nodes.getIds();
    allNodeIds.forEach(function(nodeId) {
        network.body.data.nodes.update({ id: nodeId, hidden: true });
    });

    var allEdgeIds = network.body.data.edges.getIds();
    allEdgeIds.forEach(function(edgeId) {
        network.body.data.edges.update({ id: edgeId, hidden: true });
    });
}

function hasActiveFilter() {
    var selection = buildSelection();
    return selection.activities.length > 0 || selection.objects.length > 0;
}

function setFilterSelection(filterType, values) {
    var valueSet = {};
    (values || []).forEach(function(v) {
        valueSet[String(v)] = true;
    });
    filterState[filterType] = Object.keys(valueSet).sort();
}

function getFilterSelection(filterType) {
    return (filterState[filterType] || []).slice();
}

function buildFilterItems(listEl, values, selectedValues, labelFn, filterType) {
    if (!listEl) return;
    var selectedSet = {};
    (selectedValues || []).forEach(function(v) {
        selectedSet[String(v)] = true;
    });

    listEl.innerHTML = '';

    values.forEach(function(value) {
        var item = document.createElement('label');
        item.className = 'event-object-filter-item';

        var checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = value;
        checkbox.checked = Boolean(selectedSet[value]);

        checkbox.addEventListener('change', function() {
            var next = getFilterSelection(filterType);
            var idx = next.indexOf(value);
            if (checkbox.checked && idx < 0) {
                next.push(value);
            }
            if (!checkbox.checked && idx >= 0) {
                next.splice(idx, 1);
            }
            setFilterSelection(filterType, next);
            applyGraphFilter();
        });

        var text = document.createElement('span');
        text.textContent = labelFn ? labelFn(value) : value;

        item.appendChild(checkbox);
        item.appendChild(text);
        listEl.appendChild(item);
    });
}

function buildSelection() {
    return {
        activities: getFilterSelection('activities'),
        objects: getFilterSelection('objects')
    };
}

function filterRowsBySelection(selection) {
    var activitySet = {};
    var objectSet = {};

    (selection.activities || []).forEach(function(v) { activitySet[v] = true; });
    (selection.objects || []).forEach(function(v) { objectSet[v] = true; });

    var hasActivityFilter = (selection.activities || []).length > 0;
    var hasObjectFilter = (selection.objects || []).length > 0;

    return eventObjectData.filter(function(row) {
        if (hasActivityFilter && !activitySet[row.activity]) {
            return false;
        }
        if (hasObjectFilter && !objectSet[row.otype]) {
            return false;
        }
        return true;
    });
}

function getFilteredRows() {
    return filterRowsBySelection(buildSelection());
}

function setOptionsFromValues(selectEl, values, labelFn, selectedValues) {
    buildFilterItems(selectEl, values, selectedValues, labelFn, 'activities');
}

function updateFilterOptions() {
    if (!activityFilterList || !objectFilterList) {
        return;
    }

    var currentSelection = buildSelection();

    var rowsForActivity = filterRowsBySelection({
        activities: [],
        objects: currentSelection.objects
    });
    var rowsForObject = filterRowsBySelection({
        activities: currentSelection.activities,
        objects: []
    });

    var activityValues = Array.from(new Set(rowsForActivity.map(function(r) { return r.activity; }))).sort();
    var objectValues = Array.from(new Set(rowsForObject.map(function(r) { return r.otype; }))).sort();

    var validActivitySelection = currentSelection.activities.filter(function(value) {
        return activityValues.indexOf(value) >= 0;
    });
    var validObjectSelection = currentSelection.objects.filter(function(value) {
        return objectValues.indexOf(value) >= 0;
    });

    setFilterSelection('activities', validActivitySelection);
    setFilterSelection('objects', validObjectSelection);

    buildFilterItems(activityFilterList, activityValues, validActivitySelection, function(value) { return value; }, 'activities');
    buildFilterItems(objectFilterList, objectValues, validObjectSelection, function(value) { return value; }, 'objects');
}

function setGraphVisibleForRows(rows) {
    hideAllGraphElements();
    if (!rows || rows.length === 0) {
        return;
    }

    var visibleNodeIds = {};
    var visibleEdgeIds = {};
    var dynamicNodeUpdates = [];
    var dynamicEdgeUpdates = [];

    rows.forEach(function(row) {
        var activity = row.activity;
        var otype = row.otype;
        var earliest = row.earliest_pop != null ? row.earliest_pop : 0;
        var latest = row.latest_pop != null ? row.latest_pop : 0;
        var total = row.total_pop != null ? row.total_pop : 0;
        var part = row.part_pop != null ? row.part_pop : 0;
        var roleLabel = formatRoleDisplay(activity, otype);
        var earliestLabel = 'Earliest\n' + String(earliest) + '/' + String(part);
        var latestLabel = 'Latest\n' + String(latest) + '/' + String(part);

        var roleId = 'role::' + activity + '::' + otype;
        var earliestSubroleId = 'subrole::earliest::' + activity + '::' + otype;
        var latestSubroleId = 'subrole::latest::' + activity + '::' + otype;

        dynamicNodeUpdates.push({
            id: roleId,
            label: roleLabel,
            shape: 'circle',
            margin: 2,
            font: { size: 8, face: 'Arial', color: '#1f4f2b' }
        });

        dynamicNodeUpdates.push({
            id: earliestSubroleId,
            label: earliestLabel,
            shape: 'circle',
            color: { background: '#e6f4ea', border: '#188038', highlight: { background: '#d7f1dd', border: '#188038' } },
            borderWidth: 2,
            font: { size: 7, face: 'Arial', color: '#0f5a24' },
            margin: 2,
            group: 'subrole'
        });

        dynamicNodeUpdates.push({
            id: latestSubroleId,
            label: latestLabel,
            shape: 'circle',
            color: { background: '#fce8e6', border: '#c5221f', highlight: { background: '#f9dedc', border: '#c5221f' } },
            borderWidth: 2,
            font: { size: 7, face: 'Arial', color: '#8b1d1a' },
            margin: 2,
            group: 'subrole'
        });

        dynamicEdgeUpdates.push({
            id: 'edge::role_subrole::earliest::' + activity + '::' + otype,
            from: roleId,
            to: earliestSubroleId,
            label: String(earliest) + '/' + String(part),
            color: { color: '#34a853', highlight: '#188038' },
            font: { align: 'horizontal', size: 10, background: '#fafafa', strokeWidth: 1, strokeColor: '#dddddd' },
            width: 1.3,
            length: 28
        });

        dynamicEdgeUpdates.push({
            id: 'edge::role_subrole::latest::' + activity + '::' + otype,
            from: roleId,
            to: latestSubroleId,
            label: String(latest) + '/' + String(part),
            color: { color: '#ea4335', highlight: '#c5221f' },
            font: { align: 'horizontal', size: 10, background: '#fafafa', strokeWidth: 1, strokeColor: '#dddddd' },
            width: 1.3,
            length: 28
        });

        visibleNodeIds['event::' + activity] = true;
        visibleNodeIds[roleId] = true;
        visibleNodeIds['object::' + otype] = true;
        visibleNodeIds[earliestSubroleId] = true;
        visibleNodeIds[latestSubroleId] = true;

        visibleEdgeIds['edge::event_role::' + activity + '::' + otype] = true;
        visibleEdgeIds['edge::role_object::' + activity + '::' + otype] = true;
        visibleEdgeIds['edge::role_subrole::earliest::' + activity + '::' + otype] = true;
        visibleEdgeIds['edge::role_subrole::latest::' + activity + '::' + otype] = true;
    });

    var nodeUpdates = Object.keys(visibleNodeIds).map(function(nodeId) {
        return {
            id: nodeId,
            hidden: false
        };
    });

    var edgeUpdates = Object.keys(visibleEdgeIds).map(function(edgeId) {
        var isRoleObjectEdge = String(edgeId).startsWith('edge::role_object::');
        var isRoleSubroleEdge = String(edgeId).startsWith('edge::role_subrole::');
        return {
            id: edgeId,
            hidden: false,
            length: isRoleSubroleEdge ? 28 : (isRoleObjectEdge ? 75 : 110),
            arrows: { to: { enabled: false }, from: { enabled: false } }
        };
    });

    if (nodeUpdates.length > 0 && network && network.body && network.body.data && network.body.data.nodes) {
        network.body.data.nodes.update(nodeUpdates);
        if (dynamicNodeUpdates.length > 0) {
            network.body.data.nodes.update(dynamicNodeUpdates);
        }
    }

    if (edgeUpdates.length > 0 && network && network.body && network.body.data && network.body.data.edges) {
        network.body.data.edges.update(edgeUpdates);
        if (dynamicEdgeUpdates.length > 0) {
            network.body.data.edges.update(dynamicEdgeUpdates);
        }
    }
}

function applyGraphFilter() {
    updateFilterOptions();
    var filteredRows = getFilteredRows();
    renderEventObjectRows(hasActiveFilter() ? 'Filtered Roles' : 'All Roles', filteredRows);

    if (!hasActiveFilter()) {
        hideAllGraphElements();
        if (typeof network !== 'undefined' && network && typeof network.setOptions === 'function') {
            network.setOptions({ physics: { enabled: false } });
        }
        return;
    }

    setGraphVisibleForRows(filteredRows);
    runPhysicsSettleBurst();
}

function populateFilterOptions() {
    updateFilterOptions();
}

function syncFiltersFromRole(activity, otype) {
    setFilterSelection('activities', [activity]);
    setFilterSelection('objects', [otype]);
}

function clearAllFilters() {
    setFilterSelection('activities', []);
    setFilterSelection('objects', []);
    applyGraphFilter();
}

function renderEventObjectRows(title, rows) {
    eventObjectTitle.textContent = title;
    eventObjectTableBody.innerHTML = '';
    if (!rows || rows.length === 0) {
        eventObjectEmpty.style.display = 'block';
        return;
    }
    eventObjectEmpty.style.display = 'none';

    rows.forEach(function(row) {
        var tr = document.createElement('tr');
        var roleKey = getRoleKey(row.activity, row.otype);
        tr.className = 'event-object-role-row';
        tr.dataset.roleKey = roleKey;
        var part = String(row.part_pop) + '/' + String(row.total_pop);
        var earliest = String(row.earliest_pop) + '/' + String(row.part_pop);
        var latest = String(row.latest_pop) + '/' + String(row.part_pop);
        var objectRoleMin = row.object_role_min_card != null ? row.object_role_min_card : row.min_card;
        var objectRoleMax = row.object_role_max_card != null ? row.object_role_max_card : row.max_card;
        var eventRoleMin = row.event_role_min_card != null ? row.event_role_min_card : row.min_card;
        var eventRoleMax = row.event_role_max_card != null ? row.event_role_max_card : row.max_card;
        var objectRoleCard = String(objectRoleMin) + '..' + String(objectRoleMax);
        var eventRoleCard = String(eventRoleMin) + '..' + String(eventRoleMax);
        var roleDisplay = formatRoleDisplay(row.activity, row.otype);
        tr.innerHTML = `
            <td><span class="role-badge">${roleDisplay}</span></td>
            <td><span class="evt-badge">${row.activity}</span></td>
            <td><span class="obj-badge">${row.otype}</span></td>
            <td>
                <span class="stat-line">Participation: ${part}</span>
                <span class="stat-line">Earliest: ${earliest}</span>
                <span class="stat-line">Latest: ${latest}</span>
                <span class="stat-line">Global Card: ${objectRoleCard}</span>
                <span class="stat-line">Individual Card: ${eventRoleCard}</span>
            </td>
        `;

        tr.addEventListener('click', function() {
            syncFiltersFromRole(row.activity, row.otype);
            applyGraphFilter();
        });

        eventObjectTableBody.appendChild(tr);
    });

    if (eventObjectSidebar.classList.contains('collapsed')) {
        eventObjectSidebar.classList.remove('collapsed');
        eventObjectToggle.textContent = '◀';
    }
}

eventObjectToggle.addEventListener('click', function() {
    eventObjectSidebar.classList.toggle('collapsed');
    eventObjectToggle.textContent = eventObjectSidebar.classList.contains('collapsed') ? '▶' : '◀';
});

if (clearFiltersButton) {
    clearFiltersButton.addEventListener('click', clearAllFilters);
}

populateFilterOptions();
applyGraphFilter();

var waitNetwork = setInterval(function() {
    if (typeof network === 'undefined') return;
    clearInterval(waitNetwork);
    normalizeRoleNodeAppearance();
    configureDynamicNetwork();
    hideAllGraphElements();
}, 200);
