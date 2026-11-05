var isSidebarOpen = true;
var currentTableData = [];
var currentTableTitle = "";
var sortState = { column: null, direction: 'asc' };

// Toggle View State
var isAggregated = false;

// ---------------------------------------------------------
// Toggle Logic (UI)
// ---------------------------------------------------------

function toggleSidebar() {
    var sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('collapsed');
    isSidebarOpen = !isSidebarOpen;
}

function toggleViewMode() {
    isAggregated = document.getElementById('view-toggle').checked;
    var statusText = document.getElementById('view-status-text');
    
    if (isAggregated) {
        statusText.textContent = "Aggregated View (Merged Edges)";
        renderAggregatedGraph();
    } else {
        statusText.textContent = "Detailed View (All Events)";
        renderDetailedGraph();
    }
}

// ---------------------------------------------------------
// Graph Rendering
// ---------------------------------------------------------

function formatCardinality(minVal, maxVal) {
    var minCard = Number.isFinite(Number(minVal)) ? Number(minVal) : 0;
    var maxCard = Number.isFinite(Number(maxVal)) ? Number(maxVal) : 0;
    return minCard + '..' + maxCard;
}

function getDetailedEdges() {
    // Generate Vis.js edge objects from detailed schemaData
    return schemaData.map(row => {
        var abCard = formatCardinality(row.ABCardinalityMin, row.ABCardinalityMax);
        var baCard = formatCardinality(row.BACardinalityMin, row.BACardinalityMax);
        return {
            id: String(row.id),
            from: row.otype1,
            to: row.otype2,
            label: row.viatype + '\n' + abCard + ' : ' + baCard,
            color: { color: '#5f6368', highlight: '#d93025' },
            font: { align: 'horizontal', size: 14, background: '#fafafa', strokeWidth: 1, strokeColor: '#cccccc' },
            width: 1.5,
            arrowStrikethrough: false
        };
    });
}

function getAggregatedEdges() {
    // Use the pre-calculated aggregation data from Python
    return aggSchemaData.map(row => {
        return {
            id: row.id,
            from: row.from,
            to: row.to,
            label: row.label,
            color: { color: '#1a73e8', highlight: '#174ea6' }, // Bluer to signify aggregation
            width: 3, // Thicker
            font: { align: 'horizontal', size: 14, background: '#e8f0fe', strokeWidth: 1, strokeColor: '#1a73e8' },
            arrowStrikethrough: false
        };
    });
}

function renderDetailedGraph() {
    if (typeof network === 'undefined') return;
    var edges = getDetailedEdges();
    network.body.data.edges.clear();
    network.body.data.edges.add(edges);
}

function renderAggregatedGraph() {
    if (typeof network === 'undefined') return;
    var edges = getAggregatedEdges();
    network.body.data.edges.clear();
    network.body.data.edges.add(edges);
}


// ---------------------------------------------------------
// Resizer Logic
// ---------------------------------------------------------

var resizer = document.getElementById('resizer');
var sidebar = document.getElementById('sidebar');
var isResizing = false;

if (resizer) {
    resizer.addEventListener('mousedown', function(e) {
        isResizing = true;
        e.preventDefault();
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', stopResize);
        sidebar.style.transition = 'none';
    });
}

function handleMouseMove(e) {
    if (!isResizing) return;
    var newWidth = window.innerWidth - e.clientX;
    if (newWidth > 200 && newWidth < (window.innerWidth - 50)) {
        sidebar.style.width = newWidth + 'px';
    }
}

function stopResize() {
    isResizing = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', stopResize);
    sidebar.style.transition = 'transform 0.3s ease-in-out';
}

// ---------------------------------------------------------
// Table & Interaction Logic
// ---------------------------------------------------------

function initEventList() {
    var events = new Set(schemaData.map(row => row.viatype));
    var listContainer = document.getElementById('event-list');
    
    Array.from(events).sort().forEach(evt => {
        var chip = document.createElement('div');
        chip.className = 'event-chip';
        chip.textContent = evt;
        chip.onclick = function() {
            var edges = schemaData.filter(row => row.viatype === evt);
            renderTable('Event: ' + evt, edges);
        };
        listContainer.appendChild(chip);
    });
}

function getEdgesForNode(nodeId) {
    // Always return detailed rows for the table, regardless of view mode
    return schemaData.filter(row => row.otype1 === nodeId || row.otype2 === nodeId);
}

function getEdgeDataById(edgeId) {
    var strId = String(edgeId);
    
    // Check if it's an aggregated edge (starts with 'agg_')
    if (strId.startsWith('agg_')) {
        var aggRow = aggSchemaData.find(row => row.id === strId);
        if (aggRow && aggRow.child_ids) {
            // Find all detailed rows that made up this aggregate
            // Convert child_ids to strings for safe comparison
            var childIds = aggRow.child_ids.map(String);
            var detailedRows = schemaData.filter(r => childIds.includes(String(r.id)));
            return { isAgg: true, rows: detailedRows };
        }
        return null;
    } 
    else {
        // Detailed edge
        var row = schemaData.find(row => String(row.id) === strId);
        return row ? { isAgg: false, rows: [row] } : null;
    }
}

function handleSort(column) {
    if (sortState.column === column) {
        sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
        sortState.column = column;
        sortState.direction = 'asc';
    }
    updateTableUI();
}

function updateTableUI() {
    var titleSpan = document.getElementById('details-title');
    var thead = document.getElementById('table-head');
    var tbody = document.getElementById('table-body');
    var emptyState = document.getElementById('empty-state');
    
    titleSpan.textContent = currentTableTitle;
    tbody.innerHTML = '';
    emptyState.style.display = 'none';
    
    var rows = [...currentTableData];
    if (sortState.column) {
        rows.sort((a, b) => {
            var valA = '', valB = '';
            if (sortState.column === 'event') { valA = a.viatype; valB = b.viatype; }
            if (sortState.column === 'source') { valA = a.otype1; valB = b.otype1; }
            if (sortState.column === 'target') { valA = a.otype2; valB = b.otype2; }
            
            if (valA < valB) return sortState.direction === 'asc' ? -1 : 1;
            if (valA > valB) return sortState.direction === 'asc' ? 1 : -1;
            return 0;
        });
    }

    var getSortIcon = (col) => {
        if (sortState.column !== col) return '';
        return sortState.direction === 'asc' ? ' ▲' : ' ▼';
    };
    
    thead.innerHTML = `
        <tr>
            <th style="width: 15%" class="col-left sortable" onclick="handleSort('event')">
                Event<span class="sort-icon">${getSortIcon('event')}</span>
            </th>
            <th style="width: 27.5%" class="col-right sortable" onclick="handleSort('source')">
                Source<span class="sort-icon">${getSortIcon('source')}</span>
            </th>
            <th style="width: 30%" class="col-center">Card.</th>
            <th style="width: 27.5%" class="col-left sortable" onclick="handleSort('target')">
                Target<span class="sort-icon">${getSortIcon('target')}</span>
            </th>
        </tr>
    `;
    
    rows.forEach(row => {
        var tr = document.createElement('tr');
        var abCard = formatCardinality(row.ABCardinalityMin, row.ABCardinalityMax);
        var baCard = formatCardinality(row.BACardinalityMin, row.BACardinalityMax);
        var srcTotal = row.otype1_total_pop || '?';
        var srcText = row.otype1_part_pop + '/' + srcTotal;
        var srcEarliest = (row.otype1_earliest_pop ?? 0) + '/' + srcTotal;
        var srcLatest = (row.otype1_latest_pop ?? 0) + '/' + srcTotal;
        var tgtTotal = row.otype2_total_pop || '?';
        var tgtText = row.otype2_part_pop + '/' + tgtTotal;
        var tgtEarliest = (row.otype2_earliest_pop ?? 0) + '/' + tgtTotal;
        var tgtLatest = (row.otype2_latest_pop ?? 0) + '/' + tgtTotal;

        tr.innerHTML = `
            <td class="event-badge">${row.viatype}</td>
            <td style="text-align: right;">
                <div style="font-weight: 500;">${row.otype1}</div> 
                <span class="pop-badge" style="text-align: right;">Participation: ${srcText}</span>
                <span class="pop-badge" style="text-align: right;">Earliest: ${srcEarliest}</span>
                <span class="pop-badge" style="text-align: right;">Latest: ${srcLatest}</span>
            </td>
            <td>
                <div class="card-badge-container">
                    <div class="card-badge align-st" title="Source to Target">
                        <span class="card-arrow">S→T</span>
                        <span class="card-val">${abCard}</span>
                    </div>
                    <div class="card-badge align-ts" title="Target to Source">
                        <span class="card-arrow">T→S</span>
                        <span class="card-val">${baCard}</span>
                    </div>
                </div>
            </td>
            <td style="text-align: left;">
                <div style="font-weight: 500;">${row.otype2}</div> 
                <span class="pop-badge">Participation: ${tgtText}</span>
                <span class="pop-badge">Earliest: ${tgtEarliest}</span>
                <span class="pop-badge">Latest: ${tgtLatest}</span>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function renderTable(title, rows) {
    currentTableData = rows;
    currentTableTitle = title;
    sortState = { column: null, direction: 'asc' };
    updateTableUI();
    
    if (sidebar.classList.contains('collapsed')) {
        sidebar.classList.remove('collapsed');
        isSidebarOpen = true;
    }
}

// ---------------------------------------------------------
// Init
// ---------------------------------------------------------

var checkNetwork = setInterval(function() {
    if (typeof network !== 'undefined') {
        clearInterval(checkNetwork);
        
        initEventList();
        
        // Initial setup - default to detailed view
        renderDetailedGraph();
        
        network.on("click", function (params) {
            if (params.nodes.length > 0) {
                var nodeId = params.nodes[0];
                var edges = getEdgesForNode(nodeId);
                renderTable('Object: ' + nodeId, edges);
            } 
            else if (params.edges.length > 0) {
                var edgeId = params.edges[0];
                var data = getEdgeDataById(edgeId);
                if (data) {
                    var title = data.isAgg ? "Aggregated Relationships" : "Relationship Detail";
                    renderTable(title, data.rows);
                }
            } 
        });
    }
}, 200);