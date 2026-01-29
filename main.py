import os, io, requests, pandas as pd, gspread, json, time
from flask import Flask, render_template
from datetime import datetime
import pytz
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG (Updated with reliable export link) ---
RAW_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{RAW_ID}/gviz/tq?tqx=out:csv"
SHEET_ID = RAW_ID

# --- GOOGLE API SETUP ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_json = os.environ.get('GOOGLE_CREDS')
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope) if creds_json else ServiceAccountCredentials.from_json_keyfile_name('creds.json', scope)
    archive_sheet = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Archive")
except Exception as e:
    print(f"AUTH ERROR: {e}")
    archive_sheet = None

def clean_id(val):
    try: return str(int(float(val))) if not pd.isna(val) and str(val).strip() != "" else "0"
    except: return str(val).strip()

def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "LADY"]:
        name = name.replace(r, "")
    return name.strip()

def clean_val(val, default=0.0):
    try:
        v = str(val).replace('$', '').replace('%', '').strip()
        return float(v) if v not in ['---', '', 'nan'] else default
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
                "id": str(event['id']), "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'], "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score'])), "a_logo": t_away['team'].get('logo', ''), "h_logo": t_home['team'].get('logo', '')
            }
    except: pass
    return live_map

@app.route('/')
def index():
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    # Target "1/29" and "01/29"
    targets = [f"{now_tz.month}/{now_tz.day}", f"{now_tz.strftime('%m/%d')}"]
    today_full_str = now_tz.strftime("%m/%d/%Y")

    # 1. ARCHIVE STATS
    wins, losses, pct, archive_ids, adf = 0, 0, 0.0, [], pd.DataFrame()
    if archive_sheet:
        try:
            recs = archive_sheet.get_all_records()
            if recs:
                adf = pd.DataFrame(recs)
                wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
                losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
                pct = round((wins/(wins+losses))*100, 1) if (wins+losses)>0 else 0.0
                archive_ids = [clean_id(x) for x in adf['ESPN_ID'].tolist()]
        except: pass

    # 2. DATA LOAD
    live_map = get_live_data()
    try:
        df = pd.read_csv(SHEET_URL)
        df.columns = [str(c).strip() for c in df.columns]
        print(f"COLUMNS FOUND: {list(df.columns)}") # Check your console for this!
    except Exception as e:
        print(f"CSV LOAD ERROR: {e}")
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        # Flexible Date Match
        if not any(t in g_time for t in targets): continue

        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        ld = live_map.get(normalize(a), {"id": "0", "status": g_time, "state": "pre", "s_away": 0, "s_home": 0})
        espn_id = clean_id(ld.get('id'))

        # PROJECTIONS & MATH
        h_spr, v_tot = clean_val(row.get('FD Spread')), clean_val(row.get('Total'))
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, pga, ph, pgh = clean_val(row.get('PPG Away')), clean_val(row.get('PPGA Away')), clean_val(row.get('PPG Home')), clean_val(row.get('PPGA Home'))

        our_margin = -3.8 - max(min((ra - rh) * 0.18, 28), -28)
        tp = (pa + pgh + ph + pga) / 2
        hp, ap = (tp/2) + (abs(our_margin)/2), (tp/2) - (abs(our_margin)/2)
        if our_margin > 0: hp, ap = ap, hp

        # O/U SCALE
        diff_ou = abs((ap + hp) - v_tot)
        ou_dir = "OVER" if (ap + hp) > v_tot else "UNDER"
        if v_tot == 0: conf, tip = "N/A", "Waiting for line..."
        elif diff_ou > 5.0: conf, tip = "MUST BET", "High value mismatch."
        elif diff_ou > 2.5: conf, tip = "GOOD BET", "Solid edge."
        else: conf, tip = "FADE", "Vegas is spot on."

        # PICK & LOCKING
        is_locked = espn_id in archive_ids
        if is_locked and not adf.empty:
            try:
                l_row = adf[adf['ESPN_ID'].apply(clean_id) == espn_id].iloc[-1]
                pick, p_spr = l_row['Pick'], l_row['Pick_Spread']
            except: pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)
        else:
            pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)

        # BACKFILL
        if archive_sheet and espn_id != "0" and not is_locked:
            if ld['state'] in ['in', 'post']:
                res = "LIVE"
                if ld['state'] == 'post':
                    diff = ld['s_home'] - ld['s_away']
                    res = "WIN" if ((diff + h_spr > 0) if pick == h else ((-diff) - h_spr > 0)) else "LOSS"
                archive_sheet.append_row([today_full_str, f"{a} @ {h}", res, f"{ld['s_away']}-{ld['s_home']}", str(pick), p_spr, 0, 0, 0, pa, pga, ph, pgh, int(ra), int(rh), espn_id])
                is_locked = True
                time.sleep(1)

        all_games.append({
            'Matchup': f"{a} @ {h}", 'status': ld['status'], 'is_live': ld['state'] == 'in',
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr, 'Edge': round(min(abs(h_spr - our_margin), 15.0), 1),
            'OU_Status': conf, 'OU_Pick': f"{ou_dir} {v_tot}", 'OU_Tip': tip,
            'Proj_Total': round(ap+hp, 1), 'a_logo': ld.get('a_logo') or str(row.get('Away Logo', '')),
            'h_logo': ld.get('h_logo') or str(row.get('Home Logo', ''))
        })

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)