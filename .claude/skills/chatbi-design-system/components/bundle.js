/* @ds-bundle: {"format":4,"namespace":"ChatBI","components":[{"name":"Icon"},{"name":"Button"},{"name":"Badge"},{"name":"ScopeTag"},{"name":"Tabs"},{"name":"Sidebar"},{"name":"Panel"},{"name":"TextField"},{"name":"Composer"},{"name":"ChatMessage"},{"name":"ClarifyPrompt"},{"name":"ExplainCard"},{"name":"SqlBlock"},{"name":"ResultTable"},{"name":"ChartCard"},{"name":"FeedbackBar"},{"name":"Callout"},{"name":"CheckList"},{"name":"Trace"},{"name":"RunList"}]} */
(function () {
  var R = window.React, h = R.createElement, useState = R.useState;
  function cx() { var out = []; for (var i = 0; i < arguments.length; i++) if (arguments[i]) out.push(arguments[i]); return out.join(' '); }
  function omit(o, keys) { var r = {}; for (var k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; }

  /* ---------- Icon: 24-unit grid, 1.6 stroke, round caps ---------- */
  var P = {
    chat: 'M4 5h16v11H9l-5 4z',
    database: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
    table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 10v9',
    chart: 'M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-3',
    line: 'M4 20V4M4 20h16M7 15l4-5 3 3 5-6',
    shield: 'M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z',
    lock: 'M6 11h12v10H6zM8 11V8a4 4 0 0 1 8 0v3',
    check: 'M5 12.5l4.5 4.5L19 7',
    x: 'M6 6l12 12M18 6L6 18',
    alert: 'M12 4l9 16H3zM12 10v4M12 17.5v.01',
    info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5v.01',
    help: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17v.01',
    play: 'M7 4l13 8-13 8z',
    copy: 'M9 9h11v11H9zM5 15V4h11',
    edit: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4',
    download: 'M12 4v11M7 10l5 5 5-5M4 20h16',
    thumbsUp: 'M7 10v10H4V10zM7 10l4-7c1.7 0 2.6 1.2 2.3 2.8L12.8 9H19a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.8 20H7',
    thumbsDown: 'M7 14V4H4v10zM7 14l4 7c1.7 0 2.6-1.2 2.3-2.8L12.8 15H19a2 2 0 0 0 2-2.3l-1.2-7A2 2 0 0 0 17.8 4H7',
    search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4',
    plus: 'M12 5v14M5 12h14',
    send: 'M4 12l16-8-6 16-3-7z',
    users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14c1.8.8 3 2.9 3 6',
    book: 'M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11',
    sigma: 'M18 5H6l6 7-6 7h12',
    upload: 'M12 20V9M7 14l5-5 5 5M4 4h16',
    cpu: 'M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4',
    log: 'M6 3h9l4 4v14H6zM9 12h7M9 16h7M9 8h3',
    flask: 'M9 3h6M10 3v6L4.5 19A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 1.5-2L14 9V3M7 15h10',
    flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
    refresh: 'M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4',
    loader: 'M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1',
    eyeOff: 'M3 3l18 18M10.6 6.1A10 10 0 0 1 12 6c5 0 9 6 9 6a17 17 0 0 1-2.6 3.2M6.6 6.6C4.3 8.2 3 12 3 12s4 6 9 6c1.6 0 3-.4 4.3-1M9.9 9.9a3 3 0 0 0 4.2 4.2',
    chevronDown: 'M6 9l6 6 6-6',
    more: 'M5 12h.01M12 12h.01M19 12h.01',
    sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z'
  };
  function Icon(p) {
    return h('svg', { className: cx('cb-icon', p.size === 'sm' && 'cb-icon-sm', p.className), viewBox: '0 0 24 24', 'aria-hidden': p.label ? null : 'true', role: p.label ? 'img' : null, 'aria-label': p.label || null },
      h('path', { d: P[p.name] || P.info }));
  }
  Icon.names = Object.keys(P);

  /* ---------- Button ---------- */
  function Button(p) {
    var v = p.variant || 'secondary', iconOnly = !p.children && p.icon;
    var rest = omit(p, ['variant', 'size', 'icon', 'className', 'children']);
    return h('button', Object.assign({ type: 'button' }, rest, { className: cx('cb-btn', 'cb-btn-' + v, p.size === 'sm' && 'cb-btn-sm', iconOnly && 'cb-btn-icon', p.className) }),
      p.icon ? h(Icon, { name: p.icon, size: p.size === 'sm' ? 'sm' : null }) : null, p.children);
  }

  /* ---------- Badge ---------- */
  function Badge(p) {
    return h('span', { className: cx('cb-badge', 'cb-badge-' + (p.tone || 'neutral'), p.mono && 'cb-badge-mono', p.outline && 'cb-badge-outline', p.className) },
      p.icon ? h(Icon, { name: p.icon, size: 'sm' }) : null, p.children);
  }

  /* ---------- ScopeTag ---------- */
  var SCOPE = { platform: '平台级', team: '团队级', personal: '个人级' };
  function ScopeTag(p) {
    var lv = p.level || 'platform';
    return h('span', { className: 'cb-scope cb-scope-' + lv, title: p.title || null }, p.children || SCOPE[lv]);
  }

  /* ---------- Tabs (pill) ---------- */
  function Tabs(p) {
    var s = useState(p.defaultValue || (p.items[0] && p.items[0].id)), cur = p.value != null ? p.value : s[0];
    return h('div', { className: 'cb-tabs', role: 'tablist', 'aria-label': p.label || null },
      p.items.map(function (it) {
        return h('button', { key: it.id, type: 'button', role: 'tab', className: 'cb-tab', 'aria-selected': cur === it.id ? 'true' : 'false',
          onClick: function () { s[1](it.id); p.onChange && p.onChange(it.id); } },
          it.icon ? h(Icon, { name: it.icon, size: 'sm' }) : null, it.label,
          it.count != null ? h('span', { className: 'cb-num', style: { opacity: .7 } }, it.count) : null);
      }));
  }

  /* ---------- Sidebar ---------- */
  function Sidebar(p) {
    var s = useState(p.active), cur = s[0];
    return h('nav', { className: 'cb-side', 'aria-label': '主导航' },
      h('div', { className: 'cb-brand' }, h('span', { className: 'cb-brand-mark' }, 'BI'), p.brand || 'ChatBI'),
      (p.groups || []).map(function (g) {
        return h('div', { className: 'cb-nav-group', key: g.label },
          h('div', { className: 'cb-eyebrow' }, g.label),
          g.items.map(function (it) {
            return h('button', { key: it.id, type: 'button', className: 'cb-nav', 'aria-current': cur === it.id ? 'page' : null, onClick: function () { s[1](it.id); p.onNavigate && p.onNavigate(it.id); } },
              h(Icon, { name: it.icon || 'info' }), h('span', null, it.label),
              it.count ? h('span', { className: 'cb-badge cb-count' }, it.count) : null);
          }));
      }),
      p.footer ? h('div', { className: 'cb-side-foot' }, p.footer) : null);
  }

  /* ---------- Panel ---------- */
  function Panel(p) {
    return h('section', { className: cx('cb-panel', p.className), style: p.style },
      (p.title || p.actions) ? h('div', { className: 'cb-panel-head' },
        h('div', null, p.eyebrow ? h('div', { className: 'cb-eyebrow', style: { marginBottom: 2 } }, p.eyebrow) : null, p.title ? h('h3', { className: 'cb-h' }, p.title) : null),
        p.actions || null) : null,
      h('div', { className: 'cb-panel-body' }, p.children));
  }

  /* ---------- TextField ---------- */
  function TextField(p) {
    var id = p.id || ('f' + Math.random().toString(36).slice(2, 8));
    var rest = omit(p, ['label', 'hint', 'error', 'icon', 'id', 'suffix']);
    return h('div', { className: cx('cb-field', p.error && 'cb-field-error') },
      p.label ? h('label', { htmlFor: id }, p.label) : null,
      h('div', { className: 'cb-input' }, p.icon ? h(Icon, { name: p.icon }) : null, h('input', Object.assign({ id: id, 'aria-invalid': p.error ? 'true' : null }, rest)), p.suffix || null),
      (p.error || p.hint) ? h('div', { className: 'cb-field-hint' }, p.error || p.hint) : null);
  }

  /* ---------- Composer ---------- */
  function Composer(p) {
    var s = useState(p.defaultValue || '');
    return h('div', { className: 'cb-composer' },
      h('textarea', { rows: 2, value: s[0], placeholder: p.placeholder || '用自然语言提问，例如：上个月各区域回款金额', 'aria-label': '提问', onChange: function (e) { s[1](e.target.value); } }),
      h('div', { className: 'cb-composer-bar' },
        h('span', { className: 'cb-source', title: '会话创建时绑定，不可切换' }, h(Icon, { name: 'database', size: 'sm' }), p.source || 'mysql · sales_dw', h(Icon, { name: 'lock', size: 'sm' })),
        p.hint ? h('span', { className: 'cb-muted', style: { fontSize: 12 } }, p.hint) : null,
        h('span', { className: 'cb-spacer' }),
        p.streaming
          ? h(Button, { variant: 'secondary', size: 'sm', icon: 'x' }, '停止')
          : h(Button, { variant: 'primary', size: 'sm', icon: 'send', disabled: !s[0], onClick: function () { p.onSubmit && p.onSubmit(s[0]); } }, '发送')));
  }

  /* ---------- ChatMessage ---------- */
  function ChatMessage(p) {
    if (p.role === 'user') return h('div', { className: 'cb-msg cb-msg-user' }, h('div', { className: 'cb-bubble' }, p.children));
    return h('div', { className: 'cb-msg' },
      h('div', { className: 'cb-avatar', 'aria-hidden': 'true' }, 'BI'),
      h('div', { className: 'cb-msg-body' },
        p.meta ? h('div', { className: 'cb-msg-meta' }, p.meta) : null,
        p.children,
        p.streaming ? h('span', { className: 'cb-caret', 'aria-label': '生成中' }) : null));
  }

  /* ---------- ClarifyPrompt ---------- */
  function ClarifyPrompt(p) {
    var s = useState(null);
    return h('div', { className: 'cb-clarify', role: 'group', 'aria-label': '澄清问题' },
      h('div', { className: 'cb-clarify-q' }, h(Icon, { name: 'help' }), h('div', null, h('b', null, '需要确认一下 · '), p.question)),
      h('div', { className: 'cb-row' }, (p.options || []).map(function (o, i) {
        return h('button', { key: i, type: 'button', className: 'cb-chip', 'aria-pressed': s[0] === i ? 'true' : 'false', style: s[0] === i ? { borderColor: 'var(--ink)', fontWeight: 500 } : null, onClick: function () { s[1](i); p.onPick && p.onPick(o); } },
          s[0] === i ? h(Icon, { name: 'check', size: 'sm' }) : null, o.label, o.hint ? h('code', null, o.hint) : null);
      })));
  }

  /* ---------- ExplainCard ---------- */
  function ExplainCard(p) {
    var rows = [];
    if (p.metric) rows.push(['指标', [h('span', { key: 'n' }, p.metric.name), p.metric.scope ? h(ScopeTag, { key: 's', level: p.metric.scope }) : null, p.metric.expr ? h('code', { key: 'e' }, p.metric.expr) : null]]);
    (p.terms || []).forEach(function (t) { rows.push(['口径', [h('span', { key: 'n' }, '“' + t.term + '” → '), h('code', { key: 'e' }, t.field), t.scope ? h(ScopeTag, { key: 's', level: t.scope }) : null]]); });
    if (p.filters && p.filters.length) rows.push(['过滤', p.filters.map(function (f, i) { return h('code', { key: i }, f); })]);
    if (p.timeRange) rows.push(['时间', [h('span', { key: 't' }, p.timeRange)]]);
    return h('div', { className: 'cb-explain' },
      h('div', null, h('span', { className: 'cb-explain-title' }, '这样理解你的问题'), p.summary),
      rows.length ? h('dl', null, rows.map(function (r, i) { return [h('dt', { key: 'k' + i }, r[0]), h('dd', { key: 'v' + i }, r[1])]; })) : null);
  }

  /* ---------- SqlBlock ---------- */
  var KW = /^(SELECT|FROM|WHERE|AND|OR|NOT|IN|AS|ON|JOIN|LEFT|RIGHT|INNER|OUTER|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|SUM|COUNT|AVG|MIN|MAX|DISTINCT|CASE|WHEN|THEN|ELSE|END|BETWEEN|IS|NULL|DESC|ASC|WITH|DATE_TRUNC|INTERVAL|CURRENT_DATE|LIKE|COALESCE|ROUND)$/i;
  function highlight(sql) {
    var re = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|([^\sA-Za-z0-9_']+)/g, m, out = [], i = 0;
    while ((m = re.exec(sql))) {
      i++;
      if (m[1]) out.push(h('span', { key: i, className: 'cb-cm' }, m[1]));
      else if (m[2]) out.push(h('span', { key: i, className: 'cb-str' }, m[2]));
      else if (m[3]) out.push(h('span', { key: i, className: 'cb-nm' }, m[3]));
      else if (m[4] && KW.test(m[4])) out.push(h('span', { key: i, className: 'cb-kw' }, m[4]));
      else out.push(m[0]);
    }
    return out;
  }
  function SqlBlock(p) {
    var ed = useState(!!p.editing), txt = useState(p.sql || ''), cp = useState(false);
    return h('div', { className: 'cb-sql' },
      h('div', { className: 'cb-sql-head' },
        h('span', { className: 'cb-eyebrow' }, 'SQL'),
        h(Badge, { tone: 'success', icon: 'shield' }, '只读 · 已校验'),
        ed[0] ? h(Badge, { tone: 'warning', icon: 'edit' }, '已手动修改') : null,
        h('span', { className: 'cb-spacer' }),
        p.meta ? h('span', { className: 'cb-muted cb-num', style: { fontSize: 12, marginRight: 4 } }, p.meta) : null,
        h(Button, { variant: 'ghost', size: 'sm', icon: cp[0] ? 'check' : 'copy', 'aria-label': '复制 SQL', onClick: function () { try { navigator.clipboard.writeText(txt[0]); } catch (e) {} cp[1](true); } }),
        ed[0]
          ? h(Button, { variant: 'primary', size: 'sm', icon: 'play', onClick: function () { p.onRun && p.onRun(txt[0]); } }, '重新执行')
          : (p.editable !== false ? h(Button, { variant: 'ghost', size: 'sm', icon: 'edit', onClick: function () { ed[1](true); } }, '编辑') : null)),
      ed[0]
        ? h('textarea', { value: txt[0], spellCheck: false, 'aria-label': '编辑 SQL', onChange: function (e) { txt[1](e.target.value); } })
        : h('pre', null, h('code', null, highlight(txt[0]))));
  }

  /* ---------- ResultTable ---------- */
  function fmt(v, col) {
    if (v == null) return h('span', { className: 'cb-muted' }, '—');
    if (col.masked) return h('span', { className: 'cb-mask', title: '敏感字段已脱敏' }, v);
    if (col.type === 'number' && typeof v === 'number') return v.toLocaleString('zh-CN', { maximumFractionDigits: col.digits != null ? col.digits : 2 });
    return v;
  }
  function ResultTable(p) {
    var cols = p.columns || [];
    return h('div', { className: 'cb-table-wrap', style: p.maxHeight ? { maxHeight: p.maxHeight } : null },
      h('table', { className: 'cb-table' },
        h('thead', null, h('tr', null, cols.map(function (c) {
          return h('th', { key: c.key, className: c.type === 'number' ? 'cb-r' : null }, c.label, c.masked ? ' ' : null, c.masked ? h(Icon, { name: 'eyeOff', size: 'sm', label: '已脱敏' }) : null, c.field ? h('small', null, c.field) : null);
        }))),
        h('tbody', null, (p.rows || []).map(function (r, i) {
          return h('tr', { key: i }, cols.map(function (c) { return h('td', { key: c.key, className: c.type === 'number' ? 'cb-r' : null }, fmt(r[c.key], c)); }));
        }))),
      h('div', { className: 'cb-table-foot' },
        h('span', { className: 'cb-num' }, (p.total != null ? p.total : (p.rows || []).length).toLocaleString('zh-CN') + ' 行'),
        p.truncated ? h(Badge, { tone: 'warning', icon: 'alert' }, '已截断，仅返回前 ' + p.truncated + ' 行') : null,
        p.ms != null ? h('span', { className: 'cb-num' }, '· ' + p.ms + ' ms') : null,
        h('span', { className: 'cb-spacer' }),
        h(Button, { variant: 'ghost', size: 'sm', icon: 'download' }, 'CSV'),
        h(Button, { variant: 'ghost', size: 'sm', icon: 'download' }, 'Excel')));
  }

  /* ---------- ChartCard: bar / line, up to 5 series ---------- */
  function ChartCard(p) {
    var s = useState(p.type || 'bar'), type = s[0], ref = R.useRef(null), ws = useState(640);
    R.useEffect(function () {
      var el = ref.current; if (!el || !window.ResizeObserver) return;
      var ro = new ResizeObserver(function () { var cw = Math.round(el.getBoundingClientRect().width); if (cw > 0) ws[1](cw); });
      ro.observe(el); return function () { ro.disconnect(); };
    }, []);
    var data = p.data || [], series = p.series || ['值'], W = ws[0], H = p.height || 200, L = 44, B = 24, T = 12;
    var max = 0; data.forEach(function (d) { d.values.forEach(function (v) { if (v > max) max = v; }); });
    var step = Math.pow(10, Math.floor(Math.log10(max || 1))), top = Math.ceil((max || 1) / step) * step;
    var y = function (v) { return T + (H - T - B) * (1 - v / top); };
    var bw = (W - L) / Math.max(data.length, 1), ticks = [0, .25, .5, .75, 1].map(function (f) { return top * f; });
    var fmtN = p.format || function (v) { return v >= 10000 ? (v / 10000).toFixed(v >= 100000 ? 0 : 1) + '万' : String(Math.round(v)); };
    var marks = [];
    ticks.forEach(function (t, i) {
      marks.push(h('line', { key: 'g' + i, className: 'cb-grid', x1: L, x2: W, y1: y(t), y2: y(t) }));
      marks.push(h('text', { key: 'gt' + i, className: 'cb-axis', x: L - 8, y: y(t) + 4, textAnchor: 'end' }, fmtN(t)));
    });
    data.forEach(function (d, i) {
      marks.push(h('text', { key: 'x' + i, className: 'cb-axis', x: L + bw * i + bw / 2, y: H - 6, textAnchor: 'middle' }, d.label));
    });
    if (type === 'bar') {
      var inner = Math.min(bw * .7, 28 * series.length + 4 * (series.length - 1)), each = (inner - 4 * (series.length - 1)) / series.length;
      data.forEach(function (d, i) {
        d.values.forEach(function (v, j) {
          var x = L + bw * i + (bw - inner) / 2 + j * (each + 4);
          marks.push(h('rect', { key: 'b' + i + '-' + j, className: 'cb-s' + (j + 1), x: x, y: y(v), width: each, height: Math.max(0, y(0) - y(v)), rx: 2, style: { stroke: 'none' } }, h('title', null, d.label + ' ' + series[j] + '：' + v.toLocaleString('zh-CN'))));
        });
      });
    } else {
      series.forEach(function (sn, j) {
        var pts = data.map(function (d, i) { return [L + bw * i + bw / 2, y(d.values[j])]; });
        marks.push(h('polyline', { key: 'l' + j, className: 'cb-s' + (j + 1), points: pts.map(function (q) { return q.join(','); }).join(' '), style: { fill: 'none', strokeWidth: 2, strokeLinejoin: 'round' } }));
        pts.forEach(function (q, i) { marks.push(h('circle', { key: 'c' + j + '-' + i, className: 'cb-s' + (j + 1), cx: q[0], cy: q[1], r: 3, style: { stroke: 'var(--surface)', strokeWidth: 1.5 } })); });
      });
    }
    return h('figure', { className: 'cb-chart', style: { margin: 0 } },
      h('div', { className: 'cb-chart-head' },
        h('span', { className: 'cb-h', style: { fontSize: 14 } }, p.title),
        h('span', { className: 'cb-spacer' }),
        series.length > 1 ? h('div', { className: 'cb-legend' }, series.map(function (sn, j) { return h('span', { key: j }, h('i', { className: 'cb-s' + (j + 1) , style: { background: 'var(--chart-' + (j + 1) + ')' } }), sn); })) : null,
        h('div', { className: 'cb-seg', role: 'group', 'aria-label': '图表类型' },
          h('button', { type: 'button', 'aria-pressed': type === 'bar' ? 'true' : 'false', 'aria-label': '柱状图', onClick: function () { s[1]('bar'); } }, h(Icon, { name: 'chart', size: 'sm' })),
          h('button', { type: 'button', 'aria-pressed': type === 'line' ? 'true' : 'false', 'aria-label': '折线图', onClick: function () { s[1]('line'); } }, h(Icon, { name: 'line', size: 'sm' })))),
      h('div', { ref: ref }, h('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img', 'aria-label': p.title }, marks)),
      p.caption ? h('figcaption', { className: 'cb-muted', style: { fontSize: 12, marginTop: 8 } }, p.caption) : null);
  }

  /* ---------- FeedbackBar ---------- */
  var REASONS = ['口径不对（指标或别名理解错）', '选错了表或字段', '结果数据不对', 'SQL 报错或超时', '其他'];
  function FeedbackBar(p) {
    var v = useState(p.defaultValue || null), open = useState(!!p.defaultOpen), sent = useState(false), pick = useState(0);
    return h('div', null,
      h('div', { className: 'cb-feedback' },
        h(Button, { variant: 'ghost', size: 'sm', icon: 'thumbsUp', 'aria-label': '有用', 'aria-pressed': v[0] === 'up' ? 'true' : 'false', onClick: function () { v[1]('up'); open[1](false); } }),
        h(Button, { variant: 'ghost', size: 'sm', icon: 'thumbsDown', 'aria-label': '没用', 'aria-pressed': v[0] === 'down' ? 'true' : 'false', onClick: function () { v[1]('down'); open[1](true); sent[1](false); } }),
        h(Button, { variant: 'ghost', size: 'sm', icon: 'copy', 'aria-label': '复制答案' }),
        sent[0] ? h('span', { style: { marginLeft: 6 } }, '已提交，团队负责人会处理') : null),
      open[0] && !sent[0] ? h('div', { className: 'cb-feedback-pop', role: 'dialog', 'aria-label': '点踩原因' },
        h('div', { className: 'cb-h', style: { fontSize: 14 } }, '哪里不对？'),
        REASONS.map(function (r, i) { return h('label', { key: i, className: 'cb-radio' }, h('input', { type: 'radio', name: 'cb-fb', checked: pick[0] === i, onChange: function () { pick[1](i); } }), r); }),
        h('div', { className: 'cb-muted', style: { fontSize: 12, lineHeight: '18px' } }, '会附带本轮的问题、SQL 和结果摘要（行数与前 5 行）。'),
        h('div', { className: 'cb-row', style: { justifyContent: 'flex-end' } },
          h(Button, { size: 'sm', variant: 'ghost', onClick: function () { open[1](false); } }, '取消'),
          h(Button, { size: 'sm', variant: 'primary', onClick: function () { sent[1](true); } }, '提交反馈'))) : null);
  }

  /* ---------- Callout ---------- */
  var CALLOUT_ICON = { info: 'info', warning: 'alert', danger: 'shield', success: 'check' };
  function Callout(p) {
    var t = p.tone || 'info';
    return h('div', { className: 'cb-callout cb-callout-' + t, role: t === 'danger' ? 'alert' : null },
      h(Icon, { name: p.icon || CALLOUT_ICON[t] }),
      h('div', null, p.title ? h('b', null, p.title) : null, p.children, p.actions ? h('div', { className: 'cb-callout-actions' }, p.actions) : null));
  }

  /* ---------- CheckList ---------- */
  var CHECK_ICON = { pass: 'check', fail: 'alert', warn: 'alert' };
  function CheckList(p) {
    return h('ul', { className: 'cb-checks' }, (p.items || []).map(function (it, i) {
      return h('li', { key: i, className: 'cb-check cb-check-' + (it.status || 'pass') },
        h(Icon, { name: CHECK_ICON[it.status || 'pass'], label: it.status === 'fail' ? '未通过' : it.status === 'warn' ? '警告' : '通过' }),
        h('div', null, it.label, it.detail ? h('small', null, it.detail) : null));
    }));
  }

  /* ---------- Trace ---------- */
  var TRACE_ICON = { ok: 'check', fail: 'x', retry: 'refresh', run: 'loader', wait: 'more' };
  function Trace(p) {
    return h('ol', { className: 'cb-trace' }, (p.steps || []).map(function (st, i) {
      return h('li', { key: i, className: st.status || 'ok' }, h(Icon, { name: TRACE_ICON[st.status || 'ok'] }), h('span', null, st.label), h('span', { className: 'cb-t' }, st.time || ''));
    }));
  }

  /* ---------- RunList (query log) ---------- */
  var RUN_TONE = { ok: ['success', '成功', 'check'], blocked: ['danger', '已拦截', 'shield'], error: ['danger', '失败', 'x'], fixed: ['warning', '纠错后成功', 'refresh'], clarify: ['info', '待澄清', 'help'] };
  function RunList(p) {
    return h('table', { className: 'cb-runs' }, h('tbody', null, (p.rows || []).map(function (r, i) {
      var tn = RUN_TONE[r.status] || RUN_TONE.ok;
      return h('tr', { key: i },
        h('td', { style: { width: 1 } }, h(Badge, { tone: tn[0], icon: tn[2] }, tn[1])),
        h('td', null, h('div', { className: 'q' }, r.question), r.reason ? h('div', { className: 'reason' }, r.reason) : h('div', { className: 'cb-muted', style: { fontSize: 12 } }, r.detail || '')),
        p.showUser !== false ? h('td', { className: 't' }, r.user) : null,
        h('td', { className: 't cb-mono', style: { fontSize: 12 } }, r.id),
        h('td', { className: 't' }, r.time),
        h('td', { style: { textAlign: 'right', width: 1, whiteSpace: 'nowrap' } }, h('button', { type: 'button', className: 'cb-link' }, '查看')));
    })));
  }

  window.ChatBI = Object.assign(window.ChatBI || {}, {
    Icon: Icon, Button: Button, Badge: Badge, ScopeTag: ScopeTag, Tabs: Tabs, Sidebar: Sidebar, Panel: Panel, TextField: TextField,
    Composer: Composer, ChatMessage: ChatMessage, ClarifyPrompt: ClarifyPrompt, ExplainCard: ExplainCard, SqlBlock: SqlBlock,
    ResultTable: ResultTable, ChartCard: ChartCard, FeedbackBar: FeedbackBar, Callout: Callout, CheckList: CheckList, Trace: Trace, RunList: RunList
  });
})();
