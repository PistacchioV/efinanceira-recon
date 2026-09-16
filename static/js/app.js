/* app.js — dropzone, batimento, tabela de analise, filtros, exportacao e tema */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var state = {
    files: { TRD: null, PTP: null },
    data: null,
    filter: 'all',
    query: '',
    page: 1,
    pageSize: 50
  };

  var fmtNum = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var fmtInt = new Intl.NumberFormat('pt-BR');

  // ------------------------------------------------------------------ tema
  var root = document.documentElement;
  var meta = document.querySelector('meta[name="theme-color"]');
  function applyTheme(t) {
    root.classList.remove('dark', 'light');
    root.classList.add(t);
    if (meta) meta.setAttribute('content', t === 'dark' ? '#05080A' : '#F3F4F0');
    try { localStorage.setItem('efin-theme', t); } catch (e) {}
  }
  applyTheme(root.classList.contains('light') ? 'light' : 'dark');
  $('#themeToggle').addEventListener('click', function () {
    applyTheme(root.classList.contains('dark') ? 'light' : 'dark');
  });

  // ------------------------------------------------------------ animacoes
  if (window.gsap) {
    gsap.set('.hero-anim-item', { opacity: 0, y: 40, filter: 'blur(12px)' });
    gsap.to('.hero-anim-item', { opacity: 1, y: 0, filter: 'blur(0px)', duration: 1.2, stagger: 0.15, ease: 'power3.out', delay: 0.1 });
  }

  // ----------------------------------------------------------------- toast
  var toastTimer;
  function toast(msg, ok) {
    var el = $('#toast');
    el.textContent = msg;
    el.classList.toggle('ok', !!ok);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('show'); }, ok ? 2800 : 6000);
  }

  // -------------------------------------------------------------- arquivos
  var FILE_RE = /(\d{4})\s*[_\-\s]\s*(TRD|PTP)/i;

  function sizeLabel(b) {
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    return (b / 1048576).toFixed(1) + ' MB';
  }

  function addFiles(list) {
    var arr = Array.prototype.slice.call(list || []);
    var rejected = 0;
    arr.forEach(function (f) {
      if (!/\.(xlsx|xlsm)$/i.test(f.name)) { rejected++; return; }
      var m = f.name.match(FILE_RE);
      var side = m ? m[2].toUpperCase() : null;
      if (!side) side = !state.files.TRD ? 'TRD' : (!state.files.PTP ? 'PTP' : 'TRD');
      state.files[side] = f;
    });
    if (rejected) toast('Só arquivos .xlsx ou .xlsm são aceitos.');
    renderSlots();
    if (state.files.TRD && state.files.PTP && arr.length) run();
  }

  function renderSlots() {
    ['TRD', 'PTP'].forEach(function (side) {
      var f = state.files[side];
      var slot = $('#slot-' + side);
      slot.classList.toggle('filled', !!f);
      $('[data-clear="' + side + '"]', slot).classList.toggle('hidden-soft', !f);
      var m = f && f.name.match(FILE_RE);
      $('[data-name]', slot).textContent = f ? f.name : 'Aguardando MMAA_' + side;
      $('[data-meta]', slot).textContent = f
        ? sizeLabel(f.size) + (m ? ' · competência ' + m[1] : ' · lado inferido')
        : (side === 'TRD' ? 'coluna J · INOA-XXXXXXXXXX' : 'coluna A · instrument_id');
    });
    $('#runBtn').disabled = !(state.files.TRD && state.files.PTP);
    var mm = (state.files.TRD && (state.files.TRD.name.match(FILE_RE) || [])[1]) ||
             (state.files.PTP && (state.files.PTP.name.match(FILE_RE) || [])[1]);
    if (!state.data) $('#outName').textContent = (mm || 'MMAA') + '_e-financeira_recon.xlsx';
  }

  var dz = $('#dropzone');
  var input = $('#fileInput');
  dz.addEventListener('click', function () { input.click(); });
  dz.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  input.addEventListener('change', function () { addFiles(input.files); input.value = ''; });
  ['dragenter', 'dragover'].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add('is-over'); });
  });
  ['dragleave', 'dragend', 'drop'].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove('is-over'); });
  });
  dz.addEventListener('drop', function (e) { addFiles(e.dataTransfer.files); });
  // soltar fora da area nao deve abrir o arquivo no navegador
  window.addEventListener('dragover', function (e) { e.preventDefault(); });
  window.addEventListener('drop', function (e) { if (!dz.contains(e.target)) { e.preventDefault(); addFiles(e.dataTransfer.files); } });

  $$('[data-clear]').forEach(function (b) {
    b.addEventListener('click', function () { state.files[b.getAttribute('data-clear')] = null; renderSlots(); });
  });
  $('#resetBtn').addEventListener('click', function () {
    state.files = { TRD: null, PTP: null };
    state.data = null;
    renderSlots();
    renderAll();
  });
  $('#runBtn').addEventListener('click', run);

  // ------------------------------------------------------------- batimento
  var busy = false;
  function setBusy(on) {
    busy = on;
    $('#runBtn').disabled = on || !(state.files.TRD && state.files.PTP);
    $('#runLbl').textContent = on ? 'Batendo...' : 'Executar batimento';
    $('#runIco').innerHTML = on ? '<span class="spinner"></span>' :
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M8 21H3v-5"/><path d="M21 3 14 10"/><path d="m3 21 7-7"/></svg>';
    $('#tableWrap').classList.toggle('scanline', on);
  }

  function run() {
    if (busy || !state.files.TRD || !state.files.PTP) return;
    var fd = new FormData();
    fd.append('trd', state.files.TRD, state.files.TRD.name);
    fd.append('ptp', state.files.PTP, state.files.PTP.name);
    setBusy(true);
    fetch('/api/reconcile', { method: 'POST', body: fd })
      .then(function (r) {
        return r.json().catch(function () { return { error: 'Resposta inválida do servidor (' + r.status + ').' }; })
          .then(function (j) { if (!r.ok || j.error) throw new Error(j.error || ('Erro ' + r.status)); return j; });
      })
      .then(function (j) {
        state.data = j;
        state.page = 1;
        state.filter = 'all';
        state.query = '';
        $('#search').value = '';
        renderAll();
        toast('Batimento concluído: ' + fmtInt.format(j.summary.total) + ' instrumentos.', true);
        var target = $('#analise');
        if (target) window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 70, behavior: 'smooth' });
      })
      .catch(function (err) { toast(err.message || String(err)); })
      .then(function () { setBusy(false); });
  }

  // ------------------------------------------------------------ exportacao
  $('#exportBtn').addEventListener('click', function () {
    if (!state.data) return;
    var a = document.createElement('a');
    a.href = '/api/export/' + encodeURIComponent(state.data.token);
    a.download = state.data.summary.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  });

  // ------------------------------------------------------------ filtros
  function matchesFilter(r) {
    switch (state.filter) {
      case 'div': return r.valores === 'Divergente';
      case 'ok': return r.valores === 'OK';
      case 'allege_ptp': return r.status === 'Allege on PTP side';
      case 'allege_trd': return r.status === 'Allege on TRD side';
      default:
        if (state.filter.indexOf('field:') === 0) return r.divs.indexOf(state.filter.slice(6)) >= 0;
        return true;
    }
  }
  function filtered() {
    if (!state.data) return [];
    var q = state.query.replace(/\D/g, '');
    var qt = state.query.trim().toLowerCase();
    return state.data.rows.filter(function (r) {
      if (!matchesFilter(r)) return false;
      if (!qt) return true;
      if (q && r.key.indexOf(q.replace(/^0+/, '') || '0') >= 0) return true;
      return String(r.id_trd || '').toLowerCase().indexOf(qt) >= 0 || String(r.id_ptp || '').toLowerCase().indexOf(qt) >= 0;
    });
  }

  function setFilter(f) {
    state.filter = f;
    state.page = 1;
    renderFilters();
    renderTable();
  }
  $$('#filters [data-filter]').forEach(function (b) { b.addEventListener('click', function () { setFilter(b.getAttribute('data-filter')); }); });
  $$('#kpis [data-filter]').forEach(function (b) { b.addEventListener('click', function () { if (state.data) setFilter(b.getAttribute('data-filter')); }); });

  var searchTimer;
  $('#search').addEventListener('input', function (e) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { state.query = e.target.value; state.page = 1; renderTable(); }, 120);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); $('#search').focus(); }
  });
  $('#pageSize').addEventListener('change', function (e) { state.pageSize = +e.target.value; state.page = 1; renderTable(); });
  $('#prevPage').addEventListener('click', function () { if (state.page > 1) { state.page--; renderTable(true); } });
  $('#nextPage').addEventListener('click', function () { state.page++; renderTable(true); });

  // -------------------------------------------------------------- render
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  function renderAll() {
    renderSummary();
    renderFilters();
    renderTable();
  }

  function renderSummary() {
    var d = state.data;
    var s = d && d.summary;
    $('#exportBtn').disabled = !d;
    $('#outName').textContent = s ? s.filename : 'MMAA_e-financeira_recon.xlsx';
    $('#analiseSub').innerHTML = s
      ? 'Competência <span class="font-mono t-fg2">' + esc(s.mmaa) + '</span> · <span class="font-mono">' + esc(s.trd_file) + '</span> × <span class="font-mono">' + esc(s.ptp_file) + '</span> · gerado em ' + esc(s.gerado_em)
      : 'Os resultados aparecem aqui assim que os dois arquivos forem batidos.';

    var total = s ? s.total : 0;
    ['total', 'match_ok', 'divergentes', 'allege_ptp', 'allege_trd'].forEach(function (k) {
      $('[data-k="' + k + '"]').textContent = s ? fmtInt.format(s[k]) : '—';
      var bar = $('[data-bar="' + k + '"]');
      if (bar) bar.style.width = s && total ? Math.max(s[k] ? 2 : 0, (s[k] / total) * 100) + '%' : '0';
      $$('[data-c="' + k + '"]').forEach(function (c) { c.textContent = s ? fmtInt.format(s[k]) : '0'; });
    });
    $('[data-k="linhas"]').textContent = s ? 'TRD ' + fmtInt.format(s.trd_linhas) + ' · PTP ' + fmtInt.format(s.ptp_linhas) : 'TRD — · PTP —';
    $('[data-k="taxa"]').textContent = s && total ? (s.match_ok / total * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + '%' : '—';

    var w = $('#warnings');
    if (d && d.warnings.length) {
      w.innerHTML = '<div class="inner" style="border-color:var(--warn-ring)"><div class="px-5 py-3 flex flex-col gap-1.5 text-xs">' +
        d.warnings.map(function (x) { return '<div class="flex gap-2"><span class="t-warn">!</span><span class="t-fg3">' + esc(x) + '</span></div>'; }).join('') +
        '</div></div>';
      w.classList.remove('hidden-soft');
    } else {
      w.classList.add('hidden-soft');
    }

    var fb = $('#fieldBreak');
    if (d) {
      var chips = d.groups.slice(1).map(function (g, i) {
        var n = s.div_por_campo[g.label] || 0;
        var key = 'd' + (i + 1);
        return '<button class="tag ' + (n ? 'tag-bad' : 'tag-ok') + '" data-field="' + key + '" title="Filtrar divergências neste campo">' +
          esc(g.label) + ' <span class="font-mono">' + fmtInt.format(n) + '</span></button>';
      }).join('');
      $('#fieldChips').innerHTML = '<span class="text-[11px] t-faint self-center mr-1">Divergências por campo</span>' + chips;
      $$('#fieldChips [data-field]').forEach(function (b) {
        b.addEventListener('click', function () { setFilter('field:' + b.getAttribute('data-field')); });
      });
      fb.classList.remove('hidden-soft');
    } else {
      fb.classList.add('hidden-soft');
    }
  }

  function renderFilters() {
    $$('#filters [data-filter]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-filter') === state.filter); });
    $$('#kpis [data-filter]').forEach(function (b) { b.classList.toggle('is-active', !!state.data && b.getAttribute('data-filter') === state.filter); });
    $$('#fieldChips [data-field]').forEach(function (b) {
      b.style.outline = state.filter === 'field:' + b.getAttribute('data-field') ? '1px solid currentColor' : '';
    });
  }

  function renderHead(d) {
    var g = '<tr class="g">' + d.groups.map(function (grp, i) {
      // a coluna A e fixa: o grupo dos IDs nao pode ter colspan, senao cobre a B
      if (i === 0) return '<th class="stick">' + esc(grp.label) + '</th><th style="border-left:0"></th>';
      var kind = grp.kind ? ' <span class="t-faint normal-case tracking-normal font-mono">· ' + (grp.kind === 'date' ? 'dias' : grp.kind === 'code' ? 'código' : 'valor') + '</span>' : '';
      return '<th colspan="' + grp.span + '" class="first-of-group">' + esc(grp.label) + kind + '</th>';
    }).join('') + '<th class="first-of-group">Observação</th><th class="status-col" rowspan="1">Status</th></tr>';

    var c = '<tr class="c">' + d.columns.map(function (col, i) {
      var cls = 'src-' + col.src + (i === 0 ? ' stick' : '') + (i > 1 && col.src === 'TRD' ? ' first-of-group' : '') + (i === 1 ? '' : '');
      var srcLabel = col.src === 'DIF' ? 'TRD − PTP (' + col.letter + ')' : col.src + ' · col ' + col.letter;
      return '<th class="' + cls + '"><span class="src">' + srcLabel + '</span><span class="letter">' + col.out + '</span>' + esc(col.label) + '</th>';
    }).join('') + '<th class="first-of-group"><span class="src t-faint">data</span>observacao</th><th class="status-col"><span class="src t-faint">batimento</span>status</th></tr>';
    return g + c;
  }

  function fmtCell(col, v, r) {
    var cls = ['src-' + col.src];
    var txt;
    if (v === null || v === undefined || v === '') {
      cls.push('empty');
      txt = '—';
    } else if (typeof v === 'number') {
      cls.push('num');
      if (col.src === 'DIF' && col.kind === 'date') txt = (v > 0 ? '+' : '') + fmtInt.format(v) + ' d';
      else if (col.kind === 'id') txt = String(v);
      else txt = (col.src === 'DIF' && v > 0 ? '+' : '') + fmtNum.format(v);
      if (col.src === 'DIF' && v === 0) cls.push('zero');
    } else {
      txt = String(v);
      if (col.kind === 'date' || col.kind === 'code') cls.push('num');
    }
    if (col.kind === 'id') cls.push('id');
    if (col.src === 'DIF' && r.divs.indexOf(col.key) >= 0) cls.push('bad');
    else if (col.src === 'DIF' && typeof v === 'number' && v !== 0) cls.push('tol');
    if (r.status === 'Allege on PTP side' && col.src === 'PTP') cls.push('side-missing');
    if (r.status === 'Allege on TRD side' && col.src === 'TRD') cls.push('side-missing');
    if (col.src === 'TRD' && col.group > 0) cls.push('first-of-group');
    if (col.key === 'id_trd') cls.push('stick');
    return '<td class="' + cls.join(' ') + '">' + esc(txt) + '</td>';
  }

  function obsCell(r) {
    return r.observacao
      ? '<td class="obs first-of-group">' + esc(r.observacao) + '</td>'
      : '<td class="empty first-of-group">—</td>';
  }

  function statusCell(r) {
    var tag;
    if (r.status === 'Match') {
      tag = r.valores === 'OK'
        ? '<span class="tag tag-ok">✓ Match</span>'
        : '<span class="tag tag-bad">Divergente · ' + r.divs.length + '</span>';
    } else {
      tag = '<span class="tag tag-warn">' + esc(r.status) + '</span>';
    }
    return '<td class="status-col">' + tag + '</td>';
  }

  function renderTable(scrollTop) {
    var d = state.data;
    var table = $('#reconTable');
    $('#emptyState').classList.toggle('hidden-soft', !!d);
    table.classList.toggle('hidden-soft', !d);
    if (!d) {
      $('#pageInfo').textContent = '—';
      $('#pageNum').textContent = '0 / 0';
      $('#prevPage').disabled = $('#nextPage').disabled = true;
      return;
    }
    if (!table.tHead.rows.length) table.tHead.innerHTML = renderHead(d);
    else if (table.getAttribute('data-token') !== d.token) table.tHead.innerHTML = renderHead(d);
    table.setAttribute('data-token', d.token);

    var rows = filtered();
    var pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    if (state.page > pages) state.page = pages;
    var start = (state.page - 1) * state.pageSize;
    var slice = rows.slice(start, start + state.pageSize);

    if (!slice.length) {
      table.tBodies[0].innerHTML = '<tr><td colspan="' + (d.columns.length + 2) + '" class="empty"><div class="py-10 text-center t-faint">Nenhum instrumento neste filtro.</div></td></tr>';
    } else {
      table.tBodies[0].innerHTML = slice.map(function (r) {
        var cls = r.status !== 'Match' ? 'allege' : (r.divs.length ? 'div' : '');
        return '<tr class="' + cls + '">' + d.columns.map(function (col) { return fmtCell(col, r[col.key], r); }).join('') + obsCell(r) + statusCell(r) + '</tr>';
      }).join('');
    }

    $('#pageInfo').textContent = rows.length
      ? 'Mostrando ' + fmtInt.format(start + 1) + '–' + fmtInt.format(start + slice.length) + ' de ' + fmtInt.format(rows.length) + (rows.length !== d.rows.length ? ' (filtrados de ' + fmtInt.format(d.rows.length) + ')' : '')
      : '0 resultados';
    $('#pageNum').textContent = state.page + ' / ' + pages;
    $('#prevPage').disabled = state.page <= 1;
    $('#nextPage').disabled = state.page >= pages;
    if (scrollTop) $('#tableWrap').scrollTop = 0;
  }

  renderSlots();
  renderAll();
})();
