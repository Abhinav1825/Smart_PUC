/**
 * Smart PUC — Frontend Logic (Ethers.js v6 + Leaflet + OSRM)
 * ==========================================================
 * Handles wallet connection, real-time map/simulation, 
 * contract reading, and backend API orchestration.
 */

// ═══ Configuration & Global State ═══
const API_BASE = window.SMART_PUC_API || 'http://127.0.0.1:5000';
const CONTRACT_ABI = [
    "function getAllRecords(string memory _vehicleId) public view returns (tuple(string vehicleId, uint256 co2Level, uint256 timestamp, bool status)[])",
    "function getViolations(string memory _vehicleId) public view returns (tuple(string vehicleId, uint256 co2Level, uint256 timestamp, bool status)[])",
    "function getRegisteredVehicles() public view returns (string[])",
    "event ViolationDetected(string indexed vehicleId, uint256 co2Level, uint256 timestamp)"
];

let contractAddress = null;
let provider = null;
let signer = null;
let contract = null;
let autoSimInterval = null;

// ═══ Map & Route Variables ═══
let map = null;
let carMarker = null;
let routePolyline = null;
let routeCoordinates = [];
let currentPointIndex = 0;

// Predefined Real Mumbai Routes [lon, lat]
const MUMBAI_ROUTES = [
    { name: "Bandra → Andheri", start: [72.8347, 19.0596], end: [72.8497, 19.1136] },
    { name: "Dadar → Worli", start: [72.8426, 19.0176], end: [72.8156, 19.0163] },
    { name: "Borivali → Churchgate", start: [72.8566, 19.2288], end: [72.8256, 18.9322] },
    { name: "Kurla → BKC", start: [72.8774, 19.0726], end: [72.8654, 19.0664] },
    { name: "Thane → Mulund", start: [72.9781, 19.2183], end: [72.9515, 19.1726] },
    { name: "Navi Mumbai → Sion", start: [73.0116, 19.0763], end: [72.8631, 19.0388] },
    { name: "Colaba → Marine Drive", start: [72.8153, 18.9067], end: [72.8242, 18.9431] },
    { name: "Goregaon → Powai", start: [72.8465, 19.1645], end: [72.9051, 19.1187] },
    { name: "Juhu → Vile Parle", start: [72.8267, 19.1075], end: [72.8458, 19.1009] },
    { name: "Chembur → Vashi", start: [72.8984, 19.0544], end: [72.9922, 19.0825] }
];

// ═══ Initialization ═══
document.addEventListener('DOMContentLoaded', () => {
    // If we're on the vehicle dashboard, init map
    if (document.getElementById('map')) {
        initMap();
    }
});

// ═══ Wallet Connection ═══
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
        if(document.getElementById('walletAddress')) document.getElementById('walletAddress').textContent = short;
        if(document.getElementById('connectWalletBtn')) {
            document.getElementById('connectWalletBtn').textContent = '✅ Connected';
            document.getElementById('connectWalletBtn').disabled = true;
        }

        await loadContractAddress();
        showAlert(`Wallet connected: ${short}`, 'success');
    } catch (err) {
        showAlert('Wallet connection failed: ' + err.message, 'warning');
    }
}

async function loadContractAddress() {
    try {
        const res = await fetch(`${API_BASE}/api/status`);
        const data = await res.json();
        if (data.contractAddress) contractAddress = data.contractAddress;
    } catch (e) {
        // Fallback
        try {
            const res = await fetch('../build/contracts/EmissionContract.json');
            const build = await res.json();
            const keys = Object.keys(build.networks || {});
            if (keys.length > 0) contractAddress = build.networks[keys[keys.length - 1]].address;
        } catch (err) { }
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

// ═══ Map & OSRM Routing Logic ═══
function initMap() {
    // Center map on Mumbai
    map = L.map('map').setView([19.0760, 72.8777], 11);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap'
    }).addTo(map);

    // Populate route dropdown
    const select = document.getElementById('routeSelect');
    MUMBAI_ROUTES.forEach((r, i) => {
        select.add(new Option(r.name, i));
    });

    // Handle route changes
    select.addEventListener('change', fetchAndDrawRoute);
    
    // Initial route load
    fetchAndDrawRoute();
}

async function fetchAndDrawRoute() {
    if (autoSimInterval) toggleRouteSimulation(); // stop if running

    const route = MUMBAI_ROUTES[document.getElementById('routeSelect').value];
    const url = `http://router.project-osrm.org/route/v1/driving/${route.start[0]},${route.start[1]};${route.end[0]},${route.end[1]}?overview=full&geometries=geojson`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        if (!data.routes || data.routes.length === 0) throw new Error("No route found");

        const geojson = data.routes[0].geometry;
        
        // Extract array of [Lat, Lon]
        routeCoordinates = geojson.coordinates.map(coord => [coord[1], coord[0]]);
        
        if (routePolyline) map.removeLayer(routePolyline);
        routePolyline = L.polyline(routeCoordinates, { color: 'var(--clr-primary)', weight: 5, opacity: 0.8 }).addTo(map);
        
        map.fitBounds(routePolyline.getBounds());

        // Reset car marker
        currentPointIndex = 0;
        if (carMarker) map.removeLayer(carMarker);
        
        // Use a nice car icon emoji
        const carIcon = L.divIcon({
            html: '<div style="font-size:24px; filter: drop-shadow(0 0 4px var(--clr-bg));">🚗</div>',
            className: '',
            iconSize: [24, 24],
            iconAnchor: [12, 12]
        });
        
        carMarker = L.marker(routeCoordinates[0], { icon: carIcon }).addTo(map);

    } catch (err) {
        showAlert('Error fetching OSRM Route: ' + err.message, 'warning');
    }
}

// ═══ Simulation Loop ═══
function toggleRouteSimulation() {
    const btn = document.getElementById('startRouteBtn');

    if (autoSimInterval) {
        // Stop Simulation
        clearInterval(autoSimInterval);
        autoSimInterval = null;
        if (btn) btn.innerHTML = '📍 Start Route';
    } else {
        // Start Simulation
        if (routeCoordinates.length === 0) {
            showAlert("Please wait for route to load", "warning");
            return;
        }
        if (currentPointIndex >= routeCoordinates.length - 1) {
            currentPointIndex = 0; // Restart from beginning
        }
        
        // Execute immediately, then every 3 seconds
        stepSimulation();
        autoSimInterval = setInterval(stepSimulation, 3000);
        if (btn) btn.innerHTML = '⏸ Stop Route';
    }
}

async function stepSimulation() {
    if (currentPointIndex >= routeCoordinates.length - 1) {
        toggleRouteSimulation(); // End of route reached
        showAlert("Destination Reached", "success");
        return;
    }

    // Advance car: Jump 1 to 5 points depending on "traffic"
    const jump = Math.floor(Math.random() * 5) + 1;
    const nextIndex = Math.min(currentPointIndex + jump, routeCoordinates.length - 1);
    
    // Calculate actual distance strictly between these two nodes
    const p1 = L.latLng(routeCoordinates[currentPointIndex]);
    const p2 = L.latLng(routeCoordinates[nextIndex]);
    const distMeters = p1.distanceTo(p2);
    
    currentPointIndex = nextIndex;
    carMarker.setLatLng(p2);
    map.panTo(p2, {animate: true, duration: 1});

    // Speed calculation: we assume this distance took 3 seconds
    // km/h = (meters / 3 seconds) * 3.6
    let speed = (distMeters / 3) * 3.6;
    
    // Cap the maximum speed to realistic Mumbai limits (e.g. Bandra-Worli Sea Link max is 80km/h)
    if (speed > 80) {
        speed = 65 + Math.random() * 15; // Cap between 65-80 km/h
    }

    // Apply Mumbai road heuristics
    let rpm, fuel_rate;
    if (speed > 55) {
        // Highway: smooth RPM, decent fuel rate
        rpm = 1800 + Math.random() * 1000;
        fuel_rate = 5.0 + Math.random() * 2.0;
    } else if (speed < 20) {
        // Urban junction / traffic: revving, poor efficiency
        rpm = 1000 + Math.random() * 1800;
        fuel_rate = 7.0 + Math.random() * 4.0;
    } else {
        // Suburban / Medium flow
        rpm = 1400 + Math.random() * 1200;
        fuel_rate = 6.0 + Math.random() * 3.0;
    }

    // Format for UI/API
    rpm = Math.floor(rpm);
    speed = parseFloat(speed.toFixed(1));
    fuel_rate = parseFloat(fuel_rate.toFixed(2));

    await recordCustomEmission(rpm, speed, fuel_rate);
}

// ═══ Backend Interaction ═══
async function recordCustomEmission(rpm, speed, fuel_rate) {
    const vehicleId = document.getElementById('vehicleIdInput')?.value.trim().toUpperCase() || 'MH12AB1234';

    try {
        // Call explicit api/record endpoint
        const res = await fetch(`${API_BASE}/api/record`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                vehicle_id: vehicleId,
                fuel_rate: fuel_rate,
                speed: speed,
                fuel_type: 'petrol'
            })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const r = data.data;
        
        // Render local metrics
        updateMetrics({ rpm, speed, fuel_rate, co2_int: r.co2 });
        updateCompliance(r.compliance, r.co2);
        updateLatestTx(r);
        
        if (r.compliance === 'FAIL') {
            showAlert(`⚠️ FAIL — ${vehicleId}: ${r.co2} g/km exceeds threshold`, 'violation');
        }

    } catch (err) {
        // Don't overwhelm the UI if backend is temporary offline
        console.error("Recording error:", err);
    }
}

// ═══ UI Updaters ═══
function updateMetrics(data) {
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    const bar = (id, pct) => { const el = document.getElementById(id); if (el) el.style.width = Math.min(pct, 100) + '%'; };

    set('metricRpm', data.rpm);
    set('metricSpeed', data.speed);
    set('metricFuel', data.fuel_rate);
    set('metricCo2', data.co2_int || '—');

    bar('rpmBar', (data.rpm / 4000) * 100);
    bar('speedBar', (data.speed / 120) * 100);
    bar('fuelBar', (data.fuel_rate / 15) * 100);
    bar('co2Bar', ((data.co2_int || 0) / 200) * 100);
}

function updateCompliance(status, co2) {
    const panel = document.getElementById('compliancePanel');
    const badge = document.getElementById('complianceBadge');
    if (!panel || !badge) return;

    panel.className = 'compliance-panel ' + (status === 'PASS' ? 'pass-state' : 'fail-state');
    badge.className = 'status-badge status-badge-large ' + (status === 'PASS' ? 'pass' : 'fail');
    badge.textContent = `${status} — ${co2} g/km`;
}

function updateLatestTx(data) {
    const el = document.getElementById('latestTx');
    if (!el) return;
    el.innerHTML = `
        <div style="font-size:.8125rem">
            <div class="flex-between mb-md"><span class="text-muted">Tx Hash</span><span class="text-mono">${data.txHash?.slice(0,10)}...${data.txHash?.slice(-8)}</span></div>
            <div class="flex-between mb-md"><span class="text-muted">Block</span><span class="text-mono">${data.blockNumber}</span></div>
            <div class="flex-between mb-md"><span class="text-muted">CO₂</span><span class="text-mono">${data.co2} g/km</span></div>
            <div class="flex-between mb-md"><span class="text-muted">Status</span><span class="status-badge ${data.compliance==='PASS'?'pass':'fail'}">${data.compliance}</span></div>
            <div class="flex-between"><span class="text-muted">Vehicle</span><span><strong>${data.vehicle_id}</strong></span></div>
        </div>`;
}

async function loadHistory() {
    const vehicleId = document.getElementById('vehicleIdInput')?.value.trim().toUpperCase() || 'MH12AB1234';
    const tbody = document.getElementById('historyTableBody');
    const countEl = document.getElementById('historyCount');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="5" class="text-center"><div class="spinner"></div></td></tr>';

    try {
        const res = await fetch(`${API_BASE}/api/history/${vehicleId}`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        const recs = data.records;
        if (countEl) countEl.textContent = recs.length + ' records';
        if (!recs.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-center text-dim" style="padding:2rem">No records</td></tr>'; return; }

        tbody.innerHTML = recs.map((r, i) => {
            const d = new Date(r.timestamp * 1000).toLocaleString();
            const cl = r.status === 'PASS' ? 'pass' : 'fail';
            return `<tr><td class="mono">${i+1}</td><td>${r.vehicleId}</td><td class="mono">${r.co2Level}</td><td class="text-muted">${d}</td><td><span class="status-badge ${cl}">${r.status}</span></td></tr>`;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-fail">${err.message}</td></tr>`;
    }
}

function showAlert(msg, type = 'success') {
    const c = document.getElementById('alertsContainer');
    if (!c) return;
    const cls = type === 'violation' ? 'alert-violation' : type === 'warning' ? 'alert-warning' : 'alert-success';
    const id = 'alert-' + Date.now();
    c.insertAdjacentHTML('afterbegin',
        `<div class="alert ${cls}" id="${id}">${msg}<button class="alert-dismiss" onclick="document.getElementById('${id}').remove()">✕</button></div>`);
    setTimeout(() => { const el = document.getElementById(id); if (el) el.remove(); }, 8000);
}

// Listen for network changes properly
if (typeof window.ethereum !== 'undefined') {
    window.ethereum.on('accountsChanged', () => location.reload());
    window.ethereum.on('chainChanged', () => location.reload());
}
