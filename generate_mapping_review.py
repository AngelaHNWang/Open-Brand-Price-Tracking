"""
Step 1 of the human-in-the-loop mapping workflow.

Exports every currently-tracked GfK product alongside its current Open Brand
mapping (if any) and a handful of alternative candidate matches, into a CSV
for manual review. Nothing here writes back to master_mapping.csv or the
dashboard — this is read-only reconnaissance. Once you've filled in
`Confirmed_OB_Key` for every row, run apply_mapping_review.py to make that
column the new master_mapping.csv, then price_dashboard_generator.py.

Confirmed_OB_Key conventions:
  - Leave as-is (defaults to the current mapping) if it already looks right.
  - Paste in a different OB_Key string (exact "Brand Family Processor GPU"
    text as it appears in Pricings.csv) to correct it.
  - Clear the cell entirely to mark "no comparable Open Brand SKU exists".
  - The Candidate_N columns are just reference suggestions — copy one into
    Confirmed_OB_Key if it looks right, or ignore them and paste your own.
"""
import pandas as pd
import numpy as np
import re
from rapidfuzz import process, fuzz

import weekly_price_tracker as wpt

WORKSPACE_DIR = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
REVIEW_FILE = f"{WORKSPACE_DIR}\\mapping_review_full.csv"

N_CANDIDATES = 5

hist = pd.read_csv(wpt.TRACKED_HISTORY_FILE)
cur = hist[hist['Is_Current'] == True].copy()

mm = pd.read_csv(wpt.MASTER_MAPPING_FILE)
mapped_dict = dict(zip(mm['GfK_Key'], mm['OB_Key']))
status_dict = dict(zip(mm['GfK_Key'], mm['Status']))

print("Reading Open Brand data to build the candidate pool...")
ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU', 'Country']
df_ob = pd.read_csv(wpt.OPEN_BRAND_PATH, usecols=ob_cols)
df_ob = df_ob[df_ob['Country'].isin(wpt.OB_COUNTRY_FILTER)].copy()
df_ob[['Processor', 'GPU']] = df_ob[['Processor', 'GPU']].fillna('')
df_ob['OB_Key'] = (
    df_ob['Brand'].astype(str) + ' ' + df_ob['Product Family'].astype(str) + ' '
    + df_ob['Processor'].astype(str) + ' ' + df_ob['GPU'].astype(str)
).str.replace(r'\s+', ' ', regex=True).str.strip()

unique_ob_keys = df_ob['OB_Key'].dropna().unique().tolist()
norm_ob_index = {}
for k in unique_ob_keys:
    nk = wpt._normalize_key(k)
    if nk not in norm_ob_index:
        norm_ob_index[nk] = k
norm_ob_keys = list(norm_ob_index.keys())

brand_buckets = {}
for nk in norm_ob_keys:
    brand_buckets.setdefault(wpt._brand_from_key(nk), []).append(nk)

rows = []
print(f"Scoring candidates for {len(cur)} currently-tracked products...")
for _, r in cur.iterrows():
    key = r['GfK_Key']
    norm_key = wpt._normalize_key(key)
    brand = wpt._brand_from_key(norm_key)
    candidates = brand_buckets.get(brand, norm_ob_keys)

    results = process.extract(norm_key, candidates, scorer=fuzz.WRatio, limit=N_CANDIDATES)
    cand_strs = []
    for cand_norm, cand_score, _ in results:
        cand_orig = norm_ob_index.get(cand_norm)
        flags = []
        if not wpt._product_line_ok(key, cand_orig):
            flags.append('LINE')
        if not wpt._cpu_tier_ok(key, cand_orig):
            flags.append('CPU')
        if not wpt._gpu_tier_ok(key, cand_orig):
            flags.append('GPU')
        flag_str = f" [{'/'.join(flags)}!]" if flags else ""
        cand_strs.append(f"{cand_orig} ({cand_score:.0f}){flag_str}")
    while len(cand_strs) < N_CANDIDATES:
        cand_strs.append('')

    current_ob = mapped_dict.get(key)
    current_ob = current_ob if pd.notna(current_ob) else ''
    status = status_dict.get(key, 'Unmapped')

    row = {
        'GfK_Key': key,
        'NB_NR': r.get('NB_NR', ''),
        'Product_Segment': r.get('Product_Segment', ''),
        'Vendor': r.get('Vendor', ''),
        'Model': r.get('Model', ''),
        'CPU_G': r.get('CPU_G', ''),
        'GPU_G2': r.get('GPU_G2', ''),
        'K_Unit': r.get('K_Unit', ''),
        'Current_OB_Key': current_ob,
        'Current_Status': status,
        'Confirmed_OB_Key': current_ob,  # pre-filled; edit or blank this out
    }
    for i, c in enumerate(cand_strs, start=1):
        row[f'Candidate_{i}'] = c
    rows.append(row)

out = pd.DataFrame(rows)
out = out.sort_values(['NB_NR', 'Product_Segment', 'GfK_Key'])
out.to_csv(REVIEW_FILE, index=False)
print(f"\nWrote {len(out)} rows -> {REVIEW_FILE}")
print("Edit the 'Confirmed_OB_Key' column, then run apply_mapping_review.py")
