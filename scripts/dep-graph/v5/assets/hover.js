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

  function highlight(el) {
    const k = el.dataset.iss;
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
  }

  function clearHighlight() {
    body.classList.remove('hl-active');
    document.querySelectorAll('.hl-self, .hl-upstream, .hl-downstream')
      .forEach(el => el.classList.remove('hl-self', 'hl-upstream', 'hl-downstream'));
    edges.forEach(e => e.classList.remove('hl-edge'));
  }

  targets.forEach(el => {
    el.addEventListener('mouseenter', () => highlight(el));
    el.addEventListener('mouseleave', clearHighlight);
  });

  // Epic-header hover — highlight only that epic's own cards (grid view only)
  document.querySelectorAll('.epic-header').forEach(h => {
    h.addEventListener('mouseenter', () => {
      body.classList.add('hl-active');
      const group = h.parentElement;
      group.querySelectorAll('.issue-card').forEach(c => c.classList.add('hl-self'));
    });
    h.addEventListener('mouseleave', clearHighlight);
  });
})();
