(() => {
  const search = document.getElementById('candidate-search');
  const state = document.getElementById('candidate-state');
  const asset = document.getElementById('candidate-asset');
  const counter = document.getElementById('visible-count');
  const rows = [...document.querySelectorAll('#candidate-rows tr')];
  const update = () => {
    const query = (search?.value || '').trim().toLowerCase();
    const selectedState = state?.value || '';
    const selectedAsset = asset?.value || '';
    let visible = 0;
    rows.forEach((row) => {
      const show = (!query || row.dataset.search.includes(query)) &&
        (!selectedState || row.dataset.state === selectedState) &&
        (!selectedAsset || row.dataset.asset === selectedAsset);
      row.hidden = !show;
      visible += show ? 1 : 0;
    });
    if (counter) counter.textContent = `${visible} ROWS`;
  };
  [search, state, asset].forEach((control) => {
    control?.addEventListener(control === search ? 'input' : 'change', update);
  });
  update();
})();