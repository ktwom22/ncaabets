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
        if not creds_json: return None
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"AUTH ERROR: {e}")
        return None


def clean_val(val, default=0.0):
    try:
        v = str(val).replace('$', '').replace('%', '').strip()
        return float(v) if v not in ['---', '', 'nan', 'None'] else default
    except:
        return default


@app.route('/')
def index():
    # 1. TIMEZONE SETUP
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    # This looks for "1/29"
    today_str = f"{now_tz.month}/{now_tz.day}"

    client = get_gspread_client()
    if not client:
        return "<h1>Check GOOGLE_CREDS in Railway</h1>"

    all_games = []
    wins, losses, pct = 0, 0, 0.0

    try:
        sheet = client.open_by_key(SHEET_ID)

        # --- ROBUST DATA FETCH ---
        # Get everything as raw values to avoid header crashes
        raw_values = sheet.get_worksheet(0).get_all_values()
        if not raw_values:
            return "<h1>Sheet is empty</h1>"

        # Separate headers and data
        header_row = [str(h).strip() for h in raw_values[0]]
        data_rows = raw_values[1:]

        df = pd.DataFrame(data_rows, columns=header_row)

        # Remove any columns that have empty headers to prevent the 'duplicate' error
        df = df.loc[:, df.columns != '']

        # --- STATS FETCH ---
        try:
            archive_sheet = sheet.worksheet("Archive")
            adf = pd.DataFrame(archive_sheet.get_all_records())
            wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
            losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
            pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
        except:
            pass

        # --- GAME PROCESSING ---
        for _, row in df.iterrows():
            # Get the time/date string
            g_time = str(row.get('Game Time', ''))

            # If the row doesn't have "1/29", skip it
            if today_str not in g_time:
                continue

            a = str(row.get('Away Team', 'Away'))
            h = str(row.get('Home Team', 'Home'))
            h_spr = clean_val(row.get('FD Spread'))
            v_tot = clean_val(row.get('FD Total'))
            ra = clean_val(row.get('Rank Away'))
            rh = clean_val(row.get('Rank Home'))

            # Basic logic to fill the cards
            all_games.append({
                'Matchup': f"{a} @ {h}",
                'status': g_time.split(',')[-1].strip() if ',' in g_time else "Scheduled",
                'Pick': a.upper() if h_spr < -3 else h.upper(),
                'Pick_Spread': h_spr,
                'Edge': round(abs(ra - rh) * 0.04, 1),
                'OU_Status': "MUST BET" if v_tot > 150 else "GOOD BET" if v_tot > 135 else "FADE",
                'OU_Pick': f"O {v_tot}",
                'OU_Tip': "High confidence in tempo mismatch.",
                'Proj_Away': 78.5, 'Proj_Home': 74.2, 'Proj_Total': 152.7,
                'Final_Score': "0-0",
                'a_rank': int(ra), 'h_rank': int(rh),
                'a_logo': '', 'h_logo': ''
            })

    except Exception as e:
        return f"<h1>System Error: {str(e)}</h1>"

    # If NO games found, show a message instead of just a blank screen
    if not all_games:
        return f"<h1>No games found for {today_str}. Check 'Game Time' column.</h1>"

    return render_template('index.html',
                           games=all_games,
                           stats={"W": wins, "L": losses, "PCT": pct},
                           seo={"title": "Edge Engine Pro"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))