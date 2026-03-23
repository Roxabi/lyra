const loaded = new Set();

async function loadPanel(id) {
  if (loaded.has(id)) return;
  const res = await fetch(`tabs/v8/tab-${id}.html`);
  const html = await res.text();
  document.getElementById(id).innerHTML = html;
  loaded.add(id);
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', async () => {
    const id = tab.dataset.tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tc').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    await loadPanel(id);
    document.getElementById(id).classList.add('active');
  });
});

// Load initial active tab (script is at end of body — DOM is ready)
loadPanel('qs');
