/* lyra-persistence-arch.js — tab switching + lazy panel loading */
(function () {
  'use strict';

  const THEME_KEY = 'lyra-persist-arch-theme';
  const loaded    = new Set();

  /* ── Theme ── */
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.querySelector('.theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '◑ Light' : '◑ Dark';
    localStorage.setItem(THEME_KEY, theme);
  }

  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY) || 'dark';
    applyTheme(saved);
  }

  /* ── Panel loading ── */
  async function loadPanel(id) {
    if (loaded.has(id)) return;
    const panel = document.getElementById('panel-' + id);
    if (!panel) return;
    const src = panel.dataset.src;
    if (!src) { loaded.add(id); return; }

    try {
      const res  = await fetch(src);
      const html = await res.text();
      panel.innerHTML = html;
      loaded.add(id);
      // run any inline scripts inside the fragment
      panel.querySelectorAll('script').forEach(old => {
        const s = document.createElement('script');
        s.textContent = old.textContent;
        old.parentNode.replaceChild(s, old);
      });
    } catch (e) {
      panel.innerHTML = `<p class="loading" style="color:var(--red)">⚠ Failed to load: ${src}</p>`;
    }
  }

  /* ── Tab switching ── */
  function activateTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === tabId));
    document.querySelectorAll('.panel').forEach(p =>
      p.classList.toggle('active', p.id === 'panel-' + tabId));
    loadPanel(tabId);
    history.replaceState(null, '', '#' + tabId);
  }

  /* ── Init ── */
  document.addEventListener('DOMContentLoaded', () => {
    initTheme();

    // Tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => activateTab(btn.dataset.tab));
    });

    // Theme toggle
    document.querySelector('.theme-btn')?.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });

    // Initial tab from hash or default
    const hash   = location.hash.slice(1);
    const tabs   = [...document.querySelectorAll('.tab-btn')].map(b => b.dataset.tab);
    const initId = tabs.includes(hash) ? hash : tabs[0];
    activateTab(initId);
  });
})();
