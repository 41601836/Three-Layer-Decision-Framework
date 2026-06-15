# monday_warfare/web_app.py
import streamlit as st
import pandas as pd
from strategy import run_strategy
from db_setup import init_monday_tables

st.set_page_config(page_title="周一战法 · 精选池", layout="wide")
st.title("📈 周波段 · 周一战法 v1.2 小账户生存适配版")

# 确保数据表存在
init_monday_tables()

with st.sidebar:
    st.header("⚙️ 参数设置")
    risk = st.selectbox("风险偏好", ["保守", "稳健", "激进"], index=1)
    num = st.slider("精选数量", 1, 5, 3)
    account = st.number_input("账户总资产（元）", min_value=10000, value=150000, step=10000)
    if st.button("🚀 开始周一战法分析"):
        try:
            with st.spinner("正在分析市场环境、筛选标的..."):
                result = run_strategy(risk_profile=risk, num_stocks=num, account_size=account)
            st.session_state['result'] = result
        except Exception as e:
            st.error(f"分析出错: {str(e)}")

if 'result' in st.session_state:
    res = st.session_state.result
    if res.get('status') == 'skip':
        st.error(f"⚠️ {res['message']}")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("市场环境", res['env_rating'])
        col2.metric("建议仓位上限", f"{res['position_limit']*100:.0f}%")
        sent = res.get('sentiment')
        if sent:
            col3.metric("连板率", f"{sent['boom_rate']*100:.0f}%")
        st.caption(f"数据截止日：{res['last_date']}")
        
        st.subheader("本周主线板块")
        st.write("、".join(res['top_sectors']))
        
        st.subheader("🎯 精选池")
        if res['picks']:
            picks_df = pd.DataFrame(res['picks'])
            picks_df['1手价格'] = picks_df['close'] * 100
            picks_df['占账户%'] = picks_df['1手价格'] / account * 100
            st.dataframe(picks_df[['ts_code','name','industry','close','1手价格','占账户%']], use_container_width=True)
            st.success("⚠️ 以上筛选结果仅供参考，不构成投资建议。请结合盘面实际情况执行。")
        else:
            st.warning("未筛选出符合条件的标的。")
else:
    st.info("👈 请在左侧设置参数并点击按钮开始分析")