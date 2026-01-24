from flask import Flask, render_template
import pandas as pd
import requests
import io
from datetime import datetime

app = Flask(__name__)

# CONFIGURATION
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
HISTORY_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=0&single=true&output=csv"


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None']: return default
        return float(val)
    except:
        return default


def get_completed_games():
    """Pulls live results from ESPN API"""
    url = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    params = {'groups': '50', 'limit': '100'}
    try:
        res = requests.get(url, params=params, timeout=5).json()
        completed = []
        for event in res.get('events', []):
            status = event['status']['type']['description']
            if status == "Final":
                teams = event['competitions'][0]['competitors']
                completed.append({
                    "matchup": f"{teams[1]['team']['shortDisplayName']} @ {teams[0]['team']['shortDisplayName']}",
                    "score": f"{teams[1]['score']} - {teams[0]['score']}"
                })
        return completed
    except:
        return []


@app.route('/')
def index():
    # 1. Fetch Projections
    try:
        res = requests.get(f"{SHEET_URL}&cb={datetime.now().timestamp()}", timeout=5)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except:
        return "<h1>Sheet Data Error</h1>"

    all_games = []
    for _, row in df.iterrows():
        # Math Logic
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
            'away': row.get('Away Team', 'Away'), 'home': row.get('Home Team', 'Home'),
            'a_logo': row.get('Away Logo', ''), 'h_logo': row.get('Home Logo', ''),
            'a_p': round(a_proj, 1), 'h_p': round(h_proj, 1),
            'a_v': h_vegas * -1, 'h_v': h_vegas,
            'edge': abs(round(raw_edge, 1)), 'status': status,
            'pick': row.get('Away Team') if status == "AWAY" else row.get(
                'Home Team') if status == "HOME" else "No Value"
        })

    # 2. Sort Parlay (Top 3 Edge)
    parlay = sorted([g for g in all_games if g['status'] != 'NONE'], key=lambda x: x['edge'], reverse=True)[:3]

    # 3. Get ESPN Scores
    scores = get_completed_games()

    return render_template('index.html', games=all_games, parlay=parlay, scores=scores)


if __name__ == '__main__':
    app.run(debug=True)