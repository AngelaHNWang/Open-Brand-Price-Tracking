import pandas as pd
import plotly.graph_objects as go
import os
import re
import html as html_lib

WORKSPACE_DIR        = r"D:\ASUS\Claude-Analysis\Open Brand Price Tracking"
WEEKLY_PRICE_DB_FILE = os.path.join(WORKSPACE_DIR, "weekly_price_database.csv")
TRACKED_HISTORY_FILE = os.path.join(WORKSPACE_DIR, "tracked_products_history.csv")
MASTER_MAPPING_FILE  = os.path.join(WORKSPACE_DIR, "master_mapping.csv")
DASHBOARD_HTML_FILE  = os.path.join(WORKSPACE_DIR, "Weekly_Price_Dashboard.html")

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
                fig.add_trace(go.Scatter(
                    x=pdf['Date'].tolist(),
                    y=pdf['Average_Price'].tolist(),
                    mode='lines+markers',
                    name=html_lib.escape(str(ps['key'])),
                    line=dict(color=ps['color'], width=2.5),
                    marker=dict(size=7, symbol=ps['marker'], color=ps['color']),
                    hovertemplate='%{y:$,.2f}<extra>' + html_lib.escape(str(ps['key'])) + '</extra>'
                ))
                has_any_trace = True

            chart_id = f'chart-{tab_type}-{seg.replace(" ", "_")}'
            fig.update_layout(
                xaxis_title='Date', yaxis_title='Average Price (USD)',
                yaxis_tickprefix='$', height=360,
                margin=dict(l=10, r=10, t=20, b=10),
                hovermode='x unified',
                legend=dict(orientation='h', yanchor='bottom', y=1.01,
                            xanchor='left', x=0, font=dict(size=11)),
                legend_itemclick=False,
                legend_itemdoubleclick=False,
                plot_bgcolor='#f8fafc', paper_bgcolor='#ffffff',
            )
            fig.update_xaxes(showgrid=True, gridcolor='#e2e8f0', zeroline=False)
            fig.update_yaxes(showgrid=True, gridcolor='#e2e8f0', zeroline=False)

            if not has_any_trace:
                chart_html = '<div class="no-data-msg">此 Segment 尚無 Open Brand 價格資料</div>'
            else:
                chart_html = fig.to_html(
                    full_html=False,
                    include_plotlyjs=False,
                    div_id=chart_id,
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
  .content {{ padding: 20px 24px; max-width: 1600px; margin: 0 auto; }}
  .nbnr-pane {{ display: none; }}
  .nbnr-pane.active {{ display: block; }}

  /* ── Panel top: chart + summary side by side ── */
  .panel-top {{
    display: grid;
    grid-template-columns: 1fr 290px;
    gap: 16px; margin-bottom: 16px;
  }}
  .chart-wrap {{
    min-width: 0; overflow: hidden;
    background: #fff; border-radius: 10px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    padding: 16px 16px 8px;
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
</div>

<div class="seg-tabs-bar" id="tabs-NB">
{nb_tabs}
</div>
<div class="seg-tabs-bar" id="tabs-NR" style="display:none">
{nr_tabs}
</div>

<div class="content">
  <div class="nbnr-pane active" id="pane-NB">
{nb_panels}
  </div>
  <div class="nbnr-pane" id="pane-NR">
{nr_panels}
  </div>
</div>

<script>
function switchNBNR(type, btn) {{
  document.querySelectorAll('.nbnr-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.nbnr-pane').forEach(p => p.classList.remove('active'));
  document.getElementById('pane-' + type).classList.add('active');
  document.getElementById('tabs-NB').style.display = type === 'NB' ? 'flex' : 'none';
  document.getElementById('tabs-NR').style.display = type === 'NR' ? 'flex' : 'none';
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
  if (panel) panel.style.display = 'block';
}}
</script>

</body>
</html>"""

    with open(DASHBOARD_HTML_FILE, 'w', encoding='utf-8') as f:
        f.write(html_out)
    print(f"Dashboard generated → {DASHBOARD_HTML_FILE}")


if __name__ == "__main__":
    generate_dashboard()
