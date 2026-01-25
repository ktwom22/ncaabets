import os, io, requests, gspread, pandas as pd
from flask import Flask, render_template
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
CREDS_FILE = 'creds.json'

# --- GOOGLE AUTH (Loading from File to Fix Padding Error) ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

try:
    CREDS = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, SCOPE)
    G_CLIENT = gspread.authorize(CREDS)
except Exception as e:
    print(f"CRITICAL AUTH ERROR: {e}")
    # We don't stop the app; we let it try to load the dashboard without the archive
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
    # 1. FETCH CURRENT DATA
    try:
        res = requests.get(f"{SHEET_CSV_URL}&cb={datetime.now().timestamp()}", timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except Exception as e:
        return f"<h1>Sync Error: {e}</h1>"

    all_games = []
    # 2. MODEL LOGIC
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
            'a_rank': int(ra), 'h_rank': int(rh), 'a_ppg': pa, 'a_ppga': pga, 'h_ppg': ph, 'h_ppga': pgh,
            'time': row.get('Game Time', 'TBD')
        })

    # 3. ESPN LIVE RESULTS
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
                        "score": f"{away['score']}-{home['score']}", "our_result": res, "parent": p,
                        "away_name": away['team']['displayName'].upper(),
                        "home_name": home['team']['displayName'].upper()
                    })
    except:
        pass

    # 4. ARCHIVE SYNC
    wins, losses, pct = 0, 0, 0
    if G_CLIENT:
        try:
            workbook = G_CLIENT.open_by_key(SHEET_ID)
            archive_sheet = workbook.worksheet("Archive")
            existing_ids = archive_sheet.col_values(16)

            for s in [x for x in live_scores if x['is_final']]:
                if str(s['id']) not in existing_ids:
                    p = s['parent']
                    archive_sheet.append_row([
                        datetime.now().strftime('%Y-%m-%d'), f"{p['away']} @ {p['home']}",
                        s['our_result'], s['score'], p['pick'], p['pick_spr'],
                        p['a_p'], p['h_p'], p['edge'], p['a_ppg'], p['a_ppga'],
                        p['h_ppg'], p['h_ppga'], p['a_rank'], p['h_rank'], s['id']
                    ])

            # 5. FETCH STATS
            arc_data = archive_sheet.get_all_records()
            if arc_data:
                arc_df = pd.DataFrame(arc_data)
                wins = len(arc_df[arc_df['Result'] == 'WIN'])
                losses = len(arc_df[arc_df['Result'] == 'LOSS'])
                pct = round((wins / (wins + losses) * 100), 1) if (wins + losses) > 0 else 0
        except Exception as e:
            print(f"Archive Error: {e}")

    return render_template('index.html', games=all_games, live_scores=live_scores,
                           stats={"W": wins, "L": losses, "PCT": pct})


if __name__ == '__main__':
    app.run(debug=True)