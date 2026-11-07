// Production panel: switch the chart's series and build the CSV from the page's own data.
(() => {
  const panel = document.querySelector('section.production');
  if (!panel) return;
  const chart = panel.querySelector('svg.prodchart');
  for (const button of panel.querySelectorAll('.toggle button')) {
    button.addEventListener('click', () => {
      for (const other of panel.querySelectorAll('.toggle button')) other.classList.toggle('on', other === button);
      if (chart) chart.setAttribute('class', 'prodchart show-' + button.dataset.series);
    });
  }
  // One row per month from the first reported month; an unknown month is an empty cell, never 0.
  const buildCsv = (data) => {
    const keys = ['oil', 'water', 'gas', 'gasSold', 'flared', 'days'];
    const lines = ['month,oil_bbl,water_bbl,gas_mcf,gas_sold_mcf,flared_mcf,days'];
    let [year, month] = data.start.split('-').map(Number);
    for (let i = 0; i < data.oil.length; i++) {
      const cells = keys.map((k) => (data[k] && data[k][i] != null ? data[k][i] : ''));
      lines.push(`${year}-${String(month).padStart(2, '0')},${cells.join(',')}`);
      month += 1;
      if (month === 13) { month = 1; year += 1; }
    }
    lines.push('# Source: NDIC Monthly Production Reports (https://www.dmr.nd.gov/oilgas/mprindex.asp), values as first reported; blank = unknown');
    return lines.join('\n') + '\n';
  };
  window.bakkenProductionCsv = buildCsv;
  const csv = panel.querySelector('button.csv');
  const raw = document.getElementById('production-data');
  if (!csv || !raw) return;
  csv.addEventListener('click', () => {
    let data;
    try { data = JSON.parse(raw.textContent); } catch { return; }
    const blob = new Blob([buildCsv(data)], { type: 'text/csv' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = csv.dataset.name || 'production.csv';
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 1000);
  });
})();
