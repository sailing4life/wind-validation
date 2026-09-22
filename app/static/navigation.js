/* Bookmarkable workspace pages, independent of forecast loading. */
(() => {
  const tabs = [...document.querySelectorAll('.main-tab-nav [data-tab]')];
  const main = document.querySelector('.app-main');
  const positions = new Map();
  let current = null;

  function showWorkspace() {
    const requested = location.hash.slice(1).replace(/^tab-/, '');
    const name = tabs.some(tab => tab.dataset.tab === requested) ? requested : 'forecast';
    if (current) positions.set(current, main.scrollTop);
    tabs.forEach(tab => {
      const active = tab.dataset.tab === name;
      tab.classList.toggle('active', active);
      if (active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
      document.getElementById(`tab-${tab.dataset.tab}`).classList.toggle('active', active);
    });
    current = name;
    document.body.dataset.workspace = name;
    document.title = `${tabs.find(tab => tab.dataset.tab === name).querySelector('span').textContent} · Wind Validation`;
    main.scrollTop = positions.get(name) || 0;
    document.getElementById('analysisOptions').open = name === 'validation';
    if (typeof updateSidebarAction === 'function') updateSidebarAction();
    if (name === 'briefing' && typeof renderBriefingTab === 'function') renderBriefingTab();
    if (name === 'validation' && typeof drawCharts === 'function') drawCharts();
    if (typeof resizeForecastCharts === 'function') resizeForecastCharts();
  }

  // Intercept only plain same-page navigation; modified clicks remain real links.
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const name = link.hash.slice(1);
      if (!tabs.some(tab => tab.dataset.tab === name)) return;
      event.preventDefault();
      if (location.hash !== link.hash) history.pushState(null, '', link.hash);
      showWorkspace();
    });
  });
  window.addEventListener('hashchange', showWorkspace);
  showWorkspace();
})();
