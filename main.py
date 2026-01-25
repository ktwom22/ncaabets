import os, io, requests, gspread, pandas as pd
from flask import Flask, render_template
from datetime import datetime
from google.oauth2 import service_account

app = Flask(__name__)

# --- CONFIG ---
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
CREDS_FILE = 'creds.json'

# --- AUTH ---
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
try:
    creds = service_account.Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
    G_CLIENT = gspread.authorize(creds)
except Exception as e:
    print(f"AUTH ERROR: {e}")
    G_CLIENT = None


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
    # 1. FETCH LIVE PICKS FROM GOOGLE SHEET
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
            'a_rank': int(ra), 'h_rank': int(rh), 'a_ppg': pa, 'a_ppga': pga, 'h_ppg': ph, 'h_ppga': pgh,
            'time': row.get('Game Time', 'TBD')
        })

    # 2. ESPN LIVE SCORES (With Logo Support & WIN/LOSS status)
    live_scores = []
    try:
        espn = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
            timeout=5).json()
        for event in espn.get('events', []):
            comp = event['competitions'][0]
            teams = comp['competitors']

            away_e = next(t for t in teams if t['homeAway'] == 'away')
            home_e = next(t for t in teams if t['homeAway'] == 'home')
            e_names = [away_e['team']['displayName'].upper(), home_e['team']['displayName'].upper()]

            for p in all_games:
                if normalize(p['away']) in [normalize(n) for n in e_names] or normalize(p['home']) in [normalize(n) for
                                                                                                       n in e_names]:
                    status = event['status']['type']['description']
                    res_label = ""
                    if "Final" in status:
                        winner = next(t for t in teams if t.get('winner') == True)
                        res_label = "WIN" if normalize(p['pick']) in normalize(
                            winner['team']['displayName']) else "LOSS"

                    live_scores.append({
                        "id": str(event['id']),
                        "status": status,
                        "is_final": "Final" in status,
                        "score": f"{away_e['score']} - {home_e['score']}",
                        "our_result": res_label,
                        "away_name": away_e['team']['displayName'],
                        "away_logo": away_e['team']['logo'],
                        "home_name": home_e['team']['displayName'],
                        "home_logo": home_e['team']['logo'],
                        "my_pick": p['pick']
                    })
    except:
        pass

    # 3. ARCHIVE LOGIC (RE-WRITTEN FOR CORRECT STATS)
    wins, losses, pct = 0, 0, 0
    if G_CLIENT:
        try:
            workbook = G_CLIENT.open_by_key(SHEET_ID)
            try:
                archive_sheet = workbook.worksheet("Archive")
            except:
                archive_sheet = workbook.get_worksheet(0)

            # Sync new results to sheet
            existing_ids = [str(i).strip() for i in archive_sheet.col_values(16)]
            for s in [x for x in live_scores if x['is_final']]:
                if str(s['id']) not in existing_ids:
                    # Find matching parent pick data
                    match = next((g for g in all_games if
                                  normalize(g['away']) == normalize(s['away_name']) or normalize(
                                      g['home']) == normalize(s['home_name'])), None)
                    if match:
                        archive_sheet.append_row([
                            datetime.now().strftime('%m/%d/%Y'), f"{match['away']} @ {match['home']}",
                            s['our_result'], s['score'], match['pick'], match['pick_spr'],
                            match['a_p'], match['h_p'], match['edge'], match['a_ppg'], match['a_ppga'],
                            match['h_ppg'], match['h_ppga'], match['a_rank'], match['h_rank'], str(s['id'])
                        ])

            # Recalculate Stats from Column C (Result)
            all_rows = archive_sheet.get_all_values()
            if len(all_rows) > 1:
                headers = [h.strip().upper() for h in all_rows[0]]
                res_idx = headers.index("RESULT") if "RESULT" in headers else 2

                for row in all_rows[1:]:
                    if len(row) > res_idx:
                        val = str(row[res_idx]).strip().upper()
                        if "WIN" in val:
                            wins += 1
                        elif "LOSS" in val:
                            losses += 1

                total = wins + losses
                pct = round((wins / total * 100), 1) if total > 0 else 0
        except Exception as e:
            print(f"Archive Error: {e}")

    return render_template('index.html', games=all_games, live_scores=live_scores,
                           stats={"W": wins, "L": losses, "PCT": pct})


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)