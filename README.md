# Boletim de exames — Manoel

Site estático (GitHub Pages) com o boletim diário de exames laboratoriais e de
imagem de um paciente, gerado a partir de PDFs de laudos de laboratório.

O fluxo é simples:

```
raw/*.pdf  ──►  scripts/extract_exams.py  ──►  data/*.json  ──►  public/index.html
```

## Estrutura do projeto

```
manoel/
├── README.md
├── requirements.txt            # dependencias Python (pymupdf)
├── .gitignore                  # ignora raw/ (PDFs), .venv/, __pycache__/
├── .github/
│   └── workflows/
│       └── pages.yml           # CI/CD: extrai dados e publica no GitHub Pages
├── src/                        # biblioteca (logica de parsing)
│   └── extract_exams.py        # parser PyMuPDF: PDF -> grupos de exames -> JSON
├── scripts/                    # executaveis de linha de comando
│   ├── extract_exams.py        # CLI: chama a logica em src/
│   └── check_data.py           # valida os JSONs gerados em data/
├── raw/                        # PDFs originais (NAO versionados — dados sensiveis)
│   └── exams_AAAA-MM-DD_AAAA-MM-DD.pdf
├── data/                       # JSONs processados (versionados, usados pelo site)
│   ├── exams_AAAA-MM-DD_AAAA-MM-DD.json   # um arquivo por dia de boletim
│   └── all_exams.json          # todos os dias combinados (o site carrega este)
└── public/                     # site estatico publicado
    └── index.html              # frontend (HTML + CSS + JS, sem dependencias)
```

### Papel de cada pasta

| Pasta | Conteúdo | Versionada? |
|-------|----------|-------------|
| `src/` | Código-fonte da biblioteca de extração | sim |
| `scripts/` | Entrypoints de automação (`extract_exams`, `check_data`) | sim |
| `raw/` | PDFs originais dos laudos | **não** (no `.gitignore`) |
| `data/` | Dados processados consumidos pelo frontend | sim |
| `public/` | Site estático (HTML) | sim |

## Pré-requisitos

- Python **3.10+** (o workflow CI usa 3.12)
- Git

## Como rodar localmente

### 1. Instalar dependências

Crie e ative um ambiente virtual e instale os pacotes:

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Colocar os PDFs

Copie os boletins (PDFs) do dia para a pasta `raw/`:

```bash
mkdir -p raw
cp /caminho/dos/boletins/exams_2026-08-06_2026-08-07.pdf raw/
```

Os PDFs seguem a convenção `exams_<inicio>_<fim>.pdf`, mas o script aceita
qualquer nome — ele deriva as datas dos grupos extraídos.

> **Importante:** `raw/` está no `.gitignore`. PDFs contêm dados de saúde e não
> devem ser commitados.

### 3. Extrair os exames

```bash
python scripts/extract_exams.py
```

Saída:

- `data/exams_<dia>.json` — um arquivo por PDF;
- `data/all_exams.json` — todos os dias combinados (é o arquivo que o site lê).

### 4. Validar a extração (opcional)

Confere se os JSONs têm cabeçalhos completos, séries e pontos, e lista
qualquer problema:

```bash
python scripts/check_data.py
```

O esperado ao final é `PROBLEMAS: 0`.

### 5. Visualizar o site

O frontend carrega `data/all_exams.json` via `fetch`. Por causa da política de
CORS dos navegadores, **não abra `index.html` direto com duplo clique** — sirva
a pasta raiz do projeto:

```bash
python -m http.server 8000
```

Depois acesse:

```
http://localhost:8000/public/
```

(O fetch relativo `data/all_exams.json` resolve para `http://localhost:8000/data/all_exams.json`,
que existe porque o servidor parte da raiz do repositório.)

## Adicionar um novo dia

1. Coloque o PDF do dia em `raw/`;
2. Rode `python scripts/extract_exams.py`;
3. Valide com `python scripts/check_data.py`;
4. Commit e push — ou rode o servidor local para conferir antes.

## Deploy (GitHub Pages)

O workflow `.github/workflows/pages.yml` roda automaticamente a cada push na
branch `main`:

1. Instala as dependências;
2. Se houver PDFs em `raw/`, extrai os dados (atualiza `data/`);
3. Monta o site em `_site/` a partir de `public/` + `data/`;
4. Publica no GitHub Pages.

O site publicado fica em `https://<usuario>.github.io/manoel/`.

### Por que `data/` é versionado?

Mesmo sem PDFs no push, o site funciona: o workflow usa os `data/*.json` já
commitados. O `raw/` (fonte) nunca vai para o repositório, mas o resultado
processado sim — é o que garante o site sempre no ar.

## Convenções e formato dos dados

Cada arquivo diário tem a forma:

```json
{
  "source": "exams_2026-08-05_2026-08-06.pdf",
  "patient": { "name": "...", "nasc": "..." },
  "n_groups": 38,
  "generated_at": "2026-08-05T14:45:55",
  "groups": [
    {
      "kind": "lab",                    // "lab" ou "img"
      "setor": "Internado",             // "Pronto socorro" | "Internado" | "Imagem"
      "title": "UREIA, plasma",
      "data": "04/08/2026",
      "blocks": [...],                  // material, coleta, metodo
      "measures": [...],                // parâmetro, valor, referência
      "evolution": { "title": "...", "series": [...] },  // séries temporais
      "pages": [0, 1]
    }
  ]
}
```

## Solução de problemas

| Sintoma | Causa provável | Correção |
|---------|----------------|----------|
| `Nenhum PDF encontrado` | `raw/` vazio | Coloque os PDFs em `raw/` |
| Site sem dados ao abrir direto | Abriu `index.html` por `file://` | Sirva via `python -m http.server` |
| `ModuleNotFoundError: fitz` | Dependência não instalada | `pip install -r requirements.txt` |
