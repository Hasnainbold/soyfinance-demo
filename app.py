import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Set page config
st.set_page_config(layout="wide", page_title="SoyFinance - Soybean Warehouse Receipt Finance", page_icon="🌱")

# Custom CSS for Dark Theme & Glassmorphism
st.markdown("""
<style>
    /* Main background */
    .stApp {
        background-color: #0f172a;
        color: #f8fafc;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #1e293b;
    }
    
    /* Metric Cards */
    [data-testid="stMetricValue"] {
        color: #10b981;
    }
    .metric-card {
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 15px;
        backdrop-filter: blur(10px);
        margin-bottom: 1rem;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #f8fafc !important;
    }
    
    /* Dataframe tables */
    .stDataFrame {
        border-radius: 10px;
        overflow: hidden;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #1e293b;
        border-radius: 5px 5px 0 0;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #3b82f6;
        color: white;
    }
    
    /* Custom Colors */
    .text-green { color: #10b981; }
    .text-blue { color: #3b82f6; }
    .text-red { color: #ef4444; }
    .text-amber { color: #f59e0b; }
</style>
""", unsafe_allow_html=True)

# Define current date based on context (August 2026)
CURRENT_DATE = datetime(2026, 8, 2)

@st.cache_data
def format_inr(number):
    s, *d = str(int(number)).partition(".")
    r = ",".join([s[x-2:x] for x in range(-3, -len(s), -2)][::-1] + [s[-3:]])
    return f"₹ {r}"

# --- Data Generation ---
@st.cache_data
def generate_farmers():
    np.random.seed(42)
    first_names = ['Rajesh', 'Suresh', 'Amit', 'Vijay', 'Ramesh', 'Mukesh', 'Gopal', 'Hari', 'Shiv', 'Ram']
    last_names = ['Patidar', 'Chouhan', 'Yadav', 'Sharma', 'Patel', 'Jain', 'Singh', 'Deshmukh', 'Kadam']
    return [f"{np.random.choice(first_names)} {np.random.choice(last_names)}" for _ in range(50)]

@st.cache_data
def generate_warehouse_receipts():
    np.random.seed(42)
    farmers = generate_farmers()
    locations = ['Indore', 'Ujjain', 'Dewas', 'Khandwa', 'Latur', 'Nagpur', 'Akola']
    grades = ['A', 'B', 'C']
    statuses = ['Pending', 'Approved', 'Active', 'Repaid', 'Defaulted']
    
    data = []
    for i in range(1, 101):
        qty = np.random.randint(50, 500)
        market_val = qty * np.random.randint(4600, 4900)
        loan_amt = int(market_val * np.random.uniform(0.7, 0.8))
        
        deposit_date = CURRENT_DATE - timedelta(days=np.random.randint(1, 120))
        
        data.append({
            'Receipt ID': f"WR-2026-{1000+i}",
            'Farmer Name': np.random.choice(farmers),
            'Location': np.random.choice(locations),
            'Quantity (qtl)': qty,
            'Grade': np.random.choice(grades, p=[0.6, 0.3, 0.1]),
            'Deposit Date': deposit_date.strftime("%Y-%m-%d"),
            'Market Value': market_val,
            'Loan Amount': loan_amt,
            'Status': np.random.choice(statuses, p=[0.15, 0.1, 0.5, 0.2, 0.05]),
            'Risk Score': np.random.randint(10, 85),
            'Tenure (days)': 90
        })
    return pd.DataFrame(data)

@st.cache_data
def generate_price_history():
    np.random.seed(42)
    start_date = CURRENT_DATE - timedelta(days=5*365)
    dates = pd.date_range(start=start_date, end=CURRENT_DATE, freq='D')
    
    # Base price rising over 5 years
    base_price = np.linspace(4000, 4800, len(dates))
    
    # Seasonal effect (Kharif pattern)
    # Peak around Jun-Aug, Low around Nov-Dec
    day_of_year = dates.dayofyear
    seasonal = np.sin((day_of_year - 150) / 365 * 2 * np.pi) * 400 + 200
    
    # Noise
    noise = np.random.normal(0, 100, len(dates))
    
    prices = base_price + seasonal + noise
    
    # MSP Data
    msp = np.linspace(4200, 4892, len(dates)) # Simplified MSP curve
    
    volumes = np.random.randint(10000, 100000, len(dates))
    # Volume spikes during harvest
    harvest_mask = (dates.month >= 10) & (dates.month <= 12)
    volumes[harvest_mask] = volumes[harvest_mask] * 2
    
    return pd.DataFrame({
        'Date': dates,
        'Price': prices.astype(int),
        'MSP': msp.astype(int),
        'Volume': volumes
    })

@st.cache_data
def generate_price_prediction():
    np.random.seed(42)
    dates = pd.date_range(start=CURRENT_DATE, periods=90, freq='D')
    current_price = 4850
    
    # Simulation based on harvest pressure starting in Sep/Oct
    trend = np.linspace(0, -300, 90) # Prices drop as harvest approaches
    noise = np.random.normal(0, 50, 90)
    
    predicted = current_price + trend + noise
    upper_bound = predicted + np.linspace(50, 200, 90)
    lower_bound = predicted - np.linspace(50, 200, 90)
    
    return pd.DataFrame({
        'Date': dates,
        'Predicted Price': predicted.astype(int),
        'Upper Bound': upper_bound.astype(int),
        'Lower Bound': lower_bound.astype(int)
    })

@st.cache_data
def generate_investors():
    np.random.seed(42)
    first_names = ['Anand', 'Prakash', 'Sanjay', 'Rahul', 'Neha', 'Priya', 'Kiran', 'Nitin']
    last_names = ['Rathi', 'Oswal', 'Motilal', 'Jhunjhunwala', 'Damani', 'Biyani', 'Kotak']
    return [f"{np.random.choice(first_names)} {np.random.choice(last_names)}" for _ in range(20)]

@st.cache_data
def generate_bids(receipts_df):
    np.random.seed(42)
    investors = generate_investors()
    bids = []
    
    active_receipts = receipts_df[receipts_df['Status'].isin(['Pending', 'Approved'])].head(20)
    
    for _, row in active_receipts.iterrows():
        num_bids = np.random.randint(1, 5)
        for _ in range(num_bids):
            bids.append({
                'Receipt ID': row['Receipt ID'],
                'Investor Name': np.random.choice(investors),
                'Bid Amount': row['Loan Amount'],
                'Interest Rate (%)': round(np.random.uniform(10.5, 16.0), 2),
                'Status': np.random.choice(['Pending', 'Accepted', 'Rejected'], p=[0.6, 0.2, 0.2])
            })
    return pd.DataFrame(bids)

@st.cache_data
def get_buyers():
    return pd.DataFrame([
        {"Company": "Ruchi Soya (Patanjali)", "Location": "Indore", "Product": "Soy Oil", "Price Offered (₹/qtl)": 4900, "Demand (MT)": 500, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Adani Wilmar", "Location": "Ahmedabad", "Product": "Soy Oil", "Price Offered (₹/qtl)": 4850, "Demand (MT)": 1000, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Cargill India", "Location": "Gurgaon", "Product": "Soy Meal Export", "Price Offered (₹/qtl)": 4920, "Demand (MT)": 800, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "ITC Agri Business", "Location": "Kolkata", "Product": "Soybean", "Price Offered (₹/qtl)": 4880, "Demand (MT)": 600, "Rating": "⭐⭐⭐⭐"},
        {"Company": "Sonic Biochem", "Location": "Indore", "Product": "Soy Lecithin", "Price Offered (₹/qtl)": 4870, "Demand (MT)": 200, "Rating": "⭐⭐⭐⭐"},
        {"Company": "Prestige Industries", "Location": "Indore", "Product": "Soy Oil", "Price Offered (₹/qtl)": 4840, "Demand (MT)": 300, "Rating": "⭐⭐⭐"},
        {"Company": "Vippy Industries", "Location": "Dewas", "Product": "Soy Meal", "Price Offered (₹/qtl)": 4830, "Demand (MT)": 400, "Rating": "⭐⭐⭐⭐"},
        {"Company": "NAFED", "Location": "Delhi", "Product": "MSP Procurement", "Price Offered (₹/qtl)": 4892, "Demand (MT)": 5000, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Mahakosh (Ruchi)", "Location": "Haldia", "Product": "Soy Oil", "Price Offered (₹/qtl)": 4860, "Demand (MT)": 450, "Rating": "⭐⭐⭐⭐"},
        {"Company": "Kirti Gold", "Location": "Latur", "Product": "Soy Oil", "Price Offered (₹/qtl)": 4910, "Demand (MT)": 250, "Rating": "⭐⭐⭐⭐"},
        {"Company": "Maharashtra Solvents", "Location": "Dhule", "Product": "Soy Meal", "Price Offered (₹/qtl)": 4800, "Demand (MT)": 150, "Rating": "⭐⭐⭐"},
        {"Company": "Godrej Agrovet", "Location": "Mumbai", "Product": "Animal Feed", "Price Offered (₹/qtl)": 4790, "Demand (MT)": 700, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Suguna Foods", "Location": "Coimbatore", "Product": "Poultry Feed", "Price Offered (₹/qtl)": 4820, "Demand (MT)": 900, "Rating": "⭐⭐⭐⭐⭐"},
        {"Company": "Aviagen", "Location": "Pune", "Product": "Poultry Feed", "Price Offered (₹/qtl)": 4810, "Demand (MT)": 350, "Rating": "⭐⭐⭐⭐"},
        {"Company": "Bunge India", "Location": "Rajpura", "Product": "Edible Oil", "Price Offered (₹/qtl)": 4875, "Demand (MT)": 600, "Rating": "⭐⭐⭐⭐⭐"}
    ])

# Load Data
receipts_df = generate_warehouse_receipts()
price_hist_df = generate_price_history()
price_pred_df = generate_price_prediction()
bids_df = generate_bids(receipts_df)
buyers_df = get_buyers()

# --- Sidebar ---
st.sidebar.markdown("<h1 style='text-align: center; color: #10b981;'>🌱 SoyFinance</h1>", unsafe_allow_html=True)
st.sidebar.markdown("<p style='text-align: center; color: #94a3b8;'>Warehouse Receipt Finance</p>", unsafe_allow_html=True)
st.sidebar.divider()

page = st.sidebar.radio("Navigation", 
    ["📊 Dashboard", "📋 Warehouse Receipts & Loans", "📈 Price Trends & Prediction", 
     "💰 Investor Bid Board", "🏪 Buyer Marketplace", "⚠️ Risk Assessment"]
)

st.sidebar.divider()
st.sidebar.markdown("""
<div style='font-size: 0.8rem; color: #64748b; text-align: center;'>
    System Status: <span style='color: #10b981;'>Online</span><br>
    Date: 02 Aug 2026<br>
    Version: 1.0.4-demo
</div>
""", unsafe_allow_html=True)

# --- Pages ---

if page == "📊 Dashboard":
    st.title("Platform Dashboard")
    
    # KPIs
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Receipts", len(receipts_df))
    with col2:
        total_loan = receipts_df[receipts_df['Status'] == 'Active']['Loan Amount'].sum()
        st.metric("Active Loans Value", format_inr(total_loan))
    with col3:
        st.metric("Avg Soybean Price", "₹ 4,850/qtl", "+1.2%")
    with col4:
        st.metric("Active Investors", len(bids_df['Investor Name'].unique()))
    with col5:
        st.metric("Registered Buyers", len(buyers_df))
        
    st.divider()
    
    col_chart, col_table = st.columns([2, 1])
    
    with col_chart:
        st.subheader("Recent Price Trend (30 Days)")
        recent_prices = price_hist_df.tail(30)
        fig = px.area(recent_prices, x='Date', y='Price', 
                      title="Mandi Average Price",
                      color_discrete_sequence=['#10b981'],
                      template='plotly_dark')
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig, use_container_width=True)
        
    with col_table:
        st.subheader("Platform Status")
        status_counts = receipts_df['Status'].value_counts().reset_index()
        status_counts.columns = ['Status', 'Count']
        fig2 = px.pie(status_counts, values='Count', names='Status', hole=0.7,
                     color_discrete_sequence=['#3b82f6', '#10b981', '#f59e0b', '#64748b', '#ef4444'],
                     template='plotly_dark')
        fig2.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig2, use_container_width=True)
        
    st.subheader("Recent Warehouse Receipts")
    st.dataframe(receipts_df.sort_values(by='Deposit Date', ascending=False).head(5), use_container_width=True, hide_index=True)


elif page == "📋 Warehouse Receipts & Loans":
    st.title("Warehouse Receipts & Loans")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.multiselect("Filter by Status", receipts_df['Status'].unique(), default=receipts_df['Status'].unique())
    with col2:
        loc_filter = st.multiselect("Filter by Location", receipts_df['Location'].unique(), default=receipts_df['Location'].unique())
    with col3:
        risk_filter = st.slider("Max Risk Score", 0, 100, 100)
        
    filtered_df = receipts_df[
        (receipts_df['Status'].isin(status_filter)) &
        (receipts_df['Location'].isin(loc_filter)) &
        (receipts_df['Risk Score'] <= risk_filter)
    ]
    
    # Styling the dataframe
    def color_status(val):
        color = 'white'
        if val == 'Active': color = '#10b981'
        elif val == 'Pending': color = '#f59e0b'
        elif val == 'Defaulted': color = '#ef4444'
        elif val == 'Approved': color = '#3b82f6'
        return f'color: {color}'
        
    st.dataframe(
        filtered_df.style.map(color_status, subset=['Status']),
        use_container_width=True, 
        hide_index=True,
        column_config={
            "Market Value": st.column_config.NumberColumn("Market Value", format="₹ %d"),
            "Loan Amount": st.column_config.NumberColumn("Loan Amount", format="₹ %d"),
            "Risk Score": st.column_config.ProgressColumn("Risk Score", min_value=0, max_value=100)
        }
    )
    
    with st.expander("➕ Simulate New Receipt Creation"):
        st.markdown("### Farmer Soybean Deposit")
        f_col1, f_col2, f_col3 = st.columns(3)
        f_col1.text_input("Farmer Name")
        f_col2.selectbox("Mandi Location", ['Indore', 'Ujjain', 'Dewas', 'Latur'])
        f_col3.number_input("Quantity (Quintals)", min_value=10)
        if st.button("Generate Warehouse Receipt & Assess Loan"):
            st.success("Receipt WR-2026-NEW created successfully! Risk assessment pending.")


elif page == "📈 Price Trends & Prediction":
    st.title("Soybean Market Intelligence")
    
    tabs = st.tabs(["Historical Trends", "Price Prediction (90 Days)", "Weather & Mandi Data"])
    
    with tabs[0]:
        st.subheader("5-Year Historical Price Trend (₹/Quintal)")
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=price_hist_df['Date'], y=price_hist_df['Price'], name='Market Price', line=dict(color='#3b82f6', width=2)))
        fig.add_trace(go.Scatter(x=price_hist_df['Date'], y=price_hist_df['MSP'], name='Govt MSP', line=dict(color='#f59e0b', width=2, dash='dash')))
        
        fig.update_layout(
            template='plotly_dark',
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(rangeslider=dict(visible=True), type="date"),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
        
    with tabs[1]:
        st.subheader("Price Prediction (Next 90 Days)")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=price_pred_df['Date'], y=price_pred_df['Upper Bound'], fill=None, mode='lines', line_color='rgba(255,255,255,0)', showlegend=False))
            fig.add_trace(go.Scatter(x=price_pred_df['Date'], y=price_pred_df['Lower Bound'], fill='tonexty', mode='lines', line_color='rgba(255,255,255,0)', fillcolor='rgba(59, 130, 246, 0.2)', name='Confidence Interval'))
            fig.add_trace(go.Scatter(x=price_pred_df['Date'], y=price_pred_df['Predicted Price'], mode='lines', name='Predicted Price', line=dict(color='#3b82f6', width=3)))
            
            # Add current date line
            fig.add_vline(x=CURRENT_DATE, line_width=2, line_dash="dash", line_color="red")
            
            fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
            
        with col2:
            st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
            st.metric("30-Day Forecast", "₹ 4,750", "-100", delta_color="inverse")
            st.metric("60-Day Forecast", "₹ 4,620", "-230", delta_color="inverse")
            st.metric("90-Day Forecast", "₹ 4,550", "-300", delta_color="inverse")
            st.markdown("</div>", unsafe_allow_html=True)
            
        st.subheader("Market Drivers")
        d_col1, d_col2, d_col3, d_col4, d_col5 = st.columns(5)
        d_col1.info("🌧️ Monsoon Forecast\n\n**Normal (98% LPA)**")
        d_col2.warning("📉 Global CBOT\n\n**Bearish Trend**")
        d_col3.success("📈 Domestic Demand\n\n**High (Soy Meal)**")
        d_col4.info("🏛️ Govt MSP\n\n**₹ 4,892/qtl**")
        d_col5.warning("🏭 Inventory Levels\n\n**Above Average**")

    with tabs[2]:
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Weather Impact Analysis")
            st.markdown("""
            *Simulated forecast for MP/Maharashtra Soybean Belt*
            - **Rainfall Deviation:** +5% (Normal)
            - **Temperature:** 28°C - 32°C (Optimal)
            - **Pest Alert:** Low risk of Yellow Mosaic Virus
            """)
            st.success("Overall Weather Risk: NEUTRAL TO POSITIVE for crop yield. (May put downward pressure on prices)")
            
        with col2:
            st.subheader("Current Mandi Prices (Today)")
            mandi_data = pd.DataFrame([
                {"Mandi": "Indore", "Price (₹)": 4850, "vs MSP": "-0.8%"},
                {"Mandi": "Latur", "Price (₹)": 4900, "vs MSP": "+0.1%"},
                {"Mandi": "Ujjain", "Price (₹)": 4780, "vs MSP": "-2.2%"},
                {"Mandi": "Dewas", "Price (₹)": 4820, "vs MSP": "-1.4%"},
                {"Mandi": "Nagpur", "Price (₹)": 4810, "vs MSP": "-1.6%"},
                {"Mandi": "Akola", "Price (₹)": 4770, "vs MSP": "-2.5%"},
                {"Mandi": "Khandwa", "Price (₹)": 4750, "vs MSP": "-2.9%"}
            ])
            st.dataframe(mandi_data, hide_index=True, use_container_width=True)


elif page == "💰 Investor Bid Board":
    st.title("Investor Marketplace")
    st.markdown("Bid on high-quality warehouse receipts backed by physical soybean collateral.")
    
    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown("### Portfolio Summary")
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("Total Invested", format_inr(2500000))
        st.metric("Expected Annual Return", "14.5%")
        st.metric("Active Bids", len(bids_df[bids_df['Status'] == 'Pending']))
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Risk distribution pie
        risk_dist = pd.DataFrame({'Risk Tier': ['Low', 'Medium', 'High'], 'Allocation': [60, 30, 10]})
        fig = px.pie(risk_dist, values='Allocation', names='Risk Tier', hole=0.5, 
                     color='Risk Tier', color_discrete_map={'Low':'#10b981', 'Medium':'#f59e0b', 'High':'#ef4444'},
                     template='plotly_dark')
        fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=200, plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
        
    with col2:
        st.subheader("Active Bidding Opportunities")
        opportunities = receipts_df[receipts_df['Status'] == 'Pending'].copy()
        opportunities = opportunities[['Receipt ID', 'Location', 'Quantity (qtl)', 'Market Value', 'Loan Amount', 'Risk Score']]
        st.dataframe(opportunities.head(10), use_container_width=True, hide_index=True)
        
        st.subheader("Recent Bids on Platform")
        
        def color_bid_status(val):
            color = 'white'
            if val == 'Accepted': color = '#10b981'
            elif val == 'Pending': color = '#f59e0b'
            elif val == 'Rejected': color = '#ef4444'
            return f'color: {color}'
            
        st.dataframe(bids_df.style.map(color_bid_status, subset=['Status']), use_container_width=True, hide_index=True)


elif page == "🏪 Buyer Marketplace":
    st.title("Buyer Offtake Marketplace")
    st.markdown("Verified institutional buyers ready to purchase soybean at loan maturity.")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        prod_filter = st.selectbox("Product Requirement", ["All"] + list(buyers_df['Product'].unique()))
    with col2:
        min_price = st.number_input("Minimum Price (₹/qtl)", value=4700)
    with col3:
        loc_filter = st.selectbox("Buyer Location", ["All"] + list(buyers_df['Location'].unique()))
        
    filtered_buyers = buyers_df.copy()
    if prod_filter != "All": filtered_buyers = filtered_buyers[filtered_buyers['Product'] == prod_filter]
    if loc_filter != "All": filtered_buyers = filtered_buyers[filtered_buyers['Location'] == loc_filter]
    filtered_buyers = filtered_buyers[filtered_buyers['Price Offered (₹/qtl)'] >= min_price]
    
    st.dataframe(filtered_buyers, use_container_width=True, hide_index=True)
    
    st.markdown("### 🤝 Smart Matching Engine")
    sample_receipt = receipts_df.iloc[0]
    st.info(f"Looking for matches for **{sample_receipt['Receipt ID']}** ({sample_receipt['Quantity (qtl)']} qtl at {sample_receipt['Location']})")
    
    st.success("**Top Match:** Adani Wilmar (Ahmedabad) - Offers ₹4,850/qtl. Logistics cost: ₹150/qtl. Net realization: ₹4,700/qtl.")
    st.success("**Alternative:** Ruchi Soya (Indore) - Offers ₹4,900/qtl. Logistics cost: ₹250/qtl. Net realization: ₹4,650/qtl.")


elif page == "⚠️ Risk Assessment":
    st.title("Risk Assessment Engine")
    
    selected_receipt = st.selectbox("Select Receipt for Risk Analysis", receipts_df['Receipt ID'])
    receipt_data = receipts_df[receipts_df['Receipt ID'] == selected_receipt].iloc[0]
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        risk_score = receipt_data['Risk Score']
        
        # Gauge chart
        fig = go.Figure(go.Indicator(
            mode = "gauge+number",
            value = risk_score,
            title = {'text': "Overall Risk Score"},
            gauge = {
                'axis': {'range': [0, 100]},
                'bar': {'color': "white"},
                'steps': [
                    {'range': [0, 30], 'color': "#10b981"},
                    {'range': [30, 70], 'color': "#f59e0b"},
                    {'range': [70, 100], 'color': "#ef4444"}
                ],
            }
        ))
        fig.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', height=300)
        st.plotly_chart(fig, use_container_width=True)
        
        if risk_score <= 30:
            st.success("🟢 LOW RISK: Auto-approve. Suggested Interest: 12%")
        elif risk_score <= 70:
            st.warning("🟡 MEDIUM RISK: Manual review. Suggested Interest: 15%")
        else:
            st.error("🔴 HIGH RISK: Enhanced diligence required. Suggested Interest: 18%+")

    with col2:
        st.subheader("Risk Breakdown")
        
        # Simulate risk components based on overall score
        price_risk = risk_score * 0.4
        weather_risk = risk_score * 0.2
        quality_risk = risk_score * 0.2
        market_risk = risk_score * 0.1
        credit_risk = risk_score * 0.1
        
        risk_components = pd.DataFrame({
            'Category': ['Price Risk', 'Weather Risk', 'Quality/Storage Risk', 'Market/Demand Risk', 'Credit/Farmer Risk'],
            'Score': [price_risk, weather_risk, quality_risk, market_risk, credit_risk],
            'Weight': ['30%', '25%', '15%', '15%', '15%']
        })
        
        fig2 = px.bar(risk_components, x='Score', y='Category', orientation='h', color='Score', 
                      color_continuous_scale=['#10b981', '#f59e0b', '#ef4444'])
        fig2.update_layout(template='plotly_dark', plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig2, use_container_width=True)
        
    st.divider()
    st.markdown("### Risk Mitigation Strategies in Place")
    st.markdown("""
    - **Mark-to-Market Calls:** Triggered if price drops by >15%.
    - **Insurance:** 100% warehouse stock insured against fire, theft, and natural perils.
    - **Quality Assaying:** Independent NABL accredited labs perform quality checks every 30 days.
    - **Forward Contracts:** 40% of platform receipts are pre-matched with buyers using forward contracts.
    """)
