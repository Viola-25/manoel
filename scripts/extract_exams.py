#!/usr/bin/env python3
"""Extrai exames dos PDFs em examesbruto/ para JSON estruturado em data/.

Uso:
    python scripts/extract_exams.py

Saida:
    data/<nome-do-pdf>.json  - um arquivo por PDF (um dia de exames)
    data/all_exams.json      - todos os dias combinados
"""
import glob
import json
import os
import re
import sys
from datetime import datetime

import fitz  # PyMuPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(BASE, "examesbruto")
OUT_DIR = os.path.join(BASE, "data")

PAGE_FOOTER_RE = re.compile(r"^Página:\s*(\d+)\s*/\s*(\d+)\s*$")
DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")

REF_PATTERNS = [
    re.compile(r"^[-+]?\d{1,3}(?:[.,]\d+)?\s*(?:a|e)\s*[-+]?\d{1,3}(?:[.,]\d+)?"),
    re.compile(r"^Até\s+\d"),
    re.compile(r"^Superior\s+a\s+\d"),
    re.compile(r"^Inferior\s+a\s+\d"),
    re.compile(r"^Acima\s+de\s+\d"),
    re.compile(r"^Maior\s+ou\s+igual"),
    re.compile(r"^Ver resultado tradicional"),
    re.compile(r"^0\s*/"),
]

STOP_EVOLUTION = ("Responsável Técnico:", "Emitido em:", "Página:")


def is_ref(line):
    return any(p.match(line) for p in REF_PATTERNS)


# ----------------------------------------------------------------------------
# Cabecalhos
# ----------------------------------------------------------------------------

def header_info(lines):
    """Extrai dados do cabecalho dos laudos de laboratorio."""
    info = {"setor": None, "ficha": None, "atendimento": None, "data": None,
            "medico": None, "nasc": None, "paciente": None}
    date_next = False
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s == "Pronto socorro":
            info["setor"] = "Pronto socorro"
        elif s == "Internado":
            info["setor"] = "Internado"
        elif s.startswith("Ficha:"):
            info["ficha"] = s.split(":", 1)[1].strip()
        elif s.startswith("C. Corrente / Atendimento:"):
            info["atendimento"] = s.split(":", 1)[1].strip()
        elif s == "Data:":
            date_next = True
        elif date_next:
            info["data"] = s
            date_next = False
        elif s.startswith("Paciente:"):
            info["paciente"] = s.split(":", 1)[1].strip()
        elif s.startswith("Médico:"):
            info["medico"] = s.split(":", 1)[1].strip()
        elif s.startswith("Dt Nasc:"):
            info["nasc"] = s.split(":", 1)[1].strip()
    return info


def header_end_index(lines):
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Dt Nasc:"):
            return i + 1
    return len(lines)


def imaging_header(lines):
    """Extrai dados do cabecalho dos laudos de imagem (Nome/Médico/Data/...)."""
    info = {"setor": "Imagem", "paciente": None, "medico": None, "data": None,
            "nasc": None, "rg": None, "atendimento": None}
    for ln in lines:
        s = ln.strip()
        if s.startswith("Nome:"):
            info["paciente"] = s.split(":", 1)[1].strip()
        elif s.startswith("Médico:"):
            info["medico"] = s.split(":", 1)[1].strip()
        elif s.startswith("Data:"):
            info["data"] = s.split(":", 1)[1].strip()
        elif s.startswith("Data de Nascimento:"):
            info["nasc"] = s.split(":", 1)[1].strip()
        elif s.startswith("RG:"):
            info["rg"] = s.split(":", 1)[1].strip()
        elif s.startswith("NA:"):
            info["atendimento"] = s.split(":", 1)[1].strip()
    return info


def imaging_identity(lines):
    data = medico = title = None
    prev_idade = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("Data:"):
            data = s.split(":", 1)[1].strip()
        elif s.startswith("Médico:"):
            medico = s.split(":", 1)[1].strip()
        elif s.startswith("Idade:"):
            prev_idade = True
        elif prev_idade and s and title is None:
            title = s
            break
    return (data, title, medico)


# ----------------------------------------------------------------------------
# Segmentacao de paginas em grupos
# ----------------------------------------------------------------------------

def page_kind(lines):
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("Resultados de exames"):
            return "lab"
        if s.startswith("Nome: Manoel"):
            return "img"
        return "cont"
    return "cont"


def page_footer(lines):
    for ln in lines:
        m = PAGE_FOOTER_RE.match(ln.strip())
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def split_groups(doc):
    groups = []
    cur = None
    cur_kind = None
    cur_img_id = None
    for pno, page in enumerate(doc):
        lines = page.get_text().splitlines()
        kind = page_kind(lines)
        footer = page_footer(lines)
        if kind == "img":
            ident = imaging_identity(lines)
            if cur is not None and (cur_kind != "img" or cur_img_id != ident):
                groups.append(cur)
                cur = None
            if cur is None:
                cur, cur_kind, cur_img_id = [], "img", ident
            cur.append((pno, lines))
        elif kind == "lab":
            if cur is not None and (cur_kind != "lab" or (footer and footer[0] == 1)):
                groups.append(cur)
                cur = None
            if cur is None:
                cur, cur_kind, cur_img_id = [], "lab", None
            cur.append((pno, lines))
            if footer and footer[0] == footer[1]:
                groups.append(cur)
                cur = None
        else:
            if cur is None:
                cur, cur_kind, cur_img_id = [], "cont", None
            cur.append((pno, lines))
    if cur:
        groups.append(cur)
    return groups


# ----------------------------------------------------------------------------
# Paginas de resultado (laboratorio)
# ----------------------------------------------------------------------------

MEASURE_KEYED = re.compile(
    r"^([A-ZÀ-Üa-zà-ü0-9()\-/ ]{2,60}?)\s*:\s*(?=[-+]?[\d.,])\s*(\S.*?)\s{2,}(.+)$")
MEASURE_KEYED_NO_REF = re.compile(
    r"^([A-ZÀ-Üa-zà-ü0-9()\-/ ]{2,60}?)\s*:\s*(?=[-+]?[\d.,])\s*(\S.*?)$")
MEASURE_FLAT = re.compile(r"^([-+]?[\d.,]+\s*\S.*?)\s{2,}(.+)$")
MEASURE_ALONE = re.compile(r"^([-+]?[\d.,]+)\s*$")
MEASURE_PATTERNS = (MEASURE_KEYED, MEASURE_KEYED_NO_REF, MEASURE_FLAT,
                    MEASURE_ALONE)

RESULTADO_HEADER_RE = re.compile(r"^RESULTADO(?:\s+VALORES.*)?$")


def extract_measures(text_lines, title_set=None):
    title_set = title_set or set()
    measures = []
    i = 0
    while i < len(text_lines):
        s = text_lines[i].strip()
        if not s or is_ref(s):
            i += 1
            continue
        m = MEASURE_KEYED.match(s)
        if not m and s not in title_set and i + 1 < len(text_lines) \
                and ":" not in s and not re.search(r"\d", s):
            cand = s + " " + text_lines[i + 1].strip()
            m = MEASURE_KEYED.match(cand)
            if m:
                s = cand
                i += 1
        if m:
            name, val, ref = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            measures.append({"name": name, "value": val, "reference": ref, "line": s})
            i += 1
            continue
        m = MEASURE_KEYED_NO_REF.match(s)
        if m:
            name, val = m.group(1).strip(), m.group(2).strip()
            measures.append({"name": name, "value": val, "reference": None, "line": s})
            i += 1
            continue
        m = MEASURE_FLAT.match(s)
        if m:
            val, ref = m.group(1).strip(), m.group(2).strip()
            measures.append({"name": None, "value": val, "reference": ref, "line": s})
            i += 1
            continue
        m = MEASURE_ALONE.match(s)
        if m:
            measures.append({"name": None, "value": m.group(1), "reference": None, "line": s})
        i += 1
    return measures


def parse_result_pages(pages_content):
    text_lines = []
    blocks = []
    recebido = []
    responsavel = []
    content = [l for page in pages_content for l in page]
    i = 0
    while i < len(content):
        ln = content[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("Recebido em:"):
            recebido.append(s)
            i += 1
            continue
        if s.startswith("RESPONSÁVEL:"):
            responsavel.append(s)
            i += 1
            continue
        if s.startswith("Responsável Técnico:") or s.startswith("Dr. ") \
                or s.startswith("Emitido em:") or PAGE_FOOTER_RE.match(s):
            i += 1
            continue
        if s.startswith("Material:"):
            material = s.split(":", 1)[1].strip()
            j = i + 1
            coleta = None
            while j < len(content) and not content[j].strip():
                j += 1
            if j < len(content) and content[j].strip().startswith("Data da coleta:"):
                coleta = content[j].split(":", 1)[1].strip()
                k = j + 1
                title_lines = []
                method = None
                while k < len(content):
                    t = content[k].strip()
                    if not t:
                        k += 1
                        continue
                    if t.startswith(("Material:", "Recebido em:", "RESPONSÁVEL:",
                                     "Responsável Técnico:", "Emitido em:", "Página:")):
                        break
                    if RESULTADO_HEADER_RE.match(t):
                        break
                    if any(p.match(t) for p in MEASURE_PATTERNS):
                        break
                    if t.startswith("Método:"):
                        method = t.split(":", 1)[1].strip()
                        k += 1
                        break
                    title_lines.append(t)
                    k += 1
                blocks.append({
                    "material": material,
                    "coleta": coleta,
                    "title": title_lines[0] if title_lines else None,
                    "title_lines": title_lines,
                    "method": method,
                })
                text_lines.extend(title_lines)
            else:
                text_lines.append(s)
            i += 1
            continue
        if RESULTADO_HEADER_RE.match(s):
            i += 1
            continue
        text_lines.append(s)
        i += 1
    return {
        "blocks": blocks,
        "text_lines": text_lines,
        "measures": extract_measures(text_lines,
                                    {t for b in blocks for t in b["title_lines"]}),
        "recebido": recebido,
        "responsavel": responsavel,
    }


# ----------------------------------------------------------------------------
# Paginas de evolucao (Laudo Evolutivo)
# ----------------------------------------------------------------------------

def to_num(s):
    s = s.strip().rstrip("%").strip()
    if not s or s == "[*]":
        return None
    neg = False
    if s.startswith("-"):
        neg = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if s.startswith(",") or s.startswith("."):
        s = "0" + s
    s = re.sub(r"\.(?=\d{3}(?:\.|\d*$))", "", s)
    s = s.replace(",", ".")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def iso_datetime(m):
    dd, mm, yy, hh, mi = m.groups()
    return f"20{yy}-{mm}-{dd}T{hh}:{mi}"


def parse_evolution(lines):
    series = []
    pending_name = []
    pending_ref = None
    current = None
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if any(s.startswith(st) for st in STOP_EVOLUTION):
            break
        m = DATE_RE.match(s)
        if m:
            if (pending_name or pending_ref is not None) and current is not None \
                    and current["points"]:
                current = None
            if current is None:
                current = {"name_lines": list(pending_name),
                           "reference": pending_ref, "points": []}
                series.append(current)
            dt = iso_datetime(m)
            raw = m.group(0).strip()
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and not DATE_RE.match(lines[j].strip()) \
               and not any(lines[j].strip().startswith(st) for st in STOP_EVOLUTION):
                val_line = lines[j].strip()
                j += 1
            else:
                val_line = None
            current["points"].append({
                "datetime": dt,
                "raw": raw,
                "value": None if val_line in (None, "[*]") else val_line,
                "num": to_num(val_line),
            })
            pending_name = []
            pending_ref = None
            i = j
            continue
        if is_ref(s):
            pending_ref = s
            i += 1
            continue
        pending_name.append(s)
        i += 1
    title = " ".join(series[0]["name_lines"]).strip() if series else ""
    return {"title": title, "series": series}


# ----------------------------------------------------------------------------
# Grupos
# ----------------------------------------------------------------------------

def parse_lab_group(pages):
    info = header_info(pages[0][1])
    result_pages = []
    evolution_lines = []
    evo_seen = False
    for pno, lines in pages:
        he = header_end_index(lines)
        content = lines[he:]
        if not evo_seen:
            for idx, ln in enumerate(content):
                if ln.strip() == "Laudo Evolutivo":
                    evo_seen = True
                    content = content[idx + 1:]
                    break
        if evo_seen:
            for idx, ln in enumerate(content):
                if ln.strip().startswith("Responsável Técnico:"):
                    content = content[:idx]
                    break
            evolution_lines.extend(content)
        else:
            result_pages.append(content)
    res = parse_result_pages(result_pages)
    evo = parse_evolution(evolution_lines) if evo_seen else None
    title = res["blocks"][0]["title"] if res["blocks"] else None
    if not title and evo:
        title = evo["title"]
    return {
        "kind": "lab",
        "info": info,
        "title": title,
        "result": res,
        "evolution": evo,
        "page_numbers": [p[0] for p in pages],
    }


STOP_TITLE = ("Análise:", "Indicação", "Técnica", "Peso", "Altura",
              "Valores de", "Aorta", "Átrio", "Diâmetro", "Exame anterior:",
              "Exame comparativo:", "Conclusão")


def parse_imaging_group(pages):
    info = imaging_header(pages[0][1])
    title_lines = []
    text = []
    responsavel = []
    last_page_content = None
    for pno, lines in pages:
        start = 0
        for idx, ln in enumerate(lines):
            if ln.strip().startswith("Idade:"):
                start = idx + 1
                break
        content = [ln.strip() for ln in lines[start:] if ln.strip()]
        if content == last_page_content:
            continue
        last_page_content = content
        if not title_lines:
            ti = 0
            while ti < len(content) and not any(
                    content[ti].startswith(t) for t in STOP_TITLE):
                title_lines.append(content[ti])
                ti += 1
        for idx, s in enumerate(content):
            if re.search(r"CRM", s) or "Dr." in s or "Dra." in s:
                responsavel.append(s)
                k = idx + 1
                while k < len(content) and re.match(
                        r"^[\s\-–]*\d{4,}.*(?:SP|CRM)", content[k]):
                    responsavel.append(content[k])
                    k += 1
                continue
            text.append(s)
    if title_lines:
        text = [s for s in text if s not in title_lines]
    return {
        "kind": "img",
        "info": info,
        "title": " ".join(title_lines).strip(),
        "title_lines": title_lines,
        "text": text,
        "responsavel": responsavel,
        "page_numbers": [p[0] for p in pages],
    }


def clean_lab_group(g):
    evo = g["evolution"]
    return {
        "kind": "lab",
        "setor": g["info"]["setor"],
        "ficha": g["info"]["ficha"],
        "atendimento": g["info"]["atendimento"],
        "data": g["info"]["data"],
        "medico": g["info"]["medico"],
        "nasc": g["info"]["nasc"],
        "title": g["title"],
        "pages": g["page_numbers"],
        "blocks": [
            {"material": b["material"], "coleta": b["coleta"],
             "title": b["title"], "title_lines": b["title_lines"],
             "method": b["method"]}
            for b in g["result"]["blocks"]
        ],
        "measures": g["result"]["measures"],
        "recebido": g["result"]["recebido"],
        "responsavel": g["result"]["responsavel"],
        "result_text": "\n".join(g["result"]["text_lines"]),
        "evolution": None if evo is None else {
            "title": evo["title"],
            "series": [
                {
                    "name": "" if (i == 0 and se["name_lines"]
                                   and " ".join(se["name_lines"]) == evo["title"])
                            else " ".join(se["name_lines"]).strip(),
                    "name_lines": se["name_lines"],
                    "reference": se["reference"],
                    "points": se["points"],
                }
                for i, se in enumerate(evo["series"])
            ],
        },
    }


def clean_img_group(g):
    return {
        "kind": "img",
        "setor": g["info"]["setor"],
        "data": g["info"]["data"],
        "medico": g["info"]["medico"],
        "paciente": g["info"]["paciente"],
        "nasc": g["info"]["nasc"],
        "rg": g["info"]["rg"],
        "atendimento": g["info"]["atendimento"],
        "title": g["title"],
        "title_lines": g["title_lines"],
        "text": g["text"],
        "responsavel": g["responsavel"],
        "pages": g["page_numbers"],
    }


def process_pdf(path):
    doc = fitz.open(path)
    groups = split_groups(doc)
    cleaned = []
    for grp in groups:
        first_lines = grp[0][1]
        if page_kind(first_lines) == "img":
            cleaned.append(clean_img_group(parse_imaging_group(grp)))
        else:
            cleaned.append(clean_lab_group(parse_lab_group(grp)))
    doc.close()
    return {
        "source": os.path.basename(path),
        "patient": {
            "name": "Manoel Rodrigues de Oliveira",
            "nasc": "14/10/1940",
        },
        "n_groups": len(cleaned),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "groups": cleaned,
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    if not pdfs:
        print(f"Nenhum PDF encontrado em {PDF_DIR}")
        sys.exit(1)
    all_days = []
    for path in pdfs:
        day = process_pdf(path)
        out = os.path.join(OUT_DIR, os.path.basename(path).replace(".pdf", ".json"))
        with open(out, "w", encoding="utf-8") as f:
            json.dump(day, f, ensure_ascii=False, indent=2)
        all_days.append(day)
        print(f"{os.path.basename(path)}: {day['n_groups']} grupos -> {out}")
    all_out = os.path.join(OUT_DIR, "all_exams.json")
    with open(all_out, "w", encoding="utf-8") as f:
        json.dump(all_days, f, ensure_ascii=False, indent=2)
    print(f"Combinado: {all_out}")


if __name__ == "__main__":
    main()
