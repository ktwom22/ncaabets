import os, io, requests, gspread, json, pandas as pd
from flask import Flask, render_template
from datetime import datetime
from google.oauth2 import service_account

app = Flask(__name__)

# --- CONFIG ---
# Your Published CSV for reading (Fast & No Quotas)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"
# Your Sheet ID for writing (From the URL of your Google Sheet)
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"

# --- MODERN SECURE AUTH ---
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_json = os.getenv("GOOGLE_CREDS_JSON")  # Looks for Railway/Render secret

try:
    if creds_json:
        # For Deployment (reads from Environment Variable)
        info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPE)
    else:
        # For Local Development (reads from file)
        creds = service_account.Credentials.from_service_account_file('creds.json', scopes=SCOPE)
    G_CLIENT = gspread.authorize(creds)
except Exception as e:
    print(f"AUTH ERROR: {e}")
    G_CLIENT = None


# --- HELPERS ---
def normalize(name):
    if not name: return ""
    return str(name).upper().replace(".", "").replace("'", "").strip()


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None', 'nan']: return default
        return float(val)
    except:
        return default


@app.route('/')
def index():
    # 1. FETCH LIVE PICKS DATA
    try:
        res = requests.get(f"{SHEET_CSV_URL}&cb={datetime.now().timestamp()}", timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except Exception as e:
        return f"<h1>Sync Error: {e}</h1>"

    all_games = []
    for _, row in df.iterrows():
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        ap = 71.0 + ((182 - ra) / 18.0) + (((pa - 72) + (72 - pgh)) * 0.4)
        hp = 71.0 + ((182 - rh) / 18.0) + (((ph - 72) + (72 - pga)) * 0.4) + 3.2
        model_line = round(ap - hp, 1)

        edge_to_away = model_line - (-h_spr)
        pick, pick_spr, edge = (a, -h_spr, abs(edge_to_away)) if edge_to_away > 0 else (h, h_spr, abs(edge_to_away))

        all_games.append({
            'away': a, 'home': h, 'a_p': round(ap, 1), 'h_p': round(hp, 1),
            'edge': round(edge, 1), 'pick': pick.upper(), 'pick_spr': pick_spr,
            'instruction': f"TAKE {pick} {'+' if pick_spr > 0 else ''}{pick_spr}",
            'conf': "elite" if edge >= 6 else "solid" if edge >= 3 else "low",
            'a_logo': str(row.get('Away Logo', '')), 'h_logo': str(row.get('Home Logo', '')),
            'time': row.get('Game Time', 'TBD')
        })

    # 2. ESPN LIVE RESULTS & AUTO-ARCHIVE
    live_scores = []
    try:
        espn = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
            timeout=5).json()
        for event in espn.get('events', []):
            comp = event['competitions'][0]
            teams = comp['competitors']
            names = [t['team']['displayName'].upper() for t in teams]
            for p in all_games:
                if normalize(p['away']) in [normalize(n) for n in names] or normalize(p['home']) in [normalize(n) for n
                                                                                                     in names]:
                    status = event['status']['type']['description']
                    away = next(t for t in teams if t['homeAway'] == 'away')
                    home = next(t for t in teams if t['homeAway'] == 'home')
                    res = ""
                    if "Final" in status:
                        win_obj = teams[0] if teams[0].get('winner') else teams[1]
                        res = "WIN" if normalize(p['pick']) in normalize(win_obj['team']['displayName']) else "LOSS"

                    live_scores.append({
                        "id": str(event['id']), "status": status, "is_final": "Final" in status,
                        "score": f"{away['score']}-{home['score']}", "our_result": res, "parent": p
                    })
    except:
        pass

    # 3. CALCULATE STATS FROM ARCHIVE TAB
    wins, losses, pct = 0, 0, 0
    if G_CLIENT:
        try:
            workbook = G_CLIENT.open_by_key(SHEET_ID)
            archive_sheet = workbook.worksheet("Archive")

            # Write new results
            existing_ids = archive_sheet.col_values(16)  # Assume Col P is ESPN ID
            for s in [x for x in live_scores if x['is_final']]:
                if s['id'] not in existing_ids:
                    p = s['parent']
                    archive_sheet.append_row([
                        datetime.now().strftime('%Y-%m-%d'), f"{p['away']} @ {p['home']}",
                        s['our_result'], s['score'], p['pick'], p['pick_spr'],
                        p['a_p'], p['h_p'], p['edge'], "", "", "", "", "", "", s['id']
                    ])

            # Read stats
            arc_data = archive_sheet.get_all_records()
            if arc_data:
                arc_df = pd.DataFrame(arc_data)
                arc_df.columns = arc_df.columns.str.strip()
                if 'Result' in arc_df.columns:
                    results = arc_df['Result'].astype(str).str.strip().str.upper()
                    wins = (results == 'WIN').sum()
                    losses = (results == 'LOSS').sum()
                    total = wins + losses
                    pct = round((wins / total * 100), 1) if total > 0 else 0
        except Exception as e:
            print(f"Archive Error: {e}")

    return render_template('index.html', games=all_games, live_scores=live_scores,
                           stats={"W": wins, "L": losses, "PCT": pct})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)