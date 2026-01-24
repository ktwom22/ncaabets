from flask import Flask, render_template
import pandas as pd
import requests
import io

app = Flask(__name__)

# DATA SOURCE
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
# HISTORY SOURCE (Ensure this GID is correct for your history tab)
HISTORY_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=0&single=true&output=csv"

def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None']: return default
        return float(val)
    except: return default

def get_data(url):
    try:
        res = requests.get(f"{url}&cb={pd.Timestamp.now().timestamp()}", timeout=5)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
        return df
    except: return pd.DataFrame()

@app.route('/')
def index():
    df = get_data(SHEET_URL)
    if df.empty: return "<h1>Data Syncing... Refresh in a moment.</h1>"

    all_games = []
    for _, row in df.iterrows():
        # Clean data and run math
        r_away, r_home = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        ppg_a, ppg_h = clean_val(row.get('PPG Away'), 70), clean_val(row.get('PPG Home'), 70)
        ppga_a, ppga_h = clean_val(row.get('PPGA Away'), 70), clean_val(row.get('PPGA Home'), 70)
        h_vegas = clean_val(row.get('FD Spread'), 0.0)

        a_proj = 71.0 + ((182 - r_away) / 18.0) + (((ppg_a - 72) + (72 - ppga_h)) * 0.4)
        h_proj = 71.0 + ((182 - r_home) / 18.0) + (((ppg_h - 72) + (72 - ppga_a)) * 0.4) + 3.2

        m_spread = round(a_proj - h_proj, 1)
        raw_edge = m_spread - h_vegas
        status = "AWAY" if raw_edge > 1.5 else "HOME" if raw_edge < -1.5 else "NONE"

        all_games.append({
            'away_team': row.get('Away Team', 'Away'),
            'home_team': row.get('Home Team', 'Home'),
            'away_logo': row.get('Away Logo', ''),
            'home_logo': row.get('Home Logo', ''),
            'a_proj': round(a_proj, 1),
            'h_proj': round(h_proj, 1),
            'a_odds': h_vegas * -1,
            'h_odds': h_vegas,
            'edge': abs(round(raw_edge, 1)),
            'status': status,
            'pick_team': row.get('Away Team') if status == "AWAY" else row.get('Home Team') if status == "HOME" else "No Value",
            'v_total': row.get('FD Total', '---'),
            'total': round(a_proj + h_proj, 1)
        })

    # Sort top 3 games by edge for the Parlay
    parlay = sorted([g for g in all_games if g['status'] != 'NONE'], key=lambda x: x['edge'], reverse=True)[:3]

    # Stats Calculation
    df_hist = get_data(HISTORY_URL)
    stats = {'wins': 0, 'total': 0, 'rate': 0, 'profit': 0}
    if not df_hist.empty and 'Result' in df_hist.columns:
        stats['total'] = len(df_hist)
        stats['wins'] = len(df_hist[df_hist['Result'].str.lower() == 'win'])
        stats['rate'] = round((stats['wins'] / stats['total'] * 100), 1) if stats['total'] > 0 else 0
        stats['profit'] = round((stats['wins'] * 1.0) - ((stats['total'] - stats['wins']) * 1.1), 1)

    return render_template('index.html', games=all_games, parlay=parlay, stats=stats)

if __name__ == '__main__':
    app.run(debug=True)