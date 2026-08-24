import json, re, csv, gzip, hashlib, random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path('/Users/abigailhaddad/Documents/repos/dcbudget')
OUT = Path('/private/tmp/claude-501/-Users-abigailhaddad-Documents-repos-dcbudget/4b8cf7a9-c252-4a5c-bae3-b111db8bea9c/scratchpad/solicitations')

sols = json.load(open(REPO/'data/raw/solicitations.json'))
det = json.load(open(REPO/'data/raw/contract_details.json'))
cls = list(csv.DictReader(open(REPO/'data/contract_classifications.csv')))
det_by_num = {c['contractNumber']: c for c in det}
tech = [r for r in cls if r['is_technology']=='True']

NOISE = set('''fy rfq rfp ifb rfi sow request for quotation quotations quote proposal proposals bid bids invitation
dcss cbe sbe vendors vendor only preferred authorized suppliers supplier holders set aside setaside market open
services service the and of a an to for with amp readvertised re advertised'''.split())
def toks(t):
    t = re.sub(r'\bfy\s*\'?\s*\d{2,4}\b',' ',t.lower())
    t = re.sub(r'[^a-z0-9]+',' ',t)
    out=[w for w in t.split() if w not in NOISE and not re.fullmatch(r'\d{2,4}', w) and len(w)>1]
    return out
def key(t): return ' '.join(toks(t))

def pdate(s):
    try: return datetime.strptime(s,'%m/%d/%Y')
    except (TypeError, ValueError): return None

for s in sols:
    s['_k']=key(s['title']); s['_ts']=set(s['_k'].split()); s['_close']=pdate(s['closingDate'])

# index solicitation tokens
inv=defaultdict(list)
for i,s in enumerate(sols):
    for w in s['_ts']: inv[w].append(i)

results=[]
for r in tech:
    c = det_by_num.get(r['contract_number'])
    award = pdate(c.get('awardDate')) if c else None
    k = key(r['title']); ts=set(k.split())
    best=None
    if ts:
        cand=Counter()
        for w in ts:
            for i in inv[w]: cand[i]+=1
        for i,ov in cand.most_common(400):
            s=sols[i]
            if award and s['_close'] and s['_close'] > award + timedelta(days=45): continue
            jac = ov/len(ts|s['_ts'])
            if jac < 0.3: continue
            ratio = SequenceMatcher(None,k,s['_k']).ratio()
            score = max(jac, ratio)
            # containment: solicitation tokens cover contract tokens
            cont = ov/len(ts)
            sc = 0.5*jac+0.5*ratio
            if best is None or sc>best[0]: best=(sc,jac,ratio,cont,i)
    if best:
        sc,jac,ratio,cont,i=best; s=sols[i]
        results.append(dict(contract=r['contract_number'], c_title=r['title'], agency=r['agency'],
            award=c.get('awardDate') if c else None, score=round(sc,3), jac=round(jac,3), ratio=round(ratio,3),
            sol_num=s['solicitationNumber'].strip(), sol_title=s['title'].strip().replace('\n',' '),
            sol_fy=s['archive_fy'], sol_close=s['closingDate'],
            agency_match=r['agency'].strip() in [ (a or '').strip() for a in s['agencyNames']]))
    else:
        results.append(dict(contract=r['contract_number'], c_title=r['title'], agency=r['agency'],
            award=c.get('awardDate') if c else None, score=0, jac=0, ratio=0, sol_num='', sol_title='',
            sol_fy='', sol_close='', agency_match=False))

for th in (0.9,0.8,0.7,0.6,0.5):
    n=sum(1 for x in results if x['score']>=th)
    print(f"tech contracts with best title-match score>={th}: {n} ({n/len(tech):.1%})")
n_am=sum(1 for x in results if x['score']>=0.7 and x['agency_match'])
print("score>=0.7 AND same agency:", n_am)

with open(OUT/'tech_contract_title_matches.csv','w',newline='') as f:
    w=csv.DictWriter(f, fieldnames=list(results[0])); w.writeheader()
    for x in sorted(results,key=lambda x:-x['score']): w.writerow(x)

random.seed(7)
print("\n--- sample matches 0.7-0.85 (audit) ---")
mid=[x for x in results if 0.7<=x['score']<0.85]
for x in random.sample(mid,min(12,len(mid))):
    print(f"  [{x['score']}] {x['c_title'][:70]}  <==>  {x['sol_title'][:70]}")
print("\n--- sample matches 0.5-0.7 ---")
mid=[x for x in results if 0.5<=x['score']<0.7]
for x in random.sample(mid,min(12,len(mid))):
    print(f"  [{x['score']}] {x['c_title'][:70]}  <==>  {x['sol_title'][:70]}")
json.dump(results, open(OUT/'_title_matches.json','w'))
