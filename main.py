import os, io, requests, pandas as pd, gspread
from flask import Flask, render_template, jsonify
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"

# --- GOOGLE API SETUP ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name('creds.json', scope)
    gc = gspread.authorize(creds)
    archive_sheet = gc.open_by_key(SHEET_ID).worksheet("Archive")
except Exception as e:
    print(f"Connection Error: {e}")


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
                "id": event['id'],
                "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'],  # 'pre', 'in', or 'post'
                "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score']))
            }
    except:
        pass
    return live_map


@app.route('/api/updates')
def api_updates():
    live_map = get_live_data()
    res = requests.get(SHEET_URL)
    df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
    df.columns = [str(c).strip() for c in df.columns]

    updates = []
    for _, row in df.iterrows():
        a = str(row.get('Away Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        ld = live_map.get(normalize(a),
                          {"status": str(row.get('Game Time', 'Upcoming')), "state": "pre", "s_away": 0, "s_home": 0})

        updates.append({
            "matchup": f"{a} @ {row.get('Home Team')}",
            "score": f"{ld.get('s_away')}-{ld.get('s_home')}",
            "status": ld.get('status'),
            "is_live": ld.get('state') == 'in'
        })
    return jsonify(updates)


@app.route('/')
def index():
    live_map = get_live_data()
    res = requests.get(SHEET_URL)
    df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
    df.columns = [str(c).strip() for c in df.columns]

    wins, losses, pct, archive_ids = 0, 0, 0, []
    adf = pd.DataFrame()

    try:
        archive_records = archive_sheet.get_all_records()
        if archive_records:
            adf = pd.DataFrame(archive_records)
            wins = len(adf[adf['Result'] == 'WIN'])
            losses = len(adf[adf['Result'] == 'LOSS'])
            pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0
            if 'ESPN_ID' in adf.columns:
                archive_ids = adf['ESPN_ID'].astype(str).tolist()
    except:
        pass

    all_games = []
    for _, row in df.iterrows():
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        ld = live_map.get(normalize(a),
                          {"id": "0", "status": str(row.get('Game Time', 'Upcoming')), "state": "pre", "s_away": 0,
                           "s_home": 0})
        espn_id = str(ld.get('id'))

        # Data Logic
        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, ph, pga, pgh = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home')), clean_val(
            row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        rank_gap = (ra - rh) * 0.18
        our_margin = -3.8 - rank_gap
        edge = round(abs(h_spr - our_margin), 1)

        # PICK LOCKING: If in archive, use the record. Else, calc.
        if espn_id != "0" and espn_id in archive_ids:
            locked_row = adf[adf['ESPN_ID'].astype(str) == espn_id].iloc[0]
            pick, p_spr = locked_row['Pick'], locked_row['Pick_Spread']
        else:
            pick, p_spr = (h, h_spr) if (h_spr - our_margin) > 0 else (a, -h_spr)

        # Projections
        total_pts = (pa + pgh + ph + pga) / 2
        hp, ap = (total_pts / 2) + (abs(our_margin) / 2), (total_pts / 2) - (abs(our_margin) / 2)
        if our_margin > 0: hp, ap = ap, hp

        # Results & Archiving
        res_label = ""
        if ld.get('state') == "post":
            diff = ld['s_home'] - ld['s_away']
            res_label = "WIN" if ((diff + h_spr > 0) if pick == h else ((-diff) - h_spr > 0)) else "LOSS"

            if espn_id != "0" and espn_id not in archive_ids:
                try:
                    archive_sheet.append_row([
                        datetime.now().strftime("%m/%d/%Y"), f"{a} @ {h}", res_label, f"{ld['s_away']}-{ld['s_home']}",
                        pick, p_spr, round(ap, 1), round(hp, 1), edge, pa, pga, ph, pgh, int(ra), int(rh), espn_id
                    ])
                    archive_ids.append(espn_id)
                except:
                    pass

        all_games.append({
            'Matchup': f"{a} @ {h}", 'Result': res_label, 'Final_Score': f"{ld['s_away']}-{ld['s_home']}",
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr, 'Proj_Away': round(ap, 1),
            'Proj_Home': round(hp, 1), 'Edge': edge, 'status': ld['status'],
            'a_logo': str(row.get('Away Logo', '')), 'h_logo': str(row.get('Home Logo', '')),
            'ESPN_ID': espn_id, 'is_live': ld['state'] == 'in', 'a_rank': int(ra), 'h_rank': int(rh)
        })

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct},
                           seo={"title": "Edge Engine Pro", "desc": "CBB Analytics"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)