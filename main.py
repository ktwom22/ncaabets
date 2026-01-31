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
    else:
        # Using the JSON you provided
        creds_dict = {
            "type": "service_account",
            "project_id": "ncaa-485416",
            "private_key_id": "16317a9691bc06820de3306412ef3d19ff4fad8e",
            "private_key": os.environ.get('PRIVATE_KEY', "").replace('\\n', '\n'),
            "client_email": "ncaamb@ncaa-485416.iam.gserviceaccount.com",
            "client_id": "113628747086896997388",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/ncaamb%40ncaa-485416.iam.gserviceaccount.com"
        }

    # If the private key isn't in env, use the string directly (for local testing)
    if not creds_dict["private_key"]:
        creds_dict[
            "private_key"] = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCcaxus5h9R/HJO\nWzWTdmPJCrjtmwzOSCxcE50Dfsd3CIaEFYqBpssuT6xbAmn6TNA5y/60NoZaQ6Q+\nCPA3r+3h1+fHBZld478mEjorxIEktPTs+nRCt0NpI/AG6Fnmcsx9eYwl9GQvtpIQ\nQ5fL5yOF0hlvX+RqAJ4+C+h3StPmVOPTE66vdbI/ElEvL3Sy158qiDRsqMd2fgwe\nGjbtmOOSyFRuJIKYv/LjecKzEBzOSLfNpSDbJCSsIMbQKzqPMHltiBQPPrTydF+4\nxGgdt/b1MJ34AAFV26iWM6k9Mjnkt/0Dg8byrnnDxkS5IOk0LLxmfCME5lNX4Wfa\nDaZTdeIHAgMBAAECggEAH5vHWm+gWaV9hdcJoxR0Aq7fguhntJHCIRM3kfq/HQ9E\ne5GMzTli6qdgCX4Z42I9W8ic3lb8XGY53O1aea5cEFzccgvwG5iHyo45YhnSDRRi\nQDc2Yjr9bNQ9z2+JpzeAmkSDLTJPQpMOvimilapOM98qk6aZllAsqhYm8mBVJMCm\nqdj0NFhVDv53h3BB6vyG38UWBUZMtqYU/OTTNo3RjEkQaH4433KisW4DjxD8rxo0\nFk6KoLBhHYleEdM6s+2KIFc+/+n5T4Nop0uy6WQWUaGPqBQwZostD6BZwnfoKa4n\n3TTXm3gdMNMJw5vWZGgtNnw1k/qsLWF0O1IZObAmRQKBgQDZKerRJuxdaFXcgtLU\nKj/blN6+gJymzwjpXeCfig+gUtnxCoTAaFHihMF9jwbU5TFg/NKn/csZFdPi2vCv\n2HRt1VQ8/kxDj9jAKO0j0cqIVMBNOvl1YJGC/LOfzAvKg6l1fhknd4Uh+zjSVs6X\nfwxhTk8JnZPQ5ZZX2MH5Pb+gfQKBgQC4ZC1qqFpyoUwWsQZIHMKaPb04XhvhxhwP\nMuVC2bEPyma0n5V8tA8ccMK1aMOXLp1SsEIEvlKaN4ZHpl/280+Slu3eI1Ipcq0/\nzjtpqQMizLURRkXzRharHgyeRNyBCR4KgWlBDnkTuQuVpjUXglW5bEEprQ+axcSv\nquIQgRz30wKBgFRTGoMsggujP/PoOMV5wmIZZITEnA+JxQQZF+fbTEYM5ePbGcE8\nwM8cjaWbrCNu+8WVZpckzYBoIWatbVhazJr5g0RL2oFBkgDL44lNJT/a6PEUPVl+\nrgIW7gjWyp1QkrS0yj+xAVk4m8/RNVdOVhhSuA4byhILlF14JZoKiCZhAoGBAKBq\nxvhbrxS7Ly8uo4BpRQRbYhuABFHPwOmYJcybB2ftdpz9mVf8bokXsM2Sb1c/vq8J\nmOX3jnWMiM4c+LAnzbqChD8WC4zzr7Yq/ZVW7NuBrKVytbiH6YDFi8TFs/Cutev9\n2kw3Ay8dde6jwtzJDztZ6vcPENxd4lfSGwqwyTrxAoGATBYe8KCfiEQu80LHApI5\nu4MSAzMqUmwkNpGUn0m5OP4lZZq5eWYfihUwfNJxwN7TMBT1wxKZ9DuFbFl5eUbu\nkjsyy/hDZYzSh91mQubHMNkLoZCVjFQsvs/U1QePY4XKAxlLWwhR0cT90q+RCBNg\n6AWO+r09N4NW3z6kdakWl7c=\n-----END PRIVATE KEY-----\n".replace(
            '\\n', '\n')

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    doc = gc.open_by_key(SHEET_ID)
    archive_sheet = doc.worksheet("Archive")
except Exception as e:
    print(f"CRITICAL Auth Error: {e}")
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
        print(f"Live API Error: {e}")
    return live_map


def auto_log_game(game_data, s, existing_ids):
    """Writes to sheet based on your provided column structure."""
    if not archive_sheet or game_data['ESPN_ID'] == "0" or str(game_data['ESPN_ID']) in existing_ids:
        return
    try:
        # UPDATED TO MATCH YOUR CSV SNAPSHOT EXACTLY
        new_row = [
            datetime.now(pytz.timezone('US/Eastern')).strftime("%m/%d/%Y"),  # Date
            game_data['Matchup'],  # Matchup
            "PENDING",  # Result
            game_data['Live_Score'],  # Final_Score (Updates as live)
            game_data['Pick'],  # Pick
            game_data['Pick_Spread'],  # Pick_Spread
            game_data['Left_Proj'],  # Proj_Away
            game_data['Right_Proj'],  # Proj_Home
            game_data['Edge'],  # Edge
            s['pa'],  # Away_PPG
            s['pga'],  # Away_PPGA
            s['ph'],  # Home_PPG
            s['pgh'],  # Home_PPGA
            s['ra'],  # Away_Rank
            s['rh'],  # Home_Rank
            str(game_data['ESPN_ID'])  # ESPN_ID
        ]
        archive_sheet.append_row(new_row)
        existing_ids.append(str(game_data['ESPN_ID']))
        print(f"SUCCESS: Logged {game_data['Matchup']}")
    except Exception as e:
        print(f"LOGGING FAILURE for {game_data['Matchup']}: {e}")


@app.route('/')
def index():
    now_tz = datetime.now(pytz.timezone('US/Eastern'))
    today_target = f"{now_tz.month}/{now_tz.day}"
    archive_ids, archive_data_map = [], {}
    wins, losses, pct, last_10 = 0, 0, 0.0, []

    # 1. LOAD ARCHIVE
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
        print(f"Archive CSV Load Error: {e}")

    # 2. LOAD ACTIVE DATA
    live_map = get_live_data()
    try:
        res = requests.get(SHEET_URL, timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
    except:
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_target not in g_time: continue

        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        eid = clean_id(row.get('ESPN_ID', '0'))

        # Fetch Raw Stats from main sheet
        ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        # 3. LOCKING LOGIC
        if eid in archive_data_map:
            locked = archive_data_map[eid]
            pick = str(locked.get('Pick', 'N/A')).upper()
            p_spr_val = locked.get('Pick_Spread', 0)
            abs_edge = clean_val(locked.get('Edge', 0))
            proj_left = clean_val(locked.get('Proj_Away', 0))
            proj_right = clean_val(locked.get('Proj_Home', 0))
            pa, pga, ra = clean_val(locked.get('Away_PPG')), clean_val(locked.get('Away_PPGA')), int(
                clean_val(locked.get('Away_Rank')))
            ph, pgh, rh = clean_val(locked.get('Home_PPG')), clean_val(locked.get('Home_PPGA')), int(
                clean_val(locked.get('Home_Rank')))
        else:
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

        ld = live_map.get(normalize(a), live_map.get(normalize(h), {
            "id": eid, "status": g_time, "state": "pre", "s_away": 0, "s_home": 0
        }))

        game_data = {
            'Matchup': f"{a} @ {h}",
            'ESPN_ID': ld['id'],
            'Live_Score': f"{ld['s_away']}-{ld['s_home']}",
            'Pick': str(pick).upper(),
            'Pick_Spread': f"{p_spr_val:+g}" if isinstance(p_spr_val, (int, float)) else p_spr_val,
            'Left_Proj': proj_left, 'Right_Proj': proj_right,
            'Left_Logo': row.get('Away Logo'), 'Right_Logo': row.get('Home Logo'),
            'Edge': round(abs_edge, 1),
            'status': ld['status'], 'is_live': ld.get('state') == 'in',
            'Action': "PLAY" if abs_edge >= 4.5 else "FADE",
            'Rec': "🔥 AUTO-PLAY" if abs_edge >= 8.5 else "✅ STRONG" if abs_edge >= 5.0 else "⚠️ LOW",
            'pa': pa, 'pga': pga, 'ra': int(ra),
            'ph': ph, 'pgh': pgh, 'rh': int(rh)
        }

        # 4. LOG TRIGGER
        if ld['id'] != "0" and (ld.get('state') == 'in' or "FINAL" in ld['status'].upper()):
            auto_log_game(game_data, {'ra': ra, 'rh': rh, 'pa': pa, 'ph': ph, 'pga': pga, 'pgh': pgh}, archive_ids)

        all_games.append(game_data)

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct}, last_10=last_10,
                           archive_ids=archive_ids)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))