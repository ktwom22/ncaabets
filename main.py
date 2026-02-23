import os, io, requests, json, threading, time, math, re, queue
import pandas as pd
from flask import Flask, render_template
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

# --- CONFIG ---
session = requests.Session()
session.headers.update({'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})

SPREADSHEET_ID = '1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM'
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid=1766494058"
ARCHIVE_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=csv&gid=1827029141"
ESPN_API = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=100"

SERVICE_ACCOUNT_FILE = 'credentials.json'
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/default.png"

archive_queue = queue.Queue()
EXISTING_ARCHIVE_IDS = set()

# Global cache to keep picks locked once a game starts
LOCKED_PICKS_CACHE = {}


def get_sheets_service():
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE,
                                                      scopes=['https://www.googleapis.com/auth/spreadsheets'])
        return build('sheets', 'v4', credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"❌ API AUTH ERROR: {e}")
        return None


def archive_worker():
    global EXISTING_ARCHIVE_IDS
    while True:
        d = archive_queue.get()
        if d is None: break
        try:
            eid_str = str(d['eid']).strip()
            if eid_str in EXISTING_ARCHIVE_IDS:
                archive_queue.task_done()
                continue
            service = get_sheets_service()
            if not service:
                archive_queue.task_done()
                continue

            row = [
                datetime.now().strftime("%m/%d/%Y"), f"{d['away_t']} @ {d['home_t']}", "LIVE", "TBD",
                d['pick'], d['spread_str'], d['proj_a'], d['proj_h'], d['edge'],
                d['p_a'], d['pa_a'], d['p_h'], d['pa_h'], d['rank_a'], d['rank_h'],
                eid_str, d['sos_a'], d['sos_h'], d['l3_p_a'], d['l3_p_h'],
                d['l3_pa_a'], d['l3_pa_h'], d['game_time']
            ]
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID, range='Archive!A1',
                valueInputOption='USER_ENTERED', body={'values': [row]}
            ).execute()
            EXISTING_ARCHIVE_IDS.add(eid_str)
            print(f"✅ ARCHIVED: {d['away_t']} vs {d['home_t']}")
        except Exception as e:
            print(f"❌ WORKER ERROR: {e}")
        finally:
            archive_queue.task_done()


threading.Thread(target=archive_worker, daemon=True).start()


def clean_val(val):
    if pd.isna(val) or str(val).strip().lower() in ["nan", ""]: return 0.0
    try:
        return float(re.sub(r'[^0-9.-]', '', str(val)))
    except:
        return 0.0


DATA_STORE = {"games": [], "last_updated": "Initializing..."}


def fetch_and_sync():
    global DATA_STORE, EXISTING_ARCHIVE_IDS, LOCKED_PICKS_CACHE
    try:
        e_res = session.get(ESPN_API, timeout=5).json()
        events = e_res.get('events', [])

        r_main = session.get(f"{SHEET_URL}&t={int(time.time())}", timeout=10)
        df = pd.read_csv(io.StringIO(r_main.text))
        df.columns = [str(c).strip() for c in df.columns]

        sheet_data = df.to_dict('records')
        temp_games = []

        for ev in events:
            eid = str(ev['id'])
            status_obj = ev.get('status', {})
            state = status_obj.get('type', {}).get('state', 'pre')  # 'pre', 'in', or 'post'
            score_detail = status_obj.get('type', {}).get('shortDetail', "Scheduled")

            comp = ev.get('competitions', [{}])[0]
            teams = comp.get('competitors', [])
            home_espn = next((t['team']['displayName'] for t in teams if t['homeAway'] == 'home'), "").strip()
            away_espn = next((t['team']['displayName'] for t in teams if t['homeAway'] == 'away'), "").strip()

            # Smart Matching Logic
            row = None
            for s_row in sheet_data:
                s_home = str(s_row.get('Home Team', '')).strip()
                if home_espn.lower() in s_home.lower() or s_home.lower() in home_espn.lower():
                    row = s_row
                    break

            if not row: continue

            # Basic Stats
            d = {
                'eid': eid, 'away_t': away_espn, 'home_t': home_espn,
                'sos_a': clean_val(row.get('SOS Away')), 'sos_h': clean_val(row.get('SOS Home')),
                'l3_pa_a': clean_val(row.get('L3 PPGA Away')), 'l3_pa_h': clean_val(row.get('L3 PPGA Home')),
                'p_a': clean_val(row.get('PPG Away')), 'pa_a': clean_val(row.get('PPGA Away')),
                'p_h': clean_val(row.get('PPG Home')), 'pa_h': clean_val(row.get('PPGA Home')),
                'l3_p_a': clean_val(row.get('L3 PPG Away')), 'l3_p_h': clean_val(row.get('L3 PPG Home')),
                'rank_a': clean_val(row.get('Rank Away')), 'rank_h': clean_val(row.get('Rank Home')),
                'spread_val': clean_val(row.get('FD Spread')), 'game_time': str(row.get('Game Time', 'TBD')),
                'logo_a': next((t['team']['logo'] for t in teams if t['homeAway'] == 'away'), DEFAULT_LOGO),
                'logo_h': next((t['team']['logo'] for t in teams if t['homeAway'] == 'home'), DEFAULT_LOGO)
            }

            # Check if we should use a LOCKED pick or calculate a new one
            is_locked = (state != 'pre')

            if is_locked and eid in LOCKED_PICKS_CACHE:
                # Use the pick we saved when the game started
                pick_data = LOCKED_PICKS_CACHE[eid]
            else:
                # Calculate Fresh Projections
                proj_a = (math.sqrt(max(0.1, d['p_a'] + d['l3_p_a'])) * math.sqrt(
                    max(0.1, d['pa_h'] + d['l3_pa_h'])) / 2) + ((d['rank_h'] - d['rank_a']) * 0.05)
                proj_h = (math.sqrt(max(0.1, d['p_h'] + d['l3_p_h'])) * math.sqrt(
                    max(0.1, d['pa_a'] + d['l3_pa_a'])) / 2) - ((d['rank_h'] - d['rank_a']) * 0.05)

                pick, spread_str = (d['home_t'], f"{d['spread_val']:+g}") if (proj_a - proj_h) < d['spread_val'] else (
                d['away_t'], f"{-d['spread_val']:+g}")
                edge = round(abs((proj_a - proj_h) - d['spread_val']), 1)

                pick_data = {
                    'pick': pick, 'spread_str': spread_str, 'edge': edge,
                    'proj_a': round(proj_a, 1), 'proj_h': round(proj_h, 1)
                }

                # If the game just started, save this pick to the cache permanently for this session
                if is_locked:
                    LOCKED_PICKS_CACHE[eid] = pick_data

            # Final Score / Archiving Logic
            if is_locked:
                scs = {t.get('homeAway'): t.get('score', '0') for t in teams}
                score_display = f"{scs.get('away', '0')}-{scs.get('home', '0')}"
                if eid not in EXISTING_ARCHIVE_IDS:
                    d.update(pick_data)
                    archive_queue.put(d)
            else:
                score_display = score_detail

            temp_games.append({
                "slug": f"game-{eid}", "Live_Score": score_display, "is_locked": is_locked,
                "Pick": pick_data['pick'], "Pick_Spread": pick_data['spread_str'], "Edge": pick_data['edge'],
                "L_Logo": d['logo_a'], "R_Logo": d['logo_h'],
                "Left_Proj": pick_data['proj_a'], "Right_Proj": pick_data['proj_h'],
                "Left_Team": d['away_t'], "Right_Team": d['home_t'], "full_stats": d
            })

        DATA_STORE = {"games": temp_games, "last_updated": datetime.now().strftime("%I:%M %p")}
        print(f"--- SYNCED {len(temp_games)} GAMES (ESPN LOCKING ACTIVE) ---")
    except Exception as e:
        print(f"❌ SYNC ERROR: {e}")


def refresh_loop():
    while True:
        time.sleep(30)
        fetch_and_sync()


fetch_and_sync()
threading.Thread(target=refresh_loop, daemon=True).start()


@app.route('/')
def index():
    return render_template('index.html', games=DATA_STORE["games"], last_updated=DATA_STORE["last_updated"])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)