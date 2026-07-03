"""
Step 1 (v2) of the human-curated mapping workflow — one worksheet per GfK
product, listing every Open Brand (Product Family, Processor, GPU) spec that
is brand-matched and CPU-tier / GPU-class compatible (same brand + tier
digit for CPU; same integrated-vs-discrete + brand for GPU). No product-line
keyword filtering here — that regex list turned out to be unreliable (it let
ROG Zephyrus match ROG Strix, Ryzen 7 match Ryzen AI 5, etc. in earlier
rounds), so this leans on your own eyes instead.

Delete every row that does NOT belong under a given GfK product. Whatever
rows remain across all sheets become the source of truth: every Pricings.csv
row whose (Product Family, Processor, GPU) matches a kept row, for any week,
gets included in that GfK product's weekly average — no more single exact-
string mapping.

Output: model_candidates.xlsx (one sheet per currently-tracked GfK product)
Next:   after editing, run apply_model_candidates.py
"""
import pandas as pd
import numpy as np

import weekly_price_tracker as wpt

WORKSPACE_DIR = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
OUT_FILE = f"{WORKSPACE_DIR}\\model_candidates.xlsx"

MAX_SHEET_NAME = 31  # Excel hard limit


def clean_price(x):
    if pd.isna(x):
        return np.nan
    return float(str(x).replace('$', '').replace(',', ''))


hist = pd.read_csv(wpt.TRACKED_HISTORY_FILE)
cur = hist[hist['Is_Current'] == True].copy()

mm = pd.read_csv(wpt.MASTER_MAPPING_FILE)
current_ob_by_gfk = dict(zip(mm['GfK_Key'], mm['OB_Key']))

print("Reading Open Brand data...")
ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU', 'Net Price', 'Country']
df = pd.read_csv(wpt.OPEN_BRAND_PATH, usecols=ob_cols)
df = df[df['Country'].isin(wpt.OB_COUNTRY_FILTER)].copy()
df[['Processor', 'GPU']] = df[['Processor', 'GPU']].fillna('')
df['NetPrice'] = df['Net Price'].apply(clean_price)
df['BrandLower'] = df['Brand'].str.lower()

specs = (
    df.groupby(['BrandLower', 'Product Family', 'Processor', 'GPU'])
    .agg(N_Listings=('NetPrice', 'size'),
         Min_Price=('NetPrice', 'min'),
         Median_Price=('NetPrice', 'median'),
         Max_Price=('NetPrice', 'max'))
    .reset_index()
)

used_sheet_names = {}


def sheet_name_for(gfk_key, idx):
    base = gfk_key[:MAX_SHEET_NAME]
    n = used_sheet_names.get(base, 0)
    used_sheet_names[base] = n + 1
    if n == 0:
        return base
    # extremely unlikely collision after truncation; disambiguate
    suffix = f"_{n}"
    return base[:MAX_SHEET_NAME - len(suffix)] + suffix


print(f"Building candidate sheets for {len(cur)} currently-tracked products...")
sheet_index = []  # sheet name -> full GfK_Key, since sheet names truncate at 31 chars
with pd.ExcelWriter(OUT_FILE, engine='openpyxl') as writer:
    for i, (_, r) in enumerate(cur.iterrows()):
        gfk = r['GfK_Key']
        brand = gfk.split()[0].lower()
        current_ob = current_ob_by_gfk.get(gfk)

        cand = specs[specs['BrandLower'] == brand].copy()
        keep = cand.apply(
            lambda c: wpt._cpu_tier_ok(gfk, c['Processor']) and wpt._gpu_tier_ok(gfk, c['GPU']),
            axis=1
        )
        cand = cand[keep].drop(columns=['BrandLower'])

        def is_current(row):
            ob_key = f"{row['Product Family']} {row['Processor']} {row['GPU']}".strip()
            full = f"{brand.title()} {ob_key}"
            return pd.notna(current_ob) and current_ob.strip().lower() == full.strip().lower()

        cand['Currently_Mapped'] = cand.apply(is_current, axis=1) if len(cand) else []
        cand = cand.sort_values(['Currently_Mapped', 'N_Listings'], ascending=[False, False])
        cand.insert(0, 'GfK_Key', gfk)
        cand.insert(1, 'GfK_CPU', r.get('CPU_G', ''))
        cand.insert(2, 'GfK_GPU', r.get('GPU_G2', ''))

        cols = ['GfK_Key', 'GfK_CPU', 'GfK_GPU', 'Product Family', 'Processor', 'GPU',
                'Currently_Mapped', 'N_Listings', 'Min_Price', 'Median_Price', 'Max_Price']
        cand = cand[cols].round({'Min_Price': 0, 'Median_Price': 0, 'Max_Price': 0})

        sheet_name = sheet_name_for(gfk, i)
        cand.to_excel(writer, sheet_name=sheet_name, index=False)
        sheet_index.append({'Sheet_Name': sheet_name, 'GfK_Key': gfk})

pd.DataFrame(sheet_index).to_csv(f"{WORKSPACE_DIR}\\model_candidates_sheet_index.csv", index=False)
print(f"\nWrote {len(cur)} sheets -> {OUT_FILE}")
print("Delete every row that does NOT belong to that sheet's GfK product, save, then run apply_model_candidates.py")
