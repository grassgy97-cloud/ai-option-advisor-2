import math
from scipy.stats import norm
from scipy.optimize import brentq


def black_scholes_price(S, K, T, r, sigma, option_type="C"):
    """
    Black-Scholes 期权定价
    S: 标的价格
    K: 执行价
    T: 到期时间（年）
    r: 无风险利率
    sigma: 波动率
    option_type: 'C' 认购 / 'P' 认沽
    """
    if T <= 0 or sigma <= 0:
        return max(0, S - K) if option_type == "C" else max(0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "C":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def solve_iv(market_price, S, K, T, r=0.0, option_type="C"):
    """
    用二分法反推隐含波动率
    market_price: 期权市场中间价
    返回 IV（年化），失败返回 None
    """
    if T <= 0 or market_price <= 0:
        return None

    intrinsic = max(0, S - K) if option_type == "C" else max(0, K - S)
    if market_price <= intrinsic:
        return None

    try:
        iv = brentq(
            lambda sigma: black_scholes_price(S, K, T, r, sigma, option_type) - market_price,
            1e-6, 10.0,
            xtol=1e-6,
            maxiter=200
        )
        return round(iv, 6)
    except Exception:
        return None


def calc_dte(quote_date, expiry_date):
    """
    计算到期天数和年化时间
    """
    if hasattr(quote_date, 'date'):
        quote_date = quote_date.date()
    if hasattr(expiry_date, 'date'):
        expiry_date = expiry_date.date()

    dte = (expiry_date - quote_date).days
    T = dte / 365.0
    return dte, T

def calc_greeks(S, K, T, r, sigma, option_type="C"):
    """
    计算期权 Greeks
    返回 delta, gamma, theta, vega, rho
    """
    if T <= 0 or sigma <= 0:
        return None

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100  # 每1vol point的变化

    if option_type == "C":
        delta = norm.cdf(d1)
        theta = (-(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
                 - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
        rho = K * T * math.exp(-r * T) * norm.cdf(d2) / 100
    else:
        delta = norm.cdf(d1) - 1
        theta = (-(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
                 + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365
        rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100

    return {
        "delta": round(float(delta), 4),
        "gamma": round(float(gamma), 4),
        "theta": round(float(theta), 6),
        "vega": round(float(vega), 4),
        "rho": round(float(rho), 4)
    }