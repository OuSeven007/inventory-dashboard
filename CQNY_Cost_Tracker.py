import streamlit as st
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# -------------------------
# CONFIG
# -------------------------
st.set_page_config(page_title="Inventory Dashboard", layout="wide")

SPREADSHEET_ID = "1j9KOsjrBjnY63r2ZTodrB5HXkHrk4t5vH18wbn-gkOk"
RANGE_NAME = "new_inv!A:H"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# -------------------------
# GOOGLE SHEETS CONNECTION
# -------------------------
def get_service():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)

# -------------------------
# DATA LAYER
# -------------------------
@st.cache_data(ttl=300)
def load_data():
    try:
        service = get_service()

        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()

        values = result.get("values", [])

        if not values:
            return pd.DataFrame()

        df = pd.DataFrame(values[1:], columns=values[0])

        # Normalize column names (important for spaces)
        df.columns = df.columns.str.strip()

        # Convert numeric columns
        numeric_cols = ["packed", "boxes", "loose pcs", "pcs", "Piece Cost"]

        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame()


def save_data(df):
    try:
        service = get_service()

        values = [df.columns.tolist()] + df.values.tolist()

        body = {"values": values}

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="new_inv!A1",
            valueInputOption="RAW",
            body=body
        ).execute()

    except Exception as e:
        st.error(f"Error saving data: {e}")


# -------------------------
# PROCESSING
# -------------------------
def process_data(df):
    df = df.copy()

    # Recalculate COSG always (ignore sheet value)
    df["COSG"] = df["pcs"] * df["Piece Cost"]

    missing_df = df[df["Piece Cost"].isna()].copy()
    clean_df = df[df["Piece Cost"].notna()].copy()

    return clean_df, missing_df


# -------------------------
# UPDATE LOGIC
# -------------------------
def update_piece_cost(df, sku, new_cost):
    df.loc[df["SKU"] == sku, "Piece Cost"] = new_cost
    return df


# -------------------------
# LOAD DATA
# -------------------------
raw_df = load_data()

if raw_df.empty:
    st.warning("No data found in Google Sheet.")
    st.stop()

inventory_df, missing_df = process_data(raw_df)

# -------------------------
# SIDEBAR FILTERS
# -------------------------
st.sidebar.header("Filters")

selected_skus = st.sidebar.multiselect(
    "Select SKU",
    options=inventory_df["SKU"].dropna().unique()
)

max_cost_value = float(inventory_df["Piece Cost"].max()) if not inventory_df.empty else 0.0

min_cost, max_cost = st.sidebar.slider(
    "Piece Cost Range",
    0.0,
    max_cost_value,
    (0.0, max_cost_value)
)

# -------------------------
# FILTERING
# -------------------------
filtered_df = inventory_df.copy()

if selected_skus:
    filtered_df = filtered_df[filtered_df["SKU"].isin(selected_skus)]

filtered_df = filtered_df[
    (filtered_df["Piece Cost"] >= min_cost) &
    (filtered_df["Piece Cost"] <= max_cost)
]

# -------------------------
# REMOVE AMAZON SKU FROM UI ONLY
# -------------------------
display_df = filtered_df.drop(columns=["Amazon Sku"], errors="ignore")

# -------------------------
# HEADER
# -------------------------
st.title("📦 CQNY Warehouse Inventory Dashboard")

# -------------------------
# KPIs
# -------------------------
col1, col2, col3 = st.columns(3)

col1.metric(
    "Total Inventory Value",
    f"${inventory_df['COSG'].sum():,.2f}"
)

col2.metric(
    "Filtered Value",
    f"${filtered_df['COSG'].sum():,.2f}"
)

col3.metric(
    "Missing Costs",
    missing_df.shape[0]
)

# -------------------------
# ALERTS
# -------------------------
if not missing_df.empty:
    st.warning("⚠️ Some SKUs are missing piece costs!")

# -------------------------
# MAIN TABLE
# -------------------------
st.subheader("Inventory Data")
st.dataframe(display_df, use_container_width=True)

# -------------------------
# TOP ITEMS
# -------------------------
st.subheader("Top 5 Highest Value SKUs")

top_items = inventory_df.sort_values(
    by="COSG", ascending=False
).head(5)

top_items_display = top_items.drop(columns=["Amazon Sku"], errors="ignore")

st.dataframe(top_items_display, use_container_width=True)

# -------------------------
# FIX MISSING COSTS
# -------------------------
st.subheader("🛠 Fix Missing Costs")

if missing_df.empty:
    st.success("✅ No missing costs!")
else:
    updates = {}

    for _, row in missing_df.iterrows():
        sku = row["SKU"]

        col1, col2 = st.columns([2, 1])
        col1.write(f"SKU: {sku}")

        new_cost = col2.number_input(
            f"Cost for {sku}",
            min_value=0.0,
            key=f"cost_{sku}"
        )

        if new_cost > 0:
            updates[sku] = new_cost

    if st.button("💾 Save Updates"):
        for sku, cost in updates.items():
            raw_df = update_piece_cost(raw_df, sku, cost)

        save_data(raw_df)
        st.cache_data.clear()

        st.success("✅ Costs updated successfully!")
        st.rerun()

# -------------------------
# FOOTER
# -------------------------
st.markdown("---")
st.caption("Inventory Dashboard • Built with Streamlit")
