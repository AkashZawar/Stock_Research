"""Tests for the Agent Desk multi-agent pipeline and its JSON API.

Covers the analyst team, the bull/bear debate, trader and risk sizing, the
portfolio-manager sign-off, conflict detection, data grounding, and the
``/api/agent-desk/analyze`` endpoint.

Run with ``python manage.py test agent_desk``.
"""
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test import TestCase

from . import agents


def build_report(**overrides):
    """A complete, bullish, well-grounded report payload for the desk to read."""
    report = {
        "symbol": "TESTCO.NS",
        "longName": "Test Company Ltd",
        "currency": "INR",
        "source": "Yahoo Finance + Screener.in",
        "marketClock": {"status": "Closed"},
        "history": {"chartCandles": 248, "analysisCandles": 300},
        "scores": {"confidence": 78},
        "quality": {"label": "High"},
        "quote": {
            "price": 100.0,
            "changePercent": 1.4,
            "range": {"high52w": 106.0, "low52w": 62.0},
        },
        "technical": {
            "score": 74,
            "summary": "Bullish technical structure",
            "indicators": {
                "sma20": 96.0,
                "sma50": 92.0,
                "sma200": 84.0,
                "rsi14": 58.0,
                "macd": 1.8,
                "macdSignal": 1.1,
                "atrPercent": 2.4,
                "volumeRatio": 1.6,
            },
            "relativeStrength": {
                "available": True,
                "benchmarkName": "Nifty 50",
                "averageSpread": 6.2,
            },
            "levels": {
                "supportZones": [{"price": 94.0}, {"price": 88.0}],
                "resistanceZones": [{"price": 106.0}, {"price": 114.0}],
            },
        },
        "fundamentals": {
            "score": 71,
            "summary": "Strong fundamental profile",
            "metrics": {
                "marketCap": 850_000_000_000,
                "trailingPE": 24.0,
                "priceToBook": 3.4,
                "profitMargins": 0.18,
                "returnOnEquity": 0.21,
                "revenueGrowth": 0.16,
                "earningsGrowth": 0.19,
                "debtToEquity": 42.0,
                "currentRatio": 1.8,
                "dividendYield": 0.011,
                "targetMeanPrice": 118.0,
                "targetLowPrice": 104.0,
                "targetHighPrice": 132.0,
                "recommendationMean": 1.9,
                "numberOfAnalystOpinions": 21,
                "promoterHolding": 0.54,
                "sector": "Industrials",
            },
        },
        "events": {
            "risk": {"score": 25, "label": "Normal", "summary": "No near-term results event was found."},
            "items": [{"type": "Earnings", "date": "2026-11-04", "detail": "Expected results date"}],
        },
        "growthDrivers": {
            "ownership": {
                "source": "Screener.in",
                "rows": [
                    {"name": "Promoters", "changePoints": 0.8, "trend": "Increasing", "latest": 0.54},
                    {"name": "FIIs", "changePoints": 1.2, "trend": "Increasing", "latest": 0.19},
                    {"name": "DIIs", "changePoints": 0.4, "trend": "Increasing", "latest": 0.14},
                ],
            },
            "catalysts": [{"title": "Test Company wins a large order from the state utility"}],
            "budgetImpacts": [{"theme": "Railway capex", "impact": "Supports order flow."}],
            "sectorAnalysis": {
                "available": True,
                "stockSector": "Industrials",
                "matchedSector": {
                    "sector": "Capital Goods",
                    "score": 68,
                    "trend": "Positive",
                    "marketCapChangePercent": 1.6,
                },
            },
            "dataNotes": [],
        },
        "openInterest": {
            "available": True,
            "source": "NSE India option-chain equities",
            "periods": {
                "day": {
                    "available": True,
                    "label": "Day",
                    "bias": "Bullish put-writing support",
                    "pcr": 1.24,
                    "maxPain": 102.0,
                }
            },
        },
        "researchLevels": {
            "mode": "Trend-following",
            "pullbackEntry": {"low": 94.0, "high": 96.5},
            "breakoutTrigger": 106.8,
            "invalidation": 91.5,
            "targets": [110.0, 118.0],
            "riskReward": 2.4,
            "support": 94.0,
            "resistance": 106.0,
        },
        "swingTradePlan": {
            "suitability": {"score": 76, "label": "High-quality swing setup", "bestHorizon": "Medium term"},
            "plans": [
                {"horizon": "Short term", "timeframe": "1 week"},
                {"horizon": "Medium term", "timeframe": "1 quarter"},
                {"horizon": "Long term", "timeframe": "6+ months"},
            ],
        },
    }
    report.update(overrides)
    return report


def make_bearish_report():
    report = build_report()
    report["quote"] = {"price": 100.0, "changePercent": -2.1, "range": {"high52w": 190.0, "low52w": 96.0}}
    report["technical"].update({"score": 22, "summary": "Weak technical structure"})
    report["technical"]["indicators"].update({
        "sma20": 108.0,
        "sma50": 121.0,
        "sma200": 140.0,
        "rsi14": 29.0,
        "macd": -2.4,
        "macdSignal": -1.1,
        "atrPercent": 7.8,
        "volumeRatio": 0.6,
    })
    report["technical"]["relativeStrength"] = {
        "available": True,
        "benchmarkName": "Nifty 50",
        "averageSpread": -11.4,
    }
    report["fundamentals"].update({"score": 26, "summary": "Weak fundamental profile"})
    report["fundamentals"]["metrics"].update({
        "revenueGrowth": -0.09,
        "earningsGrowth": -0.22,
        "profitMargins": 0.01,
        "returnOnEquity": 0.02,
        "debtToEquity": 240.0,
        "currentRatio": 0.7,
        "trailingPE": 88.0,
        "targetMeanPrice": 86.0,
        "targetLowPrice": 60.0,
        "targetHighPrice": 130.0,
        "recommendationMean": 4.2,
    })
    report["events"]["risk"] = {
        "score": 85,
        "label": "High",
        "summary": "A results-related event appears to be within one week.",
    }
    report["growthDrivers"]["ownership"]["rows"] = [
        {"name": "Promoters", "changePoints": -1.6, "trend": "Reducing", "latest": 0.41},
        {"name": "FIIs", "changePoints": -2.2, "trend": "Reducing", "latest": 0.08},
        {"name": "DIIs", "changePoints": -0.9, "trend": "Reducing", "latest": 0.07},
    ]
    report["growthDrivers"]["catalysts"] = []
    report["growthDrivers"]["budgetImpacts"] = []
    report["growthDrivers"]["sectorAnalysis"] = {
        "available": True,
        "stockSector": "Industrials",
        "matchedSector": {
            "sector": "Capital Goods",
            "score": 28,
            "trend": "Negative",
            "marketCapChangePercent": -2.4,
        },
    }
    report["openInterest"]["periods"]["day"].update({
        "bias": "Bearish call-writing pressure",
        "pcr": 0.62,
        "maxPain": 94.0,
    })
    return report


def make_thin_report():
    """Almost nothing was returned by the providers."""
    report = build_report()
    report["history"] = {"chartCandles": 41}
    report["scores"] = {"confidence": 28}
    report["quality"] = {"label": "Low"}
    report["technical"]["indicators"] = {"sma20": 96.0}
    report["technical"]["relativeStrength"] = {"available": False, "expected": True}
    report["technical"]["levels"] = {"supportZones": [], "resistanceZones": []}
    report["fundamentals"]["metrics"] = {"sector": "Industrials"}
    report["events"] = {"risk": {}, "items": []}
    report["growthDrivers"] = {
        "ownership": {"rows": [], "source": ""},
        "catalysts": [],
        "budgetImpacts": [],
        "sectorAnalysis": {"available": False},
        "dataNotes": ["Promoter/FII/DII trend was not available from the current data source."],
    }
    report["openInterest"] = {"available": False, "reason": "Not available for this stock."}
    return report


class AnalystTeamTests(SimpleTestCase):
    def test_team_has_four_desks_with_distinct_keys(self):
        analysts = agents.build_analyst_team(build_report())

        self.assertEqual(len(analysts), 4)
        self.assertEqual(
            [analyst["key"] for analyst in analysts],
            ["technical", "fundamentals", "positioning", "macro"],
        )
        for analyst in analysts:
            self.assertIn(analyst["stance"], {"bullish", "neutral", "bearish"})
            self.assertGreaterEqual(analyst["score"], 0)
            self.assertLessEqual(analyst["score"], 100)
            self.assertGreaterEqual(analyst["confidence"], 0)
            self.assertLessEqual(analyst["confidence"], 100)

    def test_technical_and_fundamental_scores_match_the_stock_report(self):
        report = build_report()
        analysts = {analyst["key"]: analyst for analyst in agents.build_analyst_team(report)}

        self.assertEqual(analysts["technical"]["score"], report["technical"]["score"])
        self.assertEqual(analysts["fundamentals"]["score"], report["fundamentals"]["score"])

    def test_bullish_report_makes_every_desk_bullish_with_high_confidence(self):
        analysts = agents.build_analyst_team(build_report())

        for analyst in analysts:
            self.assertEqual(analyst["stance"], "bullish", analyst["role"])
            self.assertGreaterEqual(analyst["confidence"], 70, analyst["role"])
            self.assertTrue(analyst["evidence"], analyst["role"])

    def test_bearish_report_makes_every_desk_bearish(self):
        analysts = agents.build_analyst_team(make_bearish_report())

        for analyst in analysts:
            self.assertEqual(analyst["stance"], "bearish", analyst["role"])
            self.assertTrue(analyst["concerns"], analyst["role"])

    def test_missing_data_lowers_confidence_and_records_missing_fields(self):
        rich = {analyst["key"]: analyst for analyst in agents.build_analyst_team(build_report())}
        thin = {analyst["key"]: analyst for analyst in agents.build_analyst_team(make_thin_report())}

        for key in ("technical", "fundamentals", "positioning", "macro"):
            self.assertLess(thin[key]["confidence"], rich[key]["confidence"], key)
            self.assertTrue(thin[key]["coverage"]["missingFields"], key)

    def test_public_analyst_payload_drops_internal_findings(self):
        analyst = agents.build_analyst_team(build_report())[0]

        self.assertIn("findings", analyst)
        self.assertNotIn("findings", agents.public_analyst(analyst))


class ResearchDebateTests(SimpleTestCase):
    def test_bullish_report_gives_the_bull_the_edge(self):
        report = build_report()
        analysts = agents.build_analyst_team(report)
        debate = agents.run_research_debate(analysts, agents.build_consensus(analysts), 2)

        self.assertGreater(debate["edge"], 0)
        self.assertEqual(debate["winner"], "bull")
        self.assertGreater(debate["bullStrength"], debate["bearStrength"])

    def test_bearish_report_gives_the_bear_the_edge(self):
        analysts = agents.build_analyst_team(make_bearish_report())
        debate = agents.run_research_debate(analysts, agents.build_consensus(analysts), 2)

        self.assertLess(debate["edge"], 0)
        self.assertEqual(debate["winner"], "bear")

    def test_requested_rounds_bound_the_number_of_exchanges(self):
        analysts = agents.build_analyst_team(build_report())
        consensus = agents.build_consensus(analysts)

        for rounds in (1, 2, 3):
            debate = agents.run_research_debate(analysts, consensus, rounds)
            self.assertLessEqual(debate["rounds"], rounds)
            self.assertEqual(debate["requestedRounds"], rounds)

    def test_each_exchange_cites_the_desk_that_raised_the_point(self):
        analysts = agents.build_analyst_team(build_report())
        debate = agents.run_research_debate(analysts, agents.build_consensus(analysts), 3)
        keys = {analyst["key"] for analyst in analysts}

        for exchange in debate["exchanges"]:
            for side in ("bull", "bear"):
                for point in exchange[side]["points"]:
                    self.assertIn(point["sourceKey"], keys)
                    self.assertTrue(point["text"])

    def test_rounds_are_normalized_into_range(self):
        self.assertEqual(agents.normalize_rounds(0), agents.MIN_DEBATE_ROUNDS)
        self.assertEqual(agents.normalize_rounds(99), agents.MAX_DEBATE_ROUNDS)
        self.assertEqual(agents.normalize_rounds("2"), 2)
        self.assertEqual(agents.normalize_rounds("not-a-number"), agents.DEFAULT_DEBATE_ROUNDS)
        self.assertEqual(agents.normalize_rounds(None), agents.DEFAULT_DEBATE_ROUNDS)


class TraderAndRiskTests(SimpleTestCase):
    def test_trader_levels_come_from_the_research_levels_engine(self):
        report = build_report()
        payload = agents.build_agent_report_from_report(report)
        proposal = payload["proposal"]
        levels = report["researchLevels"]

        self.assertEqual(proposal["invalidation"], levels["invalidation"])
        self.assertEqual(proposal["breakoutTrigger"], levels["breakoutTrigger"])
        self.assertEqual(proposal["targets"], levels["targets"])
        self.assertEqual(proposal["entryZone"]["low"], levels["pullbackEntry"]["low"])

    def test_bullish_report_produces_a_long_action(self):
        payload = agents.build_agent_report_from_report(build_report())

        self.assertIn(payload["proposal"]["action"], {"Buy", "Accumulate"})

    def test_bearish_report_never_produces_a_long_action(self):
        payload = agents.build_agent_report_from_report(make_bearish_report())

        self.assertIn(payload["proposal"]["action"], {"Avoid", "Reduce", "Hold"})

    def test_risk_team_sizes_aggressive_above_conservative(self):
        payload = agents.build_agent_report_from_report(build_report())
        sizes = {member["key"]: member["positionSizePercent"] for member in payload["risk"]["members"]}

        self.assertEqual(len(sizes), 3)
        self.assertGreater(sizes["aggressive"], sizes["balanced"])
        self.assertGreater(sizes["balanced"], sizes["conservative"])

    def test_high_event_risk_forces_conservative_sizing(self):
        payload = agents.build_agent_report_from_report(make_bearish_report())

        self.assertEqual(payload["risk"]["selected"], "conservative")

    def test_position_size_scales_inversely_with_stop_distance(self):
        wide = agents.position_size_percent(1.0, 10.0)
        tight = agents.position_size_percent(1.0, 2.0)

        self.assertLess(wide, tight)
        self.assertIsNone(agents.position_size_percent(1.0, None))
        self.assertIsNone(agents.position_size_percent(1.0, 0))

    def test_a_tight_stop_keeps_the_three_risk_tiers_distinct(self):
        # A 2% stop pushes every tier past the single-name cap. Sizes must be
        # scaled together, not clamped onto the same number.
        sizes = agents.risk_team_sizes(2.0)

        self.assertGreater(sizes["aggressive"], sizes["balanced"])
        self.assertGreater(sizes["balanced"], sizes["conservative"])
        self.assertLessEqual(sizes["aggressive"], agents.MAX_SINGLE_POSITION_PERCENT)

    def test_no_tier_ever_exceeds_the_single_name_cap(self):
        for stop in (0.2, 0.5, 1.0, 2.0, 5.0, 12.0, 40.0):
            for key, size in agents.risk_team_sizes(stop).items():
                self.assertLessEqual(size, agents.MAX_SINGLE_POSITION_PERCENT, f"{key} @ stop {stop}")
                self.assertGreater(size, 0, f"{key} @ stop {stop}")

    def test_risk_team_sizes_are_none_without_a_usable_stop(self):
        for stop in (None, 0, -3.0):
            self.assertEqual(set(agents.risk_team_sizes(stop).values()), {None})

    def test_multiple_desk_conflicts_cut_the_risk_stance_to_conservative(self):
        report = build_report()
        # Strong chart, broken business: splits technical against the other desks.
        report["fundamentals"]["score"] = 18
        report["fundamentals"]["metrics"].update({
            "revenueGrowth": -0.14,
            "earningsGrowth": -0.3,
            "profitMargins": 0.005,
            "returnOnEquity": 0.01,
            "debtToEquity": 300.0,
            "currentRatio": 0.5,
            "trailingPE": 110.0,
            "recommendationMean": 4.4,
        })
        payload = agents.build_agent_report_from_report(report)

        self.assertGreaterEqual(len(payload["conflicts"]), 2)
        self.assertEqual(payload["risk"]["selected"], "conservative")
        self.assertIn("disagree", payload["risk"]["verdict"]["detail"])


class PortfolioDecisionTests(SimpleTestCase):
    def test_clean_bullish_report_is_approved_with_a_position(self):
        decision = agents.build_agent_report_from_report(build_report())["decision"]

        self.assertIn(decision["status"], {"Approved", "Approved with conditions"})
        self.assertIn(decision["action"], {"Buy", "Accumulate"})
        self.assertGreater(decision["positionSizePercent"], 0)

    def test_bearish_report_is_rejected_with_no_position(self):
        decision = agents.build_agent_report_from_report(make_bearish_report())["decision"]

        self.assertEqual(decision["status"], "Rejected")
        self.assertEqual(decision["positionSizePercent"], 0)
        self.assertTrue(decision["blocking"])

    def test_thin_data_blocks_the_trade_and_reports_low_confidence(self):
        payload = agents.build_agent_report_from_report(make_thin_report())

        self.assertEqual(payload["decision"]["status"], "Rejected")
        self.assertEqual(payload["decision"]["positionSizePercent"], 0)
        self.assertLess(payload["grounding"]["score"], 55)
        self.assertEqual(payload["decision"]["deskConfidence"]["label"], "Low")

    def test_hold_and_avoid_actions_never_carry_a_position_size(self):
        for factory in (make_bearish_report, make_thin_report):
            decision = agents.build_agent_report_from_report(factory())["decision"]
            if decision["action"] in {"Hold", "Avoid", "Reduce"}:
                self.assertEqual(decision["positionSizePercent"], 0, decision["action"])


class ConflictAndGroundingTests(SimpleTestCase):
    def test_conflicting_desks_are_flagged_with_a_resolution(self):
        report = build_report()
        # Strong chart, broken business: the classic technical/fundamental split.
        report["fundamentals"]["score"] = 20
        report["fundamentals"]["metrics"].update({
            "revenueGrowth": -0.12,
            "earningsGrowth": -0.25,
            "profitMargins": 0.01,
            "returnOnEquity": 0.01,
            "debtToEquity": 260.0,
            "currentRatio": 0.6,
            "trailingPE": 92.0,
        })
        payload = agents.build_agent_report_from_report(report)
        pairs = {(conflict["left"]["key"], conflict["right"]["key"]) for conflict in payload["conflicts"]}

        self.assertIn(("technical", "fundamentals"), pairs)
        for conflict in payload["conflicts"]:
            self.assertGreaterEqual(conflict["gap"], agents.CONFLICT_SCORE_GAP)
            self.assertTrue(conflict["resolution"])

    def test_aligned_desks_produce_no_conflicts(self):
        payload = agents.build_agent_report_from_report(build_report())

        self.assertEqual(payload["conflicts"], [])

    def test_conflicts_lower_the_desk_confidence(self):
        clean = agents.build_agent_report_from_report(build_report())
        conflicted_report = build_report()
        conflicted_report["fundamentals"]["score"] = 20
        conflicted_report["fundamentals"]["metrics"].update({
            "revenueGrowth": -0.12,
            "earningsGrowth": -0.25,
            "profitMargins": 0.01,
            "returnOnEquity": 0.01,
            "debtToEquity": 260.0,
            "currentRatio": 0.6,
            "trailingPE": 92.0,
        })
        conflicted = agents.build_agent_report_from_report(conflicted_report)

        self.assertLess(
            conflicted["decision"]["deskConfidence"]["score"],
            clean["decision"]["deskConfidence"]["score"],
        )

    def test_grounding_marks_every_check_with_a_known_status(self):
        payload = agents.build_agent_report_from_report(build_report())
        grounding = payload["grounding"]

        self.assertEqual(grounding["total"], len(grounding["checks"]))
        for check in grounding["checks"]:
            self.assertIn(check["status"], {"verified", "partial", "missing"})
            self.assertTrue(check["label"])
            self.assertTrue(check["detail"])

    def test_thin_data_grounding_lists_missing_inputs(self):
        rich = agents.build_agent_report_from_report(build_report())["grounding"]
        thin = agents.build_agent_report_from_report(make_thin_report())["grounding"]

        self.assertGreater(rich["score"], thin["score"])
        self.assertEqual(rich["missing"], [])
        self.assertTrue(thin["missing"])
        self.assertEqual(thin["label"], "Low")


class DebateTextTests(SimpleTestCase):
    def test_acronyms_keep_their_capitals_when_quoted_in_a_claim(self):
        self.assertEqual(agents.lower_first("ATR is 1.4% of price."), "ATR is 1.4% of price.")
        self.assertEqual(agents.lower_first("RSI is 58."), "RSI is 58.")
        self.assertEqual(agents.lower_first("MACD is above its signal line."), "MACD is above its signal line.")
        self.assertEqual(agents.lower_first("DIIs holding is stable."), "DIIs holding is stable.")
        self.assertEqual(agents.lower_first("Price is above."), "price is above.")
        self.assertEqual(agents.lower_first("The 50-day average."), "the 50-day average.")
        self.assertEqual(agents.lower_first(""), "")
        self.assertEqual(agents.lower_first(None), "")

    def test_no_debate_claim_mangles_an_acronym(self):
        for factory in (build_report, make_bearish_report):
            payload = agents.build_agent_report_from_report(factory(), rounds=3)
            claims = [
                exchange[side]["claim"]
                for exchange in payload["debate"]["exchanges"]
                for side in ("bull", "bear")
            ]
            self.assertTrue(claims)
            for claim in claims:
                for mangled in ("aTR", "rSI", "mACD", "dIIs", "fIIs", "pCR"):
                    self.assertNotIn(mangled, claim)


class AgentReportShapeTests(SimpleTestCase):
    def test_payload_exposes_every_pipeline_stage(self):
        payload = agents.build_agent_report_from_report(build_report(), rounds=3)

        for key in (
            "symbol",
            "longName",
            "generatedAt",
            "debateRounds",
            "analysts",
            "consensus",
            "conflicts",
            "grounding",
            "debate",
            "proposal",
            "risk",
            "decision",
            "note",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["debateRounds"], 3)
        self.assertEqual(payload["symbol"], "TESTCO.NS")

    def test_consensus_counts_every_desk(self):
        payload = agents.build_agent_report_from_report(build_report())
        consensus = payload["consensus"]
        counted = len(consensus["bullish"]) + len(consensus["bearish"]) + len(consensus["neutral"])

        self.assertEqual(counted, len(payload["analysts"]))
        self.assertTrue(consensus["summary"])

    def test_empty_report_does_not_raise(self):
        payload = agents.build_agent_report_from_report({})

        self.assertEqual(payload["decision"]["positionSizePercent"], 0)
        self.assertTrue(payload["grounding"]["missing"])


class CorrelationHaircutTests(SimpleTestCase):
    """Correlated readings must not outvote independent ones by sheer count."""

    def test_repeating_one_factor_adds_less_than_the_first_reading(self):
        single = agents.factor_contributions(
            [agents.finding("a", "bear", 3, agents.FACTOR_TREND)], "bear",
        )
        repeated = agents.factor_contributions(
            [
                agents.finding("a", "bear", 3, agents.FACTOR_TREND),
                agents.finding("b", "bear", 3, agents.FACTOR_TREND),
                agents.finding("c", "bear", 3, agents.FACTOR_TREND),
            ],
            "bear",
        )

        self.assertEqual(single[agents.FACTOR_TREND], 3)
        # 3 + 3*0.4 + 3*0.16 = 4.68, not 9.
        self.assertAlmostEqual(repeated[agents.FACTOR_TREND], 4.68, places=6)
        self.assertLess(repeated[agents.FACTOR_TREND], 2 * single[agents.FACTOR_TREND])

    def test_two_independent_factors_beat_four_readings_of_one(self):
        concentrated = agents.factor_contributions(
            [agents.finding(f"t{index}", "bear", 2, agents.FACTOR_TREND) for index in range(4)],
            "bear",
        )
        spread = agents.factor_contributions(
            [
                agents.finding("g", "bull", 2, agents.FACTOR_GROWTH),
                agents.finding("v", "bull", 2, agents.FACTOR_VALUATION),
            ],
            "bull",
        )

        self.assertLess(sum(concentrated.values()), sum(spread.values()))

    def test_neutral_findings_do_not_move_the_edge(self):
        findings = [
            agents.finding("n1", "neutral", 3, agents.FACTOR_VALUATION),
            agents.finding("n2", "neutral", 3, agents.FACTOR_GROWTH),
        ]

        self.assertEqual(agents.factor_contributions(findings, "bull"), {})
        self.assertEqual(agents.factor_contributions(findings, "bear"), {})

    def test_a_desk_with_one_thin_finding_does_not_spend_its_whole_vote(self):
        loud = agents.build_debate_ledger([
            {
                "key": "macro", "role": "Macro", "weight": 0.20, "confidence": 100,
                "findings": [agents.finding("one weak headline", "bull", 1, agents.FACTOR_CATALYST)],
            },
        ])
        engaged = agents.build_debate_ledger([
            {
                "key": "macro", "role": "Macro", "weight": 0.20, "confidence": 100,
                "findings": [
                    agents.finding("a", "bull", 3, agents.FACTOR_CATALYST),
                    agents.finding("b", "bull", 3, agents.FACTOR_SECTOR),
                ],
            },
        ])

        self.assertLess(loud["bullStrength"], engaged["bullStrength"] / 3)

    def test_a_dataless_desk_is_silent(self):
        ledger = agents.build_debate_ledger([
            {
                "key": "fundamentals", "role": "Fundamentals", "weight": 0.30, "confidence": 0,
                "findings": [agents.finding("stale", "bull", 3, agents.FACTOR_GROWTH)],
            },
        ])

        self.assertEqual(ledger["bullStrength"], 0)
        self.assertEqual(ledger["bearStrength"], 0)
        self.assertIn("does not vote", ledger["desks"][0]["detail"])


class AnalystDirectionTests(SimpleTestCase):
    """Readings that argue for neither side must be reported as neutral."""

    def test_rsi_direction_is_monotonic_across_the_overbought_boundary(self):
        def rsi_direction(value):
            report = build_report()
            report["technical"]["indicators"]["rsi14"] = value
            analyst = agents.technical_analyst(report)
            item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_MOMENTUM and "RSI" in row["text"])
            return item["direction"]

        # The old rule flipped 69 (bull) to 71 (bear) on a one-point move and
        # read strong momentum as a sell signal.
        self.assertEqual(rsi_direction(69.0), "bull")
        self.assertEqual(rsi_direction(71.0), "bull")
        self.assertEqual(rsi_direction(50.0), "neutral")
        self.assertEqual(rsi_direction(35.0), "bear")

    def test_a_quiet_calendar_is_not_a_bullish_argument(self):
        analyst = agents.macro_analyst(build_report())
        event_findings = [row for row in analyst["findings"] if row["factor"] == agents.FACTOR_EVENT]

        self.assertTrue(event_findings)
        self.assertFalse([row for row in event_findings if row["direction"] == "bull"])

    def test_missing_data_notes_are_not_bearish_arguments(self):
        report = build_report()
        report["growthDrivers"]["dataNotes"] = [
            "FII holding was not available.",
            "Sector mapping was not available.",
        ]
        analyst = agents.macro_analyst(report)

        self.assertFalse([
            row for row in analyst["findings"]
            if "not available" in row["text"] and row["direction"] == "bear"
        ])
        # They are recorded as coverage gaps instead, which lowers confidence.
        self.assertTrue([
            label for label in analyst["coverage"]["missingFields"]
            if "Growth-driver note" in label
        ])

    def test_a_habitual_sell_side_premium_is_not_treated_as_upside(self):
        report = build_report()
        # Target 12% above spot is the resting state of sell-side coverage.
        report["fundamentals"]["metrics"]["targetMeanPrice"] = 112.0
        analyst = agents.fundamentals_analyst(report)
        item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_SELL_SIDE)

        self.assertEqual(item["direction"], "neutral")

    def test_an_unusually_large_target_gap_still_reads_bullish(self):
        report = build_report()
        report["fundamentals"]["metrics"]["targetMeanPrice"] = 140.0
        analyst = agents.fundamentals_analyst(report)
        item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_SELL_SIDE)

        self.assertEqual(item["direction"], "bull")


class SectorContextTests(SimpleTestCase):
    def test_bank_leverage_is_not_flagged_as_a_stretched_balance_sheet(self):
        report = build_report()
        report["longName"] = "Example Bank Ltd"
        report["fundamentals"]["metrics"].update({
            "sector": "Financial Services",
            "industry": "Banks - Regional",
            "debtToEquity": 620.0,
        })
        analyst = agents.fundamentals_analyst(report)
        item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_BALANCE_SHEET and "Debt-to-equity" in row["text"])

        self.assertEqual(item["direction"], "neutral")
        self.assertIn("structural for a lender", item["text"])

    def test_a_non_financial_with_the_same_leverage_is_still_flagged(self):
        report = build_report()
        report["fundamentals"]["metrics"].update({"sector": "Industrials", "debtToEquity": 620.0})
        analyst = agents.fundamentals_analyst(report)
        item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_BALANCE_SHEET and "Debt-to-equity" in row["text"])

        self.assertEqual(item["direction"], "bear")

    def test_a_reported_sector_overrides_the_name_heuristic(self):
        # "Reliance Capital Ventures" contains a financial marker but the
        # provider says Industrials, so the sector wins.
        self.assertFalse(agents.is_financial_business("Industrials", "Conglomerates", "Reliance Capital Ventures"))
        self.assertTrue(agents.is_financial_business("", "", "HDFC Bank Ltd"))

    def test_valuation_uses_the_sector_band_and_says_when_it_cannot(self):
        rich = build_report()
        rich["fundamentals"]["metrics"].update({"sector": "Energy", "trailingPE": 40.0})
        energy = agents.fundamentals_analyst(rich)
        energy_item = next(row for row in energy["findings"] if row["factor"] == agents.FACTOR_VALUATION)

        tech = build_report()
        tech["fundamentals"]["metrics"].update({"sector": "Technology", "trailingPE": 40.0})
        technology = agents.fundamentals_analyst(tech)
        tech_item = next(row for row in technology["findings"] if row["factor"] == agents.FACTOR_VALUATION)

        # Same multiple, opposite reads, because the sectors trade differently.
        self.assertEqual(energy_item["direction"], "bear")
        self.assertEqual(tech_item["direction"], "neutral")

        unknown = build_report()
        unknown["fundamentals"]["metrics"].update({"sector": "", "trailingPE": 40.0})
        blind = agents.fundamentals_analyst(unknown)
        blind_item = next(row for row in blind["findings"] if row["factor"] == agents.FACTOR_VALUATION)
        self.assertIn("sector was not reported", blind_item["text"])

    def test_trailing_losses_are_reported_rather_than_skipped(self):
        report = build_report()
        report["fundamentals"]["metrics"]["trailingPE"] = -14.0
        analyst = agents.fundamentals_analyst(report)
        item = next(row for row in analyst["findings"] if row["factor"] == agents.FACTOR_VALUATION)

        self.assertEqual(item["direction"], "bear")
        self.assertIn("trailing losses", item["text"])


class DeskCoherenceTests(SimpleTestCase):
    def test_a_dataless_desk_does_not_raise_a_conflict(self):
        analysts = [
            {"key": "technical", "role": "Technical", "score": 85, "stance": "bullish", "confidence": 100},
            {"key": "fundamentals", "role": "Fundamentals", "score": 50, "stance": "neutral", "confidence": 0},
        ]

        self.assertEqual(agents.detect_conflicts(analysts), [])

    def test_a_desk_with_no_view_is_not_an_opponent(self):
        # The RELIANCE case: technical strongly bearish, the others sitting at
        # their default neutral with partial data. That is missing corroboration,
        # not a disagreement, so it must not generate a "resolve this" condition.
        analysts = [
            {"key": "technical", "role": "Technical", "score": 4, "stance": "bearish", "confidence": 100},
            {"key": "fundamentals", "role": "Fundamentals", "score": 50, "stance": "neutral", "confidence": 38},
            {"key": "positioning", "role": "Positioning", "score": 49, "stance": "neutral", "confidence": 67},
        ]

        self.assertEqual(agents.detect_conflicts(analysts), [])

    def test_a_single_desk_carrying_the_verdict_loses_confidence(self):
        grounding = {"score": 80, "missing": []}
        factors = [
            {"factor": "trend", "contribution": 14.0},
            {"factor": "momentum", "contribution": 9.0},
            {"factor": "relative", "contribution": 6.0},
        ]
        alone = agents.build_desk_confidence(grounding, [], {
            "edge": -55, "unresolved": [], "bullFactors": [], "bearFactors": factors,
            "deskVotes": [
                {"role": "Technical", "bull": 0.0, "bear": 29.0},
                {"role": "Fundamentals", "bull": 0.0, "bear": 0.0},
            ],
        })
        corroborated = agents.build_desk_confidence(grounding, [], {
            "edge": -55, "unresolved": [], "bullFactors": [], "bearFactors": factors,
            "deskVotes": [
                {"role": "Technical", "bull": 0.0, "bear": 15.0},
                {"role": "Fundamentals", "bull": 0.0, "bear": 14.0},
            ],
        })

        self.assertEqual(alone["contributingDesks"], 1)
        self.assertEqual(corroborated["contributingDesks"], 2)
        self.assertLess(alone["score"], corroborated["score"])
        self.assertIn("only one desk carries the winning side", alone["detail"])
        self.assertNotIn("only one desk carries", corroborated["detail"])

    def test_the_confidence_detail_names_the_actual_cause(self):
        # A level debate is not a data problem, and the old catch-all message sent
        # the reader hunting for missing inputs that were not the issue.
        level = agents.build_desk_confidence(
            {"score": 85, "missing": []},
            [],
            {
                "edge": -1, "unresolved": [],
                "bullFactors": [{"factor": "growth", "contribution": 12.0}],
                "bearFactors": [{"factor": "trend", "contribution": 12.0}],
                "deskVotes": [
                    {"role": "Technical", "bull": 0.0, "bear": 12.0},
                    {"role": "Fundamentals", "bull": 12.0, "bear": 0.0},
                ],
            },
        )

        self.assertIn("finish level", level["detail"])
        self.assertNotIn("data grounding", level["detail"])

    def test_a_narrow_edge_is_named_rather_than_silently_marked_down(self):
        narrow = agents.build_desk_confidence(
            {"score": 85, "missing": []},
            [],
            {
                "edge": 13, "unresolved": [],
                "bullFactors": [
                    {"factor": "growth", "contribution": 9.0},
                    {"factor": "trend", "contribution": 7.0},
                    {"factor": "valuation", "contribution": 6.0},
                ],
                "bearFactors": [],
                "deskVotes": [
                    {"role": "Technical", "bull": 7.0, "bear": 0.0},
                    {"role": "Fundamentals", "bull": 15.0, "bear": 0.0},
                ],
            },
        )

        self.assertIn("narrow", narrow["detail"])
        self.assertNotIn("finish level", narrow["detail"])

    def test_desk_breadth_counts_only_the_winning_side(self):
        debate = {
            "edge": -40, "unresolved": [], "bullFactors": [], "bearFactors": [{"factor": "trend", "contribution": 20.0}],
            "deskVotes": [
                {"role": "Technical", "bull": 0.0, "bear": 20.0},
                {"role": "Macro", "bull": 18.0, "bear": 0.0},
            ],
        }

        self.assertEqual(agents.winning_desk_breadth(debate), 1)
        self.assertIsNone(agents.winning_side({"edge": 0}))

    def test_an_opinionated_disagreement_is_still_raised(self):
        analysts = [
            {"key": "technical", "role": "Technical", "score": 85, "stance": "bullish", "confidence": 100},
            {"key": "fundamentals", "role": "Fundamentals", "score": 30, "stance": "bearish", "confidence": 80},
        ]

        self.assertEqual(len(agents.detect_conflicts(analysts)), 1)

    def test_an_edge_from_a_single_driver_loses_confidence(self):
        grounding = {"score": 80, "missing": []}
        narrow = agents.build_desk_confidence(
            grounding, [], {"edge": 40, "unresolved": [], "bullFactors": [{"factor": "trend", "contribution": 30.0}], "bearFactors": []},
        )
        broad = agents.build_desk_confidence(
            grounding, [], {
                "edge": 40, "unresolved": [],
                "bullFactors": [
                    {"factor": "trend", "contribution": 12.0},
                    {"factor": "growth", "contribution": 10.0},
                    {"factor": "valuation", "contribution": 8.0},
                ],
                "bearFactors": [],
            },
        )

        self.assertEqual(narrow["independentDrivers"], 1)
        self.assertEqual(broad["independentDrivers"], 3)
        self.assertLess(narrow["score"], broad["score"])

    def test_a_new_position_is_not_approved_at_low_desk_confidence(self):
        report = build_report()
        # Thin grounding drags desk confidence into the Low band while the
        # debate still leans bullish.
        report["history"] = {"chartCandles": 40}
        report["openInterest"] = {"available": False, "reason": "No option chain"}
        report["growthDrivers"]["ownership"] = {"rows": []}
        payload = agents.build_agent_report_from_report(report)
        decision = payload["decision"]

        if decision["deskConfidence"]["score"] < agents.MIN_APPROVAL_DESK_CONFIDENCE:
            self.assertNotIn(decision["action"], ("Buy", "Accumulate"))
            self.assertEqual(decision["positionSizePercent"], 0)

    def test_a_level_debate_lists_no_unanswered_objections(self):
        # An objection is an argument against the side the desk landed on, so it
        # cannot exist before the desk has landed anywhere. Listing the losing
        # queue anyway labelled bullish points as objections and charged desk
        # confidence twice for the same balanced debate.
        analysts = [
            {
                "key": "technical", "role": "Technical", "weight": 0.32, "confidence": 100,
                "score": 40, "stance": "neutral",
                "findings": [agents.finding("trend is down", "bear", 3, agents.FACTOR_TREND)],
            },
            {
                "key": "fundamentals", "role": "Fundamentals", "weight": 0.30, "confidence": 100,
                "score": 60, "stance": "neutral",
                "findings": [agents.finding("growth is strong", "bull", 3, agents.FACTOR_GROWTH)],
            },
        ]
        debate = agents.run_research_debate(analysts, {"score": 50, "stance": "neutral"}, 2)

        self.assertEqual(debate["winner"], "undecided")
        self.assertEqual(debate["unresolved"], [])

    def test_a_decided_debate_still_lists_the_arguments_against_it(self):
        # A bearish tape with genuinely strong fundamentals: the bear case should
        # win, and the fundamental strengths should be listed as the arguments
        # that were never answered.
        report = make_bearish_report()
        report["fundamentals"] = build_report()["fundamentals"]
        payload = agents.build_agent_report_from_report(report)

        self.assertEqual(payload["debate"]["winner"], "bear")
        self.assertTrue(payload["debate"]["unresolved"])
        self.assertTrue(all(item["text"] for item in payload["debate"]["unresolved"]))

    def test_a_no_capital_call_is_not_labelled_approved(self):
        for action in ("Hold", "Avoid"):
            with self.subTest(action=action):
                decision = agents.build_portfolio_decision(
                    build_report(),
                    {"edge": 2, "unresolved": [], "bullFactors": [], "bearFactors": [], "deskVotes": []},
                    {"action": action, "conviction": 50, "plannedRiskPercent": 1.0, "riskReward": 2.0,
                     "invalidation": 90.0, "targets": [110.0]},
                    {"positionSizePercent": 8.0},
                    {"score": 80, "missing": []},
                    [],
                )

                self.assertEqual(decision["positionSizePercent"], 0)
                self.assertNotIn("Approved", decision["status"])
                self.assertEqual(decision["status"], "Stand aside")

    def test_a_real_buy_is_still_labelled_approved(self):
        decision = agents.build_portfolio_decision(
            build_report(),
            {"edge": 45, "unresolved": [],
             "bullFactors": [
                 {"factor": "trend", "contribution": 14.0},
                 {"factor": "growth", "contribution": 12.0},
                 {"factor": "valuation", "contribution": 9.0},
             ],
             "bearFactors": [],
             "deskVotes": [
                 {"role": "Technical", "bull": 18.0, "bear": 0.0},
                 {"role": "Fundamentals", "bull": 17.0, "bear": 0.0},
             ]},
            {"action": "Buy", "conviction": 72, "plannedRiskPercent": 1.0, "riskReward": 2.4,
             "invalidation": 90.0, "targets": [110.0, 118.0]},
            {"positionSizePercent": 8.0},
            {"score": 88, "missing": []},
            [],
        )

        self.assertIn("Approved", decision["status"])
        self.assertGreater(decision["positionSizePercent"], 0)

    def test_the_debate_payload_exposes_its_working(self):
        payload = agents.build_agent_report_from_report(build_report())
        debate = payload["debate"]

        for key in ("bullFactors", "bearFactors", "deskVotes", "independentFactors"):
            self.assertIn(key, debate)
        self.assertEqual(len(debate["deskVotes"]), 4)
        for row in debate["bullFactors"] + debate["bearFactors"]:
            for key in ("factor", "label", "points", "contribution"):
                self.assertIn(key, row)

    def test_each_debate_round_introduces_a_new_driver(self):
        payload = agents.build_agent_report_from_report(make_bearish_report(), rounds=3)
        factors = [
            point["factor"]
            for exchange in payload["debate"]["exchanges"]
            for point in exchange["bear"]["points"]
        ]

        self.assertEqual(len(factors), len(set(factors)))

    def test_unresolved_objections_are_one_per_driver(self):
        payload = agents.build_agent_report_from_report(make_bearish_report())
        factors = [item["factor"] for item in payload["debate"]["unresolved"]]

        self.assertEqual(len(factors), len(set(factors)))


class AgentDeskApiTests(TestCase):
    def test_analyze_returns_the_desk_payload(self):
        with patch("core.services.resolve_symbol_input", return_value="TESTCO.NS"), \
                patch("core.services.analyze_symbol", return_value=build_report()):
            response = self.client.get("/api/agent-desk/analyze", {"symbol": "TESTCO", "rounds": "3"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["symbol"], "TESTCO.NS")
        self.assertEqual(payload["debateRounds"], 3)
        self.assertEqual(len(payload["analysts"]), 4)
        self.assertIn(payload["decision"]["status"], {"Approved", "Approved with conditions", "Rejected"})

    def test_analyze_clamps_an_out_of_range_round_count(self):
        with patch("core.services.resolve_symbol_input", return_value="TESTCO.NS"), \
                patch("core.services.analyze_symbol", return_value=build_report()):
            response = self.client.get("/api/agent-desk/analyze", {"symbol": "TESTCO", "rounds": "500"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["debateRounds"], agents.MAX_DEBATE_ROUNDS)

    def test_analyze_rejects_an_unresolvable_symbol_with_suggestions(self):
        with patch("core.services.resolve_symbol_input", return_value=""), \
                patch("core.services.invalid_instrument_payload", return_value={"suggestions": []}):
            response = self.client.get("/api/agent-desk/analyze", {"symbol": "zzzz"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("suggestions", response.json())

    def test_analyze_surfaces_a_provider_failure_as_a_500(self):
        with patch("core.services.resolve_symbol_input", return_value="TESTCO.NS"), \
                patch("core.services.analyze_symbol", side_effect=RuntimeError("Could not load chart data.")):
            response = self.client.get("/api/agent-desk/analyze", {"symbol": "TESTCO"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "Could not load chart data.")
