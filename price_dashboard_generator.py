import pandas as pd
import plotly.graph_objects as go
import os
import re
import html as html_lib

WORKSPACE_DIR        = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
WEEKLY_PRICE_DB_FILE = os.path.join(WORKSPACE_DIR, "weekly_price_database.csv")
TRACKED_HISTORY_FILE = os.path.join(WORKSPACE_DIR, "tracked_products_history.csv")
MASTER_MAPPING_FILE  = os.path.join(WORKSPACE_DIR, "master_mapping.csv")
DASHBOARD_HTML_FILE  = os.path.join(WORKSPACE_DIR, "index.html")
OPEN_BRAND_PATH      = r"D:\ASUS\GAP US Retail Data\Pricings.csv"

# ── Vendor colors (fixed per brand) ─────────────────────────────────────────
VENDOR_COLORS = {
    'ASUS':      '#dc2626',   # Red
    'HP':        '#2563eb',   # Blue
    'LENOVO':    '#475569',   # Dark gray
    'ACER':      '#16a34a',   # Green
    'APPLE':     '#ec4899',   # Pink
    'MICROSOFT': '#d97706',   # Amber
    'SAMSUNG':   '#7c3aed',   # Purple
    'MSI':       '#7c3aed',   # Purple
    'DELL':      '#0891b2',   # Cyan
}
DEFAULT_COLOR = '#64748b'

# Marker symbols — each extra product of the same vendor gets the next symbol
MARKER_SYMBOLS = [
    'circle', 'square', 'diamond', 'triangle-up',
    'pentagon', 'star', 'cross', 'x', 'triangle-down', 'hexagon',
]

# ── Segment display names & canonical order ──────────────────────────────────
SEG_DISPLAY = {
    'Flip':         'FLIP',
    'Proart':       'ProArt',
    'Zenbook':      'Zenbook',
    'Vivobook S':   'Vivobook S',
    'Vivobook':     'Vivobook',
    'Vivobook Go':  'Vivobook Go',
    'Rog Zephyrus': 'ROG Zephyrus',
    'Rog Strix':    'ROG Strix',
    'Tuf Gaming':   'TUF Gaming',
    'Vivobook Rtx': 'Vivobook RTX',
}
NB_SEG_ORDER = ['Flip', 'Proart', 'Zenbook', 'Vivobook S', 'Vivobook', 'Vivobook Go']
NR_SEG_ORDER = ['Rog Zephyrus', 'Rog Strix', 'Tuf Gaming', 'Vivobook Rtx']


def get_vendor(gfk_key: str) -> str:
    return str(gfk_key).split()[0].upper()


def get_color(gfk_key: str) -> str:
    return VENDOR_COLORS.get(get_vendor(gfk_key), DEFAULT_COLOR)


# ── Fuzzy-match detection ────────────────────────────────────────────────────
_GPU_NUM_RE  = re.compile(r'\b(\d{4})\b')
_CPU_TIER_RE = re.compile(
    r'(core\s+ultra\s+\d|core\s+i\d|ryzen\s+\d|apple\s+m\d|celeron|pentium|core\s+\d)',
    re.IGNORECASE
)


def _gpu_num(s):
    m = _GPU_NUM_RE.search(str(s))
    return m.group(1) if m else None


def _cpu_tier(s):
    m = _CPU_TIER_RE.search(str(s))
    return m.group(1).lower() if m else None


def match_quality(gfk_key: str, ob_key: str) -> tuple:
    if not ob_key or pd.isna(ob_key):
        return False, ''
    mismatches = []
    g_gpu, o_gpu = _gpu_num(gfk_key), _gpu_num(ob_key)
    if g_gpu and o_gpu and g_gpu != o_gpu:
        mismatches.append(f'GPU {g_gpu}→{o_gpu}')
    g_cpu, o_cpu = _cpu_tier(gfk_key), _cpu_tier(ob_key)
    if g_cpu and o_cpu and g_cpu != o_cpu:
        mismatches.append(f'CPU {g_cpu}→{o_cpu}')
    return bool(mismatches), ', '.join(mismatches)


def calc_changes(prod_df):
    if prod_df.empty:
        return None, None, None, None, None, None, None
    last_price = prod_df['Average_Price'].iloc[-1]

    wow_diff = wow_pct = None
    if len(prod_df) >= 2:
        prev     = prod_df['Average_Price'].iloc[-2]
        wow_diff = last_price - prev
        wow_pct  = (wow_diff / prev * 100) if prev != 0 else 0.0

    mom_diff = mom_pct = mom_label = None
    prod_df  = prod_df.copy()
    prod_df['_ym'] = prod_df['Date'].dt.to_period('M')
    latest_month   = prod_df['_ym'].iloc[-1]
    prev_month     = latest_month - 1

    this_avg = prod_df.loc[prod_df['_ym'] == latest_month, 'Average_Price'].mean()
    prev_avg = prod_df.loc[prod_df['_ym'] == prev_month,  'Average_Price'].mean()

    if pd.notna(this_avg) and pd.notna(prev_avg) and prev_avg != 0:
        mom_diff  = this_avg - prev_avg
        mom_pct   = mom_diff / prev_avg * 100
        mom_label = f'{prev_month} → {latest_month}'

    return last_price, wow_diff, wow_pct, mom_diff, mom_pct, this_avg, mom_label


def change_badge(val, pct, label, sublabel=None):
    if val is None:
        return (f'<div class="metric"><div class="metric-label">{label}</div>'
                f'<div class="neutral">—</div></div>')
    cls   = 'up' if val > 0 else ('down' if val < 0 else 'neutral')
    sign  = '+' if val > 0 else ''
    arrow = '▲' if val > 0 else ('▼' if val < 0 else '▬')
    sub   = f'<div class="metric-sub">{sign}${val:.2f}</div>'
    if sublabel:
        sub += f'<div class="metric-sub" style="font-size:9px;opacity:.65">{html_lib.escape(sublabel)}</div>'
    return (f'<div class="metric"><div class="metric-label">{label}</div>'
            f'<div class="{cls}">{arrow} {sign}{pct:.1f}%{sub}'
            f'</div></div>')


def build_summary_html(prod_stats):
    """Highlight price change leaders (WoW & MoM) within the segment."""
    lines = []

    def short_name(key):
        # Show vendor + first meaningful product word, e.g. "HP VICTUS"
        parts = str(key).split()
        return ' '.join(parts[:2]) if len(parts) >= 2 else str(key)

    # ── WoW highlights ────────────────────────────────────────────────────────
    wow_valid = [s for s in prod_stats if s['wow_pct'] is not None]
    if wow_valid:
        wow_sorted = sorted(wow_valid, key=lambda x: x['wow_pct'], reverse=True)
        top_up   = wow_sorted[0]
        top_dn   = wow_sorted[-1]

        # Biggest WoW increase
        if top_up['wow_pct'] > 0.5:
            lines.append(
                f'<div class="sum-row">'
                f'<span class="sum-label">📈 WoW 漲幅最大</span>'
                f'<span class="sum-val">'
                f'<strong>{html_lib.escape(short_name(top_up["key"]))}</strong><br>'
                f'<span class="sum-up">+{top_up["wow_pct"]:.1f}% (${top_up["wow_diff"]:+,.0f})</span>'
                f'</span></div>'
            )
        else:
            lines.append(
                '<div class="sum-row"><span class="sum-label">📈 WoW 漲幅最大</span>'
                '<span class="sum-val sum-neutral">本週無明顯漲價</span></div>'
            )

        # Biggest WoW decrease
        if top_dn['wow_pct'] < -0.5:
            lines.append(
                f'<div class="sum-row">'
                f'<span class="sum-label">📉 WoW 跌幅最大</span>'
                f'<span class="sum-val">'
                f'<strong>{html_lib.escape(short_name(top_dn["key"]))}</strong><br>'
                f'<span class="sum-dn">{top_dn["wow_pct"]:.1f}% (${top_dn["wow_diff"]:+,.0f})</span>'
                f'</span></div>'
            )
        else:
            lines.append(
                '<div class="sum-row"><span class="sum-label">📉 WoW 跌幅最大</span>'
                '<span class="sum-val sum-neutral">本週無明顯降價</span></div>'
            )
    else:
        lines.append(
            '<div class="sum-row"><span class="sum-label">WoW 變動</span>'
            '<span class="sum-val sum-neutral">資料不足</span></div>'
        )

    # ── MoM highlights ────────────────────────────────────────────────────────
    mom_valid = [s for s in prod_stats if s['mom_pct'] is not None]
    if mom_valid:
        mom_sorted = sorted(mom_valid, key=lambda x: x['mom_pct'], reverse=True)
        top_up_m = mom_sorted[0]
        top_dn_m = mom_sorted[-1]

        if top_up_m['mom_pct'] > 0.5:
            lines.append(
                f'<div class="sum-row">'
                f'<span class="sum-label">📈 MoM 漲幅最大</span>'
                f'<span class="sum-val">'
                f'<strong>{html_lib.escape(short_name(top_up_m["key"]))}</strong><br>'
                f'<span class="sum-up">+{top_up_m["mom_pct"]:.1f}% (${top_up_m["mom_diff"]:+,.0f})</span>'
                f'</span></div>'
            )
        else:
            lines.append(
                '<div class="sum-row"><span class="sum-label">📈 MoM 漲幅最大</span>'
                '<span class="sum-val sum-neutral">本月無明顯漲價</span></div>'
            )

        if top_dn_m['mom_pct'] < -0.5:
            lines.append(
                f'<div class="sum-row">'
                f'<span class="sum-label">📉 MoM 跌幅最大</span>'
                f'<span class="sum-val">'
                f'<strong>{html_lib.escape(short_name(top_dn_m["key"]))}</strong><br>'
                f'<span class="sum-dn">{top_dn_m["mom_pct"]:.1f}% (${top_dn_m["mom_diff"]:+,.0f})</span>'
                f'</span></div>'
            )
        else:
            lines.append(
                '<div class="sum-row"><span class="sum-label">📉 MoM 跌幅最大</span>'
                '<span class="sum-val sum-neutral">本月無明顯降價</span></div>'
            )
    else:
        lines.append(
            '<div class="sum-row"><span class="sum-label">MoM 變動</span>'
            '<span class="sum-val sum-neutral">資料不足（需至少兩個月資料）</span></div>'
        )

    # ── Segment price range ───────────────────────────────────────────────────
    prices = [s['last_price'] for s in prod_stats if s['last_price'] is not None]
    if prices:
        lines.append(
            f'<div class="sum-row"><span class="sum-label">💲 段內價格區間</span>'
            f'<span class="sum-val">${min(prices):,.0f} – ${max(prices):,.0f}</span></div>'
        )

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════
# Market Overview — cross-brand price-change analysis (all vendors, all SKUs)
# Computed directly from the raw Open Brand feed on every regeneration,
# independent of the GfK top-5 tracking above. Compares each
# (Part Number, Merchant) price series' first vs. last observed week within
# the current file to avoid mix-shift bias from new/discontinued SKUs.
# ══════════════════════════════════════════════════════════════════════════

MKT_PRICE_LOW, MKT_PRICE_HIGH = 100, 15000   # sanity band; drops scrape errors
MKT_UP_COLOR, MKT_DOWN_COLOR  = '#dc2626', '#16a34a'   # matches existing .up/.down


def _mkt_clean_price(x):
    if pd.isna(x):
        return float('nan')
    return float(str(x).replace('$', '').replace(',', ''))


def _mkt_clean_pct(x):
    if pd.isna(x) or str(x).strip() in ('', '-'):
        return float('nan')
    return float(str(x).replace('%', ''))


def _mkt_gpu_bucket(g):
    if pd.isna(g) or g == '-':
        return 'Unknown/None'
    g = str(g)
    if 'RTX' in g or 'GTX' in g:
        return 'Nvidia 獨立顯卡 (RTX/GTX)'
    if g.startswith('AMD Radeon') and 'RX' in g:
        return 'AMD 獨立顯卡 (RX)'
    if g.startswith('AMD Radeon'):
        return 'AMD 整合顯卡'
    if g.startswith('Intel'):
        return 'Intel 整合顯卡/Arc'
    if g.startswith('Apple'):
        return 'Apple 整合顯卡'
    if g.startswith('Qualcomm'):
        return 'Qualcomm 整合顯卡'
    return '其他'


def _mkt_cpu_brand(c):
    if pd.isna(c) or c == '-':
        return 'Unknown'
    c = str(c)
    for prefix in ('Intel', 'AMD', 'Apple', 'Qualcomm'):
        if c.startswith(prefix):
            return prefix
    return 'Other'


def _mkt_cpu_tier(c):
    if pd.isna(c) or c == '-':
        return 'Unknown'
    c = str(c)
    if 'Celeron' in c or 'N100' in c or 'N4' in c or 'N9' in c or 'Pentium' in c:
        return '入門 (Celeron/N系列/Pentium)'
    if ('Ultra 9' in c or 'i9' in c or 'Ryzen 9' in c or 'M4 Pro' in c or 'M5 Pro' in c
            or 'M4 Max' in c or 'M5 Max' in c or 'M3 Max' in c or 'M3 Pro' in c):
        return '高階 (i9/Ultra9/Ryzen9/Pro·Max)'
    if 'Ultra 7' in c or 'i7' in c or 'Ryzen 7' in c or ('Apple M' in c and 'Pro' not in c and 'Max' not in c):
        return '中高階 (i7/Ultra7/Ryzen7/Apple M)'
    if 'Ultra 5' in c or 'i5' in c or 'Ryzen 5' in c:
        return '中階 (i5/Ultra5/Ryzen5)'
    if 'Snapdragon' in c:
        return 'ARM (Snapdragon)'
    return '其他/低階'


def load_market_overview_data():
    cols = ['Category', 'Part Number', 'Brand', 'Merchant', 'Net Price', 'Promo %',
            'On Promo', 'Week', 'Date', 'Product Family', 'Market Segment',
            'Form Factor', 'Processor', 'GPU', 'Country']
    df = pd.read_csv(OPEN_BRAND_PATH, usecols=cols, low_memory=False)
    if 'Category' in df.columns:
        df = df[df['Category'] == 'Notebooks'].copy()
    df['NetPrice'] = df['Net Price'].apply(_mkt_clean_price)
    df = df[(df['NetPrice'] >= MKT_PRICE_LOW) & (df['NetPrice'] <= MKT_PRICE_HIGH)].copy()
    df['PromoPct'] = df['Promo %'].apply(_mkt_clean_pct)
    df['Date'] = pd.to_datetime(df['Date'])
    df['CPU_Brand']  = df['Processor'].apply(_mkt_cpu_brand)
    df['CPU_Tier']   = df['Processor'].apply(_mkt_cpu_tier)
    df['GPU_Bucket'] = df['GPU'].apply(_mkt_gpu_bucket)

    weekly = df.groupby('Week').agg(
        AvgNetPrice=('NetPrice', 'mean'),
        MedNetPrice=('NetPrice', 'median'),
        AvgPromoPct=('PromoPct', 'mean'),
        OnPromoShare=('On Promo', lambda s: (s == 'Y').mean() * 100),
        N=('NetPrice', 'size'),
    ).reset_index()

    key_cols = ['Part Number', 'Merchant']
    cs    = df.sort_values(key_cols + ['Week'])
    first = cs.groupby(key_cols).first()
    last  = cs.groupby(key_cols).last()

    matched = pd.DataFrame({
        'FirstWeek': first['Week'], 'LastWeek': last['Week'],
        'FirstPrice': first['NetPrice'], 'LastPrice': last['NetPrice'],
        'Brand': first['Brand'], 'Product Family': first['Product Family'],
        'Market Segment': first['Market Segment'], 'Form Factor': first['Form Factor'],
        'CPU_Brand': first['CPU_Brand'], 'CPU_Tier': first['CPU_Tier'],
        'GPU_Bucket': first['GPU_Bucket'],
        'FirstPromoPct': first['PromoPct'], 'LastPromoPct': last['PromoPct'],
    }).reset_index()

    matched = matched[matched['LastWeek'] > matched['FirstWeek']].copy()
    matched['PriceChange']    = matched['LastPrice'] - matched['FirstPrice']
    matched['PriceChangePct'] = matched['PriceChange'] / matched['FirstPrice'] * 100
    matched['PromoPtsChange'] = matched['LastPromoPct'] - matched['FirstPromoPct']

    top_brand_names = df['Brand'].value_counts().head(8).index.tolist()
    weekly_by_brand = (
        df[df['Brand'].isin(top_brand_names)]
        .groupby(['Week', 'Brand'])['NetPrice'].mean()
        .unstack()
    )

    return {'weekly': weekly, 'matched': matched, 'weekly_by_brand': weekly_by_brand}


def _mkt_summarize(matched, col, min_n=15):
    g = matched.groupby(col).agg(
        N=('PriceChangePct', 'size'),
        ShareUp=('PriceChange', lambda s: (s > 0).mean() * 100),
        ShareDown=('PriceChange', lambda s: (s < 0).mean() * 100),
        MeanChangePct=('PriceChangePct', 'mean'),
        MedianChangePct=('PriceChangePct', lambda s: s[s != 0].median() if (s != 0).any() else float('nan')),
        AvgFirstPrice=('FirstPrice', 'mean'),
        AvgLastPrice=('LastPrice', 'mean'),
        AvgPromoPtsChange=('PromoPtsChange', 'mean'),
    )
    return g[g['N'] >= min_n].sort_values('MeanChangePct', ascending=False)


def _mkt_bar_fig(labels, values, xlabel='平均價格變化 %'):
    colors = [MKT_UP_COLOR if v > 0 else MKT_DOWN_COLOR for v in values]
    fig = go.Figure(go.Bar(
        x=list(values), y=list(labels), orientation='h',
        marker_color=colors,
        text=[f'{v:+.1f}%' for v in values],
        textposition='outside', textfont=dict(size=11),
        hovertemplate='<b>%{y}</b>: %{x:+.2f}%<extra></extra>',
    ))
    fig.update_layout(
        height=max(260, 32 * len(labels) + 90),
        margin=dict(l=10, r=48, t=10, b=36),
        xaxis_title=xlabel,
        xaxis=dict(zeroline=True, zerolinecolor='#cbd5e1', zerolinewidth=1.5,
                   gridcolor='#f1f5f9', tickfont=dict(size=11, color='#64748b')),
        yaxis=dict(autorange='reversed', tickfont=dict(size=11.5, color='#334155')),
        plot_bgcolor='white', paper_bgcolor='white',
        font=dict(family='Segoe UI, system-ui, sans-serif'),
    )
    return fig


def _mkt_trend_fig(weekly):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=weekly['Week'], y=weekly['OnPromoShare'], name='促銷佔比 %',
                          marker_color='rgba(220,38,38,.16)', yaxis='y2'))
    fig.add_trace(go.Scatter(x=weekly['Week'], y=weekly['AvgNetPrice'], name='平均淨價',
                              mode='lines+markers', line=dict(color='#2563eb', width=2.5),
                              marker=dict(size=6)))
    fig.add_trace(go.Scatter(x=weekly['Week'], y=weekly['MedNetPrice'], name='中位價',
                              mode='lines+markers', line=dict(color='#7c3aed', width=2, dash='dot'),
                              marker=dict(size=5)))
    fig.update_layout(
        height=380, margin=dict(l=55, r=55, t=40, b=40),
        xaxis=dict(title='週次 (Week #)', tickfont=dict(size=11, color='#64748b'), showgrid=False),
        yaxis=dict(title='價格 (USD/CAD)', tickprefix='$', tickformat=',.0f',
                   tickfont=dict(size=11, color='#64748b'), gridcolor='#f1f5f9'),
        yaxis2=dict(title='促銷佔比 %', overlaying='y', side='right', range=[0, 60],
                    tickfont=dict(size=11, color='#64748b'), showgrid=False),
        legend=dict(orientation='h', x=0, y=1.14, font=dict(size=11)),
        plot_bgcolor='white', paper_bgcolor='white',
        font=dict(family='Segoe UI, system-ui, sans-serif'),
    )
    return fig


def _mkt_brand_trend_fig(weekly_by_brand):
    fig = go.Figure()
    for brand in weekly_by_brand.columns:
        color = VENDOR_COLORS.get(str(brand).upper(), DEFAULT_COLOR)
        width = 3 if str(brand).upper() == 'ASUS' else 1.6
        fig.add_trace(go.Scatter(
            x=weekly_by_brand.index, y=weekly_by_brand[brand], name=brand,
            mode='lines+markers', line=dict(color=color, width=width), marker=dict(size=5),
        ))
    fig.update_layout(
        height=360, margin=dict(l=55, r=20, t=30, b=40),
        xaxis=dict(title='週次 (Week #)', tickfont=dict(size=11, color='#64748b'), showgrid=False),
        yaxis=dict(title='平均成交價', tickprefix='$', tickformat=',.0f',
                   tickfont=dict(size=11, color='#64748b'), gridcolor='#f1f5f9'),
        legend=dict(orientation='h', x=0, y=1.15, font=dict(size=10.5)),
        plot_bgcolor='white', paper_bgcolor='white',
        font=dict(family='Segoe UI, system-ui, sans-serif'),
    )
    return fig


def _mkt_pct_span(v):
    if pd.isna(v):
        return '<span class="neutral">—</span>'
    cls  = 'up' if v > 0 else ('down' if v < 0 else 'neutral')
    sign = '+' if v > 0 else ''
    return f'<span class="{cls}">{sign}{v:.2f}%</span>'


def _mkt_table(g, highlight=None):
    highlight = highlight or set()
    head = ('<thead><tr><th class="rowlabel">分類</th><th>組數</th><th>平均變化</th>'
            '<th>中位變化(非零)</th><th>漲比例</th><th>跌比例</th>'
            '<th>首週均價</th><th>末週均價</th><th>促銷變化(pts)</th></tr></thead>')
    rows = []
    for idx, r in g.iterrows():
        hl = ' class="hl"' if idx in highlight else ''
        rows.append(
            f'<tr{hl}><td class="rowlabel">{html_lib.escape(str(idx))}</td>'
            f'<td>{int(r["N"])}</td>'
            f'<td>{_mkt_pct_span(r["MeanChangePct"])}</td>'
            f'<td>{_mkt_pct_span(r["MedianChangePct"])}</td>'
            f'<td>{r["ShareUp"]:.1f}%</td>'
            f'<td>{r["ShareDown"]:.1f}%</td>'
            f'<td>${r["AvgFirstPrice"]:,.0f}</td>'
            f'<td>${r["AvgLastPrice"]:,.0f}</td>'
            f'<td>{r["AvgPromoPtsChange"]:+.2f}</td></tr>'
        )
    return f'<table class="ov-table">{head}<tbody>{"".join(rows)}</tbody></table>'


def build_market_overview():
    """Compute + render the cross-brand 'Market Overview' tab set, live from Pricings.csv."""
    data = load_market_overview_data()
    weekly, matched, weekly_by_brand = data['weekly'], data['matched'], data['weekly_by_brand']

    w0, w1 = weekly.iloc[0], weekly.iloc[-1]
    price_delta_pct = (w1['AvgNetPrice'] - w0['AvgNetPrice']) / w0['AvgNetPrice'] * 100
    n_series   = len(matched)
    share_up   = (matched['PriceChange'] > 0).mean() * 100
    share_down = (matched['PriceChange'] < 0).mean() * 100
    overall_mean = matched['PriceChangePct'].mean()
    asus_mean    = matched.loc[matched['Brand'] == 'Asus', 'PriceChangePct'].mean()

    tabs, panels = [], []

    def add_tab(tab_id, label, panel_html, is_first):
        active  = 'active' if is_first else ''
        display = 'block' if is_first else 'none'
        tabs.append(f'<button class="seg-tab {active}" data-tab="MKT" data-seg="{tab_id}" '
                    f'onclick="switchSeg(this)">{label}</button>\n')
        panels.append(f'<div class="seg-panel" id="MKT-{tab_id}" style="display:{display}">{panel_html}</div>')

    # ---- Tab 1: 整體趨勢 ----
    trend_html = _mkt_trend_fig(weekly).to_html(full_html=False, include_plotlyjs=False,
                                                 div_id='mkt-chart-trend', config={'responsive': True})
    brand_trend_html = _mkt_brand_trend_fig(weekly_by_brand).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-brandtrend', config={'responsive': True})
    summary1 = (
        f'<div class="sum-row"><span class="sum-label">均價變化 W{int(w0["Week"])}→W{int(w1["Week"])}</span>'
        f'<span class="sum-val">{_mkt_pct_span(price_delta_pct)} '
        f'(${w0["AvgNetPrice"]:,.0f} → ${w1["AvgNetPrice"]:,.0f})</span></div>'
        f'<div class="sum-row"><span class="sum-label">促銷佔比變化</span>'
        f'<span class="sum-val">{w1["OnPromoShare"]-w0["OnPromoShare"]:+.1f} pts '
        f'({w0["OnPromoShare"]:.0f}% → {w1["OnPromoShare"]:.0f}%)</span></div>'
        f'<div class="sum-row"><span class="sum-label">可比對序列數</span>'
        f'<span class="sum-val">{n_series:,} 組（同SKU＋同通路）</span></div>'
        f'<div class="sum-row"><span class="sum-label">漲 / 跌比例</span>'
        f'<span class="sum-val">{share_up:.0f}% 漲 · {share_down:.0f}% 跌</span></div>'
    )
    panel1 = (
        f'<div class="ov-callout">全市場均價由 W{int(w0["Week"])} 的 ${w0["AvgNetPrice"]:,.0f} '
        f'上升至 W{int(w1["Week"])} 的 ${w1["AvgNetPrice"]:,.0f}（{_mkt_pct_span(price_delta_pct)}），'
        f'促銷佔比同步由 {w0["OnPromoShare"]:.0f}% 升至 {w1["OnPromoShare"]:.0f}%。'
        f'<strong>ASUS</strong> 同期同SKU平均變化為 {_mkt_pct_span(asus_mean)}，'
        f'{"高於" if asus_mean > overall_mean else "低於"}全市場平均的 {_mkt_pct_span(overall_mean)}。</div>'
        '<div class="panel-top">'
        f'<div class="chart-wrap">{trend_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">整體趨勢摘要</div>{summary1}</div>'
        '</div>'
        '<div class="ov-subhead">主要品牌週度均價走勢</div>'
        f'<div class="chart-wrap" style="margin-bottom:16px">{brand_trend_html}</div>'
    )
    add_tab('trend', '整體趨勢', panel1, True)

    # ---- Tab 2: 品牌 ----
    g_brand = _mkt_summarize(matched, 'Brand', min_n=15)
    chart_brand_html = _mkt_bar_fig(g_brand.index.tolist(), g_brand['MeanChangePct'].tolist()).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-brand', config={'responsive': True})
    top_b, bot_b = g_brand.index[0], g_brand.index[-1]
    summary2 = (
        f'<div class="sum-row"><span class="sum-label">漲幅最大品牌</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(top_b)}</strong> '
        f'{_mkt_pct_span(g_brand.loc[top_b, "MeanChangePct"])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">跌幅最大品牌</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(bot_b)}</strong> '
        f'{_mkt_pct_span(g_brand.loc[bot_b, "MeanChangePct"])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">ASUS</span>'
        f'<span class="sum-val">{_mkt_pct_span(g_brand.loc["Asus", "MeanChangePct"]) if "Asus" in g_brand.index else "—"}</span></div>'
    )
    panel2 = (
        '<div class="panel-top">'
        f'<div class="chart-wrap">{chart_brand_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">品牌摘要</div>{summary2}</div>'
        '</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_brand, highlight={"Asus"})}</div>'
    )
    add_tab('brand', '品牌', panel2, False)

    # ---- Tab 3: 產品類型 ----
    g_seg  = _mkt_summarize(matched[matched['Market Segment'] != '-'], 'Market Segment', min_n=5)
    g_form = _mkt_summarize(matched[matched['Form Factor'] != '-'], 'Form Factor', min_n=5)
    chart_seg_html = _mkt_bar_fig(g_seg.index.tolist(), g_seg['MeanChangePct'].tolist()).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-seg', config={'responsive': True})
    summary3 = (
        f'<div class="sum-row"><span class="sum-label">漲幅最大定位</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_seg.index[0])}</strong> '
        f'{_mkt_pct_span(g_seg["MeanChangePct"].iloc[0])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">Clamshell vs Convertible</span>'
        f'<span class="sum-val">' +
        ' · '.join(f'{idx} {_mkt_pct_span(row["MeanChangePct"])}' for idx, row in g_form.iterrows()) +
        '</span></div>'
    )
    panel3 = (
        '<div class="panel-top">'
        f'<div class="chart-wrap">{chart_seg_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">產品類型摘要</div>{summary3}</div>'
        '</div>'
        '<div class="ov-subhead">依市場定位 (Market Segment)</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_seg)}</div>'
        '<div class="ov-subhead">依外型 (Form Factor)</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_form)}</div>'
    )
    add_tab('segment', '產品類型', panel3, False)

    # ---- Tab 4: CPU ----
    g_cpu_tier  = _mkt_summarize(matched[matched['CPU_Tier'] != 'Unknown'], 'CPU_Tier', min_n=5)
    g_cpu_brand = _mkt_summarize(matched[matched['CPU_Brand'] != 'Unknown'], 'CPU_Brand', min_n=5)
    chart_cpu_html = _mkt_bar_fig(g_cpu_tier.index.tolist(), g_cpu_tier['MeanChangePct'].tolist()).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-cpu', config={'responsive': True})
    summary4 = (
        f'<div class="sum-row"><span class="sum-label">漲幅最大等級</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_cpu_tier.index[0])}</strong> '
        f'{_mkt_pct_span(g_cpu_tier["MeanChangePct"].iloc[0])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">處理器品牌</span>'
        f'<span class="sum-val">' +
        ' · '.join(f'{idx} {_mkt_pct_span(row["MeanChangePct"])}' for idx, row in g_cpu_brand.iterrows()) +
        '</span></div>'
    )
    panel4 = (
        '<div class="panel-top">'
        f'<div class="chart-wrap">{chart_cpu_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">CPU 摘要</div>{summary4}</div>'
        '</div>'
        '<div class="ov-subhead">依 CPU 等級</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_cpu_tier)}</div>'
        '<div class="ov-subhead">依 CPU 品牌</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_cpu_brand)}</div>'
    )
    add_tab('cpu', 'CPU', panel4, False)

    # ---- Tab 5: GPU ----
    g_gpu = _mkt_summarize(matched[matched['GPU_Bucket'] != 'Unknown/None'], 'GPU_Bucket', min_n=5)
    chart_gpu_html = _mkt_bar_fig(g_gpu.index.tolist(), g_gpu['MeanChangePct'].tolist()).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-gpu', config={'responsive': True})
    summary5 = (
        f'<div class="sum-row"><span class="sum-label">漲幅最大類型</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_gpu.index[0])}</strong> '
        f'{_mkt_pct_span(g_gpu["MeanChangePct"].iloc[0])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">跌幅最大類型</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_gpu.index[-1])}</strong> '
        f'{_mkt_pct_span(g_gpu["MeanChangePct"].iloc[-1])}</span></div>'
    )
    panel5 = (
        '<div class="panel-top">'
        f'<div class="chart-wrap">{chart_gpu_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">GPU 摘要</div>{summary5}</div>'
        '</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_gpu)}</div>'
    )
    add_tab('gpu', 'GPU', panel5, False)

    # ---- Tab 6: ASUS 焦點 ----
    asus_m = matched[matched['Brand'] == 'Asus']
    g_asus_family = _mkt_summarize(asus_m, 'Product Family', min_n=5)
    g_asus_seg    = _mkt_summarize(asus_m[asus_m['Market Segment'] != '-'], 'Market Segment', min_n=5)
    chart_asus_html = _mkt_bar_fig(g_asus_family.index.tolist(), g_asus_family['MeanChangePct'].tolist()).to_html(
        full_html=False, include_plotlyjs=False, div_id='mkt-chart-asusfamily', config={'responsive': True})

    comp_brands = ['Asus', 'HP', 'Lenovo', 'Dell', 'Acer', 'MSI', 'Apple', 'Microsoft']
    comp = matched[matched['Brand'].isin(comp_brands)]
    piv = (comp.groupby(['Market Segment', 'Brand'])
           .agg(N=('PriceChangePct', 'size'), MeanChangePct=('PriceChangePct', 'mean'))
           .reset_index())
    piv = piv[piv['N'] >= 8]
    piv_table = piv.pivot(index='Brand', columns='Market Segment', values='MeanChangePct')
    seg_order = [c for c in ['Consumer', 'Gaming', 'Business', 'Education', 'Creator', 'Commercial']
                 if c in piv_table.columns]
    piv_table = piv_table[seg_order].reindex([b for b in comp_brands if b in piv_table.index])

    piv_head = ('<thead><tr><th class="rowlabel">品牌</th>' +
                ''.join(f'<th>{c}</th>' for c in piv_table.columns) + '</tr></thead>')
    piv_rows = []
    for b, row in piv_table.iterrows():
        hl = ' class="hl"' if b == 'Asus' else ''
        cells = f'<tr{hl}><td class="rowlabel">{html_lib.escape(b)}</td>'
        for c in piv_table.columns:
            v = row[c]
            cells += f'<td>{_mkt_pct_span(v) if pd.notna(v) else "—"}</td>'
        cells += '</tr>'
        piv_rows.append(cells)
    piv_html = f'<table class="ov-table">{piv_head}<tbody>{"".join(piv_rows)}</tbody></table>'

    summary6 = (
        f'<div class="sum-row"><span class="sum-label">ASUS 整體 vs 市場</span>'
        f'<span class="sum-val">{_mkt_pct_span(asus_mean)} vs {_mkt_pct_span(overall_mean)}</span></div>'
        f'<div class="sum-row"><span class="sum-label">系列漲幅最大</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_asus_family.index[0])}</strong> '
        f'{_mkt_pct_span(g_asus_family["MeanChangePct"].iloc[0])}</span></div>'
        f'<div class="sum-row"><span class="sum-label">系列跌幅最大</span>'
        f'<span class="sum-val"><strong>{html_lib.escape(g_asus_family.index[-1])}</strong> '
        f'{_mkt_pct_span(g_asus_family["MeanChangePct"].iloc[-1])}</span></div>'
    )
    panel6 = (
        f'<div class="ov-callout">ASUS 全品牌平均價格變化為 {_mkt_pct_span(asus_mean)}，'
        f'明顯低於全市場平均的 {_mkt_pct_span(overall_mean)}——在多數品牌持續墊高定價之際，'
        'ASUS 是少數同期均價「不升反降」的主要品牌。</div>'
        '<div class="panel-top">'
        f'<div class="chart-wrap">{chart_asus_html}</div>'
        f'<div class="summary-wrap"><div class="sum-title">ASUS 摘要</div>{summary6}</div>'
        '</div>'
        '<div class="ov-subhead">依市場定位</div>'
        f'<div class="ov-tablewrap">{_mkt_table(g_asus_seg)}</div>'
        '<div class="ov-subhead">與主要競爭品牌比較（依市場定位的平均價格變化 %）</div>'
        f'<div class="ov-tablewrap">{piv_html}</div>'
    )
    add_tab('asus', 'ASUS 焦點', panel6, False)

    return ''.join(tabs), ''.join(panels)


def generate_dashboard():
    if not os.path.exists(WEEKLY_PRICE_DB_FILE):
        print(f"Error: {WEEKLY_PRICE_DB_FILE} not found.")
        return

    db = pd.read_csv(WEEKLY_PRICE_DB_FILE)
    if db.empty:
        print("Database is empty.")
        return
    if 'Median_Price' in db.columns:
        db = db.rename(columns={'Median_Price': 'Average_Price'})
    db['Average_Price'] = pd.to_numeric(db['Average_Price'], errors='coerce')
    db = db.dropna(subset=['Average_Price'])
    db['Date'] = pd.to_datetime(db['Date'])
    db = db.sort_values(['GfK_Key', 'Date']).reset_index(drop=True)

    if not os.path.exists(TRACKED_HISTORY_FILE):
        print("Error: Tracked history not found.")
        return
    hist = pd.read_csv(TRACKED_HISTORY_FILE)
    hist = hist[hist['Is_Current'] == True].copy()
    hist = hist.sort_values('Last_Seen').drop_duplicates('GfK_Key', keep='last')

    for col in ['Vendor', 'Model', 'CPU_G', 'GPU_G2', 'Seg_Share_Pct', 'NB_NR', 'K_Unit']:
        if col not in hist.columns:
            hist[col] = ''

    mapping_dict = {}
    if os.path.exists(MASTER_MAPPING_FILE):
        mdf = pd.read_csv(MASTER_MAPPING_FILE)
        mapping_dict = dict(zip(mdf['GfK_Key'], mdf['OB_Key']))

    all_price_keys = set(db['GfK_Key'].unique())

    def ordered_segs(nb_nr, order):
        available = set(hist.loc[hist['NB_NR'] == nb_nr, 'Product_Segment'].dropna().unique())
        result = [s for s in order if s in available]
        # append any unlisted segments alphabetically
        result += sorted(available - set(order))
        return result

    nb_segs = ordered_segs('NB', NB_SEG_ORDER)
    nr_segs = ordered_segs('NR', NR_SEG_ORDER)

    def build_panels(seg_list, tab_type):
        panels_html = ''
        tabs_html   = ''

        for si, seg in enumerate(seg_list):
            seg_display = SEG_DISPLAY.get(seg, seg)
            safe_seg    = html_lib.escape(seg)
            safe_display = html_lib.escape(seg_display)
            panel_id    = f'{tab_type}-{safe_seg}'
            is_first    = si == 0
            display     = 'block' if is_first else 'none'
            active_cls  = 'active' if is_first else ''

            tabs_html += (f'<button class="seg-tab {active_cls}" '
                          f'data-tab="{tab_type}" data-seg="{safe_seg}" '
                          f'onclick="switchSeg(this)">{safe_display}</button>\n')

            seg_hist = hist[hist['Product_Segment'] == seg].copy()
            # Sort by K_Unit descending for cards and summary
            seg_hist = seg_hist.sort_values('K_Unit', ascending=False).reset_index(drop=True)

            # ── Collect product stats (shared by chart, cards, summary) ──────
            prod_stats = []
            vendor_marker_idx = {}   # track marker symbol per vendor

            for i, row in seg_hist.iterrows():
                key      = row['GfK_Key']
                vendor   = get_vendor(key)
                color    = get_color(key)
                midx     = vendor_marker_idx.get(vendor, 0)
                marker   = MARKER_SYMBOLS[midx % len(MARKER_SYMBOLS)]
                vendor_marker_idx[vendor] = midx + 1

                share_pct = float(row.get('Seg_Share_Pct', 0) or 0)
                k_unit    = float(row.get('K_Unit', 0) or 0)
                cpu       = str(row.get('CPU_G',  '') or '')
                gpu       = str(row.get('GPU_G2', '') or '')

                prod_df   = db[db['GfK_Key'] == key].sort_values('Date') if key in all_price_keys else pd.DataFrame()
                last_price, wow_diff, wow_pct, mom_diff, mom_pct, this_avg, mom_label = calc_changes(prod_df)

                ob_key         = mapping_dict.get(key, '')
                is_fuzzy, reason = match_quality(key, ob_key)

                prod_stats.append({
                    'key': key, 'vendor': vendor, 'color': color, 'marker': marker,
                    'cpu': cpu, 'gpu': gpu,
                    'share_pct': share_pct, 'k_unit': k_unit,
                    'prod_df': prod_df,
                    'last_price': last_price,
                    'wow_diff': wow_diff, 'wow_pct': wow_pct,
                    'mom_diff': mom_diff, 'mom_pct': mom_pct, 'mom_label': mom_label,
                    'ob_key': ob_key, 'is_fuzzy': is_fuzzy, 'fuzzy_reason': reason,
                })

            # ── Chart ────────────────────────────────────────────────────────
            fig = go.Figure()
            has_any_trace = False
            for ps in prod_stats:
                if ps['key'] not in all_price_keys or ps['prod_df'].empty:
                    continue
                pdf = ps['prod_df']
                legend_label = str(ps['key'])
                if len(legend_label) > 28:
                    legend_label = legend_label[:26] + '…'
                fig.add_trace(go.Scatter(
                    x=pdf['Date'].tolist(),
                    y=pdf['Average_Price'].tolist(),
                    mode='lines+markers',
                    name=legend_label,
                    line=dict(color=ps['color'], width=2.5, shape='spline', smoothing=0.6),
                    marker=dict(size=8, symbol=ps['marker'], color=ps['color'],
                                line=dict(color='white', width=1.5)),
                    hovertemplate='<b>%{y:$,.0f}</b><extra>' + html_lib.escape(str(ps['key'])) + '</extra>'
                ))
                has_any_trace = True

            chart_id = f'chart-{tab_type}-{seg.replace(" ", "_")}'
            fig.update_layout(
                xaxis_title=None, yaxis_title=None,
                yaxis_tickfont=dict(size=11, color='#64748b'),
                xaxis_tickfont=dict(size=11, color='#64748b'),
                height=420,
                autosize=True,
                margin=dict(l=60, r=20, t=70, b=40),
                hovermode='x unified',
                hoverlabel=dict(bgcolor='white', bordercolor='#e2e8f0',
                                font=dict(size=12, color='#1e293b')),
                legend=dict(
                    orientation='h', xanchor='left', x=0, yanchor='bottom', y=1.02,
                    font=dict(size=10.5, color='#334155'),
                    bgcolor='rgba(0,0,0,0)', borderwidth=0,
                    tracegroupgap=4,
                ),
                legend_itemclick=False,
                legend_itemdoubleclick=False,
                plot_bgcolor='white',
                paper_bgcolor='white',
                font=dict(family='Segoe UI, system-ui, sans-serif'),
            )
            fig.update_xaxes(
                showgrid=False, zeroline=False,
                showline=True, linecolor='#e2e8f0', linewidth=1,
                tickformat='%b %-d\n%Y',
                ticks='outside', ticklen=4, tickcolor='#e2e8f0',
            )
            fig.update_yaxes(
                showgrid=True, gridcolor='#f1f5f9', gridwidth=1,
                zeroline=False, showline=False,
                tickprefix='$', tickformat=',.0f',
            )

            if not has_any_trace:
                chart_html = '<div class="no-data-msg">此 Segment 尚無 Open Brand 價格資料</div>'
            else:
                chart_html = fig.to_html(
                    full_html=False,
                    include_plotlyjs=False,
                    div_id=chart_id,
                    config={'responsive': True},
                )
                # Custom legend interaction: click=isolate, click again=add, dblclick=reset
                chart_html += f'''<script>
(function(){{
  var attempts=0;
  function init(){{
    var gd=document.getElementById('{chart_id}');
    if(!gd||!gd.on){{if(++attempts<30)setTimeout(init,200);return;}}
    var sel=new Set();
    gd.on('plotly_legendclick',function(d){{
      var n=d.curveNumber, N=gd.data.length;
      if(sel.size===0){{
        sel.add(n);
      }} else if(sel.has(n)){{
        sel.delete(n);
        if(sel.size===0){{
          Plotly.restyle(gd,'visible',Array(N).fill(true));
          return false;
        }}
      }} else {{
        sel.add(n);
      }}
      Plotly.restyle(gd,'visible',gd.data.map(function(_,i){{return sel.has(i)?true:'legendonly';}}));
      return false;
    }});
    gd.on('plotly_legenddoubleclick',function(){{
      sel.clear();
      Plotly.restyle(gd,'visible',Array(gd.data.length).fill(true));
      return false;
    }});
  }}
  setTimeout(init,400);
}})();
</script>'''

            # ── Summary text ─────────────────────────────────────────────────
            summary_html = build_summary_html(prod_stats)

            # ── Product cards (sorted by K_Unit already) ─────────────────────
            cards_html = ''
            for ps in prod_stats:
                key       = ps['key']
                color     = ps['color']
                safe_key  = html_lib.escape(str(key))
                vendor    = html_lib.escape(ps['vendor'])
                cpu       = html_lib.escape(ps['cpu'])
                gpu       = html_lib.escape(ps['gpu'])
                share_pct = ps['share_pct']
                bar_w     = min(share_pct, 100)

                last_price = ps['last_price']
                price_str  = f'${last_price:,.2f}' if last_price is not None else '<span class="no-price">No price data</span>'
                wow_badge  = change_badge(ps['wow_diff'], ps['wow_pct'], 'WoW')
                mom_badge  = change_badge(ps['mom_diff'], ps['mom_pct'], 'MoM', sublabel=ps['mom_label'])

                ob_key   = ps['ob_key']
                safe_ob  = html_lib.escape(str(ob_key)) if ob_key else ''
                if ps['is_fuzzy']:
                    fuzzy_badge = (
                        f'<span class="fuzzy-badge" '
                        f'title="價格來自近似產品 ({html_lib.escape(ps["fuzzy_reason"])})&#10;OB: {safe_ob}">≈ 近似比對</span>'
                    )
                elif not ob_key:
                    fuzzy_badge = ''
                else:
                    fuzzy_badge = f'<span class="exact-badge" title="OB: {safe_ob}">✓ 精確比對</span>'

                cards_html += f'''
                <div class="prod-card" style="border-top:3px solid {color}">
                    <div class="prod-name" title="{safe_key}">{safe_key}</div>
                    <div class="spec-row">
                        <span class="spec-chip">{vendor}</span>
                        <span class="spec-chip">{cpu}</span>
                        <span class="spec-chip">{gpu}</span>
                        {fuzzy_badge}
                    </div>
                    <div class="share-row">
                        <div class="share-bar-bg">
                            <div class="share-bar-fill" style="width:{bar_w}%;background:{color}"></div>
                        </div>
                        <span class="share-label">{share_pct:.1f}% of segment</span>
                    </div>
                    <div class="current-price">{price_str}</div>
                    <div class="metrics-row">{wow_badge}{mom_badge}</div>
                </div>'''

            panels_html += f'''
            <div class="seg-panel" id="{panel_id}" style="display:{display}">
                <div class="panel-top">
                    <div class="chart-wrap">{chart_html}</div>
                    <div class="summary-wrap">
                        <div class="sum-title">{safe_display} 摘要</div>
                        {summary_html}
                    </div>
                </div>
                <div class="cards-row">{cards_html}</div>
            </div>'''

        return tabs_html, panels_html

    nb_tabs, nb_panels = build_panels(nb_segs, 'NB')
    nr_tabs, nr_panels = build_panels(nr_segs, 'NR')
    mkt_tabs, mkt_panels = build_market_overview()

    # Extract Plotly JS once for <head> (capture all script tags, take the large one)
    _tmp_html = go.Figure().to_html(full_html=False, include_plotlyjs='inline', div_id='__tmp__')
    _scripts = re.findall(r'<script[^>]*>.*?</script>', _tmp_html, re.DOTALL)
    # The Plotly JS bundle is the largest script tag
    plotly_js_tag = max(_scripts, key=len) if _scripts else '<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>'

    html_out = f"""<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<title>Weekly Price Tracker</title>
{plotly_js_tag}
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0;
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #f1f5f9; color: #1e293b; font-size: 14px;
  }}

  /* ── Header ── */
  .topbar {{
    background: #1e293b; color: #f8fafc;
    padding: 14px 28px; display: flex; align-items: baseline; gap: 14px;
  }}
  .topbar h1 {{ margin: 0; font-size: 18px; font-weight: 700; }}
  .topbar .subtitle {{ font-size: 12px; color: #94a3b8; }}

  /* ── NB / NR toggle ── */
  .nbnr-bar {{
    background: #ffffff; border-bottom: 1px solid #e2e8f0;
    padding: 10px 24px; display: flex; gap: 8px; align-items: center;
  }}
  .nbnr-label {{ font-size: 11px; font-weight: 700; letter-spacing:.08em;
                 text-transform: uppercase; color: #94a3b8; margin-right: 4px; }}
  .nbnr-btn {{
    border: 2px solid #e2e8f0; background: #f8fafc;
    padding: 6px 22px; border-radius: 20px;
    font-size: 13px; font-weight: 700; color: #64748b;
    cursor: pointer; transition: all .15s;
  }}
  .nbnr-btn:hover {{ border-color: #2563eb; color: #2563eb; }}
  .nbnr-btn.active {{ background: #2563eb; border-color: #2563eb; color: #ffffff; }}

  /* ── Segment tabs ── */
  .seg-tabs-bar {{
    background: #ffffff; border-bottom: 1px solid #e2e8f0;
    padding: 0 24px; display: flex; gap: 2px; overflow-x: auto;
    scrollbar-width: none;
  }}
  .seg-tabs-bar::-webkit-scrollbar {{ display: none; }}
  .seg-tab {{
    border: none; background: none; padding: 10px 16px;
    font-size: 13px; font-weight: 600; color: #64748b;
    cursor: pointer; border-bottom: 3px solid transparent;
    white-space: nowrap; transition: color .15s, border-color .15s;
  }}
  .seg-tab:hover {{ color: #1e293b; }}
  .seg-tab.active {{ color: #2563eb; border-bottom-color: #2563eb; }}

  /* ── Content ── */
  .content {{ padding: 20px 24px; max-width: 100%; }}
  .nbnr-pane {{ display: none; }}
  .nbnr-pane.active {{ display: block; }}

  /* ── Panel top: chart + summary side by side ── */
  .panel-top {{
    display: grid;
    grid-template-columns: 1fr 260px;
    gap: 16px; margin-bottom: 16px;
  }}
  .chart-wrap {{
    min-width: 0;
    background: #fff; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 8px;
  }}
  .summary-wrap {{
    min-width: 0;
    background: #fff; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 18px 20px;
    display: flex; flex-direction: column; gap: 0;
  }}
  .sum-title {{
    font-size: 13px; font-weight: 800; color: #334155;
    margin-bottom: 14px; letter-spacing: .03em;
    border-bottom: 2px solid #e2e8f0; padding-bottom: 8px;
  }}
  .sum-row {{
    display: flex; flex-direction: column;
    padding: 8px 0; border-bottom: 1px solid #f1f5f9;
  }}
  .sum-row:last-child {{ border-bottom: none; }}
  .sum-label {{
    font-size: 9.5px; font-weight: 800; letter-spacing: .08em;
    text-transform: uppercase; color: #94a3b8; margin-bottom: 3px;
  }}
  .sum-val {{ font-size: 12px; color: #334155; line-height: 1.5; }}
  .sum-up      {{ color: #dc2626; font-weight: 700; }}
  .sum-dn      {{ color: #16a34a; font-weight: 700; }}
  .sum-neutral {{ color: #94a3b8; }}

  .no-data-msg {{
    text-align: center; padding: 60px; color: #94a3b8;
    font-size: 14px; font-style: italic;
  }}

  /* ── Product cards ── */
  .cards-row {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px; margin-bottom: 8px;
  }}
  @media (max-width: 1200px) {{ .cards-row {{ grid-template-columns: repeat(3, 1fr); }} }}
  @media (max-width: 1100px) {{ .panel-top {{ grid-template-columns: 1fr; }} }}

  .prod-card {{
    background: #fff; border-radius: 8px;
    padding: 14px 14px 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,.07);
    display: flex; flex-direction: column; gap: 7px;
  }}
  .prod-name {{
    font-size: 11.5px; font-weight: 700; color: #334155;
    line-height: 1.35; overflow: hidden;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    min-height: 2.7em;
  }}
  .fuzzy-badge {{
    font-size: 9.5px; font-weight: 700;
    background: #fef3c7; color: #92400e; border: 1px solid #fcd34d;
    padding: 2px 6px; border-radius: 4px; cursor: help;
    white-space: nowrap; line-height: 1.6;
  }}
  .exact-badge {{
    font-size: 9.5px; font-weight: 700;
    background: #dcfce7; color: #166534; border: 1px solid #86efac;
    padding: 2px 6px; border-radius: 4px; cursor: help;
    white-space: nowrap; line-height: 1.6;
  }}

  /* ── Spec chips ── */
  .spec-row {{ display: flex; gap: 4px; flex-wrap: wrap; }}
  .spec-chip {{
    font-size: 10px; font-weight: 600; background: #f1f5f9;
    color: #475569; padding: 2px 6px; border-radius: 4px;
    white-space: nowrap;
  }}

  /* ── Share bar ── */
  .share-row {{ display: flex; align-items: center; gap: 8px; }}
  .share-bar-bg {{
    flex: 1; height: 5px; background: #e2e8f0; border-radius: 3px; overflow: hidden;
  }}
  .share-bar-fill {{ height: 100%; border-radius: 3px; transition: width .4s; }}
  .share-label {{ font-size: 10.5px; font-weight: 700; color: #64748b; white-space: nowrap; }}

  /* ── Price ── */
  .current-price {{
    font-size: 22px; font-weight: 800; color: #1e293b;
    font-variant-numeric: tabular-nums; letter-spacing: -.01em;
  }}
  .no-price {{ font-size: 13px; color: #94a3b8; font-style: italic; font-weight: 400; }}

  /* ── WoW / MoM ── */
  .metrics-row {{ display: flex; gap: 8px; }}
  .metric {{
    flex: 1; background: #f8fafc; border-radius: 6px;
    padding: 6px 8px;
  }}
  .metric-label {{
    font-size: 9.5px; font-weight: 800; letter-spacing: .08em;
    text-transform: uppercase; color: #94a3b8; margin-bottom: 2px;
  }}
  .metric-sub {{ font-size: 10px; opacity: .8; font-weight: 400; }}
  .up      {{ color: #dc2626; font-size: 12px; font-weight: 700; line-height: 1.4; }}
  .down    {{ color: #16a34a; font-size: 12px; font-weight: 700; line-height: 1.4; }}
  .neutral {{ color: #94a3b8; font-size: 12px; font-weight: 600; }}

  /* ── Market Overview tab ── */
  .ov-tablewrap {{
    overflow-x: auto; background: #fff; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 16px;
  }}
  .ov-table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 640px; }}
  .ov-table th {{
    text-align: right; padding: 10px 12px; font-size: 10px; font-weight: 800;
    text-transform: uppercase; letter-spacing: .05em; color: #94a3b8;
    border-bottom: 2px solid #e2e8f0; white-space: nowrap;
  }}
  .ov-table th.rowlabel {{ text-align: left; }}
  .ov-table td {{
    padding: 8px 12px; border-bottom: 1px solid #f1f5f9; text-align: right;
    font-variant-numeric: tabular-nums; white-space: nowrap;
  }}
  .ov-table td.rowlabel {{ text-align: left; font-weight: 600; color: #334155; white-space: normal; }}
  .ov-table tr.hl {{ background: #fef2f2; }}
  .ov-table tr.hl td.rowlabel {{ color: #dc2626; font-weight: 800; }}
  .ov-table tbody tr:hover {{ background: #f8fafc; }}
  .ov-callout {{
    background: #fef2f2; border-left: 3px solid #dc2626; padding: 14px 16px;
    border-radius: 0 8px 8px 0; margin-bottom: 16px; font-size: 13px;
    color: #334155; line-height: 1.65;
  }}
  .ov-callout strong {{ color: #dc2626; }}
  .ov-subhead {{ font-size: 12.5px; font-weight: 800; color: #334155; letter-spacing: .03em; margin: 18px 2px 8px; }}
</style>
</head>
<body>

<div class="topbar">
  <h1>Weekly Price Tracker</h1>
  <span class="subtitle">NA Market · Top 5 per Segment · Open Brand US</span>
</div>

<div class="nbnr-bar">
  <span class="nbnr-label">Category</span>
  <button class="nbnr-btn active" onclick="switchNBNR('NB', this)">NB — Notebook</button>
  <button class="nbnr-btn"       onclick="switchNBNR('NR', this)">NR — Gaming</button>
  <button class="nbnr-btn"       onclick="switchNBNR('MKT', this)">市場總覽 · All Brands</button>
</div>

<div class="seg-tabs-bar" id="tabs-NB">
{nb_tabs}
</div>
<div class="seg-tabs-bar" id="tabs-NR" style="display:none">
{nr_tabs}
</div>
<div class="seg-tabs-bar" id="tabs-MKT" style="display:none">
{mkt_tabs}
</div>

<div class="content">
  <div class="nbnr-pane active" id="pane-NB">
{nb_panels}
  </div>
  <div class="nbnr-pane" id="pane-NR">
{nr_panels}
  </div>
  <div class="nbnr-pane" id="pane-MKT">
{mkt_panels}
  </div>
</div>

<script>
function switchNBNR(type, btn) {{
  document.querySelectorAll('.nbnr-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.nbnr-pane').forEach(p => p.classList.remove('active'));
  var pane = document.getElementById('pane-' + type);
  pane.classList.add('active');
  ['NB', 'NR', 'MKT'].forEach(function(t) {{
    var el = document.getElementById('tabs-' + t);
    if (el) el.style.display = (t === type) ? 'flex' : 'none';
  }});
  // Resize visible chart
  var visPanel = pane.querySelector('.seg-panel[style*="block"]');
  if (visPanel) visPanel.querySelectorAll('.plotly-graph-div').forEach(function(gd) {{
    if (window.Plotly) Plotly.Plots.resize(gd);
  }});
}}

function switchSeg(btn) {{
  var tabType = btn.getAttribute('data-tab');
  var seg     = btn.getAttribute('data-seg');
  document.querySelectorAll('[data-tab="' + tabType + '"]').forEach(function(b) {{
    b.classList.remove('active');
  }});
  btn.classList.add('active');
  document.querySelectorAll('#pane-' + tabType + ' .seg-panel').forEach(function(p) {{
    p.style.display = 'none';
  }});
  var panel = document.getElementById(tabType + '-' + seg);
  if (panel) {{
    panel.style.display = 'block';
    // Resize all Plotly charts in this panel after it becomes visible
    panel.querySelectorAll('.plotly-graph-div').forEach(function(gd) {{
      if (window.Plotly) Plotly.Plots.resize(gd);
    }});
  }}
}}
// Resize all visible charts on initial load
window.addEventListener('load', function() {{
  document.querySelectorAll('.seg-panel').forEach(function(p) {{
    if (p.style.display !== 'none') {{
      p.querySelectorAll('.plotly-graph-div').forEach(function(gd) {{
        if (window.Plotly) Plotly.Plots.resize(gd);
      }});
    }}
  }});
}});
</script>

</body>
</html>"""

    with open(DASHBOARD_HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html_out)
    print(f"Dashboard generated → {DASHBOARD_HTML_FILE}")


if __name__ == "__main__":
    generate_dashboard()
