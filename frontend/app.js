/**
 * Smart PUC — Frontend Logic (3-Node Architecture)
 * ==================================================
 * Multi-contract dashboard with:
 *   - EmissionRegistry interaction (records, stats, events)
 *   - PUCCertificate NFT claiming and verification
 *   - GreenToken balance tracking
 *   - Real MetaMask signing for vehicle owner transactions
 *   - Real-time event subscriptions (no polling for alerts)
 *   - OSRM route simulation with map
 *   - LSTM prediction visualization
 */

// === HTML Escape Helper (XSS Prevention) ===
function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// === Configuration ===
const API_BASE = window.SMART_PUC_API || 'http://127.0.0.1:5000';

// === Auth Helper ===
// wallet.js monkey-patches window.fetch to auto-attach JWT tokens for
// all requests to API_BASE. For authenticated POST endpoints, use
// authFetch() which also sets Content-Type.
function authFetch(url, options = {}) {
    // wallet.js handles Authorization header via monkey-patched fetch.
    // We just ensure Content-Type is set for JSON POSTs.
    const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
    return fetch(url, { ...options, headers }).then(function(res) {
        if (!res.ok) {
            console.warn('[SmartPUC] ' + (options.method || 'GET') + ' ' + url + ' → ' + res.status);
        }
        return res;
    }).catch(function(e) {
        console.error('[SmartPUC] Network error: ' + url, e.message);
        throw e;
    });
}

// Contract ABIs — EmissionRegistry (3-node version)
const REGISTRY_ABI = [
    "function getAllRecords(string memory _vehicleId) view returns (tuple(string vehicleId, uint256 co2, uint256 co, uint256 nox, uint256 hc, uint256 pm25, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status, address deviceAddress, address stationAddress)[])",
    "function getRecordsPaginated(string memory _vehicleId, uint256 _offset, uint256 _limit) view returns (tuple(string vehicleId, uint256 co2, uint256 co, uint256 nox, uint256 hc, uint256 pm25, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status, address deviceAddress, address stationAddress)[])",
    "function getViolations(string memory _vehicleId) view returns (tuple(string vehicleId, uint256 co2, uint256 co, uint256 nox, uint256 hc, uint256 pm25, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status, address deviceAddress, address stationAddress)[])",
    "function getRegisteredVehicles() view returns (string[])",
    "function getVehicleStats(string memory _vehicleId) view returns (uint256, uint256, uint256, uint256)",
    "function getRecordCount(string memory _vehicleId) view returns (uint256)",
    "function isCertificateEligible(string memory _vehicleId) view returns (bool, uint256)",
    "function consecutivePassCount(string memory _vehicleId) view returns (uint256)",
    "function vehicleOwners(string memory _vehicleId) view returns (address)",
    "function claimVehicle(string memory _vehicleId)",
    "function setTestingStation(address _station, bool _authorized)",
    "function setRegisteredDevice(address _device, bool _registered)",
    "event RecordStored(string indexed vehicleId, uint256 recordIndex, uint256 timestamp, address indexed station, address indexed device)",
    "event ViolationDetected(string indexed vehicleId, uint256 cesScore, uint256 timestamp)",
    "event FraudDetected(string indexed vehicleId, uint256 fraudScore, uint256 timestamp)",
    "event CertificateEligible(string indexed vehicleId, uint256 consecutivePasses)",
    "function computeCES(uint256 _co2, uint256 _co, uint256 _nox, uint256 _hc, uint256 _pm25) view returns (uint256)",
    "function getViolationCount(string memory _vehicleId) view returns (uint256)",
    "function getViolationsPaginated(string memory _vehicleId, uint256 _offset, uint256 _limit) view returns (tuple(string vehicleId, uint256 co2, uint256 co, uint256 nox, uint256 hc, uint256 pm25, uint256 cesScore, uint256 fraudScore, uint256 vspValue, uint8 wltcPhase, uint256 timestamp, bool status, address deviceAddress, address stationAddress)[])"
];

const PUC_CERT_ABI = [
    "function isValid(string memory _vehicleId) view returns (bool, uint256, uint256)",
    "function getCertificate(uint256 _tokenId) view returns (tuple(string vehicleId, address vehicleOwner, uint256 issueTimestamp, uint256 expiryTimestamp, uint256 averageCES, uint256 totalRecordsAtIssue, address issuedByStation, bool revoked, string revokeReason))",
    "function getVerificationData(string memory _vehicleId) view returns (bool, uint256, string, address, uint256, uint256, uint256, bool)",
    "function getVehicleCertificate(string memory _vehicleId) view returns (uint256)",
    "function certificateCount(string memory _vehicleId) view returns (uint256)",
    "event CertificateIssued(uint256 indexed tokenId, string vehicleId, address indexed vehicleOwner, address indexed issuedBy, uint256 issueTimestamp, uint256 expiryTimestamp, uint256 averageCES)",
    "event CertificateRevoked(uint256 indexed tokenId, string vehicleId, string reason, address revokedBy)",
    "function tokenURI(uint256 tokenId) view returns (string)"
];

const GREEN_TOKEN_ABI = [
    "function balanceOf(address account) view returns (uint256)",
    "function getRewardSummary(address _account) view returns (uint256 balance, uint256 earned)",
    "function totalRewardsMinted() view returns (uint256)",
    "event RewardMinted(address indexed recipient, uint256 amount, uint256 totalEarned)",
    "function redeem(uint8 _rewardType)",
    "function getRewardCost(uint8 _rewardType) view returns (uint256)",
    "function getRedemptionStats(address _account) view returns (uint256 totalRedemptions, uint256[4] memory perType)"
];

// === Global State ===
let registryAddress = null;
let pucCertAddress = null;
let greenTokenAddress = null;
let provider = null;
let signer = null;
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
    if (document.getElementById('map')) initMap();
    initPredictionChart();
    loadContractAddresses();
});

// === Contract Address Loading ===
async function loadContractAddresses() {
    try {
        const res = await fetch(API_BASE + '/api/status');
        const data = await res.json();
        if (data.registryAddress) registryAddress = data.registryAddress;
        if (data.pucCertAddress) pucCertAddress = data.pucCertAddress;
        if (data.greenTokenAddress) greenTokenAddress = data.greenTokenAddress;
    } catch (e) {
        console.warn('Could not load contract addresses from backend:', e.message);
    }
}

// === Contract Helpers ===
async function getRegistryContract(writeable = false) {
    if (!registryAddress || !provider) return null;
    const s = writeable ? signer : provider;
    return new ethers.Contract(registryAddress, REGISTRY_ABI, s);
}

async function getPUCCertContract(writeable = false) {
    if (!pucCertAddress || !provider) return null;
    const s = writeable ? signer : provider;
    return new ethers.Contract(pucCertAddress, PUC_CERT_ABI, s);
}

async function getGreenTokenContract() {
    if (!greenTokenAddress || !provider) return null;
    return new ethers.Contract(greenTokenAddress, GREEN_TOKEN_ABI, provider);
}

// Backward compat for authority.html
async function getContract() { return getRegistryContract(); }

// === Wallet Connection ===
async function connectWallet() {
    if (typeof window.ethereum === 'undefined') {
        showAlert('MetaMask not detected. Please install MetaMask.', 'warning');
        return;
    }
    try {
        await window.ethereum.request({ method: 'eth_requestAccounts' });
        // Ensure we're on the local Hardhat chain — otherwise every
        // contract read against the local addresses would fail with
        // "missing revert data".
        if (window.SmartPUC && window.SmartPUC.ensureChain) {
            try { await window.SmartPUC.ensureChain(); }
            catch (e) {
                showAlert('Please approve the network switch in MetaMask and try again.', 'warning');
                return;
            }
        }
        provider = new ethers.BrowserProvider(window.ethereum);
        signer = await provider.getSigner();
        const address = await signer.getAddress();
        const short = address.slice(0, 6) + '...' + address.slice(-4);

        document.getElementById('walletDot')?.classList.add('connected');
        const walletAddr = document.getElementById('walletAddress');
        if (walletAddr) walletAddr.textContent = short;
        const connectBtn = document.getElementById('connectWalletBtn');
        if (connectBtn) {
            connectBtn.textContent = 'Connected';
            connectBtn.disabled = true;
        }

        await loadContractAddresses();
        showAlert('Wallet connected: ' + short, 'success');

        // Load Green Token balance if on vehicle dashboard
        loadGreenTokenBalance(address);
    } catch (err) {
        showAlert('Wallet connection failed: ' + err.message, 'warning');
    }
}

// === Green Token Balance ===
async function loadGreenTokenBalance(address) {
    const balEl = document.getElementById('gctBalance');
    const earnEl = document.getElementById('gctEarned');
    if (!balEl) return;

    try {
        const res = await fetch(`${API_BASE}/api/green-tokens/${address}`);
        const data = await res.json();
        if (data.success) {
            balEl.textContent = data.tokens.balance.toFixed(0);
            if (earnEl) earnEl.textContent = data.tokens.earned.toFixed(0);
        }
    } catch (e) { console.warn('Could not load GCT balance:', e.message); }
}

// === Map Initialization ===
function initMap() {
    const routeSelect = document.getElementById('routeSelect');
    if (routeSelect) {
        MUMBAI_ROUTES.forEach((r, i) => {
            const opt = document.createElement('option');
            opt.value = i;
            opt.textContent = r.name;
            routeSelect.appendChild(opt);
        });
    }

    map = L.map('map').setView([19.076, 72.8777], 12);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors', maxZoom: 18
    }).addTo(map);

    const carIcon = L.divIcon({
        className: 'car-marker',
        html: '<div style="font-size:24px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5));">&#128663;</div>',
        iconSize: [30, 30], iconAnchor: [15, 15]
    });
    carMarker = L.marker([19.076, 72.8777], { icon: carIcon }).addTo(map);
}

// === Route Simulation ===
async function toggleRouteSimulation() {
    console.log('[SmartPUC] toggleRouteSimulation() called');
    const btn = document.getElementById('startRouteBtn');
    if (!btn) { console.warn('[SmartPUC] startRouteBtn not found'); return; }

    if (autoSimInterval) {
        clearInterval(autoSimInterval);
        autoSimInterval = null;
        btn.textContent = 'Start Route';
        btn.classList.remove('btn-danger');
        btn.classList.add('btn-accent');
        return;
    }

    btn.textContent = 'Stop';
    btn.classList.remove('btn-accent');
    btn.classList.add('btn-danger');

    // Load route
    const routeIdx = document.getElementById('routeSelect')?.value || 0;
    const route = MUMBAI_ROUTES[routeIdx];
    await loadRoute(route.start, route.end);

    currentPointIndex = 0;
    routeFinished = false;
    // Reset simulator cycle on backend
    const vehicleId = document.getElementById('vehicleIdInput')?.value?.trim()?.toUpperCase() || 'MH12AB1234';
    fetch(API_BASE + '/api/simulate/reset?vehicle_id=' + encodeURIComponent(vehicleId)).catch(() => {});
    // Run first step immediately, then every 500ms for fast demo
    runSimulationStep();
    moveCarAlongRoute();
    autoSimInterval = setInterval(async () => {
        await runSimulationStep();
        moveCarAlongRoute();
    }, 500);
}

async function loadRoute(start, end) {
    try {
        const url = `https://router.project-osrm.org/route/v1/driving/${start[0]},${start[1]};${end[0]},${end[1]}?geometries=geojson&overview=full`;
        console.log('[SmartPUC] Loading route from OSRM...');
        const res = await fetch(url);
        const data = await res.json();
        if (data.routes && data.routes.length > 0) {
            const coords = data.routes[0].geometry.coordinates.map(c => [c[1], c[0]]);
            routeCoordinates = coords;
            if (routePolyline) map.removeLayer(routePolyline);
            routePolyline = L.polyline(coords, { color: '#007AFF', weight: 4, opacity: 0.8 }).addTo(map);
            map.fitBounds(routePolyline.getBounds(), { padding: [30, 30] });
            carMarker.setLatLng(coords[0]);
            console.log('[SmartPUC] Route loaded:', coords.length, 'points');
            return;
        }
    } catch (e) { console.warn('[SmartPUC] OSRM route failed, using straight line:', e.message); }

    // Fallback: straight line between start and end (10 interpolated points)
    console.log('[SmartPUC] Using fallback straight-line route');
    const steps = 30;
    routeCoordinates = [];
    for (let i = 0; i <= steps; i++) {
        const t = i / steps;
        routeCoordinates.push([
            start[1] + t * (end[1] - start[1]),
            start[0] + t * (end[0] - start[0])
        ]);
    }
    if (routePolyline) map.removeLayer(routePolyline);
    routePolyline = L.polyline(routeCoordinates, { color: '#007AFF', weight: 4, opacity: 0.8, dashArray: '10 6' }).addTo(map);
    map.fitBounds(routePolyline.getBounds(), { padding: [30, 30] });
    carMarker.setLatLng(routeCoordinates[0]);
}

let routeFinished = false;

function moveCarAlongRoute() {
    if (routeCoordinates.length === 0) return;
    currentPointIndex += 3; // skip points for faster car movement
    if (currentPointIndex >= routeCoordinates.length) {
        // Route completed — stop simulation automatically
        routeFinished = true;
        clearInterval(autoSimInterval);
        autoSimInterval = null;
        const btn = document.getElementById('startRouteBtn');
        if (btn) { btn.textContent = 'Start Route'; btn.classList.remove('btn-danger'); btn.classList.add('btn-accent'); }
        showAlert('Route completed! Check your certificate eligibility below.', 'success');
        currentPointIndex = routeCoordinates.length - 1; // stay at end
        // Load history for the completed route
        loadHistory();
        return;
    }
    carMarker.setLatLng(routeCoordinates[currentPointIndex]);
}

// === Simulation Step ===
async function runSimulationStep() {
    if (routeFinished) return; // route already completed
    const vehicleId = document.getElementById('vehicleIdInput')?.value?.trim()?.toUpperCase() || 'MH12AB1234';
    console.log('[SmartPUC] runSimulationStep() vehicle=' + vehicleId);

    try {
        // Use /api/simulate (no auth required) for frontend demo.
        // This generates a realistic reading without writing to blockchain.
        const url = API_BASE + '/api/simulate?vehicle_id=' + encodeURIComponent(vehicleId);
        console.log('[SmartPUC] Fetching:', url);
        const res = await fetch(url);
        if (!res.ok) { console.warn('[SmartPUC] simulate HTTP', res.status); return; }
        const data = await res.json();
        console.log('[SmartPUC] simulate response:', JSON.stringify(data).substring(0, 200));
        if (!data.success) { console.warn('[SmartPUC] simulate success=false:', data.error); return; }

        const d = data.data || data;
        console.log('[SmartPUC] Updating UI — CES:', d.ces_score, 'status:', d.status, 'speed:', d.speed);
        updateLiveMetrics(d);
        updateCESGauge(d.ces_score);
        updateComplianceBadge(d.status);
        updateWLTCPhase(d.wltc_phase);
        updateFraudAlert(d.fraud_score, d.fraud_status);
        updateLatestTx(d);
        updatePredictionChart(d.predictions);
        updateVehicleStats(d.vehicle_stats);
        checkCertificateEligibility(d.certificate_eligible);
    } catch (e) { console.error('[SmartPUC] Simulation step FAILED:', e.message, e); }
}

// === UI Update Functions ===
function updateLiveMetrics(d) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    const bar = (id, pct) => { const el = document.getElementById(id); if (el) el.style.width = Math.min(pct, 100) + '%'; };

    set('metricRpm', Math.round(d.rpm));
    set('metricSpeed', d.speed?.toFixed(1));
    set('metricFuel', d.fuel_rate?.toFixed(2));
    set('metricCo2', d.co2_g_per_km?.toFixed(1));
    set('metricCo', d.co_g_per_km?.toFixed(4));
    set('metricNox', d.nox_g_per_km?.toFixed(4));
    set('metricHc', d.hc_g_per_km?.toFixed(4));
    set('metricPm25', d.pm25_g_per_km?.toFixed(6));

    bar('rpmBar', (d.rpm / 6500) * 100);
    bar('speedBar', (d.speed / 131.3) * 100);
    bar('fuelBar', (d.fuel_rate / 15) * 100);
    bar('co2Bar', (d.co2_g_per_km / 120) * 100);
    bar('coBar', (d.co_g_per_km / 1.0) * 100);
    bar('noxBar', (d.nox_g_per_km / 0.06) * 100);
    bar('hcBar', (d.hc_g_per_km / 0.10) * 100);
    bar('pm25Bar', (d.pm25_g_per_km / 0.0045) * 100);
}

function updateCESGauge(ces) {
    const arc = document.getElementById('cesArc');
    const text = document.getElementById('cesValueText');
    if (!arc || !text) return;

    const maxArc = 251.3;
    const ratio = Math.min(ces / 2.0, 1.0);
    arc.setAttribute('stroke-dasharray', `${ratio * maxArc} ${maxArc}`);
    text.textContent = ces?.toFixed(3) || '--';
}

function updateComplianceBadge(status) {
    const badge = document.getElementById('complianceBadge');
    if (!badge) return;
    badge.textContent = status || 'UNKNOWN';
    badge.className = 'status-badge status-badge-large ' + (status === 'PASS' ? 'pass' : status === 'FAIL' ? 'fail' : '');
}

function updateWLTCPhase(phase) {
    WLTC_PHASE_KEYS.forEach((key, i) => {
        const el = document.getElementById(key);
        if (el) el.classList.toggle('active', i === phase);
    });
}

function updateFraudAlert(score, fraudStatus) {
    const banner = document.getElementById('fraudAlertBanner');
    const display = document.getElementById('fraudScoreDisplay');
    if (!banner) return;

    if (score >= 0.65) {
        banner.classList.remove('hidden');
        if (display) display.textContent = score.toFixed(3);
    } else {
        banner.classList.add('hidden');
    }
}

function updateLatestTx(d) {
    const container = document.getElementById('latestTx');
    if (!container) return;

    if (d.txHash) {
        container.innerHTML = `
            <div style="padding: 0.5rem 0;">
                <div style="margin-bottom:0.5rem;"><span class="text-muted">TX Hash:</span> <span class="text-mono">${escapeHtml(d.txHash)}</span></div>
                <div style="margin-bottom:0.5rem;"><span class="text-muted">Block:</span> ${escapeHtml(String(d.blockNumber))}</div>
                <div style="margin-bottom:0.5rem;"><span class="text-muted">Gas Used:</span> ${escapeHtml(String(d.gas_used || 0))}</div>
                <div style="margin-bottom:0.5rem;"><span class="text-muted">Status:</span> <span class="status-badge ${d.tx_status === 'success' ? 'pass' : 'fail'}">${escapeHtml(d.tx_status)}</span></div>
                <div style="margin-bottom:0.5rem;"><span class="text-muted">Device Signed:</span> ${d.device_signed ? '<span class="text-pass">Yes</span>' : '<span class="text-muted">No (demo mode)</span>'}</div>
                ${d.device_address ? `<div><span class="text-muted">Device:</span> <span class="text-mono">${escapeHtml(d.device_address.slice(0,10))}...</span></div>` : ''}
            </div>`;
    }
}

function updateVehicleStats(stats) {
    if (!stats) return;
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('statTotalRecords', stats.total_records || 0);
    set('statViolations', stats.violations || 0);
    set('statFraudAlerts', stats.fraud_alerts || 0);
    set('statAvgCES', stats.avg_ces ? stats.avg_ces.toFixed(4) : '--');
}

// === Certificate Functions ===
function checkCertificateEligibility(certEligible) {
    const claimBtn = document.getElementById('claimCertBtn');
    const eligDiv = document.getElementById('certEligibility');
    if (!claimBtn || !certEligible) return;

    if (certEligible.eligible) {
        claimBtn.style.display = 'inline-block';
        if (eligDiv) {
            eligDiv.style.display = 'block';
            eligDiv.style.background = 'rgba(76,175,80,0.1)';
            eligDiv.style.border = '1px solid rgba(76,175,80,0.3)';
            eligDiv.innerHTML = `<span style="color:#4CAF50;font-weight:600;">Eligible for PUC Certificate!</span> (${certEligible.consecutive_passes} consecutive passes)`;
        }
    } else {
        claimBtn.style.display = 'none';
        if (eligDiv && certEligible.consecutive_passes > 0) {
            eligDiv.style.display = 'block';
            eligDiv.style.background = 'rgba(255,152,0,0.1)';
            eligDiv.style.border = '1px solid rgba(255,152,0,0.3)';
            eligDiv.innerHTML = `<span style="color:#FF9800;">${certEligible.consecutive_passes}/3 consecutive passes</span> — need ${3 - certEligible.consecutive_passes} more`;
        }
    }
}

async function loadCertificateStatus() {
    const vehicleId = document.getElementById('vehicleIdInput')?.value?.trim()?.toUpperCase() || '';
    try {
        const res = await fetch(`${API_BASE}/api/certificate/${encodeURIComponent(vehicleId)}`);
        const data = await res.json();
        if (!data.success) return;

        const cert = data.certificate;
        const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

        if (cert.valid) {
            set('certStatus', 'VALID');
            document.getElementById('certStatus')?.setAttribute('style', 'color:#4CAF50;font-weight:700;');
        } else if (cert.certificate?.revoked) {
            set('certStatus', 'REVOKED');
            document.getElementById('certStatus')?.setAttribute('style', 'color:#F44336;font-weight:700;');
        } else {
            set('certStatus', 'Not Issued');
        }

        const c = cert.certificate;
        if (c) {
            set('certIssueDate', c.issueTimestamp ? new Date(c.issueTimestamp * 1000).toLocaleDateString() : '—');
            set('certExpiryDate', c.expiryTimestamp ? new Date(c.expiryTimestamp * 1000).toLocaleDateString() : '—');
            set('certAvgCES', c.averageCES?.toFixed(4) || '—');
            set('certTokenId', cert.token_id > 0 ? `#${cert.token_id}` : '—');

            const days = c.expiryTimestamp ? Math.max(0, Math.floor((c.expiryTimestamp - Date.now()/1000) / 86400)) : 0;
            set('certValidity', cert.valid ? `${days} days remaining` : '—');
        }
    } catch (e) { console.warn('Certificate load failed:', e.message); }
}

async function requestCertificate() {
    const vehicleId = document.getElementById('vehicleIdInput')?.value?.trim()?.toUpperCase() || '';

    // Try to get signer from MetaMask if not already set (wallet.js may
    // have connected without setting app.js's signer variable)
    if (!signer && typeof window.ethereum !== 'undefined') {
        try {
            const accounts = await window.ethereum.request({ method: 'eth_accounts' });
            if (accounts && accounts.length > 0) {
                provider = new ethers.BrowserProvider(window.ethereum);
                signer = await provider.getSigner();
            }
        } catch (e) { /* ignore */ }
    }
    if (!signer) { showAlert('Connect MetaMask first', 'warning'); return; }

    const ownerAddress = await signer.getAddress();

    try {
        const res = await authFetch(`${API_BASE}/api/certificate/issue`, {
            method: 'POST',
            body: JSON.stringify({ vehicle_id: vehicleId, vehicle_owner: ownerAddress })
        });
        const data = await res.json();
        if (data.success) {
            showAlert('PUC Certificate NFT issued! TX: ' + data.result.tx_hash?.slice(0, 16) + '...', 'success');
            loadCertificateStatus();
            loadGreenTokenBalance(ownerAddress);
        } else {
            showAlert('Certificate issuance failed: ' + (data.error || 'Unknown error'), 'warning');
        }
    } catch (e) { showAlert('Error: ' + e.message, 'warning'); }
}

// === History Loading ===
async function loadHistory() {
    console.log('[SmartPUC] loadHistory() called');
    const vehicleId = document.getElementById('vehicleIdInput')?.value?.trim()?.toUpperCase() || 'MH12AB1234';
    console.log('[SmartPUC] Loading history for:', vehicleId);
    const tbody = document.getElementById('historyTableBody');
    if (!tbody) { console.warn('[SmartPUC] historyTableBody element not found'); return; }

    tbody.innerHTML = '<tr><td colspan="10" class="text-center"><div class="spinner"></div></td></tr>';

    try {
        const res = await fetch(`${API_BASE}/api/history/${encodeURIComponent(vehicleId)}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const countEl = document.getElementById('historyCount');
        if (countEl) countEl.textContent = `${data.count} records`;

        if (!data.records || data.records.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="text-center text-dim" style="padding:2rem">No records found</td></tr>';
            return;
        }

        tbody.innerHTML = data.records.map((r, i) => {
            const co2 = (r.co2Level / 1000).toFixed(1);
            const co = (r.coLevel / 1000).toFixed(4);
            const nox = (r.noxLevel / 1000).toFixed(4);
            const hc = (r.hcLevel / 1000).toFixed(4);
            const pm25 = (r.pm25Level / 1000).toFixed(6);
            const ces = (r.cesScore / 10000).toFixed(3);
            const fraud = (r.fraudScore / 10000).toFixed(3);
            const cl = r.status === 'PASS' ? 'pass' : 'fail';
            const fcl = parseFloat(fraud) >= 0.65 ? 'text-fail' : 'text-pass';

            return `<tr>
                <td class="mono">${i + 1}</td>
                <td>${escapeHtml(r.vehicleId)}</td>
                <td class="mono">${co2}</td><td class="mono">${co}</td>
                <td class="mono">${nox}</td><td class="mono">${hc}</td><td class="mono">${pm25}</td>
                <td class="mono">${ces}</td>
                <td class="mono ${fcl}">${fraud}</td>
                <td><span class="status-badge ${cl}">${escapeHtml(r.status)}</span></td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="10" class="text-center text-fail">${escapeHtml(e.message)}</td></tr>`;
    }
}

// === Prediction Chart ===
function initPredictionChart() {
    const canvas = document.getElementById('predictionChart');
    if (!canvas || typeof Chart === 'undefined') return;

    predictionChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: ['+5s', '+10s', '+15s', '+20s', '+25s'],
            datasets: [{
                label: 'Predicted CES',
                data: [0, 0, 0, 0, 0],
                borderColor: '#00BCD4',
                backgroundColor: 'rgba(0, 188, 212, 0.1)',
                borderWidth: 2, fill: true, tension: 0.4,
                pointBackgroundColor: '#00BCD4', pointRadius: 4,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, max: 2.0, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0' } },
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#8892b0' } }
            }
        }
    });
}

function updatePredictionChart(predictions) {
    if (!predictionChart || !predictions) return;
    const statusEl = document.getElementById('predictionStatus');

    if (predictions.ces_scores) {
        predictionChart.data.datasets[0].data = predictions.ces_scores;
        predictionChart.update('none');
        if (statusEl) statusEl.textContent = 'LSTM active';

        // LSTM warning
        const maxPred = Math.max(...predictions.ces_scores);
        const warnEl = document.getElementById('lstmWarning');
        const warnMsg = document.getElementById('lstmWarningMsg');
        if (warnEl && maxPred > 0.85) {
            warnEl.classList.remove('hidden');
            if (warnMsg) warnMsg.textContent = `CES predicted to reach ${maxPred.toFixed(3)} in next 25s`;
        } else if (warnEl) {
            warnEl.classList.add('hidden');
        }
    }
}

// === Alert System ===
function showAlert(message, type = 'info') {
    const container = document.getElementById('alertsContainer');
    if (!container) return;
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.innerHTML = `${escapeHtml(message)}<button class="alert-dismiss" onclick="this.parentElement.remove()">&#10005;</button>`;
    container.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 8000);
}
