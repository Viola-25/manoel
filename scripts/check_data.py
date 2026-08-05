import json, glob, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

LAB_KEYS = ["kind", "setor", "title", "data", "blocks", "measures", "evolution", "pages"]
IMG_KEYS = ["kind", "setor", "title", "data", "text", "responsavel", "pages"]


def has_keys(obj, keys):
    return all(k in obj for k in keys)


problems = []
day_files = [f for f in sorted(glob.glob('data/*.json'))
             if not f.endswith('all_exams.json')]
if not day_files:
    problems.append("nenhum arquivo diario em data/")

for f in day_files:
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        problems.append(f"{os.path.basename(f)}: JSON invalido ({e})")
        continue
    print('===', d.get('source'), d.get('n_groups'))
    for gi, g in enumerate(d.get('groups', [])):
        if g['kind'] == 'img':
            print(f"  [IMAGEM] {g.get('data')} | {g.get('title')} | "
                  f"texto={len(g.get('text', []))} linhas | "
                  f"resp={len(g.get('responsavel', []))}")
            if not has_keys(g, IMG_KEYS):
                problems.append(f"{os.path.basename(f)} grupo {gi}: imagem sem chaves obrigatorias")
            continue
        if not has_keys(g, LAB_KEYS):
            problems.append(f"{os.path.basename(f)} grupo {gi}: lab sem chaves obrigatorias")
        if not g.get('setor') or not g.get('data'):
            problems.append(f"{os.path.basename(f)} grupo {gi}: sem header ({g.get('title')})")
        nser = len(g['evolution']['series']) if g.get('evolution') else 0
        npts = sum(len(s['points']) for s in g['evolution']['series']) if g.get('evolution') else 0
        print(f"  [{g.get('setor')}] {g.get('data')} | {g.get('title')} | "
              f"series={nser} pts={npts} meas={len(g.get('measures', []))}")

allp = 'data/all_exams.json'
try:
    env = json.load(open(allp, encoding='utf-8'))
    meta = env.get('meta', {})
    data = env.get('data', [])
    if meta.get('errors'):
        problems.append(f"all_exams.json: meta.errors nao vazio ({len(meta['errors'])} erro(s))")
    if meta.get('count') != len(data):
        problems.append(f"all_exams.json: meta.count={meta.get('count')} != len(data)={len(data)}")
except Exception as e:
    problems.append(f"all_exams.json: JSON invalido ({e})")

print()
print('PROBLEMAS:', len(problems))
for p in problems:
    print(' -', p)
sys.exit(1 if problems else 0)
