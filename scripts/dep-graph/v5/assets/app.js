// View toggle + filter toolbar. Persists active view in localStorage + URL hash.
(function () {
  const body = document.body;
  const STORAGE_KEY = 'depgraph-v5-view';
  const VALID = new Set(['grid', 'graph']);

  function readHash() {
    const m = /#view=(grid|graph)/.exec(window.location.hash || '');
    return m ? m[1] : null;
  }

  function readStorage() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return VALID.has(v) ? v : null;
    } catch { return null; }
  }

  function writeStorage(v) {
    try { localStorage.setItem(STORAGE_KEY, v); } catch { /* ignore */ }
  }

  function getInitialView() {
    return readHash() || readStorage() || 'graph';
  }

  function activateView(name) {
    if (!VALID.has(name)) return;
    document.querySelectorAll('.view').forEach(v => {
      v.classList.toggle('view-active', v.dataset.view === name);
    });
    document.querySelectorAll('.mode-toggle button').forEach(b => {
      b.classList.toggle('active', b.dataset.view === name);
      b.setAttribute('aria-pressed', b.dataset.view === name ? 'true' : 'false');
    });
    document.body.classList.toggle('view-grid-active', name === 'grid');
    document.body.classList.toggle('view-graph-active', name === 'graph');
    writeStorage(name);
    if (readHash() !== name) {
      history.replaceState(null, '', '#view=' + name);
    }
  }

  // Wire buttons
  document.querySelectorAll('.mode-toggle button').forEach(b => {
    b.addEventListener('click', () => activateView(b.dataset.view));
  });

  // Keyboard nav on toggle — arrow keys switch view
  const toggle = document.querySelector('.mode-toggle');
  if (toggle) {
    toggle.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        const cur = document.querySelector('.mode-toggle button.active');
        const next = cur?.dataset.view === 'graph' ? 'grid' : 'graph';
        activateView(next);
      }
    });
  }

  // React to back/forward hash changes
  window.addEventListener('hashchange', () => {
    const v = readHash();
    if (v) activateView(v);
  });

  // Filter toolbar
  function bindFilter(id, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('change', e => body.classList.toggle(cls, e.target.checked));
  }
  bindFilter('toggle-epic', 'group-epic');
  bindFilter('toggle-closed', 'hide-closed');
  bindFilter('toggle-ready', 'only-ready');

  // Initial activation (view is already baked into static HTML — this is
  // just to sync storage/hash with that baked choice, and to honor a hash override)
  const initial = getInitialView();
  activateView(initial);
})();
