# MELHORIAS.md — Análise consolidada da auditoria

Análise de melhoria gerada a partir da auditoria multi-agente (5 subagentes em
paralelo — organização, corretude, frontend, sanidade, datas) sobre o estado do
repo em **2026-08-05** (`data/` = 5 dias, 162 grupos: 157 lab + 5 img).

## Veredito geral

- **P1: 0** — nenhum dado clinicamente impossível, nenhum mojibake, nenhum valor
  numérico inventado/errado nas séries, nenhuma data inválida.
- **`check_data.py` → PROBLEMAS: 0**; JS do dashboard com sintaxe válida;
  site renderiza 162 exames / 5 dias.
- Dados estão **corretos**, mas com defeitos de **representação** (P2/P3):
  qualitativos perdidos de `measures`, medidas espúrias, rótulos contaminados.

## Já corrigido nesta rodada (commit `...`)

| # | Correção | Local | Efeito |
|---|---|---|---|
| 1 | Pontos duplicados `(datetime, value)` em séries de hemocultura (10 séries, 01/08) | `parse_evolution` | dedup adjacente → 0 duplicados |
| 2 | Pseudo-medidas "Data da coleta"/"Data e hora da coleta" (162 ocorrências) | `extract_measures` | removidas; info segue em `blocks[].coleta` |
| 3 | Chip "Imagem" duplicado no summary de cards `img` | `public/index.html` | só `setorChip` mostra Imagem |

## Pendências por prioridade

### P2 — representação (corrigir no parser)

1. **Resultados qualitativos fora de `measures`**:
   - CULTURA DE URINA JATO MEDIO (01/08 G31): "Parcial negativo" só em `result_text`.
   - CULTURA PARA ENTEROCOCCUS VRE (01/08 G32) e BACILOS GRAM-NEGATIVOS (02/08 G0):
     "N E G A T I V A" ausente de `measures`.
   - Os valores estão preservados (result_text/séries); falta representá-los como medida.
2. **VANCOMICINA (03/08 G22 e 04/08 G1)**:
   - valor atual (26,5 / 23,2 mg/L) fora de `measures` (só na série);
   - `g.data` = data do resultado anterior (03/08) vs coleta 04/08;
   - measure espúria `{"name": null, "value": "4580621655 03/08/2026", "reference": "26,5"}`
     (linha de "RESULTADOS ANTERIORES" com colunas trocadas);
   - referência "Infecções graves por MRSA: ver NOTAS 1, 2 e 3" perdida.
3. **Hemograma — rótulos contaminados**:
   - 4 measures com nome fundido "X MIL": "QUARENTA E OITO MIL VOLUME
     PLAQUETÁRIO MÉDIO" etc. (linha do PDF "48 MIL" colada no nome seguinte);
   - G28 (05/08 05:10): "TOTAL DE PLAQUETAS" perdido → measure
     `name=null value="69.000/mm3"`; VPM e IPF sem medida atual (histórico).

### P3 — exibição/consistência

1. **Agrupamento por data de coleta**: 23 grupos com `g.data` = dia do pedido
   (coleta pós-meia-noite, ex.: 04/08 pedido / 05/08 00:51 coleta). Fiel ao PDF;
   decidir se o site deve agrupar por `blocks[].coleta`.
2. **measures com `name=null`**: 123 casos (linha valor+referência, nome = título
   do grupo) — por design; revisar exceções (VANCOMICINA, G28).
3. **PCR**: `reference="Ver resultado tradicional"` não-numérico (fiel ao PDF;
   série complementar em mg/L existe).
4. **Texto de referência virando measure**: "Infecções graves por outros
   microrganismos" / "Outras infecções NÃO graves" (VANCOMICINA).
5. **Grupo "** Resultado incorporado em outro exame **"** (04/08 G0): placeholder
   inerte — filtrar no parser ou esconder no frontend.
6. **Frontend**:
   - chip da cronologia usa só `k.unit` estático (ignora unidade real da série);
   - busca de card-chave usa texto próprio ≠ `groupSearchText` (buscar "17,4" não
     acha PCR);
   - `semref` contado em `renderStats` mas nunca exibido;
   - `fileDate` no `<h3>` sem `esc()` (escape único faltante).
7. **Gasometria venosa pO2 115/146 + SO2 97/99%** (05/08, 2 grupos): provável
   amostra arterial rotulada venosa — dado fiel à fonte; anotar, não corrigir.

### Dívida técnica / melhorias estruturais

1. **Tailwind CDN de dev** (`cdn.tailwindcss.com`) → CSS pré-compilado (build
   único ou arquivo CSS estático). Remove dependência de runtime + noflash.
2. **Busca sem acento**: normalizar (ex.: "ureia" deveria achar "UREIA").
3. **Testes automatizados do parser** (pytest): fixar regressões dos casos acima
   (dedup, qualitativos, coleta, "X MIL") e travar schema.
4. **Convenção data-vs-coleta** documentada em AGENTS.md (agrupar por pedido ou
   por coleta; hoje é por `data` do cabeçalho).
5. **CI**: rodar `check_data.py` já existe; considerar smoke test do dashboard
   (ex.: contar grupos renderizados) num passo extra.

## Como verificar cada pendência

- P2-1/P2-2/P2-3: rodar `python scripts/extract_exams.py` + `check_data.py` e
  conferir `measures` dos grupos citados.
- P3 frontend: smoke test local (`python -m http.server 8000` → `/public/`).
- Roadmap: auditoria multi-agente (AUDITORIA.md) de novo após cada bloco de fixes.
