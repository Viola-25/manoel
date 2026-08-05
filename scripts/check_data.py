import json, glob, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

problems = []
for f in sorted(glob.glob('data/*.json')):
    if f.endswith('all_exams.json'):
        continue
    d = json.load(open(f, encoding='utf-8'))
    print('===', d['source'], d['n_groups'])
    for gi, g in enumerate(d['groups']):
        if g['kind'] == 'img':
            print(f"  [IMAGEM] {g['data']} | {g['title']} | texto={len(g['text'])} linhas | resp={len(g['responsavel'])}")
            continue
        if not g['setor'] or not g['data']:
            problems.append(f"{os.path.basename(f)} grupo {gi}: sem header ({g['title']})")
        nser = len(g['evolution']['series']) if g['evolution'] else 0
        npts = sum(len(s['points']) for s in g['evolution']['series']) if g['evolution'] else 0
        print(f"  [{g['setor']}] {g['data']} | {g['title']} | series={nser} pts={npts} meas={len(g['measures'])}")
print()
print('PROBLEMAS:', len(problems))
for p in problems:
    print(' -', p)
