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
            # Require EVERY significant word (e.g. both 'rog' and 'zephyrus') to appear —
            # matching only the shared 'rog' prefix let cross-line mismatches like
            # ROG Zephyrus -> ROG Strix slip through.
            # \d* after the word lets a directly-appended generation number (e.g.
            # "Book4", "Book5") still count as the same line as bare "book".
            return all(re.search(r'\b' + re.escape(w) + r'\d*\b', ob)
                       for w in line.split() if len(w) > 1)
    return True  # no recognized line → no restriction


# ── CPU / GPU tier alignment ─────────────────────────────────────────────────
# The product-line check alone let mismatched silicon through, e.g. a GfK
# "Ryzen 7" key auto/manually mapped to an OB "Ryzen AI 5" SKU, or "Core i7"
# mapped to "Core i9". These extract a coarse (brand, tier-number) signature
# from free-text CPU/GPU strings — exact model numbers don't need to match
# (13650HX vs 14650HX is fine), but the brand + tier digit must (i5 vs i5,
# Ryzen 7 vs Ryzen 7). Returns None when no recognizable tier is present in
# the text at all (e.g. a blank/dash spec) so those cases don't get blocked.

def _cpu_tier_code(text):
    t = str(text).lower()
    if re.search(r'\b(celeron|pentium)\b', t) or re.search(r'\bn\d{3,4}\b', t) or 'processor n' in t:
        return ('intel', 'entry')
    if 'athlon' in t:
        return ('amd', 'entry')
    m = re.search(r'core\s+ultra\s+(\d)', t)
    if m:
        return ('intel', m.group(1))
    m = re.search(r'core\s+i(\d)', t)
    if m:
        return ('intel', m.group(1))
    m = re.search(r'\bcore\s+(\d)\b', t)
    if m:
        return ('intel', m.group(1))
    m = re.search(r'ryzen\s+(?:ai\s+)?(\d)', t)
    if m:
        return ('amd', m.group(1))
    m = re.search(r'\bm(\d)\b', t)
    if m:
        return ('apple', m.group(1))
    m = re.search(r'snapdragon\s+x\s+(elite|plus)', t)
    if m:
        return ('qualcomm', m.group(1))
    if 'snapdragon' in t:
        return ('qualcomm', 'x')
    # GfK sometimes drops the "Snapdragon" word and just writes "X Plus"/"X Elite"
    m = re.search(r'\bx\s+(elite|plus)\b', t)
    if m:
        return ('qualcomm', m.group(1))
    return None


def _gpu_tier_code(text):
    """(class, brand, model) — class is 'integrated' or 'discrete'. model is None
    when GfK only gave a generic marker (GMA / AMD Oth. / zthers) with no specific
    chip — those still carry class+brand so an integrated marker can never silently
    match a discrete GPU (the bug that let a "GMA" laptop map to an RTX 3050 Ti)."""
    t = str(text).lower()
    m = re.search(r'\b(?:rtx|gtx)\s*(\d{3,4})', t)
    if m:
        return ('discrete', 'nvidia', m.group(1))
    if 'radeon' in t and re.search(r'\brx\s*\d{3,4}\b', t):
        m = re.search(r'rx\s*(\d{3,4})', t)
        return ('discrete', 'amd', m.group(1))
    if 'radeon' in t:
        m = re.search(r'radeon\s+(\d{3}m)\b', t)
        return ('integrated', 'amd', m.group(1) if m else None)
    if re.search(r'\bamd\s+oth', t):
        return ('integrated', 'amd', None)
    m = re.search(r'\barc\s*a(\d{3})\b', t)
    if m:
        return ('discrete', 'intel', 'a' + m.group(1))
    if 'arc' in t:
        m = re.search(r'arc\s+(?:graphics\s+)?(\d{3}[a-z]?)', t)
        return ('integrated', 'intel', m.group(1) if m else None)
    if 'iris' in t:
        return ('integrated', 'intel', 'iris')
    if 'uhd' in t or 'intel graphics' in t or 'intel hd' in t:
        return ('integrated', 'intel', 'basic')
    if re.search(r'\bgma\b', t):
        # GfK's generic label for "Intel integrated, model unspecified" — every
        # occurrence in this dataset is paired with an Intel CPU.
        return ('integrated', 'intel', None)
    if 'adreno' in t:
        return ('integrated', 'qualcomm', 'adreno')
    m = re.search(r'(\d+)-core\s+gpu', t)
    if m:
        return ('integrated', 'apple', m.group(1))
    if re.search(r'\bzthers\b', t):
        # GfK's catch-all "other/unspecified" GPU marker — infer brand from
        # whatever CPU platform is named in the same key, since ARM/Apple SoCs
        # have no discrete-GPU option anyway.
        if 'snapdragon' in t:
            return ('integrated', 'qualcomm', None)
        if 'apple' in t:
            return ('integrated', 'apple', None)
        return ('integrated', None, None)
    return None


def _gpu_compatible(g, o) -> bool:
    """Looser than exact-equality: brand/model may be unknown (None) on either
    side without blocking a match, but a known integrated/discrete class or
    brand may never contradict the other side's known value."""
    if g is None or o is None:
        return True
    g_class, g_brand, g_model = g
    o_class, o_brand, o_model = o
    if g_class != o_class:
        return False
    if g_brand and o_brand and g_brand != o_brand:
        return False
    if g_class == 'discrete' and g_model and o_model and g_model != o_model:
        return False
    return True


def _cpu_tier_ok(gfk_key: str, ob_key: str) -> bool:
    g, o = _cpu_tier_code(gfk_key), _cpu_tier_code(ob_key)
    if g is None or o is None:
        return True
    return g == o


def _gpu_tier_ok(gfk_key: str, ob_key: str) -> bool:
    return _gpu_compatible(_gpu_tier_code(gfk_key), _gpu_tier_code(ob_key))


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
GFK_YEAR_FILTER   = 2026   # rank Top-N by this year's K Unit only, not lifetime-to-date

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
    print(f"Reading GfK data (NB + NR), filtering to NA (.USA + .Canada) and {GFK_YEAR_FILTER}...")
    gfk_cols = ['Product (NB/NR)', 'NB/NR', 'Country', 'Vendor (Rank)',
                'Model (G1)', 'CPU G', 'GPU G2', 'K Unit', 'Period (M)']

    def _load(path):
        d = pd.read_csv(path, usecols=gfk_cols)
        d = d[d['Country'].isin(NA_COUNTRIES)]
        d = d[pd.to_datetime(d['Period (M)']).dt.year == GFK_YEAR_FILTER]
        return d.drop(columns=['Period (M)'])

    df_nr = _load(GFK_NR_PATH)
    df_nb = _load(GFK_NB_PATH)
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

    # Current source file's week window. Any (GfK_Key, Week) inside this range must be
    # re-derived fresh every run — carrying forward a stale value here would mean showing
    # a price for a week the current raw feed no longer supports (e.g. a SKU's GPU/CPU
    # label text drifted between pulls and stopped matching its mapped OB_Key).
    CURRENT_WEEK_MIN, CURRENT_WEEK_MAX = df_ob['Week'].min(), df_ob['Week'].max()

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

        # Threshold: 70 when brand-filtered, 80 when global fallback
        threshold = 70 if in_brand else FUZZY_THRESHOLD

        # WRatio scores ALL tokens (no subset loophole of token_set_ratio).
        # Pull the top several candidates (not just #1) and walk them in score
        # order, taking the first that also survives product-line + CPU-tier +
        # GPU-tier checks — the highest text-similarity match isn't always the
        # one with matching silicon (e.g. "Ryzen 7" scoring highest against a
        # "Ryzen AI 5" listing over a lower-scoring genuine "Ryzen 7" one).
        results = process.extract(norm_key, candidates, scorer=fuzz.WRatio, limit=8)

        best_match, score = None, 0
        top_match, top_score = None, 0
        for cand_norm, cand_score, _ in results:
            cand_orig = norm_ob_index.get(cand_norm)
            if top_match is None:
                top_match, top_score = cand_orig, cand_score
            if cand_score < threshold:
                break  # results are sorted desc; nothing further will pass
            if (_product_line_ok(key, cand_orig) and _cpu_tier_ok(key, cand_orig)
                    and _gpu_tier_ok(key, cand_orig)):
                best_match, score = cand_orig, cand_score
                break

        if best_match is not None:
            mapped_dict[key] = best_match
            new_mapping_rows.append({
                'GfK_Key': key, 'OB_Key': best_match,
                'Score': score, 'Status': 'Auto-Mapped'
            })
            print(f"  [mapped] {key}")
            print(f"           -> {best_match} ({score:.0f})")
        else:
            reasons = []
            if top_match is not None:
                if not _product_line_ok(key, top_match):
                    reasons.append('product_line_mismatch')
                if not _cpu_tier_ok(key, top_match):
                    reasons.append('cpu_tier_mismatch')
                if not _gpu_tier_ok(key, top_match):
                    reasons.append('gpu_tier_mismatch')
            if not reasons:
                reasons.append('low_score')
            reason = ','.join(reasons)
            review_list.append({'GfK_Key': key, 'Suggested_OB_Key': top_match,
                                 'Score': top_score, 'Reason': reason})
            print(f"  [review:{reason}] {key}")
            print(f"           -> {top_match} ({top_score:.0f})")

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
    # Remove if price < 30% or > 300% of the product median (catches clearance outliers
    # and one-off scrape/listing errors, e.g. a single week priced ~4x every other week
    # for the same SKU). 300% (not 400%) because a genuine ~3.9x one-week spike was
    # observed slipping through at the old 400% cutoff; legitimate recurring price points
    # for a shared generic bucket (multiple real SKUs mapped to one GfK_Key) stay under 3x.
    product_medians = df_ob_filtered.groupby('GfK_Key')['Net Price'].median()
    df_ob_filtered = df_ob_filtered.copy()
    df_ob_filtered['_prod_median'] = df_ob_filtered['GfK_Key'].map(product_medians)
    df_ob_filtered = df_ob_filtered[
        (df_ob_filtered['Net Price'] >= df_ob_filtered['_prod_median'] * 0.30) &
        (df_ob_filtered['Net Price'] <= df_ob_filtered['_prod_median'] * 3.00)
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

        # Drop every stored row whose Week falls inside the CURRENT file's window —
        # it will be replaced by this run's fresh computation (or correctly disappear
        # if the mapping no longer holds for that week). Rows outside the window are
        # weeks that have already rolled off the raw feed; keep them as archived history.
        in_current_window = db_df['Week'].between(CURRENT_WEEK_MIN, CURRENT_WEEK_MAX)
        n_stale = in_current_window.sum() - db_df.merge(
            weekly_prices[['GfK_Key', 'Week']], on=['GfK_Key', 'Week'], how='inner'
        ).shape[0]
        if n_stale > 0:
            print(f"  Pruned {n_stale} stale in-window row(s) that no longer match a current SKU.")
        db_df = db_df[~in_current_window]
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
