# AGENTS — Guia do projeto (Manoel)

Boletim diário de exames laboratoriais e de imagem de paciente, site estático em GitHub Pages.

## Pipeline

```
raw/*.pdf ──► scripts/extract_exams.py ──► data/*.json ──► public/index.html (fetch)
```

## Comandos

```bash
pip install -r requirements.txt          # dep: pymupdf; Python 3.10+, CI usa 3.12
python scripts/extract_exams.py [--jobs N] [--verbose]
                                         # PDFs raw/ → data/exams_<range>.json + data/all_exams.json
python scripts/check_data.py             # valida; esperado "PROBLEMAS: 0"
python -m http.server 8000               # sirva raiz do repo; site em http://localhost:8000/public/
```

## Arquitetura

| Arquivo | Papel |
|---|---|
| `src/extract_exams.py` | Biblioteca/parser PyMuPDF. `main()` é o entrypoint. |
| `scripts/extract_exams.py` | CLI wrapper, importa `main` de `src/`. |
| `scripts/check_data.py` | Valida JSONs de `data/`. |
| `data/` | JSONs versionados. `all_exams.json` = envelope `{meta, data:[dias]}`; site carrega só este. |
| `raw/` | PDFs originais. **Gitignored** (dados de saúde). Nome `exams_AAAA-MM-DD_AAAA-MM-DD.pdf`, mas script não depende do nome. |
| `public/index.html` | Frontend HTML+CSS+JS. Tailwind CSS via CDN (`cdn.tailwindcss.com`) + fonte Inter (Google Fonts) — sem build step. JS lê o envelope `{meta, data}` de `all_exams.json`; fetch tenta `data/all_exams.json` e cai para `../data/all_exams.json` (funciona tanto na raiz quanto em `/public/`). |
| `.github/workflows/pages.yml` | CI/CD: no push `main` extrai PDFs (se houver em raw/), monta `_site/`, deploy Pages. |

## Schema (arquivo por dia)

```json
{
  "source": "exams_....pdf",
  "patient": {"name": "Manoel Rodrigues de Oliveira", "nasc": "14/10/1940"},
  "n_groups": N, "generated_at": "...",
  "groups": [ ... ]
}
```

Grupo `lab`:
```json
{
  "kind": "lab",
  "setor": "Pronto socorro" | "Internado",
  "ficha", "atendimento", "data", "medico", "nasc", "title",
  "pages": [0,1],
  "blocks": [{"material","coleta","title","title_lines","method"}],
  "measures": [{"name","value","reference"}],
  "recebido": [], "responsavel": [],
  "result_text": "...",
  "evolution": {"title", "series": [{"name","name_lines","reference",
                "points": [{"datetime","raw","value","num"}]}]} | null
}
```

Grupo `img`:
```json
{
  "kind": "img", "setor": "Imagem",
  "data", "medico", "paciente", "nasc", "rg", "atendimento",
  "title", "title_lines", "text", "responsavel", "pages"
}
```

`all_exams.json`:
```json
{"meta": {"version": 1, "schema": "boletim_exames", "generated_at", "count", "errors": []},
 "data": [dia1, dia2, ...]}
```

## Convenções

- Paciente hardcoded: `PATIENT_NAME = "Manoel Rodrigues de Oliveira"`, `PATIENT_NASC = "14/10/1940"` em `src/extract_exams.py`.
- 1 PDF = 1 arquivo diário. Nome do JSON deriva do PDF.
- Erro num PDF não aborta lote: loga e vai para `meta.errors`; demais dias gravam.
- `all_exams.json` montado por streaming (memória constante).
- `data/` versionado de propósito: site vive mesmo sem raw/ no push.
- Nunca commitar `raw/`, `.venv/`, `__pycache__/`.
- README.md é doc de usuário; este AGENTS.md é o guia do agente.

## Adicionar novo dia

1. Copiar PDF do dia para `raw/`.
2. `python scripts/extract_exams.py`
3. `python scripts/check_data.py` → `PROBLEMAS: 0`
4. Conferir em `python -m http.server 8000` → `/public/`
5. Commit dos `data/*.json` e push (workflow publica sozinho).

## Auditoria multi-agente

Para checagem geral antes de commits grandes, usar **AUDITORIA.md**: prompts
prontos para 5 subagentes em paralelo (organização/schema, corretude dos valores,
frontend, sanidade clínica, datas). Disparar via tool Task (`subagent_type: explore`),
read-only, e consolidar achados P1/P2/P3 antes do push.

## Armadilhas

- NÃO abrir `index.html` via `file://` — CORS bloqueia fetch de `all_exams.json`. Sempre servir via HTTP.
- Workflow só extrai se `raw/*.pdf` existir; caso contrário mantém `data/` commitado.
- `check_data.py` espera `sys.stdout` com encoding utf-8 (já configura).
