/**
 * Smart PUC — Frontend Logic (Ethers.js v6 + Leaflet + Chart.js)
 * ================================================================
 * Multi-pollutant dashboard with fraud alerts, LSTM predictions,
 * WLTC phase tracking, CES gauge, and NFT certificate viewer.
 */

// === HTML Escape Helper (XSS Prevention) ===
function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// === Configuration & Global State ===
const API_BASE = window.SMART_PUC_API || 'http://127.0.0.1:5000';
const CONTRACT_ABI = [
    "function getAllRecords(string memory _vehicleId) public view returns (tuple(string vehicleId, uint256 co2Level, uint256 coLevel, uint256 noxLevel, uint256 hcLevel, uint256 pm25Level, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status)[])",
    "function getViolations(string memory _vehicleId) public view returns (tuple(string vehicleId, uint256 co2Level, uint256 coLevel, uint256 noxLevel, uint256 hcLevel, uint256 pm25Level, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status)[])",
    "function getRegisteredVehicles() public view returns (string[])",
    "function getVehicleStats(string memory _vehicleId) public view returns (uint256, uint256, uint256, uint256)",
    "event ViolationDetected(string indexed vehicleId, uint256 cesScore, uint256 timestamp)",
    "event FraudDetected(string indexed vehicleId, uint256 fraudScore, uint256 timestamp)"
];

let contractAddress = null;
let provider = null;
let signer = null;
let contract = null;
let autoSimInterval = null;
let predictionChart = null;

// === Map & Route Variables ===
let map = null;
let carMarker = null;
let routePolyline = null;
let routeCoordinates = [];
let currentPointIndex = 0;

const MUMBAI_ROUTES = [
    { name: "Bandra - Andheri", start: [72.8347, 19.0596], end: [72.8497, 19.1136] },
    { name: "Dadar - Worli", start: [72.8426, 19.0176], end: [72.8156, 19.0163] },
    { name: "Borivali - Churchgate", start: [72.8566, 19.2288], end: [72.8256, 18.9322] },
    { name: "Kurla - BKC", start: [72.8774, 19.0726], end: [72.8654, 19.0664] },
    { name: "Thane - Mulund", start: [72.9781, 19.2183], end: [72.9515, 19.1726] },
    { name: "Navi Mumbai - Sion", start: [73.0116, 19.0763], end: [72.8631, 19.0388] },
    { name: "Colaba - Marine Drive", start: [72.8153, 18.9067], end: [72.8242, 18.9431] },
    { name: "Goregaon - Powai", start: [72.8465, 19.1645], end: [72.9051, 19.1187] },
    { name: "Juhu - Vile Parle", start: [72.8267, 19.1075], end: [72.8458, 19.1009] },
    { name: "Chembur - Vashi", start: [72.8984, 19.0544], end: [72.9922, 19.0825] }
];

const WLTC_PHASE_NAMES = ['Low (Urban)', 'Medium (Suburban)', 'High (Rural)', 'Extra High (Motorway)'];
const WLTC_PHASE_KEYS = ['phaseLow', 'phaseMedium', 'phaseHigh', 'phaseExtraHigh'];

// === Initialization ===
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('map')) {
        initMap();
    }
    initPredictionChart();
});

// === Wallet Connection ===
async function connectWallet() {
    if (typeof window.ethereum === 'undefined') {
        showAlert('MetaMask not detected. Please install MetaMask.', 'warning');
        return;
    }
    try {
        await window.ethereum.request({ method: 'eth_requestAccounts' });
        provider = new ethers.BrowserProvider(window.ethereum);
        signer = await provider.getSigner();
        const address = await signer.getAddress();
        const short = address.slice(0, 6) + '...' + address.slice(-4);

        document.getElementById('walletDot')?.classList.add('connected');
        if (document.getElementById('walletAddress')) document.getElementById('walletAddress').textContent = short;
        if (document.getElementById('connectWalletBtn')) {
            document.getElementById('connectWalletBtn').textContent = 'Connected';
            document.getElementById('connectWalletBtn').disabled = true;
        }

        await loadContractAddress();
        showAlert('Wallet connected: ' + short, 'success');
    } catch (err) {
        showAlert('Wallet connection failed: ' + err.message, 'warning');
    }
}

async function loadContractAddress() {
    try {
        const res = await fetch(API_BASE + '/api/status');
        const data = await res.json();
        if (data.contractAddress) contractAddress = data.contractAddress;
    } catch (e) {
        try {
            const res = await fetch('../build/contracts/EmissionContract.json');
            const build = await res.json();
            const keys = Object.keys(build.networks || {});
            if (keys.length > 0) contractAddress = build.networks[keys[keys.length - 1]].address;
        } catch (err) { /* ignore */ }
    }
}

async function getContract() {
    if (contract) return contract;
    if (!provider && typeof window.ethereum !== 'undefined') {
        provider = new ethers.BrowserProvider(window.ethereum);
        signer = await provider.getSigner();
    }
    if (!contractAddress) await loadContractAddress();
    if (!contractAddress) return null;
    contract = new ethers.Contract(contractAddress, CONTRACT_ABI, signer || provider);
    return contract;
}

// === Map & OSRM Routing ===
function initMap() {
    map = L.map('map').setView([19.0760, 72.8777], 11);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap'
    }).addTo(map);

    const select = document.getElementById('routeSelect');
    MUMBAI_ROUTES.forEach((r, i) => {
        select.add(new Option(r.name, i));
    });

    select.addEventListener('change', fetchAndDrawRoute);
    fetchAndDrawRoute();
}

async function fetchAndDrawRoute() {
    if (autoSimInterval) toggleRouteSimulation();

    const route = MUMBAI_ROUTES[document.getElementById('routeSelect').value];
    const url = 'http://router.project-osrm.org/route/v1/driving/' +
        route.start[0] + ',' + route.start[1] + ';' + route.end[0] + ',' + route.end[1] +
        '?overview=full&geometries=geojson';

    try {
        const res = await fetch(url);
        const data = await res.json();
        if (!data.routes || data.routes.length === 0) throw new Error("No route found");

        const geojson = data.routes[0].geometry;
        routeCoordinates = geojson.coordinates.map(coord => [coord[1], coord[0]]);

        if (routePolyline) map.removeLayer(routePolyline);
        routePolyline = L.polyline(routeCoordinates, { color: 'var(--clr-primary)', weight: 5, opacity: 0.8 }).addTo(map);
        map.fitBounds(routePolyline.getBounds());

        currentPointIndex = 0;
        if (carMarker) map.removeLayer(carMarker);

        const carIcon = L.divIcon({
            html: '<div style="font-size:24px; filter: drop-shadow(0 0 4px var(--clr-bg));">&#128663;</div>',
            className: '',
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        });

        carMarker = L.marker(routeCoordinates[0], { icon: carIcon }).addTo(map);
    } catch (err) {
        showAlert('Error fetching route: ' + err.message, 'warning');
    }
}

// === Simulation Loop ===
function toggleRouteSimulation() {
    const btn = document.getElementById('startRouteBtn');

    if (autoSimInterval) {
        clearInterval(autoSimInterval);
        autoSimInterval = null;
        if (btn) btn.innerHTML = 'Start Route';
    } else {
        if (routeCoordinates.length === 0) {
            showAlert("Please wait for route to load", "warning");
            return;
        }
        if (currentPointIndex >= routeCoordinates.length - 1) {
            currentPointIndex = 0;
        }

        stepSimulation();
        autoSimInterval = setInterval(stepSimulation, 3000);
        if (btn) btn.innerHTML = 'Stop Route';
    }
}

async function stepSimulation() {
    if (currentPointIndex >= routeCoordinates.length - 1) {
        toggleRouteSimulation();
        showAlert("Destination Reached", "success");
        return;
    }

    const jump = Math.floor(Math.random() * 5) + 1;
    const nextIndex = Math.min(currentPointIndex + jump, routeCoordinates.length - 1);

    const p1 = L.latLng(routeCoordinates[currentPointIndex]);
    const p2 = L.latLng(routeCoordinates[nextIndex]);
    const distMeters = p1.distanceTo(p2);

    currentPointIndex = nextIndex;
    carMarker.setLatLng(p2);
    map.panTo(p2, { animate: true, duration: 1 });

    let speed = (distMeters / 3) * 3.6;
    if (speed > 80) {
        speed = 65 + Math.random() * 15;
    }

    let rpm, fuel_rate;
    if (speed > 55) {
        rpm = 1800 + Math.random() * 1000;
        fuel_rate = 5.0 + Math.random() * 2.0;
    } else if (speed < 20) {
        rpm = 1000 + Math.random() * 1800;
        fuel_rate = 7.0 + Math.random() * 4.0;
    } else {
        rpm = 1400 + Math.random() * 1200;
        fuel_rate = 6.0 + Math.random() * 3.0;
    }

    rpm = Math.floor(rpm);
    speed = parseFloat(speed.toFixed(1));
    fuel_rate = parseFloat(fuel_rate.toFixed(2));

    await recordCustomEmission(rpm, speed, fuel_rate);
}

// === Backend Interaction ===
async function recordCustomEmission(rpm, speed, fuel_rate) {
    const vehicleId = document.getElementById('vehicleIdInput')?.value.trim().toUpperCase() || 'MH12AB1234';

    try {
        const res = await fetch(API_BASE + '/api/record', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                vehicle_id: vehicleId,
                fuel_rate: fuel_rate,
                speed: speed,
                rpm: rpm,
                fuel_type: 'petrol'
            })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const r = data.data;

        // Update all metrics
        updateMetrics(r);
        updatePollutantMetrics(r);
        updateCompliance(r.status, r.ces_score);
        updateCESGauge(r.ces_score);
        updateWLTCPhase(r.wltc_phase);
        updateFraudAlert(r.fraud_score, r.fraud_status);
        updateLatestTx(r);
        updatePredictions(r.predictions);
        updateVehicleStats(r);

        if (r.status === 'FAIL') {
            showAlert('VIOLATION: ' + vehicleId + ' CES=' + (r.ces_score || 0).toFixed(3), 'violation');
        }

    } catch (err) {
        console.error("Recording error:", err);
    }
}

// === UI Updaters ===
function updateMetrics(data) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    const bar = (id, pct) => { const el = document.getElementById(id); if (el) el.style.width = Math.min(pct, 100) + '%'; };

    set('metricRpm', data.rpm || '--');
    set('metricSpeed', data.speed || '--');
    set('metricFuel', data.fuel_rate || '--');
    set('metricCo2', data.co2_g_per_km != null ? data.co2_g_per_km.toFixed(1) : '--');

    bar('rpmBar', ((data.rpm || 0) / 6500) * 100);
    bar('speedBar', ((data.speed || 0) / 140) * 100);
    bar('fuelBar', ((data.fuel_rate || 0) / 15) * 100);
    bar('co2Bar', ((data.co2_g_per_km || 0) / 200) * 100);
}

function updatePollutantMetrics(data) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    const bar = (id, pct) => { const el = document.getElementById(id); if (el) el.style.width = Math.min(pct, 100) + '%'; };

    set('metricCo', data.co_g_per_km != null ? data.co_g_per_km.toFixed(4) : '--');
    set('metricNox', data.nox_g_per_km != null ? data.nox_g_per_km.toFixed(5) : '--');
    set('metricHc', data.hc_g_per_km != null ? data.hc_g_per_km.toFixed(5) : '--');
    set('metricPm25', data.pm25_g_per_km != null ? data.pm25_g_per_km.toFixed(6) : '--');

    bar('coBar', ((data.co_g_per_km || 0) / 1.5) * 100);
    bar('noxBar', ((data.nox_g_per_km || 0) / 0.1) * 100);
    bar('hcBar', ((data.hc_g_per_km || 0) / 0.15) * 100);
    bar('pm25Bar', ((data.pm25_g_per_km || 0) / 0.007) * 100);
}

function updateCompliance(status, cesScore) {
    const panel = document.getElementById('compliancePanel');
    const badge = document.getElementById('complianceBadge');
    if (!panel || !badge) return;

    const displayScore = cesScore != null ? cesScore.toFixed(3) : '?';
    panel.className = 'compliance-panel ' + (status === 'PASS' ? 'pass-state' : 'fail-state');
    badge.className = 'status-badge status-badge-large ' + (status === 'PASS' ? 'pass' : 'fail');
    badge.textContent = status + ' (CES: ' + displayScore + ')';
}

function updateCESGauge(cesScore) {
    if (cesScore == null) return;
    const arc = document.getElementById('cesArc');
    const text = document.getElementById('cesValueText');
    if (!arc || !text) return;

    const maxArc = 251.3;
    const ratio = Math.min(cesScore / 1.5, 1.0);
    arc.setAttribute('stroke-dasharray', (ratio * maxArc).toFixed(1) + ' ' + maxArc);
    text.textContent = cesScore.toFixed(3);

    if (cesScore >= 1.0) {
        text.style.fill = 'var(--clr-fail)';
    } else if (cesScore >= 0.75) {
        text.style.fill = 'var(--clr-warn)';
    } else {
        text.style.fill = 'var(--clr-pass)';
    }
}

function updateWLTCPhase(phase) {
    if (phase == null) return;
    WLTC_PHASE_KEYS.forEach((key, i) => {
        const el = document.getElementById(key);
        if (el) {
            el.classList.toggle('active', i === phase);
        }
    });
}

function updateFraudAlert(fraudScore, fraudStatus) {
    const banner = document.getElementById('fraudAlertBanner');
    const scoreEl = document.getElementById('fraudScoreDisplay');
    const msgEl = document.getElementById('fraudAlertMsg');
    if (!banner) return;

    if (fraudScore != null && fraudScore >= 0.65) {
        banner.classList.remove('hidden');
        if (scoreEl) scoreEl.textContent = fraudScore.toFixed(3);
        if (msgEl && fraudStatus) {
            msgEl.textContent = 'Severity: ' + (fraudStatus.severity || 'HIGH') +
                '. Violations: ' + (fraudStatus.violations || []).join(', ');
        }
    } else {
        banner.classList.add('hidden');
    }
}

function updateLatestTx(data) {
    const el = document.getElementById('latestTx');
    if (!el) return;
    el.innerHTML =
        '<div style="font-size:.8125rem">' +
        '<div class="flex-between mb-md"><span class="text-muted">Tx Hash</span><span class="text-mono">' + (data.txHash ? escapeHtml(data.txHash.slice(0, 10)) + '...' + escapeHtml(data.txHash.slice(-8)) : 'N/A') + '</span></div>' +
        '<div class="flex-between mb-md"><span class="text-muted">Block</span><span class="text-mono">' + escapeHtml(data.blockNumber || 'N/A') + '</span></div>' +
        '<div class="flex-between mb-md"><span class="text-muted">CO&#8322;</span><span class="text-mono">' + escapeHtml(data.co2_g_per_km != null ? data.co2_g_per_km.toFixed(1) : '--') + ' g/km</span></div>' +
        '<div class="flex-between mb-md"><span class="text-muted">CES</span><span class="text-mono">' + escapeHtml(data.ces_score != null ? data.ces_score.toFixed(3) : '--') + '</span></div>' +
        '<div class="flex-between mb-md"><span class="text-muted">Fraud</span><span class="text-mono">' + escapeHtml(data.fraud_score != null ? data.fraud_score.toFixed(3) : '--') + '</span></div>' +
        '<div class="flex-between mb-md"><span class="text-muted">Status</span><span class="status-badge ' + (data.status === 'PASS' ? 'pass' : 'fail') + '">' + escapeHtml(data.status || '--') + '</span></div>' +
        '<div class="flex-between"><span class="text-muted">Vehicle</span><span><strong>' + escapeHtml(data.vehicle_id || '--') + '</strong></span></div>' +
        '</div>';
}

function updateVehicleStats(data) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    if (data.vehicle_stats) {
        set('statTotalRecords', data.vehicle_stats.total_records || 0);
        set('statViolations', data.vehicle_stats.violations || 0);
        set('statFraudAlerts', data.vehicle_stats.fraud_alerts || 0);
        set('statAvgCES', data.vehicle_stats.avg_ces != null ? data.vehicle_stats.avg_ces.toFixed(3) : '--');
    }
}

// === LSTM Prediction Chart ===
function initPredictionChart() {
    const canvas = document.getElementById('predictionChart');
    if (!canvas) return;

    predictionChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: ['+5s', '+10s', '+15s', '+20s', '+25s'],
            datasets: [
                {
                    label: 'Predicted CES',
                    data: [null, null, null, null, null],
                    borderColor: '#6366f1',
                    backgroundColor: 'rgba(99,102,241,0.1)',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Threshold (1.0)',
                    data: [1.0, 1.0, 1.0, 1.0, 1.0],
                    borderColor: '#ef4444',
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false
                },
                {
                    label: 'Warning (0.85)',
                    data: [0.85, 0.85, 0.85, 0.85, 0.85],
                    borderColor: '#f59e0b',
                    borderDash: [3, 3],
                    pointRadius: 0,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    min: 0,
                    max: 1.5,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#94a3b8' }
                },
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#94a3b8' }
                }
            },
            plugins: {
                legend: {
                    labels: { color: '#e2e8f0', font: { size: 11 } }
                }
            }
        }
    });
}

function updatePredictions(predictions) {
    const statusEl = document.getElementById('predictionStatus');
    const warningEl = document.getElementById('lstmWarning');
    const warningMsgEl = document.getElementById('lstmWarningMsg');

    if (!predictions || !predictions.predictions) {
        if (statusEl) statusEl.textContent = 'Collecting data...';
        return;
    }

    if (statusEl) statusEl.textContent = 'Active';

    if (predictionChart) {
        const cesValues = predictions.predictions.map(p => p.ces);
        predictionChart.data.datasets[0].data = cesValues;
        predictionChart.update('none');
    }

    if (predictions.warning && warningEl) {
        warningEl.classList.remove('hidden');
        if (warningMsgEl) warningMsgEl.textContent = predictions.warning_message || 'CES predicted to exceed 0.85';
    } else if (warningEl) {
        warningEl.classList.add('hidden');
    }
}

// === Certificate Status ===
async function loadCertificateStatus() {
    const vehicleId = document.getElementById('vehicleIdInput')?.value.trim().toUpperCase() || 'MH12AB1234';
    try {
        const res = await fetch(API_BASE + '/api/certificate/' + vehicleId);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const cert = data.certificate;
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

        set('certStatus', cert.valid ? 'VALID' : (cert.revoked ? 'REVOKED' : 'EXPIRED'));
        set('certIssueDate', cert.issue_date ? new Date(cert.issue_date * 1000).toLocaleDateString() : '--');
        set('certExpiryDate', cert.expiry_date ? new Date(cert.expiry_date * 1000).toLocaleDateString() : '--');
        set('certAvgCES', cert.avg_ces != null ? (cert.avg_ces / 10000).toFixed(3) : '--');
        set('certTokenId', cert.token_id || '--');
        set('certValidity', cert.valid ? 'Active' : 'Inactive');

        const statusEl = document.getElementById('certStatus');
        if (statusEl) {
            statusEl.className = 'cert-value ' + (cert.valid ? 'text-pass' : 'text-fail');
        }
    } catch (err) {
        showAlert('Certificate check failed: ' + err.message, 'warning');
    }
}

// === History ===
async function loadHistory() {
    const vehicleId = document.getElementById('vehicleIdInput')?.value.trim().toUpperCase() || 'MH12AB1234';
    const tbody = document.getElementById('historyTableBody');
    const countEl = document.getElementById('historyCount');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="10" class="text-center"><div class="spinner"></div></td></tr>';

    try {
        const res = await fetch(API_BASE + '/api/history/' + vehicleId);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const recs = data.records;
        if (countEl) countEl.textContent = recs.length + ' records';
        if (!recs.length) {
            tbody.innerHTML = '<tr><td colspan="10" class="text-center text-dim" style="padding:2rem">No records</td></tr>';
            return;
        }

        tbody.innerHTML = recs.map((r, i) => {
            const cl = r.status === 'PASS' ? 'pass' : 'fail';
            return '<tr>' +
                '<td class="mono">' + (i + 1) + '</td>' +
                '<td>' + escapeHtml(r.vehicleId) + '</td>' +
                '<td class="mono">' + escapeHtml((r.co2Level / 1000).toFixed(1)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.coLevel / 1000).toFixed(3)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.noxLevel / 1000).toFixed(4)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.hcLevel / 1000).toFixed(4)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.pm25Level / 1000).toFixed(4)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.cesScore / 10000).toFixed(3)) + '</td>' +
                '<td class="mono">' + escapeHtml((r.fraudScore / 10000).toFixed(3)) + '</td>' +
                '<td><span class="status-badge ' + cl + '">' + escapeHtml(r.status) + '</span></td>' +
                '</tr>';
        }).join('');
    } catch (err) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-fail">' + escapeHtml(err.message) + '</td></tr>';
    }
}

// === Alerts ===
function showAlert(msg, type) {
    type = type || 'success';
    const c = document.getElementById('alertsContainer');
    if (!c) return;
    const cls = type === 'violation' ? 'alert-violation' : type === 'warning' ? 'alert-warning' : 'alert-success';
    const id = 'alert-' + Date.now();
    c.insertAdjacentHTML('afterbegin',
        '<div class="alert ' + cls + '" id="' + id + '">' + escapeHtml(msg) +
        '<button class="alert-dismiss" onclick="document.getElementById(\'' + id + '\').remove()">&#10005;</button></div>');
    setTimeout(function () { var el = document.getElementById(id); if (el) el.remove(); }, 8000);
}

// === Network listeners ===
if (typeof window.ethereum !== 'undefined') {
    window.ethereum.on('accountsChanged', function () { location.reload(); });
    window.ethereum.on('chainChanged', function () { location.reload(); });
}
