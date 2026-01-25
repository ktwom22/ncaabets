import os, io, requests, pandas as pd
from flask import Flask, render_template, request, Response, send_file
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# --- SECURE CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
# Stores the data in a dedicated folder to keep it hidden
ARCHIVE_PATH = os.path.join(os.getcwd(), 'hidden_learning_data', 'ncaa_archive.csv')
os.makedirs(os.path.dirname(ARCHIVE_PATH), exist_ok=True)

ADMIN_USER, ADMIN_PASS = "admin", "winning123"
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/2.png"


# --- AUTHENTICATION ---
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_USER and auth.password == ADMIN_PASS):
            return Response('Access Denied', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)

    return decorated


# --- ULTRA FUZZY MATCHING ---
def normalize(name):
    if not name: return ""
    name = str(name).upper().replace(".", "").replace("'", "").strip()
    for word in ["STATE", "UNIV", "UNIVERSITY", "ST", "NC", "TECH", "TECHNOLOGY", "A&M"]:
        name = name.replace(f" {word}", "")
    return name.strip()


def is_fuzzy_match(pick_name, espn_names):
    p_norm = normalize(pick_name)
    for e_name in espn_names:
        e_norm = normalize(e_name)
        if p_norm in e_norm or e_norm in p_norm: return True
    return False


# --- DATA HELPERS ---
def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None', 'nan']: return default
        return float(val)
    except:
        return default


def get_live_scoreboard():
    url = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    try:
        res = requests.get(url, params={'groups': '50', 'limit': '350'}, timeout=5).json()
        games_list = []
        for event in res.get('events', []):
            status = event['status']['type']['description']
            comp = event['competitions'][0]
            teams = comp['competitors']
            away = next(t for t in teams if t['homeAway'] == 'away')
            home = next(t for t in teams if t['homeAway'] == 'home')
            winner = ""
            if "Final" in status:
                win_obj = teams[0] if teams[0].get('winner') else teams[1]
                winner = win_obj['team']['displayName'].upper()
            games_list.append({
                "espn_id": str(event['id']), "status": status, "is_final": "Final" in status,
                "score": f"{away['score']}-{home['score']}", "winner": winner,
                "away_name": away['team']['displayName'].upper(), "home_name": home['team']['displayName'].upper(),
                "names": [t['team']['displayName'].upper() for t in teams]
            })
        return games_list
    except:
        return []


# --- ROUTES ---

@app.route('/private/secure-download')
@requires_auth
def download():
    if not os.path.exists(ARCHIVE_PATH): return "Archive is empty."
    return send_file(ARCHIVE_PATH, as_attachment=True)


@app.route('/')
def index():
    try:
        res = requests.get(f"{SHEET_URL}&cb={datetime.now().timestamp()}", timeout=8)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except:
        return "<h1>Sheet Sync Error</h1>"

    all_games, live_scores = [], get_live_scoreboard()

    for _, row in df.iterrows():
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        # Stats & Spread Logic
        h_spr = clean_val(row.get('FD Spread'))
        a_spr = -h_spr
        ra, rh = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        pa, ph = clean_val(row.get('PPG Away'), 72.0), clean_val(row.get('PPG Home'), 72.0)
        pga, pgh = clean_val(row.get('PPGA Away'), 72.0), clean_val(row.get('PPGA Home'), 72.0)

        # Projections
        ap = 71.0 + ((182 - ra) / 18.0) + (((pa - 72) + (72 - pgh)) * 0.4)
        hp = 71.0 + ((182 - rh) / 18.0) + (((ph - 72) + (72 - pga)) * 0.4) + 3.2
        model_line = round(ap - hp, 1)

        edge_to_away = model_line - a_spr
        pick, pick_spr, edge = (a, a_spr, abs(edge_to_away)) if edge_to_away > 0 else (h, h_spr, abs(edge_to_away))

        g_data = {
            'time': row.get('Game Time', 'TBD'), 'away': a, 'home': h, 'a_p': round(ap, 1), 'h_p': round(hp, 1),
            'edge': round(edge, 1), 'pick': pick.upper(), 'pick_spr': pick_spr,
            'instruction': f"TAKE {pick} {'+' if pick_spr > 0 else ''}{pick_spr}",
            'conf': "elite" if edge >= 6 else "solid" if edge >= 3 else "low",
            'a_logo': str(row.get('Away Logo')) if str(row.get('Away Logo')) != 'nan' else DEFAULT_LOGO,
            'h_logo': str(row.get('Home Logo')) if str(row.get('Home Logo')) != 'nan' else DEFAULT_LOGO,
            'a_rank': int(ra), 'h_rank': int(rh), 'a_ppg': pa, 'a_ppga': pga, 'h_ppg': ph, 'h_ppga': pgh
        }
        all_games.append(g_data)

    # Persistence Layer
    if not os.path.exists(ARCHIVE_PATH):
        pd.DataFrame(
            columns=["Date", "Matchup", "Pick", "Spread", "Edge", "A_PPG", "A_PPGA", "H_PPG", "H_PPGA", "Result",
                     "Score", "ESPN_ID"]).to_csv(ARCHIVE_PATH, index=False)

    archive_df = pd.read_csv(ARCHIVE_PATH)
    for s in [x for x in live_scores if x['is_final']]:
        if str(s['espn_id']) not in archive_df['ESPN_ID'].astype(str).values:
            for p in all_games:
                if is_fuzzy_match(p['pick'], s['names']):
                    win_norm, pick_norm = normalize(s['winner']), normalize(p['pick'])
                    res = "WIN" if (pick_norm in win_norm or win_norm in pick_norm) else "LOSS"
                    new_entry = {
                        "Date": datetime.now().strftime('%Y-%m-%d'), "Matchup": f"{p['away']} @ {p['home']}",
                        "Pick": p['pick'], "Spread": p['pick_spr'], "Edge": p['edge'],
                        "A_PPG": p['a_ppg'], "A_PPGA": p['a_ppga'], "H_PPG": p['h_ppg'], "H_PPGA": p['h_ppga'],
                        "Result": res, "Score": s['score'], "ESPN_ID": s['espn_id']
                    }
                    pd.DataFrame([new_entry]).to_csv(ARCHIVE_PATH, mode='a', header=False, index=False)
                    break

    return render_template('index.html', games=all_games, live_scores=live_scores)


if __name__ == '__main__':
    app.run(debug=True)