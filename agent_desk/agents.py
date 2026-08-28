"""Multi-agent research pipeline for the Agent Desk tab.

The pipeline mirrors the desk structure used by the TradingAgents framework
(https://github.com/TauricResearch/TradingAgents): a team of specialist
analysts hands findings to a bull/bear research debate, a trader turns the
debate into a proposal, a risk team sizes it, and a portfolio manager signs
off. The difference here is that every claim is derived from the report that
``core.services.analyze_symbol`` already builds, so the desk uses the same
numbers the Stock Analysis tab shows and needs no LLM or extra API keys.

Stages:
1. ``build_analyst_team``   - Technical, Fundamentals, Positioning, Macro/News.
2. ``run_research_debate``  - Bull vs Bear rounds over the analyst findings.
3. ``build_trader_proposal``- Action, levels, and conviction.
4. ``run_risk_debate``      - Aggressive / Balanced / Conservative sizing.
5. ``build_portfolio_decision`` - Final verdict and conditions.
6. ``build_grounding`` / ``detect_conflicts`` - What was verified, and where
   the analysts disagree. Low coverage or open conflicts cut the desk
   confidence, so a thin-data symbol cannot produce a confident verdict.

``build_agent_report(symbol)`` is the single entry point used by the view.
"""
from core import services


DEFAULT_DEBATE_ROUNDS = 2
MIN_DEBATE_ROUNDS = 1
MAX_DEBATE_ROUNDS = 3

# Desk weights. Technical and fundamentals carry the most weight to stay in
# line with the overall score in core.services, and the two newer desks
# (positioning, macro) fill the remainder.
ANALYST_WEIGHTS = {
    "technical": 0.32,
    "fundamentals": 0.30,
    "positioning": 0.18,
    "macro": 0.20,
}

BULLISH_STANCE_SCORE = 60
BEARISH_STANCE_SCORE = 40
CONFLICT_SCORE_GAP = 30

# Findings are grouped into factors before the debate is scored. Within one
# factor the signals are near-duplicates of each other: "price below its 50-day
# average", "price below its 200-day average", "50-day below 200-day", and
# "MACD below signal" are four readings of a single downtrend, not four
# independent reasons to sell. Summing them let the side with the most restated
# talking points win, which manufactured strong verdicts out of one observation.
#
# So each factor is aggregated with diminishing returns: its strongest argument
# counts in full, the second corroborating one at CORRELATION_DISCOUNT, the
# third at the square of it, and so on. Corroboration still helps, but it can
# never add more than roughly 1/(1-discount) times the best single signal.
CORRELATION_DISCOUNT = 0.4

# Desk votes are expressed on a 0-100 scale, so a factor worth less than this is
# rounding noise rather than an independent driver of the verdict.
MIN_MATERIAL_FACTOR_CONTRIBUTION = 2.0

# Post-haircut evidence weight a desk needs before it votes at its full weight -
# roughly two strong findings on independent factors. Below that its voice is
# scaled down, so one thin observation cannot carry a whole desk's vote.
FULL_VOICE_EVIDENCE_MASS = 5.0

FACTOR_TREND = "trend"
FACTOR_MOMENTUM = "momentum"
FACTOR_RELATIVE = "relative"
FACTOR_VOLATILITY = "volatility"
FACTOR_PARTICIPATION = "participation"
FACTOR_GROWTH = "growth"
FACTOR_PROFITABILITY = "profitability"
FACTOR_BALANCE_SHEET = "balanceSheet"
FACTOR_VALUATION = "valuation"
FACTOR_SELL_SIDE = "sellSide"
FACTOR_OWNERSHIP = "ownership"
FACTOR_OPTIONS = "options"
FACTOR_EVENT = "event"
FACTOR_SECTOR = "sector"
FACTOR_CATALYST = "catalyst"

FACTOR_LABELS = {
    FACTOR_TREND: "Trend",
    FACTOR_MOMENTUM: "Momentum",
    FACTOR_RELATIVE: "Relative strength",
    FACTOR_VOLATILITY: "Volatility",
    FACTOR_PARTICIPATION: "Participation",
    FACTOR_GROWTH: "Growth",
    FACTOR_PROFITABILITY: "Profitability",
    FACTOR_BALANCE_SHEET: "Balance sheet",
    FACTOR_VALUATION: "Valuation",
    FACTOR_SELL_SIDE: "Sell-side view",
    FACTOR_OWNERSHIP: "Ownership flows",
    FACTOR_OPTIONS: "Option positioning",
    FACTOR_EVENT: "Event risk",
    FACTOR_SECTOR: "Sector context",
    FACTOR_CATALYST: "Catalysts",
}

RISK_PROFILES = (
    ("aggressive", "Aggressive Risk Analyst", 1.5),
    ("balanced", "Balanced Risk Analyst", 1.0),
    ("conservative", "Conservative Risk Analyst", 0.5),
)

# No single name should dominate the book, however tight the stop is.
MAX_SINGLE_POSITION_PERCENT = 12.0

# Sell-side mean price targets sit habitually above spot, so "target implies
# +12%" is the resting state of the data rather than a bullish finding. Only the
# distance from this premium is informative. The figure is a broad long-run
# average across large-cap coverage, not a per-symbol measurement.
SELL_SIDE_TARGET_PREMIUM = 12.0

# Broad reference P/E ranges by sector. A single absolute band (the old
# 30x/60x rule) called every IT and FMCG name expensive and every lender cheap,
# because sector multiples differ structurally. These are wide reference ranges
# for orientation, not a substitute for a peer-group comparison, and the finding
# says so when the sector is unknown.
SECTOR_PE_BANDS = {
    "technology": (24, 58),
    "consumer defensive": (32, 75),
    "healthcare": (24, 58),
    "financial services": (12, 32),
    "energy": (8, 22),
    "utilities": (10, 26),
    "basic materials": (10, 28),
    "industrials": (18, 46),
    "consumer cyclical": (20, 52),
    "communication services": (15, 42),
    "real estate": (14, 40),
}
DEFAULT_PE_BAND = (18, 45)

# Sector/industry strings that mark a business whose balance sheet is funded by
# deposits or borrowings by design.
FINANCIAL_SECTOR_MARKERS = ("financial", "bank", "insurance", "capital markets", "credit")
# Fallback markers on the company name, used when the provider does not return a
# sector (Yahoo's profile endpoint answers 401 for anonymous callers, so sector
# is frequently blank for Indian symbols).
FINANCIAL_NAME_MARKERS = (
    "bank", "finance", "financial", "insurance", "assurance", "nbfc",
    "housing development finance", "capital", "securities", "credit", "lending",
)


def build_agent_report(symbol, rounds=DEFAULT_DEBATE_ROUNDS):
    """Run the desk for ``symbol``, reusing the cached stock report."""
    report = services.analyze_symbol(symbol)
    return build_agent_report_from_report(report, rounds)


def build_agent_report_from_report(report, rounds=DEFAULT_DEBATE_ROUNDS):
    """Run the desk over an already-built stock report payload."""
    rounds = normalize_rounds(rounds)
    analysts = build_analyst_team(report)
    grounding = build_grounding(report, analysts)
    conflicts = detect_conflicts(analysts)
    consensus = build_consensus(analysts)
    debate = run_research_debate(analysts, consensus, rounds)
    proposal = build_trader_proposal(report, analysts, debate)
    risk = run_risk_debate(report, analysts, debate, proposal, grounding, conflicts)
    decision = build_portfolio_decision(report, debate, proposal, risk, grounding, conflicts)

    return {
        "symbol": report.get("symbol") or "",
        "longName": report.get("longName") or "",
        "currency": report.get("currency") or "",
        "generatedAt": services.iso_now(),
        "source": report.get("source") or "",
        "debateRounds": rounds,
        "quote": report.get("quote") or {},
        "marketClock": report.get("marketClock") or {},
        "analysts": [public_analyst(analyst) for analyst in analysts],
        "consensus": consensus,
        "conflicts": conflicts,
        "grounding": grounding,
        "debate": debate,
        "proposal": proposal,
        "risk": risk,
        "decision": decision,
        "note": (
            "Every claim on this tab is derived from the same report the Stock Analysis tab "
            "uses. It is research output, not personalized financial advice."
        ),
    }


def normalize_rounds(rounds):
    try:
        value = int(rounds)
    except (TypeError, ValueError):
        return DEFAULT_DEBATE_ROUNDS
    return int(services.clamp(value, MIN_DEBATE_ROUNDS, MAX_DEBATE_ROUNDS))


# ---------------------------------------------------------------------------
# Stage 1 - Analyst team
# ---------------------------------------------------------------------------

def build_analyst_team(report):
    return [
        technical_analyst(report),
        fundamentals_analyst(report),
        positioning_analyst(report),
        macro_analyst(report),
    ]


def technical_analyst(report):
    technical = report.get("technical") or {}
    indicators = technical.get("indicators") or {}
    relative = technical.get("relativeStrength") or {}
    quote = report.get("quote") or {}
    price = quote.get("price")
    price_range = quote.get("range") or {}
    findings = []
    coverage = Coverage()

    for label, key, weight in (
        ("20-day average", "sma20", 1),
        ("50-day average", "sma50", 2),
        ("200-day average", "sma200", 3),
    ):
        value = indicators.get(key)
        if coverage.check(label, services.is_finite(value) and services.is_finite(price)):
            gap = services.safe_divide(price - value, value) * 100
            # Inside 1% is noise on a daily close, not a trend statement.
            direction = "neutral" if abs(gap) < 1 else "bull" if gap > 0 else "bear"
            findings.append(finding(
                f"Price is {abs(gap):.1f}% {'above' if gap >= 0 else 'below'} its {label}.",
                direction,
                weight,
                FACTOR_TREND,
            ))

    sma50 = indicators.get("sma50")
    sma200 = indicators.get("sma200")
    if coverage.check("Moving-average cross", services.is_finite(sma50) and services.is_finite(sma200)):
        findings.append(finding(
            "The 50-day average sits above the 200-day average, so the longer trend is intact."
            if sma50 > sma200
            else "The 50-day average sits below the 200-day average, so the longer trend is still broken.",
            "bull" if sma50 > sma200 else "bear",
            2,
            FACTOR_TREND,
        ))

    rsi = indicators.get("rsi14")
    if coverage.check("RSI (14)", services.is_finite(rsi)):
        # RSI is read as a momentum measure, so its sign follows its level
        # monotonically. The old rule flipped 70 to bearish while 69 stayed
        # bullish, which meant a one-point move reversed the desk's read and
        # treated strong momentum as a sell signal. Extremes instead keep the
        # momentum sign but lose weight, because mean-reversion risk offsets
        # continuation there.
        if rsi >= 70:
            findings.append(finding(
                f"RSI is {rsi:.0f}: momentum is strong but stretched, so entries should wait for a pause.",
                "bull",
                1,
                FACTOR_MOMENTUM,
            ))
        elif rsi >= 55:
            findings.append(finding(f"RSI is {rsi:.0f}, a constructive range with room to run.", "bull", 2, FACTOR_MOMENTUM))
        elif rsi > 45:
            findings.append(finding(f"RSI is {rsi:.0f}, neither stretched nor washed out.", "neutral", 1, FACTOR_MOMENTUM))
        elif rsi > 30:
            findings.append(finding(f"RSI is {rsi:.0f}, which shows active selling pressure.", "bear", 2, FACTOR_MOMENTUM))
        else:
            findings.append(finding(
                f"RSI is {rsi:.0f}: momentum is firmly negative, though this deep a reading can precede a bounce.",
                "bear",
                1,
                FACTOR_MOMENTUM,
            ))

    macd = indicators.get("macd")
    macd_signal = indicators.get("macdSignal")
    if coverage.check("MACD", services.is_finite(macd) and services.is_finite(macd_signal)):
        findings.append(finding(
            "MACD is above its signal line, so momentum is confirming the move."
            if macd > macd_signal
            else "MACD is below its signal line, so momentum is not confirming the move.",
            "bull" if macd > macd_signal else "bear",
            2,
            FACTOR_TREND,
        ))

    volume_ratio = indicators.get("volumeRatio")
    if coverage.check("Volume vs 20-day average", services.is_finite(volume_ratio)):
        if volume_ratio >= 1.4:
            findings.append(finding(f"Volume is {volume_ratio:.1f}x its 20-day average, so participation is real.", "bull", 2, FACTOR_PARTICIPATION))
        elif volume_ratio <= 0.7:
            findings.append(finding(f"Volume is only {volume_ratio:.1f}x its 20-day average, so conviction is thin.", "bear", 1, FACTOR_PARTICIPATION))
        else:
            findings.append(finding(f"Volume is {volume_ratio:.1f}x its 20-day average, close to normal.", "neutral", 1, FACTOR_PARTICIPATION))

    atr_percent = indicators.get("atrPercent")
    if coverage.check("ATR %", services.is_finite(atr_percent)):
        if atr_percent > 6:
            findings.append(finding(f"ATR is {atr_percent:.1f}% of price, so stops must be wide and size small.", "bear", 2, FACTOR_VOLATILITY))
        else:
            # Normal volatility is a sizing input, not a reason to be long.
            findings.append(finding(f"ATR is {atr_percent:.1f}% of price, so risk can be defined tightly.", "neutral", 1, FACTOR_VOLATILITY))

    high52 = price_range.get("high52w")
    low52 = price_range.get("low52w")
    if coverage.check("52-week range", services.is_finite(high52) and services.is_finite(low52) and services.is_finite(price)):
        from_high = services.safe_divide(high52 - price, high52) * 100
        from_low = services.safe_divide(price - low52, low52) * 100
        if from_high < 10:
            findings.append(finding(f"Price is {from_high:.1f}% off its one-year high, which favours continuation.", "bull", 2, FACTOR_MOMENTUM))
        elif from_low < 12:
            findings.append(finding(f"Price is only {from_low:.1f}% off its one-year low, so the base is unproven.", "bear", 2, FACTOR_MOMENTUM))
        else:
            findings.append(finding(f"Price sits mid-range: {from_high:.1f}% off the high, {from_low:.1f}% off the low.", "neutral", 1, FACTOR_MOMENTUM))

    average_spread = relative.get("averageSpread")
    if coverage.check("Relative strength vs benchmark", relative.get("available") and services.is_finite(average_spread)):
        benchmark = relative.get("benchmarkName") or "its benchmark"
        findings.append(finding(
            f"It is {'beating' if average_spread >= 0 else 'lagging'} {benchmark} by "
            f"{abs(average_spread):.1f}pp on average across 1W-6M.",
            "neutral" if abs(average_spread) < 1 else "bull" if average_spread > 0 else "bear",
            2,
            FACTOR_RELATIVE,
        ))

    return assemble_analyst(
        key="technical",
        role="Technical Analyst",
        focus="Trend, momentum, volume, and where price sits in its range.",
        score=technical.get("score"),
        summary=technical.get("summary") or "",
        findings=findings,
        coverage=coverage,
        metrics=[
            metric("Trend score", technical.get("score")),
            metric("RSI (14)", indicators.get("rsi14")),
            metric("ATR %", indicators.get("atrPercent"), suffix="%"),
            metric("Volume vs 20d", indicators.get("volumeRatio"), suffix="x"),
            metric("RS spread", relative.get("averageSpread"), suffix="pp"),
        ],
    )


def fundamentals_analyst(report):
    fundamentals = report.get("fundamentals") or {}
    metrics = fundamentals.get("metrics") or {}
    quote = report.get("quote") or {}
    price = quote.get("price")
    findings = []
    coverage = Coverage()

    sector = str(metrics.get("sector") or "").strip()
    industry = str(metrics.get("industry") or "").strip()
    is_financial = is_financial_business(sector, industry, report.get("longName"))

    checks = (
        ("Revenue growth", "revenueGrowth", 0.08, 0.0, 3, "Revenue growth is {pct}.", FACTOR_GROWTH),
        ("Earnings growth", "earningsGrowth", 0.08, 0.0, 3, "Earnings growth is {pct}.", FACTOR_GROWTH),
        ("Profit margin", "profitMargins", 0.12, 0.03, 2, "Profit margin is {pct}.", FACTOR_PROFITABILITY),
        ("Return on equity", "returnOnEquity", 0.15, 0.05, 2, "Return on equity is {pct}.", FACTOR_PROFITABILITY),
    )
    for label, key, good, bad, weight, template, factor in checks:
        value = metrics.get(key)
        if not coverage.check(label, services.is_finite(value)):
            continue
        text = template.format(pct=f"{value * 100:.1f}%")
        if value < 0:
            # An outright contraction or loss is a stronger statement than
            # merely being under the floor, so it is not lumped in below.
            findings.append(finding(f"{text} That is an outright decline.", "bear", 3, factor))
        elif value >= good:
            findings.append(finding(f"{text} That is a genuine strength.", "bull", weight, factor))
        elif value < bad:
            findings.append(finding(f"{text} That is below an acceptable floor.", "bear", weight, factor))
        else:
            findings.append(finding(f"{text} Adequate but not a differentiator.", "neutral", 1, factor))

    debt = metrics.get("debtToEquity")
    if coverage.check("Debt to equity", services.is_finite(debt)):
        if is_financial:
            # Lenders and insurers fund their balance sheet with deposits and
            # borrowings by design, so bank-like debt-to-equity of 400-800 is
            # normal. The old flat 180 threshold marked every Indian bank and
            # NBFC as "stretched", which is a false negative across the whole
            # sector. Capital adequacy is the right lens and is not in this
            # feed, so the reading is reported without a direction.
            findings.append(finding(
                f"Debt-to-equity is {debt:.0f}, which is structural for a lender or insurer - "
                "judge capital adequacy and asset quality instead, which this feed does not carry.",
                "neutral",
                1,
                FACTOR_BALANCE_SHEET,
            ))
        elif debt > 180:
            findings.append(finding(f"Debt-to-equity is {debt:.0f}, so the balance sheet is stretched.", "bear", 3, FACTOR_BALANCE_SHEET))
        elif debt < 80:
            findings.append(finding(f"Debt-to-equity is {debt:.0f}, so leverage is manageable.", "bull", 2, FACTOR_BALANCE_SHEET))
        else:
            findings.append(finding(f"Debt-to-equity is {debt:.0f}, moderate but worth watching.", "neutral", 1, FACTOR_BALANCE_SHEET))

    current_ratio = metrics.get("currentRatio")
    if coverage.check("Current ratio", services.is_finite(current_ratio)):
        if is_financial:
            findings.append(finding(
                f"Current ratio is {current_ratio:.2f}, which does not carry its usual meaning for a financial business.",
                "neutral",
                1,
                FACTOR_BALANCE_SHEET,
            ))
        elif current_ratio < 1:
            findings.append(finding(f"Current ratio is {current_ratio:.2f}, so short-term liabilities exceed current assets.", "bear", 2, FACTOR_BALANCE_SHEET))
        elif current_ratio >= 1.2:
            findings.append(finding(f"Current ratio is {current_ratio:.2f}, so short-term liquidity looks fine.", "bull", 1, FACTOR_BALANCE_SHEET))
        else:
            findings.append(finding(f"Current ratio is {current_ratio:.2f}, adequate but not comfortable.", "neutral", 1, FACTOR_BALANCE_SHEET))

    trailing_pe = metrics.get("trailingPE")
    if coverage.check("Trailing P/E", services.is_finite(trailing_pe)):
        cheap, rich = sector_pe_band(sector)
        known_sector = bool(sector)
        # Without a sector the band is a market-wide default, so the call is
        # weaker and says so rather than asserting a confident verdict.
        context = f"versus a {sector} reference range of {cheap:.0f}-{rich:.0f}x" if known_sector else (
            f"versus a market-wide reference range of {cheap:.0f}-{rich:.0f}x, as the sector was not reported"
        )
        if trailing_pe <= 0:
            findings.append(finding(
                f"Trailing P/E is not meaningful ({trailing_pe:.1f}), which points to trailing losses.",
                "bear",
                2,
                FACTOR_VALUATION,
            ))
        elif trailing_pe < cheap:
            findings.append(finding(
                f"Trailing P/E is {trailing_pe:.1f}, below its band {context} - cheap, but check whether earnings are falling before calling it value.",
                "bull",
                2 if known_sector else 1,
                FACTOR_VALUATION,
            ))
        elif trailing_pe > rich:
            findings.append(finding(
                f"Trailing P/E is {trailing_pe:.1f}, above its band {context}, so a lot of growth is already priced in.",
                "bear",
                3 if known_sector else 2,
                FACTOR_VALUATION,
            ))
        else:
            findings.append(finding(
                f"Trailing P/E is {trailing_pe:.1f}, inside its band {context}.",
                "neutral",
                1,
                FACTOR_VALUATION,
            ))

    target = metrics.get("targetMeanPrice")
    if coverage.check("Analyst target price", services.is_finite(target) and services.is_finite(price)):
        upside = services.safe_divide(target - price, price) * 100
        # Sell-side mean targets sit structurally above spot, so a positive
        # number is the default state and carries no information. Only the gap
        # against that habitual premium does. Treating raw upside as bullish
        # made this finding bullish on almost every symbol.
        excess = upside - SELL_SIDE_TARGET_PREMIUM
        if excess >= 10:
            findings.append(finding(
                f"Mean analyst target implies {upside:+.1f}%, well beyond the "
                f"{SELL_SIDE_TARGET_PREMIUM:.0f}% premium sell-side targets usually carry.",
                "bull",
                2,
                FACTOR_SELL_SIDE,
            ))
        elif excess <= -10:
            findings.append(finding(
                f"Mean analyst target implies only {upside:+.1f}%, short of the "
                f"{SELL_SIDE_TARGET_PREMIUM:.0f}% premium sell-side targets usually carry.",
                "bear",
                2,
                FACTOR_SELL_SIDE,
            ))
        else:
            findings.append(finding(
                f"Mean analyst target implies {upside:+.1f}%, about the usual sell-side premium, so it is not a signal.",
                "neutral",
                1,
                FACTOR_SELL_SIDE,
            ))

    return assemble_analyst(
        key="fundamentals",
        role="Fundamentals Analyst",
        focus="Growth, margins, returns, leverage, and what the price already assumes.",
        score=fundamentals.get("score"),
        summary=fundamentals.get("summary") or "",
        findings=findings,
        coverage=coverage,
        metrics=[
            metric("Quality score", fundamentals.get("score")),
            metric("Revenue growth", ratio_percent(metrics.get("revenueGrowth")), suffix="%"),
            metric("Earnings growth", ratio_percent(metrics.get("earningsGrowth")), suffix="%"),
            metric("Return on equity", ratio_percent(metrics.get("returnOnEquity")), suffix="%"),
            metric("Debt to equity", metrics.get("debtToEquity")),
            metric("Trailing P/E", metrics.get("trailingPE")),
        ],
    )


def positioning_analyst(report):
    """Who is actually buying: analysts, institutions, and the option chain."""
    fundamentals = report.get("fundamentals") or {}
    metrics = fundamentals.get("metrics") or {}
    growth = report.get("growthDrivers") or {}
    ownership = growth.get("ownership") or {}
    open_interest = report.get("openInterest") or {}
    quote = report.get("quote") or {}
    price = quote.get("price")
    findings = []
    coverage = Coverage()
    score = 50

    recommendation_mean = metrics.get("recommendationMean")
    opinions = metrics.get("numberOfAnalystOpinions")
    if coverage.check("Analyst consensus", services.is_finite(recommendation_mean)):
        count = f" from {int(opinions)} analysts" if services.is_finite(opinions) else ""
        if recommendation_mean <= 2.0:
            score += 12
            findings.append(finding(f"Analyst consensus is {recommendation_mean:.1f}{count}, a clear buy tilt.", "bull", 3, FACTOR_SELL_SIDE))
        elif recommendation_mean <= 2.5:
            score += 6
            findings.append(finding(f"Analyst consensus is {recommendation_mean:.1f}{count}, mildly constructive.", "bull", 2, FACTOR_SELL_SIDE))
        elif recommendation_mean >= 4.0:
            score -= 12
            findings.append(finding(f"Analyst consensus is {recommendation_mean:.1f}{count}, a sell tilt.", "bear", 3, FACTOR_SELL_SIDE))
        elif recommendation_mean >= 3.0:
            score -= 6
            findings.append(finding(f"Analyst consensus is {recommendation_mean:.1f}{count}, effectively a hold.", "bear", 2, FACTOR_SELL_SIDE))
        else:
            findings.append(finding(f"Analyst consensus is {recommendation_mean:.1f}{count}, neutral.", "neutral", 1, FACTOR_SELL_SIDE))

    target = metrics.get("targetMeanPrice")
    if coverage.check("Target price dispersion", services.is_finite(target) and services.is_finite(price)):
        low = metrics.get("targetLowPrice")
        high = metrics.get("targetHighPrice")
        if services.is_finite(low) and services.is_finite(high) and services.is_finite(target) and target:
            spread = services.safe_divide(high - low, target) * 100
            if spread > 60:
                score -= 6
                findings.append(finding(f"Analyst targets are {spread:.0f}% wide around the mean, so consensus is fragile.", "bear", 2, FACTOR_SELL_SIDE))
            else:
                score += 4
                findings.append(finding(f"Analyst targets cluster within {spread:.0f}% of the mean, so consensus is coherent.", "neutral", 1, FACTOR_SELL_SIDE))

    for name, weight in (("Promoters", 3), ("FIIs", 2), ("DIIs", 2)):
        row = next((item for item in (ownership.get("rows") or []) if item.get("name") == name), None)
        if not coverage.check(f"{name} holding trend", bool(row) and services.is_finite((row or {}).get("changePoints"))):
            continue
        change = row["changePoints"]
        trend = row.get("trend") or ""
        if change > 0.15:
            score += 4 + weight
            findings.append(finding(f"{name} holding is {trend.lower()}, up {abs(change):.2f}pp over the available quarters.", "bull", weight, FACTOR_OWNERSHIP))
        elif change < -0.15:
            score -= 4 + weight
            findings.append(finding(f"{name} holding is {trend.lower()}, down {abs(change):.2f}pp over the available quarters.", "bear", weight, FACTOR_OWNERSHIP))
        else:
            findings.append(finding(f"{name} holding is broadly stable ({change:+.2f}pp).", "neutral", 1, FACTOR_OWNERSHIP))

    oi_period = nearest_open_interest_period(open_interest)
    if coverage.check("Option-chain open interest", bool(oi_period)):
        bias = oi_period.get("bias") or ""
        pcr = oi_period.get("pcr")
        pcr_text = f" (PCR {pcr:.2f})" if services.is_finite(pcr) else ""
        direction = open_interest_direction(bias)
        if direction == "bull":
            score += 8
            findings.append(finding(f"{oi_period.get('label') or 'Near-term'} option chain shows {bias.lower()}{pcr_text}.", "bull", 2, FACTOR_OPTIONS))
        elif direction == "bear":
            score -= 8
            findings.append(finding(f"{oi_period.get('label') or 'Near-term'} option chain shows {bias.lower()}{pcr_text}.", "bear", 2, FACTOR_OPTIONS))
        else:
            findings.append(finding(f"{oi_period.get('label') or 'Near-term'} option chain is balanced{pcr_text}.", "neutral", 1, FACTOR_OPTIONS))

        max_pain = oi_period.get("maxPain")
        if services.is_finite(max_pain) and services.is_finite(price) and price:
            gap = services.safe_divide(max_pain - price, price) * 100
            # Max pain is a magnet, not a direction: writers gain if price
            # settles there, whichever side of spot it sits. Only a wide gap
            # says anything, and the sign is reported without a stance.
            findings.append(finding(
                f"Max-pain strike sits {gap:+.1f}% from spot, which is where option writers are anchored.",
                "neutral",
                1,
                FACTOR_OPTIONS,
            ))

    return assemble_analyst(
        key="positioning",
        role="Positioning Analyst",
        focus="Analyst consensus, promoter/FII/DII flows, and option-chain positioning.",
        score=score,
        summary=positioning_summary(score, coverage),
        findings=findings,
        coverage=coverage,
        metrics=[
            metric("Analyst consensus", metrics.get("recommendationMean")),
            metric("Analyst count", metrics.get("numberOfAnalystOpinions")),
            metric("Promoter holding", ratio_percent(metrics.get("promoterHolding")), suffix="%"),
            metric("Option PCR", (oi_period or {}).get("pcr")),
            metric("OI bias", (oi_period or {}).get("bias")),
        ],
    )


def macro_analyst(report):
    """Event risk, sector context, and company-specific catalysts."""
    events = report.get("events") or {}
    event_risk = events.get("risk") or {}
    growth = report.get("growthDrivers") or {}
    sector = growth.get("sectorAnalysis") or {}
    catalysts = growth.get("catalysts") or []
    budget_impacts = growth.get("budgetImpacts") or []
    findings = []
    coverage = Coverage()
    score = 50

    risk_score = event_risk.get("score")
    if coverage.check("Event risk window", services.is_finite(risk_score)):
        # Event risk is inverted: a high risk score is a reason to wait.
        score += round((50 - risk_score) * 0.5)
        label = event_risk.get("label") or "Normal"
        summary = event_risk.get("summary") or ""
        if risk_score >= 65:
            findings.append(finding(f"Event risk is {label.lower()}. {summary}", "bear", 3, FACTOR_EVENT))
        elif risk_score >= 45:
            findings.append(finding(f"Event risk is {label.lower()}. {summary}", "bear", 2, FACTOR_EVENT))
        else:
            # A quiet calendar removes a reason to wait; it is not a reason to
            # buy. Scoring it bullish tilted the macro desk positive on every
            # symbol that simply had no event scheduled.
            findings.append(finding(f"Event risk is {label.lower()}. {summary}", "neutral", 1, FACTOR_EVENT))

    if coverage.check("Event calendar", bool(events.get("items"))):
        next_event = next((item for item in events["items"] if item.get("date")), None)
        if next_event:
            # A scheduled result or dividend date widens the outcome
            # distribution in both directions, so it is a sizing input rather
            # than a bearish argument.
            findings.append(finding(
                f"Calendar shows {next_event.get('type') or 'an event'} dated {next_event['date']}, "
                "which widens the near-term outcome range in both directions.",
                "neutral",
                1,
                FACTOR_EVENT,
            ))

    if coverage.check("Sector context", bool(sector.get("available"))):
        matched = sector.get("matchedSector") or {}
        sector_name = matched.get("sector") or sector.get("stockSector") or "its sector"
        sector_score = matched.get("score")
        trend = (matched.get("trend") or "").lower()
        change = matched.get("marketCapChangePercent")
        change_text = f" ({change:+.2f}% market cap change)" if services.is_finite(change) else ""
        if services.is_finite(sector_score) and sector_score >= 60:
            score += 8
            findings.append(finding(f"{sector_name} is a supportive sector right now: {trend or 'positive'}{change_text}.", "bull", 2, FACTOR_SECTOR))
        elif services.is_finite(sector_score) and sector_score <= 40:
            score -= 8
            findings.append(finding(f"{sector_name} is a weak sector right now: {trend or 'negative'}{change_text}.", "bear", 2, FACTOR_SECTOR))
        else:
            findings.append(finding(f"{sector_name} sector context is mixed{change_text}.", "neutral", 1, FACTOR_SECTOR))

    if coverage.check("Catalyst headlines", bool(catalysts)):
        # The upstream scan is keyword-based, so a match is a lead to check
        # rather than a confirmed catalyst. Keep the weight and score low.
        score += 3
        headline = str((catalysts[0] or {}).get("title") or "").strip()
        findings.append(finding(
            f"{len(catalysts)} order/contract/policy headline(s) matched the keyword scan"
            + (f', latest: "{truncate(headline, 110)}"' if headline else "")
            + " - relevance is not verified, so read it before relying on it.",
            "bull",
            1,
            FACTOR_CATALYST,
        ))

    if coverage.check("Budget / policy themes", bool(budget_impacts)):
        score += 3
        theme = str((budget_impacts[0] or {}).get("theme") or "").strip()
        findings.append(finding(
            f"Policy theme exposure detected{f': {theme}' if theme else ''}, which can support order visibility.",
            "bull",
            1,
            FACTOR_CATALYST,
        ))

    # Upstream data-availability notes used to be emitted as bearish arguments,
    # which made a thinly covered symbol look like a fundamentally weak one and
    # pushed the debate edge negative purely because a feed was missing. Missing
    # data is a confidence problem, so it is routed through coverage (and from
    # there into the grounding panel) instead of the debate.
    for note in (growth.get("dataNotes") or [])[:3]:
        coverage.check(f"Growth-driver note: {truncate(str(note), 90)}", False)

    return assemble_analyst(
        key="macro",
        role="Macro & News Analyst",
        focus="Results calendar, sector rotation, order flow, and policy exposure.",
        score=score,
        summary=macro_summary(score, event_risk),
        findings=findings,
        coverage=coverage,
        metrics=[
            metric("Event risk", event_risk.get("score")),
            metric("Event risk label", event_risk.get("label")),
            metric("Sector score", ((sector.get("matchedSector") or {}).get("score"))),
            metric("Catalyst headlines", len(catalysts)),
            metric("Policy themes", len(budget_impacts)),
        ],
    )


# ---------------------------------------------------------------------------
# Analyst plumbing
# ---------------------------------------------------------------------------

class Coverage:
    """Tracks which inputs an analyst actually had, to grade its confidence."""

    def __init__(self):
        self.available = []
        self.missing = []

    def check(self, label, is_available):
        if is_available:
            self.available.append(label)
        else:
            self.missing.append(label)
        return bool(is_available)

    @property
    def expected(self):
        return len(self.available) + len(self.missing)

    @property
    def ratio(self):
        return services.safe_divide(len(self.available), self.expected)

    def as_payload(self):
        return {
            "available": len(self.available),
            "expected": self.expected,
            "ratio": round(self.ratio * 100),
            "availableFields": self.available,
            "missingFields": self.missing,
        }


def finding(text, direction, weight, factor):
    """One piece of analyst evidence.

    ``direction`` is ``bull``, ``bear``, or ``neutral``. Neutral findings are
    shown to the user but contribute nothing to the debate, so genuinely
    uninformative readings ("holding is broadly stable") no longer nudge the
    verdict. ``factor`` is the underlying driver, used to discount corroborating
    signals that say the same thing twice.
    """
    return {
        "text": text,
        "direction": direction,
        "weight": int(services.clamp(weight, 1, 3)),
        "factor": factor,
    }


def assemble_analyst(key, role, focus, score, summary, findings, coverage, metrics):
    score = int(services.clamp(round(score if services.is_finite(score) else 50), 0, 100))
    # A desk that received no data at all used to keep a 30% confidence floor,
    # so it still carried its full weight into the consensus and still spoke in
    # the debate. Confidence now starts at zero and only the presence of real
    # inputs lifts it.
    confidence = int(services.clamp(round(100 * coverage.ratio), 0, 100)) if coverage.available else 0
    stance = stance_for(score) if coverage.available else "neutral"
    evidence = [item["text"] for item in sorted_findings(findings, "bull")]
    concerns = [item["text"] for item in sorted_findings(findings, "bear")]
    observations = [item["text"] for item in sorted_findings(findings, "neutral")]
    return {
        "key": key,
        "role": role,
        "focus": focus,
        "score": score,
        "stance": stance,
        "confidence": confidence,
        "weight": ANALYST_WEIGHTS.get(key, 0.25),
        "headline": analyst_headline(role, stance, score, confidence),
        "summary": summary,
        "findings": findings,
        "evidence": evidence,
        "concerns": concerns,
        # Readings that are real but do not argue either way. They are shown so
        # nothing is hidden, but they carry no weight in the debate.
        "observations": observations,
        "metrics": [item for item in metrics if item.get("value") is not None],
        "coverage": coverage.as_payload(),
    }


def public_analyst(analyst):
    """Drop the internal findings list before sending the payload out."""
    return {key: value for key, value in analyst.items() if key != "findings"}


def sorted_findings(findings, direction):
    return sorted(
        [item for item in findings if item["direction"] == direction],
        key=lambda item: item["weight"],
        reverse=True,
    )


def sector_pe_band(sector):
    return SECTOR_PE_BANDS.get(str(sector or "").strip().lower(), DEFAULT_PE_BAND)


def is_financial_business(sector, industry, long_name):
    """True for lenders, insurers, and other balance-sheet-funded businesses.

    Sector is the reliable signal, but it is often blank for Indian symbols, so
    the company name is used as a fallback. The name check is deliberately
    conservative: a false positive only softens two balance-sheet findings to
    neutral, while a false negative marks a healthy bank as over-levered.
    """
    haystack = f"{sector} {industry}".strip().lower()
    if any(marker in haystack for marker in FINANCIAL_SECTOR_MARKERS):
        return True
    if haystack:
        # A sector was reported and it is not a financial one, so trust it
        # rather than pattern-matching a name like "Reliance Capital Ventures".
        return False
    name = str(long_name or "").lower()
    return any(marker in name for marker in FINANCIAL_NAME_MARKERS)


def stance_for(score):
    if score >= BULLISH_STANCE_SCORE:
        return "bullish"
    if score <= BEARISH_STANCE_SCORE:
        return "bearish"
    return "neutral"


def analyst_headline(role, stance, score, confidence):
    return f"{role} is {stance} at {score}/100 with {confidence}% data confidence."


def positioning_summary(score, coverage):
    if not coverage.available:
        return "No positioning data was available for this symbol."
    if score >= 62:
        return "Analysts, institutions, or option writers are leaning the same way as the trend."
    if score <= 38:
        return "Positioning data leans against a long here."
    return "Positioning data is mixed and does not confirm either side."


def macro_summary(score, event_risk):
    label = (event_risk.get("label") or "Normal").lower()
    if score >= 62:
        return f"Context is supportive and event risk is {label}."
    if score <= 38:
        return f"Context is unhelpful and event risk is {label}."
    return f"Context is neutral with {label} event risk."


def nearest_open_interest_period(open_interest):
    if not (open_interest or {}).get("available"):
        return None
    periods = open_interest.get("periods") or {}
    for key in ("day", "week", "month", "quarter"):
        period = periods.get(key) or {}
        if period.get("available"):
            return period
    return None


def open_interest_direction(bias):
    text = (bias or "").lower()
    if "bullish" in text or "put oi building" in text:
        return "bull"
    if "bearish" in text or "call oi building" in text:
        return "bear"
    return "neutral"


# ---------------------------------------------------------------------------
# Stage 2 - Bull vs Bear research debate
# ---------------------------------------------------------------------------

def build_consensus(analysts):
    """Confidence-weighted blend of the four analyst scores."""
    weighted = [
        (analyst["score"], analyst["weight"] * analyst["confidence"] / 100)
        for analyst in analysts
    ]
    total_weight = sum(weight for _, weight in weighted)
    score = round(sum(value * weight for value, weight in weighted) / total_weight) if total_weight else 50
    bullish = [analyst["key"] for analyst in analysts if analyst["stance"] == "bullish"]
    bearish = [analyst["key"] for analyst in analysts if analyst["stance"] == "bearish"]
    neutral = [analyst["key"] for analyst in analysts if analyst["stance"] == "neutral"]
    confidence = round(services.safe_divide(sum(analyst["confidence"] for analyst in analysts), len(analysts)))

    return {
        "score": int(services.clamp(score, 0, 100)),
        "stance": stance_for(score),
        "confidence": confidence,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "agreement": round(services.safe_divide(max(len(bullish), len(bearish)), len(analysts)) * 100),
        "summary": (
            f"{len(bullish)} of {len(analysts)} desks are bullish, {len(bearish)} bearish, "
            f"{len(neutral)} neutral. Weighted read: {score}/100."
        ),
    }


def run_research_debate(analysts, consensus, rounds):
    """Alternate bull and bear arguments, strongest points first."""
    bull_queue = ranked_arguments(analysts, "bull")
    bear_queue = ranked_arguments(analysts, "bear")
    ledger = build_debate_ledger(analysts)
    bull_strength = ledger["bullStrength"]
    bear_strength = ledger["bearStrength"]
    bull_factors = ledger["bullFactors"]
    bear_factors = ledger["bearFactors"]
    total = bull_strength + bear_strength
    edge = round(services.safe_divide(bull_strength - bear_strength, total) * 100) if total else 0

    exchanges = []
    per_round = 2
    # Each round should introduce a new driver rather than restate the previous
    # one in different words, so the agenda is one point per factor.
    bull_agenda = distinct_by_factor(bull_queue, rounds * per_round)
    bear_agenda = distinct_by_factor(bear_queue, rounds * per_round)
    for index in range(rounds):
        bull_points = bull_agenda[index * per_round:(index + 1) * per_round]
        bear_points = bear_agenda[index * per_round:(index + 1) * per_round]
        if not bull_points and not bear_points:
            break
        exchanges.append({
            "round": index + 1,
            "bull": {
                "speaker": "Bull Researcher",
                "claim": debate_claim(bull_points, "bull", index),
                "points": [argument_payload(item) for item in bull_points],
            },
            "bear": {
                "speaker": "Bear Researcher",
                "claim": debate_claim(bear_points, "bear", index),
                "points": [argument_payload(item) for item in bear_points],
            },
        })

    # Objections are the points arguing against the side the desk landed on, so
    # they only exist once the desk has landed somewhere. When the debate is level
    # there is no verdict to object to, and listing the losing queue anyway both
    # read as nonsense ("unanswered objection: earnings growth is 19.1%, a genuine
    # strength") and charged desk confidence a second time for a balanced debate
    # the near-zero edge had already priced in. HDFCBANK lost 8 points that way.
    #
    # One objection per factor: three ways of saying "the trend is down" is a
    # single unresolved risk, and listing it three times overstated the risk
    # count that the portfolio manager then acts on.
    winner = "bull" if edge >= 8 else "bear" if edge <= -8 else "undecided"
    losing_side = {"bull": bear_queue, "bear": bull_queue}.get(winner, [])
    unresolved = [
        argument_payload(item)
        for item in distinct_by_factor([row for row in losing_side if row["impact"] >= 2], 3)
    ]
    winning_factors = bull_factors if edge > 0 else bear_factors if edge < 0 else []
    winning_breadth = len([
        row for row in winning_factors
        if row["contribution"] >= MIN_MATERIAL_FACTOR_CONTRIBUTION
    ])

    return {
        "rounds": len(exchanges),
        "requestedRounds": rounds,
        "bullStrength": round(bull_strength, 1),
        "bearStrength": round(bear_strength, 1),
        "edge": edge,
        "winner": winner,
        "exchanges": exchanges,
        "unresolved": unresolved,
        "bullFactors": bull_factors,
        "bearFactors": bear_factors,
        "deskVotes": ledger["desks"],
        "independentFactors": len({*(row["factor"] for row in bull_factors), *(row["factor"] for row in bear_factors)}),
        "managerVerdict": research_manager_verdict(edge, consensus, unresolved, winning_breadth),
    }


def build_debate_ledger(analysts):
    """Score the debate as a weighted vote of desks, not a count of talking points.

    Two corrections happen here.

    First, within a desk the findings for one side are collapsed per factor with
    a diminishing-returns discount, so four readings of the same downtrend count
    as roughly one and a half.

    Second, each desk's total voice is normalised to its assigned weight. That
    matters because data availability differs structurally by desk: price data
    is always complete, so the technical desk always reached full confidence and
    emitted the most findings, while scraped fundamentals are usually partial.
    Multiplying each finding by desk confidence penalised the thin desks twice -
    once for producing fewer findings and again on every finding - which quietly
    turned the whole desk into a momentum model. Now a desk argues at its policy
    weight and only the bull/bear balance *within* the desk moves the edge.
    """
    bull_total = 0.0
    bear_total = 0.0
    bull_factors = {}
    bear_factors = {}
    desks = []

    for analyst in analysts:
        bull_parts = factor_contributions(analyst["findings"], "bull")
        bear_parts = factor_contributions(analyst["findings"], "bear")
        desk_raw = sum(bull_parts.values()) + sum(bear_parts.values())
        voice = analyst["weight"] * desk_voice_multiplier(analyst["confidence"])
        if not desk_raw or voice <= 0:
            desks.append({
                "key": analyst["key"],
                "role": analyst["role"],
                "voice": round(voice * 100, 1),
                "bull": 0.0,
                "bear": 0.0,
                "detail": (
                    "No data, so this desk does not vote."
                    if analyst["confidence"] <= 0
                    else "No directional finding, so this desk abstains."
                ),
            })
            continue

        # Normalising to the desk's full weight would let a desk with a single
        # weight-1 finding shout as loudly as one with three strong findings -
        # the keyword-scan catalyst headline became the largest bull factor on
        # the board that way. Engagement scales the desk's voice down until it
        # has brought enough evidence to earn all of it.
        engagement = min(1.0, desk_raw / FULL_VOICE_EVIDENCE_MASS)
        scale = voice * engagement / desk_raw * 100
        desk_bull = sum(bull_parts.values()) * scale
        desk_bear = sum(bear_parts.values()) * scale
        bull_total += desk_bull
        bear_total += desk_bear
        for factor, value in bull_parts.items():
            bull_factors[factor] = bull_factors.get(factor, 0.0) + value * scale
        for factor, value in bear_parts.items():
            bear_factors[factor] = bear_factors.get(factor, 0.0) + value * scale
        desks.append({
            "key": analyst["key"],
            "role": analyst["role"],
            "voice": round(voice * 100, 1),
            "bull": round(desk_bull, 2),
            "bear": round(desk_bear, 2),
            "detail": f"Votes {'bull' if desk_bull > desk_bear else 'bear' if desk_bear > desk_bull else 'even'} at {round(voice * 100, 1)}% of the desk vote.",
        })

    return {
        "bullStrength": bull_total,
        "bearStrength": bear_total,
        "bullFactors": factor_breakdown(bull_factors, analysts, "bull"),
        "bearFactors": factor_breakdown(bear_factors, analysts, "bear"),
        "desks": desks,
    }


def factor_contributions(findings, direction):
    """Per-factor weight for one side of one desk, after the correlation haircut."""
    grouped = {}
    for item in findings:
        if item["direction"] != direction:
            continue
        grouped.setdefault(item["factor"], []).append(item["weight"])

    return {
        factor: sum(
            weight * (CORRELATION_DISCOUNT ** rank)
            for rank, weight in enumerate(sorted(weights, reverse=True))
        )
        for factor, weights in grouped.items()
    }


def factor_breakdown(contributions, analysts, direction):
    counts = {}
    for analyst in analysts:
        for item in analyst["findings"]:
            if item["direction"] != direction:
                continue
            counts[item["factor"]] = counts.get(item["factor"], 0) + 1

    rows = [
        {
            "factor": factor,
            "label": FACTOR_LABELS.get(factor, titleize_factor(factor)),
            "points": counts.get(factor, 0),
            "contribution": round(value, 2),
        }
        for factor, value in contributions.items()
    ]
    rows.sort(key=lambda row: row["contribution"], reverse=True)
    return rows


def desk_voice_multiplier(confidence):
    """How much of its assigned weight a desk keeps, given its data coverage.

    A desk with no data at all is silent. Otherwise the discount is gentle,
    because the desk has already lost influence by having fewer findings to
    contribute; a second full penalty here is what made partial-data desks
    inaudible.
    """
    if not services.is_finite(confidence) or confidence <= 0:
        return 0.0
    return 0.55 + 0.45 * services.clamp(confidence, 0, 100) / 100


def titleize_factor(factor):
    text = str(factor or "").strip()
    if not text:
        return "Other"
    return text[0].upper() + text[1:]


def ranked_arguments(analysts, direction):
    arguments = []
    for analyst in analysts:
        for item in analyst["findings"]:
            if item["direction"] != direction:
                continue
            impact = item["weight"] * analyst["weight"] * desk_voice_multiplier(analyst["confidence"]) * 4
            arguments.append({
                "text": item["text"],
                "source": analyst["role"],
                "sourceKey": analyst["key"],
                "weight": item["weight"],
                "factor": item["factor"],
                "impact": round(impact, 2),
            })
    return sorted(arguments, key=lambda item: item["impact"], reverse=True)


def argument_payload(item):
    return {
        "text": item["text"],
        "source": item["source"],
        "sourceKey": item["sourceKey"],
        "factor": item["factor"],
        "factorLabel": FACTOR_LABELS.get(item["factor"], titleize_factor(item["factor"])),
        "impact": item["impact"],
    }


def distinct_by_factor(arguments, limit):
    """Strongest argument per factor, so a list of objections is not one
    objection restated. Used for the unresolved-risk list and the debate rounds.
    """
    seen = set()
    picked = []
    for item in arguments:
        if item["factor"] in seen:
            continue
        seen.add(item["factor"])
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def debate_claim(points, direction, index):
    if not points:
        return (
            "No further material argument remains on this side."
            if direction == "bull"
            else "No further material objection remains on this side."
        )
    openers = {
        "bull": [
            "The setup earns a position because",
            "Building on that, the case still holds because",
            "Even allowing for the objections, it works because",
        ],
        "bear": [
            "That ignores the fact that",
            "The rebuttal does not resolve this:",
            "The remaining objection is the decisive one:",
        ],
    }
    opener = openers[direction][min(index, len(openers[direction]) - 1)]
    sources = ", ".join(sorted({item["source"] for item in points}))
    return f"{opener} {lower_first(points[0]['text'])} (source: {sources})"


def research_manager_verdict(edge, consensus, unresolved, breadth=0):
    if edge >= 30:
        stance = "Bull case wins clearly"
        detail = "The bull arguments dominate on both weight and desk coverage."
    elif edge >= 8:
        stance = "Bull case wins narrowly"
        detail = "The bull side is ahead, but the bear objections are not fully answered."
    elif edge <= -30:
        stance = "Bear case wins clearly"
        detail = "The bear arguments dominate; a long here fights the evidence."
    elif edge <= -8:
        stance = "Bear case wins narrowly"
        detail = "The bear side is ahead, so any long needs an unusually tight invalidation."
    else:
        stance = "Debate is a draw"
        detail = "Neither side has a decisive edge, which usually argues for waiting."

    if unresolved:
        detail += f" {len(unresolved)} objection(s) remain open."

    return {
        "speaker": "Research Manager",
        "stance": stance,
        "edge": edge,
        "detail": detail + (
            f" Correlated readings are collapsed into {breadth} independent driver(s) before scoring."
            if breadth else ""
        ),
        "alignsWithConsensus": stance_for(consensus["score"]) == ("bullish" if edge >= 8 else "bearish" if edge <= -8 else "neutral"),
    }


# ---------------------------------------------------------------------------
# Stage 3 - Trader proposal
# ---------------------------------------------------------------------------

def build_trader_proposal(report, analysts, debate):
    levels = report.get("researchLevels") or {}
    swing = report.get("swingTradePlan") or {}
    quote = report.get("quote") or {}
    price = quote.get("price")
    edge = debate["edge"]
    technical = next((item for item in analysts if item["key"] == "technical"), {}) or {}
    fundamentals = next((item for item in analysts if item["key"] == "fundamentals"), {}) or {}

    action, action_detail = trader_action(edge, technical.get("score", 50), fundamentals.get("score", 50))
    average_confidence = round(services.safe_divide(sum(item["confidence"] for item in analysts), len(analysts)))
    conviction = int(services.clamp(round(abs(edge) * 0.6 + average_confidence * 0.4), 0, 100))
    entry = levels.get("pullbackEntry") or {}
    targets = levels.get("targets") or []
    suitability = swing.get("suitability") or {}

    return {
        "speaker": "Trader",
        "action": action,
        "actionDetail": action_detail,
        "conviction": conviction,
        "convictionLabel": conviction_label(conviction),
        "timeframe": swing_timeframe(swing),
        "swingSuitability": {
            "score": suitability.get("score"),
            "label": suitability.get("label") or "",
            "bestHorizon": suitability.get("bestHorizon") or "",
        },
        "mode": levels.get("mode") or "",
        "currentPrice": price,
        "entryZone": {"low": entry.get("low"), "high": entry.get("high")},
        "breakoutTrigger": levels.get("breakoutTrigger"),
        "invalidation": levels.get("invalidation"),
        "targets": targets,
        "riskReward": levels.get("riskReward"),
        "rationale": trader_rationale(action, debate, edge),
        "plannedRiskPercent": stop_distance_percent(price, levels.get("invalidation")),
    }


def swing_timeframe(swing):
    """Reuse the swing engine's best-fit horizon so both tabs agree."""
    suitability = swing.get("suitability") or {}
    horizon = suitability.get("bestHorizon") or ""
    plans = swing.get("plans") or []
    best_plan = next((plan for plan in plans if plan.get("horizon") == horizon), None) or (plans[0] if plans else {})
    timeframe = best_plan.get("timeframe") or ""
    if horizon and timeframe:
        return f"{horizon} ({timeframe})"
    return horizon or "Reviewed at every research level."


def trader_action(edge, technical_score, fundamental_score):
    if edge >= 30 and technical_score >= 60:
        return "Buy", "Trend and evidence both support taking a full planned position."
    if edge >= 12:
        return "Accumulate", "Evidence leans bullish, so scale in rather than taking size at once."
    if edge <= -30:
        return "Avoid", "The evidence argues against a long; stand aside until the structure repairs."
    if edge <= -12:
        return "Reduce", "Evidence leans bearish, so trim exposure and do not add."
    if fundamental_score >= 60 and technical_score < 45:
        return "Hold", "The business reads better than the chart, so wait for price to confirm."
    return "Hold", "Neither side has an edge, so no new risk is justified here."


def conviction_label(conviction):
    if conviction >= 70:
        return "High"
    if conviction >= 45:
        return "Moderate"
    return "Low"


def trader_rationale(action, debate, edge):
    verdict = debate["managerVerdict"]["stance"]
    lead = debate["exchanges"][0]["bull" if edge >= 0 else "bear"]["points"] if debate["exchanges"] else []
    driver = lower_first(lead[0]["text"]) if lead else "no single dominant driver"
    return (
        f"{verdict} at an edge of {edge:+d}. Acting on \"{action}\" because {driver} "
        f"Levels come straight from the research-levels engine, not from the debate."
    )


def stop_distance_percent(price, invalidation):
    if not (services.is_finite(price) and services.is_finite(invalidation) and price):
        return None
    return round(abs(services.safe_divide(price - invalidation, price)) * 100, 2)


# ---------------------------------------------------------------------------
# Stage 4 - Risk team
# ---------------------------------------------------------------------------

def run_risk_debate(report, analysts, debate, proposal, grounding, conflicts):
    indicators = (report.get("technical") or {}).get("indicators") or {}
    event_risk = ((report.get("events") or {}).get("risk") or {})
    atr_percent = indicators.get("atrPercent")
    stop_percent = proposal.get("plannedRiskPercent")
    sizes = risk_team_sizes(stop_percent)
    members = []

    for key, role, risk_budget in RISK_PROFILES:
        size = sizes.get(key)
        members.append({
            "key": key,
            "speaker": role,
            "riskBudgetPercent": risk_budget,
            "positionSizePercent": size,
            "argument": risk_argument(key, risk_budget, size, stop_percent, atr_percent, event_risk, debate),
        })

    chosen = choose_risk_stance(event_risk, grounding, debate, atr_percent, conflicts)
    selected = next((member for member in members if member["key"] == chosen), members[1])

    return {
        "members": members,
        "selected": chosen,
        "positionSizePercent": selected["positionSizePercent"],
        "riskBudgetPercent": selected["riskBudgetPercent"],
        "stopDistancePercent": stop_percent,
        "atrPercent": services.round_or_none(atr_percent),
        "maxSinglePositionPercent": MAX_SINGLE_POSITION_PERCENT,
        "verdict": {
            "speaker": "Risk Manager",
            "stance": selected["speaker"],
            "detail": risk_verdict_detail(chosen, event_risk, grounding, atr_percent, conflicts),
        },
    }


def risk_team_sizes(stop_percent):
    """Size each risk tier, then scale the whole team down together if the top
    tier breaches the single-name cap.

    Scaling proportionally (rather than clamping each tier) matters: a tight stop
    pushes every tier past the cap, and clamping would collapse aggressive,
    balanced, and conservative onto the same number.
    """
    raw = {
        key: (services.safe_divide(risk_budget, stop_percent) * 100)
        if services.is_finite(stop_percent) and stop_percent > 0
        else None
        for key, _role, risk_budget in RISK_PROFILES
    }
    values = [value for value in raw.values() if value is not None]
    if not values:
        return raw

    largest = max(values)
    factor = MAX_SINGLE_POSITION_PERCENT / largest if largest > MAX_SINGLE_POSITION_PERCENT else 1.0
    return {
        key: round(max(value * factor, 0.1), 1) if value is not None else None
        for key, value in raw.items()
    }


def position_size_percent(risk_budget, stop_percent):
    """Position size for one tier that keeps the loss at the stop near the budget."""
    if not (services.is_finite(stop_percent) and stop_percent > 0):
        return None
    return round(services.clamp(risk_budget / stop_percent * 100, 0.1, MAX_SINGLE_POSITION_PERCENT), 1)


def risk_argument(key, risk_budget, size, stop_percent, atr_percent, event_risk, debate):
    size_text = f"about {size}% of the book" if size is not None else "a size that cannot be computed without a valid stop"
    stop_text = f"a {stop_percent:.2f}% stop" if services.is_finite(stop_percent) else "an undefined stop"
    if key == "aggressive":
        return (
            f"Risk {risk_budget}% of capital: {size_text} against {stop_text}. "
            f"The debate edge is {debate['edge']:+d}, and waiting for perfect confirmation gives up the move."
        )
    if key == "conservative":
        risk_note = f"ATR is {atr_percent:.1f}% of price" if services.is_finite(atr_percent) else "ATR is unknown"
        return (
            f"Risk only {risk_budget}% of capital: {size_text} against {stop_text}. "
            f"{risk_note} and event risk is {(event_risk.get('label') or 'unknown').lower()}, so survival beats optimisation."
        )
    return (
        f"Risk {risk_budget}% of capital: {size_text} against {stop_text}. "
        "This is the default unless event risk or data gaps argue otherwise."
    )


def choose_risk_stance(event_risk, grounding, debate, atr_percent, conflicts):
    risk_score = event_risk.get("score")
    if services.is_finite(risk_score) and risk_score >= 65:
        return "conservative"
    if grounding["score"] < 55:
        return "conservative"
    if services.is_finite(atr_percent) and atr_percent > 6:
        return "conservative"
    # Desks reading the same data differently is itself a risk signal.
    if len(conflicts) >= 2 or debate["edge"] <= -12:
        return "conservative"
    if debate["edge"] >= 30 and grounding["score"] >= 75 and not conflicts:
        return "aggressive"
    return "balanced"


def risk_verdict_detail(chosen, event_risk, grounding, atr_percent, conflicts):
    reasons = []
    risk_score = event_risk.get("score")
    if services.is_finite(risk_score) and risk_score >= 65:
        reasons.append(f"event risk is {(event_risk.get('label') or 'high').lower()}")
    if grounding["score"] < 55:
        reasons.append(f"data grounding is only {grounding['score']}/100")
    if services.is_finite(atr_percent) and atr_percent > 6:
        reasons.append(f"ATR is {atr_percent:.1f}% of price")
    if len(conflicts) >= 2:
        reasons.append(f"{len(conflicts)} desks disagree on the same data")
    if chosen == "aggressive":
        return "Full planned risk is acceptable: the edge is clear and the underlying data is well grounded."
    if reasons:
        return "Sizing is cut back because " + ", and ".join(reasons) + "."
    return "Standard sizing applies; nothing in the data argues for cutting or raising risk."


# ---------------------------------------------------------------------------
# Stage 5 - Portfolio manager
# ---------------------------------------------------------------------------

def build_portfolio_decision(report, debate, proposal, risk, grounding, conflicts):
    conditions = []
    blocking = []
    event_risk = (report.get("events") or {}).get("risk") or {}
    risk_score = event_risk.get("score")

    desk_confidence = build_desk_confidence(grounding, conflicts, debate)

    if grounding["score"] < 45:
        blocking.append(f"Data grounding is {grounding['score']}/100, which is too thin to act on.")
    if proposal.get("plannedRiskPercent") is None:
        blocking.append("No valid invalidation level could be computed, so risk cannot be capped.")
    if debate["edge"] <= -30:
        blocking.append("The bear case wins clearly on the available evidence.")
    if desk_confidence["score"] < MIN_APPROVAL_DESK_CONFIDENCE and proposal["action"] in ("Buy", "Accumulate"):
        # Approving new risk while reporting low desk confidence is
        # self-contradictory, and it was possible before: a symbol could read
        # "Approved with conditions: Accumulate" at 17/100 confidence.
        blocking.append(
            f"Desk confidence is only {desk_confidence['score']}/100, which is too low to open a new position."
        )

    if services.is_finite(risk_score) and risk_score >= 65:
        conditions.append(f"Wait out the results/event window first: {event_risk.get('summary') or 'event risk is elevated'}")
    for conflict in conflicts:
        conditions.append(f"Resolve the disagreement between {conflict['left']['role']} and {conflict['right']['role']} before sizing up.")
    # These argue against the side the desk landed on, so name them as the risk
    # to the call rather than as a free-floating "objection".
    for objection in debate["unresolved"][:2]:
        conditions.append(
            f"Unanswered argument against this call, from {objection['source']}: {objection['text']}"
        )
    if grounding["missing"]:
        conditions.append(
            "Verify the missing inputs manually: " + ", ".join(grounding["missing"][:3]) + "."
        )
    if proposal["action"] in ("Buy", "Accumulate") and services.is_finite(proposal.get("riskReward")) and proposal["riskReward"] < 1.5:
        conditions.append(f"Risk/reward is only {proposal['riskReward']}:1; require a better entry before committing.")

    if blocking:
        status = "Rejected"
        final_action = "Avoid" if debate["edge"] <= -12 else "Hold"
        size = 0
    elif conditions:
        status = "Approved with conditions"
        final_action = proposal["action"]
        size = scale_size(risk["positionSizePercent"], 0.6)
    else:
        status = "Approved"
        final_action = proposal["action"]
        size = risk["positionSizePercent"]

    if final_action in ("Hold", "Avoid", "Reduce"):
        size = 0

    # "Approved" describes a position the desk is willing to open. When the call
    # itself commits no capital, approving it is a contradiction: HDFCBANK read
    # "Approved with conditions: Hold at 0% of the book", which invites the reader
    # to treat a decision to do nothing as a green light. Reduce is excluded
    # because it is a real instruction on an existing holding.
    if status != "Rejected" and final_action in ("Hold", "Avoid"):
        status = "Stand aside"

    return {
        "speaker": "Portfolio Manager",
        "status": status,
        "action": final_action,
        "positionSizePercent": size,
        "conviction": proposal["conviction"],
        "deskConfidence": desk_confidence,
        "blocking": blocking,
        "conditions": conditions,
        "invalidation": proposal.get("invalidation"),
        "targets": proposal.get("targets") or [],
        "riskReward": proposal.get("riskReward"),
        "summary": decision_summary(status, final_action, size, desk_confidence, debate),
    }


def scale_size(size, factor):
    if not services.is_finite(size):
        return None
    return round(size * factor, 1)


def build_desk_confidence(grounding, conflicts, debate):
    grounding_score = grounding["score"]
    # A one-sided debate is only meaningful if the inputs behind it exist. Scale
    # the edge contribution by grounding so a thin-data symbol cannot look
    # confident just because nothing was available to argue the other side.
    edge_component = min(abs(debate["edge"]), 60) / 60 * 100 * (grounding_score / 100)
    score = grounding_score * 0.55 + edge_component * 0.45
    score -= len(conflicts) * 8
    score -= len(debate["unresolved"]) * 4

    # Breadth matters as much as size. An edge resting on a single driver is one
    # observation, however lopsided the arithmetic looks, so it should not read
    # as a confident desk view.
    breadth = winning_factor_breadth(debate)
    if breadth <= 1:
        score -= 18
    elif breadth == 2:
        score -= 8

    # Factor breadth alone can still be fooled. RELIANCE produced six "independent"
    # bear drivers - trend, momentum, relative strength, participation and two
    # others - but four of them were computed from the same price series by the
    # same desk, and that desk supplied 80% of the bear case while every other
    # desk abstained. Six readings of one data feed is not six independent
    # confirmations, so how many desks actually carry the winning side is
    # charged for separately.
    desk_breadth = winning_desk_breadth(debate)
    if desk_breadth <= 1:
        score -= 12

    score = int(services.clamp(round(score), 0, 100))
    return {
        "score": score,
        "label": "High" if score >= 72 else "Moderate" if score >= 50 else "Low",
        "independentDrivers": breadth,
        "contributingDesks": desk_breadth,
        "detail": desk_confidence_detail(score, grounding_score, conflicts, debate, breadth, desk_breadth),
    }


def desk_confidence_detail(score, grounding_score, conflicts, debate, breadth, desk_breadth):
    """Name the largest reason the score is where it is.

    The generic "too many data gaps, open disagreements, or too few independent
    drivers" listed every possible cause and told the reader nothing. HDFCBANK
    scored 23/100 purely because the debate finished level, which is not a data
    problem at all, and the message sent the reader looking for missing inputs
    that were not the issue.
    """
    if score >= 72:
        return "Grounded inputs and a clear debate edge across several independent drivers."

    causes = []
    edge = debate["edge"]
    if abs(edge) < 12:
        causes.append("the bull and bear cases finish level, so the debate itself is unresolved")
    elif abs(edge) < 30:
        # INFY carried eight drivers across all four desks and still scored 30,
        # because the edge was only +13. Without naming that, the broadest
        # evidence on the board looked as though it had been marked down for
        # nothing.
        causes.append(f"the debate edge is narrow at {edge:+d}, so the two cases are close")
    if grounding_score < 60:
        causes.append(f"data grounding is only {grounding_score}/100")
    if conflicts:
        causes.append(f"{len(conflicts)} desk disagreement(s) remain open")
    if breadth and breadth <= 2:
        causes.append(f"the edge rests on just {breadth} independent driver(s)")
    if desk_breadth <= 1 and breadth > 0:
        causes.append("only one desk carries the winning side, with no corroboration from the others")

    if not causes:
        return "Usable, but cross-check the flagged items before acting."
    lead = "Usable, but note that " if score >= 50 else "Not a score to lean on: "
    return lead + "; ".join(causes) + "."


def winning_side(debate):
    """``bull``, ``bear``, or ``None`` when the debate is level."""
    if debate["edge"] > 0:
        return "bull"
    if debate["edge"] < 0:
        return "bear"
    return None


def winning_factor_breadth(debate):
    """How many distinct factors carry the side that is ahead."""
    side = winning_side(debate)
    if side is None:
        return 0
    rows = debate.get(f"{side}Factors") or []
    # Ignore factors that barely register, so a rounding-level contribution does
    # not count as an independent driver.
    return len([
        row for row in rows
        if row["contribution"] >= MIN_MATERIAL_FACTOR_CONTRIBUTION
    ])


def winning_desk_breadth(debate):
    """How many desks contribute materially to the side that is ahead."""
    side = winning_side(debate)
    if side is None:
        return 0
    return len([
        row for row in debate.get("deskVotes") or []
        if row.get(side, 0) >= MIN_MATERIAL_FACTOR_CONTRIBUTION
    ])


def decision_summary(status, action, size, desk_confidence, debate):
    size_text = f" at about {size}% of the book" if services.is_finite(size) and size else ""
    return (
        f"{status}: {action}{size_text}. Debate edge {debate['edge']:+d}, "
        f"desk confidence {desk_confidence['score']}/100 ({desk_confidence['label']})."
    )


# ---------------------------------------------------------------------------
# Stage 6 - Grounding and conflict detection
# ---------------------------------------------------------------------------

def build_grounding(report, analysts):
    """What the desk could verify, and what a human still has to check."""
    quality = report.get("quality") or {}
    history = report.get("history") or {}
    technical = report.get("technical") or {}
    relative = technical.get("relativeStrength") or {}
    fundamentals = (report.get("fundamentals") or {}).get("metrics") or {}
    ownership = ((report.get("growthDrivers") or {}).get("ownership") or {})
    open_interest = report.get("openInterest") or {}
    levels = technical.get("levels") or {}

    checks = [
        grounding_check(
            "Price history",
            (history.get("chartCandles") or 0) >= 200,
            f"{history.get('chartCandles') or 0} daily candles in the display window.",
            partial=(history.get("chartCandles") or 0) >= 60,
        ),
        grounding_check(
            "Support / resistance zones",
            len(levels.get("supportZones") or []) + len(levels.get("resistanceZones") or []) >= 3,
            f"{len(levels.get('supportZones') or [])} support and {len(levels.get('resistanceZones') or [])} resistance zones detected.",
            partial=bool(levels.get("supportZones") or levels.get("resistanceZones")),
        ),
        grounding_check(
            "Fundamental metrics",
            count_finite(fundamentals, FUNDAMENTAL_FIELDS) >= 7,
            f"{count_finite(fundamentals, FUNDAMENTAL_FIELDS)} of {len(FUNDAMENTAL_FIELDS)} key metrics available.",
            partial=count_finite(fundamentals, FUNDAMENTAL_FIELDS) >= 4,
        ),
        grounding_check(
            "Analyst targets",
            services.is_finite(fundamentals.get("targetMeanPrice")),
            "Mean analyst target price available." if services.is_finite(fundamentals.get("targetMeanPrice")) else "No analyst target price returned.",
        ),
        grounding_check(
            "Promoter / FII / DII holdings",
            len(ownership.get("rows") or []) >= 3,
            f"{len(ownership.get('rows') or [])} shareholding rows from {ownership.get('source') or 'the provider'}.",
            partial=bool(ownership.get("rows")),
        ),
        grounding_check(
            "Benchmark relative strength",
            bool(relative.get("available")),
            f"Measured against {relative.get('benchmarkName') or 'benchmark'}." if relative.get("available") else "Benchmark comparison unavailable.",
        ),
        grounding_check(
            "Option-chain open interest",
            bool(open_interest.get("available")),
            open_interest.get("source") or open_interest.get("reason") or "Open interest not available for this symbol.",
        ),
        grounding_check(
            "Results / event calendar",
            bool((report.get("events") or {}).get("items")),
            f"{len((report.get('events') or {}).get('items') or [])} calendar entries.",
        ),
    ]

    verified = len([check for check in checks if check["status"] == "verified"])
    partial = len([check for check in checks if check["status"] == "partial"])
    missing = [check["label"] for check in checks if check["status"] == "missing"]
    coverage_score = services.safe_divide(verified + partial * 0.5, len(checks)) * 100
    analyst_confidence = services.safe_divide(
        sum(analyst["confidence"] for analyst in analysts), len(analysts)
    )
    score = int(services.clamp(round(coverage_score * 0.6 + analyst_confidence * 0.4), 0, 100))

    return {
        "score": score,
        "label": "High" if score >= 75 else "Moderate" if score >= 55 else "Low",
        "verified": verified,
        "partial": partial,
        "total": len(checks),
        "missing": missing,
        "checks": checks,
        "reportConfidence": (report.get("scores") or {}).get("confidence"),
        "reportQualityLabel": quality.get("label") or "",
        "summary": (
            f"{verified} of {len(checks)} inputs fully verified, {partial} partial. "
            + ("Nothing critical is missing." if not missing else f"Still unverified: {', '.join(missing)}.")
        ),
    }


FUNDAMENTAL_FIELDS = (
    "marketCap",
    "trailingPE",
    "priceToBook",
    "profitMargins",
    "returnOnEquity",
    "revenueGrowth",
    "earningsGrowth",
    "debtToEquity",
    "currentRatio",
    "dividendYield",
)


def grounding_check(label, is_verified, detail, partial=False):
    status = "verified" if is_verified else "partial" if partial else "missing"
    return {"label": label, "status": status, "detail": detail}


def count_finite(mapping, fields):
    return len([field for field in fields if services.is_finite((mapping or {}).get(field))])


# A desk with almost no inputs sits at its default score, which is not an
# opinion. Comparing that default against a well-covered desk used to raise a
# "disagreement" that was really just a missing feed, and each such conflict cut
# sizing and desk confidence.
MIN_CONFLICT_CONFIDENCE = 35

# Below this desk confidence the manager will not open new risk, however the
# debate reads. Matches the "Low" band in build_desk_confidence.
MIN_APPROVAL_DESK_CONFIDENCE = 50


def detect_conflicts(analysts):
    """Flag desk pairs that hold genuinely opposing views.

    A conflict requires one desk to be bullish and another bearish. Two weaker
    tests were tried first and both were wrong:

    A score gap alone counts a desk with no view as an opponent. RELIANCE showed
    two "conflicts" where the technical desk was bearish at 4/100 and the
    fundamentals and positioning desks sat at their default 50/100 neutral with
    no directional finding at all. Neither desk disagreed with anything - they
    had nothing to say - yet each produced a "resolve this disagreement before
    sizing up" instruction and pushed desk confidence down a second time for a
    weakness the driver-breadth penalty had already charged for.

    Excluding only zero-confidence desks does not fix it either: partial data
    clears the confidence bar while still producing no opinion.

    A directional desk that nobody corroborates is a real weakness, but it is
    thin evidence rather than a disagreement, and it is priced through
    ``winning_factor_breadth`` instead.
    """
    conflicts = []
    opinionated = [
        analyst for analyst in analysts
        if analyst["confidence"] >= MIN_CONFLICT_CONFIDENCE
        and analyst["stance"] in ("bullish", "bearish")
    ]
    for index, left in enumerate(opinionated):
        for right in opinionated[index + 1:]:
            gap = abs(left["score"] - right["score"])
            if gap < CONFLICT_SCORE_GAP:
                continue
            if left["stance"] == right["stance"]:
                continue
            bullish, bearish = (left, right) if left["score"] > right["score"] else (right, left)
            conflicts.append({
                "gap": gap,
                "left": {"key": bullish["key"], "role": bullish["role"], "score": bullish["score"], "stance": bullish["stance"]},
                "right": {"key": bearish["key"], "role": bearish["role"], "score": bearish["score"], "stance": bearish["stance"]},
                "detail": (
                    f"{bullish['role']} reads {bullish['score']}/100 while {bearish['role']} reads "
                    f"{bearish['score']}/100, a {gap}-point split."
                ),
                "resolution": conflict_resolution(bullish["key"], bearish["key"]),
            })
    return sorted(conflicts, key=lambda item: item["gap"], reverse=True)


CONFLICT_RESOLUTIONS = {
    ("technical", "fundamentals"): "Price is ahead of the business. Treat it as a trade with a hard stop, not an investment.",
    ("fundamentals", "technical"): "The business is better than the chart. Wait for price to confirm before committing.",
    ("technical", "macro"): "The chart is strong into an event window. Size down or wait for the event to clear.",
    ("macro", "technical"): "Context is supportive but price is not. Let price lead.",
    ("technical", "positioning"): "The trend is up but positioning is not following. Expect a lower-quality move.",
    ("positioning", "technical"): "Institutions or analysts are ahead of price. Watch for a base to form.",
    ("fundamentals", "positioning"): "Good numbers with weak positioning. Check for a known overhang.",
    ("positioning", "fundamentals"): "Strong positioning on weak numbers. Verify what the flow is anticipating.",
    ("fundamentals", "macro"): "Quality business into a poor sector or event window. Patience usually pays.",
    ("macro", "fundamentals"): "Good context, weak company. Prefer a stronger name in the same theme.",
    ("positioning", "macro"): "Flows are constructive against weak context. Keep the position small.",
    ("macro", "positioning"): "Context is fine but nobody is positioned. Wait for confirmation.",
}


def conflict_resolution(bullish_key, bearish_key):
    return CONFLICT_RESOLUTIONS.get(
        (bullish_key, bearish_key),
        "Treat the disagreement as a reason to cut size until one side is resolved.",
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def metric(label, value, suffix=""):
    if isinstance(value, str):
        return {"label": label, "value": value or None}
    if not services.is_finite(value):
        return {"label": label, "value": None}
    return {"label": label, "value": f"{services.round2(value):g}{suffix}"}


def ratio_percent(value):
    return value * 100 if services.is_finite(value) else None


def lower_first(text):
    """Lowercase the first letter, but leave acronyms (ATR, RSI, MACD, DIIs) alone."""
    text = str(text or "")
    if not text or (len(text) > 1 and text[1].isupper()):
        return text
    return text[0].lower() + text[1:]


def truncate(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"
