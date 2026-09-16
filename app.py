"""
app.py — servidor Flask do e-Financeira Recon.

  GET  /                    pagina da ferramenta
  POST /api/reconcile       recebe trd + ptp (multipart) e devolve a analise
  GET  /api/export/<token>  baixa o MMAA_e-financeira_recon.xlsx da analise
"""

import os
import threading
import uuid
from collections import OrderedDict

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

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
    return render_template("index.html")


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


@app.errorhandler(413)
def muito_grande(_):
    return jsonify(error="Arquivo acima de 200 MB."), 413


if __name__ == "__main__":
    port = int(os.environ.get("EFIN_PORTA", "5070"))
    host = os.environ.get("EFIN_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("EFIN_DEBUG") == "1")
