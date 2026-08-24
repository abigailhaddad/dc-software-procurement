"""Build the DCAS roster and its payments-by-year table into data/dcas/.

    uv run python analysis/build_dcas.py

Which contracts count as DCAS is a judgement, so every row carries the rule that
put it there in `inclusion_rule`. verify_claims.py checks the totals this writes.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

OUT = ROOT / "data" / "dcas"
OUT.mkdir(parents=True, exist_ok=True)
cls = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
con = parse.load_contracts()
pay = parse.load_payments()

# ---------- inclusion rules ----------
m_sys = cls['system_name'] == 'DCAS'
m_title = cls['title'].str.contains('DCAS', case=False, na=False)
m_curam = cls['title'].str.contains('Curam', case=False, na=False) & ~m_title
rules = {}
for i in cls.index:
    r = []
    if m_sys[i]: r.append('A:system_name=DCAS')
    if m_title[i]: r.append('B:title names DCAS')
    if m_curam[i]: r.append('C:Curam-named (DCAS platform licensor)')
    if r: rules[cls.loc[i,'contract_number']] = '; '.join(r)
# D: award-PDF evidence — the contract's own award document says the work is DCAS O&M
rules['CW95738']  = 'D:award PDF says "Staffing solution to support the operations and maintenance of DCAS"'
rules['CW112488'] = 'D:award PDF says "Operations and Maintenance Services of the DCAS"; monthly invoices identical to CW121488 ($3,490,537.83)'
# E: vendor-inference — vendor has no visible DHCF business other than DCAS/Curam
rules['CW111054'] = 'E:Merative payment at DHCF; Merative sells only Curam (DCAS platform); no portal contract record'
rules['CW134380'] = 'E:Merative payment at DHCF (FY2026); no portal contract record'
rules['CW49026']  = 'E:Infosys Public Services payment at DHCF FY2017-18; Infosys was the original DCAS integrator (2013 press release, saved source); no portal contract record'
rules['CW83476']  = 'D:Diona mobile-app contract; award PDF names DCAS; District Direct Mobile is the DCAS mobile front end'
rules['CW99465']  = 'D:Diona mobile-app contract; award PDF names DCAS'
rules['CW101930'] = 'D:Diona SME/PM contract; award PDF names DCAS 19 times'
rules['DHCF-2017-C-0026'] = 'D:legacy contract number; vouchers cross-reference it to CW62227 (DCAS R3)'
rules['DHCF-2017-C-0026 (CW62227)'] = 'D:voucher contract field explicitly cross-references CW62227 (DCAS R3)'
rules['CW16474']  = 'D:CW16474 is the solicitation number of the 2012 RFP titled District of Columbia Access System (DCAS) Implementation (saved source: hbx_dcas_rfp.pdf); Infosys payments FY2017'

roster = cls[cls.contract_number.isin(rules)].copy()
extra = [n for n in rules if n not in set(roster.contract_number)]
extra_rows = []
titles_known = {'CW112488': 'Operations and Maintenance Services of the DCAS (title from award PDF; not in portal contracts module)'}
vendors_known = {'CW112488':'D2Sol Inc','DHCF-2017-C-0026':'KPMG, LLP','DHCF-2017-C-0026 (CW62227)':'KPMG, LLP','CW111054':'Merative US L.P.','CW134380':'Merative US L.P.',
                 'CW49026':'Infosys Public Services','CW16474':'Infosys Public Services'}
ceil_known = {'CW112488': None}  # base year $41,886,454 per PDF; total ceiling not published
for n in extra:
    row = cls[cls.contract_number==n]
    if len(row): continue
    extra_rows.append({'contract_number': n, 'title': titles_known.get(n,''), 'vendor': vendors_known.get(n,''),
                       'ceiling': float('nan'), 'work_type':'', 'system_name':''})
roster = pd.concat([roster, pd.DataFrame(extra_rows)], ignore_index=True)
roster['inclusion_rule'] = roster.contract_number.map(rules)

# ---------- portal contract fields ----------
agg = con.groupby('contract_number').agg(
    agency=('agency','first'), contract_type=('contract_type','first'),
    n_option_periods=('id','count'), sum_period_amounts=('amount','sum'),
    start_date=('start_date','min'), end_date=('end_date','max'), award_date=('award_date','min'))
roster = roster.rename(columns={'agency':'agency_cls','contract_type':'contract_type_cls'})
roster = roster.merge(agg, on='contract_number', how='left')
pay_agency = pay.dropna(subset=['contract_number']).groupby('contract_number')['agency'].agg(lambda x: x.mode().iat[0] if len(x) else '')
roster['agency'] = roster['agency'].fillna(roster.get('agency_cls')).fillna(roster.contract_number.map(pay_agency))
roster['contract_type'] = roster['contract_type'].fillna(roster.get('contract_type_cls'))
assert (roster.dropna(subset=['ceiling','sum_period_amounts']).eval('abs(ceiling-sum_period_amounts)') < 1).all()

# ---------- payments by contract number ----------
p = pay[pay.contract_number.isin(roster.contract_number)].sort_values('payment_date').drop_duplicates('payment_id', keep='first')
roster = roster.merge(p.groupby('contract_number').agg(
    payments_total=('amount','sum'), n_payments=('payment_id','count'),
    first_payment=('payment_date','min'), last_payment=('payment_date','max')), on='contract_number', how='left')
roster['payments_total'] = roster['payments_total'].fillna(0)

cols = ['contract_number','vendor','title','agency','work_type','contract_type','ceiling',
        'payments_total','n_payments','start_date','end_date','award_date','n_option_periods',
        'system_name','inclusion_rule','first_payment','last_payment']
roster = roster[cols].sort_values(['ceiling'], ascending=False)
roster.to_csv(OUT / 'dcas_roster.csv', index=False)

# ---------- payments by year ----------
p2 = p.copy()
p2['attribution'] = 'contract_number_on_voucher'
# vendor-inference null-contract payments at DHCF, for vendors whose DHCF work is DCAS-only
dh = pay[pay.agency.str.contains('Health Care Finance', na=False)]
null = dh[dh.contract_number.isna()]
infer = {
 'KPMG': 'KPMG null-contract at DHCF; KPMG\'s only DHCF contract is CW62227 DCAS R3',
 'D2SOL': 'D2Sol null-contract at DHCF; monthly invoices match CW121488 DCAS O&M rate',
 'CDE SYSTEM': 'CDE Systems null-contract at DHCF; only DHCF contract is CW87331 DCAS App Dev Team',
 'DIONA': 'Diona null-contract at DHCF; Diona builds the DCAS/District Direct mobile app',
 'MERATIVE': 'Merative null-contract at DHCF; Merative sells only Curam, the DCAS platform',
 'NTE SYSTEMS': 'NTE Systems null-contract at DHCF; only DHCF contract is CW95738 DCAS O&M staffing',
}
inf_rows = []
for key, why in infer.items():
    s = null[null.vendor.str.upper().str.contains(key, na=False)].copy()
    s['attribution'] = 'vendor_inference: ' + why
    inf_rows.append(s)
p_all = pd.concat([p2] + inf_rows, ignore_index=True)
ndup = p_all.payment_id.duplicated().sum()
dup_amt = p_all[p_all.payment_id.duplicated()].amount.sum()
print(f'deduped {ndup} duplicate payment_id rows worth {dup_amt:,.2f} (same voucher listed under two payment runs)')
p_all = p_all.sort_values('payment_date').drop_duplicates('payment_id', keep='first')
p_all['vendor_norm'] = p_all.vendor.map(parse.normalize_vendor)
byfy = (p_all.groupby(['fiscal_year','vendor_norm', p_all.contract_number.fillna('(none on voucher)'), 'attribution'])['amount']
        .sum().reset_index().rename(columns={'level_2':'contract_number'}).sort_values(['fiscal_year','amount'], ascending=[True,False]))
byfy.to_csv(OUT / 'dcas_payments_by_year.csv', index=False)

pd.set_option('display.width',300); pd.set_option('display.max_colwidth',60)
print('=== HEADLINES ===')
print('Roster contracts:', len(roster))
print('  rule A (system_name=DCAS):', m_sys.sum(), 'ceiling', round(cls[m_sys].ceiling.sum(),2))
print('  rules A-C (portal titles/system):', len(cls[cls.contract_number.isin(rules)]), 'ceiling', round(cls[cls.contract_number.isin(rules)].ceiling.sum(),2))
print('  CW95738 ceiling (rule D, in portal):', float(cls[cls.contract_number=='CW95738'].ceiling.iloc[0]))
print('Payments, contract-number-tagged:', round(p2.amount.sum(),2))
print('Payments, vendor-inference additions:', round(sum(s.amount.sum() for s in inf_rows),2))
print('Total visible DCAS-associated payments:', round(p_all.amount.sum(),2))
print()
print(p_all.groupby('fiscal_year')['amount'].sum().round(0).to_string())
print()
print(p_all.pivot_table(index='vendor_norm', columns='fiscal_year', values='amount', aggfunc='sum', fill_value=0, margins=True, margins_name='TOTAL').round(0).to_string())
