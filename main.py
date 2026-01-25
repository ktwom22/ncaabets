import os, io, requests, pandas as pd
from flask import Flask, render_template, request, Response, send_file
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# --- SECURE CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_PATH = os.path.join(os.getcwd(), 'protected_archive', 'ncaa_results.csv')
os.makedirs(os.path.dirname(ARCHIVE_PATH), exist_ok=True)

ADMIN_USER, ADMIN_PASS = "admin", "winning123"
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/2.png"


# --- AUTH ---
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_USER and auth.password == ADMIN_PASS):
            return Response('Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)

    return decorated


# --- HELPERS ---
def normalize(name):
    if not name: return ""
    name = str(name).upper().replace(".", "").replace("'", "").strip()
    for word in ["STATE", "UNIV", "UNIVERSITY", "ST", "NC", "TECH", "A&M"]:
        name = name.replace(f" {word}", "")
    return name.strip()


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None', 'nan']: return default
        return float(val)
    except:
        return default


# --- ROUTES ---

@app.route('/protected/download')
@requires_auth
def download():
    return send_file(ARCHIVE_PATH, as_attachment=True)


@app.route('/')
def index():
    try:
        res = requests.get(f"{SHEET_URL}&cb={datetime.now().timestamp()}", timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except:
        return "<h1>Sheet Sync Failed</h1>"

    all_games = []

    # 1. Process EVERY stat into the all_games list
    for _, row in df.iterrows():
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan': continue

        h_spr = clean_val(row.get('FD Spread'))
        a_spr = -h_spr
        ra, rh = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        # Projections
        ap = 71.0 + ((182 - ra) / 18.0) + (((pa - 72) + (72 - pgh)) * 0.4)
        hp = 71.0 + ((182 - rh) / 18.0) + (((ph - 72) + (72 - pga)) * 0.4) + 3.2
        model_line = round(ap - hp, 1)

        edge_to_away = model_line - a_spr
        pick, pick_spr, edge = (a, a_spr, abs(edge_to_away)) if edge_to_away > 0 else (h, h_spr, abs(edge_to_away))

        all_games.append({
            'away': a, 'home': h, 'a_p': round(ap, 1), 'h_p': round(hp, 1),
            'edge': round(edge, 1), 'pick': pick.upper(), 'pick_spr': pick_spr,
            'instruction': f"TAKE {pick} {'+' if pick_spr > 0 else ''}{pick_spr}",
            'conf': "elite" if edge >= 6 else "solid" if edge >= 3 else "low",
            'a_logo': str(row.get('Away Logo')) if str(row.get('Away Logo')) != 'nan' else DEFAULT_LOGO,
            'h_logo': str(row.get('Home Logo')) if str(row.get('Home Logo')) != 'nan' else DEFAULT_LOGO,
            'a_rank': int(ra), 'h_rank': int(rh), 'a_ppg': pa, 'a_ppga': pga, 'h_ppg': ph, 'h_ppga': pgh,
            'time': row.get('Game Time', 'TBD')
        })

    # 2. Scoreboard & Filtering
    my_picks_normalized = [normalize(g['pick']) for g in all_games]
    live_scores = []
    try:
        espn = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
            params={'groups': '50', 'limit': '350'}, timeout=5).json()
        for event in espn.get('events', []):
            status = event['status']['type']['description']
            comp = event['competitions'][0]
            teams = comp['competitors']
            names = [t['team']['displayName'].upper() for t in teams]

            if any(normalize(n) in my_picks_normalized for n in names):
                away = next(t for t in teams if t['homeAway'] == 'away')
                home = next(t for t in teams if t['homeAway'] == 'home')
                winner = ""
                our_res = ""

                if "Final" in status:
                    win_obj = teams[0] if teams[0].get('winner') else teams[1]
                    winner = win_obj['team']['displayName'].upper()
                    for p in all_games:
                        if normalize(p['away']) in [normalize(n) for n in names] or normalize(p['home']) in [
                            normalize(n) for n in names]:
                            our_res = "WIN" if normalize(p['pick']) in normalize(winner) else "LOSS"

                live_scores.append({
                    "id": str(event['id']), "status": status, "is_final": "Final" in status,
                    "score": f"{away['score']}-{home['score']}",
                    "away_name": away['team']['displayName'].upper(),
                    "home_name": home['team']['displayName'].upper(),
                    "our_result": our_res
                })
    except:
        pass

    # 3. Persistence: The "EVERYTHING" Archive Logic
    # Define columns to match your exact request
    cols = ["Date", "Matchup", "Result", "Final_Score", "Pick", "Pick_Spread", "Proj_Away", "Proj_Home", "Edge",
            "Away_PPG", "Away_PPGA", "Home_PPG", "Home_PPGA", "Away_Rank", "Home_Rank", "ESPN_ID"]

    if not os.path.exists(ARCHIVE_PATH):
        pd.DataFrame(columns=cols).to_csv(ARCHIVE_PATH, index=False)

    archive_df = pd.read_csv(ARCHIVE_PATH)
    for s in [x for x in live_scores if x['is_final']]:
        if str(s['id']) not in archive_df['ESPN_ID'].astype(str).values:
            for p in all_games:
                if normalize(p['away']) == normalize(s['away_name']) or normalize(p['home']) == normalize(
                        s['home_name']):
                    new_row = {
                        "Date": datetime.now().strftime('%Y-%m-%d'),
                        "Matchup": f"{p['away']} @ {p['home']}",
                        "Result": s['our_result'],
                        "Final_Score": s['score'],
                        "Pick": p['pick'],
                        "Pick_Spread": p['pick_spr'],
                        "Proj_Away": p['a_p'],
                        "Proj_Home": p['h_p'],
                        "Edge": p['edge'],
                        "Away_PPG": p['a_ppg'],
                        "Away_PPGA": p['a_ppga'],
                        "Home_PPG": p['h_ppg'],
                        "Home_PPGA": p['h_ppga'],
                        "Away_Rank": p['a_rank'],
                        "Home_Rank": p['h_rank'],
                        "ESPN_ID": s['id']
                    }
                    pd.DataFrame([new_row]).to_csv(ARCHIVE_PATH, mode='a', header=False, index=False)
                    break

    # 4. Smart Box Calculation
    archive_df = pd.read_csv(ARCHIVE_PATH)  # Reload to include new games
    stats = {"W": len(archive_df[archive_df['Result'] == 'WIN']), "L": len(archive_df[archive_df['Result'] == 'LOSS'])}
    stats["PCT"] = round((stats["W"] / (stats["W"] + stats["L"]) * 100), 1) if (stats["W"] + stats["L"]) > 0 else 0

    return render_template('index.html', games=all_games, live_scores=live_scores, stats=stats)


if __name__ == '__main__':
    app.run(debug=True)