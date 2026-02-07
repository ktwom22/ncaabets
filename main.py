import os
import io
import requests
import pandas as pd
import gspread
import json
import pytz
import stripe
import resend
from flask import Flask, render_template, jsonify, redirect, url_for, request, flash
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer

app = Flask(__name__)

# --- CONFIG & DATABASE ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-dev-key-123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
resend.api_key = os.environ.get('RESEND_API_KEY')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_premium = db.Column(db.Boolean, default=False)
    stripe_id = db.Column(db.String(100))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# --- GOOGLE SHEETS & LIVE API CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


# --- UTILITIES ---
def get_gspread_client():
    try:
        creds_json = os.environ.get('GOOGLE_CREDS')
        if not creds_json: return None
        creds_dict = json.loads(creds_json)
        if 'private_key' in creds_dict:
            creds_dict['private_key'] = creds_dict['private_key'].replace('\\n', '\n')
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Auth Error: {e}")
        return None


def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "COUGARS"]:
        name = name.replace(r, "")
    return "".join(filter(str.isalnum, name))


def clean_val(val, default=0.0):
    try:
        s = str(val).strip().replace('%', '').replace('+', '')
        if s in ['---', '', 'nan', 'None']: return default
        return float(s)
    except:
        return default


def clean_id(val):
    try:
        if pd.isna(val) or str(val).strip() == '': return "0"
        return str(int(float(str(val).strip())))
    except:
        return str(val).split('.')[0].strip()


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
            status_state = event['status']['type']['state']
            data = {
                "id": str(event['id']),
                "status": "FINAL" if status_state == "post" else event['status']['type']['shortDetail'],
                "state": status_state,
                "s_away": int(clean_val(t_away.get('score', 0))),
                "s_home": int(clean_val(t_home.get('score', 0))),
                "a_team": t_away['team']['displayName'],
                "h_team": t_home['team']['displayName'],
                "a_logo": t_away['team'].get('logo', ''),
                "h_logo": t_home['team'].get('logo', ''),
                "plays": [{"clock": "LIVE", "text": p.strip()} for p in
                          comp.get('situation', {}).get('lastPlay', {}).get('text', '').split(';') if p.strip()][:2]
            }
            live_map[normalize(t_away['team']['displayName'])] = data
            live_map[normalize(t_home['team']['displayName'])] = data
            live_map[str(event['id'])] = data
    except Exception as e:
        print(f"Live API Error: {e}")
    return live_map


def auto_log_game(game_data, stats, ld, archive_sheet, col_p_values):
    eid = str(game_data['ESPN_ID']).strip()
    if eid == "0" or not archive_sheet: return

    try:
        is_finished = ld.get('state') == 'post'
        is_live = ld.get('state') == 'in'

        if eid in col_p_values:
            row_idx = col_p_values.index(eid) + 1
            current_status = archive_sheet.cell(row_idx, 3).value

            if is_finished and current_status == "PENDING":
                score_a, score_h = ld['s_away'], ld['s_home']
                pick_team = normalize(game_data['Raw_Pick'])
                spread = float(game_data['Raw_Spread'])
                is_away = normalize(ld['a_team']) == pick_team
                p_score, o_score = (score_a, score_h) if is_away else (score_h, score_a)

                net = p_score + spread - o_score
                res = "WIN" if net > 0 else "LOSS" if net < 0 else "PUSH"
                archive_sheet.update(f"C{row_idx}:D{row_idx}", [[res, f"{score_a}-{score_h}"]])

        elif is_live or is_finished:
            new_row = [
                datetime.now(pytz.timezone('US/Eastern')).strftime("%m/%d/%Y"),
                game_data['Matchup'], "PENDING", f"{ld['s_away']}-{ld['s_home']}",
                game_data['Raw_Pick'], game_data['Raw_Spread'],
                game_data['Left_Proj'], game_data['Right_Proj'], game_data['Edge'],
                stats['pa'], stats['pga'], stats['ph'], stats['pgh'],
                stats['ra'], stats['rh'], eid, stats['sa'], stats['sh'],
                stats['l3pa'], stats['l3ph'], stats['l3pga'], stats['l3pgh']
            ]
            archive_sheet.append_row(new_row, value_input_option='RAW')
            col_p_values.append(eid)
    except Exception as e:
        print(f"Logging Failed for {eid}: {e}")


# --- CORE ROUTES ---

# ... (Keep all imports and utility functions the same until the index route)

@app.route('/')
def index():
    # --- 1. ACCESS CONTROL ---
    # Check for the pro_access key in the URL or if the logged-in user is premium
    has_pro_key = request.args.get('key') == 'pro_access'
    global_is_premium = (current_user.is_authenticated and current_user.is_premium) or has_pro_key

    now_tz = datetime.now(pytz.timezone('US/Eastern'))
    today_target = f"{now_tz.month}/{now_tz.day}"
    archive_data_map, wins, losses, pct, last_10 = {}, 0, 0, 0.0, []

    # --- 2. FETCH ARCHIVE (STATIONARY DATA SOURCE) ---
    try:
        ares = requests.get(ARCHIVE_CSV_URL, timeout=10)
        adf = pd.read_csv(io.StringIO(ares.content.decode('utf-8')))
        if not adf.empty:
            adf.columns = [c.strip() for c in adf.columns]
            # Map by ESPN_ID so we can lock in picks already saved to the sheet
            for _, row in adf.iterrows():
                archive_data_map[clean_id(row.get('ESPN_ID', '0'))] = row

            res_col = adf['Result'].astype(str).str.strip().str.upper()
            wins = len(adf[res_col == 'WIN'])
            losses = len(adf[res_col == 'LOSS'])
            if (wins + losses) > 0:
                pct = round((wins / (wins + losses)) * 100, 1)
            last_10 = adf[adf['Result'].isin(['WIN', 'LOSS', 'PUSH'])].tail(10).to_dict('records')[::-1]
    except Exception as e:
        print(f"Archive CSV Error: {e}")

    # --- 3. GSPREAD CLIENT (FOR AUTO-LOGGING) ---
    gc = get_gspread_client()
    archive_sheet, col_p_values = None, []
    if gc:
        try:
            archive_sheet = gc.open_by_key(SHEET_ID).worksheet("Archive")
            col_p_values = archive_sheet.col_values(16)  # Column P is ESPN_ID
        except Exception as e:
            print(f"GSpread Connection Error: {e}")

    # --- 4. FETCH LIVE DATA ---
    live_map = get_live_data()
    try:
        df = pd.read_csv(io.StringIO(requests.get(SHEET_URL).text))
    except Exception as e:
        print(f"Main Sheet Load Error: {e}")
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        # Skip games that aren't today or aren't currently finishing up
        if today_target not in g_time and "FINAL" not in g_time.upper():
            continue

        a_name = str(row.get('Away Team', '')).strip()
        h_name = str(row.get('Home Team', '')).strip()

        # Match with Live API
        ld = live_map.get(normalize(a_name), live_map.get(normalize(h_name), {
            "id": "0", "status": g_time, "state": "pre", "s_away": 0, "s_home": 0,
            "a_team": a_name, "h_team": h_name
        }))
        eid = clean_id(ld.get('id', '0'))

        # --- 5. STATIONARY PICK LOGIC ---
        # If the game is in the archive, use that data EXACTLY. Do not recalculate.
        if eid != "0" and eid in archive_data_map:
            hist = archive_data_map[eid]
            pick = str(hist.get('Pick', 'N/A'))
            p_spr_val = clean_val(hist.get('Spread'))
            proj_l = clean_val(hist.get('Proj_Away'))
            proj_r = clean_val(hist.get('Proj_Home'))
            abs_edge = clean_val(hist.get('Edge'))
            stats = {
                'ra': clean_val(hist.get('Away_Rank')), 'rh': clean_val(hist.get('Home_Rank')),
                'pa': clean_val(hist.get('Away_PPG')), 'ph': clean_val(hist.get('Home_PPG')),
                'pga': clean_val(hist.get('Away_PPGA')), 'pgh': clean_val(hist.get('Home_PPGA')),
                'sa': clean_val(hist.get('Away_SOS')), 'sh': clean_val(hist.get('Home_SOS')),
                'l3pa': clean_val(hist.get('Away_L3_PPG')), 'l3ph': clean_val(hist.get('Home_L3_PPG')),
                'l3pga': clean_val(hist.get('Away_L3_PPGA')), 'l3pgh': clean_val(hist.get('Home_L3_PPGA'))
            }
        else:
            # Otherwise, calculate projections fresh from the main sheet
            ra, rh = clean_val(row.get('Rank Away')), clean_val(row.get('Rank Home'))
            pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
            pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))
            sa, sh = clean_val(row.get('SOS Away')), clean_val(row.get('SOS Home'))
            l3pa, l3ph = clean_val(row.get('L3 PPG Away')), clean_val(row.get('L3 PPG Home'))
            l3pga, l3pgh = clean_val(row.get('L3 PPGA Away')), clean_val(row.get('L3 PPGA Home'))
            fd_spread = clean_val(row.get('FD Spread'))

            proj_l = round(((pa * (1 + (175 - sa) / 1000)) + pgh) / 2, 1)
            proj_r = round(((ph * (1 + (175 - sh) / 1000)) + pga) / 2 + 3.2, 1)

            rank_diff = abs(ra - rh)
            boost = rank_diff * 0.05
            if ra < rh:
                proj_l = round(proj_l + boost, 1)
            elif rh < ra:
                proj_r = round(proj_r + boost, 1)

            edge = fd_spread - (proj_l - proj_r)
            pick, p_spr_val = (h_name, fd_spread) if edge > 0 else (a_name, -fd_spread)
            abs_edge = round(abs(edge), 1)
            stats = {'ra': ra, 'rh': rh, 'pa': pa, 'ph': ph, 'pga': pga, 'pgh': pgh, 'sa': sa, 'sh': sh, 'l3pa': l3pa,
                     'l3ph': l3ph, 'l3pga': l3pga, 'l3pgh': l3pgh}

        # --- 6. VISIBILITY LOGIC ---
        # A game is public only if BOTH teams are ranked > 100
        is_public_game = (stats['ra'] > 100) and (stats['rh'] > 100)
        can_view = is_public_game or global_is_premium

        # Determine Recommendation Level
        if abs_edge >= 8.0:
            action_val, rec_val = "PLAY", "🔥 AUTO-PLAY"
        elif abs_edge >= 5.0:
            action_val, rec_val = "PLAY", "✅ STRONG"
        elif abs_edge >= 3.0:
            action_val, rec_val = "PLAY", "⚠️ LOW"
        else:
            action_val, rec_val = "FADE", "❌ NO PLAY"

        game_obj = {
            'Matchup': f"{a_name} @ {h_name}",
            'ESPN_ID': eid,
            'Live_Score': f"{ld.get('s_away', 0)}-{ld.get('s_home', 0)}",
            'Raw_Pick': pick,
            'Raw_Spread': p_spr_val,
            'Pick': pick.upper() if can_view else "LOCKED",
            'Pick_Spread': f"{p_spr_val:+g}" if can_view else "???",
            'can_view': can_view,
            'Left_Proj': proj_l, 'Right_Proj': proj_r,
            'Left_Logo': ld.get('a_logo') or row.get('Away Logo'),
            'Right_Logo': ld.get('h_logo') or row.get('Home Logo'),
            'Edge': abs_edge, 'status': ld['status'],
            'is_live': ld.get('state') == 'in',
            'last_plays': ld.get('plays', []),
            'Action': action_val, 'Rec': rec_val,
            'stats': stats, 'is_public': is_public_game
        }

        # --- 7. AUTO-LOGGING (TRIGGER STATIONARY SAVE) ---
        if eid != "0" and ld.get('state') in ['in', 'post']:
            auto_log_game(game_obj, stats, ld, archive_sheet, col_p_values)

        all_games.append(game_obj)

    # --- 8. PARLAY GENERATOR ---
    top_plays = sorted(
        [g for g in all_games if g['can_view'] and g['Action'] == 'PLAY'],
        key=lambda x: x['Edge'],
        reverse=True
    )[:10]

    return render_template('index.html',
                           games=all_games,
                           top_plays=top_plays,
                           stats={"W": wins, "L": losses, "PCT": pct},
                           last_10=last_10,
                           is_premium=global_is_premium)


# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pw = request.form.get('email').lower(), request.form.get('password')
        u = db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none()
        if u and check_password_hash(u.password, pw):
            login_user(u)
            return redirect(url_for('index'))
    return render_template('login.html')




if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))