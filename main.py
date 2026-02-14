import os
import io
import requests
import pandas as pd
import gspread
import json
import pytz
import stripe
import resend
import secrets
from flask import Flask, render_template, jsonify, redirect, url_for, request, flash, session, send_from_directory, \
    make_response
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select, text
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_caching import Cache

app = Flask(__name__)

# --- CONFIG & CACHING ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-dev-key-123')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 60})

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
    reset_token = db.Column(db.String(100), unique=True, nullable=True)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception as e:
        db.session.rollback()
        print(f"User load failed (likely missing column): {e}")
        return None


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
    name = str(name).upper().strip()
    for r in ["STATE", "ST", "UNIVERSITY", "UNIV", "TIGERS", "WILDCATS", "BULLDOGS", "COUGARS", "OWLS", "PHOENIX"]:
        name = name.replace(r, "")
    return "".join(filter(str.isalnum, name))


def clean_val(val, default=0.0):
    try:
        if val is None: return default
        s = str(val).strip().replace('%', '').replace('+', '')
        if s in ['---', '', 'nan', 'None', 'TBD']: return default
        return float(s)
    except:
        return default


def clean_id(val):
    try:
        if pd.isna(val) or str(val).strip() == '' or str(val).strip() == '0': return "0"
        return str(int(float(str(val).strip())))
    except:
        return str(val).split('.')[0].strip()


@cache.memoize(timeout=30)
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
                "h_logo": t_home['team'].get('logo', '')
            }
            norm_away, norm_home = normalize(data['a_team']), normalize(data['h_team'])
            live_map[norm_away] = live_map[norm_home] = live_map[data['id']] = data
    except Exception as e:
        print(f"Live API Error: {e}")
    return live_map


def auto_log_game(game_data, stats, ld, archive_sheet, col_p_values):
    eid = str(game_data['ESPN_ID']).strip()
    if eid == "0" or not archive_sheet or game_data['Raw_Pick'] == "TBD": return
    try:
        is_finished = ld.get('state') == 'post'
        if eid in col_p_values:
            row_idx = col_p_values.index(eid) + 1
            if is_finished:
                score_a, score_h = ld['s_away'], ld['s_home']
                archive_sheet.update(f"C{row_idx}:D{row_idx}", [
                    ["WIN" if "WIN" in str(game_data.get('Rec')) else "LOSS", f"{score_a}-{score_h}"]])
        elif ld.get('state') in ['in', 'post']:
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
        print(f"Logging Failed: {e}")


def get_seo_metadata(top_play=None):
    if top_play:
        matchup, edge = top_play['Matchup'], top_play['Edge']
        return {
            "title": f"Lock Alert: {matchup} (+{edge} Edge) | Edge Engine Pro",
            "description": f"Today's Featured Play: {matchup}. Variance: {edge}. View metrics now.",
            "image": top_play['Left_Logo'] or top_play['Right_Logo']
        }
    return {
        "title": "Edge Engine Pro | Live College Basketball Analytics",
        "description": "Professional CBB analytics. Stationary pick logic for market-beating edge.",
        "image": ""
    }


# --- CORE ROUTES ---

@app.route('/')
def index():
    has_pro_key = request.args.get('key') == 'pro_access'
    if request.args.get('session_id') and current_user.is_authenticated:
        current_user.is_premium = True
        db.session.commit()
        flash("Pro Access Activated!", "success")

    is_premium = has_pro_key or (current_user.is_authenticated and current_user.is_premium)
    team_filter = request.args.get('filter', 'all')
    now_tz = datetime.now(pytz.timezone('US/Eastern'))
    today_target = f"{now_tz.month}/{now_tz.day}"

    archive_data_map, wins, losses, pct, last_10 = {}, 0, 0, 0.0, []

    try:
        ares = requests.get(ARCHIVE_CSV_URL, timeout=10)
        if ares.status_code == 200:
            adf = pd.read_csv(io.StringIO(ares.content.decode('utf-8')), skipinitialspace=True)
            if not adf.empty:
                adf.columns = [c.strip() for c in adf.columns]
                for _, row in adf.iterrows():
                    eid_key = clean_id(row.get('ESPN_ID', '0'))
                    if eid_key != "0": archive_data_map[eid_key] = row
                res_col = adf['Result'].astype(str).str.strip().str.upper()
                wins = len(adf[res_col == 'WIN'])
                losses = len(adf[res_col == 'LOSS'])
                if (wins + losses) > 0:
                    pct = round((wins / (wins + losses)) * 100, 1)
                last_10 = adf[res_col.isin(['WIN', 'LOSS', 'PUSH'])].tail(10).to_dict('records')[::-1]
    except Exception as e:
        print(f"Archive Error: {e}")

    live_map = get_live_data()
    try:
        df = pd.read_csv(io.StringIO(requests.get(SHEET_URL).text))
    except:
        df = pd.DataFrame()

    gc = get_gspread_client()
    archive_sheet, col_p_values = None, []
    if gc:
        try:
            archive_sheet = gc.open_by_key(SHEET_ID).worksheet("Archive")
            col_p_values = [clean_id(x) for x in archive_sheet.col_values(16)]
        except Exception as e:
            print(f"GSpread Error: {e}")

    all_games = []
    for _, row in df.iterrows():
        g_time = str(row.get('Game Time', ''))
        if today_target not in g_time and "FINAL" not in g_time.upper():
            continue

        a_name, h_name = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        ld = live_map.get(normalize(a_name), live_map.get(normalize(h_name), {
            "id": "0", "status": g_time, "state": "pre", "s_away": 0, "s_home": 0,
            "a_team": a_name, "h_team": h_name
        }))
        eid = clean_id(ld.get('id', '0'))
        status_label = str(ld.get('status', '')).upper()
        is_locked = any(x in status_label for x in ["PENDING", "FINAL", "1ST", "2ND", "HALF"]) or ld.get('state') in [
            'in', 'post']

        if is_locked and eid in archive_data_map:
            hist = archive_data_map[eid]
            pick, p_spr_val = str(hist.get('Pick', 'N/A')), clean_val(hist.get('Pick_Spread'))
            proj_l, proj_r, abs_edge = clean_val(hist.get('Proj_Away')), clean_val(hist.get('Proj_Home')), clean_val(
                hist.get('Edge'))
            stats = {
                'ra': clean_val(hist.get('Away_Rank')), 'rh': clean_val(hist.get('Home_Rank')),
                'pa': clean_val(hist.get('Away_PPG')), 'ph': clean_val(hist.get('Home_PPG')),
                'pga': clean_val(hist.get('Away_PPGA')), 'pgh': clean_val(hist.get('Home_PPGA')),
                'sa': clean_val(hist.get('Away_SOS')), 'sh': clean_val(hist.get('Home_SOS')),
                'l3pa': clean_val(hist.get('Away_L3_PPG')), 'l3ph': clean_val(hist.get('Home_L3_PPG')),
                'l3pga': clean_val(hist.get('Away_L3_PPGA')), 'l3pgh': clean_val(hist.get('Home_L3_PPGA'))
            }
        else:
            stats = {
                'ra': clean_val(row.get('Rank Away')), 'rh': clean_val(row.get('Rank Home')),
                'pa': clean_val(row.get('PPG Away')), 'ph': clean_val(row.get('PPG Home')),
                'pga': clean_val(row.get('PPGA Away')), 'pgh': clean_val(row.get('PPGA Home')),
                'sa': clean_val(row.get('SOS Away')), 'sh': clean_val(row.get('SOS Home')),
                'l3pa': clean_val(row.get('L3 PPG Away')), 'l3ph': clean_val(row.get('L3 PPG Home')),
                'l3pga': clean_val(row.get('L3 PPGA Away')), 'l3pgh': clean_val(row.get('L3 PPGA Home'))
            }
            fd_spread = clean_val(row.get('FD Spread'), default=None)
            proj_l = round(((stats['pa'] * (1 + (175 - stats['sa']) / 1000)) + stats['pgh']) / 2, 1)
            proj_r = round(((stats['ph'] * (1 + (175 - stats['sh']) / 1000)) + stats['pga']) / 2 + 3.2, 1)
            if fd_spread is None:
                pick, p_spr_val, abs_edge = "TBD", 0.0, 0.0
            else:
                edge = fd_spread - (proj_l - proj_r)
                pick, p_spr_val = (h_name, fd_spread) if edge > 0 else (a_name, -fd_spread)
                abs_edge = round(abs(edge), 1)

        if team_filter == 'top25' and not (stats['ra'] <= 25 or stats['rh'] <= 25): continue
        can_view = is_premium or (stats['ra'] > 100)

        action_val = "PLAY" if abs_edge >= 3.0 else "FADE"
        game_obj = {
            'Matchup': f"{a_name} @ {h_name}", 'ESPN_ID': eid,
            'Live_Score': f"{ld.get('s_away', 0)}-{ld.get('s_home', 0)}",
            'Raw_Pick': pick, 'Raw_Spread': p_spr_val,
            'Pick': pick.upper() if can_view else "LOCKED",
            'Pick_Spread': f"{p_spr_val:+g}" if (can_view and pick != "TBD") else "???",
            'Left_Proj': proj_l, 'Right_Proj': proj_r,
            'Left_Logo': ld.get('a_logo') or row.get('Away Logo'),
            'Right_Logo': ld.get('h_logo') or row.get('Home Logo'),
            'Edge': abs_edge, 'status': ld['status'], 'is_live': ld.get('state') == 'in',
            'Action': action_val, 'stats': stats, 'is_public': (stats['ra'] > 100)
        }
        if eid != "0" and ld.get('state') in ['in', 'post']:
            auto_log_game(game_obj, stats, ld, archive_sheet, col_p_values)
        all_games.append(game_obj)

    top_plays = sorted([g for g in all_games if g['Action'] == 'PLAY' and g['Raw_Pick'] != "TBD"],
                       key=lambda x: x['Edge'], reverse=True)
    seo = get_seo_metadata(top_plays[0] if top_plays else None)
    return render_template('index.html', games=all_games, top_plays=top_plays,
                           stats={"W": wins, "L": losses, "PCT": pct}, last_10=last_10, is_premium=is_premium, seo=seo)


# --- USER & AUTH ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pw = request.form.get('email').lower(), request.form.get('password')
        u = db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none()
        if u and check_password_hash(u.password, pw):
            login_user(u)
            return redirect(url_for('index'))
        flash("Invalid login.", "danger")
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email, password = request.form.get('email').lower(), request.form.get('password')
        if db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none():
            flash("Email exists.", "warning")
            return redirect(url_for('signup'))
        new_user = User(email=email, password=generate_password_hash(password, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        try:
            checkout_session = stripe.checkout.Session.create(
                line_items=[{'price': 'price_1Sw38dJ6FrWhUke2VG2oDzLY', 'quantity': 1}],
                mode='subscription',
                success_url=url_for('index', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=url_for('signup', _external=True),
                customer_email=email
            )
            return redirect(checkout_session.url, code=303)
        except Exception as e:
            return str(e)
    return render_template('subscribe.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("Successfully logged out.", "info")
    return redirect(url_for('login'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email').lower()
        user = db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none()
        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            db.session.commit()
            reset_url = url_for('reset_password', token=token, _external=True)
            try:
                resend.Emails.send({
                    "from": "Edge Engine Pro <auth@edgeenginepro.com>",
                    "to": email, "subject": "Reset Your Password",
                    "html": f"<p>Click <a href='{reset_url}'>here</a> to reset your password.</p>"
                })
            except Exception as e:
                print(f"Email Error: {e}")
        flash("If registered, a reset link has been sent.", "info")
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = db.session.execute(select(User).filter_by(reset_token=token)).scalar_one_or_none()
    if not user:
        flash("Invalid token.", "danger")
        return redirect(url_for('login'))
    if request.method == 'POST':
        user.password = generate_password_hash(request.form.get('password'), method='pbkdf2:sha256')
        user.reset_token = None
        db.session.commit()
        flash("Password updated!", "success")
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)


@app.route('/api/updates')
def ghost_updates():
    return jsonify(get_live_data())


@app.route('/set_parlay_size', methods=['POST'])
def set_size():
    session['team_count'] = request.json.get('count')
    return jsonify(success=True)


@app.route('/robots.txt')
def robots_txt():
    lines = ["User-agent: *", "Allow: /", "", "Sitemap: https://betifysports.com/sitemap.xml"]
    response = make_response("\n".join(lines))
    response.headers["Content-Type"] = "text/plain"
    return response


@app.route('/sitemap.xml')
def sitemap():
    today = datetime.now().strftime('%Y-%m-%d')
    pages = [
        {'loc': 'https://betifysports.com/', 'lastmod': today, 'priority': '1.0'},
        {'loc': 'https://betifysports.com/archive', 'lastmod': today, 'priority': '0.8'}
    ]
    sitemap_xml = render_template('sitemap_template.xml', pages=pages)
    response = make_response(sitemap_xml)
    response.headers["Content-Type"] = "application/xml"
    return response


# Create the app context and run migrations BEFORE the app starts
with app.app_context():
    db.create_all()
    try:
        # Check if reset_token exists, if not, add it
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS reset_token VARCHAR(100);'))
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS stripe_id VARCHAR(100);'))
        db.session.commit()
        print("Database columns verified/added successfully.")
    except Exception as e:
        db.session.rollback()
        print(f"Migration check error: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))