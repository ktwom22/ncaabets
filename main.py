import os
import io
import requests
import pandas as pd
from flask import Flask, render_template, request, Response, send_file
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_FILE = 'archive.csv'
ADMIN_USER = "admin"
ADMIN_PASS = "winning123"
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/2.png&w=80&h=80"


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_USER and auth.password == ADMIN_PASS):
            return Response('Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)

    return decorated


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None']: return default
        return float(val)
    except:
        return default


def get_completed_games():
    url = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    try:
        res = requests.get(url, params={'groups': '50', 'limit': '100'}, timeout=5).json()
        completed = []
        for event in res.get('events', []):
            if event['status']['type']['description'] == "Final":
                teams = event['competitions'][0]['competitors']
                a_name = teams[1]['team']['displayName'].upper()
                h_name = teams[0]['team']['displayName'].upper()
                completed.append({
                    "matchup": f"{teams[1]['team']['shortDisplayName']} @ {teams[0]['team']['shortDisplayName']}",
                    "score": f"{teams[1]['score']} - {teams[0]['score']}",
                    "winner": a_name if int(teams[1]['score']) > int(teams[0]['score']) else h_name,
                    "search_pool": [a_name, h_name, teams[1]['team']['shortDisplayName'].upper(),
                                    teams[0]['team']['shortDisplayName'].upper()],
                    "espn_id": event['id']
                })
        return completed
    except:
        return []


@app.route('/')
def index():
    try:
        res = requests.get(f"{SHEET_URL}&cb={datetime.now().timestamp()}", timeout=5)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        df.columns = df.columns.str.strip()
    except:
        return "<h1>Sheet Connection Error</h1>"

    all_games, active_picks = [], []
    for _, row in df.iterrows():
        # DATA EXTRACTION
        g_time = row.get('Game Time', 'N/A')
        a_team, h_team = row.get('Away Team'), row.get('Home Team')
        fd_spread = clean_val(row.get('FD Spread'))
        fd_total = clean_val(row.get('FD Total'))
        r_away, r_home = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        ppg_a, ppg_h = clean_val(row.get('PPG Away'), 70), clean_val(row.get('PPG Home'), 70)
        ppga_a, ppga_h = clean_val(row.get('PPGA Away'), 70), clean_val(row.get('PPGA Home'), 70)

        # PROJECTION LOGIC
        a_p = 71.0 + ((182 - r_away) / 18.0) + (((ppg_a - 72) + (72 - ppga_h)) * 0.4)
        h_p = 71.0 + ((182 - r_home) / 18.0) + (((ppg_h - 72) + (72 - ppga_a)) * 0.4) + 3.2

        edge = (a_p - h_p) - fd_spread
        status = "AWAY" if edge > 1.5 else "HOME" if edge < -1.5 else "NONE"
        pick_team = a_team if status == "AWAY" else h_team if status == "HOME" else None

        a_logo = row.get('Away Logo', DEFAULT_LOGO)
        h_logo = row.get('Home Logo', DEFAULT_LOGO)

        game_data = {
            'time': g_time, 'away': a_team, 'home': h_team, 'a_p': round(a_p, 1), 'h_p': round(h_p, 1),
            'edge': abs(round(edge, 1)), 'status': status, 'pick': pick_team, 'a_logo': a_logo, 'h_logo': h_logo,
            'a_v': fd_spread * -1, 'h_v': fd_spread, 'total': fd_total,
            'r_a': r_away, 'r_h': r_home, 'ppg_a': ppg_a, 'ppg_h': ppg_h, 'ppga_a': ppga_a, 'ppga_h': ppga_h
        }
        all_games.append(game_data)
        if pick_team: active_picks.append(game_data)

    # ARCHIVING
    scores = get_completed_games()
    headers = ["Date", "Game_Time", "Pick", "Result", "Final_Score", "Away_Team", "Home_Team",
               "Proj_Away", "Proj_Home", "FD_Spread", "FD_Total", "Rank_A", "Rank_H",
               "PPG_A", "PPG_H", "PPGA_A", "PPGA_H", "ESPN_ID"]

    if not os.path.exists(ARCHIVE_FILE):
        pd.DataFrame(columns=headers).to_csv(ARCHIVE_FILE, index=False)

    history_df = pd.read_csv(ARCHIVE_FILE)
    for s in scores:
        for p in active_picks:
            p_name = str(p['pick']).upper()
            if any(p_name in name for name in s['search_pool']):
                outcome = "WIN" if p_name in s['winner'] else "LOSS"
                s['result_label'] = outcome
                if str(s['espn_id']) not in history_df['ESPN_ID'].astype(str).values:
                    new_row = pd.DataFrame([{
                        "Date": datetime.now().strftime('%Y-%m-%d'), "Game_Time": p['time'],
                        "Pick": p['pick'], "Result": outcome, "Final_Score": s['score'],
                        "Away_Team": p['away'], "Home_Team": p['home'],
                        "Proj_Away": p['a_p'], "Proj_Home": p['h_p'],
                        "FD_Spread": p['h_v'], "FD_Total": p['total'],
                        "Rank_A": p['r_a'], "Rank_H": p['r_h'],
                        "PPG_A": p['ppg_a'], "PPG_H": p['ppg_h'],
                        "PPGA_A": p['ppga_a'], "PPGA_H": p['ppga_h'], "ESPN_ID": s['espn_id']
                    }])
                    new_row.to_csv(ARCHIVE_FILE, mode='a', header=False, index=False)
                    history_df = pd.concat([history_df, new_row], ignore_index=True)

    daily_df = history_df[history_df['Date'] == datetime.now().strftime('%Y-%m-%d')]
    stats = {'wins': len(daily_df[daily_df['Result'] == 'WIN']), 'total': len(daily_df),
             'rate': round((len(daily_df[daily_df['Result'] == 'WIN']) / len(daily_df) * 100), 1) if len(
                 daily_df) > 0 else 0}

    parlay = sorted([g for g in all_games if g['status'] != 'NONE'], key=lambda x: x['edge'], reverse=True)[:3]
    return render_template('index.html', games=all_games, parlay=parlay, scores=scores, stats=stats)


@app.route('/private')
@requires_auth
def private_archive():
    if not os.path.exists(ARCHIVE_FILE): return "<h1>Vault empty.</h1>"
    df = pd.read_csv(ARCHIVE_FILE)
    wins = len(df[df['Result'] == 'WIN'])
    stats = {'wins': wins, 'losses': len(df) - wins, 'rate': round((wins / len(df) * 100), 1) if len(df) > 0 else 0}
    return render_template('private.html', stats=stats, records=df.tail(100).to_dict('records'))


@app.route('/download')
@requires_auth
def download_vault():
    return send_file(ARCHIVE_FILE, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True)