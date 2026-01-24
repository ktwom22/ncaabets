from flask import Flask, render_template
import pandas as pd
import requests
import io

app = Flask(__name__)

SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None']:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def get_data():
    try:
        url = f"{SHEET_URL}&cb={pd.Timestamp.now().timestamp()}"
        res = requests.get(url, timeout=5)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        return pd.DataFrame()


@app.route('/')
def index():
    df = get_data()
    if df.empty:
        return "<h1>Refreshing Sheet Data... Please wait.</h1>"

    games = []
    for _, row in df.iterrows():
        # Clean inputs
        r_away = clean_val(row.get('Rank Away'), 182)
        r_home = clean_val(row.get('Rank Home'), 182)
        ppg_a = clean_val(row.get('PPG Away'), 70)
        ppg_h = clean_val(row.get('PPG Home'), 70)
        ppga_a = clean_val(row.get('PPGA Away'), 70)
        ppga_h = clean_val(row.get('PPGA Home'), 70)
        h_vegas = clean_val(row.get('FD Spread'), 0.0)
        v_total = clean_val(row.get('FD Total'), 140.0)

        # Math Engine
        base = 71.0
        a_proj = base + ((182 - r_away) / 18.0) + (((ppg_a - 72) + (72 - ppga_h)) * 0.4)
        h_proj = base + ((182 - r_home) / 18.0) + (((ppg_h - 72) + (72 - ppga_a)) * 0.4) + 3.2

        m_spread = round(a_proj - h_proj, 1)
        # Edge = How much the Model disagrees with Vegas
        # If Model says -5 and Vegas says -10, Edge is 5 points for the Underdog.
        raw_edge = m_spread - h_vegas

        if raw_edge > 1.5:
            pick_team = row.get('Away Team', 'Away')
            status = "AWAY"
        elif raw_edge < -1.5:
            pick_team = row.get('Home Team', 'Home')
            status = "HOME"
        else:
            pick_team = "No Value"
            status = "NONE"

        games.append({
            'away_team': row.get('Away Team', 'Away'),
            'home_team': row.get('Home Team', 'Home'),
            'away_logo': row.get('Away Logo', ''),
            'home_logo': row.get('Home Logo', ''),
            'a_proj': round(a_proj, 1),
            'h_proj': round(h_proj, 1),
            'a_odds': h_vegas * -1,
            'h_odds': h_vegas,
            'm_spread': m_spread,
            'total': round(a_proj + h_proj, 1),
            'v_total': v_total,
            'pick_team': pick_team,
            'status': status,
            'edge': abs(round(raw_edge, 1))
        })

    return render_template('index.html', games=games)


if __name__ == '__main__':
    app.run(debug=True)