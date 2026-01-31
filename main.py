import os, io, requests, pandas as pd, gspread, json
from flask import Flask, render_template
from datetime import datetime
import pytz
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7w"

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
    print(f"Auth Error: {e}")
    archive_sheet = None


# --- HELPERS ---
def clean_id(val):
    try:
        if pd.isna(val) or str(val).strip() == '': return "0"
        return str(int(float(str(val).strip())))
    except:
        return str(val).split('.')[0].strip()


def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS"]:
        name = name.replace(r, "")
    return "".join(filter(str.isalnum, name))


def clean_val(val, default=0.0):
    try:
        s = str(val).strip()
        if s in ['---', '', 'nan']: return default
        return float(s)
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
            data = {
                "id": str(event['id']),
                "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'],
                "s_away": int(clean_val(t_away['score'])),
                "s_home": int(clean_val(t_home['score']))
            }
            live_map[normalize(t_away['team']['displayName'])] = data
            live_map[normalize(t_home['team']['displayName'])] = data
    except Exception as e:
        print(f"Live Error: {e}")
    return live_map


def auto_log_game(game_data, row_stats, existing_ids):
    """Writes the game to Google Sheets Archive only once."""
    if not archive_sheet or game_data['ESPN_ID'] == "0" or str(game_data['ESPN_ID']) in existing_ids:
        return
    try:
        new_row = [
            datetime.now(pytz.timezone('US/Eastern')).strftime("%Y-%m-%d"),
            game_data['Matchup'],
            "PENDING",
            "0-0",
            game_data['Pick'],
            game_data['Pick_Spread'],
            game_data['Left_Proj'],
            game_data['Right_Proj'],
            game_data['Edge'],
            row_stats['pa'],
            row_stats['pga'],
            row_stats['ph'],
            row_stats['pgh'],
            row_stats['ra'],
            row_stats['rh'],
            str(game_data['ESPN_ID'])
        ]
        archive_sheet.append_row(new_row)
        # Update local list to prevent double-writing in the same loop
        existing_ids.append(str(game_data['ESPN_ID']))
    except Exception as e:
        print(f"Log Error: {e}")


@app.route('/')
def index():
    now_tz = datetime.now(pytz.timezone('US/Eastern'))
    today_target = f"{now_tz.month}/{now_tz.day}"
    archive_ids, archive_data_map = [], {}
    wins, losses, pct, last_10 = 0, 0, 0.0, []

    # 1. LOAD ARCHIVE FROM CSV (FOR PERSISTENCE)
    try:
        ares = requests.get(ARCHIVE_CSV_URL, timeout=10)
        adf = pd.read_csv(io.StringIO(ares.content.decode('utf-8')))
        if not adf.empty:
            adf.columns = [c.strip() for c in adf.columns]
            for _, row in adf.iterrows():
                sid = clean_id(row.get('ESPN_ID', '0'))
                archive_ids.append(sid)
                archive_data_map[sid] = row

            if 'Result' in adf.columns:
                res_col = adf['Result'].astype(str).str.strip().str.upper()
                wins = len(adf[res_col == 'WIN'])
                losses = len(adf[res_col == 'LOSS'])
                if (wins + losses) > 0:
                    pct = round((wins / (wins + losses)) * 100, 1)
                last_10 = adf.tail(10).to_dict('records')[::-1]
    except Exception as e:
        print(f"Archive Load Error: {e}")

    # 2. LOAD TODAY'S ACTIVE DATA
    live_map = get_live_data()
    try:
        res = requests.get(SHEET_URL, timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
    except Exception as e:
        print(f"Main Sheet Error: {e}")
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_target not in g_time:
            continue

        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        eid = clean_id(row.get('ESPN_ID', '0'))

        # Standard Stats from main sheet
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        # 3. PERSISTENCE LOGIC (LOCKING)
        # If game exists in archive, overwrite sheet values with the locked ones
        if eid in archive_data_map:
            locked = archive_data_map[eid]
            pick = str(locked.get('Pick', 'N/A')).upper()
            p_spr_val = locked.get('Pick_Spread', 0)
            abs_edge = clean_val(locked.get('Edge', 0))
            proj_left = clean_val(locked.get('Proj_Away', 0))
            proj_right = clean_val(locked.get('Proj_Home', 0))

            # Use stats frozen in the archive
            pa = clean_val(locked.get('PPGA', pa))
            pga = clean_val(locked.get('PPGAA', pga))
            ra = int(clean_val(locked.get('RankA', ra)))
            ph = clean_val(locked.get('PPGH', ph))
            pgh = clean_val(locked.get('PPGAH', pgh))
            rh = int(clean_val(locked.get('RankH', rh)))
        else:
            # DYNAMIC CALCULATION (Pick hasn't started yet)
            h_spr = clean_val(row.get('FD Spread'))
            rank_diff = ra - rh
            scaled_diff = (rank_diff / abs(rank_diff)) * (abs(rank_diff) ** 0.65) if rank_diff != 0 else 0
            our_margin = 3.2 + (scaled_diff * 0.8)
            avg_total = (pa + pgh + ph + pga) / 2
            proj_left = round((avg_total / 2) - (our_margin / 2), 1)
            proj_right = round((avg_total / 2) + (our_margin / 2), 1)

            engine_home_spread = proj_left - proj_right
            home_edge = h_spr - engine_home_spread

            if home_edge > 0:
                pick, p_spr_val = h, h_spr
            else:
                pick, p_spr_val = a, -h_spr
            abs_edge = abs(home_edge)

        # Get Live Stats from ESPN API
        ld = live_map.get(normalize(a), live_map.get(normalize(h), {
            "id": eid, "status": g_time, "state": "pre", "s_away": 0, "s_home": 0
        }))

        # Package data for Frontend
        game_data = {
            'Matchup': f"{a} @ {h}",
            'ESPN_ID': ld['id'],
            'Live_Score': f"{ld['s_away']}-{ld['s_home']}",
            'Pick': str(pick).upper(),
            'Pick_Spread': f"{p_spr_val:+g}" if isinstance(p_spr_val, (int, float)) else p_spr_val,
            'Left_Proj': proj_left, 'Right_Proj': proj_right,
            'Left_Logo': row.get('Away Logo'), 'Right_Logo': row.get('Home Logo'),
            'Edge': round(abs_edge, 1),
            'status': ld['status'],
            'is_live': ld.get('state') == 'in',
            'Action': "PLAY" if abs_edge >= 4.5 else "FADE",
            'Rec': "🔥 AUTO-PLAY" if abs_edge >= 8.5 else "✅ STRONG" if abs_edge >= 5.0 else "⚠️ LOW",
            'pa': pa, 'pga': pga, 'ra': int(ra),
            'ph': ph, 'pgh': pgh, 'rh': int(rh)
        }

        # 4. AUTO-LOG TRIGGER
        # If the game is LIVE or FINAL and not yet archived, save it.
        if ld['id'] != "0" and (ld.get('state') == 'in' or "FINAL" in ld['status'].upper()):
            stats_to_save = {'ra': ra, 'rh': rh, 'pa': pa, 'ph': ph, 'pga': pga, 'pgh': pgh}
            auto_log_game(game_data, stats_to_save, archive_ids)

        all_games.append(game_data)

    return render_template('index.html',
                           games=all_games,
                           stats={"W": wins, "L": losses, "PCT": pct},
                           last_10=last_10,
                           archive_ids=archive_ids)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)