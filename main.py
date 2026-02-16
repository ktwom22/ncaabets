import os, io, requests, json, threading, time, math, re
import pandas as pd
from flask import Flask, render_template, jsonify
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

# --- CONFIGURATION ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"
ESPN_API = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=100"

SPREADSHEET_ID = '1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SERVICE_ACCOUNT_FILE = 'credentials.json'

DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/default.png"


def get_sheets_service():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)


def clean_val(val):
    if pd.isna(val) or str(val).strip().lower() == "nan" or str(val).strip() == "": return 0.0
    try:
        cleaned = "".join(c for c in str(val) if c.isdigit() or c in '.-')
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0


def check_and_archive_game(eid, current_data):
    """Checks if a game is in the Archive; if not, logs it to lock the pick."""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range='Archive!A:J').execute()
        rows = result.get('values', [])
        for row in rows:
            if len(row) >= 10 and str(row[9]) == str(eid):
                return {
                    "Pick": row[4], "Spread": row[5], "Proj_A": float(row[6]),
                    "Proj_H": float(row[7]), "Edge": float(row[8]) if len(row) > 8 else 0.0
                }

        archive_row = [datetime.now().strftime("%m/%d/%Y"), current_data['matchup'], "LIVE", "TBD",
                       current_data['pick'], current_data['spread'], current_data['proj_a'], current_data['proj_h'],
                       current_data['edge'], str(eid)]

        service.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range='Archive!A:J',
                                               valueInputOption='USER_ENTERED',
                                               body={'values': [archive_row]}).execute()
        return None
    except Exception as e:
        print(f"Archive Error: {e}")
        return None


DATA_STORE = {"games": [], "stats": {"PCT": 0.0}, "last_updated": "Initializing..."}
session = requests.Session()


def fetch_and_sync():
    global DATA_STORE
    try:
        # 1. Fetch ESPN Data
        espn_res = session.get(ESPN_API, timeout=10).json()
        live_map = {str(e['id']): e for e in espn_res.get('events', [])}

        # 2. Fetch Google Sheet (Live Odds)
        df = pd.read_csv(io.StringIO(session.get(SHEET_URL).text))

        # 3. Fetch Win % from Archive CSV
        try:
            adf = pd.read_csv(io.StringIO(session.get(ARCHIVE_CSV_URL).text))
            wins = (adf['Result'].str.upper() == 'WIN').sum()
            losses = (adf['Result'].str.upper() == 'LOSS').sum()
            pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
        except:
            pct = 0.0

        temp_games = []
        for _, row in df.iterrows():
            try:
                away_t = str(row.get('Away Team', '')).strip()
                home_t = str(row.get('Home Team', '')).strip()
                if not away_t or away_t.lower() == "nan": continue

                eid = str(row.get('ESPN ID', '0')).strip()
                ld = live_map.get(eid, {})
                comp = ld.get('competitions', [{}])[0]
                competitors = comp.get('competitors', [])

                # Team Intelligence
                prob_away = 0.0
                try:
                    prob_away = comp.get('odds', [{}])[0].get('awayWinPercentage', 0.0)
                except:
                    pass

                intel = {
                    "broadcast": comp.get('broadcasts', [{}])[0].get('names', ['ESPN+'])[0],
                    "venue": comp.get('venue', {}).get('fullName', 'TBD Venue'),
                    "prob_away": prob_away,
                    "prob_home": round(100 - prob_away, 1) if prob_away > 0 else 0
                }

                # Status and Locking
                state = ld.get('status', {}).get('type', {}).get('state')
                is_started = state in ['in', 'post']
                is_locked = False

                # Stats Cleanup
                p_a, p_h = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
                pa_a, pa_h = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))
                l3_p_a, l3_p_h = clean_val(row.get('L3 PPG Away')), clean_val(row.get('L3 PPG Home'))
                l3_pa_a, l3_pa_h = clean_val(row.get('L3 PPGA Away')), clean_val(row.get('L3 PPGA Home'))
                r_a, r_h = clean_val(row.get('Rank Away')), clean_val(row.get('Rank Home'))
                h_spread = clean_val(row.get('FD Spread'))

                # AI Model Math
                proj_a = ((math.sqrt(max(0.1, p_a + l3_p_a)) * math.sqrt(max(0.1, pa_h + l3_pa_h))) / 2) + (
                            (r_h - r_a) * 0.05)
                proj_h = ((math.sqrt(max(0.1, p_h + l3_p_h)) * math.sqrt(max(0.1, pa_a + l3_pa_a))) / 2) - (
                            (r_h - r_a) * 0.05)
                our_line = proj_a - proj_h

                pick, spread_str = (home_t, f"{h_spread:+g}") if our_line < h_spread else (away_t, f"{-h_spread:+g}")
                edge = round(abs(our_line - h_spread), 1)

                # Locking: Retrieve snapshot if game is underway
                if is_started:
                    arch_data = {"matchup": f"{away_t} @ {home_t}", "pick": pick, "spread": spread_str,
                                 "proj_a": round(proj_a, 1), "proj_h": round(proj_h, 1), "edge": edge}
                    history = check_and_archive_game(eid, arch_data)
                    if history:
                        pick, spread_str, proj_a, proj_h, edge = history['Pick'], history['Spread'], history['Proj_A'], \
                                                                 history['Proj_H'], history['Edge']
                        is_locked = True

                # Scoreboard Logic
                score_display = "Scheduled"
                if is_started:
                    h_s = next((t.get('score') for t in competitors if t.get('homeAway') == 'home'), "0")
                    a_s = next((t.get('score') for t in competitors if t.get('homeAway') == 'away'), "0")
                    score_display = f"{a_s}-{h_s}"
                else:
                    score_display = ld.get('status', {}).get('type', {}).get('shortDetail', "Scheduled")

                # Logo Safety
                away_l = str(row.get('Away Logo', '')).strip()
                home_l = str(row.get('Home Logo', '')).strip()

                temp_games.append({
                    "slug": re.sub(r'[^a-z0-9]+', '-', f"{away_t}-{home_t}-{eid}".lower()),
                    "is_locked": is_locked,
                    "ESPN_ID": eid,
                    "intel": intel,
                    "Live_Score": score_display,
                    "status_state": state,
                    "Pick": pick,
                    "Pick_Spread": spread_str,
                    "Edge": edge,
                    "L_Logo": away_l if away_l and away_l.lower() != 'nan' else DEFAULT_LOGO,
                    "R_Logo": home_l if home_l and home_l.lower() != 'nan' else DEFAULT_LOGO,
                    "Left_Proj": round(proj_a, 1),
                    "Right_Proj": round(proj_h, 1),
                    "full_stats": {
                        "away_name": away_t, "home_name": home_t,
                        "ppg_a": p_a, "ppg_h": p_h,
                        "l3_ppg_a": l3_p_a, "l3_ppg_h": l3_p_h,
                        "ppga_a": pa_a, "ppga_h": pa_h,
                        "rank_a": int(r_a), "rank_h": int(r_h),
                        "total": clean_val(row.get('FD Total')),
                        "spread": h_spread
                    }
                })
            except Exception as e:
                print(f"Row Processing Error: {e}")
                continue

        DATA_STORE = {"games": temp_games, "stats": {"PCT": pct}, "last_updated": datetime.now().strftime("%I:%M %p")}
    except Exception as e:
        print(f"Global Fetch Error: {e}")


def refresh_loop():
    while True:
        time.sleep(60)
        fetch_and_sync()


# --- INITIALIZATION ---
fetch_and_sync()
threading.Thread(target=refresh_loop, daemon=True).start()


@app.route('/')
def index():
    return render_template('index.html',
                           games=DATA_STORE["games"],
                           stats=DATA_STORE["stats"],
                           last_updated=DATA_STORE["last_updated"])


@app.route('/matchup/<slug>')
def matchup_detail(slug):
    game = next((g for g in DATA_STORE["games"] if g['slug'] == slug), None)
    return render_template('matchup.html', g=game) if game else ("Not Found", 404)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)