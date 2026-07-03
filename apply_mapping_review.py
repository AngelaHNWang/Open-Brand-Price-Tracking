"""
Step 2 of the human-in-the-loop mapping workflow.

Reads the reviewed mapping_review_full.csv (specifically the Confirmed_OB_Key
column you edited) and rewrites master_mapping.csv to match it exactly for
every currently-tracked GfK product. Rows for non-current/historical GfK
keys already in master_mapping.csv are left untouched.

After this runs, do:
    python weekly_price_tracker.py       # recompute weekly prices from the confirmed mapping
    python price_dashboard_generator.py  # rebuild index.html
"""
import re
import pandas as pd
import numpy as np

import weekly_price_tracker as wpt

WORKSPACE_DIR = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
REVIEW_FILE = f"{WORKSPACE_DIR}\\mapping_review_full.csv"

_SCORE_SUFFIX_RE = re.compile(r'\s*\(\d+(?:\.\d+)?\)\s*$')     # trailing " (88)"
_FLAG_SUFFIX_RE = re.compile(r'\s*\[[A-Z/]+!\]\s*$')           # trailing " [CPU/GPU!]"


def clean_ob_key(raw):
    """Strip the '(score)' / '[FLAG!]' decorations a user may have copy-pasted
    in from a Candidate_N column — the real OB_Key text never has these."""
    s = str(raw).strip()
    s = _FLAG_SUFFIX_RE.sub('', s)
    s = _SCORE_SUFFIX_RE.sub('', s)
    return s.strip()


review = pd.read_csv(REVIEW_FILE)
mm = pd.read_csv(wpt.MASTER_MAPPING_FILE)

reviewed_keys = set(review['GfK_Key'])
mm_other = mm[~mm['GfK_Key'].isin(reviewed_keys)].copy()

print("Building the set of valid OB_Key strings from Pricings.csv (US-only) to validate against...")
ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU', 'Country']
df_ob = pd.read_csv(wpt.OPEN_BRAND_PATH, usecols=ob_cols)
df_ob = df_ob[df_ob['Country'].isin(wpt.OB_COUNTRY_FILTER)].copy()
df_ob[['Processor', 'GPU']] = df_ob[['Processor', 'GPU']].fillna('')
valid_ob_keys = set(
    (df_ob['Brand'].astype(str) + ' ' + df_ob['Product Family'].astype(str) + ' '
     + df_ob['Processor'].astype(str) + ' ' + df_ob['GPU'].astype(str))
    .str.replace(r'\s+', ' ', regex=True).str.strip()
)

new_rows = []
n_mapped, n_no_match, n_cleaned, n_unverified = 0, 0, 0, 0
for _, r in review.iterrows():
    raw_ob_key = r['Confirmed_OB_Key']
    if pd.isna(raw_ob_key) or str(raw_ob_key).strip() == '':
        new_rows.append({'GfK_Key': r['GfK_Key'], 'OB_Key': np.nan, 'Score': 0.0, 'Status': 'No-Match-Confirmed'})
        n_no_match += 1
        continue

    ob_key = clean_ob_key(raw_ob_key)
    if ob_key != str(raw_ob_key).strip():
        n_cleaned += 1
        print(f"  [cleaned] {r['GfK_Key']}: {raw_ob_key!r} -> {ob_key!r}")

    if ob_key not in valid_ob_keys:
        n_unverified += 1
        print(f"  [!! NOT FOUND in Pricings.csv, kept anyway — double-check] {r['GfK_Key']}: {ob_key!r}")

    new_rows.append({'GfK_Key': r['GfK_Key'], 'OB_Key': ob_key, 'Score': 100.0, 'Status': 'Reviewed'})
    n_mapped += 1

new_mm = pd.concat([mm_other, pd.DataFrame(new_rows)], ignore_index=True)
new_mm.to_csv(wpt.MASTER_MAPPING_FILE, index=False)

print(f"\nApplied {len(review)} reviewed rows: {n_mapped} mapped, {n_no_match} confirmed no-match.")
print(f"  ({n_cleaned} had a Candidate-column score/flag suffix stripped)")
if n_unverified:
    print(f"  !! {n_unverified} confirmed OB_Key(s) don't exactly match any row in Pricings.csv — see [!!] lines above.")
print(f"master_mapping.csv now has {len(new_mm)} total rows -> {wpt.MASTER_MAPPING_FILE}")
print("\nNext: python weekly_price_tracker.py  &&  python price_dashboard_generator.py")
