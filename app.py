"""
app.py — servidor Flask do EDG Tracker.

  e-Financeira Recon
    GET  /                         pagina do batimento
    POST /api/reconcile            recebe trd + ptp (multipart) e devolve a analise
    GET  /api/export/<token>       baixa o MMAA_e-financeira_recon.xlsx da analise

  Fatos Relevantes
    GET  /fatos                    pagina dos fatos relevantes
    GET  /api/fatos                cards do cache (filtra por data de referencia e texto)
    POST /api/fatos/importar       dispara a importacao do periodo (B3 -> CVM/FNET)
    GET  /api/fatos/importar/status andamento da importacao em curso
    GET  /api/fatos/<id>           card com o texto integral do documento
    GET  /api/fatos/<id>/pdf       PDF do documento na integra
"""

import os
import threading
import uuid
from collections import OrderedDict

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

import fatos
import recon

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_RESULTADOS = 20

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

# resultados recentes em memoria, para o export nao exigir novo upload
_resultados = OrderedDict()
_lock = threading.Lock()


def _guardar(result):
    token = uuid.uuid4().hex
    with _lock:
        _resultados[token] = result
        while len(_resultados) > MAX_RESULTADOS:
            _resultados.popitem(last=False)
    return token


@app.route("/")
def index():
    return render_template("index.html", pagina="recon")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "favicon.ico", mimetype="image/x-icon")


@app.route("/api/reconcile", methods=["POST"])
def api_reconcile():
    trd = request.files.get("trd")
    ptp = request.files.get("ptp")
    if not trd or not ptp:
        return jsonify(error="Envie os dois arquivos: MMAA_TRD e MMAA_PTP."), 400
    try:
        result = recon.reconcile((trd.filename, trd.read()), (ptp.filename, ptp.read()))
    except recon.ReconError as exc:
        return jsonify(error=str(exc)), 422
    except Exception as exc:  # pragma: no cover - rede de seguranca
        app.logger.exception("falha no batimento")
        return jsonify(error="Falha inesperada no batimento: %s" % exc), 500

    payload = recon.to_json(result)
    payload["token"] = _guardar(result)
    return jsonify(payload)


@app.route("/api/export/<token>")
def api_export(token):
    with _lock:
        result = _resultados.get(token)
    if result is None:
        return jsonify(error="Analise expirada. Rode o batimento de novo."), 404
    buf = recon.to_excel(result)
    return send_file(
        buf,
        as_attachment=True,
        download_name=result["summary"]["filename"],
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------- fatos relevantes
# uma importacao por vez; a tela acompanha pelo /api/fatos/importar/status
_job = {"rodando": False, "feitos": 0, "total": 0, "msg": "", "resumo": None, "erro": None}
_job_lock = threading.Lock()


def _job_snapshot():
    with _job_lock:
        return dict(_job)


@app.route("/fatos")
def pagina_fatos():
    de, ate = fatos.periodo_padrao(7)
    return render_template("fatos.html", pagina="fatos", de=de, ate=ate,
                           palavra=fatos.PALAVRA_PADRAO)


@app.route("/api/fatos")
def api_fatos():
    cards = fatos.listar_cards(
        q=(request.args.get("q") or "").strip() or None,
        de=(request.args.get("de") or "").strip() or None,
        ate=(request.args.get("ate") or "").strip() or None,
        apenas_com_documento=request.args.get("com_documento") == "1",
    )
    return jsonify(total=len(cards), cards=cards)


@app.route("/api/fatos/<int:id_noticia>")
def api_fato(id_noticia):
    card = fatos.carregar_index().get(str(id_noticia))
    if card is None:
        return jsonify(error="Fato relevante não encontrado no cache."), 404
    card = dict(card)
    card["texto"] = fatos.texto_completo(id_noticia)
    return jsonify(card)


@app.route("/api/fatos/<int:id_noticia>/pdf")
def api_fato_pdf(id_noticia):
    caminho = fatos.caminho_pdf(id_noticia)
    if not os.path.isfile(caminho):
        return jsonify(error="Documento não baixado para este fato."), 404
    return send_file(caminho, mimetype="application/pdf",
                     download_name="fato_relevante_%s.pdf" % id_noticia)


@app.route("/api/fatos/importar", methods=["POST"])
def api_fatos_importar():
    corpo = request.get_json(silent=True) or {}
    de = (corpo.get("de") or "").strip() or None
    ate = (corpo.get("ate") or "").strip() or None
    palavra = (corpo.get("palavra") or fatos.PALAVRA_PADRAO).strip()
    reimportar = bool(corpo.get("reimportar"))

    with _job_lock:
        if _job["rodando"]:
            return jsonify(error="Já existe uma importação em andamento."), 409
        _job.update(rodando=True, feitos=0, total=0, msg="Consultando a B3...",
                    resumo=None, erro=None)

    def progresso(feitos, total, msg):
        with _job_lock:
            _job["feitos"] = feitos
            _job["total"] = total
            if msg:
                _job["msg"] = msg

    def rodar():
        try:
            resumo = fatos.importar(palavra=palavra, de=de, ate=ate,
                                    reimportar=reimportar, progresso=progresso)
            with _job_lock:
                _job["resumo"] = resumo
                _job["msg"] = "Importação concluída."
        except fatos.FatosError as exc:
            with _job_lock:
                _job["erro"] = str(exc)
        except Exception as exc:  # pragma: no cover - rede de seguranca
            app.logger.exception("falha na importacao de fatos relevantes")
            with _job_lock:
                _job["erro"] = "Falha inesperada na importação: %s" % exc
        finally:
            with _job_lock:
                _job["rodando"] = False

    threading.Thread(target=rodar, daemon=True).start()
    return jsonify(_job_snapshot())


@app.route("/api/fatos/importar/status")
def api_fatos_status():
    return jsonify(_job_snapshot())


@app.errorhandler(413)
def muito_grande(_):
    return jsonify(error="Arquivo acima de 200 MB."), 413


if __name__ == "__main__":
    port = int(os.environ.get("EFIN_PORTA", "5070"))
    host = os.environ.get("EFIN_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("EFIN_DEBUG") == "1")
