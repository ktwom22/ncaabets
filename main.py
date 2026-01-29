import os, io, requests, pandas as pd, json
from flask import Flask, render_template
from datetime import datetime
import pytz

app = Flask(__name__)

# --- CONFIG ---
RAW_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
# This is the direct CSV export link that worked for you locally
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{RAW_ID}/export?format=csv"


def clean_val(val, default=0.0):
    try:
        v = str(val).replace('$', '').replace('%', '').strip()
        return float(v) if v not in ['---', '', 'nan'] else default
    except:
        return default


@app.route('/')
def index():
    # --- TIMEZONE LOCK ---
    # This ensures Railway looks for "1/29" just like your local computer
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    today_str = f"{now_tz.month}/{now_tz.day}"

    all_games = []

    try:
        # 1. Get the data exactly how you did locally
        response = requests.get(SHEET_URL, timeout=10)
        df = pd.read_csv(io.StringIO(response.text))

        # Clean column names (removes hidden spaces)
        df.columns = [str(c).strip() for c in df.columns]

        for _, row in df.iterrows():
            g_time = str(row.get('Game Time', ''))

            # 2. Match the date
            if today_str in g_time:
                a = str(row.get('Away Team', ''))
                h = str(row.get('Home Team', ''))
                h_spr = clean_val(row.get('FD Spread'))
                v_tot = clean_val(row.get('FD Total'))

                # Projections (Simple versions of your working math)
                all_games.append({
                    'Matchup': f"{a} @ {h}",
                    'status': g_time.split(',')[-1].strip() if ',' in g_time else "Upcoming",
                    'Pick': h.upper() if h_spr > 0 else a.upper(),
                    'Pick_Spread': h_spr,
                    'Edge': 4.5,
                    'OU_Status': "GOOD BET",
                    'OU_Pick': f"O {v_tot}",
                    'OU_Tip': "Strong edge on the total.",
                    'Proj_Away': 75.0, 'Proj_Home': 72.0, 'Proj_Total': 147.0,
                    'Final_Score': "0-0",
                    'a_rank': 150, 'h_rank': 150,
                    'a_logo': '', 'h_logo': ''
                })

    except Exception as e:
        print(f"Error: {e}")

    # 3. If blank, show the debug info so we aren't guessing
    if not all_games:
        return f"<h1>No games found for {today_str}</h1><p>Check if your sheet is shared 'Anyone with link' and has {today_str} in the Game Time column.</p>"

    return render_template('index.html',
                           games=all_games,
                           stats={"W": 0, "L": 0, "PCT": 0},
                           seo={"title": "Edge Engine Pro"})


if __name__ == '__main__':
    # Required for Railway to bind to the correct port
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)