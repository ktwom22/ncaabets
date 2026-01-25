import os
import io
import requests
import pandas as pd
from flask import Flask, render_template, request, Response
from datetime import datetime
from functools import wraps

app = Flask(__name__)

# --- CONFIGURATION ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
ARCHIVE_FILE = 'archive.csv'

ADMIN_USER = "admin"
ADMIN_PASS = "winning123"


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
    params = {'groups': '50', 'limit': '100'}
    try:
        res = requests.get(url, params=params, timeout=5).json()
        completed = []
        for event in res.get('events', []):
            if event['status']['type']['description'] == "Final":
                teams = event['competitions'][0]['competitors']
                a_name, h_name = teams[1]['team']['displayName'].upper(), teams[0]['team']['displayName'].upper()
                a_short, h_short = teams[1]['team']['shortDisplayName'].upper(), teams[0]['team'][
                    'shortDisplayName'].upper()
                winner = a_name if int(teams[1]['score']) > int(teams[0]['score']) else h_name
                completed.append({
                    "matchup": f"{teams[1]['team']['shortDisplayName']} @ {teams[0]['team']['shortDisplayName']}",
                    "score": f"{teams[1]['score']} - {teams[0]['score']}",
                    "winner": winner,
                    "search_pool": [a_name, a_short, h_name, h_short],
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
        r_away, r_home = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        h_vegas = clean_val(row.get('FD Spread'), 0.0)
        a_p = 71.0 + ((182 - r_away) / 18.0) + (
                    ((clean_val(row.get('PPG Away'), 70) - 72) + (72 - clean_val(row.get('PPGA Home'), 70))) * 0.4)
        h_p = 71.0 + ((182 - r_home) / 18.0) + (((clean_val(row.get('PPG Home'), 70) - 72) + (
                    72 - clean_val(row.get('PPGA Away'), 70))) * 0.4) + 3.2

        edge = (a_p - h_p) - h_vegas
        status = "AWAY" if edge > 1.5 else "HOME" if edge < -1.5 else "NONE"
        pick_team = row.get('Away Team') if status == "AWAY" else row.get('Home Team') if status == "HOME" else None

        game_data = {'away': row.get('Away Team'), 'home': row.get('Home Team'), 'a_p': round(a_p, 1),
                     'h_p': round(h_p, 1), 'edge': abs(round(edge, 1)), 'status': status, 'pick': pick_team,
                     'a_logo': row.get('Away Logo', ''), 'h_logo': row.get('Home Logo', ''), 'a_v': h_vegas * -1,
                     'h_v': h_vegas}
        all_games.append(game_data)
        if pick_team:
            active_picks.append(game_data)

    scores = get_completed_games()
    win_count, match_count = 0, 0

    if not os.path.exists(ARCHIVE_FILE):
        pd.DataFrame(columns=["Date", "Team", "Matchup", "Score", "Result", "ESPN_ID"]).to_csv(ARCHIVE_FILE,
                                                                                               index=False)

    history_df = pd.read_csv(ARCHIVE_FILE)

    for s in scores:
        for p in active_picks:
            p_name = str(p['pick']).upper()
            if any(p_name in name or name in p_name for name in s['search_pool']):
                match_count += 1
                outcome = "WIN" if (p_name in s['winner'] or s['winner'] in p_name) else "LOSS"
                if outcome == "WIN": win_count += 1
                s['result_label'] = outcome

                # --- SELECTIVE ARCHIVE LOGIC ---
                # Only save if we haven't saved it before AND it's a valid pick
                if str(s['espn_id']) not in history_df['ESPN_ID'].astype(str).values:
                    new_row = pd.DataFrame([{
                        "Date": datetime.now().strftime('%Y-%m-%d'),
                        "Team": p['pick'],
                        "Matchup": s['matchup'],
                        "Score": s['score'],
                        "Result": outcome,
                        "ESPN_ID": s['espn_id']
                    }])
                    new_row.to_csv(ARCHIVE_FILE, mode='a', header=False, index=False)
                    # Update local DF so we don't double-add in the same loop
                    history_df = pd.concat([history_df, new_row], ignore_index=True)

    stats = {'wins': win_count, 'total': match_count,
             'rate': round((win_count / match_count * 100), 1) if match_count > 0 else 0}
    parlay = sorted([g for g in all_games if g['status'] != 'NONE'], key=lambda x: x['edge'], reverse=True)[:3]
    return render_template('index.html', games=all_games, parlay=parlay, scores=scores, stats=stats)


@app.route('/private')
@requires_auth
def private_archive():
    if not os.path.exists(ARCHIVE_FILE):
        return "<h1>No history recorded yet.</h1>"
    df = pd.read_csv(ARCHIVE_FILE)
    wins = len(df[df['Result'] == 'WIN'])
    losses = len(df[df['Result'] == 'LOSS'])
    total = wins + losses
    stats = {'wins': wins, 'losses': losses, 'rate': round((wins / total * 100), 1) if total > 0 else 0}
    records = df.tail(50).to_dict('records')
    return render_template('private.html', stats=stats, records=records)


if __name__ == '__main__':
    app.run(debug=True)