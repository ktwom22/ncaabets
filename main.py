import os, io, requests, pandas as pd, gspread, json
from flask import Flask, render_template, jsonify
from datetime import datetime
import pytz
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"

# --- GOOGLE API SETUP ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_json = os.environ.get('GOOGLE_CREDS')
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name('creds.json', scope)
    gc = gspread.authorize(creds)
    doc = gc.open_by_key(SHEET_ID)
    archive_sheet = doc.worksheet("Archive")
except Exception as e:
    print(f"Auth/Init Error: {e}")
    archive_sheet = None

def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "LADY"]:
        name = name.replace(r, "")
    return name.strip()

def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None', 'nan']: return default
        return float(val)
    except: return default

def get_live_data():
    live_map = {}
    try:
        res = requests.get("http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=150", timeout=5).json()
        for event in res.get('events', []):
            comp = event['competitions'][0]
            t_away = next(t for t in comp['competitors'] if t['homeAway'] == 'away')
            t_home = next(t for t in comp['competitors'] if t['homeAway'] == 'home')
            live_map[normalize(t_away['team']['displayName'])] = {
                "id": str(event['id']),
                "link": event['links'][0]['href'],
                "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'],
                "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score'])),
                "away_logo": t_away['team'].get('logo', ''),
                "home_logo": t_home['team'].get('logo', '')
            }
    except: pass
    return live_map

@app.route('/')
def index():
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    # Target formats like "1/28"
    today_target = f"{now_tz.month}/{now_tz.day}"
    today_full_str = now_tz.strftime("%m/%d/%Y")

    # 1. READ ARCHIVE (Stats Connection)
    wins, losses, pct, archive_ids = 0, 0, 0.0, []
    adf = pd.DataFrame()
    if archive_sheet:
        try:
            records = archive_sheet.get_all_records()
            if records:
                adf = pd.DataFrame(records)
                wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
                losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
                pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
                if 'ESPN_ID' in adf.columns:
                    archive_ids = adf['ESPN_ID'].astype(str).tolist()
        except: pass

    # 2. FETCH DATA
    live_map = get_live_data()
    try:
        res = requests.get(SHEET_URL, timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = [str(c).strip() for c in df.columns]
    except: df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        # --- FLEXIBLE DATE MATCH ---
        # Checks if "1/28" is inside "1/28, 6:30 PM"
        game_time_str = str(row.get('Game Time', ''))
        if today_target not in game_time_str:
            continue

        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        ld = live_map.get(normalize(a), {"id": "0", "status": game_time_str, "state": "pre", "s_away": 0, "s_home": 0})
        espn_id, state = str(ld.get('id')), ld.get('state')

        # Logic & Math
        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        # Archive/Lock Check
        is_locked = False
        if espn_id != "0" and espn_id in archive_ids:
            locked_row = adf[adf['ESPN_ID'].astype(str) == espn_id].iloc[-1]
            pick, p_spr = locked_row['Pick'], locked_row['Pick_Spread']
            is_locked = True
        else:
            rank_gap = (ra - rh) * 0.18
            our_margin = -3.8 - rank_gap
            pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)

        # Write to Archive
        if archive_sheet and espn_id != "0":
            try:
                if state == "in" and espn_id not in archive_ids:
                    archive_sheet.append_row([today_full_str, f"{a} @ {h}", "LIVE", "0-0", str(pick), p_spr, 0, 0, 0, pa, pga, ph, pgh, int(ra), int(rh), espn_id])
                    archive_ids.append(espn_id)
                elif state == "post" and espn_id not in archive_ids:
                    diff = ld['s_home'] - ld['s_away']
                    res_label = "WIN" if ((diff + h_spr > 0) if pick == h else ((-diff) - h_spr > 0)) else "LOSS"
                    archive_sheet.append_row([today_full_str, f"{a} @ {h}", res_label, f"{ld['s_away']}-{ld['s_home']}", str(pick), p_spr, 0, 0, 0, pa, pga, ph, pgh, int(ra), int(rh), espn_id])
                    archive_ids.append(espn_id)
            except: pass

        # Projection Math
        rank_gap = (ra - rh) * 0.18
        our_margin = -3.8 - rank_gap
        total_pts = (pa + pgh + ph + pga) / 2
        hp, ap = (total_pts / 2) + (abs(our_margin) / 2), (total_pts / 2) - (abs(our_margin) / 2)
        if our_margin > 0: hp, ap = ap, hp

        all_games.append({
            'Matchup': f"{a} @ {h}",
            'Result': (adf[adf['ESPN_ID'].astype(str) == espn_id]['Result'].values[-1] if is_locked and not adf.empty else ""),
            'Final_Score': f"{ld['s_away']}-{ld['s_home']}",
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr,
            'Proj_Away': round(ap, 1), 'Proj_Home': round(hp, 1),
            'Edge': round(abs(h_spr - our_margin), 1),
            'status': ld['status'], 'is_live': state == 'in', 'is_locked': is_locked,
            'a_logo': str(row.get('Away Logo', '')), 'h_logo': str(row.get('Home Logo', ''))
        })

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct}, seo={"title": "Edge Engine Pro"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)