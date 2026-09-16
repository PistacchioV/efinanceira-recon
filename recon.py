"""
recon.py — motor do batimento e-Financeira (TRD x PTP).

Regras:
  * Chave: coluna J do MMAA_TRD (formato INOA-1310004198) contra a coluna A do
    MMAA_PTP. O casamento usa apenas o miolo numerico da celula.
  * Instrumento so no TRD  -> "Allege on PTP side" (falta do lado PTP).
    Instrumento so no PTP  -> "Allege on TRD side" (falta do lado TRD).
  * Subtracoes (TRD - PTP): N-B, P-C, Q-D, T-E, U-F, V-G.
  * Nome de coluna = nome original + "_TRD" / "_PTP".
"""

import io
import re
from datetime import date, datetime, timedelta

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------- #
#  Layout dos arquivos
# --------------------------------------------------------------------------- #
TRD_ID_COL = "J"
PTP_ID_COL = "A"
TRD_ANOMES_COL = "E"  # anomescaixa (ex.: 202207) — fallback para o MMAA

# (coluna TRD, coluna PTP, tipo)
PAIRS = [
    ("N", "B", "date"),
    ("P", "C", "number"),
    ("Q", "D", "number"),
    ("T", "E", "number"),
    ("U", "F", "code"),
    ("V", "G", "number"),
]

STATUS_MATCH = "Match"
STATUS_ALLEGE_PTP = "Allege on PTP side"
STATUS_ALLEGE_TRD = "Allege on TRD side"
VAL_OK = "OK"
VAL_DIV = "Divergente"
TXT_DIVERGENTE = "DIVERGENTE"

TOLERANCIA = 0.005
FILE_RE = re.compile(r"(\d{4})\s*[_\-\s]\s*(TRD|PTP)", re.IGNORECASE)
EXCEL_EPOCH = datetime(1899, 12, 30)


class ReconError(Exception):
    """Erro de entrada que deve chegar ao usuario como mensagem legivel."""


def col_idx(letter):
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


# --------------------------------------------------------------------------- #
#  Normalizacao de valores
# --------------------------------------------------------------------------- #
def is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def id_key(v):
    """Miolo numerico do ID: 'INOA-1310004198' -> '1310004198'."""
    if is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    digits = re.sub(r"\D", "", str(v))
    if not digits:
        return None
    return digits.lstrip("0") or "0"


def to_number(v):
    if is_blank(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):  # 1.234.567,89
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", s):  # 1234,56
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):  # 1,234,567.89
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_date(v):
    if is_blank(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 <= v <= 80000:
        return (EXCEL_EPOCH + timedelta(days=float(v))).date()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_code(v):
    if is_blank(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


def clean_display(v):
    """Valor lido da planilha, pronto para exibir/exportar."""
    if is_blank(v):
        return None
    if isinstance(v, datetime):
        return v.date() if v.time() == datetime.min.time() else v
    if isinstance(v, str):
        return v.strip()
    return v


def compare(kind, a, b):
    """
    Retorna (diff, divergente).
      date   -> diferenca em dias (TRD - PTP)
      number -> TRD - PTP (vazio conta como zero)
      code   -> TRD - PTP se ambos numericos; senao 0 quando iguais e
                'DIVERGENTE' quando diferentes
    """
    if kind == "date":
        da, db = to_date(a), to_date(b)
        if da is None and db is None:
            if is_blank(a) and is_blank(b):
                return None, False
            return (0, False) if to_code(a) == to_code(b) else (TXT_DIVERGENTE, True)
        if da is None or db is None:
            return TXT_DIVERGENTE, True
        d = (da - db).days
        return d, d != 0

    if kind == "number":
        na, nb = to_number(a), to_number(b)
        a_ok = na is not None or is_blank(a)
        b_ok = nb is not None or is_blank(b)
        if a_ok and b_ok:
            d = round((na or 0.0) - (nb or 0.0), 6)
            if abs(d) < TOLERANCIA:
                d = 0.0
            return d, d != 0
        return compare("code", a, b)

    # code
    na, nb = to_number(a), to_number(b)
    if na is not None and nb is not None:
        d = round(na - nb, 6)
        return d, abs(d) >= TOLERANCIA
    if to_code(a) == to_code(b):
        return (None, False) if is_blank(a) and is_blank(b) else (0, False)
    return TXT_DIVERGENTE, True


# --------------------------------------------------------------------------- #
#  Leitura
# --------------------------------------------------------------------------- #
def read_sheet(data, filename):
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # arquivo corrompido, .xls antigo, etc.
        raise ReconError(
            "Nao consegui abrir '%s' como planilha .xlsx (%s)." % (filename, exc.__class__.__name__)
        )
    ws = wb.worksheets[0]
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append(list(row))
    wb.close()
    # descarta linhas totalmente vazias no fim
    while rows and all(is_blank(v) for v in rows[-1]):
        rows.pop()
    if not rows:
        raise ReconError("A planilha '%s' esta vazia." % filename)
    return rows


def cell(row, letter):
    i = col_idx(letter)
    return row[i] if i < len(row) else None


def looks_like(rows):
    """Adivinha o tipo pelo conteudo: 'TRD', 'PTP' ou None."""
    header = [str(v or "").strip().lower() for v in rows[0]]
    if header and header[0] == "instrument_id":
        return "PTP"
    if any(h.endswith("_ptp") for h in header):
        return "PTP"
    for r in rows[1:30]:
        j = cell(r, TRD_ID_COL)
        if isinstance(j, str) and re.search(r"[A-Za-z]+-\d+", j):
            return "TRD"
    if len(header) > col_idx("V"):
        return "TRD"
    return None


def classify(name, rows):
    m = FILE_RE.search(name or "")
    by_name = m.group(2).upper() if m else None
    return by_name or looks_like(rows), (m.group(1) if m else None)


def header_name(rows, letter, suffix):
    raw = cell(rows[0], letter)
    base = str(raw).strip() if not is_blank(raw) else "col_%s" % letter
    base = re.sub(r"\s+", "_", base)
    if base.lower().endswith(suffix.lower()):
        return base
    return base + suffix


def strip_suffix(name):
    return re.sub(r"_(TRD|PTP)$", "", name, flags=re.IGNORECASE)


# --------------------------------------------------------------------------- #
#  Batimento
# --------------------------------------------------------------------------- #
def reconcile(trd_file, ptp_file):
    """
    trd_file / ptp_file: tuplas (nome, bytes). Os lados sao validados pelo
    nome (MMAA_TRD / MMAA_PTP) e, na falta dele, pelo conteudo — se vierem
    trocados, sao destrocados.
    """
    warnings = []
    files = []
    for name, data in (trd_file, ptp_file):
        rows = read_sheet(data, name)
        kind, mmaa = classify(name, rows)
        files.append({"name": name, "rows": rows, "kind": kind, "mmaa": mmaa})

    a, b = files
    if a["kind"] == "PTP" and b["kind"] in ("TRD", None):
        a, b = b, a
        warnings.append("Os arquivos vieram trocados; o lado TRD e o PTP foram ajustados.")
    elif a["kind"] is None and b["kind"] == "TRD":
        a, b = b, a
    if a["kind"] == b["kind"] and a["kind"] is not None:
        raise ReconError(
            "Os dois arquivos parecem ser %s. Envie um MMAA_TRD e um MMAA_PTP." % a["kind"]
        )
    trd, ptp = a, b

    trd_rows = [r for r in trd["rows"][1:] if not all(is_blank(v) for v in r)]
    ptp_rows = [r for r in ptp["rows"][1:] if not all(is_blank(v) for v in r)]

    # ---- MMAA do arquivo de saida ----------------------------------------
    mmaa = trd["mmaa"] or ptp["mmaa"]
    if trd["mmaa"] and ptp["mmaa"] and trd["mmaa"] != ptp["mmaa"]:
        warnings.append(
            "Competencias diferentes nos nomes: TRD %s x PTP %s. Usei %s."
            % (trd["mmaa"], ptp["mmaa"], trd["mmaa"])
        )
    if not mmaa:
        for r in trd_rows[:5]:
            v = to_number(cell(r, TRD_ANOMES_COL))
            if v and 190001 <= v <= 299912:
                s = str(int(v))
                mmaa = s[4:6] + s[2:4]
                break
    if not mmaa:
        mmaa = datetime.now().strftime("%m%y")

    # ---- colunas ----------------------------------------------------------
    columns = [
        {"key": "id_trd", "label": header_name(trd["rows"], TRD_ID_COL, "_TRD"), "src": "TRD", "letter": TRD_ID_COL, "group": 0, "kind": "id"},
        {"key": "id_ptp", "label": header_name(ptp["rows"], PTP_ID_COL, "_PTP"), "src": "PTP", "letter": PTP_ID_COL, "group": 0, "kind": "id"},
    ]
    groups = [{"label": "instrument_id", "span": 2}]
    for gi, (lt, lp, kind) in enumerate(PAIRS, start=1):
        nt = header_name(trd["rows"], lt, "_TRD")
        np_ = header_name(ptp["rows"], lp, "_PTP")
        columns.append({"key": "t%d" % gi, "label": nt, "src": "TRD", "letter": lt, "group": gi, "kind": kind})
        columns.append({"key": "p%d" % gi, "label": np_, "src": "PTP", "letter": lp, "group": gi, "kind": kind})
        columns.append({"key": "d%d" % gi, "label": "dif_" + strip_suffix(nt), "src": "DIF", "letter": "%s-%s" % (lt, lp), "group": gi, "kind": kind})
        groups.append({"label": strip_suffix(nt), "span": 3, "kind": kind})
    for i, c in enumerate(columns):
        c["out"] = get_column_letter(i + 1)

    # ---- indexa o PTP -----------------------------------------------------
    def index(rows, letter, side):
        idx, order, sem_id = {}, [], 0
        for r in rows:
            k = id_key(cell(r, letter))
            if k is None:
                sem_id += 1
                continue
            if k not in idx:
                idx[k] = []
                order.append(k)
            idx[k].append(r)
        dups = [k for k in order if len(idx[k]) > 1]
        if dups:
            warnings.append(
                "%d ID(s) repetido(s) no %s (ex.: %s). As ocorrencias foram pareadas na ordem."
                % (len(dups), side, ", ".join(dups[:3]))
            )
        if sem_id:
            warnings.append("%d linha(s) sem ID no %s foram ignoradas." % (sem_id, side))
        return idx, order

    trd_idx, trd_order = index(trd_rows, TRD_ID_COL, "TRD")
    ptp_idx, ptp_order = index(ptp_rows, PTP_ID_COL, "PTP")

    out = []

    def build(key, rt, rp):
        rec = {"key": key}
        rec["id_trd"] = clean_display(cell(rt, TRD_ID_COL)) if rt else None
        rec["id_ptp"] = clean_display(cell(rp, PTP_ID_COL)) if rp else None
        divs = []
        for gi, (lt, lp, kind) in enumerate(PAIRS, start=1):
            vt = clean_display(cell(rt, lt)) if rt else None
            vp = clean_display(cell(rp, lp)) if rp else None
            if kind == "date":
                vt = to_date(vt) or vt
                vp = to_date(vp) or vp
            elif kind == "number":
                vt = vt if to_number(vt) is None else to_number(vt)
                vp = vp if to_number(vp) is None else to_number(vp)
            rec["t%d" % gi] = vt
            rec["p%d" % gi] = vp
            if rt is not None and rp is not None:
                d, is_div = compare(kind, vt, vp)
                rec["d%d" % gi] = d
                if is_div:
                    divs.append("d%d" % gi)
            else:
                rec["d%d" % gi] = None
        if rt is not None and rp is not None:
            rec["status"] = STATUS_MATCH
            rec["valores"] = VAL_DIV if divs else VAL_OK
        elif rt is not None:
            rec["status"] = STATUS_ALLEGE_PTP
            rec["valores"] = "-"
        else:
            rec["status"] = STATUS_ALLEGE_TRD
            rec["valores"] = "-"
        rec["divs"] = divs
        return rec

    for k in trd_order:
        lt_rows, lp_rows = trd_idx[k], ptp_idx.get(k, [])
        for i, rt in enumerate(lt_rows):
            out.append(build(k, rt, lp_rows[i] if i < len(lp_rows) else None))
        for rp in lp_rows[len(lt_rows):]:
            out.append(build(k, None, rp))
    for k in ptp_order:
        if k not in trd_idx:
            for rp in ptp_idx[k]:
                out.append(build(k, None, rp))

    # ---- resumo -----------------------------------------------------------
    div_por_campo = {}
    for gi in range(1, len(PAIRS) + 1):
        div_por_campo[groups[gi]["label"]] = sum(1 for r in out if "d%d" % gi in r["divs"])

    summary = {
        "mmaa": mmaa,
        "filename": "%s_e-financeira_recon.xlsx" % mmaa,
        "trd_file": trd["name"],
        "ptp_file": ptp["name"],
        "trd_linhas": len(trd_rows),
        "ptp_linhas": len(ptp_rows),
        "total": len(out),
        "match": sum(1 for r in out if r["status"] == STATUS_MATCH),
        "match_ok": sum(1 for r in out if r["valores"] == VAL_OK),
        "divergentes": sum(1 for r in out if r["valores"] == VAL_DIV),
        "allege_ptp": sum(1 for r in out if r["status"] == STATUS_ALLEGE_PTP),
        "allege_trd": sum(1 for r in out if r["status"] == STATUS_ALLEGE_TRD),
        "div_por_campo": div_por_campo,
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    return {"columns": columns, "groups": groups, "rows": out, "summary": summary, "warnings": warnings}


# --------------------------------------------------------------------------- #
#  Serializacao
# --------------------------------------------------------------------------- #
def _json_value(v):
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return v


def to_json(result):
    rows = []
    for r in result["rows"]:
        rows.append({k: (_json_value(v) if k not in ("divs",) else v) for k, v in r.items()})
    return {
        "columns": result["columns"],
        "groups": result["groups"],
        "rows": rows,
        "summary": result["summary"],
        "warnings": result["warnings"],
    }


# --------------------------------------------------------------------------- #
#  Exportacao Excel
# --------------------------------------------------------------------------- #
INK = "05080A"
LIME = "C6F91F"
FILL_HEAD = PatternFill("solid", fgColor=INK)
FILL_TRD = PatternFill("solid", fgColor="EEF1F4")
FILL_PTP = PatternFill("solid", fgColor="F6F7F2")
FILL_DIF = PatternFill("solid", fgColor="F4FBDC")
FILL_BAD = PatternFill("solid", fgColor="FDE2DC")
FILL_ALLEGE = PatternFill("solid", fgColor="FFF4D6")
THIN = Side(style="thin", color="D9DDE1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def to_excel(result):
    cols = result["columns"]
    s = result["summary"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Recon"

    extra = [("status_batimento", "status"), ("status_valores", "valores")]
    headers = [c["label"] for c in cols] + [h for h, _ in extra]

    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.fill = FILL_HEAD
        is_dif = j <= len(cols) and cols[j - 1]["src"] == "DIF"
        c.font = Font(bold=True, color=LIME if is_dif else "FFFFFF", name="Calibri", size=11)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[1].height = 32

    for i, r in enumerate(result["rows"], start=2):
        allege = r["status"] != STATUS_MATCH
        for j, col in enumerate(cols, start=1):
            v = r.get(col["key"])
            c = ws.cell(row=i, column=j, value=v)
            c.border = BORDER
            if col["src"] == "DIF":
                c.fill = FILL_BAD if col["key"] in r["divs"] else FILL_DIF
                if col["key"] in r["divs"]:
                    c.font = Font(bold=True, color="B3261E")
            elif allege:
                c.fill = FILL_ALLEGE
            else:
                c.fill = FILL_TRD if col["src"] == "TRD" else FILL_PTP
            if isinstance(v, (date, datetime)):
                c.number_format = "dd/mm/yyyy"
                c.alignment = Alignment(horizontal="center")
            elif isinstance(v, float) or (isinstance(v, int) and col["kind"] == "number"):
                c.number_format = "#,##0.00;[Red]-#,##0.00"
            elif col["src"] == "DIF" and isinstance(v, int):
                c.number_format = '0" d";[Red]-0" d"'
        for k, (_, key) in enumerate(extra):
            c = ws.cell(row=i, column=len(cols) + 1 + k, value=r[key])
            c.border = BORDER
            if key == "status" and allege:
                c.fill = FILL_ALLEGE
                c.font = Font(bold=True, color="8A5A00")
            elif key == "valores" and r[key] == VAL_DIV:
                c.fill = FILL_BAD
                c.font = Font(bold=True, color="B3261E")

    widths = {"id": 18, "date": 14, "number": 16, "code": 12}
    for j, col in enumerate(cols, start=1):
        w = max(widths.get(col["kind"], 14), min(len(col["label"]) + 2, 28))
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.column_dimensions[get_column_letter(len(cols) + 1)].width = 22
    ws.column_dimensions[get_column_letter(len(cols) + 2)].width = 16
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(headers)), max(1, len(result["rows"]) + 1))

    # ---- aba de resumo ----------------------------------------------------
    rs = wb.create_sheet("Resumo")
    linhas = [
        ("Batimento e-Financeira", ""),
        ("Competencia (MMAA)", s["mmaa"]),
        ("Arquivo TRD", s["trd_file"]),
        ("Arquivo PTP", s["ptp_file"]),
        ("Gerado em", s["gerado_em"]),
        ("", ""),
        ("Linhas TRD", s["trd_linhas"]),
        ("Linhas PTP", s["ptp_linhas"]),
        ("Instrumentos casados (Match)", s["match"]),
        ("  sem divergencia", s["match_ok"]),
        ("  com divergencia", s["divergentes"]),
        ("Allege on PTP side (so no TRD)", s["allege_ptp"]),
        ("Allege on TRD side (so no PTP)", s["allege_trd"]),
        ("", ""),
        ("Divergencias por campo", ""),
    ] + [("  " + k, v) for k, v in s["div_por_campo"].items()]
    if result["warnings"]:
        linhas += [("", ""), ("Avisos", "")] + [("  -", w) for w in result["warnings"]]
    for i, (k, v) in enumerate(linhas, start=1):
        rs.cell(row=i, column=1, value=k)
        rs.cell(row=i, column=2, value=v)
    rs["A1"].font = Font(bold=True, size=14, color=INK)
    for ref in ("A15",):
        rs[ref].font = Font(bold=True)
    rs.column_dimensions["A"].width = 36
    rs.column_dimensions["B"].width = 60

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
