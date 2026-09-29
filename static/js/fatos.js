/* fatos.js — Fatos Relevantes: importacao da B3, filtros e cards. */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var toast = window.EDG.toast;

  var state = { cards: [], total: 0, comDoc: 0, doc: '0', timer: null, poll: null, abertos: {} };

  if (window.gsap) {
    gsap.set('.hero-anim-item', { opacity: 0, y: 40, filter: 'blur(12px)' });
    gsap.to('.hero-anim-item', { opacity: 1, y: 0, filter: 'blur(0px)', duration: 1.2, stagger: 0.15, ease: 'power3.out', delay: 0.1 });
  }

  // ----------------------------------------------------------------- utils
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function dataBR(iso) {
    if (!iso) return '—';
    var p = String(iso).slice(0, 10).split('-');
    return p.length === 3 ? p[2] + '/' + p[1] + '/' + p[0] : iso;
  }

  function dataHoraBR(dt) {
    if (!dt) return '—';
    var p = String(dt).split(' ');
    return dataBR(p[0]) + (p[1] ? ' ' + p[1].slice(0, 5) : '');
  }

  // os campos de data sao texto com mascara: o seletor nativo mostraria o
  // formato do idioma do navegador (mm/dd/aaaa em maquina em ingles)
  function paraIso(v) {
    var m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(String(v || '').trim());
    if (!m) return '';
    var d = new Date(+m[3], +m[2] - 1, +m[1]);
    if (d.getDate() !== +m[1] || d.getMonth() !== +m[2] - 1) return '';
    return m[3] + '-' + m[2] + '-' + m[1];
  }

  function mascaraData(el) {
    el.addEventListener('input', function () {
      var d = el.value.replace(/\D/g, '').slice(0, 8);
      var out = d.slice(0, 2);
      if (d.length > 2) out += '/' + d.slice(2, 4);
      if (d.length > 4) out += '/' + d.slice(4, 8);
      el.value = out;
    });
    el.addEventListener('blur', function () {
      el.classList.toggle('is-invalid', !!el.value && !paraIso(el.value));
    });
  }
  $$('.data-br').forEach(mascaraData);

  // ------------------------------------------------------------ importacao
  function setImportando(on) {
    $('#impBtn').disabled = on;
    $('#impLbl').textContent = on ? 'Importando...' : 'Importar fatos';
    $('#impIco').innerHTML = on
      ? '<span class="spinner"></span>'
      : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M3 17v2a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2"/></svg>';
    $('#impProgWrap').classList.toggle('hidden-soft', !on);
  }

  function pintarStatus(s) {
    $('#impMsg').textContent = s.msg || '—';
    $('#impCont').textContent = s.feitos + ' / ' + s.total;
    $('#impBar').style.width = (s.total ? (s.feitos / s.total) * 100 : 0) + '%';
  }

  function fimDaImportacao(s) {
    clearInterval(state.poll);
    state.poll = null;
    setImportando(false);

    if (s.erro) {
      $('#impResumo').innerHTML = '<span class="t-danger">' + esc(s.erro) + '</span>';
      toast(s.erro);
      return;
    }
    var r = s.resumo || {};
    $('#impResumo').innerHTML =
      '<span class="t-fg2">' + r.encontrados + '</span> notícias no período · ' +
      '<span class="t-accent">' + r.importados + '</span> importadas · ' +
      '<span class="t-fg3">' + r.ja_existiam + '</span> já estavam no cache.';

    var erros = r.erros || [];
    var box = $('#impErros');
    box.classList.toggle('hidden-soft', !erros.length);
    if (erros.length) {
      box.innerHTML = '<div class="text-xs t-warn mb-1">' + erros.length + ' documento(s) não vieram:</div>' +
        erros.map(function (e) { return '<div class="text-[11px] t-faint leading-relaxed">• ' + esc(e) + '</div>'; }).join('');
    }
    toast('Importação concluída: ' + r.importados + ' novo(s).', true);
    carregar();
  }

  function importar() {
    var corpo = {
      palavra: $('#impPalavra').value.trim(),
      de: paraIso($('#impDe').value),
      ate: paraIso($('#impAte').value),
      reimportar: $('#impRefazer').checked
    };
    if (!corpo.de || !corpo.ate) { toast('Informe o período no formato dd/mm/aaaa.'); return; }
    if (corpo.de > corpo.ate) { toast('A data inicial não pode ser maior que a final.'); return; }

    setImportando(true);
    pintarStatus({ msg: 'Consultando a B3...', feitos: 0, total: 0 });

    fetch('/api/fatos/importar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(corpo)
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'Falha ao iniciar a importação.'); return j; });
    }).then(function () {
      state.poll = setInterval(function () {
        fetch('/api/fatos/importar/status').then(function (r) { return r.json(); }).then(function (s) {
          pintarStatus(s);
          if (!s.rodando) fimDaImportacao(s);
        });
      }, 700);
    }).catch(function (e) {
      setImportando(false);
      toast(e.message);
    });
  }

  // ----------------------------------------------------------------- cards
  function cardHTML(c) {
    var doc = c.tem_pdf
      ? '<a class="btn btn-ghost btn-sm" href="/api/fatos/' + c.id + '/pdf" target="_blank" rel="noopener">Documento (' + c.paginas + 'p)</a>'
      : '';
    var origem = c.link
      ? '<a class="btn btn-ghost btn-sm" href="' + esc(c.link) + '" target="_blank" rel="noopener">Fonte</a>'
      : '';
    var alerta = c.erro_documento
      ? '<div class="text-[11px] t-warn">' + esc(c.erro_documento) + '</div>'
      : '';
    var marcador = c.marcador ? '<span class="tag tag-warn">' + esc(c.marcador) + '</span>' : '';
    var aberto = state.abertos[c.id];

    return '' +
      '<article class="fato" data-id="' + c.id + '">' +
        '<div class="fato-topo">' +
          '<div class="min-w-0">' +
            '<div class="flex items-center gap-2 mb-1.5 flex-wrap">' +
              (c.ticker ? '<span class="fato-ticker">' + esc(c.ticker) + '</span>' : '') +
              marcador +
            '</div>' +
            '<h3 class="fato-empresa">' + esc(c.empresa || c.titulo) + '</h3>' +
            (c.assunto ? '<div class="fato-assunto mt-1">' + esc(c.assunto) + '</div>' : '') +
          '</div>' +
          '<span class="fato-ref" title="Data de referência (usada na busca)">' +
            '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>' +
            dataBR(c.data_referencia) +
          '</span>' +
        '</div>' +
        '<p class="fato-resumo">' + esc(c.resumo || c.titulo) + '</p>' +
        alerta +
        '<div class="fato-meta">' +
          '<span>publicado <b>' + dataHoraBR(c.data_noticia) + '</b></span>' +
          (c.protocolo ? '<span>protocolo <b>' + esc(c.protocolo) + '</b></span>' : '') +
          '<span>id <b>' + c.id + '</b></span>' +
        '</div>' +
        '<div class="fato-acoes">' +
          '<button class="btn btn-ghost btn-sm" data-ver="' + c.id + '">' + (aberto ? 'Ocultar texto' : 'Ver texto') + '</button>' +
          doc + origem +
        '</div>' +
        (aberto ? '<div class="fato-texto">' + esc(aberto) + '</div>' : '') +
      '</article>';
  }

  function render() {
    var grid = $('#grid');
    grid.innerHTML = state.cards.map(cardHTML).join('');
    grid.classList.toggle('hidden-soft', !state.cards.length);
    $('#vazio').classList.toggle('hidden-soft', !!state.cards.length);

    var empresas = {};
    state.cards.forEach(function (c) { if (c.ticker || c.empresa) empresas[c.ticker || c.empresa] = 1; });
    $('[data-k="total"]').textContent = state.total;
    $('[data-k="exibindo"]').textContent = state.cards.length;
    $('[data-k="com_doc"]').textContent = state.cards.filter(function (c) { return c.tem_pdf; }).length;
    $('[data-k="empresas"]').textContent = Object.keys(empresas).length;

    $$('[data-ver]').forEach(function (b) {
      b.addEventListener('click', function () { alternarTexto(parseInt(b.getAttribute('data-ver'), 10)); });
    });
  }

  // abre/fecha no proprio card, sem redesenhar a grade (nao perde a rolagem)
  function alternarTexto(id) {
    var art = $('.fato[data-id="' + id + '"]');
    var btn = $('[data-ver="' + id + '"]', art);

    if (state.abertos[id]) {
      delete state.abertos[id];
      var box = $('.fato-texto', art);
      if (box) box.remove();
      btn.textContent = 'Ver texto';
      return;
    }

    btn.disabled = true;
    fetch('/api/fatos/' + id).then(function (r) { return r.json(); }).then(function (c) {
      btn.disabled = false;
      if (c.error) { toast(c.error); return; }
      state.abertos[id] = c.texto || 'Sem texto extraído para este documento.';
      var el = document.createElement('div');
      el.className = 'fato-texto';
      el.textContent = state.abertos[id];
      art.appendChild(el);
      btn.textContent = 'Ocultar texto';
    }).catch(function () {
      btn.disabled = false;
      toast('Não foi possível abrir o texto do fato.');
    });
  }

  function carregar() {
    var p = new URLSearchParams();
    var q = $('#busca').value.trim();
    if (q) p.set('q', q);
    if (paraIso($('#refDe').value)) p.set('de', paraIso($('#refDe').value));
    if (paraIso($('#refAte').value)) p.set('ate', paraIso($('#refAte').value));
    if (state.doc === '1') p.set('com_documento', '1');

    fetch('/api/fatos?' + p.toString()).then(function (r) { return r.json(); }).then(function (j) {
      state.cards = j.cards || [];
      // o total do cache so muda com importacao/filtro limpo; guarda o maior visto
      if (!p.toString()) state.total = j.total;
      else state.total = Math.max(state.total, j.total);
      render();
    }).catch(function () { toast('Falha ao carregar os fatos do cache.'); });
  }

  function agendarCarga() {
    clearTimeout(state.timer);
    state.timer = setTimeout(carregar, 220);
  }

  // ------------------------------------------------------------------ liga
  $('#impBtn').addEventListener('click', importar);
  $('#busca').addEventListener('input', agendarCarga);
  $('#refDe').addEventListener('input', agendarCarga);
  $('#refAte').addEventListener('input', agendarCarga);
  $('#limparFiltros').addEventListener('click', function () {
    $('#busca').value = ''; $('#refDe').value = ''; $('#refAte').value = '';
    $$('.data-br').forEach(function (el) { el.classList.remove('is-invalid'); });
    state.doc = '0';
    $$('#segDoc button').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-doc') === '0'); });
    carregar();
  });
  $$('#segDoc button').forEach(function (b) {
    b.addEventListener('click', function () {
      state.doc = b.getAttribute('data-doc');
      $$('#segDoc button').forEach(function (o) { o.classList.toggle('active', o === b); });
      carregar();
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
      e.preventDefault(); $('#busca').focus();
    }
  });

  // se a aba abrir com uma importacao em andamento, acompanha
  fetch('/api/fatos/importar/status').then(function (r) { return r.json(); }).then(function (s) {
    if (s.rodando) {
      setImportando(true);
      pintarStatus(s);
      state.poll = setInterval(function () {
        fetch('/api/fatos/importar/status').then(function (r) { return r.json(); }).then(function (x) {
          pintarStatus(x);
          if (!x.rodando) fimDaImportacao(x);
        });
      }, 700);
    }
  });

  carregar();
})();
