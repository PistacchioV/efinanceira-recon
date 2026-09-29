"""
fatos.py — importacao dos Fatos Relevantes do Plantao de Noticias da B3.

Fluxo (somente HTTP, sem Selenium):

  1. lista      GET  /PlantaoNoticias/Noticias/ListarTitulosNoticias
                     ?agencia=18&palavra=fato relevante&dataInicial=AAAA-MM-DD&dataFinal=AAAA-MM-DD
                -> JSON [{NwsMsg:{id, dateTime, headline}}]

  2. detalhe    GET  /PlantaoNoticias/Noticias/Detail?idNoticia=<id>&agencia=18&dataNoticia=<dt>
                -> <pre id="conteudoDetalhe"> com o texto e o link do documento na integra
                   (https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx?ID=<protocolo>)

  3. documento  POST https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx/ExibirPDF
                     {codigoInstituicao, numeroProtocolo, token, versaoCaptcha}
                -> {"d": "<pdf em base64>"}  (o mesmo que o visualizador da CVM consome)

O PDF e o texto extraido ficam em cache em disco (EDG_DATA, padrao ./data/fatos),
entao reimportar o mesmo periodo nao baixa nada de novo.

Se algum dia a CVM voltar a exigir captcha (hdnHabilitaCaptcha = 'S'), a etapa 3
falha e o card e gravado so com o texto da B3 e o link para o documento.
"""

import base64
import html
import io
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

B3_BASE = "https://sistemasweb.b3.com.br/PlantaoNoticias/Noticias"
CVM_VIEW = "https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx"
CVM_PDF = CVM_VIEW + "/ExibirPDF"
FNET_BASE = "https://fnet.bmfbovespa.com.br/fnet/publico"
AGENCIA = 18
PALAVRA_PADRAO = "Fato Relevante"
JANELA_MAX_DIAS = 30  # limite da propria B3

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("EDG_DATA") or os.path.join(BASE_DIR, "data", "fatos")
PDF_DIR = os.path.join(DATA_DIR, "pdf")
TXT_DIR = os.path.join(DATA_DIR, "txt")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")

_io_lock = threading.Lock()


class FatosError(Exception):
    """Erro tratado da importacao (mensagem vai direto para a tela)."""


# ------------------------------------------------------------------ http ---
def _get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _post_json(url, payload, referer, timeout=90):
    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# --------------------------------------------------------------- parsing ---
RE_TICKER = re.compile(r"\(([A-Z0-9]{3,6}(?:-[A-Z0-9]{1,4})?)\)")
RE_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{2,4})")
RE_FLAG = re.compile(r"\(([RCN])\)\s*$")
RE_LINK = re.compile(r"https?://[^\s<>\"]+")
RE_PROTOCOLO = re.compile(r"[?&]ID=(\d+)", re.I)

FLAGS = {"R": "Reapresentação", "C": "Documento cancelado", "N": "Norma / Notas"}


def data_br(iso):
    """AAAA-MM-DD -> DD/MM/AAAA (formato usado na tela)."""
    if not iso:
        return ""
    partes = str(iso)[:10].split("-")
    return "%s/%s/%s" % (partes[2], partes[1], partes[0]) if len(partes) == 3 else str(iso)


def data_iso(valor):
    """Aceita DD/MM/AAAA ou AAAA-MM-DD e devolve sempre AAAA-MM-DD."""
    valor = (valor or "").strip()
    if not valor:
        return None
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", valor)
    if m:
        return _iso(m.group(1), m.group(2), m.group(3))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", valor)
    return valor if m else None


def _iso(dia, mes, ano):
    ano = int(ano)
    if ano < 100:
        ano += 2000
    try:
        return date(ano, int(mes), int(dia)).isoformat()
    except ValueError:
        return None


def parse_headline(headline):
    """Quebra o titulo da B3 em empresa, ticker, data de referencia e marcador."""
    h = " ".join(headline.split())
    info = {"titulo": h, "empresa": h, "ticker": None, "data_referencia": None, "marcador": None}

    m = RE_FLAG.search(h)
    if m:
        info["marcador"] = FLAGS.get(m.group(1))
        h = h[: m.start()].strip()

    m = RE_TICKER.search(h)
    if m:
        info["ticker"] = m.group(1)
        info["empresa"] = h[: m.start()].strip(" -–")

    datas = RE_DATA.findall(h)
    if datas:
        info["data_referencia"] = _iso(*datas[-1])
    return info


def _limpar_texto(txt):
    txt = txt.replace("\r\n", "\n").replace("\xa0", " ")
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _resumo(texto, limite=420):
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    # descarta cabecalho institucional (nome da companhia, CNPJ, NIRE, titulo)
    corpo = []
    for l in linhas:
        up = _sem_acento(l).upper()
        if len(l) < 12 or l == l.upper():          # titulos e siglas em caixa alta
            continue
        if l.lower().startswith(("http", "www.")):  # link do documento na integra
            continue
        if re.match(r"^\([RCN]\)\s*=", l):          # legenda do plantao
            continue
        if any(k in up for k in ("CNPJ", "NIRE", "COMPANHIA ABERTA", "FATO RELEVANTE")):
            continue
        corpo.append(l)
    base = " ".join(corpo) if corpo else " ".join(linhas)
    base = RE_LINK.sub("", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base[: limite - 1] + "…" if len(base) > limite else base


def _assunto(texto):
    """Linha de subtitulo do documento (ex.: DISTRIBUICAO DE JUROS SOBRE CAPITAL PROPRIO)."""
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    marco = None
    for i, l in enumerate(linhas):
        janela = _sem_acento(" ".join(linhas[i : i + 2])).upper()
        if re.search(r"\bFATO\s+RELEVANTE\b", janela):
            marco = i
            break
    if marco is None:
        return None
    for prox in linhas[marco + 1 : marco + 5]:
        up = _sem_acento(prox).upper()
        if "RELEVANTE" in up or "FATO" == up.strip():
            continue
        if 12 <= len(prox) <= 120 and prox == prox.upper():
            if prox.split()[-1] in ("DE", "DA", "DO", "DAS", "DOS", "E", "-"):
                continue  # linha cortada no meio do titulo
            return prox
    return None


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------- b3 ----
def listar_titulos(palavra=PALAVRA_PADRAO, de=None, ate=None, agencia=AGENCIA):
    """Titulos do periodo. de/ate em AAAA-MM-DD (a B3 aceita no maximo 30 dias)."""
    ate = data_iso(ate) or date.today().isoformat()
    de = data_iso(de) or ate
    if (date.fromisoformat(ate) - date.fromisoformat(de)).days > JANELA_MAX_DIAS:
        raise FatosError("A B3 aceita no máximo %d dias por consulta." % JANELA_MAX_DIAS)

    url = "%s/ListarTitulosNoticias?%s" % (
        B3_BASE,
        urllib.parse.urlencode(
            {"agencia": agencia, "palavra": palavra, "dataInicial": de, "dataFinal": ate}
        ),
    )
    try:
        bruto = _get(url)
    except Exception as exc:
        raise FatosError("Não foi possível consultar o Plantão de Notícias da B3: %s" % exc)

    try:
        dados = json.loads(bruto.decode("utf-8", "replace"))
    except ValueError:
        raise FatosError("A B3 respondeu em formato inesperado (verifique a rede/proxy).")

    saida = []
    for item in dados or []:
        msg = item.get("NwsMsg") or {}
        if not msg.get("id"):
            continue
        saida.append(
            {
                "id": int(msg["id"]),
                "data_noticia": msg.get("dateTime") or "",
                "headline": msg.get("headline") or "",
            }
        )
    return saida


def detalhe(id_noticia, data_noticia, agencia=AGENCIA):
    """Texto da noticia e link do documento na integra."""
    url = "%s/Detail?%s" % (
        B3_BASE,
        urllib.parse.urlencode(
            {"idNoticia": id_noticia, "agencia": agencia, "dataNoticia": data_noticia}
        ),
    )
    pagina = _get(url).decode("utf-8", "replace")
    m = re.search(r'<pre[^>]*id="conteudoDetalhe"[^>]*>(.*?)</pre>', pagina, re.S)
    texto = _limpar_texto(html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) if m else ""

    link = protocolo = fonte = None
    for cand in RE_LINK.findall(texto):
        cand = cand.rstrip(".,;)")
        p = RE_PROTOCOLO.search(cand)
        if "rad.cvm.gov.br" in cand and p:
            link, protocolo, fonte = cand, p.group(1), "cvm"
            break
        if "fnet.bmfbovespa.com.br" in cand and p:
            link, protocolo, fonte = cand, p.group(1), "fnet"
            break
        link = link or cand
    return {"texto_b3": texto, "link": link, "protocolo": protocolo, "fonte": fonte}


# ------------------------------------------------------------------ cvm ----
def baixar_pdf(protocolo):
    """PDF do documento na integra (o mesmo que o visualizador da CVM carrega)."""
    referer = "%s?ID=%s&flnk" % (CVM_VIEW, protocolo)
    payload = (
        "{ codigoInstituicao: '2', numeroProtocolo: '%s', token: '', versaoCaptcha: ''}" % protocolo
    )
    resp = _post_json(CVM_PDF, payload, referer)
    d = (resp or {}).get("d") or ""
    if d == "V2":
        raise FatosError("A CVM está pedindo captcha para o protocolo %s." % protocolo)
    if d.startswith(":ERRO:"):
        raise FatosError(d.replace(":ERRO:", "").strip())
    if not d:
        raise FatosError("A CVM devolveu um documento vazio.")
    return base64.b64decode(d)


def baixar_fnet(protocolo):
    """PDF dos documentos de fundos, que a B3 publica no FNET em vez da CVM."""
    url = "%s/exibirDocumento?id=%s" % (FNET_BASE, protocolo)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/pdf,*/*",
            "Referer": "%s/visualizarDocumento?id=%s" % (FNET_BASE, protocolo),
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        conteudo = resp.read()
    if not conteudo.startswith(b"%PDF"):
        raise FatosError("O FNET não devolveu um PDF para o documento %s." % protocolo)
    return conteudo


def baixar_documento(fonte, protocolo, tentativas=3):
    """Baixa o documento na integra; a CVM e o FNET derrubam conexao com alguma frequencia."""
    ultimo = None
    for tentativa in range(tentativas):
        try:
            return baixar_fnet(protocolo) if fonte == "fnet" else baixar_pdf(protocolo)
        except FatosError:
            raise  # captcha ou erro de negocio: repetir nao ajuda
        except Exception as exc:
            ultimo = exc
            time.sleep(1.5 * (tentativa + 1))
    raise FatosError("Não consegui baixar o documento %s: %s" % (protocolo, ultimo))


def texto_pdf(conteudo):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise FatosError("Instale o pypdf (pip install -r requirements.txt) para ler os documentos.")
    leitor = PdfReader(io.BytesIO(conteudo))
    paginas = [(p.extract_text() or "") for p in leitor.pages]
    return _limpar_texto("\n".join(paginas)), len(leitor.pages)


# ---------------------------------------------------------------- cache ----
def _garantir_dirs():
    for d in (DATA_DIR, PDF_DIR, TXT_DIR):
        os.makedirs(d, exist_ok=True)


def carregar_index():
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            dados = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in (dados.get("fatos") or {}).items()}


def _salvar_index(index):
    _garantir_dirs()
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"atualizado_em": datetime.now().isoformat(timespec="seconds"), "fatos": index},
                  fh, ensure_ascii=False, indent=1)
    os.replace(tmp, INDEX_PATH)


def caminho_pdf(id_noticia):
    return os.path.join(PDF_DIR, "%s.pdf" % id_noticia)


def texto_completo(id_noticia):
    try:
        with open(os.path.join(TXT_DIR, "%s.txt" % id_noticia), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ------------------------------------------------------------ importacao ---
def _montar_card(titulo):
    info = parse_headline(titulo["headline"])
    det = detalhe(titulo["id"], titulo["data_noticia"])

    card = {
        "id": titulo["id"],
        "titulo": info["titulo"],
        "empresa": info["empresa"],
        "ticker": info["ticker"],
        "marcador": info["marcador"],
        "tipo": "Fato Relevante",
        "data_noticia": titulo["data_noticia"],
        "data_referencia": info["data_referencia"] or (titulo["data_noticia"][:10] or None),
        "protocolo": det["protocolo"],
        "fonte": det["fonte"],
        "link": det["link"],
        "texto_b3": det["texto_b3"],
        "assunto": None,
        "resumo": None,
        "paginas": 0,
        "tem_pdf": False,
        "erro_documento": None,
        "importado_em": datetime.now().isoformat(timespec="seconds"),
    }

    texto = ""
    if det["protocolo"]:
        try:
            pdf = baixar_documento(det["fonte"], det["protocolo"])
            texto, paginas = texto_pdf(pdf)
            _garantir_dirs()
            with open(caminho_pdf(card["id"]), "wb") as fh:
                fh.write(pdf)
            card["paginas"] = paginas
            card["tem_pdf"] = True
        except Exception as exc:
            card["erro_documento"] = str(exc)
    else:
        card["erro_documento"] = "A notícia não traz link para o documento na íntegra."

    if not texto:
        texto = det["texto_b3"]
    _garantir_dirs()
    with open(os.path.join(TXT_DIR, "%s.txt" % card["id"]), "w", encoding="utf-8") as fh:
        fh.write(texto)

    card["assunto"] = _assunto(texto)
    card["resumo"] = _resumo(texto)
    return card


def importar(palavra=PALAVRA_PADRAO, de=None, ate=None, limite=200, reimportar=False,
             progresso=None, workers=4):
    """Importa os fatos do periodo e grava no cache. Devolve um resumo da rodada."""
    titulos = listar_titulos(palavra, de, ate)
    index = carregar_index()

    def falta(t):
        antigo = index.get(str(t["id"]))
        return reimportar or antigo is None or bool(antigo.get("erro_documento"))

    pendentes = [t for t in titulos if falta(t)][:limite]
    total = len(pendentes)
    if progresso:
        progresso(0, total, "%d notícias no período · %d para importar" % (len(titulos), total))

    novos, erros = [], []
    feitos = 0

    def tarefa(t):
        try:
            return _montar_card(t)
        except Exception as exc:
            return {"_erro": "%s — %s" % (t["headline"].strip()[:60], exc)}

    if total:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futuros = [pool.submit(tarefa, t) for t in pendentes]
            for fut in as_completed(futuros):
                card = fut.result()
                feitos += 1
                if "_erro" in card:
                    erros.append(card["_erro"])
                else:
                    with _io_lock:
                        index[str(card["id"])] = card
                    novos.append(card["id"])
                if progresso:
                    progresso(feitos, total, None)
        _salvar_index(index)

    return {
        "encontrados": len(titulos),
        "importados": len(novos),
        "ja_existiam": len(titulos) - total,
        "erros": erros,
        "periodo": {"de": de, "ate": ate, "palavra": palavra},
    }


def listar_cards(q=None, de=None, ate=None, apenas_com_documento=False):
    """Cards do cache, do mais recente para o mais antigo, filtrados pela data de referência."""
    cards = list(carregar_index().values())
    de, ate = data_iso(de), data_iso(ate)

    if de:
        cards = [c for c in cards if (c.get("data_referencia") or "") >= de]
    if ate:
        cards = [c for c in cards if (c.get("data_referencia") or "") <= ate]
    if apenas_com_documento:
        cards = [c for c in cards if c.get("tem_pdf")]
    if q:
        alvo = _sem_acento(q).lower().strip()
        def bate(c):
            campos = " ".join(str(c.get(k) or "") for k in
                              ("titulo", "empresa", "ticker", "assunto", "resumo", "protocolo"))
            return alvo in _sem_acento(campos).lower()
        cards = [c for c in cards if bate(c)]

    cards.sort(key=lambda c: (c.get("data_referencia") or "", c.get("data_noticia") or ""), reverse=True)
    return cards


def periodo_padrao(dias=1):
    """Periodo inicial da tela: por padrao so o dia de hoje (de = ate = hoje)."""
    hoje = date.today()
    return (hoje - timedelta(days=dias - 1)).isoformat(), hoje.isoformat()
