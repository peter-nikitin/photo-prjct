(() => {
  const theme = getComputedStyle(document.documentElement);
  const resolved = (token) => theme.getPropertyValue(token).trim();
  const hex = (value) => {
    const channels = value.match(/^rgb\((\d+)\s+(\d+)\s+(\d+)\)$/);
    if (/^#[0-9a-f]{3}$/i.test(value)) {
      return `#${[...value.slice(1)].map((digit) => digit.repeat(2)).join('').toUpperCase()}`;
    }
    return channels
      ? `#${channels.slice(1).map((channel) => Number(channel).toString(16).padStart(2, '0')).join('').toUpperCase()}`
      : value.toUpperCase();
  };

  for (const item of document.querySelectorAll('[data-token]')) {
    const value = resolved(item.dataset.token);
    item.querySelector('[data-computed-value]').textContent = hex(value);
  }

  for (const item of document.querySelectorAll('[data-font-meta]')) {
    const sample = item.previousElementSibling;
    const style = getComputedStyle(sample);
    item.textContent = `${style.fontFamily} · ${style.fontSize} · ${style.fontWeight} · line-height ${style.lineHeight}`;
  }

  for (const item of document.querySelectorAll('[data-geometry]')) {
    const property = item.dataset.geometry;
    const style = property.startsWith('--') ? theme : getComputedStyle(item);
    item.querySelector('[data-computed-value]').textContent = style.getPropertyValue(property).trim();
  }
})();
