import os, io, requests, pandas as pd, gspread, json, time
from flask import Flask, render_template
from datetime import datetime
import pytz
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"


def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds_json = os.environ.get('GOOGLE_CREDS')
        if not creds_json:
            print("ERROR: GOOGLE_CREDS variable is missing")
            return None
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"AUTH ERROR: {e}")
        return None


def normalize(name):
    if not name or pd.isna(name): return ""
    return str(name).upper().replace("STATE", "").replace("ST", "").replace("UNIVERSITY", "").replace("UNIV",
                                                                                                      "").strip()


def clean_val(val, default=0.0):
    try:
        v = str(val).replace('$', '').replace('%', '').strip()
        return float(v) if v not in ['---', '', 'nan', 'None'] else default
    except:
        return default


@app.route('/')
def index():
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    today_m_d = f"{now_tz.month}/{now_tz.day}"

    client = get_gspread_client()
    if not client:
        return "<h1>Configuration Error: Check Railway Variables</h1>"

    all_games = []
    wins, losses, pct = 0, 0, 0.0

    try:
        sheet = client.open_by_key(SHEET_ID)

        # --- NEW CLEANER DATA FETCH ---
        # Get all values as a list of lists instead of a dictionary
        raw_rows = sheet.get_worksheet(0).get_all_values()
        if not raw_rows:
            return "<h1>Sheet is Empty</h1>"

        # First row is headers. Clean them and handle duplicates/blanks.
        headers = [str(h).strip() for h in raw_rows[0]]

        # Convert to DataFrame, then drop any columns that have no name
        df = pd.DataFrame(raw_rows[1:], columns=headers)
        df = df.loc[:, df.columns != '']  # This removes the duplicate blank columns!

        # --- ARCHIVE STATS ---
        try:
            archive_data = sheet.worksheet("Archive").get_all_records()
            adf = pd.DataFrame(archive_data)
            wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
            losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
            pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
        except:
            pass

        for _, row in df.iterrows():
            g_time = str(row.get('Game Time', ''))
            if today_m_d not in g_time:
                continue

            a = str(row.get('Away Team', 'Unknown'))
            h = str(row.get('Home Team', 'Unknown'))

            h_spr = clean_val(row.get('FD Spread'))
            v_tot = clean_val(row.get('FD Total'))
            ra = clean_val(row.get('Rank Away'))
            rh = clean_val(row.get('Rank Home'))

            all_games.append({
                'Matchup': f"{a} @ {h}",
                'status': g_time.split(',')[-1] if ',' in g_time else g_time,
                'Pick': a.upper() if h_spr < -5 else h.upper(),
                'Pick_Spread': h_spr,
                'Edge': round(abs(ra - rh) * 0.05, 1),
                'OU_Status': "GOOD BET" if v_tot > 140 else "FADE",
                'OU_Pick': f"O {v_tot}",
                'OU_Tip': "Line suggests high tempo.",
                'Proj_Away': 75, 'Proj_Home': 72, 'Proj_Total': 147,
                'Final_Score': "0-0",
                'a_rank': int(ra), 'h_rank': int(rh),
                'a_logo': '', 'h_logo': ''
            })

    except Exception as e:
        return f"<h1>System Error: {e}</h1>"

    return render_template('index.html',
                           games=all_games,
                           stats={"W": wins, "L": losses, "PCT": pct},
                           seo={"title": "Edge Engine Pro"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))