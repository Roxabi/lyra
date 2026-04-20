// Hover-chain highlight. Any element with a data-iss attribute is a target.
// Grid uses .issue-card; graph uses .gg-node + .gg-ilabel. All are unified by
// the same class names: hl-self / hl-upstream / hl-downstream.
(function () {
  const body = document.body;
  const targets = Array.from(document.querySelectorAll('[data-iss]'));
  if (targets.length === 0) return;

  // Bucket every element (card, node, label) by its issue key so highlights
  // apply to all instances simultaneously.
  const byKey = new Map();
  targets.forEach(el => {
    const k = el.dataset.iss;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(el);
  });

  // Build blocker + unblock adjacency once per key.
  const blockers = new Map();
  const unblocks = new Map();
  targets.forEach(el => {
    const k = el.dataset.iss;
    if (blockers.has(k)) return;
    blockers.set(k, (el.dataset.blockedby || '').split(',').filter(Boolean));
    unblocks.set(k, (el.dataset.blocking || '').split(',').filter(Boolean));
  });

  const edges = Array.from(document.querySelectorAll('.gg-edge[data-src]'));

  function traverse(start, adj) {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const k = stack.pop();
      for (const n of adj.get(k) || []) {
        if (!seen.has(n)) { seen.add(n); stack.push(n); }
      }
    }
    return seen;
  }

  function highlightKey(k) {
    if (!byKey.has(k)) return false;
    const up = traverse(k, blockers);
    const down = traverse(k, unblocks);
    body.classList.add('hl-active');
    (byKey.get(k) || []).forEach(n => n.classList.add('hl-self'));
    up.forEach(key => (byKey.get(key) || []).forEach(n => n.classList.add('hl-upstream')));
    down.forEach(key => (byKey.get(key) || []).forEach(n => n.classList.add('hl-downstream')));
    const chain = new Set([k, ...up, ...down]);
    edges.forEach(e => {
      if (chain.has(e.dataset.src) && chain.has(e.dataset.tgt)) {
        e.classList.add('hl-edge');
      }
    });
    return true;
  }

  function clearHighlight() {
    body.classList.remove('hl-active');
    document.querySelectorAll('.hl-self, .hl-upstream, .hl-downstream')
      .forEach(el => el.classList.remove('hl-self', 'hl-upstream', 'hl-downstream'));
    edges.forEach(e => e.classList.remove('hl-edge'));
  }

  // Persistent search selection — re-applied after any hover clears.
  let pinnedKey = null;
  function restorePinned() {
    clearHighlight();
    if (pinnedKey) highlightKey(pinnedKey);
  }

  targets.forEach(el => {
    el.addEventListener('mouseenter', () => {
      clearHighlight();
      highlightKey(el.dataset.iss);
    });
    el.addEventListener('mouseleave', restorePinned);
  });

  // Epic-header hover — highlight only that epic's own cards (grid view only)
  document.querySelectorAll('.epic-header').forEach(h => {
    h.addEventListener('mouseenter', () => {
      clearHighlight();
      body.classList.add('hl-active');
      const group = h.parentElement;
      group.querySelectorAll('.issue-card').forEach(c => c.classList.add('hl-self'));
    });
    h.addEventListener('mouseleave', restorePinned);
  });

  // ── Issue-number search — exact match, highlight same as hover ─────
  // Map: issue number (int) → array of full keys (`repo#num`). Typing a bare
  // number matches the num part exactly, not as a prefix (69 ≠ 690).
  const byNum = new Map();
  for (const k of byKey.keys()) {
    const m = /#(\d+)$/.exec(k);
    if (!m) continue;
    const num = m[1];
    if (!byNum.has(num)) byNum.set(num, []);
    byNum.get(num).push(k);
  }

  const input = document.getElementById('issue-search');
  if (input) {
    const wrap = input.closest('.search') || input;
    function applySearch() {
      const raw = input.value.trim().replace(/^#/, '');
      if (!raw) {
        pinnedKey = null;
        wrap.classList.remove('no-match');
        clearHighlight();
        return;
      }
      const keys = byNum.get(raw);
      if (!keys || keys.length === 0) {
        pinnedKey = null;
        wrap.classList.add('no-match');
        clearHighlight();
        return;
      }
      wrap.classList.remove('no-match');
      pinnedKey = keys[0];
      clearHighlight();
      highlightKey(pinnedKey);
    }
    input.addEventListener('input', applySearch);
    // Esc clears search — works from input focus or anywhere on the page.
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      if (!input.value && !pinnedKey) return;
      input.value = '';
      applySearch();
      if (document.activeElement === input) input.blur();
    });
  }
})();
