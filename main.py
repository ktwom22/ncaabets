import streamlit as st
import pandas as pd
import io
import requests
from PIL import Image

# 1. PAGE CONFIG (Mobile optimization)
st.set_page_config(page_title="NCAAB Engine", layout="centered")  # 'centered' is better for mobile

SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTh9hxThF-cxBppDUYAPp0TMH3ckTHBIUqKcD2EhtBaVmbXx5SRiid9gJnVA1xQkQ9FPpiRMI9fhqDJ/pub?gid=1766494058&single=true&output=csv"


# 2. DATA LOADING (Prevents the "Jump" when refreshing)
@st.cache(ttl=600, allow_output_mutation=True, show_spinner=False)
def load_data():
    try:
        res = requests.get(SHEET_URL)
        df = pd.read_csv(io.StringIO(res.content.decode('utf-8')))
        # Numeric cleanup
        cols = ['Rank Away', 'Rank Home', 'PPG Away', 'PPG Home', 'PPGA Away', 'PPGA Home', 'FD Spread', 'FD Total']
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        return df
    except:
        return pd.DataFrame()


def get_logo(url):
    try:
        res = requests.get(url, stream=True, timeout=3)
        return Image.open(res.raw)
    except:
        return None


def predict_outcome(row):
    base = 71.0
    a_proj = base + ((182 - row['Rank Away']) / 18.0) + (((row['PPG Away'] - 72) + (72 - row['PPGA Home'])) * 0.4)
    h_proj = base + ((182 - row['Rank Home']) / 18.0) + (((row['PPG Home'] - 72) + (72 - row['PPGA Away'])) * 0.4) + 3.2
    return round(a_proj, 1), round(h_proj, 1)


# --- MAIN UI ---
df = load_data()

st.title("🏀 NCAAB Value Engine")
st.markdown("---")

if not df.empty:
    for _, row in df.iterrows():
        a_proj, h_proj = predict_outcome(row)
        h_odds = row['FD Spread']
        a_odds = h_odds * -1
        m_spread = a_proj - h_proj
        diff = m_spread - h_odds

        # Determine Verdict
        away_covers = diff > 1.5
        home_covers = diff < -1.5

        # MOBILE CARD UI
        with st.expander(f"{row['Away Team']} @ {row['Home Team']}", expanded=True):
            # Create a 3-column layout that scales well
            c1, c2, c3 = st.columns([1, 1, 1])

            with c1:  # AWAY
                logo_a = get_logo(row['Away Logo'])
                if logo_a: st.image(logo_a, width=50)
                st.write(f"**{row['Away Team']}**")
                st.write(f"Proj: {a_proj}")
                st.caption(f"Odds: {a_odds:+.1f}")
                if away_covers: st.success("🎯 PICK")

            with c2:  # CENTER INFO
                st.markdown(
                    "<div style='text-align: center; border-left: 1px solid #333; border-right: 1px solid #333;'>",
                    unsafe_allow_html=True)
                st.caption("MODEL LINE")
                st.write(f"**{m_spread:+.1f}**")
                st.caption("O/U")
                st.write(f"{a_proj + h_proj:.1f}")
                st.markdown("</div>", unsafe_allow_html=True)

            with c3:  # HOME
                logo_h = get_logo(row['Home Logo'])
                if logo_h: st.image(logo_h, width=50)
                st.write(f"**{row['Home Team']}**")
                st.write(f"Proj: {h_proj}")
                st.caption(f"Odds: {h_odds:+.1f}")
                if home_covers: st.success("🎯 PICK")