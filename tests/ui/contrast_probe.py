"""Measures the contrast of every visible text in the page against the background it sits on (WCAG 2.x)."""

PROBE = r"""
() => {
  const parse = value => {
    let m = value.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = value.match(/color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return null;
  };
  const over = (top, under) => {
    const a = top[3];
    return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1];
  };
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const background = el => {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== 'none') return null; // gradients and images are not measured
      const color = parse(style.backgroundColor);
      if (color && color[3] > 0) { layers.push(color); if (color[3] >= 1) break; }
    }
    let base = [255, 255, 255, 1];
    const root = parse(getComputedStyle(document.documentElement).backgroundColor);
    const body = parse(getComputedStyle(document.body).backgroundColor);
    if (root && root[3] >= 1) base = root;
    if (body && body[3] >= 1 && !layers.some(l => l[3] >= 1)) base = body;
    return layers.reduceRight((under, layer) => over(layer, under), layers.some(l => l[3] >= 1) ? layers.find(l => l[3] >= 1) : base);
  };
  const opacityOf = el => { let o = 1; for (let n = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim();
    const el = node.parentElement;
    if (!text || seen.has(el) || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) continue;
    seen.add(el);
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || box.width === 0 || box.height === 0) continue;
    if (el.closest('[hidden], [inert], dialog:not([open])') || el.closest('.sr-only')) continue;
    if (el.closest('.vis-network, .vis-timeline, canvas')) continue;
    const fg = parse(style.color);
    const bg = background(el);
    if (!fg || !bg) continue;
    const opacity = opacityOf(el);
    const shown = over([fg[0], fg[1], fg[2], fg[3] * opacity], bg);
    const size = parseFloat(style.fontSize), bold = Number(style.fontWeight) >= 700;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3 : 4.5;
    const got = ratio(shown, bg);
    if (got < need) out.push({ text: text.slice(0, 40), tag: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ').slice(0, 3).join('.') : ''),
                               ratio: Math.round(got * 100) / 100, need, fg: style.color, size });
  }
  return out;
};
"""
