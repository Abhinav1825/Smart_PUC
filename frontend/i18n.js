/**
 * Smart PUC — Shared i18n (internationalisation) helper
 * ======================================================
 * Include this script AFTER wallet.js / app.js on every page except
 * index.html (which has its own extended i18n block).
 *
 * Reads the saved language from localStorage('smartpuc_lang') and
 * applies translations to any element carrying a `data-i18n` attribute.
 */
(function () {
    'use strict';

    // ── Shared translation strings (nav links + common labels) ──
    var I18N = {
        en: {
            nav_vehicle: 'Vehicle',
            nav_authority: 'Authority',
            nav_verify: 'Verify',
            nav_analytics: 'Analytics',
            nav_fleet: 'Fleet',
            nav_rto: 'RTO',
            nav_marketplace: 'Marketplace',
            nav_cpcb: 'CPCB',
            nav_compare: 'Compare',
            btn_connect_wallet: 'Connect Wallet',
            status_pass: 'PASS',
            status_fail: 'FAIL'
        },
        hi: {
            nav_vehicle: '\u0935\u093E\u0939\u0928',
            nav_authority: '\u092A\u094D\u0930\u093E\u0927\u093F\u0915\u0930\u0923',
            nav_verify: '\u0938\u0924\u094D\u092F\u093E\u092A\u0928',
            nav_analytics: '\u0935\u093F\u0936\u094D\u0932\u0947\u0937\u0923',
            nav_fleet: '\u092C\u0947\u0921\u093C\u093E',
            nav_rto: 'RTO',
            nav_marketplace: '\u092C\u093E\u091C\u093E\u0930',
            nav_cpcb: 'CPCB',
            nav_compare: '\u0924\u0941\u0932\u0928\u093E',
            btn_connect_wallet: '\u0935\u0949\u0932\u0947\u091F \u091C\u094B\u0921\u093C\u0947\u0902',
            status_pass: '\u0938\u092B\u0932',
            status_fail: '\u0935\u093F\u092B\u0932'
        },
        mr: {
            nav_vehicle: '\u0935\u093E\u0939\u0928',
            nav_authority: '\u092A\u094D\u0930\u093E\u0927\u093F\u0915\u0930\u0923',
            nav_verify: '\u092A\u0921\u0924\u093E\u0933\u0923\u0940',
            nav_analytics: '\u0935\u093F\u0936\u094D\u0932\u0947\u0937\u0923',
            nav_fleet: '\u0924\u093E\u092B\u093E',
            nav_rto: 'RTO',
            nav_marketplace: '\u092C\u093E\u091C\u093E\u0930',
            nav_cpcb: 'CPCB',
            nav_compare: '\u0924\u0941\u0932\u0928\u093E',
            btn_connect_wallet: '\u0935\u0949\u0932\u0947\u091F \u091C\u094B\u0921\u093E',
            status_pass: '\u0909\u0924\u094D\u0924\u0940\u0930\u094D\u0923',
            status_fail: '\u0905\u0928\u0941\u0924\u094D\u0924\u0940\u0930\u094D\u0923'
        }
    };

    function applyLanguage(lang) {
        var strings = I18N[lang] || I18N.en;
        document.querySelectorAll('[data-i18n]').forEach(function (el) {
            var key = el.getAttribute('data-i18n');
            if (strings[key] != null) {
                el.textContent = strings[key];
            }
        });
        // Update lang button label
        var langLabels = { en: 'EN', hi: '\u0939\u093F', mr: '\u092E\u0930' };
        var btn = document.querySelector('.lang-selector-btn');
        if (btn) btn.innerHTML = '&#127760; ' + (langLabels[lang] || 'EN');
        // Update active state
        document.querySelectorAll('.lang-option').forEach(function (opt) {
            opt.classList.remove('active');
        });
        var opts = document.querySelectorAll('.lang-option');
        var idx = { en: 0, hi: 1, mr: 2 }[lang] || 0;
        if (opts[idx]) opts[idx].classList.add('active');
    }

    // ── Global API used by onclick handlers in the HTML ──
    window.switchLanguage = function (lang) {
        try { localStorage.setItem('smartpuc_lang', lang); } catch (_) {}
        applyLanguage(lang);
        // Close dropdown
        var dd = document.getElementById('langDropdown');
        if (dd) dd.classList.remove('active');
    };

    window.toggleLangDropdown = function () {
        var dd = document.getElementById('langDropdown');
        if (dd) dd.classList.toggle('active');
    };

    // Close dropdown on outside click
    document.addEventListener('click', function (e) {
        var sel = document.getElementById('langSelector');
        var dd = document.getElementById('langDropdown');
        if (sel && dd && !sel.contains(e.target)) {
            dd.classList.remove('active');
        }
    });

    // ── Restore saved language on page load ──
    var saved = 'en';
    try { saved = localStorage.getItem('smartpuc_lang') || 'en'; } catch (_) {}
    if (saved !== 'en') {
        // Apply after a micro-tick so the DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () {
                applyLanguage(saved);
            });
        } else {
            applyLanguage(saved);
        }
    }
})();
