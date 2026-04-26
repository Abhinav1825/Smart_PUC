/* Smart PUC — Shared Navbar Functions
 * Provides global handlers for the unified navbar on EVERY page:
 *   - toggleNotifDropdown()
 *   - toggleLangDropdown()
 *   - switchLanguage(code)
 * Auto-closes dropdowns on outside click and on ESC.
 */
(function () {
    'use strict';

    var LANG_LABELS = { en: 'EN', hi: 'HI', mr: 'MR' };
    var GLOBE = '🌐'; // 🌐

    function $(id) { return document.getElementById(id); }

    function closeAll(exceptId) {
        var notif = $('notifDropdown');
        var lang  = $('langDropdown');
        if (notif && notif.id !== exceptId) notif.classList.remove('open');
        if (lang  && lang.id  !== exceptId) lang.classList.remove('active');
    }

    window.toggleNotifDropdown = function (e) {
        if (e) e.stopPropagation();
        var d = $('notifDropdown');
        if (!d) return;
        var isOpen = d.classList.contains('open');
        closeAll('notifDropdown');
        d.classList.toggle('open', !isOpen);
    };

    window.toggleLangDropdown = function (e) {
        if (e) e.stopPropagation();
        var d = $('langDropdown');
        if (!d) return;
        var isOpen = d.classList.contains('active');
        closeAll('langDropdown');
        d.classList.toggle('active', !isOpen);
    };

    // Language switching: store in localStorage, reload data-i18n content if i18n.js is loaded,
    // otherwise just update the label.
    window.switchLanguage = function (code) {
        try { localStorage.setItem('smartpuc_lang', code); } catch (e) {}
        // If i18n.js is loaded, it exposes window.applyI18n(code) or window.setLang(code)
        if (typeof window.applyI18n === 'function') {
            window.applyI18n(code);
        } else if (typeof window.setLang === 'function') {
            window.setLang(code);
        }
        // Update visual label + active state
        var btn = document.querySelector('.lang-selector-btn');
        if (btn) btn.innerHTML = GLOBE + ' ' + (LANG_LABELS[code] || 'EN');
        document.querySelectorAll('.lang-option').forEach(function (opt) {
            opt.classList.remove('active');
            // Match option by its onclick argument
            var m = (opt.getAttribute('onclick') || '').match(/'([^']+)'/);
            if (m && m[1] === code) opt.classList.add('active');
        });
        // Close dropdown
        var dd = $('langDropdown');
        if (dd) dd.classList.remove('active');
    };

    // Outside-click + ESC to close both dropdowns
    document.addEventListener('click', function (e) {
        var inNotif = e.target.closest && e.target.closest('#notifBellContainer');
        var inLang  = e.target.closest && e.target.closest('#langSelector');
        if (!inNotif) {
            var n = $('notifDropdown');
            if (n) n.classList.remove('open');
        }
        if (!inLang) {
            var l = $('langDropdown');
            if (l) l.classList.remove('active');
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeAll();
            // Also close mobile nav
            var nav = $('navbarActions');
            var h = $('hamburgerBtn');
            if (nav) nav.classList.remove('active');
            if (h) h.setAttribute('aria-expanded', 'false');
        }
    });

    // Initialize lang label on page load
    document.addEventListener('DOMContentLoaded', function () {
        var saved = null;
        try { saved = localStorage.getItem('smartpuc_lang'); } catch (e) {}
        if (saved && LANG_LABELS[saved]) {
            var btn = document.querySelector('.lang-selector-btn');
            if (btn) btn.innerHTML = GLOBE + ' ' + LANG_LABELS[saved];
            document.querySelectorAll('.lang-option').forEach(function (opt) {
                opt.classList.remove('active');
                var m = (opt.getAttribute('onclick') || '').match(/'([^']+)'/);
                if (m && m[1] === saved) opt.classList.add('active');
            });
            if (typeof window.applyI18n === 'function') window.applyI18n(saved);
        }
    });
})();
