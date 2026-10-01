# -*- coding: utf-8 -*-
"""
缠论分析网页版（Streamlit）—— 由 daily_stock_analysis 仓库的 chan_analysis.py 改造
功能：
  1. 自由输入股票名称或代码（支持模糊匹配）
  2. 自由输入开始/结束时间（默认 2024-01-01 ~ 最新交易日）
  3. K线不足 600 根给出提醒
  4. ECharts 交互式K线图：dataZoom 滚轮缩放 + 滑块 + 双击复位
  5. czsc 缠论逻辑与 chan_analysis.py 完全一致（分型/笔/中枢/背驰/三类买卖点）
部署：Streamlit Community Cloud（免费，绑 GitHub 自动更新）
"""
import os
import time
import pandas as pd

import streamlit as st
from czsc import CZSC, RawBar, Freq
from czsc.utils.sig import get_zs_seq

# ==================== 页面基础配置 ====================
st.set_page_config(
    page_title="缠论分析 · 网页版",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_START = "2024-01-01"
MIN_K_LINES = 600  # 用户要求的提醒阈值


# ==================== 数据获取（与 chan_analysis.py 一致） ====================
def get_bars(code, kind, start, end):
    """获取K线：yfinance(Yahoo,海外可用) -> akshare东财 -> akshare新浪
    个股后缀：6xx->.SS，0xx/3xx->.SZ；指数用映射表
    """
    import akshare as ak
    last_err = None

    def norm_rename(df):
        return df.rename(columns={
            "日期": "dt", "date": "dt", "Date": "dt",
            "开盘": "open", "open": "open", "Open": "open",
            "收盘": "close", "close": "close", "Close": "close",
            "最高": "high", "high": "high", "High": "high",
            "最低": "low", "low": "low", "Low": "low",
            "成交量": "vol", "volume": "vol", "Volume": "vol",
            "成交额": "amount", "amount": "amount",
        })

    def to_bars(df):
        df = norm_rename(df)
        need = ["dt", "open", "close", "high", "low", "vol"]
        if not all(c in df.columns for c in need):
            raise ValueError(f"列不完整: {list(df.columns)}")
        bars = []
        for i, (_, row) in enumerate(df.iterrows()):
            bars.append(RawBar(
                symbol=code, id=i, dt=pd.Timestamp(row["dt"]), freq=Freq.D,
                open=float(row["open"]), close=float(row["close"]),
                high=float(row["high"]), low=float(row["low"]),
                vol=float(row["vol"]), amount=float(row.get("amount", 0) or 0),
            ))
        if len(bars) < 10:
            raise ValueError(f"K线不足({len(bars)})")
        return bars, df

    # 1) yfinance (Yahoo) —— 海外服务器首选
    try:
        import yfinance as yf
        yf_code = None
        if kind == "index":
            idx_map = {"000001": "000001.SS", "399001": "399001.SZ",
                       "399006": "399006.SZ", "000300": "000300.SS", "000016": "000016.SS"}
            yf_code = idx_map.get(code)
        else:
            yf_code = code + (".SS" if code.startswith("6") else ".SZ")
        if yf_code:
            df = yf.download(yf_code, start=start, end=end,
                             auto_adjust=False, progress=False, threads=False, timeout=20)
            if df is not None and not df.empty:
                df = df.reset_index()
                if hasattr(df.columns, "get_level_values"):
                    df.columns = df.columns.get_level_values(0)
                return to_bars(df)
    except Exception as e:
        last_err = f"yfinance: {type(e).__name__}: {str(e)[:80]}"

    # 2) akshare 东财 -> 新浪（本地/国内可用）
    start_n = start.replace("-", "")
    end_n = end.replace("-", "")
    sources = []
    if kind == "index":
        sources = [
            ("index_zh_a_hist", lambda: ak.index_zh_a_hist(
                symbol=code, period="daily", start_date=start_n, end_date=end_n)),
            ("stock_zh_index_daily", lambda: ak.stock_zh_index_daily(
                symbol=("sh" if code.startswith("000") or code.startswith("60") else "sz") + code)),
        ]
    else:
        sources = [
            ("stock_zh_a_hist", lambda: ak.stock_zh_a_hist(
                symbol=code, period="daily", start_date=start_n, end_date=end_n, adjust="qfq")),
            ("stock_zh_a_daily", lambda: ak.stock_zh_a_daily(
                symbol=("sh" if code.startswith("6") else "sz") + code,
                start_date=start, adjust="qfq")),
        ]

    for name, fetcher in sources:
        try:
            df = fetcher()
            if df is None or df.empty:
                last_err = f"{name} 返回空数据"
                continue
            return to_bars(df)
        except Exception as e:
            last_err = f"{name}: {type(e).__name__}: {str(e)[:80]}"
            time.sleep(2)

    raise ConnectionError(f"所有数据源失败: {last_err}")


# ==================== 缠论判断（与 chan_analysis.py 一致） ====================
def check_bei_chi(c):
    """趋势/盘整背驰判断：比较最后两笔的波动幅度"""
    bis = c.bi_list
    if len(bis) < 5:
        return "笔数不足，无法判断背驰", False, False
    last_power = abs(bis[-1].high - bis[-1].low)
    prev_power = abs(bis[-2].high - bis[-2].low)
    if prev_power == 0 or last_power >= prev_power * 0.8:
        return "无背驰", False, False
    if bis[-1].direction.value == "向下":
        trend = len(bis) >= 7 and bis[-3].direction.value == "向下"
        return ("下跌趋势背驰（关注阶段底部）" if trend
                else "下跌盘整背驰（短线反弹机会）"), trend, not trend
    if bis[-1].direction.value == "向上":
        trend = len(bis) >= 7 and bis[-3].direction.value == "向上"
        return ("上涨趋势背驰（警惕阶段顶部）" if trend
                else "上涨盘整背驰（短线回调风险）"), trend, not trend
    return "无背驰", False, False


def get_points(c, zs_list):
    """识别一/二/三类买卖点"""
    bis = c.bi_list
    if len(bis) < 6 or not zs_list:
        return "数据不足，未识别买卖点"
    bc_msg, is_trend, _ = check_bei_chi(c)
    last = bis[-1]
    zs = zs_list[-1]
    msgs = []
    if is_trend and last.direction.value == "向下":
        msgs.append("一类买点")
    if is_trend and last.direction.value == "向上":
        msgs.append("一类卖点")
    if len(bis) >= 8:
        pre = bis[-2]
        if last.direction.value == "向上" and pre.low > zs.zd:
            msgs.append("二类买点")
        if last.direction.value == "向下" and pre.high < zs.zg:
            msgs.append("二类卖点")
    if last.direction.value == "向上" and last.low > zs.zg:
        msgs.append("三类买点")
    if last.direction.value == "向下" and last.high < zs.zd:
        msgs.append("三类卖点")
    return "、".join(msgs) if msgs else "无明显三类买卖点"


# ==================== 名称/代码解析 ====================
INDEX_MAP = {
    "000001": "上证指数", "399001": "深证指数", "399006": "创业板指",
    "000300": "沪深300", "000016": "上证50",
}
# 指数名称反向映射（输入名称也能识别指数）
INDEX_NAME_MAP = {v: (k, v, "index") for k, v in INDEX_MAP.items()}

# 常用股票内置兜底表（akshare 拉取失败时仍可解析常用标的）
FALLBACK_STOCKS = {
    "000725": "京东方A", "000651": "格力电器", "000625": "长安汽车",
    "601118": "海南橡胶", "002230": "科大讯飞", "000009": "中国宝安",
    "002465": "海格通信", "002936": "郑州银行", "601318": "中国平安",
    "000333": "美的集团", "002594": "比亚迪", "600519": "贵州茅台",
    "601398": "工商银行", "600036": "招商银行",
    "601988": "中国银行", "601857": "中国石油",
    "600900": "长江电力", "601899": "紫金矿业", "600030": "中信证券",
}


@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_map():
    """全量A股代码→名称映射（akshare，每日缓存）；失败返回内置兜底表"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()  # columns: code, name
        if df is not None and len(df) > 1000:
            return df
    except Exception:
        pass
    # 兜底：内置常用股票表
    df = pd.DataFrame(
        [{"code": c, "name": n} for c, n in FALLBACK_STOCKS.items()])
    return df


def _norm(s):
    """全角→半角归一化（akshare 名称里 '京东方Ａ' 是全角 Ａ）"""
    import unicodedata
    out = []
    for ch in str(s):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out).upper().replace(" ", "")


def resolve_symbol(user_input):
    """输入名称/代码 -> (code, name, kind)；找不到返回 None"""
    text = _norm(user_input)
    # 1) 指数名称直接命中（如"上证指数"）
    if text in INDEX_NAME_MAP:
        return INDEX_NAME_MAP[text]
    # 2) 指数代码命中（000001=上证指数；浦发银行请输入"浦发银行"或 600000）
    if text in INDEX_MAP:
        return text, INDEX_MAP[text], "index"
    # 3) 纯数字代码 → 先查股票表
    if text.isdigit() and len(text) == 6:
        df = get_stock_map()
        row = df[df["code"] == text]
        if not row.empty:
            return text, row.iloc[0]["name"], "stock"
        # 股票表没有（如新股未收录）→ 原样返回代码
        return text, text, "stock"
    # 4) 名称匹配（先精确，再包含；名称先归一化）
    df = get_stock_map()
    df = df.assign(norm_name=df["name"].map(_norm))
    exact = df[df["norm_name"] == text]
    if not exact.empty:
        row = exact.iloc[0]
        return row["code"], row["name"], "stock"
    contain = df[df["norm_name"].str.contains(text, case=False, regex=False)]
    if len(contain) == 1:
        row = contain.iloc[0]
        return row["code"], row["name"], "stock"
    if len(contain) > 1:
        # 有多个候选 → 返回候选列表由用户选
        cands = [(r["code"], r["name"]) for _, r in contain.head(20).iterrows()]
        return "multi", cands, "stock"
    return None


# ==================== ECharts 交互式K线图 ====================
def make_kline_option(bars, c, zs_list, name, code, pt_msg):
    """生成 ECharts candlestick option：分型/笔/中枢/买卖点 + dataZoom 缩放"""
    dts = [b.dt.strftime("%Y-%m-%d") for b in bars]
    kdata = [[round(b.open, 3), round(b.close, 3), round(b.low, 3), round(b.high, 3)]
             for b in bars]

    # 笔：连分型端点
    bi_line = []
    for bi in c.bi_list:
        try:
            xa = dts.index(bi.fx_a.dt.strftime("%Y-%m-%d"))
            xb = dts.index(bi.fx_b.dt.strftime("%Y-%m-%d"))
        except ValueError:
            continue
        bi_line.append([xa, round(bi.fx_a.fx, 3)])
        bi_line.append([xb, round(bi.fx_b.fx, 3)])

    # 分型标记
    fx_bottom, fx_top = [], []
    for fx in c.fx_list:
        try:
            xi = dts.index(fx.dt.strftime("%Y-%m-%d"))
        except ValueError:
            continue
        if fx.mark.value == "底分型":
            fx_bottom.append([xi, round(fx.fx, 3)])
        else:
            fx_top.append([xi, round(fx.fx, 3)])

    # 中枢矩形（markArea）
    zs_areas = []
    for zs in zs_list:
        try:
            x0 = dts.index(zs.sdt.strftime("%Y-%m-%d"))
            x1 = dts.index(zs.edt.strftime("%Y-%m-%d"))
        except ValueError:
            continue
        zs_areas.append([{
            "xAxis": x0, "yAxis": round(zs.zd, 3),
        }, {
            "xAxis": x1, "yAxis": round(zs.zg, 3),
        }])

    # 买卖点标注（最后一笔端点）
    markers = []
    if c.bi_list:
        last = c.bi_list[-1]
        if last.direction.value == "向上":
            markers.append({
                "name": "卖点", "coord": [dts.index(last.fx_b.dt.strftime("%Y-%m-%d")),
                                          round(last.fx_b.fx, 3)],
                "value": pt_msg, "itemStyle": {"color": "#ef232a"},
            })
        else:
            markers.append({
                "name": "买点", "coord": [dts.index(last.fx_b.dt.strftime("%Y-%m-%d")),
                                          round(last.fx_b.fx, 3)],
                "value": pt_msg, "itemStyle": {"color": "#14b143"},
            })

    option = {
        "animation": False,
        "backgroundColor": "#ffffff",
        "legend": {"data": ["K线", "笔", "底分型", "顶分型"],
                   "top": 4, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "toolbox": {
            "feature": {
                "dataZoom": {"yAxisIndex": "none"},
                "restore": {},
                "saveAsImage": {},
            },
        },
        "grid": [
            {"left": 50, "right": 20, "top": 34, "height": "58%"},
            {"left": 50, "right": 20, "top": "78%", "height": "12%"},
        ],
        "xAxis": [
            {"type": "category", "data": dts, "gridIndex": 0,
             "axisLabel": {"rotate": 45, "fontSize": 10}},
            {"type": "category", "gridIndex": 1,
             "axisLabel": {"show": False}, "axisTick": {"show": False}},
        ],
        "yAxis": [
            {"scale": True, "gridIndex": 0, "splitLine": {"show": False}},
            {"gridIndex": 1, "splitNumber": 2, "axisLabel": {"show": False},
             "axisLine": {"show": False}, "axisTick": {"show": False}, "splitLine": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "xAxisIndex": [0, 1], "start": 60, "end": 100},
            {"type": "slider", "xAxisIndex": [0, 1], "start": 60, "end": 100,
             "height": 18, "bottom": 6},
        ],
        "series": [
            {
                "name": "K线", "type": "candlestick", "data": kdata,
                "itemStyle": {"color": "#ef232a", "color0": "#14b143",
                              "borderColor": "#ef232a", "borderColor0": "#14b143"},
                "markArea": {
                    "silent": True,
                    "itemStyle": {"color": "rgba(249,168,37,0.15)", "borderColor": "#f9a825",
                                  "borderWidth": 1},
                    "data": zs_areas,
                },
                "markPoint": {
                    "symbol": "pin", "symbolSize": 46,
                    "label": {"fontSize": 9, "formatter": "{b}"},
                    "data": markers,
                },
            },
            {
                "name": "笔", "type": "line", "data": bi_line,
                "symbol": "none", "lineStyle": {"width": 1.6, "color": "#0e6efd"},
                "z": 3,
            },
            {
                "name": "底分型", "type": "scatter", "data": fx_bottom,
                "symbol": "triangle", "symbolSize": 9,
                "itemStyle": {"color": "#14b143"}, "z": 4,
            },
            {
                "name": "顶分型", "type": "scatter", "data": fx_top,
                "symbol": "triangle", "symbolRotate": 180, "symbolSize": 9,
                "itemStyle": {"color": "#ef232a"}, "z": 4,
            },
        ],
    }
    return option


# ==================== 主界面 ====================
st.title("📈 缠论分析 · 网页版")
st.caption("基于 czsc 0.9.51 · 数据源：akshare(东财) → yfinance 兜底 · 与 chan_analysis.py 逻辑一致")

with st.sidebar:
    st.header("⚙️ 分析参数")
    symbol_input = st.text_input(
        "股票名称 / 代码",
        placeholder="例：京东方A、000725、中国平安、601318",
    )
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        start_date = st.date_input("开始日期", value=pd.Timestamp(DEFAULT_START).date())
    with date_col2:
        end_date = st.date_input("结束日期", value=pd.Timestamp.today().date())
    analyze_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)

    st.divider()
    st.caption("提示：K线少于 600 根会提醒（默认 2024-01-01 至今约 660+ 个交易日，通常满足）")

if not symbol_input:
    st.info("👈 在左侧输入股票名称或代码，设置起止日期，点击「开始分析」")
    st.stop()

if not analyze_btn:
    st.info("👈 输入已完成，点击左侧「🚀 开始分析」运行缠论分析")
    st.stop()

if start_date >= end_date:
    st.error("开始日期必须早于结束日期")
    st.stop()

# ---- 解析输入 ----
resolved = resolve_symbol(symbol_input)
if resolved is None:
    st.error(f"未找到「{symbol_input}」对应的股票，请检查名称或直接输入 6 位代码（如 000725）")
    st.stop()
if resolved[0] == "multi":
    code, cands, kind = resolved
    st.warning(f"「{symbol_input}」匹配到多个结果，请选择：")
    labels = {f"{c} · {n}": c for c, n in cands}
    pick = st.selectbox("候选列表", list(labels.keys()))
    code, name, kind = labels[pick], pick.split(" · ")[1], "stock"
else:
    code, name, kind = resolved
    if name is None:
        # 纯代码输入，查名称
        try:
            df = get_stock_map()
            row = df[df["code"] == code]
            name = row.iloc[0]["name"] if not row.empty else code
        except Exception:
            name = code

st.subheader(f"🔍 {name}（{code}）　{kind.upper()}")

# ---- 拉取数据 ----
with st.status(f"正在获取 {name}（{code}）数据…", expanded=True) as status:
    st.write(f"区间：{start_date} ~ {end_date}")
    try:
        bars, df = get_bars(code, kind, str(start_date), str(end_date))
        status.update(label=f"✅ 数据获取成功：{len(bars)} 根K线", state="complete")
    except Exception as e:
        status.update(label=f"❌ 数据获取失败", state="error")
        st.error(f"{type(e).__name__}: {str(e)}")
        st.stop()

# ---- 600 根提醒 ----
if len(bars) < MIN_K_LINES:
    st.warning(f"⚠️ 当前仅 {len(bars)} 根 K 线，不足 {MIN_K_LINES} 根。"
               f"建议把开始日期提前（如 {DEFAULT_START}），以获得更充分的缠论结构。")
else:
    st.success(f"✅ K 线数量 {len(bars)} 根，满足 {MIN_K_LINES} 根要求。")

# ---- 缠论分析 ----
with st.status("正在进行缠论分析…", expanded=True) as status:
    c = CZSC(bars)
    zs_list = get_zs_seq(c.bi_list)
    bc_msg, _, _ = check_bei_chi(c)
    pt_msg = get_points(c, zs_list)
    status.update(label="✅ 缠论分析完成", state="complete")

# ---- 结果指标 ----
last_bi = c.bi_list[-1] if c.bi_list else None
bi_info = "无"
if last_bi:
    bi_info = (f"{last_bi.direction.value} | "
               f"{last_bi.sdt:%Y-%m-%d}~{last_bi.edt:%Y-%m-%d} | "
               f"低{last_bi.low:.2f}/高{last_bi.high:.2f}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("最新收盘价", f"{df.iloc[-1]['close']:.2f}")
m2.metric("有效K线数", f"{len(bars)}")
m3.metric("笔 / 中枢", f"{len(c.bi_list)} / {len(zs_list)}")
m4.metric("三类买卖点", pt_msg)

st.markdown(f"**最新一笔走势**：{bi_info}")
st.markdown(f"**缠论背驰判断**：{bc_msg}")
st.markdown(f"**三类买卖点识别**：{pt_msg}")

# ---- 交互式K线图 ----
st.subheader("📊 K线图（滚轮/滑块缩放 · 双击复位）")
try:
    from streamlit_echarts import st_echarts
    option = make_kline_option(bars, c, zs_list, name, code, pt_msg)
    st_echarts(options=option, height="620px")
except ImportError:
    st.warning("streamlit-echarts 未安装，图表降级显示。请安装后刷新。")
    st.dataframe(df.tail(20))
except Exception as e:
    st.error(f"图表渲染失败：{type(e).__name__}: {str(e)}")

st.divider()
with st.expander("📄 原始数据预览（最近 10 条）"):
    st.dataframe(df.tail(10))
