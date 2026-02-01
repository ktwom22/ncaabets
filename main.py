import os
import io
import requests
import pandas as pd
import gspread
import json
import pytz
import stripe
from flask import Flask, render_template, jsonify, redirect, url_for, request, flash
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail
from itsdangerous import URLSafeTimedSerializer

app = Flask(__name__)

# --- CONFIG & DATABASE ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-in-railway-vars')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Stripe & Mail Config
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('EMAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('EMAIL_PASS')

db = SQLAlchemy(app)
mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])


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


# --- GOOGLE SHEETS CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"
SHEET_ID = "1RIxl64YbDn7st6h0g9LZFdL8r_NgAwYgs-KW3Kni7wM"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]


# --- HELPERS (STAYED SAME) ---
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


def clean_id(val):
    try:
        if pd.isna(val) or str(val).strip() == '': return "0"
        return str(int(float(str(val).strip())))
    except:
        return str(val).split('.')[0].strip()


def normalize(name):
    if not name or pd.isna(name): return ""
    name = str(name).upper()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "COUGARS"]:
        name = name.replace(r, "")
    return "".join(filter(str.isalnum, name))


def clean_val(val, default=0.0):
    try:
        s = str(val).strip()
        if s in ['---', '', 'nan', 'None']: return default
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
                "status": "FINAL" if event['status']['type']['state'] == "post" else event['status']['type'][
                    'shortDetail'],
                "state": event['status']['type']['state'],
                "s_away": int(clean_val(t_away.get('score', 0))),
                "s_home": int(clean_val(t_home.get('score', 0))),
                "a_team": t_away['team']['displayName'],
                "h_team": t_home['team']['displayName'],
                "a_logo": t_away['team'].get('logo', ''),
                "h_logo": t_home['team'].get('logo', ''),
                "plays": [{"clock": "LIVE", "text": p.strip()} for p in
                          comp.get('situation', {}).get('lastPlay', {}).get('text', '').split(';') if p.strip()][:2]
            }
            data['formatted_score'] = f"{data['s_away']}-{data['s_home']}"
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
        is_finished = ld['state'] == 'post' or "FINAL" in str(ld['status']).upper()
        if eid in col_p_values:
            if is_finished:
                row_idx = col_p_values.index(eid) + 1
                curr_res = archive_sheet.cell(row_idx, 3).value
                if curr_res == "PENDING":
                    score_a, score_h = ld['s_away'], ld['s_home']
                    pick_team = normalize(game_data['Raw_Pick'])
                    spread = float(game_data['Raw_Spread'])
                    is_away = normalize(ld['a_team']) == pick_team
                    p_score, o_score = (score_a, score_h) if is_away else (score_h, score_a)
                    res = "WIN" if (p_score + spread) > o_score else "LOSS" if (p_score + spread) < o_score else "PUSH"
                    archive_sheet.update(f"C{row_idx}:D{row_idx}", [[res, f"{score_a}-{score_h}"]])
        else:
            new_row = [
                datetime.now(pytz.timezone('US/Eastern')).strftime("%m/%d/%Y"),
                game_data['Matchup'], "PENDING", game_data['Live_Score'],
                game_data['Raw_Pick'], game_data['Raw_Spread'], game_data['Left_Proj'],
                game_data['Right_Proj'], game_data['Edge'], stats['pa'], stats['pga'],
                stats['ph'], stats['pgh'], stats['ra'], stats['rh'], eid, stats['sos_a'], stats['sos_h']
            ]
            archive_sheet.append_row(new_row, value_input_option='RAW')
            col_p_values.append(eid)
    except Exception as e:
        print(f"Logging Failed: {e}")


# --- UPDATED ROUTES ---

@app.route('/')
def index():
    # Detect if user is premium via Login or the Master Key
    is_premium = False
    if current_user.is_authenticated:
        is_premium = current_user.is_premium
    if request.args.get('key') == 'pro_access':
        is_premium = True

    now_tz = datetime.now(pytz.timezone('US/Eastern'))
    today_target = f"{now_tz.month}/{now_tz.day}"
    archive_data_map, wins, losses, pct, last_10 = {}, 0, 0, 0.0, []

    try:
        ares = requests.get(ARCHIVE_CSV_URL, timeout=10)
        adf = pd.read_csv(io.StringIO(ares.content.decode('utf-8')))
        if not adf.empty:
            adf.columns = [c.strip() for c in adf.columns]
            for _, row in adf.iterrows():
                archive_data_map[clean_id(row.get('ESPN_ID', '0'))] = row
            res_col = adf['Result'].astype(str).str.strip().str.upper()
            wins, losses = len(adf[res_col == 'WIN']), len(adf[res_col == 'LOSS'])
            if (wins + losses) > 0: pct = round((wins / (wins + losses)) * 100, 1)
            last_10 = adf[adf['Result'].isin(['WIN', 'LOSS', 'PUSH'])].tail(10).to_dict('records')[::-1]
    except:
        pass

    gc = get_gspread_client()
    archive_sheet = gc.open_by_key(SHEET_ID).worksheet("Archive") if gc else None
    col_p_values = archive_sheet.col_values(16) if archive_sheet else []
    live_map = get_live_data()

    try:
        df = pd.read_csv(io.StringIO(requests.get(SHEET_URL).text))
    except:
        df = pd.DataFrame()

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_target not in g_time: continue

        a_name, h_name = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        ld = live_map.get(normalize(a_name), live_map.get(normalize(h_name),
                                                          {"id": "0", "status": g_time, "state": "pre", "s_away": 0,
                                                           "s_home": 0}))
        eid = clean_id(ld.get('id', '0'))

        # Data processing logic...
        if eid != "0" and eid in archive_data_map:
            hist = archive_data_map[eid]
            pick = str(hist.get('Pick', 'N/A'))
            p_spr_val = clean_val(hist.get('Pick_Spread'))
            proj_l, proj_r = clean_val(hist.get('Proj_Away')), clean_val(hist.get('Proj_Home'))
            abs_edge = clean_val(hist.get('Edge'))
            stats = {
                'pa': hist.get('Away_PPG'), 'pga': hist.get('Away_PPGA'), 'ph': hist.get('Home_PPG'),
                'pgh': hist.get('Home_PPGA'), 'ra': hist.get('Away_Rank'), 'rh': hist.get('Home_Rank'),
                'sos_a': hist.get('Away_SOS'), 'sos_h': hist.get('Home_SOS')
            }
        else:
            ra, rh = clean_val(row.get('Rank Away', 150)), clean_val(row.get('Rank Home', 150))
            pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
            pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))
            sos_a, sos_h = clean_val(row.get('SOS Away', 150)), clean_val(row.get('SOS Home', 150))
            fd_spread = clean_val(row.get('FD Spread'))
            proj_l = round(((pa * (1 + (175 - sos_a) / 500)) + pgh) / 2, 1)
            proj_r = round(((ph * (1 + (175 - sos_h) / 500)) + pga) / 2 + 3.2, 1)
            edge = fd_spread - (proj_l - proj_r)
            pick, p_spr_val = (h_name, fd_spread) if edge > 0 else (a_name, -fd_spread)
            abs_edge = abs(edge)
            stats = {'ra': ra, 'rh': rh, 'pa': pa, 'ph': ph, 'pga': pga, 'pgh': pgh, 'sos_a': sos_a, 'sos_h': sos_h}

        # Pack the game data. Note: 'is_premium' determines if the user sees LOCKED or actual data
        game_obj = {
            'Matchup': f"{a_name} @ {h_name}", 'ESPN_ID': eid,
            'Live_Score': f"{ld.get('s_away', 0)}-{ld.get('s_home', 0)}",
            'Raw_Pick': pick, 'Raw_Spread': p_spr_val,
            'Pick': pick.upper() if is_premium else "LOCKED",
            'Pick_Spread': f"{p_spr_val:+g}" if is_premium else "???",
            'Left_Proj': proj_l, 'Right_Proj': proj_r,
            'Left_Logo': ld.get('a_logo') or row.get('Away Logo'),
            'Right_Logo': ld.get('h_logo') or row.get('Home Logo'),
            'Edge': round(abs_edge, 1), 'status': ld['status'],
            'is_live': ld.get('state') == 'in', 'last_plays': ld.get('plays', []),
            'Action': "PLAY" if abs_edge >= 4.5 else "FADE",
            'Rec': "🔥 AUTO-PLAY" if abs_edge >= 8.5 else "✅ STRONG" if abs_edge >= 5.0 else "⚠️ LOW",
            'stats': stats, 'archive_ids': list(archive_data_map.keys())
        }
        if eid != "0" and ld.get('state') in ['in', 'post']:
            auto_log_game(game_obj, stats, ld, archive_sheet, col_p_values)
        all_games.append(game_obj)

    return render_template('index.html', games=all_games, stats={"W": wins, "L": losses, "PCT": pct}, last_10=last_10,
                           is_premium=is_premium)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email, pw = request.form.get('email').lower(), request.form.get('password')
        if db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none():
            flash("Email already exists")
            return redirect(url_for('login'))

        # Create user
        u = User(email=email, password=generate_password_hash(pw, method='pbkdf2:sha256'))
        db.session.add(u)
        db.session.commit()

        # LOG IN THE USER IMMEDIATELY
        login_user(u)

        # REDIRECT DIRECTLY TO STRIPE
        return redirect(url_for('create_checkout_session'))

    return render_template('subscribe.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = db.session.execute(select(User).filter_by(email=request.form.get('email').lower())).scalar_one_or_none()
        if u and check_password_hash(u.password, request.form.get('password')):
            login_user(u)
            # If they are already premium, go to index. If not, go to subscribe.
            if u.is_premium:
                return redirect(url_for('index'))
            else:
                return redirect(url_for('create_checkout_session'))
    return render_template('login.html')


@app.route('/create-checkout-session', methods=['POST', 'GET'])
@login_required
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=current_user.email,
            payment_method_types=['card'],
            line_items=[{
                'price': os.environ.get('STRIPE_PRICE_ID'),
                'quantity': 1,
            }],
            mode='subscription',
            success_url=url_for('index', _external=True) + '?status=success',
            cancel_url=url_for('index', _external=True),
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return str(e)


@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except:
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        user_email = session.get('customer_email')
        user = db.session.execute(select(User).filter_by(email=user_email)).scalar_one_or_none()
        if user:
            user.is_premium = True
            db.session.commit()
    return jsonify(success=True)


@app.route('/api/updates')
def api_updates():
    return jsonify(get_live_data())


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))