#!/usr/bin/env python3
"""Recompute every number used in the briefing, from the raw snapshot.

Each claim prints with the figure it asserts and the figure the data gives, so a
mismatch is visible rather than something a reader has to go hunting for. Run
this before publishing anything.

    uv run python analysis/verify_claims.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dcprocure import parse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

FAILURES: list[str] = []


def check(claim: str, computed: object, asserted: object, tolerance: float = 0.0) -> None:
    """Compare an asserted figure against what the data actually gives."""
    if isinstance(computed, (int, float)) and isinstance(asserted, (int, float)):
        ok = abs(float(computed) - float(asserted)) <= tolerance
    else:
        ok = computed == asserted
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {claim}\n         asserted={asserted}  computed={computed}")
    if not ok:
        FAILURES.append(claim)


def main() -> int:
    contracts = parse.load_contracts()
    orders = parse.load_purchase_orders()
    payments = parse.load_payments()
    labels = pd.read_csv(ROOT / "data" / "contract_classifications.csv")
    delivery = pd.read_csv(ROOT / "data" / "delivery_structure.csv")

    print("\n-- corpus --")
    check("contract option periods", len(contracts), 7113)
    check("distinct contracts", contracts.contract_number.nunique(), 3983)
    check("contracts classified", len(labels), 3983)
    check("purchase orders (IT classes)", len(orders), 114255)
    check("payments", len(payments), 866823)
    check("earliest PO fiscal year", int(orders.fiscal_year.min()), 2013)

    # The manifest is the provenance record. If it stops describing the files
    # on disk, rebuild it: uv run python analysis/rebuild_manifest.py
    import json as _json

    manifest = _json.load(open(ROOT / "data" / "raw" / "manifest.json"))
    recorded = {s["name"]: s["rows"] for s in manifest["snapshots"]}
    check("manifest matches contracts on disk", recorded.get("contracts_by_commodity"), len(contracts))
    check("manifest matches purchase orders on disk", recorded.get("purchase_by_commodity"), len(orders))
    check("manifest matches payments on disk", recorded.get("payments"), len(payments))

    print("\n-- what DC buys --")
    tech = labels[labels.is_technology == True]  # noqa: E712
    check("technology contracts", len(tech), 1372)
    check("technology ceilings $M", round(tech.ceiling.sum() / 1e6), 4641, 1)
    for work_type, contracts_n, ceiling_m in [
        ("ordering_vehicle", 328, 1840),
        ("staff_augmentation", 211, 1203),
        ("custom_software_build", 60, 38),
    ]:
        rows = labels[labels.work_type == work_type]
        check(f"{work_type} contracts", len(rows), contracts_n)
        check(f"{work_type} ceilings $M", round(rows.ceiling.sum() / 1e6), ceiling_m, 1)

    print("\n-- ceilings are not spend --")
    # Computed over every contract classified as technology, which is a stable
    # and documented basis. An earlier version used an internal commodity
    # grouping instead and produced 23% and 95%; both were wrong.
    paid = payments.groupby("contract_number")["amount"].sum()
    tech = tech.copy()
    tech["paid"] = tech.contract_number.map(paid).fillna(0.0)
    types = contracts.groupby("contract_number").contract_type.first()
    tech["contract_type"] = tech.contract_number.map(types)
    for label, ctype, pct in [
        ("IDIQ drawdown %", "Indefinite Delivery Indefinite Quantity", 4),
        ("firm fixed price drawdown %", "Firm Fixed Price", 44),
    ]:
        rows = tech[tech.contract_type == ctype]
        check(label, round(100 * rows.paid.sum() / rows.ceiling.sum()), pct, 1)
    check("technology contracts with any linked payment %",
          round(100 * (tech.paid > 0).mean()), 44, 1)

    print("\n-- the payments trap --")
    check("line-level total $B", round(payments.amount.sum() / 1e9, 1), 33.3, 0.1)
    check("check-total field $B", round(payments.check_total.sum() / 1e9, 1), 113.7, 0.1)
    star = payments[payments.contract_number == "CW95481"]
    check("CW95481 correct $M", round(star.amount.sum() / 1e6), 132, 1)
    check("CW95481 if summed wrong $B", round(star.check_total.sum() / 1e9, 1), 17.3, 0.2)
    # The portal is live: counts move a little between pulls, so these carry a
    # tolerance rather than pretending to be exact.
    check("payments with no contract number",
          int(payments.contract_number.isna().sum()), 478700, 200)

    print("\n-- agile language --")
    build_types = {"custom_software_build", "cots_implementation",
                   "software_om_support", "it_consulting_advisory"}
    check("delivery-structure candidates", int(labels.work_type.isin(build_types).sum()), 476)
    check("readable award documents", len(delivery), 288)
    mentions = delivery[delivery.agile_mention_kind != "no_agile_language"]
    check("documents mentioning agile", len(mentions), 38)
    commits = delivery[delivery.agile_mention_kind == "delivery_commitment"]
    check("genuine delivery commitments", len(commits), 11)
    check("mentions that are staff qualifications",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 14)
    gated = (commits.delivery_structure == "sequential_gated_deliverables").sum()
    check("commitments that are structurally gated", int(gated), 9)
    other = delivery[delivery.agile_mention_kind != "delivery_commitment"]
    check("baseline gated %",
          round(100 * (other.delivery_structure == "sequential_gated_deliverables").mean()), 26, 1)

    # Two of the eleven commitments are hand-read corrections of classifier
    # misses. The overrides file must stay in agreement with the gold labels,
    # and every override must actually be applied in the published data.
    import yaml as _yaml

    overrides = _yaml.safe_load((ROOT / "delivery_overrides.yaml").read_text(encoding="utf-8"))
    corrected = overrides.get("agile_mention_kind") or {}
    check("hand-corrected commitment rows", len(corrected), 2)
    gold = pd.read_csv(ROOT / "tests" / "delivery_gold_labels.csv").set_index("contract_number")
    for contract_number, spec in corrected.items():
        check(f"override {contract_number} applied in published data",
              delivery.set_index("contract_number").loc[contract_number, "agile_mention_kind"],
              spec["value"])
        check(f"override {contract_number} recorded in gold labels",
              spec["value"] in str(gold.loc[contract_number, "agile_mention_kind"]).split("|"),
              True)

    print("\n-- vendor concentration (payments to technology contracts, FY2021-2025) --")
    window = payments[
        payments.fiscal_year.between(2021, 2025)
        & payments.contract_number.isin(set(tech.contract_number))
    ].copy()
    window["vendor_key"] = window.vendor.map(parse.normalize_vendor)
    total = window.amount.sum()
    ranked = window.groupby("vendor_key").amount.sum().sort_values(ascending=False)
    check("technology payments FY21-25 $M", round(total / 1e6), 579, 3)
    check("distinct vendors after normalising", ranked.size, 295, 4)
    check("top 1 vendor share %", round(100 * ranked.head(1).sum() / total, 1), 17.4, 0.4)
    check("top 5 vendor share %", round(100 * ranked.head(5).sum() / total, 1), 42.3, 0.6)
    check("top 10 vendor share %", round(100 * ranked.head(10).sum() / total, 1), 52.5, 0.6)
    check("largest vendor is Computer Aid", ranked.index[0], "COMPUTER AID")
    check("largest vendor paid $M", round(ranked.iloc[0] / 1e6, 1), 100.6, 0.5)

    # The alias file is the record of which names were treated as one company.
    # If it stops matching what the rules actually merge, the published
    # concentration figures rest on claims nobody has reviewed.
    import yaml

    aliases = yaml.safe_load((ROOT / "vendor_aliases.yaml").read_text(encoding="utf-8"))
    recorded = {k: sorted(v) for k, v in (aliases.get("merged_by_rule") or {}).items()}
    actual: dict[str, list[str]] = {}
    for raw in window.vendor.dropna().unique():
        actual.setdefault(parse.normalize_vendor(raw), []).append(raw)
    actual = {k: sorted(v) for k, v in actual.items() if len(v) > 1}
    check("alias file matches what the rules merge", actual == recorded, True)
    if actual != recorded:
        missing = sorted(set(actual) - set(recorded))
        stale = sorted(set(recorded) - set(actual))
        print(f"         unrecorded: {missing[:5]}  stale: {stale[:5]}")
        print("         run: uv run python analysis/refresh_vendor_aliases.py")

    print("\n-- the DC Supply Schedule --")
    dcss = tech[tech.title.str.contains("DCSS", case=False, na=False)]
    check("technology contracts naming DCSS", len(dcss), 397, 3)
    check("their ceilings $M", round(dcss.ceiling.sum() / 1e6), 2410, 5)
    check("share of technology ceilings %",
          round(100 * dcss.ceiling.sum() / tech.ceiling.sum()), 52, 1)
    vehicles = tech[tech.work_type == "ordering_vehicle"]
    check("ordering vehicles that are DCSS",
          int(vehicles.title.str.contains("DCSS", case=False, na=False).sum()), 305, 3)
    # The $2.4B mixes pool-vehicle ceilings with DCSS-named awards for actual
    # work; the briefing states the split, so it is checked here.
    non_vehicle = dcss[dcss.work_type != "ordering_vehicle"]
    check("DCSS-named contracts that are not vehicles", len(non_vehicle), 92, 2)
    check("their ceilings $M", round(non_vehicle.ceiling.sum() / 1e6), 844, 5)
    check("of them, staff augmentation",
          int((non_vehicle.work_type == "staff_augmentation").sum()), 75, 2)

    print("\n-- payments against unpublished contract numbers --")
    # The evidence that awards exist beyond what the portal publishes: money
    # moves under portal-style CW numbers that appear nowhere in the published
    # contract list. Some may have been published once and later removed;
    # either way a reader of the portal today cannot look them up.
    recent_pay = payments[payments.fiscal_year.between(2021, 2025)]
    cw_pay = recent_pay[recent_pay.contract_number.astype(str).str.match(r"^CW\d+$", na=False)]
    unpublished = cw_pay[~cw_pay.contract_number.isin(set(contracts.contract_number))]
    check("distinct CW numbers paid FY2021-25", cw_pay.contract_number.nunique(), 3079, 10)
    check("of them, absent from the published list",
          unpublished.contract_number.nunique(), 1471, 10)
    check("payments against absent numbers $B",
          round(unpublished.amount.sum() / 1e9, 1), 0.8, 0.05)

    print("\n-- traceable technology payments by year --")
    tech_pay = payments[
        payments.contract_number.isin(set(tech.contract_number))
        & payments.fiscal_year.between(2021, 2025)
    ]
    by_year = tech_pay.groupby("fiscal_year").amount.sum() / 1e6
    check("traceable technology payments FY2021 $M", round(by_year.loc[2021]), 58, 2)
    check("traceable technology payments FY2025 $M", round(by_year.loc[2025]), 175, 2)

    print("\n-- payments outside the ceiling window --")
    # Payments reach back to FY2017 while contract option periods start in
    # October 2020, so a whole-history comparison counts money paid before any
    # ceiling in this data existed.
    check("earliest payment fiscal year", int(payments.fiscal_year.min()), 2017)
    starts = pd.to_datetime(contracts.start_date, errors="coerce")
    check("earliest option period start", str(starts.min().date()), "2020-10-01")
    pre = payments[payments.fiscal_year < 2021]
    check("payments before FY2021 $B", round(pre.amount.sum() / 1e9, 1), 11.9, 0.1)

    print("\n-- named systems --")
    named = tech[tech.system_name.notna() & (tech.system_name.astype(str).str.strip() != "")]
    named = named[named.system_name.astype(str).str.lower() != "nan"]
    check("technology contracts naming a system", len(named), 273)
    by_system = named.groupby(named.system_name.astype(str).str.upper().str.strip()).agg(
        contracts=("contract_number", "size"), ceiling=("ceiling", "sum")
    ).sort_values("ceiling", ascending=False)
    check("largest named system is DCAS", by_system.index[0], "DCAS")
    check("DCAS contracts", int(by_system.iloc[0].contracts), 28, 1)
    check("DCAS ceilings $M", round(by_system.iloc[0].ceiling / 1e6, 1), 198.3, 1.0)

    print("\n-- delivery structure --")
    check("sequential and gated contracts", int((delivery.delivery_structure == "sequential_gated_deliverables").sum()), 82)
    waterfall = delivery[delivery.delivery_structure == "sequential_gated_deliverables"]
    check("of those, scope fixed in advance", int((waterfall.scope_fixed_in_advance == "yes").sum()), 74)
    check("of those, also commit to agile",
          int((waterfall.agile_mention_kind == "delivery_commitment").sum()), 9)
    check("agile only as a hiring requirement",
          int((delivery.agile_mention_kind == "personnel_qualification").sum()), 14)

    print("\n-- authorised against paid, FY2021-FY2025 window --")
    # The briefing compares ceilings and payments over the same five fiscal
    # years: option periods falling entirely inside FY2021-FY2025 against
    # payments made in those years. Without the window the comparison counts
    # payments made before a ceiling existed against authorisation nobody
    # could have drawn yet.
    cwin = contracts[contracts.contract_number.isin(set(tech.contract_number))].copy()
    cwin["start"] = pd.to_datetime(cwin.start_date, errors="coerce")
    cwin["end"] = pd.to_datetime(cwin.end_date, errors="coerce")
    lo, hi = pd.Timestamp("2020-10-01"), pd.Timestamp("2025-09-30")
    inwin = cwin[(cwin.start >= lo) & (cwin.end <= hi)]
    win_paid = payments[payments.fiscal_year.between(2021, 2025)].groupby("contract_number")["amount"].sum()
    g = inwin.groupby("contract_number").agg(ceiling=("amount", "sum"), ctype=("contract_type", "first"))
    g["paid"] = g.index.map(win_paid).fillna(0.0)
    check("windowed technology contracts", len(g), 1070)
    check("windowed ceilings $M", round(g.ceiling.sum() / 1e6), 3094, 2)
    check("windowed paid $M", round(g.paid.sum() / 1e6), 527, 2)
    check("windowed drawdown %", round(100 * g.paid.sum() / g.ceiling.sum(), 1), 17.0, 0.2)
    for ctype, n, ceil_m, paid_m, pct in [
        ("Indefinite Delivery Indefinite Quantity", 359, 1995, 56, 2.8),
        ("Firm Fixed Price", 575, 668, 313, 46.8),
        ("Labor Hour", 97, 394, 130, 33.0),
    ]:
        rows = g[g.ctype == ctype]
        check(f"windowed {ctype}: contracts", len(rows), n)
        check(f"windowed {ctype}: ceilings $M", round(rows.ceiling.sum() / 1e6), ceil_m, 2)
        check(f"windowed {ctype}: paid $M", round(rows.paid.sum() / 1e6), paid_m, 2)
        check(f"windowed {ctype}: drawdown %", round(100 * rows.paid.sum() / rows.ceiling.sum(), 1), pct, 0.2)

    print("\n-- the CW91684 rate card --")
    # The staffing-contract section quotes the price schedule of Computer Aid's
    # CW91684. Its award PDF is cached locally, so the quoted figures can be
    # re-read from the document text itself.
    import gzip
    import json as _json
    import re
    import statistics

    from dcprocure import documents

    details = _json.load(open(ROOT / "data" / "raw" / "contract_details.json"))
    rec = next(d for d in details if d["contractNumber"] == "CW91684")
    check("CW91684 vendor", rec["vendor"], "COMPUTER AID, INC.")
    check("CW91684 title", rec["title"], "INFORMATION TECHNOLOGY STAFF AUGMENTATION SERVICES")
    check("CW91684 ceiling $M", round(float(rec["contractAmount"].strip("$").replace(",", "")) / 1e6), 65)
    doc = documents.pick_award_document(rec)
    text = gzip.open(documents._text_path(doc["id"]), "rt").read()
    for needle in ("45,000,000", "20,000,000", "Labor Hour", "Fixed Price Project"):
        check(f"CW91684 award text contains '{needle}'", needle in text, True)
    rates = [float(x) for x in re.findall(r"\b(\d{2,3}\.\d{2})\b", text)]
    rates = [x for x in rates if 24 <= x <= 174]
    check("CW91684 hourly rates found", len(rates), 180)
    check("CW91684 lowest rate", min(rates), 24.01)
    check("CW91684 highest rate", max(rates), 173.71)
    check("CW91684 median rate", round(statistics.median(rates), 2), 69.97)

    print("\n-- published hourly rate cards --")
    # Built by analysis/extract_rates.py: every rate re-found verbatim in the
    # award text; the pairing column records whether the category-to-rate
    # assignment could also be verified (deterministic reconstruction for
    # CW91684, text proximity for row-oriented tables).
    lr = pd.read_csv(ROOT / "data" / "labor_rates.csv")
    check("contracts publishing hourly rates", lr.contract_number.nunique(), 51)
    check("published hourly rates", len(lr), 2471)
    check("rates with verified category pairing",
          int(lr.pairing.isin(["deterministic", "proximate"]).sum()), 1629)
    check("lowest published rate", float(lr.hourly_rate.min()), 24.01)
    check("highest published rate", float(lr.hourly_rate.max()), 1015.0)
    check("median published rate", float(lr.hourly_rate.median()), 144.05, 0.5)
    # 325 rates come from OCR-recovered scans; their verification is against
    # the OCR's reading, one model layer from the pixels, and every one of
    # them carries a proximity-verified pairing.
    ocr_rates = lr[lr.text_source == "ocr"]
    check("rates from OCR-recovered scans", len(ocr_rates), 325)
    check("all OCR rates pairing-verified", bool((ocr_rates.pairing == "proximate").all()), True)
    check("native rows unchanged", int((lr.text_source == "native").sum()), 2146)
    cai_card = lr[lr.contract_number == "CW91684"].set_index(["labor_category", "level"])
    for cat, lvl, want in [
        ("Enterprise Architect", "Entry", 75.83),
        ("Web Developer", "Master", 77.00),
        ("Mobile App Developer", "Entry", 70.76),
        ("Cyber Security Engineer", "Master", 143.42),
    ]:
        check(f"CW91684 {cat} ({lvl})", float(cai_card.loc[(cat, lvl)].hourly_rate), want)

    # The disclosure contrast the briefing states: the largest consultancies'
    # rates are not published.
    from dcprocure import documents as _docs
    det_by_cn = {d["contractNumber"]: d for d in details}
    check("KPMG CW62227 has no award document",
          _docs.pick_award_document(det_by_cn["CW62227"]) is None, True)
    check("Ridge IT CW115647 has no award document",
          _docs.pick_award_document(det_by_cn["CW115647"]) is None, True)
    acc_doc = _docs.pick_award_document(det_by_cn["CW67170"])
    acc_text = gzip.open(_docs._text_path(acc_doc["id"]), "rt").read()
    check("Accenture CW67170 award text is a stub (chars)", len(acc_text), 41)

    # The billed-vs-offered comparison quotes job postings. Those pages die, so
    # every quoted posting must be preserved in evidence/job_ads/ with its pay
    # text recorded in the manifest exactly as the briefing states it.
    manifest_text = (ROOT / "evidence" / "job_ads" / "manifest.csv").read_text(encoding="utf-8")
    for needle in ("$74.00 - $75.00 per hour", "$54 - $59 per hour", "$40 - $45 per hour",
                   "$20 - $23 per hour", "$91,000.00 - USD $96,000.00", "$80 - $90/hr"):
        check(f"posting evidence preserved: '{needle}'", needle in manifest_text, True)
    for cat, lvl, want in [("Customer Support", "Senior", 29.31),
                           ("Technical Writer", "Journeyman", 57.35)]:
        check(f"CW91684 {cat} ({lvl}) vs posting", float(cai_card.loc[(cat, lvl)].hourly_rate), want)

    print("\n-- federal rate comparison (CALC+) --")
    # DC rates matched by exact title against GSA's published awarded ceiling
    # rates (data/calc/, with methods and raw API responses). The comparison
    # file is rebuilt by analysis/build_calc_comparison.py.
    calc = pd.read_csv(ROOT / "data" / "calc" / "dc_vs_calc_comparison.csv")
    check("matched DC rate rows", len(calc), 112)
    cai_rows = calc[calc.dc_contract == "CW91684"]
    check("of them, on the CW91684 card", len(cai_rows), 44)
    check("CW91684 median gap vs federal median %",
          round(cai_rows.dc_vs_fed_median_pct.median()), -50, 1)
    check("DC rows below the federal median",
          int((calc.dc_vs_fed_median_pct < 0).sum()), 70)
    check("median gap excluding CW91684 %",
          round(calc[calc.dc_contract != "CW91684"].dc_vs_fed_median_pct.median()), 2, 1)
    ne = calc[(calc.dc_category == "Network Engineer/Specialist") & (calc.dc_level == "Entry")]
    check("Network Engineer entry: DC rate", float(ne.dc_rate.iloc[0]), 40.64)
    check("Network Engineer entry: federal median", float(ne.federal_median.iloc[0]), 108.97)
    cs = calc[(calc.dc_category == "Computer Scientist") & (calc.dc_level == "Entry")]
    check("Computer Scientist entry: federal median", float(cs.federal_median.iloc[0]), 146.08)

    print("\n-- solicitation visibility --")
    # Whether each technology contract traces to any posted solicitation,
    # via award-text citation or title match (data/solicitations/, rebuilt by
    # the analysis/solicitations_* scripts). Matching recall is ~63%, so the
    # briefing states these as "at most about a third of contracts, about a
    # tenth of dollars" after correction — the raw figures are checked here.
    vis = pd.read_csv(ROOT / "data" / "solicitations" / "tech_contract_solicitation_visibility.csv")
    check("technology contracts assessed", len(vis), 1372)
    visible = vis[vis.visibility.str.startswith("visible")]
    check("trace to a posted solicitation", len(visible), 295)
    check("their share of tech ceilings %",
          round(100 * visible.ceiling.sum() / vis.ceiling.sum(), 1), 11.4, 0.2)
    fy = pd.read_csv(ROOT / "data" / "solicitations" / "solicitations_by_fy.csv")
    check("solicitations posted FY2016", int(fy[fy.fiscal_year == "FY2016"].total.iloc[0]), 1351)
    check("solicitations posted FY2025", int(fy[fy.fiscal_year == "FY2025"].total.iloc[0]), 749)
    sols = _json.load(open(ROOT / "data" / "raw" / "solicitations.json"))
    doc_ids = sum(1 for s in sols if str(s.get("solicitationNumber", "")).startswith("Doc"))
    check("solicitation rows showing internal Doc IDs %",
          round(100 * doc_ids / len(sols)), 96, 1)

    print("\n-- the DCAS case study --")
    # data/dcas/ holds the roster and attributed payments (rebuilt by
    # analysis/build_dcas.py); evidence/dcas_sources/ holds every cited page.
    roster = pd.read_csv(ROOT / "data" / "dcas" / "dcas_roster.csv")
    check("DCAS roster contracts", len(roster), 49)
    check("visible DCAS ceilings $M", round(roster.ceiling.sum() / 1e6), 249, 1)
    check("roster contracts with zero tagged payments",
          int((roster.payments_total.fillna(0) == 0).sum()), 19, 1)
    check("roster contracts paid above their surviving ceiling",
          int((roster.payments_total.fillna(0) > roster.ceiling).sum()), 7, 1)
    dcas_pay = pd.read_csv(ROOT / "data" / "dcas" / "dcas_payments_by_year.csv")
    check("payments tied to DCAS $M", round(dcas_pay.amount.sum() / 1e6), 284, 1)

    # The two claims a hostile reader will check first, recomputed from raw:
    kpmg_pay = payments[payments.contract_number == "CW62227"]
    check("KPMG paid citing DCAS R3's number $M", round(kpmg_pay.amount.sum() / 1e6), 50, 1)
    check("DCAS R3's surviving portal record: option periods",
          int((contracts.contract_number == "CW62227").sum()), 1)
    check("that surviving option period $M",
          round(contracts[contracts.contract_number == "CW62227"].amount.iloc[0] / 1e6), 7, 1)
    kpmg_null = payments[
        payments.vendor.astype(str).str.upper().str.startswith("KPMG")
        & payments.contract_number.isna()
        & payments.agency.astype(str).str.contains("Health Care Finance", na=False)
    ]
    check("KPMG null-contract payments at Health Care Finance $M",
          round(kpmg_null.amount.sum() / 1e6), 60, 1)
    d2sol_exact = payments[
        payments.vendor.astype(str).str.upper().str.contains("D2SOL", na=False)
        & (abs(payments.amount - 3490537.83) < 0.01)
    ]
    check("D2Sol monthly O&M vouchers at $3,490,537.83", len(d2sol_exact) >= 17, True)

    # Web-sourced figures must exist verbatim in the saved sources.
    henderson = (ROOT / "evidence" / "dcas_sources" / "henderson_joint_roundtable_dcas.txt").read_text(encoding="utf-8")
    check("source: Council's 'more than $622 million'", "more than $622 million" in henderson, True)
    check("source: Deputy Mayor's 'emphatic' no", "emphatic" in henderson, True)
    check("source: FY2022 SNAP timeliness below half the 2017 lawsuit rate",
          "more than double the Fiscal Year 2022 rate" in henderson, True)
    infosys = (ROOT / "evidence" / "dcas_sources" / "infosyspublicservices_2013_hbx_press_release.txt").read_text(encoding="utf-8")
    check("source: Infosys $49.5M build award", "49.5" in infosys, True)

    print("\n-- the OCR-recovered awards --")
    # 74 scanned award documents were recovered by analysis/ocr_awards.py and
    # classified into data/delivery_structure_ocr.csv (separate file; the
    # native results are untouched). The briefing states combined figures.
    ocr = pd.read_csv(ROOT / "data" / "delivery_structure_ocr.csv")
    check("awards recovered by OCR", len(ocr), 75)
    check("overlap with the native readable set",
          len(set(ocr.contract_number) & set(delivery.contract_number)), 0)
    combined = pd.concat([delivery, ocr], ignore_index=True)
    check("readable awards combined", len(combined), 363)
    check("of 476 candidates, contracts with no award document at all", 476 - 363, 113)
    check("combined recurring operations",
          int((combined.delivery_structure == "recurring_operations").sum()), 108)
    check("combined sequential and gated",
          int((combined.delivery_structure == "sequential_gated_deliverables").sum()), 95)
    gated_combined = combined[combined.delivery_structure == "sequential_gated_deliverables"]
    check("of those, scope fixed in advance",
          int((gated_combined.scope_fixed_in_advance == "yes").sum()), 86)
    combined_commits = combined[combined.agile_mention_kind == "delivery_commitment"]
    check("combined delivery commitments", len(combined_commits), 14)
    check("of those, structurally gated",
          int((combined_commits.delivery_structure == "sequential_gated_deliverables").sum()), 11)
    check("combined agile-as-hiring-requirement",
          int((combined.agile_mention_kind == "personnel_qualification").sum()), 15)

    print("\n-- purchase-order vendor figures --")
    recent = orders[orders.fiscal_year.between(2022, 2026)].copy()
    recent["vendor_key"] = recent.vendor.str.upper().str.strip()
    cai = recent[recent.vendor_key == "COMPUTER AID  INC"]
    check("Computer Aid FY22-26 $M", round(cai.amount.sum() / 1e6), 465, 3)
    check("Computer Aid purchase orders", len(cai), 5787, 40)
    check("Computer Aid median PO $", round(cai.amount.median()), 59944, 200)
    check("Computer Aid distinct agencies", cai.agency.nunique(), 33, 1)
    fast = recent[recent.vendor_key == "FAST ENTERPRISES LLC"]
    check("FAST Enterprises $M", round(fast.amount.sum() / 1e6), 51, 2)
    check("FAST Enterprises purchase orders", len(fast), 11, 1)
    check("FAST median PO $M", round(fast.amount.median() / 1e6, 1), 3.5, 0.2)

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else f'{len(FAILURES)} FAILED: ' + '; '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
