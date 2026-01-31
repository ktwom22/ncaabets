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
    creds_dict = json.loads(creds_json) if creds_json else None
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict,
                                                             scope) if creds_dict else ServiceAccountCredentials.from_json_keyfile_name(
        'creds.json', scope)
    gc = gspread.authorize(creds)
    doc = gc.open_by_key(SHEET_ID)
    archive_sheet = doc.worksheet("Archive")
except Exception as e:
    print(f"Auth Error: {e}");
    archive_sheet = None


# --- HELPERS ---
def clean_id(val):
    try:
        return str(int(float(val))) if not pd.isna(val) and str(val).strip() != '' else "0"
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
        return float(val) if not pd.isna(val) and str(val).strip() not in ['---', '', 'nan'] else default
    except:
        return default


def get_live_data():
    live_map = {}
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=150",
            headers=headers, timeout=5).json()
        for event in res.get('events', []):
            comp = event['competitions'][0]
            t_away = next(t for t in comp['competitors'] if t['homeAway'] == 'away')
            t_home = next(t for t in comp['competitors'] if t['homeAway'] == 'home')
            live_map[normalize(t_away['team']['displayName'])] = {
                "id": str(event['id']), "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'], "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score'])),
                "a_logo": t_away['team'].get('logo', '').replace('http://', 'https://'),
                "h_logo": t_home['team'].get('logo', '').replace('http://', 'https://')
            }
    except Exception as e:
        print(f"Live Error: {e}")
    return live_map


# --- LOGGING & UPDATING ---
def auto_log_game(game_data, row_stats, existing_ids):
    if not archive_sheet or game_data['ESPN_ID'] == "0" or game_data['ESPN_ID'] in existing_ids:
        return
    try:
        new_row = [
            datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d"),
            game_data['Matchup'], "PENDING", "0-0",
            game_data['Pick'], game_data['Pick_Spread'],
            game_data['Left_Proj'], game_data['Right_Proj'], game_data['Edge'],
            row_stats['pa'], row_stats['pga'], row_stats['ph'], row_stats['pgh'],
            row_stats['ra'], row_stats['rh'], game_data['ESPN_ID']
        ]
        archive_sheet.append_row(new_row)
        existing_ids.append(game_data['ESPN_ID'])
    except Exception as e:
        print(f"Log Error: {e}")


def update_finished_results(adf, live_map):
    """Deep searches for PENDING games and settles them in the Sheet."""
    if not archive_sheet or adf.empty: return
    pending = adf[adf['Result'].astype(str).str.upper() == 'PENDING']

    for index, row in pending.iterrows():
        eid = clean_id(row.get('ESPN_ID'))
        if eid == "0": continue

        # Check current scoreboard map
        game_info = next((v for k, v in live_map.items() if v['id'] == eid), None)

        # If not on today's scoreboard, fetch historical summary
        if not game_info:
            try:
                url = f"http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={eid}"
                res = requests.get(url, timeout=5).json()
                header = res.get('header', {})
                if header.get('status', {}).get('type', {}).get('state') == 'post':
                    comp = header['competitions'][0]
                    t_away = next(t for t in comp['competitors'] if t['homeAway'] == 'away')
                    t_home = next(t for t in comp['competitors'] if t['homeAway'] == 'home')
                    game_info = {'state': 'post', 's_away': int(t_away['score']), 's_home': int(t_home['score'])}
            except:
                continue

        if game_info and game_info.get('state') == 'post':
            s_a, s_h = game_info['s_away'], game_info['s_home']
            pick = str(row['Pick']).upper()
            spr = float(row['Pick_Spread'])
            matchup = str(row['Matchup'])
            home_team_name = matchup.split(" @ ")[1].upper()

            # Use keyword matching to see if the pick was the Home team
            is_home_pick = any(word in pick for word in home_team_name.split() if len(word) > 3)

            if is_home_pick:
                win = (s_h + spr) > s_a
            else:
                win = (s_a + spr) > s_h

            row_idx = index + 2
            archive_sheet.update_cell(row_idx, 3, "WIN" if win else "LOSS")
            archive_sheet.update_cell(row_idx, 4, f"{s_a}-{s_h}")


@app.route('/api/updates')
def api_updates():
    live_map = get_live_data()
    updates = {data['id']: {"score": f"{data['s_away']}-{data['s_home']}", "status": data['status'],
                            "is_live": data['state'] == 'in'} for team, data in live_map.items()}
    return jsonify(updates)


@app.route('/')
def index():
    tz = pytz.timezone('US/Eastern')
    now_tz = datetime.now(tz)
    today_target = f"{now_tz.month}/{now_tz.day}"

    wins, losses, pct, archive_ids, last_10 = 0, 0, 0.0, [], []
    adf = pd.DataFrame()

    if archive_sheet:
        try:
            records = archive_sheet.get_all_records()
            if records:
                adf = pd.DataFrame(records)
                settled = adf[adf['Result'].str.upper().isin(['WIN', 'LOSS'])]
                wins = len(settled[settled['Result'].str.upper() == 'WIN'])
                losses = len(settled[settled['Result'].str.upper() == 'LOSS'])
                pct = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0.0
                archive_ids = [clean_id(x) for x in adf['ESPN_ID'].tolist()]

                # Fetch last 10 settled games
                last_10 = settled.tail(10)[['Matchup', 'Result']].to_dict('records')
                last_10.reverse()
        except:
            pass

    live_map = get_live_data()
    if not adf.empty: update_finished_results(adf, live_map)

    try:
        res = requests.get(SHEET_URL, timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = [str(c).strip() for c in df.columns]
    except:
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_target not in g_time: continue
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        ld = live_map.get(normalize(a), {"id": "0", "status": g_time, "state": "pre", "s_away": 0, "s_home": 0})
        espn_id = clean_id(ld.get('id'))

        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))
        stats_bundle = {'ra': ra, 'rh': rh, 'pa': pa, 'ph': ph, 'pga': pga, 'pgh': pgh}

        our_margin = 3.8 + ((ra - rh) * 0.18)
        our_margin = max(min(our_margin, 28), -28)
        true_edge = abs(h_spr - our_margin)
        avg_total = (pa + pgh + ph + pga) / 2
        proj_left = round((avg_total / 2) - (our_margin / 2), 1)
        proj_right = round((avg_total / 2) + (our_margin / 2), 1)

        if espn_id != "0" and espn_id in archive_ids:
            try:
                l_row = adf[adf['ESPN_ID'].apply(clean_id) == espn_id].iloc[-1]
                pick, p_spr = l_row['Pick'], l_row['Pick_Spread']
            except:
                pick, p_spr = (h, h_spr) if (our_margin > h_spr) else (a, -h_spr)
        else:
            pick, p_spr = (h, h_spr) if (our_margin > h_spr) else (a, -h_spr)

        game_data = {
            'Matchup': f"{a} @ {h}", 'ESPN_ID': espn_id, 'Live_Score': f"{ld['s_away']}-{ld['s_home']}",
            'Pick': str(pick).upper(), 'Pick_Spread': p_spr, 'Left_Proj': proj_left, 'Right_Proj': proj_right,
            'Left_Logo': ld.get('a_logo'), 'Right_Logo': ld.get('h_logo'), 'Edge': round(true_edge, 1),
            'status': ld['status'], 'is_live': ld.get('state') == 'in',
            'Action': "PLAY" if true_edge >= 10.0 else "FADE",
            'Rec': "🔥 AUTO-PLAY" if true_edge >= 15 else "✅ STRONG" if true_edge >= 10 else "⚠️ LOW"
        }

        if espn_id != "0" and (ld.get('state') == 'in' or "FINAL" in ld['status'].upper()):
            auto_log_game(game_data, stats_bundle, archive_ids)

        all_games.append(game_data)

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct}, last_10=last_10)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))