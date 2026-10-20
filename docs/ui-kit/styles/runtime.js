/* AskData UI Kit — preview runtime (charts, tabs, dialogs, toasts, pager, field panel).
   Reference behaviour only: re-implement in React; do not ship this file. */
(function () {
  var KIND = { bar: ['柱状图', 'M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-3'], hbar: ['条形图', 'M3 7h16M3 13h16M3 19h16'], stacked: ['堆叠柱状图', 'M4 20h16M6 20v-5h4v5M6 15v-4h4v4M14 20v-9h4v9M14 11V7h4v4'], line: ['折线图', 'M4 20V4M4 20h16M7 15l4-5 3 3 5-6'], pie: ['占比图', 'M12 4v8h8M12 4a8 8 0 1 0 8 8'], scatter: ['散点图', 'M4 20V4M4 20h16M8 15h.01M11 10h.01M15 14h.01M18 7h.01'] };
  function icon(d) { return '<svg class="ak-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>'; }
  function nice(max, min) { min = Math.min(0, min || 0); var span = (max - min) || 1, raw = span / 4, p = Math.pow(10, Math.floor(Math.log10(raw))); var st = [1, 2, 2.5, 5, 10].map(function (m) { return m * p; }).filter(function (v) { return v >= raw; })[0]; var lo = Math.floor(min / st) * st, hi = Math.ceil(max / st) * st, t = []; for (var v = lo; v <= hi + st / 2; v += st) t.push(+v.toPrecision(10)); return { lo: lo, hi: hi, ticks: t }; }
  function fmt(v, u) { var s = Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('zh-CN') : (+v.toFixed(1)).toString(); return u === '%' ? s + '%' : s; }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
  function tw(s) { var w = 0; s = String(s); for (var i = 0; i < s.length; i++) w += s.charCodeAt(i) > 255 ? 12 : 7; return w; }
  /* Geometry follows AskData's charts.tsx: slim bars (<=28px per series), value labels when they fit, full pie with a side legend. */
  function draw(m, kind, W) {
    var H = 240, T = 10, B = 28, out = '', S = m.series, n = m.labels.length;
    var all = []; S.forEach(function (s) { all = all.concat(s.values); });
    var unit = m.unit && m.unit !== '%' ? ' ' + m.unit : '';
    if (kind === 'pie') {
      var vals = S[0].values, tot = vals.reduce(function (a, b) { return a + b; }, 0), r = H / 2 - 14, cy = H / 2;
      var cx = Math.max(r + 8, Math.min(W / 2 - 100, W - r - 220)), a0 = -Math.PI / 2;
      vals.forEach(function (v, i) { var a1 = a0 + v / tot * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0;
        out += '<path class="slice s' + (i + 1) + '" d="M' + cx.toFixed(1) + ' ' + cy + 'L' + (cx + r * Math.cos(a0)).toFixed(1) + ' ' + (cy + r * Math.sin(a0)).toFixed(1) + 'A' + r + ' ' + r + ' 0 ' + big + ' 1 ' + (cx + r * Math.cos(a1)).toFixed(1) + ' ' + (cy + r * Math.sin(a1)).toFixed(1) + 'Z"><title>' + esc(m.labels[i]) + '：' + fmt(v, m.unit) + unit + '</title></path>'; a0 = a1; });
      var y0 = cy - vals.length * 26 / 2;
      vals.forEach(function (v, i) { var y = y0 + i * 26 + 13;
        out += '<rect class="s' + (i + 1) + '" x="' + (cx + r + 28).toFixed(1) + '" y="' + (y - 6) + '" width="12" height="12" rx="2" style="stroke:none"/><text class="axis" x="' + (cx + r + 48).toFixed(1) + '" y="' + (y + 4) + '">' + esc(m.labels[i]) + ' ' + (v / tot * 100).toFixed(1) + '%</text>'; });
      return '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(m.title) + '">' + out + '</svg>';
    }
    if (kind === 'hbar') {
      var rowH = Math.max(24, Math.min(34, 14 + 10 * S.length)); H = T + n * rowH + B;
      var L = Math.min(Math.max(48, Math.max.apply(null, m.labels.map(tw)) + 14), W * 0.4);
      var sc = nice(Math.max.apply(null, all)), gut = S.length === 1 ? Math.max(56, Math.max.apply(null, S[0].values.map(function (v) { return tw(fmt(v, m.unit)) + 14; }))) : 12;
      var px = function (v) { return L + (W - L - gut) * (v - sc.lo) / (sc.hi - sc.lo || 1); }, bh = Math.min(18, (rowH - 8) / S.length);
      sc.ticks.forEach(function (t) { out += '<line class="grid" x1="' + px(t).toFixed(1) + '" x2="' + px(t).toFixed(1) + '" y1="' + T + '" y2="' + (H - B) + '"/><text class="axis" x="' + px(t).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + fmt(t) + '</text>'; });
      m.labels.forEach(function (l, i) { var top = T + rowH * i + (rowH - bh * S.length) / 2;
        out += '<text class="axis" x="' + (L - 8) + '" y="' + (T + rowH * i + rowH / 2 + 4).toFixed(1) + '" text-anchor="end">' + esc(l) + '</text>';
        S.forEach(function (s, j) { var v = s.values[i]; out += '<rect class="s' + (j + 1) + '" x="' + px(0).toFixed(1) + '" y="' + (top + j * bh).toFixed(1) + '" width="' + Math.max(1, px(v) - px(0)).toFixed(1) + '" height="' + (bh - 2).toFixed(1) + '" rx="2" style="stroke:none"/>';
          if (S.length === 1) out += '<text class="val" x="' + (px(v) + 6).toFixed(1) + '" y="' + (top + bh / 2 + 4).toFixed(1) + '">' + fmt(v, m.unit) + '</text>'; }); });
      return '<svg viewBox="0 0 ' + W + ' ' + H + '">' + out + '</svg>';
    }
    var Lp = 56, R = 12, plotW = W - Lp - R;
    var top2 = kind === 'stacked' ? Math.max.apply(null, m.labels.map(function (_, i) { return S.reduce(function (a, s) { return a + s.values[i]; }, 0); })) : Math.max.apply(null, all);
    var sc2 = nice(top2), y = function (v) { return T + (H - T - B) * (1 - (v - sc2.lo) / (sc2.hi - sc2.lo || 1)); };
    sc2.ticks.forEach(function (t) { out += '<line class="grid" x1="' + Lp + '" x2="' + (W - R) + '" y1="' + y(t).toFixed(1) + '" y2="' + y(t).toFixed(1) + '"/><text class="axis" x="' + (Lp - 8) + '" y="' + (y(t) + 4).toFixed(1) + '" text-anchor="end">' + fmt(t) + '</text>'; });
    if (kind === 'scatter') {
      var xs = m.x, xlo = Math.min.apply(null, xs), xhi = Math.max.apply(null, xs), X = function (v) { return Lp + 8 + (plotW - 16) * (v - xlo) / (xhi - xlo || 1); };
      [0, .25, .5, .75, 1].forEach(function (f) { var v = Math.round(xlo + (xhi - xlo) * f); out += '<text class="axis" x="' + X(v).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + fmt(v) + '</text>'; });
      xs.forEach(function (xv, i) { out += '<circle class="s1" cx="' + X(xv).toFixed(1) + '" cy="' + y(S[0].values[i]).toFixed(1) + '" r="4.5" style="fill-opacity:.75;stroke:var(--bg-card);stroke-width:1"/>'; });
      return '<svg viewBox="0 0 ' + W + ' ' + H + '">' + out + '</svg>';
    }
    var bw = plotW / n, cxs = function (i) { return Lp + bw * i + bw / 2; };
    var every = Math.max(1, Math.ceil((Math.max.apply(null, m.labels.map(tw)) + 10) / bw));
    m.labels.forEach(function (l, i) { if (i % every === 0) out += '<text class="axis" x="' + cxs(i).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(l) + '</text>'; });
    if (kind === 'bar') {
      var k = S.length, inner = Math.min(bw * 0.7, 28 * k + 4 * (k - 1)), each = (inner - 4 * (k - 1)) / k;
      var labelled = k === 1 && S[0].values.every(function (v) { return tw(fmt(v, m.unit)) <= bw - 4; });
      m.labels.forEach(function (_, i) { S.forEach(function (s, j) { var v = s.values[i], x = Lp + bw * i + (bw - inner) / 2 + j * (each + 4);
        out += '<rect class="s' + (j + 1) + '" x="' + x.toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + each.toFixed(1) + '" height="' + Math.max(1, y(0) - y(v)).toFixed(1) + '" rx="2" style="stroke:none"><title>' + esc(m.labels[i]) + ' ' + esc(s.name) + '：' + fmt(v, m.unit) + unit + '</title></rect>';
        if (labelled) out += '<text class="val" x="' + (x + each / 2).toFixed(1) + '" y="' + (y(v) - 6).toFixed(1) + '" text-anchor="middle">' + fmt(v, m.unit) + '</text>'; }); });
    } else if (kind === 'stacked') {
      var inn = Math.min(bw * 0.6, 48);
      m.labels.forEach(function (_, i) { var acc = 0; S.forEach(function (s, j) { var v = s.values[i], from = acc; acc += v;
        out += '<rect class="s' + (j + 1) + '" x="' + (cxs(i) - inn / 2).toFixed(1) + '" y="' + y(acc).toFixed(1) + '" width="' + inn.toFixed(1) + '" height="' + Math.max(0, y(from) - y(acc)).toFixed(1) + '" style="stroke:var(--bg-card);stroke-width:1"/>'; }); });
    } else {
      S.forEach(function (s, j) { out += '<polyline class="ln s' + (j + 1) + '" points="' + s.values.map(function (v, i) { return cxs(i).toFixed(1) + ',' + y(v).toFixed(1); }).join(' ') + '"/>';
        s.values.forEach(function (v, i) { out += '<circle class="pt s' + (j + 1) + '" cx="' + cxs(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="3"><title>' + esc(m.labels[i]) + ' ' + esc(s.name) + '：' + fmt(v, m.unit) + unit + '</title></circle>'; }); });
    }
    return '<svg viewBox="0 0 ' + W + ' ' + H + '">' + out + '</svg>';
  }
  window.__adDraw = draw; window.__adPaint = paint;
  function legend(m, kind) { if (kind === 'pie' || kind === 'scatter' || m.series.length < 2) return ''; return '<div class="ak-legend-row">' + m.series.map(function (s, j) { return '<span><i class="s' + (j + 1) + '" style="background:var(--chart-' + (j + 1) + ')"></i>' + esc(s.name) + '</span>'; }).join('') + '</div>'; }
  function table(m) {
    var head = '<tr><th>' + esc(m.xName || '') + '</th>' + m.series.map(function (s) { return '<th class="r">' + esc(s.name) + '</th>'; }).join('') + '</tr>';
    var rows = m.labels.map(function (l, i) { return '<tr><td>' + esc(l) + '</td>' + m.series.map(function (s) { return '<td class="r">' + fmt(s.values[i], m.unit) + '</td>'; }).join('') + '</tr>'; }).join('');
    return '<table class="ak-table"><thead>' + head + '</thead><tbody>' + rows + '</tbody></table>';
  }
  function paint(card) {
    var m = JSON.parse(card.getAttribute('data-result')), st = card._st || (card._st = { view: m.view || 'chart', kind: m.kind || m.kinds[0] });
    var cw = card.clientWidth; card._w = cw; var W = cw ? Math.max(360, cw - 34) : 760, chart = st.view === 'chart';
    var h = '<div class="ak-result-head">';
    h += '<div class="ak-seg" role="group" aria-label="展示方式"><button type="button" data-view="chart" aria-pressed="' + chart + '">' + icon(KIND.bar[1]) + '图表</button><button type="button" data-view="table" aria-pressed="' + !chart + '">' + icon('M3 5h18v14H3zM3 10h18M3 15h18M9 10v9') + '表格</button></div>';
    h += '<span class="ak-result-title">' + esc(m.title) + '</span><span class="ak-spacer"></span>';
    if (chart) { h += '<div class="ak-kinds" role="group" aria-label="图表类型">' + m.kinds.map(function (k) { return '<button type="button" data-kind="' + k + '" aria-pressed="' + (k === st.kind) + '" title="' + KIND[k][0] + '" aria-label="' + KIND[k][0] + '">' + icon(KIND[k][1]) + '</button>'; }).join('') + '</div><button class="ak-btn ak-btn-ghost ak-btn-sm" type="button" aria-label="导出 PNG">' + icon('M12 4v11M7 10l5 5 5-5M4 20h16') + 'PNG</button>'; }
    else h += '<button class="ak-btn ak-btn-ghost ak-btn-sm" type="button">' + icon('M12 4v11M7 10l5 5 5-5M4 20h16') + '导出 CSV</button><button class="ak-btn ak-btn-ghost ak-btn-sm" type="button">' + icon('M12 4v11M7 10l5 5 5-5M4 20h16') + '导出 Excel</button>';
    h += '</div><div class="ak-result-body">' + (chart ? legend(m, st.kind) + '<div class="ak-plot">' + draw(m, st.kind, W) + '</div>' : table(m)) + '</div>';
    h += '<div class="ak-result-foot"><span class="ak-num">共 ' + m.total + ' 行</span></div>';
    card.innerHTML = h;
  }
  document.querySelectorAll('[data-result]').forEach(paint);
  document.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var card = b.closest('[data-result]');
    if (card && b.dataset.view) { card._st.view = b.dataset.view; paint(card); return; }
    if (card && b.dataset.kind) { card._st.kind = b.dataset.kind; paint(card); return; }
    var id = b.getAttribute('data-toggle');
    if (id) { var t = document.getElementById(id), open = b.getAttribute('aria-expanded') !== 'true'; b.setAttribute('aria-expanded', String(open)); t.hidden = !open;
      var grp = b.getAttribute('data-group'); if (grp && open) document.querySelectorAll('[data-group="' + grp + '"]').forEach(function (o) { if (o !== b && o.getAttribute('aria-expanded') === 'true') { o.setAttribute('aria-expanded', 'false'); document.getElementById(o.getAttribute('data-toggle')).hidden = true; } });
      var lab = b.querySelector('[data-open-label]'); if (lab) lab.textContent = open ? lab.getAttribute('data-open-label') : lab.getAttribute('data-closed-label'); }
    if (b.hasAttribute('aria-pressed') && b.closest('.ak-seg') && !card) { b.closest('.ak-seg').querySelectorAll('button').forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); }); }
  });
})();
(function () {
  function legend(m) { if (m.series.length < 2) return ''; return '<div class="ak-legend-row">' + m.series.map(function (s, j) { return '<span><i style="background:var(--chart-' + (j + 1) + ')"></i>' + s.name + '</span>'; }).join('') + '</div>'; }
  function paintChart(el) { var m = JSON.parse(el.getAttribute('data-chart')), cw = el.clientWidth; el._w = cw; el.innerHTML = legend(m) + '<div class="ak-plot">' + window.__adDraw(m, m.kind, cw ? Math.max(360, cw) : 720) + '</div>'; }
  document.querySelectorAll('[data-chart]').forEach(paintChart);
  // Charts are drawn at the width they actually get. A chart first painted while hidden or before layout
  // (width 0) used to fall back to a fixed width and then be scaled up, which made its text huge.
  // Repaint whenever a chart's real width changes.
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(function (entries) {
      entries.forEach(function (en) { var el = en.target, cw = el.clientWidth; if (!cw || Math.abs(cw - (el._w || 0)) < 4) return;
        if (el.hasAttribute('data-result')) window.__adPaint(el); else paintChart(el); });
    });
    document.querySelectorAll('[data-result],[data-chart]').forEach(function (el) { ro.observe(el); });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-tab],[data-open],[data-close],[data-goto],.ak-switch,.ak-reveal,.ak-list li,[data-pick]'); if (!b) return;
    if (b.hasAttribute('data-tab')) { var scope = b.closest('[data-tabscope]'); scope.querySelectorAll(':scope [data-tab]').forEach(function (t) { if (t.closest('[data-tabscope]') === scope) t.setAttribute('aria-selected', String(t === b)); }); scope.querySelectorAll('[data-tabpanel]').forEach(function (p) { if (p.closest('[data-tabscope]') === scope) p.hidden = p.getAttribute('data-tabpanel') !== b.getAttribute('data-tab'); }); }
    if (b.hasAttribute('data-open')) { var o = document.getElementById(b.getAttribute('data-open')); var cur = b.closest('.ak-overlay'); if (o) { if (cur && cur !== o && o.classList.contains('ak-overlay')) cur.hidden = true; o.hidden = false; } }
    if (b.hasAttribute('data-close')) { var ov = b.closest('.ak-overlay'); if (ov) ov.hidden = true; }
    if (b.classList.contains('ak-reveal')) { var inp = b.parentNode.querySelector('input'); var show = inp.type === 'password'; inp.type = show ? 'text' : 'password'; b.textContent = show ? '隐藏' : '显示'; b.setAttribute('aria-pressed', String(show)); }
    if (b.classList.contains('ak-switch')) b.setAttribute('aria-checked', String(b.getAttribute('aria-checked') !== 'true'));
    if (b.matches('.ak-list li') && !b.hasAttribute('data-open')) { var ls = b.closest('[data-tabscope]') || b.parentNode; ls.querySelectorAll('.ak-list li').forEach(function (li) { li.setAttribute('aria-current', String(li === b)); }); }
    if (b.hasAttribute('data-goto')) { var root = b.closest('.ak-proto') || document.querySelector('.ak-proto'); if (!root) return; root.querySelectorAll('[data-goto]').forEach(function (n) { if (n.getAttribute('data-goto') === b.getAttribute('data-goto')) n.setAttribute('aria-current', 'page'); else n.removeAttribute('aria-current'); }); root.querySelectorAll('[data-page]').forEach(function (p) { p.hidden = p.getAttribute('data-page') !== b.getAttribute('data-goto'); }); root.querySelectorAll('.ak-overlay').forEach(function (o) { o.hidden = true; }); var mn = root.querySelector('[data-page]:not([hidden])'); if (mn) { mn.scrollTop = 0; mn.querySelectorAll('[data-result]').forEach(function (c) { window.__adPaint(c); }); mn.querySelectorAll('[data-chart]').forEach(function (el) { var m = JSON.parse(el.getAttribute('data-chart')); el._w = el.clientWidth; el.querySelector('.ak-plot').innerHTML = window.__adDraw(m, m.kind, el.clientWidth ? Math.max(360, el.clientWidth) : 720); }); } }
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') document.querySelectorAll('.ak-overlay').forEach(function (o) { o.hidden = true; }); });
})();
document.addEventListener('change', function (e) { var t = e.target; if (t && t.hasAttribute && t.hasAttribute('data-mixed')) t.removeAttribute('data-mixed'); });
document.addEventListener('change', function (e) { var t = e.target; if (t && t.getAttribute && t.getAttribute('data-toggle-enum')) { var row = document.getElementById(t.getAttribute('data-toggle-enum')); if (row) row.hidden = !t.checked; } });
(function () {
  var CH = { prev: 'M15 6l-6 6 6 6', next: 'M9 6l6 6-6 6' };
  function ic(d) { return '<svg class="ak-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>'; }
  function pages(n, c) { if (n <= 7) { var a = []; for (var i = 1; i <= n; i++) a.push(i); return a; } var s = [1]; var lo = Math.max(2, c - 1), hi = Math.min(n - 1, c + 1); if (c <= 3) { lo = 2; hi = 4; } if (c >= n - 2) { lo = n - 3; hi = n - 1; } if (lo > 2) s.push(0); for (var j = lo; j <= hi; j++) s.push(j); if (hi < n - 1) s.push(0); s.push(n); return s; }
  function render(el) {
    var n = +el.getAttribute('data-pages'), c = +el.getAttribute('data-cur'), cp = el.hasAttribute('data-compact');
    var h = cp ? '' : '<span class="ak-pager-total">' + el.getAttribute('data-label') + '</span>';
    h += '<button class="ak-pg" type="button" data-pg="prev" aria-label="上一页"' + (c <= 1 ? ' disabled' : '') + '>' + ic(CH.prev) + '</button>';
    if (cp) h += '<span class="ak-num ak-pg-of">' + c + ' / ' + n + '</span>';
    else pages(n, c).forEach(function (p) { h += p ? '<button class="ak-pg" type="button" data-pg="' + p + '"' + (p === c ? ' aria-current="page"' : '') + '>' + p + '</button>' : '<span class="ak-pg-gap">…</span>'; });
    h += '<button class="ak-pg" type="button" data-pg="next" aria-label="下一页"' + (c >= n ? ' disabled' : '') + '>' + ic(CH.next) + '</button>';
    h += '<label class="ak-pg-jump">跳至<input inputmode="numeric" aria-label="页码">页</label>';
    el.innerHTML = h;
  }
  function go(el, p) { var n = +el.getAttribute('data-pages'); p = Math.max(1, Math.min(n, p | 0)); if (!p) return; el.setAttribute('data-cur', p); render(el);
    var sc = el.closest('[data-pagescope]'); if (!sc) return; var per = +el.getAttribute('data-per') || 10, first = null;
    sc.querySelectorAll('[data-pagerow]').forEach(function (r, i) { var show = Math.floor(i / per) === p - 1; r.hidden = !show; if (show && !first) first = r; });
    if (first) first.click(); }
  document.addEventListener('click', function (e) { var b = e.target.closest('.ak-pager [data-pg]'); if (!b || b.disabled) return; var el = b.closest('.ak-pager'), c = +el.getAttribute('data-cur'), v = b.getAttribute('data-pg'); go(el, v === 'prev' ? c - 1 : v === 'next' ? c + 1 : +v); });
  document.addEventListener('keydown', function (e) { var t = e.target; if (e.key === 'Enter' && t.closest && t.closest('.ak-pg-jump')) { go(t.closest('.ak-pager'), parseInt(t.value, 10)); } });
})();

(function () {
  var IC = { success: 'M5 12.5l4.5 4.5L19 7.5', info: 'M12 11v6M12 7.5v.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', warning: 'M12 9v4M12 16.5v.01M10.3 4.3L2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z', danger: 'M12 8v5M12 16.5v.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z' };
  function svg(d) { return '<svg class="ak-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="' + d + '"/></svg>'; }
  function host() {
    var h = document.querySelector('.ak-toasts:not(.ak-toasts-static)');
    if (!h) { h = document.createElement('div'); h.className = 'ak-toasts'; h.setAttribute('role', 'status'); h.setAttribute('aria-live', 'polite'); (document.querySelector('.ak-proto') || document.querySelector('.ak-app') || document.body).appendChild(h); }
    return h;
  }
  function close(t) { if (!t || t._gone) return; t._gone = true; clearTimeout(t._tm); t.classList.add('is-leaving'); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 180); }
  function arm(t) { clearTimeout(t._tm); t._tm = setTimeout(function () { close(t); }, t._ms); }
  function toast(o) {
    var t = document.createElement('div'); var tone = o.tone || 'info';
    t.className = 'ak-toast ak-toast-' + tone; t._ms = o.ms || 4000;
    var act = o.action ? '<button class="ak-link" type="button"' + (o.goto ? ' data-goto="' + o.goto + '"' : '') + (o.open ? ' data-open="' + o.open + '"' : '') + ' data-toast-close>' + o.action + '</button>' : '';
    t.innerHTML = '<span class="ak-toast-ic">' + svg(IC[tone] || IC.info) + '</span><div class="ak-toast-text"><b>' + o.title + '</b>' + (o.text ? '<span>' + o.text + '</span>' : '') + act + '</div>' +
      '<button class="ak-icon-btn" type="button" data-toast-close aria-label="关闭通知">' + svg('M6 6l12 12M18 6L6 18') + '</button><i class="ak-toast-bar" style="animation-duration:' + t._ms + 'ms"></i>';
    var h = host(); h.appendChild(t);
    while (h.children.length > 3) close(h.children[0]);
    t.addEventListener('mouseenter', function () { clearTimeout(t._tm); t.classList.add('is-paused'); });
    t.addEventListener('mouseleave', function () { t.classList.remove('is-paused'); arm(t); });
    arm(t);
  }
  window.akToast = toast;
  document.addEventListener('click', function (e) {
    var c = e.target.closest('[data-toast-close]'); if (c) close(c.closest('.ak-toast'));
    var b = e.target.closest('[data-toast]'); if (!b || b.disabled) return;
    var list = JSON.parse(b.getAttribute('data-toast')); if (!Array.isArray(list)) list = [list];
    list.forEach(function (o) { setTimeout(function () { toast(o); }, o.delay || 0); });
  });
  // chat: new session shows the guided empty state; picking a session shows the thread again
  document.addEventListener('click', function (e) {
    var col = document.querySelector('.ak-chat-col'); if (!col) return;
    if (e.target.closest('[data-chat-new]')) col.classList.add('is-empty');
    else if (e.target.closest('.ak-sessions .ak-list li')) col.classList.remove('is-empty');
    var q = e.target.closest('[data-ask]'); if (q) { var ta = col.querySelector('.ak-composer textarea'); if (ta) { ta.value = q.getAttribute('data-ask'); ta.focus(); } }
  });
})();
(function () {
  document.addEventListener('click', function (e) {
    // collapsible field panel on the chat page
    var t = e.target.closest('[data-dict-toggle]');
    if (t) { var chat = t.closest('.ak-chat'); if (chat) { var open = !chat.classList.contains('dict-open'); chat.classList.toggle('dict-open', open); chat.querySelectorAll('[data-dict-toggle][aria-pressed]').forEach(function (b) { b.setAttribute('aria-pressed', String(open)); }); } }
    // dropdown with options
    var dd = e.target.closest('.ak-dd');
    document.querySelectorAll('.ak-dd .ak-menu:not([hidden])').forEach(function (m) { if (!dd || !dd.contains(m)) { m.hidden = true; m.parentNode.querySelector('.ak-dd-btn').setAttribute('aria-expanded', 'false'); } });
    if (dd) {
      var btn = e.target.closest('.ak-dd-btn'), opt = e.target.closest('.ak-menu-item'), menu = dd.querySelector('.ak-menu');
      if (btn) { menu.hidden = !menu.hidden; btn.setAttribute('aria-expanded', String(!menu.hidden)); var si = menu.querySelector('.ak-menu-search input'); if (si && !menu.hidden) { si.value = ''; si.dispatchEvent(new Event('input', { bubbles: true })); si.focus(); } }
      if (opt) { var lab = dd.querySelector('.ak-dd-label'); if (lab && opt.hasAttribute('data-label')) lab.textContent = opt.getAttribute('data-label'); menu.hidden = true; dd.querySelector('.ak-dd-btn').setAttribute('aria-expanded', 'false');
        menu.querySelectorAll('.ak-menu-item').forEach(function (o) { o.setAttribute('aria-selected', String(o === opt)); }); }
    }
    // field row -> its description below
    var row = e.target.closest('tr[data-fd]');
    if (row) { var panel = row.closest('.ak-dict-panel'); panel.querySelectorAll('tr[data-fd]').forEach(function (r) { r.setAttribute('aria-selected', String(r === row)); });
      panel.querySelectorAll('.ak-fd-item').forEach(function (d) { d.hidden = d.id !== row.getAttribute('data-fd'); }); }
    // insert a field alias into the composer
    var ins = e.target.closest('[data-insert]');
    if (ins) { var ta = document.querySelector('.ak-chat-col .ak-composer textarea'); if (ta) { var v = ins.getAttribute('data-insert'); ta.value = ta.value ? ta.value.replace(/\s*$/, '') + ' ' + v : v; ta.focus(); } }
  });
})();

(function () {
  function setDict(chat, open) { if (!chat) return; chat.classList.toggle('dict-open', open); chat.querySelectorAll('[data-dict-toggle][aria-pressed]').forEach(function (b) { b.setAttribute('aria-pressed', String(open)); }); }
  // sending a message folds the field panel; starting a new session opens it again
  document.addEventListener('click', function (e) {
    if (e.target.closest('.ak-send')) setDict(e.target.closest('.ak-chat'), false);
    if (e.target.closest('[data-chat-new]')) setDict(document.querySelector('.ak-chat'), true);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target.matches && e.target.matches('.ak-composer textarea')) { e.preventDefault(); setDict(e.target.closest('.ak-chat'), false); }
  });
})();

document.addEventListener('input', function (e) {
  var t = e.target; if (!t.closest || !t.closest('.ak-menu-search')) return;
  var q = t.value.trim().toLowerCase(), menu = t.closest('.ak-menu'), n = 0;
  menu.querySelectorAll('.ak-menu-item').forEach(function (o) { var hit = !q || o.getAttribute('data-search').toLowerCase().indexOf(q) >= 0; o.hidden = !hit; if (hit) n++; });
  var em = menu.querySelector('.ak-menu-empty'); if (em) em.hidden = n > 0;
});
