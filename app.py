# app.py
import streamlit as st
import pandas as pd
from src import process_data

# Load data directly from the processed cache (fast)
df = pd.read_csv("data/processed/activities.csv")

# Or run a specific calculation on the fly
stats = process_data.calculate_custom_metric(df)

st.title("Strava Dashboard")
st.metric("My Custom Metric", stats)
st.image("data/images/footer_stats.png")