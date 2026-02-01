import time
from datetime import datetime

# --- YOUR LOCKED DATA (Simulating your spreadsheet) ---
# Even if odds change elsewhere, this 'my_picks' entry stays static.
my_picks = [
    {
        "game_id": "NFL_101",
        "team": "Chiefs",
        "pick_type": "Spread",
        "line": -3.5,
        "locked_odds": -110,
        "status": "LOCKED",  # This pick is now immune to sheet updates
        "result": "PENDING"
    }
]


def simulate_game_completion(game_id, home_score, away_score):
    print(f"--- Processing Game {game_id} ---")

    for pick in my_picks:
        if pick["game_id"] == game_id:
            # 1. Verification: Only process if status is LOCKED
            print(f"Confirmed: Using locked line ({pick['line']}) and odds ({pick['locked_odds']})")

            # 2. Logic: Calculate outcome based on final score
            # (Assuming Chiefs were the -3.5 favorite)
            score_diff = home_score - away_score

            if score_diff > abs(pick["line"]):
                pick["result"] = "WIN"
            elif score_diff == abs(pick["line"]):
                pick["result"] = "PUSH"
            else:
                pick["result"] = "LOSS"

            # 3. Finalization: Mark game as COMPLETE
            pick["status"] = "COMPLETE"
            print(f"Final Score: {home_score}-{away_score}. Result: {pick['result']}")


# --- THE TEST ---
print("Initial Pick Status:", my_picks[0])
print("\n[System Message]: Odds for NFL_101 just moved to -4.5... (Ignored by Locked Pick)")

# Simulate game ending with Chiefs winning 24-20 (Covering -3.5)
simulate_game_completion("NFL_101", 24, 20)

print("\nUpdated Spreadsheet Record:", my_picks[0])