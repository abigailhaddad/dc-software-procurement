import json, re, csv, gzip, hashlib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'solicitations'
OUT.mkdir(parents=True, exist_ok=True)

sols = json.load(open(ROOT / 'data/raw/solicitations.json'))
det = json.load(open(ROOT / 'data/raw/contract_details.json'))
cls = list(csv.DictReader(open(ROOT / 'data/contract_classifications.csv')))
det_by_num = {c['contractNumber']: c for c in det}
tech = [r for r in cls if r['is_technology'] == 'True']

# Award text has accumulated in four caches, not one: the original pull, the
# OCR pass over scanned awards, and the two the D&F reading added. Scanning only
# the first missed award text for 199 technology contracts and undercounted the
# high-precision traces -- the ones where an award document prints its own
# solicitation number -- by a third.
TXT_DIRS = [
    ROOT / 'data/pdftext',
    ROOT / 'data/pdftext_ocr',
    ROOT / 'data/pdftext_df',
    ROOT / 'data/pdftext_df_ocr',
]
CACHED = {}
for _d in TXT_DIRS:
    for _p in _d.glob('*.txt.gz'):
        CACHED.setdefault(_p.name[:-7], _p)
have = set(CACHED)

sol_by_doc = {}
for s in sols:
    m = re.fullmatch(r'Doc(\d+)', s['solicitationNumber'].strip())
    if m: sol_by_doc[m.group(1)] = s
solnum_set = set()
for s in sols:
    n = re.sub(r'[\s\x1a]', '-', s['solicitationNumber'].strip().upper())
    solnum_set.add(n)
    for m in re.finditer(r'\b(DC[A-Z]{2}|GAGA|CFOPD)-?(\d{4})-?([A-Z]{1,2})-?(\d{3,5})\b', s['title'].upper().replace(' ', '')):
        solnum_set.add('-'.join(m.groups()))

DOCID = re.compile(r'\bDoc\s*(\d{5,7})\b')
DASH = r'[-–—]'
DCNUM = re.compile(r'\b(DC[A-Z]{2}|GAGA|CFOPD|DCPL|DCHA|POHC|CFSA)\s*' + DASH + r'\s*(\d{2,4})\s*' + DASH + r'\s*([A-Z]{1,2})\s*' + DASH + r'\s*(\d{3,5})\b')
SCHED = re.compile(r'\b(MOBIS\s?\d{4,8}|ITES\s?\d{4,8}|TS\s?\d{4,6}|GS-\d{2}F-\w{4,6}|DCSS-\d{4}-[A-Z]-\d{3,4}|PODS-\d{4}-[A-Z]-\d{3}(?:-\d{2})?)\b')
CAP = re.compile(r'Solicitation\s+Number\W{0,10}((?:\s*\n){0,3}[^\n]{0,80})', re.I)
NA = re.compile(r'^\s*(N/?A|NONE)\b', re.I)

rows_out = []
stats = Counter()
for r in tech:
    num = r['contract_number']
    c = det_by_num.get(num)
    texts = []
    for d in (c.get('documents') or []) if c else []:
        dig = hashlib.sha1(d['id'].encode()).hexdigest()
        if dig in have:
            texts.append(gzip.open(CACHED[dig], 'rt', errors='replace').read())
    if not texts:
        stats['no_award_text'] += 1
        rows_out.append(dict(contract=num, status='no_award_text', doc_ids='', dc_nums='', sched='', doc_in_pull='', pull_title=''))
        continue
    full = '\n'.join(texts)
    doc_ids = set(DOCID.findall(full))
    dc_nums = set('-'.join(m) for m in DCNUM.findall(full))
    # drop dc_nums that are actually the contract's own number pattern? contract nums are CW\d+ so no clash
    scheds = set(re.sub(r'\s', '', m) for m in SCHED.findall(full))
    cap_na = False
    for m in CAP.finditer(full):
        v = m.group(1)
        if NA.match(v.strip()): cap_na = True
    in_pull = sorted(d for d in doc_ids if d in sol_by_doc)
    num_in_pull = sorted(n for n in dc_nums if n in solnum_set)
    if in_pull or num_in_pull:
        status = 'ref_found_in_pull'
    elif doc_ids or dc_nums:
        status = 'ref_not_in_pull'
    elif scheds:
        status = 'schedule_ref_only'
    elif cap_na:
        status = 'explicit_no_solicitation'
    else:
        status = 'no_ref_in_text'
    stats[status] += 1
    ptitle = '; '.join(sol_by_doc[d]['title'].strip().replace('\n', ' ')[:70] for d in in_pull[:2])
    rows_out.append(dict(contract=num, status=status, doc_ids=';'.join(sorted(doc_ids)),
                         dc_nums=';'.join(sorted(dc_nums)), sched=';'.join(sorted(scheds)),
                         doc_in_pull=';'.join(in_pull) + (('|' + ';'.join(num_in_pull)) if num_in_pull else ''),
                         pull_title=ptitle))

print("tech contracts:", len(tech))
for k, v in stats.most_common(): print(f"  {k}: {v}")
n_text = len(tech) - stats['no_award_text']
print(f"of {n_text} with award text: found-in-pull {stats['ref_found_in_pull']} ({stats['ref_found_in_pull']/n_text:.1%}),",
      f"ref-not-in-pull {stats['ref_not_in_pull']} ({stats['ref_not_in_pull']/n_text:.1%}),",
      f"schedule-only {stats['schedule_ref_only']}, explicit-NA {stats['explicit_no_solicitation']}, nothing {stats['no_ref_in_text']}")

with open(OUT / 'tech_award_doc_solicitation_refs.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows_out[0]))
    w.writeheader()
    for x in rows_out: w.writerow(x)

# sanity: for found-in-pull, print contract title vs solicitation title pairs
print('\n--- sample found-in-pull pairs (contract title <> solicitation title) ---')
shown = 0
tech_by = {r['contract_number']: r for r in tech}
for x in rows_out:
    if x['status'] == 'ref_found_in_pull' and x['pull_title'] and shown < 15:
        print(f"  {tech_by[x['contract']]['title'][:60]:60s} <> {x['pull_title'][:60]}")
        shown += 1
print('\n--- sample ref_not_in_pull (what they cite) ---')
shown = 0
for x in rows_out:
    if x['status'] == 'ref_not_in_pull' and shown < 12:
        print(f"  {x['contract']}: doc={x['doc_ids'][:30]} dc={x['dc_nums'][:40]}")
        shown += 1
