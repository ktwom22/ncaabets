import os, io, requests, pandas as pd
from flask import Flask, render_template
from datetime import datetime

app = Flask(__name__)

# --- CONFIG ---
SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"
STATS_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1827029141&single=true&output=csv"


def normalize(name):
    if not name: return ""
    removals = ["STATE", "ST", "UNIVERSITY", "UNIV", "BLUE DEVILS", "TIGERS", "KNIGHTS",
                "WILDCATS", "BULLDOGS", "HORNETS", "DOLPHINS", "RATTLERS", "TEXANS", "SKYHAWKS"]
    name = str(name).upper()
    for r in removals: name = name.replace(r, "")
    return name.strip()


def clean_val(val, default=0.0):
    try:
        if pd.isna(val) or str(val).strip() in ['---', '', 'None', 'nan']: return default
        return float(val)
    except:
        return default


@app.route('/')
def index():
    # 1. FETCH ESPN LIVE FEED (Global D1)
    postponed_teams = []
    live_data_map = {}
    try:
        espn_res = requests.get(
            "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=50&limit=150",
            timeout=5).json()
        for event in espn_res.get('events', []):
            status_name = event['status']['type']['name']
            comp = event['competitions'][0]
            t_away = next(t for t in comp['competitors'] if t['homeAway'] == 'away')
            t_home = next(t for t in comp['competitors'] if t['homeAway'] == 'home')
            a_norm = normalize(t_away['team']['displayName'])

            if any(x in status_name for x in ["POSTPONED", "CANCELED"]):
                postponed_teams.append(a_norm)
                continue

            live_data_map[a_norm] = {
                "id": event['id'], "status": event['status']['type']['shortDetail'],
                "state": event['status']['type']['state'],
                "score_away": int(clean_val(t_away['score'])), "score_home": int(clean_val(t_home['score'])),
                "away_logo": t_away['team']['logo'], "home_logo": t_home['team']['logo']
            }
    except:
        pass

    # 2. FETCH SHEET DATA & GRID LOGIC
    try:
        res = requests.get(f"{SHEET_URL}&cb={datetime.now().timestamp()}", timeout=10)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
    except Exception as e:
        return f"<h1>Sync Error: {e}</h1>"

    all_games, final_sidebar = [], []
    for _, row in df.iterrows():
        a, h = str(row.get('Away Team', '')).strip(), str(row.get('Home Team', '')).strip()
        if not a or a.lower() == 'nan' or normalize(a) in postponed_teams: continue

        # --- THE PROJECTION MATH ---
        h_spr = clean_val(row.get('FD Spread'))
        ra, rh = clean_val(row.get('Rank Away'), 182), clean_val(row.get('Rank Home'), 182)
        pa, ph = clean_val(row.get('PPG Away')), clean_val(row.get('PPG Home'))
        pga, pgh = clean_val(row.get('PPGA Away')), clean_val(row.get('PPGA Home'))

        rank_gap = (ra - rh) * 0.18
        our_margin = -3.8 - rank_gap
        edge_raw = h_spr - our_margin

        pick, p_spr = (h, h_spr) if edge_raw > 0 else (a, -h_spr)
        edge = abs(edge_raw)

        # Calculate Expected Scores
        total = (pa + pgh + ph + pga) / 2
        hp = (total / 2) + (abs(our_margin) / 2)
        ap = (total / 2) - (abs(our_margin) / 2)
        if our_margin > 0: hp, ap = ap, hp

        game_obj = {
            'away': a, 'home': h, 'a_p': round(ap, 1), 'h_p': round(hp, 1),
            'edge': round(edge, 1), 'pick': pick.upper(), 'pick_spr': p_spr,
            'instruction': f"TAKE {pick} {'+' if p_spr > 0 else ''}{p_spr}",
            'conf': "elite" if edge >= 8 else "solid" if edge >= 4 else "low",
            'a_logo': str(row.get('Away Logo', '')), 'h_logo': str(row.get('Home Logo', '')),
            'time': row.get('Game Time', 'TBD'), 'a_rank': int(ra), 'h_rank': int(rh)
        }
        all_games.append(game_obj)

        # Build Sidebar
        s_entry = {
            "id": "0", "status": game_obj['time'], "is_final": False,
            "away_name": a[:12].upper(), "home_name": h[:12].upper(),
            "score_away": 0, "score_home": 0, "away_logo": game_obj['a_logo'],
            "home_logo": game_obj['h_logo'], "our_pick": pick.upper(), "our_result": ""
        }
        if normalize(a) in live_data_map:
            lv = live_data_map[normalize(a)]
            res_label = ""
            if lv['state'] == "post":
                margin = lv['score_home'] - lv['score_away']
                is_win = (margin + h_spr) > 0 if pick.upper() == h.upper() else ((-margin) - h_spr) > 0
                res_label = "WIN" if is_win else "LOSS"
            s_entry.update({"id": lv['id'], "status": lv['status'], "is_final": (lv['state'] == "post"),
                            "score_away": lv['score_away'], "score_home": lv['score_home'],
                            "away_logo": lv['away_logo'], "home_logo": lv['home_logo'], "our_result": res_label})
        final_sidebar.append(s_entry)

    # 3. STATS & SEO
    wins, losses, pct = 0, 0, 0
    try:
        h_df = pd.read_csv(io.StringIO(requests.get(STATS_URL).content.decode('utf-8')))
        valid = h_df[~h_df['Result'].str.contains('POSTPONED|CANCELED', na=False, case=False)]
        wins = len(valid[valid['Result'].str.contains('WIN', na=False, case=False)])
        losses = len(valid[valid['Result'].str.contains('LOSS', na=False, case=False)])
        pct = round((wins / (wins + losses) * 100), 1) if (wins + losses) > 0 else 0
    except:
        pass

    top = max(all_games, key=lambda x: x['edge']) if all_games else None
    seo = {"title": "Edge Engine Pro", "desc": "CBB Model"}
    if top:
        seo["title"] = f"{top['away']} vs {top['home']} (+{top['edge']} Edge)"
        seo["desc"] = f"Prediction: {top['away']} {top['a_p']} - {top['home']} {top['h_p']}. Rec: {top['instruction']}."

    return render_template('index.html', games=all_games, live_scores=final_sidebar,
                           stats={"W": wins, "L": losses, "PCT": pct}, seo=seo)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)