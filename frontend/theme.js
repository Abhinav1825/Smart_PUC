/* Smart PUC — Shared Theme Toggle
 * Manages dark/light theme across ALL pages via localStorage.
 * Exposes window.toggleTheme() (called from the navbar button).
 * Runs as early as possible to avoid a dark→light flash on load.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'smartpuc_theme';
    var MOON = '🌙';   // 🌙
    var SUN  = '☀️';  // ☀️

    function applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
    }

    function updateIcon(theme) {
        var btn = document.getElementById('themeToggleBtn');
        if (btn) btn.textContent = theme === 'light' ? SUN : MOON;
    }

    function getSaved() {
        try { return localStorage.getItem(STORAGE_KEY) || 'dark'; }
        catch (e) { return 'dark'; }
    }

    function setSaved(theme) {
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}
    }

    // Apply saved theme IMMEDIATELY (pre-DOMContentLoaded) to prevent flash
    applyTheme(getSaved());

    // Listen for cross-tab theme changes so all open pages stay in sync
    window.addEventListener('storage', function (e) {
        if (e.key === STORAGE_KEY && e.newValue) {
            applyTheme(e.newValue);
            updateIcon(e.newValue);
        }
    });

    // Global function — invoked by the theme toggle button
    window.toggleTheme = function () {
        var current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
        var next = current === 'light' ? 'dark' : 'light';
        applyTheme(next);
        setSaved(next);
        updateIcon(next);
    };

    // When DOM is ready, set the correct icon on the button
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { updateIcon(getSaved()); });
    } else {
        updateIcon(getSaved());
    }
})();
