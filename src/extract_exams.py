#!/usr/bin/env python3
"""Logica de extracao de exames: PDFs em raw/ para JSON estruturado em data/.

Pipeline:
    raw/*.pdf --process_pdf--> data/exams_<dia>.json (um arquivo por dia)
                             + data/all_exams.json (envelope com "meta" + "data",
                               pronto para consumo por API)

Este modulo e a biblioteca; o entrypoint de linha de comando fica em
scripts/extract_exams.py (que importa `main()` daqui). Tambem pode ser
executado diretamente:

    python src/extract_exams.py [--jobs N] [--verbose]

Saida:
    data/<nome-do-pdf>.json  - um arquivo por PDF (um dia de exames)
    data/all_exams.json      - todos os dias combinados, com cabecalho "meta"

Robustez:
    - Um PDF com erro nao aborta o lote: o erro e logado e registrado em
      meta.errors no all_exams.json; os demais dias continuam sendo gravados.
    - O all_exams.json e montado por streaming (um dia por vez), mantendo o
      uso de memoria constante independente do volume de PDFs.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Any

import fitz  # PyMuPDF

logger = logging.getLogger("extract_exams")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

PATIENT_NAME = "Manoel Rodrigues de Oliveira"
PATIENT_NASC = "14/10/1940"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(BASE, "raw")
OUT_DIR = os.path.join(BASE, "data")

TextLines = list[str]
Page = tuple[int, TextLines]
GroupPages = list[Page]
InfoDict = dict[str, str | None]
Measure = dict[str, Any]
Record = dict[str, Any]

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


def is_ref(line: str) -> bool:
    return any(p.match(line) for p in REF_PATTERNS)


# ----------------------------------------------------------------------------
# Cabecalhos
# ----------------------------------------------------------------------------

def header_info(lines: TextLines) -> InfoDict:
    """Extrai dados do cabecalho dos laudos de laboratorio."""
    info: InfoDict = {"setor": None, "ficha": None, "atendimento": None,
                      "data": None, "medico": None, "nasc": None,
                      "paciente": None}
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


def header_end_index(lines: TextLines) -> int:
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Dt Nasc:"):
            return i + 1
    return len(lines)


def imaging_header(lines: TextLines) -> InfoDict:
    """Extrai dados do cabecalho dos laudos de imagem (Nome/Médico/Data/...)."""
    info: InfoDict = {"setor": "Imagem", "paciente": None, "medico": None,
                      "data": None, "nasc": None, "rg": None,
                      "atendimento": None}
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


def imaging_identity(lines: TextLines) -> tuple[str | None, str | None, str | None]:
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

def page_kind(lines: TextLines) -> str:
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


def page_footer(lines: TextLines) -> tuple[int, int] | None:
    for ln in lines:
        m = PAGE_FOOTER_RE.match(ln.strip())
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def split_groups(doc: Any) -> list[GroupPages]:
    groups: list[GroupPages] = []
    cur: GroupPages | None = None
    cur_kind: str | None = None
    cur_img_id: tuple[str | None, str | None, str | None] | None = None
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


def extract_measures(text_lines: TextLines,
                     title_set: set[str] | None = None) -> list[Measure]:
    title_set = title_set or set()
    measures: list[Measure] = []
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


def parse_result_pages(pages_content: list[TextLines]) -> Record:
    text_lines: TextLines = []
    blocks: list[Record] = []
    recebido: list[str] = []
    responsavel: list[str] = []
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
                title_lines: list[str] = []
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

def to_num(s: str) -> float | None:
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


def iso_datetime(m: re.Match[str]) -> str:
    dd, mm, yy, hh, mi = m.groups()
    return f"20{yy}-{mm}-{dd}T{hh}:{mi}"


def parse_evolution(lines: TextLines) -> Record:
    series: list[Record] = []
    pending_name: list[str] = []
    pending_ref: str | None = None
    current: Record | None = None
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

def parse_lab_group(pages: GroupPages) -> Record:
    info = header_info(pages[0][1])
    result_pages: list[TextLines] = []
    evolution_lines: TextLines = []
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


def parse_imaging_group(pages: GroupPages) -> Record:
    info = imaging_header(pages[0][1])
    title_lines: list[str] = []
    text: list[str] = []
    responsavel: list[str] = []
    last_page_content: TextLines | None = None
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


def clean_lab_group(g: Record) -> Record:
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


def clean_img_group(g: Record) -> Record:
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


def process_pdf(path: str) -> Record:
    logger.debug("Abrindo %s", path)
    with fitz.open(path) as doc:
        groups = split_groups(doc)
        cleaned: list[Record] = []
        for grp in groups:
            first_lines = grp[0][1]
            if page_kind(first_lines) == "img":
                cleaned.append(clean_img_group(parse_imaging_group(grp)))
            else:
                cleaned.append(clean_lab_group(parse_lab_group(grp)))
        return {
            "source": os.path.basename(path),
            "patient": {"name": PATIENT_NAME, "nasc": PATIENT_NASC},
            "n_groups": len(cleaned),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "groups": cleaned,
        }


# ----------------------------------------------------------------------------
# Orquestracao do pipeline
# ----------------------------------------------------------------------------

def process_one(path: str, out_dir: str) -> Record:
    """Extrai um PDF e grava data/<nome-do-pdf>.json. Retorna o registro do dia."""
    day = process_pdf(path)
    out = os.path.join(out_dir, os.path.basename(path).replace(".pdf", ".json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, indent=2)
    return day


def safe_process_one(path: str, out_dir: str) -> Record:
    """process_one com isolamento de erros: falha retorna registro com "error"."""
    name = os.path.basename(path)
    try:
        day = process_one(path, out_dir)
    except Exception:
        logger.exception("Falha ao processar %s", name)
        return {"source": name, "error": traceback.format_exc().strip()}
    logger.info("[%s] %d grupos -> %s", name, day["n_groups"],
                os.path.join(out_dir, name.replace(".pdf", ".json")))
    return day


def _init_worker_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATEFMT)


def process_many(pdfs: list[str], out_dir: str, jobs: int) -> list[Record]:
    logger.info("Processando %d PDF(s) em paralelo (%d workers)", len(pdfs), jobs)
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker_logging) as ex:
        return list(ex.map(safe_process_one, pdfs, [out_dir] * len(pdfs)))


def assemble_all_exams(out_dir: str, day_files: list[str], errors: list[Record],
                       started: datetime) -> Record:
    """Monta all_exams.json por streaming: envelope "meta" + "data".

    Cada dia e lido e serializado um de cada vez, entao o uso de memoria nao
    cresce com o numero de PDFs (suporta grandes volumes sem OOM).
    """
    generated_at = datetime.now().isoformat(timespec="seconds")
    all_out = os.path.join(out_dir, "all_exams.json")
    meta: Record = {
        "version": 1,
        "schema": "boletim_exames",
        "generated_at": generated_at,
        "count": len(day_files),
        "errors": errors,
    }
    with open(all_out, "w", encoding="utf-8") as f:
        f.write('{\n  "meta": ')
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write(',\n  "data": [')
        first = True
        for fp in day_files:
            try:
                with open(fp, "r", encoding="utf-8") as df:
                    day = json.load(df)
            except (OSError, json.JSONDecodeError):
                logger.exception("Erro ao ler %s para o arquivo combinado; ignorando", fp)
                continue
            if not first:
                f.write(",")
            first = False
            json.dump(day, f, ensure_ascii=False, indent=2)
        f.write("\n  ]\n}\n")
    elapsed = (datetime.now() - started).total_seconds()
    logger.info("Combinado: %s (%d dia(s), %.1fs)", all_out, meta["count"], elapsed)
    return meta


def extract_all(pdf_dir: str, out_dir: str, jobs: int) -> Record:
    os.makedirs(out_dir, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    if not pdfs:
        logger.error("Nenhum PDF encontrado em %s", pdf_dir)
        raise FileNotFoundError(pdf_dir)
    logger.info("Encontrados %d PDF(s); iniciando extracao (jobs=%d)", len(pdfs), jobs)
    started = datetime.now()

    if jobs == 1:
        results = [safe_process_one(p, out_dir) for p in pdfs]
    else:
        results = process_many(pdfs, out_dir, jobs)

    errors: list[Record] = []
    day_files: list[str] = []
    for pdf, res in zip(pdfs, results):
        if res.get("error"):
            errors.append({"source": os.path.basename(pdf), "error": res["error"]})
        else:
            day_files.append(os.path.join(
                out_dir, os.path.basename(pdf).replace(".pdf", ".json")))

    meta = assemble_all_exams(out_dir, day_files, errors, started)
    if errors:
        logger.warning("%d PDF(s) com erro: %s",
                       len(errors), [e["source"] for e in errors])
    return meta


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extrai exames de PDFs em raw/ para JSON estruturado em data/.")
    parser.add_argument("--jobs", type=int, default=1, metavar="N",
                        help="PDFs processados em paralelo; 0 = todos os cores "
                             "(default: 1, sequencial)")
    parser.add_argument("--verbose", action="store_true",
                        help="log em nivel DEBUG")
    return parser


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format=LOG_FORMAT, datefmt=LOG_DATEFMT)


def _resolve_jobs(requested: int) -> int:
    if requested == 0:
        return max(1, os.cpu_count() or 1)
    return max(1, requested)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    jobs = _resolve_jobs(args.jobs)
    logger.debug("Config: jobs=%d, verbose=%s", jobs, args.verbose)
    try:
        meta = extract_all(PDF_DIR, OUT_DIR, jobs)
    except Exception:
        logger.exception("Falha na extracao; nenhum dado gravado")
        return 1
    logger.info("Concluido: %d dia(s), %d erro(s) -> %s",
                meta["count"], len(meta["errors"]),
                os.path.join(OUT_DIR, "all_exams.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
