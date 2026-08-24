import json, re, csv, gzip, hashlib, random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'solicitations'
OUT.mkdir(parents=True, exist_ok=True)

sols = json.load(open(ROOT/'data/raw/solicitations.json'))
det = json.load(open(ROOT/'data/raw/contract_details.json'))
cls = list(csv.DictReader(open(ROOT/'data/contract_classifications.csv')))

# ---------- normalization ----------
def norm_market(m):
    if m is None: return 'unknown'
    s=m.strip().lower()
    if not s or s in ('na','tbd'): return 'unknown'
    if 'set aside' in s or 'set-aside' in s: return 'set_aside'
    if s.startswith('open') or 'full and open' in s: return 'open'
    return 'other'

TECH_BASE = re.compile(r"\b(software|information technology|cybersecurity|cyber security|data center|datacenter|network (?:management|equipment|infrastructure|services?|cabling|installation|upgrade)|core network|area network|telecomm?\w*|website|web site|web portal|web-based|application development|app development|mobile app|database|data warehouse|data analytics|cloud|saas|erp|crm|geographic information|gis|licens\w* (?:renewal|subscription)|microsoft|oracle|salesforce|servicenow|adobe|laptops?|desktop computers?|computers?|hardware|servers?|tablets?|ipads?|printers?|copiers?|help ?desk|broadband|wi-?fi|fiber optics?|cctv|video surveillance|surveillance cameras?|security cameras?|cameras?|access control system|voip|telephony|electronic health record|case management system|records? management system|learning management|timekeeping system|payroll system|licensing system|permitting system|e-?procurement|digitization|digital services?)\b", re.I)
TECH_IT = re.compile(r"\bIT\b")
def tech_flag(t): return bool(TECH_BASE.search(t) or TECH_IT.search(t))

DCSS = re.compile(r'\bDCSS\b|supply schedule', re.I)
STAFFAUG = re.compile(r'\bstaff(?:ing)? aug\w*|\btemporary (?:support )?staff\w*|\btemporary staffing|\bIT staff\w*|personnel support services|temp staff', re.I)

for r in sols:
    r['_mk'] = norm_market(r['marketType'])
    r['_tech'] = tech_flag(r['title'])
    r['_dcss'] = bool(DCSS.search(r['title'])) or bool(r['marketType'] and 'dcss' in r['marketType'].lower())
    r['_staffaug'] = bool(STAFFAUG.search(r['title']))
    r['_fy'] = int(r['archive_fy'][2:]) if r['archive_fy'] else None
    try: r['_close'] = datetime.strptime(r['closingDate'],'%m/%d/%Y') if r['closingDate'] else None
    except ValueError: r['_close']=None
    n = r['solicitationNumber'].strip()
    r['_docid'] = bool(re.fullmatch(r'Doc\d+', n))

print("=== headline counts ===")
print("total", len(sols), "open", sum(r['is_open'] for r in sols))
print("Doc-id numbers", sum(r['_docid'] for r in sols))
print("tech-flagged", sum(r['_tech'] for r in sols))
print("dcss", sum(r['_dcss'] for r in sols), "staffaug", sum(r['_staffaug'] for r in sols))
print("dcss+tech", sum(r['_dcss'] and r['_tech'] for r in sols))
print("market:", Counter(r['_mk'] for r in sols))

# ---------- per-year table ----------
with open(OUT/'solicitations_by_fy.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['fiscal_year','total','open_market','set_aside','other_unknown','tech_flagged','dcss_flagged','staff_aug_flagged'])
    for fy in sorted(set(r['_fy'] for r in sols if r['_fy'])):
        g=[r for r in sols if r['_fy']==fy]
        w.writerow([f'FY{fy}',len(g),sum(r['_mk']=='open' for r in g),sum(r['_mk']=='set_aside' for r in g),
                    sum(r['_mk'] in ('other','unknown') for r in g),sum(r['_tech'] for r in g),
                    sum(r['_dcss'] for r in g),sum(r['_staffaug'] for r in g)])
    g=[r for r in sols if r['is_open']]
    w.writerow(['open_now',len(g),sum(r['_mk']=='open' for r in g),sum(r['_mk']=='set_aside' for r in g),
                sum(r['_mk'] in ('other','unknown') for r in g),sum(r['_tech'] for r in g),
                sum(r['_dcss'] for r in g),sum(r['_staffaug'] for r in g)])

# ---------- per-agency table ----------
ag = defaultdict(lambda: [0,0,0])
for r in sols:
    for a in set(x.strip() for x in r['agencyNames'] if x):
        ag[a][0]+=1; ag[a][1]+=r['_tech']; ag[a][2]+=r['_dcss']
with open(OUT/'solicitations_by_agency.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['agency','solicitations','tech_flagged','dcss_flagged'])
    # agency name breaks ties: `ag` is keyed off a set, whose order varies
    # per process, so count alone leaves the row order nondeterministic.
    for a,(n,t,d) in sorted(ag.items(), key=lambda x:(-x[1][0], x[0])):
        w.writerow([a,n,t,d])
print("agencies:", len(ag), "; rows with a null agency entry:", sum(1 for r in sols if any(x is None for x in r['agencyNames'])))

# ---------- tech solicitations vs tech contract awards per FY ----------
tech_contracts = {r['contract_number']: r for r in cls if r['is_technology']=='True'}
det_by_num = {c['contractNumber']: c for c in det}
def contract_fy(c):
    d=c.get('awardDate')
    if not d: return None
    try: dt=datetime.strptime(d,'%m/%d/%Y')
    except ValueError: return None
    return dt.year + (1 if dt.month>=10 else 0)
tc_fy = Counter()
for num in tech_contracts:
    c = det_by_num.get(num)
    if c:
        fy=contract_fy(c)
        if fy: tc_fy[fy]+=1
with open(OUT/'tech_solicitations_vs_awards_by_fy.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(['fiscal_year','tech_flagged_solicitations','tech_contracts_awarded'])
    for fy in range(2016,2027):
        w.writerow([f'FY{fy}', sum(1 for r in sols if r['_fy']==fy and r['_tech']), tc_fy.get(fy,0)])
print("tech contracts:", len(tech_contracts), "with details:", sum(1 for n in tech_contracts if n in det_by_num),
      "award FY dist:", sorted(tc_fy.items()))

json.dump([{k:v for k,v in r.items() if not k.startswith('_')} | {'flags':{k[1:]:r[k] for k in ('_mk','_tech','_dcss','_staffaug')}} for r in sols],
          open(OUT/'_sols_flagged.json','w'))
