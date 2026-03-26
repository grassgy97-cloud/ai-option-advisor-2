from sqlalchemy import text
from app.core.db import SessionLocal

# 只保留静态描述性信息，不含任何IV数值
UNDERLYING_KNOWLEDGE = {
    "510050": {
        "name": "上证50ETF",
        "exchange": "SH",
        "lot_size": 10000,
        "liquidity": "high",
        "characteristics": "流动性最好，机构参与度高，定价效率高，套利空间相对小",
        "suitable_strategies": ["parity_arb", "calendar_arb", "vertical_spread"],
    },
    "510300": {
        "name": "沪深300ETF",
        "exchange": "SH",
        "lot_size": 10000,
        "liquidity": "high",
        "characteristics": "最主流标的，定价效率较高，近年市场成熟化后跨期套利空间收窄",
        "suitable_strategies": ["parity_arb", "calendar_arb", "vertical_spread", "calendar_spread"],
    },
    "510500": {
        "name": "中证500ETF",
        "exchange": "SH",
        "lot_size": 10000,
        "liquidity": "medium",
        "characteristics": "弹性较大，IV系统性高于50/300，适合卖方策略",
        "suitable_strategies": ["vertical_spread", "calendar_spread", "diagonal_spread"],
    },
    "588000": {
        "name": "科创50ETF",
        "exchange": "SH",
        "lot_size": 10000,
        "liquidity": "medium",
        "characteristics": "IV常年偏高，卖方策略性价比突出，但标的本身弹性大风险不可忽视",
        "suitable_strategies": ["vertical_spread", "diagonal_spread", "calendar_spread"],
    },
    "159901": {
        "name": "深证100ETF",
        "exchange": "SZ",
        "lot_size": 10000,
        "liquidity": "low",
        "characteristics": "流动性较差，买卖价差宽，套利成本高",
        "suitable_strategies": ["vertical_spread"],
    },
    "159915": {
        "name": "创业板ETF",
        "exchange": "SZ",
        "lot_size": 10000,
        "liquidity": "medium",
        "characteristics": "波动大，适合方向性策略，IV分位高时卖方也有机会",
        "suitable_strategies": ["long_call_put", "vertical_spread", "diagonal_spread"],
    },
    "159919": {
        "name": "沪深300ETF(深交所)",
        "exchange": "SZ",
        "lot_size": 10000,
        "liquidity": "medium",
        "characteristics": "与510300高度相关，可做跨市套利，但流动性稍弱",
        "suitable_strategies": ["parity_arb", "vertical_spread"],
    },
}


def get_underlying_info(underlying_id: str) -> dict:
    """获取标的静态描述信息"""
    return UNDERLYING_KNOWLEDGE.get(underlying_id, {})


def get_all_underlyings() -> list:
    """获取所有标的列表"""
    return list(UNDERLYING_KNOWLEDGE.keys())


def get_iv_context(underlying_id: str, current_iv: float) -> dict:
    """
    评估当前IV相对于历史水平的位置
    完全从数据库读，无数据则返回空，不用假数据兜底
    """
    if current_iv is None:
        return {}

    session = SessionLocal()
    try:
        row = session.execute(text("""
            SELECT iv_mean_20d, iv_mean_60d, iv_typical_low, iv_typical_high
            FROM underlying_master
            WHERE underlying_id = :uid
        """), {"uid": underlying_id}).fetchone()
    finally:
        session.close()

    if not row or not row.iv_mean_20d:
        return {
            "current_iv": current_iv,
            "iv_level": "未知",
            "data_source": "no_data",
            "note": "暂无历史IV统计数据，接入真实行情后自动更新"
        }

    mean = float(row.iv_mean_20d)
    low = float(row.iv_typical_low)
    high = float(row.iv_typical_high)

    if current_iv >= high:
        iv_level = "极高"
        sell_side_attractive = True
    elif current_iv >= mean * 1.3:
        iv_level = "偏高"
        sell_side_attractive = True
    elif current_iv <= low:
        iv_level = "极低"
        sell_side_attractive = False
    elif current_iv <= mean * 0.85:
        iv_level = "偏低"
        sell_side_attractive = False
    else:
        iv_level = "正常"
        sell_side_attractive = False

    return {
        "current_iv": current_iv,
        "historical_mean": mean,
        "iv_level": iv_level,
        "sell_side_attractive": sell_side_attractive,
        "vs_mean": round((current_iv - mean) / mean * 100, 1),
        "data_source": "database"
    }