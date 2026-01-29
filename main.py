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
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_json),
                                                             scope) if creds_json else ServiceAccountCredentials.from_json_keyfile_name(
        'creds.json', scope)
    archive_sheet = gspread.authorize(creds).open_by_key(SHEET_ID).worksheet("Archive")
except:
    archive_sheet = None


def clean_id(val):
    try:
        return str(int(float(val))) if not pd.isna(val) else "0"
    except:
        return str(val).strip()


def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS"]:
        name = name.replace(r, "")
    return name.strip()


def clean_val(val, default=0.0):
    try:
        return float(val) if not pd.isna(val) and str(val).strip() not in ['---', ''] else default
    except:
        return default


def get_live_data():
    live_map = {}
    try:
        res = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=150",
            timeout=5).json()
        for event in res.get('events', []):
            comp = event['competitions'][0]
            t_away = next(t for t in comp['competitors'] if t['homeAway'] == 'away')
            t_home = next(t for t in comp['competitors'] if t['homeAway'] == 'home')
            live_map[normalize(t_away['team']['displayName'])] = {
                "id": str(event['id']), "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'], "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score'])), "a_logo": t_away['team'].get('logo', ''),
                "h_logo": t_home['team'].get('logo', '')
            }
    except:
        pass
    return live_map


@app.route('/')
def index():
    tz = pytz.timezone('US/Eastern')
    today_target = f"{datetime.now(tz).month}/{datetime.now(tz).day}"

    # 1. READ ARCHIVE
    wins, losses, pct, archive_ids = 0, 0, 0.0, []
    if archive_sheet:
        try:
            adf = pd.DataFrame(archive_sheet.get_all_records())
            wins = len(adf[adf['Result'].astype(str).str.upper() == 'WIN'])
            losses = len(adf[adf['Result'].astype(str).str.upper() == 'LOSS'])
            pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
            archive_ids = [clean_id(x) for x in adf['ESPN_ID'].tolist()]
        except:
            pass

    # 2. FETCH DATA
    live_map = get_live_data()
    try:
        df = pd.read_csv(io.StringIO(requests.get(SHEET_URL).content.decode('utf-8')))
        df.columns = [str(c).strip() for c in df.columns]
    except:
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        if today_target not in str(row.get('Game Time', '')): continue
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        ld = live_map.get(normalize(a),
                          {"id": "0", "status": str(row.get('Game Time', '')), "state": "pre", "s_away": 0,
                           "s_home": 0})
        espn_id = clean_id(ld.get('id'))

        # MATH
        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, pga, ph, pgh = clean_val(row.get('PPG Away')), clean_val(row.get('PPGA Away')), clean_val(
            row.get('PPG Home')), clean_val(row.get('PPGA Home'))

        our_margin = -3.8 - max(min((ra - rh) * 0.18, 28), -28)
        edge = min(abs(h_spr - our_margin), 15.0)

        # PROJECTIONS
        tp = (pa + pgh + ph + pga) / 2
        hp, ap = (tp / 2) + (abs(our_margin) / 2), (tp / 2) - (abs(our_margin) / 2)
        if our_margin > 0: hp, ap = ap, hp

        # O/U CONFIDENCE SCALE
        v_tot = clean_val(row.get('Total'))
        diff = abs((ap + hp) - v_tot)
        ou_dir = "OVER" if (ap + hp) > v_tot else "UNDER"
        if v_tot == 0:
            conf, tip = "N/A", "No Vegas Total provided."
        elif diff > 5.0:
            conf, tip = "MUST BET", "Strong mismatch! Engine total is 5+ points off Vegas."
        elif diff > 2.5:
            conf, tip = "GOOD BET", "Statistical advantage identified. 2.5+ point difference."
        else:
            conf, tip = "FADE", "Too close to the pin. Vegas has this one right."

        pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)

        all_games.append({
            'Matchup': f"{a} @ {h}", 'status': ld['status'], 'is_live': ld['state'] == 'in',
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr, 'Edge': round(edge, 1),
            'OU_Status': conf, 'OU_Pick': f"{ou_dir} {v_tot}", 'OU_Tip': tip,
            'Proj_Away': round(ap, 1), 'Proj_Home': round(hp, 1), 'Proj_Total': round(ap + hp, 1),
            'a_logo': ld.get('a_logo') or str(row.get('Away Logo', '')),
            'h_logo': ld.get('h_logo') or str(row.get('Home Logo', ''))
        })

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)