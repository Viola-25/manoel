# AUDITORIA.md — Checagem geral com múltiplos agentes

Checklist e prompts prontos para auditar o pipeline **PDF → JSON → frontend**
com vários subagentes em paralelo. Foco principal: **organização dos dados**
(structura/schema) e **corretude** (valores batem com o PDF de origem).

## Quando rodar

- Após rodar `python scripts/extract_exams.py` e antes de commitar `data/`.
- Quando o dashboard mostrar algo suspeito (filtro, card-chave, cronologia).
- Antes de push de release/atualização grande.

## Pré-requisitos

1. `python scripts/check_data.py` → **`PROBLEMAS: 0`** (obrigatório).
2. `node --check` no JS solto do `public/index.html` (extrair `<script>` principal).
3. Smoke test local: `python -m http.server 8000` → `/public/` no navegador.

## Como rodar os agentes

Disparar via tool **Task** (`subagent_type: explore` ou `general`), **em paralelo**,
um prompt por agente. Cada agente é **read-only** (não altera arquivos); reporta achados
com `arquivo:linha` ou `grupo/índice`.

```
Launch 4-5 tasks in parallel, one per prompt below.
Each agent: research only, do NOT modify files. Report findings with
data/<arquivo>.json grupo N / séries "nome" / public/index.html:linha.
```

---

## Agente 1 — Organização / schema

**Objetivo:** conferir se `data/` segue o schema do AGENTS.md e o envelope de `all_exams.json`.

Prompt:

```
Audite a ORGANIZAÇÃO dos dados do projeto manoel (repo raiz).
Compare cada arquivo de data/ com o schema documentado em AGENTS.md
(arquivo por dia; envelope all_exams.json {meta,data}; grupos lab/img).

Cheque e reporte:
1. all_exams.json: envelope {meta:{version,schema,generated_at,count,errors}, data:[...]};
   meta.count == len(data); meta.errors vazio.
2. 1 arquivo diário por PDF; nome do JSON deriva do PDF; source == nome do PDF.
3. Campos obrigatórios por grupo:
   - lab: kind,setor,ficha,atendimento,data,medico,nasc,title,pages,blocks,measures,
     recebido,responsavel,result_text,evolution(null|{title,series[]})
   - img: kind,setor,data,medico,paciente,nasc,rg,atendimento,title,title_lines,
     text,responsavel,pages
4. Séries de evolution: cada uma com name,name_lines,reference,points[]; pontos com
   datetime ISO (YYYY-MM-DDTHH:MM), raw, value, num.
5. Dias em ordem cronológica; datas consistentes entre arquivos diários e o dia em all_exams.json.
6. Duplicidade: mesmo grupo/série aparecendo duas vezes no mesmo dia sem justificativa.

Retorne: lista de problemas (arquivo, grupo/índice, campo, descrição) + "ORGANIZAÇÃO: OK" se limpo.
```

---

## Agente 2 — Corretude dos valores laboratoriais

**Objetivo:** os valores exibidos (measures, séries, result_text) batem com o
conteúdo bruto extraído do PDF? Sem mojibake, sem valores perdidos.

Prompt:

```
Audite a CORRETUDE dos dados laboratoriais do projeto manoel (repo raiz).

Para cada grupo kind=="lab" em data/*.json:
1. Comparar measures[] e evolution.series[].points[] com o result_text do mesmo
   grupo: todo valor numérico presente em result_text deveria ter contraparte
   parseada (name/value/reference corretos); e todo value parseado deveria
   aparecer (sem inventar dados).
2. Datas de coleta: formato dd/mm/aaaa HH:MM:SS consistente; séries com pontos
   datados dentro do intervalo do PDF (source).
3. Acentos/mojibake: procurar caracteres corrompidos (Ã/, �, "�") em names,
   titles, values, references.
4. Números: conversão de vírgula decimal para num correta (ex.: "3,6" -> 3.6);
   valores com [*] tratados como null; unidades (g/dL, mg/dL, %, mmHg, mmol/L).
5. Referências: parseáveis por "a"/"e", "Até", "Superior a"/"Acima de";
   referência ausente vs "Sem referência" coerente.
6. Resultados qualitativos (ex.: cultura, hemocultura) representados sem perda
   (não sumirem do measures).

Retorne: problemas (arquivo, grupo/índice, série, valor, descrição) + "CORRETUDE: OK" se limpo.
```

---

## Agente 3 — Frontend / exibição

**Objetivo:** o dashboard renderiza o que os dados dizem? Filtros, cards-chave,
cronologia e contagem consistentes.

Prompt:

```
Audite a EXIBIÇÃO do dashboard do projeto manoel (repo raiz, public/index.html).

Leia public/index.html inteiro. Verifique:
1. JS sem erro de sintaxe (extrair <script> principal; node --check).
2. Contagem: "Total de N exames em D dias" == soma real de grupos em all_exams.json;
   filtros (q, setor, status, dia) alteram contagem e lista de forma coerente.
3. Filtro status "sem-ref" NÃO inclui grupos kind=="img".
4. Cards-chave (KEYS): cada key (Hemoglobina, Leucócitos, Plaquetas, PCR, Ureia,
   Creatinina, pH, pCO2, HCO3, BE, pO2) resolve série existente; unidade exibida
   correta (g/dL -> g%, mg/dL intacto).
5. Cronologia (renderCronologia) e timeline coerentes com fileDate/datas reais.
6. groupCardHtml: chip de status coerente com groupStatus(g); details/summary com
   aria-expanded presente; aspas/escape correto (nenhum HTML quebrado).
7. fetch: tenta data/all_exams.json e cai para ../data/all_exams.json; sem erro
   quando um dos dois 404.

Retorne: problemas (arquivo:linha, elemento, descrição) + "FRONTEND: OK" se limpo.
```

---

## Agente 4 — Sanidade clínica e estrutura interna

**Objetivo:** valores biologicamente plausíveis e estrutura sem ruído (medidas
espúrias, títulos duplicados, séries vazias).

Prompt:

```
Audite a SANIDADE dos dados clínicos do projeto manoel (repo raiz, data/*.json).

Cheque e reporte:
1. Faixas plausíveis por exame comum (ex.: glicose 40-1000 mg/dL, sódio 100-200
   mmol/L, potássio 1-10 mmol/L, hemoglobina 2-25 g/dL, plaquetas 1.000-1.000.000).
   Apenas sinalize valores claramente impossíveis (digitados errado / parse errado).
2. Medidas espúrias: nomes que não são exames (ex.: "Data da coleta", linhas de
   cabeçalho virando measures) e quantas ocorrências por arquivo.
3. Séries vazias (points==[]) e grupos com evolution.title vazio mas séries com nome.
4. Títulos de grupo: duplicados no mesmo dia com mesmo setor (hemoculturas
   repetidas, gasometrias múltiplas) — listar, mas marcar quais parecem legítimos.
5. Resultados incorporados ("** Resultado incorporado em outro exame **") e se
   ficaram sem series/measures.

Retorne: problemas (arquivo, grupo/índice, valor, descrição) + "SANIDADE: OK" se limpo.
```

---

## Agente 5 — Datas e cronologia

**Objetivo:** datas de coleta e de arquivo coerentes entre si e com o nome do PDF.

Prompt:

```
Audite as DATAS do projeto manoel (repo raiz, data/*.json).

Cheque e reporte:
1. Para cada grupo, g.data ("dd/mm/aaaa") dentro do intervalo indicado pelo nome
   do source (exams_AAAA-MM-DD_AAAA-MM-DD.pdf).
2. fileDate (frontend: maior g.data do dia; fallback = fim do range do PDF)
   consistente com o dia listado em all_exams.json.
3. Pontos de séries: datetime dentro do range do PDF; ordem cronológica dentro da série.
4. meta.generated_at em all_exams.json == último horário de extração (bater com
   os arquivos diários).
5. Nenhuma data futura/errada de digitação (dia 13/13, mês 00, ano errado).

Retorne: problemas (arquivo, grupo/índice, data, descrição) + "DATAS: OK" se limpo.
```

---

## Pós-auditoria

1. Consolidar achados dos 5 agentes; classificar P1/P2/P3.
2. P1: impedir commit (corrigir parser/dados antes).
3. Re-rodar `check_data.py` + smoke test após qualquer correção.
4. Commit separado por tema (`fix: dados ...`, `fix: parser ...`, `fix: frontend ...`).

## Pendências conhecidas (contexto para os agentes)

- Parser ainda pode gerar medidas espúrias "Data da coleta" (~150 linhas em 5 dias).
- Tailwind é CDN de dev (`cdn.tailwindcss.com`); P2 eventual: CSS pré-compilado.
- Busca do dashboard não normaliza acentos (ex.: "ureia" não acha "UREIA"? — na
  verdade busca é lowercase, mas sem remoção de acento).
- `raw/` é gitignored; agentes só veem `data/` e `public/`, não os PDFs.

## Estado das pendências (2026-08-05)

Já corrigido: "Data da coleta" espúria (removida do measures), pontos duplicados
em hemoculturas (dedup), prefixo `[*]` em séries de gasometria, chip "Imagem"
duplicado, filtro sem-ref incluindo img, unidade g/dL → g%, aria-expanded.
Consolidado em **MELHORIAS.md**: P2 restantes (qualitativos fora de measures,
VANCOMICINA, nomes "X MIL") e P3 (agrupamento data-vs-coleta, PCR "Ver resultado
tradicional", grupo "Resultado incorporado", itens de frontend).
