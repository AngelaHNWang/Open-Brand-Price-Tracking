"""
Appends candidate sheets to the EXISTING model_candidates.xlsx for GfK
products that are newly current (e.g. after switching the Top-5 ranking to
a different year filter) without touching any sheet you've already edited.
"""
import pandas as pd
import numpy as np
import openpyxl

import weekly_price_tracker as wpt

WORKSPACE_DIR = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
OUT_FILE = f"{WORKSPACE_DIR}\\model_candidates.xlsx"
SHEET_INDEX_FILE = f"{WORKSPACE_DIR}\\model_candidates_sheet_index.csv"
MAX_SHEET_NAME = 31


def clean_price(x):
    if pd.isna(x):
        return np.nan
    return float(str(x).replace('$', '').replace(',', ''))


hist = pd.read_csv(wpt.TRACKED_HISTORY_FILE)
cur = hist[hist['Is_Current'] == True].drop_duplicates('GfK_Key').copy()

sheet_idx = pd.read_csv(SHEET_INDEX_FILE)
existing_keys = set(sheet_idx['GfK_Key'])
used_sheet_names = set(sheet_idx['Sheet_Name'])

to_add = cur[~cur['GfK_Key'].isin(existing_keys)].copy()
print(f"{len(to_add)} new GfK product(s) need candidate sheets: {to_add['GfK_Key'].tolist()}")

if to_add.empty:
    print("Nothing to add.")
    raise SystemExit(0)

mm = pd.read_csv(wpt.MASTER_MAPPING_FILE)
current_ob_by_gfk = {}
for gfk, sub in mm.dropna(subset=['OB_Key']).groupby('GfK_Key'):
    current_ob_by_gfk[gfk] = set(sub['OB_Key'].str.strip().str.lower())

print("Reading Open Brand data...")
ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU', 'Net Price', 'Country']
df = pd.read_csv(wpt.OPEN_BRAND_PATH, usecols=ob_cols)
df = df[df['Country'].isin(wpt.OB_COUNTRY_FILTER)].copy()
df[['Processor', 'GPU']] = df[['Processor', 'GPU']].fillna('')
df['NetPrice'] = df['Net Price'].apply(clean_price)
df['BrandLower'] = df['Brand'].str.lower()

specs = (
    df.groupby(['BrandLower', 'Product Family', 'Processor', 'GPU'])
    .agg(N_Listings=('NetPrice', 'size'), Min_Price=('NetPrice', 'min'),
         Median_Price=('NetPrice', 'median'), Max_Price=('NetPrice', 'max'))
    .reset_index()
)


def sheet_name_for(gfk_key):
    base = gfk_key[:MAX_SHEET_NAME]
    name = base
    n = 1
    while name in used_sheet_names:
        suffix = f"_{n}"
        name = base[:MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    used_sheet_names.add(name)
    return name


new_sheet_index_rows = []
sheets_to_write = {}
for _, r in to_add.iterrows():
    gfk = r['GfK_Key']
    brand = gfk.split()[0].lower()
    current_ob_set = current_ob_by_gfk.get(gfk, set())

    cand = specs[specs['BrandLower'] == brand].copy()
    keep = cand.apply(
        lambda c: wpt._cpu_tier_ok(gfk, c['Processor']) and wpt._gpu_tier_ok(gfk, c['GPU']), axis=1
    )
    cand = cand[keep].drop(columns=['BrandLower'])

    def is_current(row):
        full = f"{brand.title()} {row['Product Family']} {row['Processor']} {row['GPU']}".strip()
        full = ' '.join(full.split()).lower()
        return full in current_ob_set

    cand['Currently_Mapped'] = cand.apply(is_current, axis=1) if len(cand) else []
    cand = cand.sort_values(['Currently_Mapped', 'N_Listings'], ascending=[False, False])
    cand.insert(0, 'GfK_Key', gfk)
    cand.insert(1, 'GfK_CPU', r.get('CPU_G', ''))
    cand.insert(2, 'GfK_GPU', r.get('GPU_G2', ''))

    cols = ['GfK_Key', 'GfK_CPU', 'GfK_GPU', 'Product Family', 'Processor', 'GPU',
            'Currently_Mapped', 'N_Listings', 'Min_Price', 'Median_Price', 'Max_Price']
    cand = cand[cols].round({'Min_Price': 0, 'Median_Price': 0, 'Max_Price': 0})

    sheet_name = sheet_name_for(gfk)
    sheets_to_write[sheet_name] = cand
    new_sheet_index_rows.append({'Sheet_Name': sheet_name, 'GfK_Key': gfk})
    print(f"  {gfk}: {len(cand)} candidate rows -> sheet '{sheet_name}'")

print(f"\nAppending {len(sheets_to_write)} sheet(s) to {OUT_FILE}...")
with pd.ExcelWriter(OUT_FILE, engine='openpyxl', mode='a', if_sheet_exists='error') as writer:
    for sheet_name, cand in sheets_to_write.items():
        cand.to_excel(writer, sheet_name=sheet_name, index=False)

updated_index = pd.concat([sheet_idx, pd.DataFrame(new_sheet_index_rows)], ignore_index=True)
updated_index.to_csv(SHEET_INDEX_FILE, index=False)

print(f"Done. {OUT_FILE} now has {len(used_sheet_names)} sheets total "
      f"({len(existing_keys)} kept as you left them, {len(sheets_to_write)} newly added for review).")
