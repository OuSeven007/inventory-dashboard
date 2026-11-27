import streamlit as st
import pandas as pd
from google.cloud import firestore
from google.oauth2 import service_account


# -------------------------
# CONFIG
# -------------------------
st.set_page_config(
    page_title="Inventory Dashboard",
    layout="wide"
)

# -------------------------
# FIRESTORE CONNECTION
# -------------------------
@st.cache_resource
def get_db():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"]
    )

    return firestore.Client(
        credentials=creds,
        project=st.secrets["gcp_service_account"]["project_id"]
    )


# -------------------------
# DATA LAYER
# Auto refresh every 12 hours
# -------------------------
@st.cache_data(ttl=7200)
def load_data():
    try:
        db = get_db()

        docs = db.collection("inventory").stream()

        data = []

        for doc in docs:
            d = doc.to_dict()

            data.append({
                "SKU": doc.id,
                "units per box": d.get("units_per_box", 0),
                "boxes": d.get("boxes", 0),
                "loose pcs": d.get("loose_units", 0),
                "pcs": d.get("total_units", 0),
                "reserved": d.get("reserved_units", 0),
                "incoming_stock": d.get("incoming_stock", 0),
                "Piece Cost": d.get("piece_cost", None),
            })

        df = pd.DataFrame(data)

        if df.empty:
            return df

        numeric_cols = [
            "boxes",
            "loose pcs",
            "units per box",
            "pcs",
            "reserved",
            "Piece Cost"
        ]

        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    except Exception as e:
        st.error(f"Error loading Firestore data: {e}")
        return pd.DataFrame()


# -------------------------
# SAVE COST CHANGES
# -------------------------
def update_piece_cost(sku, new_cost):
    try:
        db = get_db()

        db.collection("inventory").document(sku).update({
            "piece_cost": float(new_cost)
        })

    except Exception as e:
        st.error(f"Error updating {sku}: {e}")


# -------------------------
# PROCESSING
# -------------------------
def process_data(df):
    df = df.copy()

    if "Piece Cost" not in df.columns:
        df["Piece Cost"] = None

    df["COSG"] = df["pcs"].fillna(0) * df["Piece Cost"].fillna(0)

    missing_df = df[df["Piece Cost"].isna()].copy()
    clean_df = df[df["Piece Cost"].notna()].copy()

    return clean_df, missing_df


# -------------------------
# LOAD DATA
# -------------------------
raw_df = load_data()

if raw_df.empty:
    st.warning("No inventory data found in Firestore.")
    st.stop()

inventory_df, missing_df = process_data(raw_df)

# -------------------------
# SIDEBAR FILTERS
# -------------------------
st.sidebar.header("Filters")

# -------------------------
# SKU FILTER
# -------------------------
selected_skus = st.sidebar.multiselect(
    "Select SKU",
    options=raw_df["SKU"].dropna().unique()
)

# -------------------------
# COLUMN FILTER
# Choose which columns to display
# -------------------------
selected_columns = st.sidebar.multiselect(
    "Select Columns",
    options=raw_df.columns.tolist() + ["COSG"],
    default=raw_df.columns.tolist() + ["COSG"]
)

# -------------------------
# COST RANGE FILTER
# -------------------------
max_cost_value = (
    float(inventory_df["Piece Cost"].max())
    if not inventory_df.empty and inventory_df["Piece Cost"].notna().any()
    else 0.0
)

min_cost, max_cost = st.sidebar.slider(
    "Piece Cost Range",
    0.0,
    max_cost_value,
    (0.0, max_cost_value)
)


# -------------------------
# HEADER
# -------------------------
st.title("📦 CQNY Warehouse Inventory Dashboard")

# -------------------------
# MAIN TABLE HEADER + TOGGLE
# -------------------------
left_col, right_col = st.columns([6, 1])

with left_col:
    st.subheader("Inventory Data")

with right_col:
    cost_only = st.toggle(
        "Has COSG",
        value=False
    )

# -------------------------
# FILTERING
# -------------------------
if cost_only:
    filtered_df = inventory_df.copy()
else:
    filtered_df = raw_df.copy()
    filtered_df["COSG"] = (
        filtered_df["pcs"].fillna(0) *
        filtered_df["Piece Cost"].fillna(0)
    )

if selected_skus:
    filtered_df = filtered_df[
        filtered_df["SKU"].isin(selected_skus)
    ]

if "Piece Cost" in filtered_df.columns:
    filtered_df = filtered_df[
        (
            filtered_df["Piece Cost"].fillna(0) >= min_cost
        ) &
        (
            filtered_df["Piece Cost"].fillna(0) <= max_cost
        )
    ]

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
# APPLY COLUMN FILTER
# -------------------------
display_df = filtered_df.copy()

if selected_columns:
    valid_columns = [
        col for col in selected_columns
        if col in display_df.columns
    ]

    display_df = display_df[valid_columns]

# -------------------------
# MAIN TABLE
# -------------------------
st.dataframe(
    display_df,
    use_container_width=True
)


# -------------------------
# TOP ITEMS
# -------------------------
st.subheader("Top 5 Highest Value SKUs")

top_items = inventory_df.sort_values(
    by="COSG",
    ascending=False
).head(5)

st.dataframe(
    top_items,
    use_container_width=True
)

st.subheader("Low Stock Alert🚨")

low_stock = inventory_df[
    (inventory_df["pcs"] < 20) &
    (inventory_df["pcs"] >= 0)
]

st.dataframe(
    low_stock,
    use_container_width=True
)

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

        c1, c2 = st.columns([2, 1])

        c1.write(f"SKU: {sku}")

        new_cost = c2.number_input(
            f"Cost for {sku}",
            min_value=0.0,
            key=f"cost_{sku}"
        )

        if new_cost > 0:
            updates[sku] = new_cost

    if st.button("💾 Save Updates"):

        for sku, cost in updates.items():
            update_piece_cost(sku, cost)

        st.cache_data.clear()

        st.success("✅ Costs updated successfully!")
        st.rerun()

# -------------------------
# FOOTER
# -------------------------
st.markdown("---")
st.caption("Powered for CQNY Operations")

