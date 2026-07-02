import pandas as pd
import numpy as np
import os
import re
from rapidfuzz import process, fuzz  # pip install rapidfuzz

# ── Key normalization ────────────────────────────────────────────────────────
# Strategy: strip CPU generation codes (285H, 13420H) but KEEP GPU numbers
# (3050, 4050) since those are discriminative. Normalize brand/product terms.
_CPU_GEN_RE = re.compile(r'\b\d{3,5}[A-Z]{1,3}\b', re.IGNORECASE)  # 285H, 13420H
_SCREEN_RE  = re.compile(r'\b(15|16|17|14|13)\b')
_GPU_NUM_RE = re.compile(r'\b(\d{4})\b')   # 3050, 4050, 2050 — used as pre-filter

# Term expansions applied to BOTH GfK and OB keys before comparison
_EXPAND = [
    (re.compile(r'\bNvidia\s+GeForce\b', re.I),        'GeForce'),
    (re.compile(r'\bIntel\s+Arc\b', re.I),             'Arc'),
    (re.compile(r'\bIntel\s+Core\b', re.I),            'Core'),
    (re.compile(r'\bAMD\s+Radeon\b', re.I),            'Radeon'),
    (re.compile(r'\bAMD\s+Ryzen\b', re.I),             'Ryzen'),
    (re.compile(r'\bcore\s+i([357])\b', re.I),         r'Core i\1'),
    (re.compile(r'\bCore\s+Ultra\s+(\d)\b', re.I),     r'Core Ultra \1'),
    (re.compile(r'\bRTX\b'),                            'GeForce RTX'),
    (re.compile(r'\bGTX\b'),                            'GeForce GTX'),
]


def _normalize_key(key: str) -> str:
    """Strip CPU gen codes, screen sizes; unify vendor/GPU naming."""
    k = str(key)
    k = _CPU_GEN_RE.sub('', k)
    k = _SCREEN_RE.sub('', k)
    for pat, repl in _EXPAND:
        k = pat.sub(repl, k)
    return re.sub(r'\s+', ' ', k).strip().lower()


def _brand_from_key(key: str) -> str:
    return str(key).split()[0].lower() if key else ''


def _gpu_num(key: str):
    """Return 4-digit GPU number (e.g. '3050') if present, else None."""
    m = _GPU_NUM_RE.search(str(key))
    return m.group(1) if m else None


# Product-line words that must appear in the OB match when present in the GfK key.
# Ordered longest-first so 'rog zephyrus' is checked before 'rog'.
_PRODUCT_LINES = [
    'rog zephyrus', 'rog strix', 'rog',
    'tuf gaming', 'tuf',
    'vivobook pro', 'vivobook',
    'zenbook duo', 'zenbook',
    'predator helios', 'predator',
    'nitro v', 'nitro',
    'ideapad slim', 'ideapad',
    'yoga pro', 'yoga',
    'legion pro', 'legion',
    'pavilion', 'envy x360', 'envy', 'victus', 'omen',
    'omnibook x flip', 'omnibook',
    'galaxy book', 'galaxy',
    'macbook pro', 'macbook air', 'macbook',
    'surface laptop', 'surface',
    'inspiron', 'vostro', 'xps',
    'chromebook',
]


def _product_line_ok(gfk_key: str, ob_key: str) -> bool:
    """Return True if the product line found in gfk_key also appears in ob_key."""
    gfk = gfk_key.lower()
    ob  = ob_key.lower()
    for line in _PRODUCT_LINES:
        if line in gfk:
            # Use word-boundary match to avoid 'v' matching inside 'nvidia' etc.
            return any(re.search(r'\b' + re.escape(w) + r'\b', ob)
                       for w in line.split() if len(w) > 1)
    return True  # no recognized line → no restriction

# ── File paths ──────────────────────────────────────────────────────────────
GFK_NR_PATH      = r"D:\ASUS\GfK Monthy update\4. Pivot\GfK NR Raw.csv"
GFK_NB_PATH      = r"D:\ASUS\GfK Monthy update\4. Pivot\GfK NB Raw.csv"
OPEN_BRAND_PATH  = r"D:\ASUS\GAP US Retail Data\Pricings.csv"

WORKSPACE_DIR        = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
TRACKED_HISTORY_FILE = os.path.join(WORKSPACE_DIR, "tracked_products_history.csv")
MASTER_MAPPING_FILE  = os.path.join(WORKSPACE_DIR, "master_mapping.csv")
MAPPING_REVIEW_FILE  = os.path.join(WORKSPACE_DIR, "mapping_review.xlsx")
WEEKLY_PRICE_DB_FILE = os.path.join(WORKSPACE_DIR, "weekly_price_database.csv")

TOP_N             = 5
FUZZY_THRESHOLD   = 80
OB_COUNTRY_FILTER = ['US']
NA_COUNTRIES      = ['.USA', '.Canada']

HISTORY_COLS = [
    'GfK_Key', 'Product_Segment', 'NB_NR', 'Vendor', 'Model',
    'CPU_G', 'GPU_G2', 'K_Unit', 'Seg_Share_Pct',
    'First_Seen', 'Last_Seen', 'Is_Current'
]


def clean_price(price_str):
    if pd.isna(price_str):
        return np.nan
    if isinstance(price_str, (int, float)):
        return float(price_str)
    cleaned = str(price_str).replace('$', '').replace(',', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return np.nan


def process_gfk(top_n=TOP_N):
    print("Reading GfK data (NB + NR), filtering to NA (.USA + .Canada)...")
    gfk_cols = ['Product (NB/NR)', 'NB/NR', 'Country', 'Vendor (Rank)',
                'Model (G1)', 'CPU G', 'GPU G2', 'K Unit']

    df_nr = pd.read_csv(GFK_NR_PATH, usecols=gfk_cols)
    df_nr = df_nr[df_nr['Country'].isin(NA_COUNTRIES)]

    df_nb = pd.read_csv(GFK_NB_PATH, usecols=gfk_cols)
    df_nb = df_nb[df_nb['Country'].isin(NA_COUNTRIES)]
    # Vivobook RTX is tracked from NR only
    df_nb = df_nb[df_nb['Product (NB/NR)'].str.strip().str.title() != 'Vivobook Rtx']

    df_gfk = pd.concat([df_nr, df_nb], ignore_index=True)
    df_gfk['Product (NB/NR)'] = df_gfk['Product (NB/NR)'].str.strip().str.title()
    df_gfk[['CPU G', 'GPU G2']] = df_gfk[['CPU G', 'GPU G2']].fillna('Unknown')
    df_gfk['K Unit'] = pd.to_numeric(df_gfk['K Unit'], errors='coerce').fillna(0)

    # Segment totals (full data, before top-N filter) — used for share %
    seg_totals = df_gfk.groupby('Product (NB/NR)')['K Unit'].sum()

    print(f"Extracting Top {top_n} per Segment x NB/NR (DRAM variants aggregated)...")
    # Group WITHOUT DRAM so different RAM configs of the same model collapse into one row
    grouped = (
        df_gfk
        .groupby(['Product (NB/NR)', 'NB/NR', 'Vendor (Rank)', 'Model (G1)', 'CPU G', 'GPU G2'],
                 dropna=False)['K Unit']
        .sum()
        .reset_index()
        .sort_values(['Product (NB/NR)', 'NB/NR', 'K Unit'], ascending=[True, True, False])
    )

    top_models = grouped.groupby(['Product (NB/NR)', 'NB/NR']).head(top_n).copy()

    top_models['GfK_Key'] = (
        top_models['Vendor (Rank)'].astype(str) + ' '
        + top_models['Model (G1)'].astype(str) + ' '
        + top_models['CPU G'].astype(str) + ' '
        + top_models['GPU G2'].astype(str)
    ).str.replace(r'\s+', ' ', regex=True).str.strip()

    top_models['Seg_Total_KUnit'] = top_models['Product (NB/NR)'].map(seg_totals)
    top_models['Seg_Share_Pct'] = (
        top_models['K Unit'] / top_models['Seg_Total_KUnit'] * 100
    ).round(2)

    print("\nTop models per Segment (NA only):")
    print(top_models[['Product (NB/NR)', 'NB/NR', 'GfK_Key', 'K Unit', 'Seg_Share_Pct']].to_string())
    return top_models


def update_tracked_history(top_n_df):
    current_month = pd.Timestamp.now().strftime('%Y-%m')

    if os.path.exists(TRACKED_HISTORY_FILE):
        history_df = pd.read_csv(TRACKED_HISTORY_FILE)
        # Add missing columns for backward compatibility
        for col in HISTORY_COLS:
            if col not in history_df.columns:
                history_df[col] = np.nan
    else:
        history_df = pd.DataFrame(columns=HISTORY_COLS)

    incoming = (
        top_n_df
        .rename(columns={
            'Product (NB/NR)': 'Product_Segment',
            'NB/NR':           'NB_NR',
            'Vendor (Rank)':   'Vendor',
            'Model (G1)':      'Model',
            'CPU G':           'CPU_G',
            'GPU G2':          'GPU_G2',
            'K Unit':          'K_Unit',
        })
        .copy()
    )
    incoming['Last_Seen'] = current_month

    # Composite key: a product may appear in multiple segments with different context
    incoming['_seg_key'] = incoming['GfK_Key'] + '|||' + incoming['Product_Segment']
    current_keys     = set(incoming['GfK_Key'])
    current_seg_keys = set(incoming['_seg_key'])

    # Update mutable fields on existing (GfK_Key, Product_Segment) pairs
    update_cols = ['K_Unit', 'Seg_Share_Pct', 'Last_Seen', 'NB_NR',
                   'Vendor', 'Model', 'CPU_G', 'GPU_G2']

    # Deduplicate incoming by _seg_key before merge to avoid row explosion
    incoming_dedup = (
        incoming.sort_values('K_Unit', ascending=False)
        .drop_duplicates('_seg_key', keep='first')
    )

    history_df['_seg_key'] = history_df['GfK_Key'].astype(str) + '|||' + history_df['Product_Segment'].astype(str)
    merged = history_df.merge(
        incoming_dedup[['_seg_key'] + update_cols],
        on='_seg_key', how='left', suffixes=('', '_new')
    )
    for col in update_cols:
        new_col = col + '_new'
        if new_col in merged.columns:
            mask = merged[new_col].notna()
            merged.loc[mask, col] = merged.loc[mask, new_col]
            merged.drop(columns=[new_col], inplace=True)
    history_df = merged.drop(columns=['_seg_key'])

    # Append genuinely new (GfK_Key, Segment) pairs
    existing_seg_keys = set(
        (history_df['GfK_Key'].astype(str) + '|||' + history_df['Product_Segment'].astype(str))
    )
    new_incoming = incoming[~incoming['_seg_key'].isin(existing_seg_keys)].copy()
    if not new_incoming.empty:
        new_incoming = new_incoming.drop(columns=['_seg_key'])
        new_incoming['First_Seen'] = current_month
        history_df = pd.concat([history_df, new_incoming], ignore_index=True)

    history_df['Is_Current'] = history_df['GfK_Key'].isin(current_keys)
    # Deduplicate: keep one row per (GfK_Key, Product_Segment), most recent Last_Seen
    history_df = (
        history_df.sort_values('Last_Seen')
        .drop_duplicates(['GfK_Key', 'Product_Segment'], keep='last')
        .reset_index(drop=True)
    )
    history_df.to_csv(TRACKED_HISTORY_FILE, index=False)
    n_current = history_df['Is_Current'].sum()
    n_unique  = history_df.loc[history_df['Is_Current'], 'GfK_Key'].nunique()
    print(f"Tracked history updated. Total: {len(history_df)} | Current rows: {n_current} | Unique keys: {n_unique}")
    return history_df


def process_mapping_and_pricing(history_df):
    print("Reading Open Brand data...")
    ob_cols = ['Brand', 'Product Family', 'Processor', 'GPU',
               'Net Price', 'Week', 'Date', 'Country']
    df_ob = pd.read_csv(OPEN_BRAND_PATH, usecols=ob_cols)

    if OB_COUNTRY_FILTER:
        df_ob = df_ob[df_ob['Country'].isin(OB_COUNTRY_FILTER)].copy()
        print(f"  Filtered to {OB_COUNTRY_FILTER} → {len(df_ob)} rows")

    df_ob[['Processor', 'GPU']] = df_ob[['Processor', 'GPU']].fillna('')
    df_ob['OB_Key'] = (
        df_ob['Brand'].astype(str) + ' '
        + df_ob['Product Family'].astype(str) + ' '
        + df_ob['Processor'].astype(str) + ' '
        + df_ob['GPU'].astype(str)
    ).str.replace(r'\s+', ' ', regex=True).str.strip()

    unique_ob_keys = df_ob['OB_Key'].dropna().unique().tolist()

    if os.path.exists(MASTER_MAPPING_FILE):
        master_mapping = pd.read_csv(MASTER_MAPPING_FILE)
    else:
        master_mapping = pd.DataFrame(columns=['GfK_Key', 'OB_Key', 'Score', 'Status'])

    mapped_dict = dict(zip(master_mapping['GfK_Key'], master_mapping['OB_Key']))

    # Pre-build normalized OB key index: norm_key → original_ob_key
    norm_ob_index = {}
    for k in unique_ob_keys:
        nk = _normalize_key(k)
        if nk not in norm_ob_index:       # first occurrence wins
            norm_ob_index[nk] = k
    norm_ob_keys = list(norm_ob_index.keys())

    # Brand buckets (by first word) for narrowing candidates
    brand_buckets: dict = {}
    for nk in norm_ob_keys:
        brand_buckets.setdefault(_brand_from_key(nk), []).append(nk)

    # GPU-number buckets (4-digit) for narrowing within brand
    gpu_buckets: dict = {}
    for nk in norm_ob_keys:
        g = _gpu_num(nk)
        if g:
            gpu_buckets.setdefault(g, []).append(nk)

    review_list = []
    new_mapping_rows = []

    current_keys = history_df.loc[history_df['Is_Current'] == True, 'GfK_Key']
    print("Fuzzy matching current-run GfK keys to Open Brand (normalized, brand+GPU filtered)...")
    for key in current_keys:
        if key in mapped_dict:
            continue
        if not unique_ob_keys:
            review_list.append({'GfK_Key': key, 'Suggested_OB_Key': None, 'Score': 0})
            continue

        norm_key = _normalize_key(key)
        brand    = _brand_from_key(norm_key)
        gpu      = _gpu_num(norm_key)

        # Step 1: brand-filter
        brand_cands = brand_buckets.get(brand, norm_ob_keys)
        in_brand    = brand_cands is not norm_ob_keys

        # Step 2: GPU-number filter (only when brand-filtered AND GPU present)
        if in_brand and gpu:
            gpu_cands = [c for c in brand_cands if _gpu_num(c) == gpu]
            candidates = gpu_cands if gpu_cands else brand_cands
        else:
            candidates = brand_cands

        # WRatio scores ALL tokens (no subset loophole of token_set_ratio)
        result = process.extractOne(norm_key, candidates, scorer=fuzz.WRatio)
        best_norm, score = (result[0], result[1]) if result else (None, 0)
        best_match = norm_ob_index.get(best_norm) if best_norm else None

        # Threshold: 70 when brand-filtered, 80 when global fallback
        threshold = 70 if in_brand else FUZZY_THRESHOLD

        line_ok = _product_line_ok(key, best_match) if best_match else False

        if score >= threshold and line_ok:
            mapped_dict[key] = best_match
            new_mapping_rows.append({
                'GfK_Key': key, 'OB_Key': best_match,
                'Score': score, 'Status': 'Auto-Mapped'
            })
            print(f"  [mapped] {key}")
            print(f"           -> {best_match} ({score:.0f})")
        else:
            reason = 'product_line_mismatch' if not line_ok else 'low_score'
            review_list.append({'GfK_Key': key, 'Suggested_OB_Key': best_match,
                                 'Score': score, 'Reason': reason})
            print(f"  [review:{reason}] {key}")
            print(f"           -> {best_match} ({score:.0f})")

    if new_mapping_rows:
        master_mapping = pd.concat(
            [master_mapping, pd.DataFrame(new_mapping_rows)], ignore_index=True
        )
    master_mapping.to_csv(MASTER_MAPPING_FILE, index=False)

    if review_list:
        pd.DataFrame(review_list).to_excel(MAPPING_REVIEW_FILE, index=False)
        print(f"[!] {len(review_list)} items need manual review → {MAPPING_REVIEW_FILE}")

    # ── Weekly price calculation ─────────────────────────────────────────────
    print("Calculating weekly prices...")
    df_ob['Net Price'] = df_ob['Net Price'].apply(clean_price)
    df_ob = df_ob.dropna(subset=['Net Price'])

    ob_to_gfk = {v: k for k, v in mapped_dict.items() if pd.notna(v)}
    df_ob_filtered = df_ob[df_ob['OB_Key'].isin(ob_to_gfk)].copy()
    df_ob_filtered['GfK_Key'] = df_ob_filtered['OB_Key'].map(ob_to_gfk)

    # ── Outlier filter: two-stage ─────────────────────────────────────────────
    # Stage 1 — within-week IQR: removes extreme entries in busy weeks
    #           (weeks with multiple SKUs mapped to the same GfK_Key).
    # Stage 2 — cross-week product median: removes anomalous weeks where the
    #           only entry for that week is far from the product's typical range.
    #           e.g. a clearance $79 when all other weeks are ~$940.

    def iqr_filter_week(grp):
        prices = grp['Net Price']
        if len(prices) <= 2:
            return grp
        q1, q3 = prices.quantile(0.25), prices.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            return grp[(prices >= q1 - 1.5 * iqr) & (prices <= q3 + 1.5 * iqr)]
        return grp

    df_ob_filtered = (
        df_ob_filtered
        .groupby(['GfK_Key', 'Week'], group_keys=False)
        .apply(iqr_filter_week)
        .reset_index(drop=True)
    )

    # Stage 2: cross-week product median filter
    # For each row, compare price to the product's overall median across all weeks.
    # Remove if price < 30% or > 400% of the product median (catches clearance outliers).
    product_medians = df_ob_filtered.groupby('GfK_Key')['Net Price'].median()
    df_ob_filtered = df_ob_filtered.copy()
    df_ob_filtered['_prod_median'] = df_ob_filtered['GfK_Key'].map(product_medians)
    df_ob_filtered = df_ob_filtered[
        (df_ob_filtered['Net Price'] >= df_ob_filtered['_prod_median'] * 0.30) &
        (df_ob_filtered['Net Price'] <= df_ob_filtered['_prod_median'] * 4.00)
    ].drop(columns=['_prod_median'])

    weekly_prices = (
        df_ob_filtered
        .groupby(['GfK_Key', 'Week'])
        .agg(Average_Price=('Net Price', 'mean'), Date=('Date', 'max'))
        .reset_index()
    )

    if os.path.exists(WEEKLY_PRICE_DB_FILE):
        db_df = pd.read_csv(WEEKLY_PRICE_DB_FILE)
        if 'Median_Price' in db_df.columns:
            db_df = db_df.rename(columns={'Median_Price': 'Average_Price'})
        merged_check = db_df.merge(
            weekly_prices[['GfK_Key', 'Week']], on=['GfK_Key', 'Week'],
            how='left', indicator=True
        )
        db_df = db_df[merged_check['_merge'] == 'left_only']
        db_df = pd.concat([db_df, weekly_prices], ignore_index=True)
    else:
        db_df = weekly_prices

    db_df['Date'] = pd.to_datetime(db_df['Date'], errors='coerce')
    db_df = db_df.sort_values(['GfK_Key', 'Date']).reset_index(drop=True)
    db_df.to_csv(WEEKLY_PRICE_DB_FILE, index=False)
    print(f"Weekly price database updated → {WEEKLY_PRICE_DB_FILE}")


if __name__ == "__main__":
    top_models = process_gfk(top_n=TOP_N)
    history    = update_tracked_history(top_models)
    process_mapping_and_pricing(history)
