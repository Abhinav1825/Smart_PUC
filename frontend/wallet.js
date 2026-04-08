/* Smart PUC — shared wallet / auth / network helper
   ====================================================
   Give every HTML page:
     • persistent wallet connection across tab navigation
     • automatic network switch to the local Hardhat chain before any
       MetaMask contract read (fixes "missing revert data" when the
       user's wallet happens to be on mainnet / Sepolia / etc.)
     • apiFetch() wrapper that auto-attaches the JWT stored by the
       authority login page, so every protected endpoint works without
       each page re-implementing header plumbing.

   Include this script BEFORE any page-specific <script> block:
       <script src="wallet.js"></script>
*/
(function () {
  'use strict';

  const API_BASE = window.SMART_PUC_API || 'http://127.0.0.1:5000';

  // Storage keys — shared across all pages.
  const LS_TOKEN   = 'smartpuc.jwt';
  const LS_ACCOUNT = 'smartpuc.wallet.account';
  const LS_CHAIN   = 'smartpuc.wallet.chainId';

  // Expected local dev chain — matches what backend/.env / deploy.js targets.
  // If you ever move to Ganache flip this to 0x1691 (5777) and update the URL.
  const LOCAL_CHAIN_ID_HEX = '0x7a69';    // 31337
  const LOCAL_CHAIN_ID_DEC = 31337;
  const LOCAL_CHAIN_NAME   = 'Smart PUC (Hardhat)';
  const LOCAL_RPC_URL      = 'http://127.0.0.1:8545';

  // ───────────────────────── auth / fetch wrapper ─────────────────────────

  function getToken() {
    try { return localStorage.getItem(LS_TOKEN) || ''; } catch (_) { return ''; }
  }
  function setToken(t) {
    try {
      if (t) localStorage.setItem(LS_TOKEN, t);
      else   localStorage.removeItem(LS_TOKEN);
    } catch (_) {}
  }

  // Globally monkey-patch window.fetch so every request to the Smart
  // PUC backend gets the stored JWT attached automatically. This fixes
  // every page's /api/* calls without per-call edits.
  const _nativeFetch = window.fetch.bind(window);

  function _shouldAttachAuth(url) {
    try {
      const s = String(url);
      // Only attach to our backend to avoid leaking the token to CDNs,
      // OSRM, IPFS gateways, etc.
      if (s.startsWith(API_BASE)) return true;
      if (s.startsWith('/api/')) return true;
      // Relative paths that look like our API
      if (s.startsWith('api/')) return true;
      return false;
    } catch (_) { return false; }
  }

  window.fetch = function patchedFetch(input, init) {
    init = init || {};
    let url = typeof input === 'string' ? input : (input && input.url) || '';
    const authRelevant = _shouldAttachAuth(url);
    if (authRelevant) {
      const token = getToken();
      if (token) {
        const headers = new Headers(init.headers || (typeof input === 'object' && input.headers) || {});
        if (!headers.has('Authorization')) {
          headers.set('Authorization', 'Bearer ' + token);
        }
        init = Object.assign({}, init, { headers });
      }
    }
    return _nativeFetch(input, init).then((res) => {
      // If a backend call came back 401/403, auto-open the login modal
      // so the user can authenticate without leaving the page.
      if (authRelevant && (res.status === 401 || res.status === 403)) {
        _maybeShowLoginModal();
      }
      return res;
    });
  };

  /** Explicit drop-in replacement — kept for pages that want to bypass
   *  the monkey-patch or talk to a non-backend host. */
  async function apiFetch(url, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    const token = getToken();
    if (token && !headers['Authorization']) {
      headers['Authorization'] = 'Bearer ' + token;
    }
    if (opts.body && !headers['Content-Type'] && typeof opts.body === 'string') {
      headers['Content-Type'] = 'application/json';
    }
    return _nativeFetch(url, Object.assign({}, opts, { headers }));
  }

  /** Login helper — stores the JWT under a canonical key. */
  async function login(username, password) {
    const res = await fetch(API_BASE + '/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      throw new Error('Login failed: HTTP ' + res.status);
    }
    const data = await res.json();
    const token = data.token || data.access_token;
    if (!token) throw new Error('Login response missing token');
    setToken(token);
    return token;
  }

  function logout() { setToken(''); }
  function isLoggedIn() { return !!getToken(); }

  // ───────────────────────── wallet helpers ───────────────────────────────

  function getCachedAccount() {
    try { return localStorage.getItem(LS_ACCOUNT) || ''; } catch (_) { return ''; }
  }
  function setCachedAccount(addr) {
    try {
      if (addr) localStorage.setItem(LS_ACCOUNT, addr);
      else      localStorage.removeItem(LS_ACCOUNT);
    } catch (_) {}
  }
  function setCachedChain(chainId) {
    try {
      if (chainId) localStorage.setItem(LS_CHAIN, chainId);
      else         localStorage.removeItem(LS_CHAIN);
    } catch (_) {}
  }

  /** Ensure MetaMask is pointed at the local dev chain; prompt to switch
   *  or add it if not. Throws on failure. Returns the chainId hex. */
  async function ensureChain() {
    if (typeof window.ethereum === 'undefined') {
      throw new Error('MetaMask not detected');
    }
    const current = await window.ethereum.request({ method: 'eth_chainId' });
    if (current === LOCAL_CHAIN_ID_HEX) return current;
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: LOCAL_CHAIN_ID_HEX }],
      });
      return LOCAL_CHAIN_ID_HEX;
    } catch (switchErr) {
      // 4902 = chain not added to MetaMask — add it.
      if (switchErr && (switchErr.code === 4902 || (switchErr.data && switchErr.data.originalError && switchErr.data.originalError.code === 4902))) {
        await window.ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [{
            chainId: LOCAL_CHAIN_ID_HEX,
            chainName: LOCAL_CHAIN_NAME,
            nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
            rpcUrls: [LOCAL_RPC_URL],
            blockExplorerUrls: [],
          }],
        });
        return LOCAL_CHAIN_ID_HEX;
      }
      throw switchErr;
    }
  }

  /** Connect (opens MetaMask popup), switch to local chain, cache state. */
  async function connectWallet() {
    if (typeof window.ethereum === 'undefined') {
      throw new Error('MetaMask not detected. Install MetaMask to continue.');
    }
    const accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
    if (!accounts || accounts.length === 0) throw new Error('No account returned');
    await ensureChain();
    const chainId = await window.ethereum.request({ method: 'eth_chainId' });
    setCachedAccount(accounts[0]);
    setCachedChain(chainId);
    _notifyUI(accounts[0], chainId);
    // Auto-fund wallet with test ETH on local dev chain
    _autoFundWallet(accounts[0]);
    return { account: accounts[0], chainId };
  }

  /** Request test ETH from the backend faucet (local dev only). */
  function _autoFundWallet(address) {
    const api = window.SMART_PUC_API || 'http://127.0.0.1:5000';
    fetch(api + '/api/faucet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: address }),
    }).catch(function() { /* faucet is optional */ });
  }

  /** Silent restore on page load — uses already-authorised accounts from
   *  MetaMask's permission list without popping the UI. Returns null if
   *  the wallet isn't available or the user has never connected here. */
  async function restoreWallet() {
    if (typeof window.ethereum === 'undefined') return null;
    try {
      const accounts = await window.ethereum.request({ method: 'eth_accounts' });
      if (!accounts || accounts.length === 0) {
        setCachedAccount('');
        _notifyUI('', '');
        return null;
      }
      const chainId = await window.ethereum.request({ method: 'eth_chainId' });
      setCachedAccount(accounts[0]);
      setCachedChain(chainId);
      _notifyUI(accounts[0], chainId);
      _autoFundWallet(accounts[0]);
      return { account: accounts[0], chainId };
    } catch (err) {
      console.warn('restoreWallet:', err);
      return null;
    }
  }

  /** Update all on-page wallet indicators (buttons, address chips, dots). */
  function _notifyUI(account, chainId) {
    const onLocal = chainId === LOCAL_CHAIN_ID_HEX;
    const short = account ? (account.slice(0, 6) + '...' + account.slice(-4)) : '';
    document.querySelectorAll('[data-wallet-address], #walletAddress').forEach(el => {
      el.textContent = short || 'Not connected';
    });
    document.querySelectorAll('[data-wallet-dot], #walletDot').forEach(el => {
      el.style.background = account
        ? (onLocal ? '#4CAF50' : '#FF9800')
        : '#8892b0';
      el.title = account
        ? (onLocal ? 'Connected to ' + LOCAL_CHAIN_NAME : 'Wrong network — click to switch')
        : 'Disconnected';
    });
    document.querySelectorAll('[data-wallet-button], #connectWalletBtn').forEach(btn => {
      if (account) {
        btn.textContent = onLocal ? ('Connected: ' + short) : 'Switch Network';
        btn.disabled = false;
      } else {
        btn.textContent = 'Connect Wallet';
        btn.disabled = false;
      }
    });
    // Let page-local scripts react (e.g. refresh a balance display).
    window.dispatchEvent(new CustomEvent('smartpuc:wallet', {
      detail: { account, chainId, onLocalChain: onLocal }
    }));
  }

  // ───────────────────────── Login modal ─────────────────────────────────

  let _loginModalOpen = false;
  let _loginModalEl = null;

  function showLoginModal(reason) {
    if (_loginModalOpen) return;
    _loginModalOpen = true;

    if (!_loginModalEl) {
      const wrap = document.createElement('div');
      wrap.id = 'smartpuc-login-modal';
      wrap.style.cssText = [
        'position:fixed', 'inset:0', 'z-index:99999',
        'background:rgba(0,0,0,0.75)',
        'display:flex', 'align-items:center', 'justify-content:center',
        'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
      ].join(';');
      wrap.innerHTML = `
        <div style="background:#1a1d29;color:#e0e6ed;padding:2rem;border-radius:12px;min-width:320px;max-width:420px;box-shadow:0 20px 60px rgba(0,0,0,0.5);">
          <h2 style="margin:0 0 0.25rem 0;font-size:1.25rem;">Authority Login</h2>
          <p id="smartpuc-login-reason" style="margin:0 0 1rem 0;font-size:0.85rem;color:#8892b0;">
            This action requires an authenticated operator session.
          </p>
          <label style="display:block;font-size:0.8rem;color:#8892b0;margin-top:0.6rem;">Username</label>
          <input id="smartpuc-login-user" type="text" autocomplete="username" style="width:100%;padding:0.6rem 0.8rem;margin-top:0.25rem;border-radius:6px;border:1px solid #2a2e3f;background:#0f1119;color:#e0e6ed;box-sizing:border-box;"/>
          <label style="display:block;font-size:0.8rem;color:#8892b0;margin-top:0.6rem;">Password</label>
          <input id="smartpuc-login-pw" type="password" autocomplete="current-password" style="width:100%;padding:0.6rem 0.8rem;margin-top:0.25rem;border-radius:6px;border:1px solid #2a2e3f;background:#0f1119;color:#e0e6ed;box-sizing:border-box;"/>
          <div id="smartpuc-login-err" style="color:#F44336;font-size:0.8rem;margin-top:0.5rem;min-height:1em;"></div>
          <div style="display:flex;gap:0.5rem;margin-top:1rem;">
            <button id="smartpuc-login-cancel" style="flex:1;padding:0.6rem;border-radius:6px;border:1px solid #2a2e3f;background:transparent;color:#e0e6ed;cursor:pointer;">Cancel</button>
            <button id="smartpuc-login-submit" style="flex:2;padding:0.6rem;border-radius:6px;border:0;background:#007AFF;color:white;font-weight:600;cursor:pointer;">Sign In</button>
          </div>
          <p style="margin:1rem 0 0 0;font-size:0.7rem;color:#5a6378;">
            Credentials live in your server's <code style="color:#8892b0;">.env</code>
            (<code style="color:#8892b0;">AUTH_USERNAME</code> / <code style="color:#8892b0;">AUTH_PASSWORD</code>).
          </p>
        </div>
      `;
      document.body.appendChild(wrap);
      _loginModalEl = wrap;

      const close = () => {
        _loginModalOpen = false;
        wrap.style.display = 'none';
      };
      wrap.querySelector('#smartpuc-login-cancel').addEventListener('click', close);
      wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });

      const doSubmit = async () => {
        const u = wrap.querySelector('#smartpuc-login-user').value.trim();
        const p = wrap.querySelector('#smartpuc-login-pw').value;
        const err = wrap.querySelector('#smartpuc-login-err');
        err.textContent = '';
        try {
          await login(u, p);
          close();
          // Notify the page so it can refresh any views that were 401'd.
          window.dispatchEvent(new CustomEvent('smartpuc:login', { detail: { username: u } }));
        } catch (e2) {
          err.textContent = e2.message || 'Login failed';
        }
      };
      wrap.querySelector('#smartpuc-login-submit').addEventListener('click', doSubmit);
      wrap.querySelector('#smartpuc-login-pw').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doSubmit();
      });
    }

    if (reason) {
      _loginModalEl.querySelector('#smartpuc-login-reason').textContent = reason;
    }
    _loginModalEl.style.display = 'flex';
    setTimeout(() => {
      const u = _loginModalEl.querySelector('#smartpuc-login-user');
      if (u) u.focus();
    }, 50);
  }

  function _maybeShowLoginModal() {
    // Don't spam the modal if it's already open.
    if (document.readyState === 'loading') return;
    showLoginModal('Your session has expired or this action requires login.');
  }

  // ───────────────────────── Event wiring ─────────────────────────────────

  if (typeof window.ethereum !== 'undefined') {
    window.ethereum.on && window.ethereum.on('accountsChanged', (accs) => {
      const a = (accs && accs[0]) || '';
      setCachedAccount(a);
      _notifyUI(a, localStorage.getItem(LS_CHAIN) || '');
    });
    window.ethereum.on && window.ethereum.on('chainChanged', (cid) => {
      setCachedChain(cid);
      _notifyUI(getCachedAccount(), cid);
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    // Re-wire any "Connect Wallet" button that still points at an old
    // per-page connectWallet() implementation. We delegate to the
    // shared one when the button has no explicit onclick handler.
    document.querySelectorAll('[data-wallet-button], #connectWalletBtn').forEach(btn => {
      if (!btn.dataset.smartpucWired) {
        btn.dataset.smartpucWired = '1';
        btn.addEventListener('click', async (e) => {
          try { await connectWallet(); }
          catch (err) { console.error(err); alert(err.message || String(err)); }
        });
      }
    });
    // Auto-restore on load (no popup) so users stay "connected" across
    // navigation between pages.
    restoreWallet().catch((e) => console.warn('restoreWallet failed:', e));
  });

  // ───────────────────────── Public API ───────────────────────────────────

  window.SmartPUC = {
    API_BASE,
    LOCAL_CHAIN_ID_HEX,
    LOCAL_CHAIN_ID_DEC,
    LOCAL_CHAIN_NAME,
    LOCAL_RPC_URL,
    apiFetch,
    login,
    logout,
    isLoggedIn,
    getToken,
    setToken,
    connectWallet,
    restoreWallet,
    ensureChain,
    getCachedAccount,
    showLoginModal,
  };
})();
