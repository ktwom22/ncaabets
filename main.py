import os, io, requests, pandas as pd, gspread, json, time
from flask import Flask, render_template
from datetime import datetime
import pytz # Make sure 'pytz' is in your requirements.txt
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
RAW_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{RAW_ID}/gviz/tq?tqx=out:csv"
SHEET_ID = RAW_ID

# --- GOOGLE API SETUP ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_json = os.environ.get('GOOGLE_CREDS')
    if creds_json:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json), scope)
        archive_sheet = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Archive")
    else: archive_sheet = None
except: archive_sheet = None

# --- HELPERS ---
def clean_id(val):
    try: return str(int(float(val))) if not pd.isna(val) else "0"
    except: return str(val).strip()

def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "PALADINS", "MOCS", "TERRIERS"]:
        name = name.replace(r, "")
    return name.strip()

def clean_val(val, default=0.0):
    try: return float(str(val).replace('$', '').strip())
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
    # --- CRITICAL RAILWAY FIX: FORCE EST TIMEZONE ---
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    today_str = f"{now_tz.month}/{now_tz.day}" # Force "1/29" based on EST
    today_full = now_tz.strftime("%m/%d/%Y")

    # 1. ARCHIVE STATS
    wins, losses, pct, archive_ids, adf = 0, 0, 0.0, [], pd.DataFrame()
    if archive_sheet:
        try:
            adf = pd.DataFrame(archive_sheet.get_all_records())
            wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
            losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
            pct = round((wins/(wins+losses))*100, 1) if (wins+losses)>0 else 0.0
            archive_ids = [clean_id(x) for x in adf['ESPN_ID'].tolist()]
        except: pass

    # 2. DATA LOAD
    live_map = get_live_data()
    try:
        # Use headers to prevent Railway's IP from being flagged as a bot
        response = requests.get(SHEET_URL, headers={'User-Agent': 'Mozilla/5.0'})
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = [str(c).strip() for c in df.columns]
    except: df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_str not in g_time: continue

        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        ld = live_map.get(normalize(a), {"id": "0", "status": g_time, "state": "pre", "s_away": 0, "s_home": 0})
        espn_id = clean_id(ld.get('id'))

        # MATH & PROJECTIONS
        h_spr = clean_val(row.get('FD Spread'))
        v_tot = clean_val(row.get('FD Total'))
        ra, rh = clean_val(row.get('Rank Away')), clean_val(row.get('Rank Home'))
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        our_margin = -3.8 - max(min((ra - rh) * 0.18, 28), -28)
        tp = (pa + pgh + ph + pga) / 2
        hp, ap = (tp/2) + (abs(our_margin)/2), (tp/2) - (abs(our_margin)/2)
        if our_margin > 0: hp, ap = ap, hp

        diff_ou = abs((ap + hp) - v_tot)
        ou_dir = "OVER" if (ap + hp) > v_tot else "UNDER"
        if v_tot == 0: conf, tip = "N/A", "Waiting for line..."
        elif diff_ou > 5.0: conf, tip = "MUST BET", "High value mismatch."
        elif diff_ou > 2.5: conf, tip = "GOOD BET", "Solid edge."
        else: conf, tip = "FADE", "Vegas is spot on."

        is_locked = espn_id in archive_ids
        if is_locked and not adf.empty:
            try:
                l_row = adf[adf['ESPN_ID'].apply(clean_id) == espn_id].iloc[-1]
                pick, p_spr = l_row['Pick'], l_row['Pick_Spread']
            except: pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)
        else:
            pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)

        all_games.append({
            'Matchup': f"{a} @ {h}", 'status': ld['status'], 'is_live': ld['state'] == 'in',
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr, 'Edge': round(min(abs(h_spr - our_margin), 15.0), 1),
            'OU_Status': conf, 'OU_Pick': f"{ou_dir} {v_tot}", 'OU_Tip': tip,
            'Proj_Away': round(ap, 1), 'Proj_Home': round(hp, 1), 'Proj_Total': round(ap+hp, 1),
            'Final_Score': f"{ld['s_away']}-{ld['s_home']}", 'is_locked': is_locked, 'ESPN_ID': espn_id,
            'a_rank': int(ra), 'h_rank': int(rh), 'a_logo': ld.get('a_logo'), 'h_logo': ld.get('h_logo')
        })

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct}, seo={"title":"Edge Engine Pro"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))