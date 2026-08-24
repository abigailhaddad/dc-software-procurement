import re, html, glob, statistics, collections, sys

DCMETRO = ['WASHINGTON, DC','ARLINGTON, VA','ALEXANDRIA, VA','SILVER SPRING, MD','BETHESDA, MD','ROCKVILLE, MD','MCLEAN, VA','FALLS CHURCH, VA','RESTON, VA','HERNDON, VA','FAIRFAX, VA','VIENNA, VA','TYSONS','GREENBELT, MD','LANHAM, MD','LARGO, MD','HYATTSVILLE, MD','COLLEGE PARK, MD','GAITHERSBURG, MD','GERMANTOWN, MD','SPRINGFIELD, VA','CHANTILLY, VA','UPPER MARLBORO, MD','CAPITOL HEIGHTS, MD','BOWIE, MD','LAUREL, MD','COLUMBIA, MD','ANNANDALE, VA','ASHBURN, VA','STERLING, VA','WOODBRIDGE, VA']

def parse(fn):
    raw = open(fn).read()
    rows = re.findall(r'<tr>(.*?)</tr>', raw, re.S)
    out = []
    for r in rows:
        cells = [html.unescape(re.sub(r'<[^>]+>','',c)).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)]
        if len(cells) >= 6 and cells[2].replace(',','').isdigit():
            out.append(cells)
    return out

for fn in sorted(glob.glob('*.html')):
    rows = parse(fn)
    print("="*70)
    print(fn, "-> filings:", len(rows))
    if not rows: continue
    emps = collections.Counter(r[0] for r in rows)
    print("EMPLOYERS:", dict(emps.most_common(10)))
    yrs = collections.Counter(r[4][-4:] for r in rows)
    print("YEARS:", dict(sorted(yrs.items())))
    wages = sorted(int(r[2].replace(',','')) for r in rows)
    print("WAGE min/med/max:", wages[0], int(statistics.median(wages)), wages[-1])
    titles = collections.Counter(r[1] for r in rows)
    print("TOP TITLES:", dict(titles.most_common(8)))
    cities = collections.Counter(r[3] for r in rows)
    print("TOP CITIES:", dict(cities.most_common(10)))
    dc = [r for r in rows if any(m in r[3] for m in DCMETRO)]
    print("DC-METRO filings:", len(dc))
    dccities = collections.Counter(r[3] for r in dc)
    print("DC-METRO cities:", dict(dccities.most_common(15)))
    dcdc = [r for r in rows if r[3]=='WASHINGTON, DC']
    if dcdc:
        print("WASHINGTON DC rows (title, wage, year):")
        for r in dcdc[:25]:
            print("   ", r[0], "|", r[1], "|", r[2], "|", r[4])
