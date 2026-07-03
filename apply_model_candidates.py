"""
Step 2 (v2) — reads your edited model_candidates.xlsx (whatever rows you left
in each sheet after deleting the ones that don't belong) and rebuilds the
weekly price database directly from that allowlist. No more single
exact-OB_Key-per-GfK_Key mapping: every Pricings.csv row whose
(Product Family, Processor, GPU) matches ANY kept row on a sheet, in any
week, is included in that GfK product's weekly average.

This fully replaces weekly_price_database.csv (clean recompute, not an
incremental merge) and rewrites master_mapping.csv with one row per kept
spec per GfK product (so the fuzzy-match/no-match tooling from before still
has something to read, even though a GfK product may now have several rows).

After this runs, do:
    python price_dashboard_generator.py
"""
import pandas as pd
import numpy as np

import weekly_price_tracker as wpt

WORKSPACE_DIR = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
CANDIDATES_FILE = f"{WORKSPACE_DIR}\\model_candidates.xlsx"


def clean_price(x):
    if pd.isna(x):
        return np.nan
    return float(str(x).replace('$', '').replace(',', ''))


print("Reading your edited candidate sheets...")
SHEET_INDEX_FILE = f"{WORKSPACE_DIR}\\model_candidates_sheet_index.csv"
sheet_to_gfk = dict(pd.read_csv(SHEET_INDEX_FILE)[['Sheet_Name', 'GfK_Key']].values)

xls = pd.ExcelFile(CANDIDATES_FILE)
keep_rows = []
all_sheet_gfk_keys = set()
for sheet in xls.sheet_names:
    gfk = sheet_to_gfk.get(sheet)
    if gfk is None:
        print(f"  [!] Sheet '{sheet}' not found in {SHEET_INDEX_FILE} — skipping (was it renamed?)")
        continue
    all_sheet_gfk_keys.add(gfk)
    sdf = xls.parse(sheet)
    if sdf.empty:
        continue
    for _, r in sdf.iterrows():
        keep_rows.append({
            'GfK_Key': gfk,
            'Product Family': r['Product Family'],
            'Processor': r['Processor'],
            'GPU': r['GPU'],
        })
keep_df = pd.DataFrame(keep_rows)
n_keys = keep_df['GfK_Key'].nunique() if len(keep_df) else 0
print(f"Kept {len(keep_df)} spec rows across {n_keys} GfK products "
      f"({len(all_sheet_gfk_keys) - n_keys} sheet(s) ended up with zero rows -> no price data)")

print("Reading Open Brand data...")
ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU', 'Net Price', 'Week', 'Date', 'Country']
df = pd.read_csv(wpt.OPEN_BRAND_PATH, usecols=ob_cols)
df = df[df['Country'].isin(wpt.OB_COUNTRY_FILTER)].copy()
df[['Processor', 'GPU']] = df[['Processor', 'GPU']].fillna('')
df['NetPrice'] = df['Net Price'].apply(clean_price)
df = df.dropna(subset=['NetPrice'])
df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

# Join every raw row against the (brand-implicit) kept specs for each GfK_Key.
# Match on Product Family + Processor + GPU exactly (brand comes along for
# free since these specs were only ever pulled from that GfK product's brand).
merged = df.merge(keep_df, on=['Product Family', 'Processor', 'GPU'], how='inner')
print(f"Matched {len(merged)} raw Pricings.csv rows to a kept spec.")


def iqr_filter_week(grp):
    prices = grp['NetPrice']
    if len(prices) <= 2:
        return grp
    q1, q3 = prices.quantile(0.25), prices.quantile(0.75)
    iqr = q3 - q1
    if iqr > 0:
        return grp[(prices >= q1 - 1.5 * iqr) & (prices <= q3 + 1.5 * iqr)]
    return grp


merged = (
    merged.groupby(['GfK_Key', 'Week'], group_keys=False)
    .apply(iqr_filter_week)
    .reset_index(drop=True)
)

product_medians = merged.groupby('GfK_Key')['NetPrice'].median()
merged['_prod_median'] = merged['GfK_Key'].map(product_medians)
merged = merged[
    (merged['NetPrice'] >= merged['_prod_median'] * 0.30) &
    (merged['NetPrice'] <= merged['_prod_median'] * 3.00)
].drop(columns=['_prod_median'])

weekly_prices = (
    merged.groupby(['GfK_Key', 'Week'])
    .agg(Average_Price=('NetPrice', 'mean'), Date=('Date', 'max'))
    .reset_index()
    .sort_values(['GfK_Key', 'Week'])
)
weekly_prices.to_csv(wpt.WEEKLY_PRICE_DB_FILE, index=False)
print(f"Wrote {len(weekly_prices)} weekly rows -> {wpt.WEEKLY_PRICE_DB_FILE}")

# Rebuild master_mapping.csv: one row per kept spec per GfK product (for the
# non-current historical keys, and any GfK product not in this workbook,
# leave whatever was already there untouched).
mm = pd.read_csv(wpt.MASTER_MAPPING_FILE)
mm_other = mm[~mm['GfK_Key'].isin(all_sheet_gfk_keys)].copy()
new_mm_rows = []
for gfk in all_sheet_gfk_keys:
    rows = keep_df[keep_df['GfK_Key'] == gfk]
    if rows.empty:
        new_mm_rows.append({'GfK_Key': gfk, 'OB_Key': np.nan, 'Score': 0.0, 'Status': 'No-Match-Confirmed'})
        continue
    brand = gfk.split()[0].title()
    for _, r in rows.iterrows():
        ob_key = f"{brand} {r['Product Family']} {r['Processor']} {r['GPU']}".strip()
        ob_key = ' '.join(ob_key.split())
        new_mm_rows.append({'GfK_Key': gfk, 'OB_Key': ob_key, 'Score': 100.0, 'Status': 'Reviewed-MultiSpec'})

new_mm = pd.concat([mm_other, pd.DataFrame(new_mm_rows)], ignore_index=True)
new_mm.to_csv(wpt.MASTER_MAPPING_FILE, index=False)
print(f"Rewrote master_mapping.csv: {len(new_mm)} total rows.")

print("\nNext: python price_dashboard_generator.py")
