"""Unit and integration tests for the analysis engine and APIs.

Covers swing-trade plans, ownership/shareholding parsing, recommendations,
relative strength, candlestick patterns, the market clock, caching, the
market-monitor endpoint, open-interest reports, NSE market snapshots, asset
(ETF/MF) analysis, search suggestions, history windows, quality reports, and
concurrent loader settling.

Every test is a ``SimpleTestCase``: the app has no database.

Run with ``python manage.py test``.
"""
import time
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from . import services


class SwingTradePlanTests(SimpleTestCase):
    def test_build_swing_trade_plan_returns_three_horizons_with_valid_levels(self):
        plan = services.build_swing_trade_plan({
            "current": 100.0,
            "yearHigh": 124.0,
            "yearLow": 72.0,
            "technical": {"score": 72},
            "fundamental": {"score": 64},
            "eventRisk": {"score": 25},
            "researchLevels": {
                "support": 94.0,
                "resistance": 108.0,
                "breakoutTrigger": 109.5,
                "targets": [112.0, 122.0],
            },
            "supportResistance": {
                "supportZones": [{"price": 94.0}, {"price": 88.0}],
                "resistanceZones": [{"price": 108.0}, {"price": 116.0}],
            },
            "avgVolume20": 1_000_000,
            "volumeRatio": 1.5,
            "atrValue": 3.0,
            "atrPercent": 3.0,
            "sma20": 98.0,
            "sma50": 95.0,
            "sma200": 86.0,
            "rsi14": 56.0,
            "performance": {
                "oneWeek": 2.0,
                "oneMonth": 5.0,
                "threeMonth": 8.0,
                "sixMonth": 14.0,
            },
        })

        self.assertEqual([item["timeframe"] for item in plan["plans"]], ["1 week", "1 quarter", "6+ months"])
        self.assertIn(plan["suitability"]["bestHorizon"], {item["horizon"] for item in plan["plans"]})

        for item in plan["plans"]:
            self.assertGreaterEqual(item["entry"]["high"], item["entry"]["low"])
            self.assertLess(item["stopLoss"], item["entry"]["low"])
            self.assertGreater(item["targets"][0]["price"], item["entry"]["high"])
            self.assertGreater(item["targets"][1]["price"], item["targets"][0]["price"])
            self.assertGreater(item["riskReward"], 0)


class GrowthDriverTests(SimpleTestCase):
    def test_extract_screener_shareholding_keeps_latest_four_quarters(self):
        html = """
        <section id="shareholding">
          <table>
            <thead>
              <tr>
                <th></th><th>Jun 2025</th><th>Sep 2025</th><th>Dec 2025</th><th>Mar 2026</th><th>Jun 2026</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Promoters +</td><td>51.00%</td><td>51.25%</td><td>51.40%</td><td>51.40%</td><td>51.60%</td></tr>
              <tr><td>FIIs +</td><td>12.00%</td><td>12.30%</td><td>12.10%</td><td>12.80%</td><td>13.20%</td></tr>
              <tr><td>DIIs +</td><td>18.00%</td><td>18.10%</td><td>18.35%</td><td>18.20%</td><td>18.00%</td></tr>
              <tr><td>Public +</td><td>19.00%</td><td>18.35%</td><td>18.15%</td><td>17.60%</td><td>17.20%</td></tr>
            </tbody>
          </table>
        </section>
        """

        shareholding = services.extract_screener_shareholding(html)
        promoters = next(row for row in shareholding["rows"] if row["name"] == "Promoters")
        fii = next(row for row in shareholding["rows"] if row["name"] == "FIIs")

        self.assertEqual([quarter["period"] for quarter in promoters["quarters"]], ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"])
        self.assertEqual(promoters["quarters"][-1]["value"], 0.516)
        self.assertEqual(fii["quarters"][-1]["value"], 0.132)

    def test_extract_groww_shareholding_maps_institution_rows(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"stockData":{"shareHoldingPattern":{
          "Dec '25":{
            "promoters":{"individual":{"percent":50.01}},
            "foreignInstitutions":{"percent":19.09},
            "otherDomesticInstitutions":{"insurance":{"percent":10.66}},
            "mutualFunds":{"percent":9.52},
            "retailAndOthers":{"percent":10.73}
          },
          "Mar '26":{
            "promoters":{"individual":{"percent":50}},
            "foreignInstitutions":{"percent":18.67},
            "otherDomesticInstitutions":{"insurance":{"percent":10.77}},
            "mutualFunds":{"percent":9.78},
            "retailAndOthers":{"percent":10.79}
          }
        }}}}}
        </script>
        """

        shareholding = services.extract_groww_shareholding(html)
        promoters = next(row for row in shareholding["rows"] if row["name"] == "Promoters")
        dii = next(row for row in shareholding["rows"] if row["name"] == "DIIs")

        self.assertEqual(shareholding["source"], "Groww public shareholding")
        self.assertEqual(promoters["quarters"][-1]["period"], "Mar 2026")
        self.assertEqual(promoters["quarters"][-1]["value"], 0.5)
        self.assertAlmostEqual(dii["quarters"][-1]["value"], 0.2055)

    def test_extract_upstox_shareholding_merges_mutual_funds_into_diis(self):
        html = """
        {\\"shareHolderType\\":\\"Promoters\\",\\"history\\":[{\\"period\\":\\"Mar 2026\\",\\"totalPercent\\":50}]}
        {\\"shareHolderType\\":\\"Foreign institutions-FII\\",\\"history\\":[{\\"period\\":\\"Mar 2026\\",\\"totalPercent\\":18.67}]}
        {\\"shareHolderType\\":\\"Other domestic institutions\\",\\"history\\":[{\\"period\\":\\"Mar 2026\\",\\"totalPercent\\":10.77}]}
        {\\"shareHolderType\\":\\"Mutual Funds\\",\\"history\\":[{\\"period\\":\\"Mar 2026\\",\\"totalPercent\\":9.78}]}
        """

        shareholding = services.extract_upstox_shareholding(html)
        fii = next(row for row in shareholding["rows"] if row["name"] == "FIIs")
        dii = next(row for row in shareholding["rows"] if row["name"] == "DIIs")

        self.assertEqual(shareholding["source"], "Upstox public shareholding")
        self.assertEqual(fii["quarters"][-1]["value"], 0.1867)
        self.assertAlmostEqual(dii["quarters"][-1]["value"], 0.2055)

    def test_merge_shareholding_fills_missing_categories_from_fallback(self):
        primary = {
            "periods": ["Mar 2026"],
            "rows": [{"name": "Promoters", "quarters": [{"period": "Mar 2026", "value": 0.5}]}],
            "source": "Screener.in shareholding",
        }
        fallback = {
            "periods": ["Mar 2026"],
            "rows": [{"name": "FIIs", "quarters": [{"period": "Mar 2026", "value": 0.18}]}],
            "source": "Groww public shareholding",
        }

        merged = services.merge_shareholding(primary, fallback)
        self.assertIn("Screener.in shareholding", merged["source"])
        self.assertIn("Groww public shareholding", merged["source"])
        self.assertTrue(services.has_shareholding_categories(merged, ("Promoters", "FIIs")))

    def test_build_ownership_trend_adds_quarter_change_flags(self):
        shareholding = {
            "periods": ["Dec 2025", "Mar 2026"],
            "source": "Screener.in shareholding",
            "rows": [
                {"name": "Promoters", "quarters": [{"period": "Dec 2025", "value": 0.507}, {"period": "Mar 2026", "value": 0.5}]},
                {"name": "FIIs", "quarters": [{"period": "Dec 2025", "value": 0.12}, {"period": "Mar 2026", "value": 0.128}]},
                {"name": "DIIs", "quarters": [{"period": "Dec 2025", "value": 0.18}, {"period": "Mar 2026", "value": 0.179}]},
            ],
        }

        trend = services.build_ownership_trend(shareholding, {})
        promoters = next(row for row in trend["rows"] if row["name"] == "Promoters")
        titles = {flag["title"] for flag in trend["flags"]}

        self.assertEqual(promoters["quarterChangePoints"], -0.7)
        self.assertIn("Promoter holding reduced", titles)
        self.assertIn("FIIs accumulation", titles)


class RecommendationFeedTests(SimpleTestCase):
    def test_extract_screener_quarterly_results_summarizes_latest_quarter(self):
        html = """
        <section id="quarters">
          <table>
            <thead>
              <tr>
                <th></th><th>Jun 2025</th><th>Sep 2025</th><th>Dec 2025</th><th>Mar 2026</th><th>Jun 2026</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Sales +</td><td>1,000</td><td>1,100</td><td>1,150</td><td>1,250</td><td>1,400</td></tr>
              <tr><td>Operating Profit</td><td>200</td><td>230</td><td>240</td><td>260</td><td>310</td></tr>
              <tr><td>OPM %</td><td>20%</td><td>21%</td><td>21%</td><td>21%</td><td>22%</td></tr>
              <tr><td>Net Profit +</td><td>100</td><td>120</td><td>130</td><td>150</td><td>180</td></tr>
              <tr><td>EPS in Rs</td><td>10.0</td><td>12.0</td><td>13.0</td><td>15.0</td><td>18.0</td></tr>
            </tbody>
          </table>
        </section>
        """

        result = services.extract_screener_quarterly_results(html)

        self.assertTrue(result["available"])
        self.assertEqual(result["period"], "Jun 2026")
        self.assertEqual(result["sales"], 1400.0)
        self.assertEqual(result["netProfit"], 180.0)
        self.assertEqual(result["salesQoqPercent"], 12.0)
        self.assertEqual(result["netProfitYoyPercent"], 80.0)
        self.assertIn("Jun 2026 results", result["summary"])

    def test_build_recommendation_uses_analyst_and_fii_signals(self):
        candles = [
            {"high": 96 + index, "low": 90 + index * 0.6, "close": 92 + index * 0.25}
            for index in range(35)
        ]
        summary = {
            "financialData": {
                "currentPrice": {"raw": 100.0},
                "targetMeanPrice": {"raw": 125.0},
                "targetHighPrice": {"raw": 136.0},
                "recommendationMean": {"raw": 1.8},
                "recommendationKey": "buy",
                "numberOfAnalystOpinions": {"raw": 18},
            },
            "upgradeDowngradeHistory": {
                "history": [
                    {
                        "firm": "Goldman Sachs",
                        "action": "up",
                        "toGrade": "Buy",
                        "epochGradeDate": 1770000000,
                    }
                ]
            },
        }
        ownership = {
            "source": "Screener.in shareholding",
            "rows": [
                {
                    "name": "FIIs",
                    "latest": 0.132,
                    "changePoints": 1.2,
                    "quarterChangePoints": 0.8,
                    "latestPeriod": "Mar 2026",
                }
            ],
        }

        row = services.build_recommendation_from_inputs(
            {"symbol": "ABC.NS", "name": "ABC Ltd"},
            {},
            summary,
            candles,
            ownership,
            {"available": True, "period": "Jun 2026", "summary": "Jun 2026 results; sales INR 1,400 cr.", "source": "Screener.in quarterly results"},
        )

        self.assertEqual(row["symbol"], "ABC.NS")
        self.assertEqual(row["sourceType"], "Analyst + FII/DII")
        self.assertIn("Goldman Sachs", row["recommendedBy"])
        self.assertIn("Foreign institutional investor group", row["fundGroup"])
        self.assertEqual(row["recommenderDetails"][1]["name"], "FIIs")
        self.assertIn("mutual funds", services.RECOMMENDATION_GROUP_DETAILS["DIIs"])
        self.assertEqual(row["sellPrice"], 125.0)
        self.assertEqual(row["duration"], "6-12 months")
        self.assertEqual(row["quarterlyResults"]["period"], "Jun 2026")
        self.assertGreater(row["score"], 70)

    def test_build_recommendation_rejects_weak_analyst_signal_without_ownership(self):
        row = services.build_recommendation_from_inputs(
            {"symbol": "WEAK.NS", "name": "Weak Ltd", "nsePrice": 100.0},
            {},
            {
                "financialData": {
                    "targetMeanPrice": {"raw": 101.0},
                    "recommendationMean": {"raw": 3.9},
                    "recommendationKey": "sell",
                }
            },
            [],
            {"rows": []},
        )

        self.assertIsNone(row)

    def test_recommendation_cache_key_is_scoped_to_ist_date(self):
        before_midnight_utc = datetime(2026, 6, 18, 18, 0, tzinfo=timezone.utc)
        after_midnight_ist = datetime(2026, 6, 18, 19, 0, tzinfo=timezone.utc)

        self.assertEqual(
            services.recommendation_cache_key(before_midnight_utc),
            "recommendations:2026-06-18",
        )
        self.assertEqual(
            services.recommendation_cache_key(after_midnight_ist),
            "recommendations:2026-06-19",
        )

    def test_clear_recommendations_cache_clears_all_daily_keys(self):
        services._cache["recommendations:2026-06-18"] = {"data": {}, "expiresAt": 9999999999}
        services._cache["recommendations:2026-06-19"] = {"data": {}, "expiresAt": 9999999999}
        services._cache["analysis:ABC.NS"] = {"data": {}, "expiresAt": 9999999999}

        services.clear_recommendations_cache()

        self.assertNotIn("recommendations:2026-06-18", services._cache)
        self.assertNotIn("recommendations:2026-06-19", services._cache)
        self.assertIn("analysis:ABC.NS", services._cache)
        services.clear_cache("analysis:ABC.NS")

    def test_build_daily_recommendation_from_nifty500_scan(self):
        candles = self._daily_recommendation_candles()

        with patch("core.services.rsi", return_value=[None] * (len(candles) - 1) + [60.0]):
            row = services.build_daily_recommendation_from_inputs(
                {
                    "symbol": "DAILY.NS",
                    "name": "Daily Setup Ltd",
                    "nsePrice": 110.0,
                    "nseChangePercent": 3.5,
                    "nseVolume": 2_500_000,
                    "nseYearHigh": 111.0,
                    "nseYearLow": 92.0,
                },
                candles,
            )

        self.assertIsNotNone(row)
        self.assertEqual(row["sourceType"], "Daily Nifty 500")
        self.assertEqual(row["duration"], "3-15 sessions")
        self.assertGreater(row["sellPrice"], row["buyPrice"])
        self.assertIn("20-day breakout", row["reason"])

    def test_build_recommendations_merges_daily_nifty500_rows(self):
        candles = self._daily_recommendation_candles()
        with patch("core.services.rsi", return_value=[None] * (len(candles) - 1) + [60.0]):
            daily_row = services.build_daily_recommendation_from_inputs(
                {
                    "symbol": "DAILY.NS",
                    "name": "Daily Setup Ltd",
                    "nsePrice": 110.0,
                    "nseChangePercent": 3.5,
                    "nseVolume": 2_500_000,
                    "nseYearHigh": 111.0,
                    "nseYearLow": 92.0,
                },
                candles,
            )

        with (
            patch("core.services.recommendation_universe", return_value=[]),
            patch("core.services.safe_daily_recommendations", return_value={
                "scannedCount": 1,
                "failedCount": 0,
                "results": [daily_row],
            }),
            patch("core.services.safe_intraday_recommendations", return_value={
                "scannedCount": 0,
                "failedCount": 0,
                "results": [],
            }),
        ):
            payload = services.build_recommendations()

        self.assertEqual(payload["results"][0]["symbol"], "DAILY.NS")
        self.assertIn("daily Nifty 500", payload["summary"])
        self.assertEqual(payload["scannedCount"], 1)

    def test_nifty500_universe_falls_back_to_constituent_csv(self):
        csv_text = (
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Mahindra & Mahindra Ltd.,Automobile,M&M,EQ,INE101A01026\n"
            "Daily Setup Ltd,Capital Goods,DAILY,EQ,INE000A01000\n"
        )

        with (
            patch("core.services.fetch_nse_stock_index_payload", side_effect=RuntimeError("NSE India returned 404.")),
            patch("core.services.fetch_text_once", return_value=csv_text),
            patch("core.services.get_quotes", return_value={
                "M&M.NS": {
                    "regularMarketPrice": 3123.45,
                    "regularMarketChangePercent": 1.23,
                    "regularMarketVolume": 123456,
                    "fiftyTwoWeekHigh": 3300.0,
                    "fiftyTwoWeekLow": 2400.0,
                },
            }),
        ):
            rows = services.build_nifty500_primary_universe()

        by_symbol = {row["symbol"]: row for row in rows}
        self.assertEqual(services.normalize_symbol("M&M.NS"), "M&M.NS")
        self.assertIn("M&M.NS", by_symbol)
        self.assertEqual(by_symbol["M&M.NS"]["nsePrice"], 3123.45)
        self.assertIn("Automobile", by_symbol["M&M.NS"]["tags"])

    @staticmethod
    def _daily_recommendation_candles():
        candles = []
        for index in range(59):
            close = 100.0 + (index % 8) * 0.45
            candles.append({
                "open": close - 0.15,
                "high": close + 0.55,
                "low": close - 0.55,
                "close": close,
                "volume": 1_000_000,
            })
        candles[-1] = {
            "open": 103.5,
            "high": 104.6,
            "low": 103.0,
            "close": 104.0,
            "volume": 1_000_000,
        }
        candles.append({
            "open": 106.0,
            "high": 111.0,
            "low": 105.5,
            "close": 110.0,
            "volume": 2_500_000,
        })
        return candles

    def test_build_intraday_recommendation_flags_volume_breakout_long(self):
        candles = self._intraday_base_candles()
        previous_close = candles[-2]["close"]
        candles[-1] = {
            "open": previous_close + 0.4,
            "high": 111.0,
            "low": previous_close,
            "close": 110.0,
            "volume": 3_400_000,
        }

        row = services.build_intraday_recommendation_from_inputs(
            {
                "symbol": "LONG.NS",
                "name": "Long Setup Ltd",
                "nsePrice": 110.0,
                "nseChangePercent": 5.8,
                "nseVolume": 3_400_000,
                "nseYearHigh": 111.0,
                "nseYearLow": 88.0,
                "tags": ["Nifty 500"],
            },
            candles,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["direction"], "Long")
        self.assertGreaterEqual(row["score"], 58)
        self.assertGreaterEqual(row["expectedMovePercent"], 3)
        self.assertLessEqual(row["expectedMovePercent"], 5)
        self.assertGreater(row["volumeRatio"], 3)
        self.assertIn("Volume is", row["reason"])

    def test_build_intraday_recommendation_flags_volume_breakdown_short(self):
        candles = self._intraday_base_candles(start=122.0, step=-0.04)
        previous_close = candles[-2]["close"]
        candles[-1] = {
            "open": previous_close - 0.5,
            "high": previous_close,
            "low": 107.5,
            "close": 108.0,
            "volume": 3_200_000,
        }

        row = services.build_intraday_recommendation_from_inputs(
            {
                "symbol": "SHORT.NS",
                "name": "Short Setup Ltd",
                "nsePrice": 108.0,
                "nseChangePercent": -8.3,
                "nseVolume": 3_200_000,
                "nseYearHigh": 140.0,
                "nseYearLow": 105.0,
                "tags": ["Nifty 500"],
            },
            candles,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["direction"], "Short")
        self.assertGreaterEqual(row["score"], 58)
        self.assertGreaterEqual(row["expectedMovePercent"], 3)
        self.assertLessEqual(row["expectedMovePercent"], 5)
        self.assertGreater(row["deliveryProxy"], 3)
        self.assertIn("Price is down", row["reason"])

    def test_build_intraday_recommendation_rejects_weak_volume_setup(self):
        candles = self._intraday_base_candles()
        previous_close = candles[-2]["close"]
        candles[-1] = {
            "open": previous_close,
            "high": previous_close + 0.4,
            "low": previous_close - 0.4,
            "close": previous_close + 0.2,
            "volume": 950_000,
        }

        row = services.build_intraday_recommendation_from_inputs(
            {
                "symbol": "QUIET.NS",
                "name": "Quiet Ltd",
                "nsePrice": previous_close + 0.2,
                "nseChangePercent": 0.2,
                "nseVolume": 950_000,
                "nseYearHigh": 115.0,
                "nseYearLow": 90.0,
                "tags": ["Nifty 500"],
            },
            candles,
        )

        self.assertIsNone(row)

    def test_build_intraday_recommendation_skips_malformed_provider_candles(self):
        candles = self._intraday_base_candles()
        candles.insert(5, {"open": None, "high": None, "low": None, "close": None, "volume": None})
        candles.insert(12, {"open": 105.0, "high": 102.0, "low": 104.0, "close": 103.0, "volume": 1_000_000})
        previous_close = candles[-2]["close"]
        candles[-1] = {
            "open": previous_close + 0.4,
            "high": 111.0,
            "low": previous_close,
            "close": 110.0,
            "volume": 3_400_000,
        }

        row = services.build_intraday_recommendation_from_inputs(
            {
                "symbol": "MESSY.NS",
                "name": "Messy Provider Ltd",
                "nsePrice": 110.0,
                "nseChangePercent": 5.8,
                "nseVolume": 3_400_000,
                "nseYearHigh": 111.0,
                "nseYearLow": 88.0,
                "tags": ["Nifty 500"],
            },
            candles,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["symbol"], "MESSY.NS")

    def test_intraday_recommendations_reports_failed_symbol_scans(self):
        def candidate(stock):
            if stock["symbol"] == "BAD.NS":
                raise RuntimeError("provider failed")
            return {
                "symbol": "GOOD.NS",
                "analysisSymbol": "GOOD.NS",
                "name": "Good Ltd",
                "direction": "Long",
                "score": 72,
                "volumeRatio": 2.0,
                "changePercent": 3.4,
            }

        with (
            patch("core.services.intraday_recommendation_universe", return_value=[
                {"symbol": "GOOD.NS", "name": "Good Ltd"},
                {"symbol": "BAD.NS", "name": "Bad Ltd"},
            ]),
            patch("core.services.build_intraday_recommendation_candidate", side_effect=candidate),
        ):
            payload = services.build_intraday_recommendations()

        self.assertEqual(payload["scannedCount"], 2)
        self.assertEqual(payload["failedCount"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertIn("could not be checked", payload["summary"])

    def test_build_daily_recommendation_flags_short_term_breakout(self):
        candles = self._daily_recommendation_candles()

        with patch("core.services.rsi", return_value=[None] * (len(candles) - 1) + [60.0]):
            row = services.build_daily_recommendation_from_inputs(
                {
                    "symbol": "BREAKOUT.NS",
                    "name": "Breakout Ltd",
                    "nsePrice": 110.0,
                    "nseChangePercent": 3.5,
                    "nseVolume": 2_500_000,
                    "nseYearHigh": 111.0,
                    "nseYearLow": 90.0,
                },
                candles,
            )

        self.assertIsNotNone(row)
        self.assertEqual(row["sourceType"], "Daily Nifty 500")
        self.assertEqual(row["duration"], "3-15 sessions")
        self.assertGreaterEqual(row["upsidePercent"], 4)
        self.assertIn("Nifty 500 daily technical scan", row["recommendedBy"])

    def test_recommendation_cache_key_rotates_daily_and_clear_removes_all_days(self):
        first = services.recommendation_cache_key(datetime(2026, 6, 18, 23, 55, tzinfo=ZoneInfo("Asia/Kolkata")))
        second = services.recommendation_cache_key(datetime(2026, 6, 19, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")))

        self.assertNotEqual(first, second)

        services.set_cached(first, {"day": "old"}, 60)
        services.set_cached(second, {"day": "new"}, 60)
        services.clear_recommendations_cache()

        self.assertIsNone(services.get_cached(first))
        self.assertIsNone(services.get_cached(second))

    @staticmethod
    def _intraday_base_candles(start=100.0, step=0.08, count=60):
        candles = []
        for index in range(count):
            close = start + index * step
            candles.append({
                "open": close - 0.1,
                "high": close + 0.6,
                "low": close - 0.6,
                "close": close,
                "volume": 1_000_000,
            })
        return candles


class RelativeStrengthTests(SimpleTestCase):
    def test_build_relative_strength_compares_stock_with_benchmark(self):
        candles = [{"close": 100 + index * 1.0} for index in range(130)]
        benchmark = {
            "symbol": "^NSEI",
            "name": "Nifty 50",
            "candles": [{"close": 100 + index * 0.25} for index in range(130)],
        }

        result = services.build_relative_strength(candles, benchmark)

        self.assertTrue(result["available"])
        self.assertEqual(result["benchmarkName"], "Nifty 50")
        self.assertGreater(result["averageSpread"], 0)
        self.assertEqual(len(result["rows"]), 4)


class CandlestickPatternTests(SimpleTestCase):
    def test_latest_candle_detects_bullish_marubozu(self):
        result = services.analyze_latest_candle([
            {"date": "2026-05-08", "open": 96.0, "high": 100.0, "low": 94.0, "close": 95.0},
            {"date": "2026-05-11", "open": 100.0, "high": 112.0, "low": 99.8, "close": 111.6},
        ])

        self.assertTrue(result["available"])
        self.assertEqual(result["pattern"], "Bullish Marubozu")
        self.assertIn("continue upward", result["nextCandleExpectation"])
        self.assertEqual(result["confirmationLevel"], 112.0)

    def test_latest_candle_detects_shooting_star(self):
        result = services.analyze_latest_candle([
            {"date": "2026-05-08", "open": 100.0, "high": 104.0, "low": 99.0, "close": 103.0},
            {"date": "2026-05-11", "open": 104.0, "high": 116.0, "low": 102.8, "close": 103.2},
        ])

        self.assertTrue(result["available"])
        self.assertEqual(result["pattern"], "Shooting Star")
        self.assertIn("close below", result["nextCandleExpectation"])
        self.assertEqual(result["invalidationLevel"], 116.0)


class MarketClockTests(SimpleTestCase):
    def setUp(self):
        # The clock resolves its calendar from the exchange, so without this the
        # test would reach the network and change behaviour whenever NSE amends
        # the holiday list.
        services.clear_cache_prefix("market:holidays")
        self.addCleanup(services.clear_cache_prefix, "market:holidays")
        patcher = patch.object(
            services,
            "resolve_market_holidays",
            lambda market: {date(2026, 5, 28): "Bakri Id"} if market == "india" else {},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_market_clock_marks_indian_regular_session_open(self):
        now = datetime(2026, 5, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        clock = services.build_market_clock(
            "RELIANCE.NS",
            {"marketState": "REGULAR", "exchange": "NSI"},
            {},
            now=now,
            market_status={},
        )

        self.assertEqual(clock["market"], "india")
        self.assertTrue(clock["isOpen"])
        self.assertEqual(clock["status"], "open")
        self.assertEqual(clock["timezone"], "Asia/Kolkata")
        self.assertTrue(clock["sessionCloseAt"].startswith("2026-05-12T10:00:00"))

    def test_market_clock_marks_nse_holiday(self):
        now = datetime(2026, 5, 28, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

        clock = services.build_market_clock(
            "RELIANCE.NS",
            {"exchange": "NSI"},
            {},
            now=now,
            market_status={},
        )

        self.assertFalse(clock["isOpen"])
        self.assertTrue(clock["isHoliday"])
        self.assertEqual(clock["status"], "holiday")
        self.assertIn("Bakri Id", clock["holidayName"])
        self.assertTrue(clock["nextOpenAt"].startswith("2026-05-29T03:45:00"))


class TradingHolidayResolutionTests(SimpleTestCase):
    """Where the holiday calendar comes from, and what happens when it doesn't."""

    HOLIDAY_PAYLOAD = {
        "CM": [
            {"tradingDate": "15-Jan-2026", "description": "Municipal Corporation Election - Maharashtra"},
            {"tradingDate": "26-Jan-2026", "description": "Republic Day"},
            {"tradingDate": "14-Sep-2026", "description": "Ganesh Chaturthi"},
        ],
        # A different segment, which must not leak into the equities calendar.
        "FO": [{"tradingDate": "01-Jul-2026", "description": "Derivatives-only closure"}],
    }

    def setUp(self):
        services.clear_cache_prefix("market:holidays")
        self.addCleanup(services.clear_cache_prefix, "market:holidays")

    def test_the_exchange_calendar_is_used_and_names_the_holiday(self):
        with patch.object(services, "fetch_nse_json_with_session", return_value=self.HOLIDAY_PAYLOAD):
            holidays = services.market_holidays("india")
        self.assertEqual(holidays[date(2026, 1, 26)], "Republic Day")

    def test_a_holiday_declared_mid_year_is_picked_up(self):
        # The hardcoded table this replaced had no way to learn about an
        # election holiday announced after it was written, so the clock called a
        # closed Thursday a trading day.
        with patch.object(services, "fetch_nse_json_with_session", return_value=self.HOLIDAY_PAYLOAD):
            holidays = services.market_holidays("india")
        self.assertIn(date(2026, 1, 15), holidays)

    def test_only_the_cash_market_segment_is_read(self):
        with patch.object(services, "fetch_nse_json_with_session", return_value=self.HOLIDAY_PAYLOAD):
            holidays = services.market_holidays("india")
        self.assertNotIn(date(2026, 7, 1), holidays)

    def test_future_holidays_are_included_so_the_next_open_is_right(self):
        with patch.object(services, "fetch_nse_json_with_session", return_value=self.HOLIDAY_PAYLOAD):
            holidays = services.market_holidays("india")
        self.assertIn(date(2026, 9, 14), holidays)

    def test_the_calendar_falls_back_to_inference_when_the_exchange_is_down(self):
        candles = {
            "^NSEI": ["2026-01-22", "2026-01-23", "2026-01-27"],
            "^BSESN": ["2026-01-22", "2026-01-23", "2026-01-27"],
            "RELIANCE.NS": ["2026-01-22", "2026-01-23", "2026-01-27"],
        }
        with patch.object(services, "fetch_nse_json_with_session", side_effect=RuntimeError("blocked")), \
             patch.object(services, "get_chart_range",
                          lambda symbol, *a, **k: {"candles": [{"date": d} for d in candles[symbol]]}):
            holidays = services.market_holidays("india")
        # Monday the 26th is absent from every series, so it was a closure.
        self.assertIn(date(2026, 1, 26), holidays)

    def test_a_gap_in_one_series_alone_is_not_called_a_holiday(self):
        # Yahoo's index history has occasional single-day holes. 28-Aug-2026 is
        # missing from both indices yet RELIANCE traded, so requiring unanimity
        # is what stops a data gap being reported as a closure.
        candles = {
            "^NSEI": ["2026-08-27", "2026-08-31"],
            "^BSESN": ["2026-08-27", "2026-08-31"],
            "RELIANCE.NS": ["2026-08-27", "2026-08-28", "2026-08-31"],
        }
        with patch.object(services, "fetch_nse_json_with_session", side_effect=RuntimeError("blocked")), \
             patch.object(services, "get_chart_range",
                          lambda symbol, *a, **k: {"candles": [{"date": d} for d in candles[symbol]]}):
            holidays = services.market_holidays("india")
        self.assertNotIn(date(2026, 8, 28), holidays)

    def test_no_calendar_and_no_history_claims_no_holidays(self):
        # Weekends still close the market; inventing holidays would be worse
        # than admitting we have none.
        with patch.object(services, "fetch_nse_json_with_session", side_effect=RuntimeError("blocked")), \
             patch.object(services, "get_chart_range", side_effect=RuntimeError("no data")):
            self.assertEqual(services.market_holidays("india"), {})

    def test_an_unknown_market_has_no_calendar_rather_than_a_borrowed_one(self):
        self.assertEqual(services.market_holidays(None), {})

    def test_the_exchange_date_format_is_parsed(self):
        self.assertEqual(services.parse_nse_holiday_date("26-Jan-2026"), date(2026, 1, 26))
        self.assertEqual(services.parse_nse_holiday_date("2026-01-26"), date(2026, 1, 26))
        self.assertIsNone(services.parse_nse_holiday_date(""))
        self.assertIsNone(services.parse_nse_holiday_date("not a date"))


class PerformanceCacheTests(SimpleTestCase):
    def setUp(self):
        services._cache.clear()

    def tearDown(self):
        services._cache.clear()

    @patch("core.services.build_nse_market_snapshot")
    def test_safe_nse_market_snapshot_reuses_short_ttl_cache(self, build_snapshot):
        build_snapshot.return_value = {
            "available": True,
            "source": "NSE India public market APIs",
            "generatedAt": "2026-05-22T10:00:00+05:30",
        }

        first = services.safe_nse_market_snapshot()
        second = services.safe_nse_market_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(build_snapshot.call_count, 1)


class MarketMonitorEndpointTests(SimpleTestCase):
    def setUp(self):
        services._cache.clear()

    def tearDown(self):
        services._cache.clear()

    def test_market_monitor_reuses_cached_live_payload_while_refreshing(self):
        live_payload = {
            "generatedAt": "2026-05-22T10:00:00+05:30",
            "refreshing": True,
            "liveMode": True,
            "breakoutCandidates": [],
        }

        with (
            patch("core.services.start_market_monitor_refresh") as start_refresh,
            patch("core.services.is_market_monitor_refreshing", side_effect=[False, True]),
            patch("core.services.build_live_market_monitor", return_value=live_payload) as build_live,
        ):
            first = self.client.get("/api/market-monitor?live=1")
            second = self.client.get("/api/market-monitor?live=1")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), live_payload)
        self.assertEqual(second.json(), live_payload)
        self.assertEqual(start_refresh.call_count, 1)
        self.assertEqual(build_live.call_count, 1)


class OpenInterestTests(SimpleTestCase):
    def test_build_open_interest_report_summarizes_expiry_horizons(self):
        payload = {
            "records": {
                "timestamp": "13-May-2026 10:25:00",
                "underlyingValue": 1000,
                "expiryDates": ["14-May-2026", "28-May-2026", "25-Jun-2026"],
                "data": [
                    {
                        "expiryDate": "14-May-2026",
                        "strikePrice": 980,
                        "CE": {"openInterest": 120, "changeinOpenInterest": 10, "totalTradedVolume": 50, "impliedVolatility": 20},
                        "PE": {"openInterest": 240, "changeinOpenInterest": 30, "totalTradedVolume": 70, "impliedVolatility": 22},
                    },
                    {
                        "expiryDate": "14-May-2026",
                        "strikePrice": 1020,
                        "CE": {"openInterest": 300, "changeinOpenInterest": 45, "totalTradedVolume": 90, "impliedVolatility": 19},
                        "PE": {"openInterest": 100, "changeinOpenInterest": -5, "totalTradedVolume": 40, "impliedVolatility": 21},
                    },
                    {
                        "expiryDate": "28-May-2026",
                        "strikePrice": 1000,
                        "CE": {"openInterest": 200, "changeinOpenInterest": 20, "totalTradedVolume": 60},
                        "PE": {"openInterest": 220, "changeinOpenInterest": 35, "totalTradedVolume": 80},
                    },
                    {
                        "expiryDate": "25-Jun-2026",
                        "strikePrice": 1040,
                        "CE": {"openInterest": 500, "changeinOpenInterest": 100, "totalTradedVolume": 120},
                        "PE": {"openInterest": 150, "changeinOpenInterest": 15, "totalTradedVolume": 55},
                    },
                ],
            },
        }

        report = services.build_open_interest_report("ABC", payload, today=date(2026, 5, 13))

        self.assertTrue(report["available"])
        self.assertEqual(report["periods"]["day"]["totalCallOi"], 420)
        self.assertEqual(report["periods"]["day"]["totalPutOi"], 340)
        self.assertEqual(report["periods"]["day"]["totalCallVolume"], 140)
        self.assertEqual(report["periods"]["day"]["totalPutVolume"], 110)
        self.assertEqual(report["periods"]["day"]["volumePcr"], 0.79)
        self.assertEqual(report["periods"]["day"]["volumeBias"], "Call volume dominates")
        self.assertIn("call positions are more active", report["periods"]["day"]["volumeSummary"])
        self.assertEqual(report["periods"]["day"]["maxCallOiStrike"], 1020)
        self.assertEqual(report["periods"]["month"]["totalCallOi"], 620)
        self.assertEqual(report["periods"]["quarter"]["totalCallOi"], 1120)
        self.assertEqual(report["periods"]["quarter"]["totalPutOi"], 710)
        self.assertTrue(report["periods"]["quarter"]["callPutVolumeSplitAvailable"])
        self.assertEqual(report["periods"]["day"]["rows"][0]["volumeBias"], "Call volume dominates")

    def test_build_open_interest_report_accepts_nse_v3_expiry_shape(self):
        payload = {
            "records": {
                "timestamp": "13-May-2026 15:30:00",
                "underlyingValue": 1361.2,
                "data": [
                    {
                        "strikePrice": 1360,
                        "CE": {"expiryDate": "26-05-2026", "openInterest": 900, "changeinOpenInterest": 50, "totalTradedVolume": 80},
                        "PE": {"expiryDate": "26-05-2026", "openInterest": 1200, "changeinOpenInterest": 70, "totalTradedVolume": 120},
                    }
                ],
            },
        }

        report = services.build_open_interest_report("RELIANCE", payload, today=date(2026, 5, 13))

        self.assertTrue(report["available"])
        self.assertEqual(report["expiryDates"][0]["label"], "26-05-2026")
        self.assertEqual(report["periods"]["day"]["totalCallVolume"], 80)
        self.assertEqual(report["periods"]["day"]["totalPutVolume"], 120)
        self.assertEqual(report["periods"]["day"]["volumePcr"], 1.5)
        self.assertIn("put positions are more active", report["periods"]["day"]["volumeSummary"])

    def test_oi_spurt_fallback_builds_day_aggregate_view(self):
        services.clear_cache("nse-oi-spurts-underlyings")
        payload = {
            "timestamp": "13-May-2026 15:20:00",
            "data": [
                {
                    "symbol": "RELIANCE",
                    "latestOI": 410741,
                    "prevOI": 400145,
                    "changeInOI": 10596,
                    "avgInOI": 2.65,
                    "volume": 211592,
                    "optValue": 136702549855,
                    "underlyingValue": 1361,
                }
            ],
        }

        with patch("core.services.fetch_nse_json", return_value=payload):
            report = services.get_oi_spurt_open_interest("RELIANCE")

        self.assertTrue(report["available"])
        self.assertTrue(report["periods"]["day"]["aggregateOnly"])
        self.assertEqual(report["periods"]["day"]["totalOi"], 410741)
        self.assertEqual(report["periods"]["day"]["changeOi"], 10596)
        self.assertEqual(report["periods"]["day"]["volume"], 211592)
        self.assertFalse(report["periods"]["day"]["callPutVolumeSplitAvailable"])
        self.assertIn("not call/put split", report["periods"]["day"]["volumeSummary"])
        self.assertFalse(report["periods"]["week"]["available"])



class MarketSnapshotTests(SimpleTestCase):
    def test_build_nse_market_snapshot_from_payloads_normalizes_exchange_data(self):
        payloads = {
            "marketStatus": {
                "marketState": [
                    {
                        "market": "Capital Market",
                        "marketStatus": "Open",
                        "tradeDate": "11-May-2026 10:25",
                        "index": "NIFTY 50",
                        "last": 23869.45,
                        "variation": -306.7,
                        "percentChange": -1.27,
                    }
                ],
                "marketcap": {"timeStamp": "08-May-2026", "marketCapinLACCRRupees": 473.63},
                "giftnifty": {"LASTPRICE": 23845.5, "DAYCHANGE": -414, "PERCHANGE": -1.71},
            },
            "allIndices": {
                "timestamp": "11-May-2026 10:24",
                "advances": 200,
                "declines": 100,
                "unchanged": 10,
                "data": [
                    {
                        "key": "BROAD MARKET INDICES",
                        "index": "NIFTY 50",
                        "last": "23,869.45",
                        "variation": "-306.70",
                        "percentChange": "-1.27",
                        "advances": 10,
                        "declines": 40,
                        "pe": "22.5",
                    },
                    {
                        "key": "SECTORAL INDICES",
                        "index": "NIFTY IT",
                        "last": "29,318.00",
                        "variation": "-76.20",
                        "percentChange": "-0.26",
                    },
                ],
            },
            "gainers": {
                "NIFTY": {"data": [{"symbol": "TECHM", "ltp": 1550, "net_price": 45, "perChange": 3, "trade_quantity": 1000}]},
                "allSec": {"data": [{"symbol": "TEST", "ltp": 100, "net_price": 5, "perChange": 5, "trade_quantity": 1000}]},
            },
            "losers": {
                "NIFTY": {"data": [{"symbol": "TITAN", "ltp": 3200, "net_price": -64, "perChange": -2, "trade_quantity": 900}]},
                "allSec": {"data": [{"symbol": "FAIL", "ltp": 50, "net_price": -2, "perChange": -4, "trade_quantity": 900}]},
            },
            "nifty50": {
                "data": [
                    {"symbol": "NIFTY 50", "lastPrice": "23,869.45", "pChange": "-1.27"},
                    {"symbol": "RELIANCE", "lastPrice": 1400, "change": 28, "pChange": 2.0, "totalTradedVolume": 10000, "dayHigh": 1410, "dayLow": 1375},
                    {"symbol": "TCS", "lastPrice": 3900, "change": -39, "pChange": -1.0, "totalTradedVolume": 8000, "dayHigh": 3950, "dayLow": 3880},
                ]
            },
            "mostActive": {"data": [{"symbol": "ACTIVE", "lastPrice": 99, "pChange": 1.2, "totalTradedVolume": 5000}]},
            "weekHighs": {"dataLtpGreater20": [{"symbol": "HIGH", "new52WHL": 120, "ltp": 119, "pChange": 2.1}]},
            "priceBands": {"AllSec": {"count": [{"key": "TOTAL", "value": 3}], "data": [{"symbol": "BAND", "ltp": 10, "priceBand": 5}]}},
        }

        snapshot = services.build_nse_market_snapshot_from_payloads(payloads)

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["breadth"]["advanceDeclineRatio"], 2.0)
        self.assertEqual(snapshot["indices"][0]["name"], "NIFTY 50")
        self.assertEqual(snapshot["sectorIndices"][0]["name"], "NIFTY IT")
        self.assertEqual(snapshot["topGainers"][0]["symbol"], "TECHM")
        self.assertEqual(snapshot["topLosers"][0]["symbol"], "TITAN")
        self.assertNotIn("TEST", [row["symbol"] for row in snapshot["topGainers"]])
        self.assertEqual(snapshot["priceBands"]["count"][0]["label"], "Total")

    def test_nse_movers_fall_back_to_nifty50_index_constituents(self):
        payloads = {
            "gainers": {"allSec": {"data": [{"symbol": "OUTSIDE", "ltp": 100, "net_price": 5, "perChange": 5}]}},
            "losers": {"NIFTY": {"data": []}},
            "nifty50": {
                "data": [
                    {"symbol": "NIFTY 50", "lastPrice": 23869, "pChange": 0.5},
                    {"symbol": "AAA", "lastPrice": 100, "change": 3, "pChange": 3, "totalTradedVolume": 1000},
                    {"symbol": "BBB", "lastPrice": 200, "change": -4, "pChange": -2, "totalTradedVolume": 2000},
                ]
            },
        }

        snapshot = services.build_nse_market_snapshot_from_payloads(payloads)

        self.assertEqual([row["symbol"] for row in snapshot["topGainers"]], ["AAA"])
        self.assertEqual([row["symbol"] for row in snapshot["topLosers"]], ["BBB"])

    def test_nse_mover_notes_explain_when_nifty_is_not_moving_that_direction(self):
        payloads = {
            "gainers": {"NIFTY": {"data": []}},
            "losers": {"NIFTY": {"data": []}},
            "nifty50": {"data": [{"symbol": "NIFTY 50", "lastPrice": 23869, "pChange": 0.5}]},
        }

        snapshot = services.build_nse_market_snapshot_from_payloads(payloads)

        self.assertEqual(snapshot["topGainers"], [])
        self.assertEqual(snapshot["topLosers"], [])
        self.assertIn("not showing positive movers", snapshot["topGainersNote"])
        self.assertIn("not showing negative movers", snapshot["topLosersNote"])

    def test_nse_index_movers_only_use_nifty50_constituents(self):
        payload = {
            "data": [
                {"symbol": "NIFTY 50", "lastPrice": 23869, "pChange": 0.5},
                {"symbol": "AAA", "lastPrice": 100, "change": 3, "pChange": 3, "totalTradedVolume": 1000},
                {"symbol": "BBB", "lastPrice": 200, "change": 1, "pChange": 1, "totalTradedVolume": 2000},
                {"symbol": "CCC", "lastPrice": 90, "change": -2, "pChange": -2, "totalTradedVolume": 900},
                {"symbol": "DDD", "lastPrice": 120, "change": 0, "pChange": 0, "totalTradedVolume": 1200},
            ]
        }

        gainers = services.normalize_nse_index_movers(payload, "gainers", limit=10)
        losers = services.normalize_nse_index_movers(payload, "losers", limit=10)

        self.assertEqual([row["symbol"] for row in gainers], ["AAA", "BBB"])
        self.assertEqual([row["symbol"] for row in losers], ["CCC"])

    def test_fetch_nse_sector_constituents_finds_best_and_worst_stock(self):
        payload = {
            "timestamp": "22-May-2026 10:25",
            "data": [
                {"symbol": "NIFTY IT", "lastPrice": 30000, "pChange": 1.2},
                {"symbol": "AAA", "meta": {"companyName": "AAA Tech"}, "lastPrice": 100, "change": 3, "pChange": 3, "totalTradedVolume": 1000},
                {"symbol": "BBB", "meta": {"companyName": "BBB Tech"}, "lastPrice": 200, "change": -2, "pChange": -1, "totalTradedVolume": 2000},
                {"symbol": "CCC", "meta": {"companyName": "CCC Tech"}, "lastPrice": 150, "change": 0, "pChange": 0, "totalTradedVolume": 1500},
            ],
        }

        with patch("core.services.fetch_nse_json", return_value=payload):
            result = services.fetch_nse_sector_constituents("NIFTY IT")

        self.assertEqual(result["symbols"], ["AAA", "BBB", "CCC"])
        self.assertEqual(result["bestStock"]["symbol"], "AAA")
        self.assertEqual(result["worstStock"]["symbol"], "BBB")
        self.assertEqual(result["advance"], 1)
        self.assertEqual(result["decline"], 1)
        self.assertEqual(result["unchanged"], 1)

    def test_sector_stock_performance_summarizes_stock_vs_sector(self):
        nse_snapshot = {
            "sectorIndices": [
                {
                    "name": "NIFTY IT",
                    "last": 30000,
                    "change": 300,
                    "changePercent": 1.0,
                    "high": 30200,
                    "low": 29600,
                    "pe": 28,
                    "oneMonthChange": 4.5,
                    "oneYearChange": 12,
                }
            ]
        }
        sector_performance = {
            "NIFTY IT": {
                "stockCount": 3,
                "advance": 2,
                "decline": 1,
                "unchanged": 0,
                "bestStock": {"symbol": "AAA", "name": "AAA Tech", "price": 100, "changePercent": 3.0},
                "worstStock": {"symbol": "BBB", "name": "BBB Tech", "price": 200, "changePercent": -1.0},
            }
        }

        report = services.build_sector_stock_performance(nse_snapshot, sector_performance)
        nifty_it = next(row for row in report["rows"] if row["sector"] == "NIFTY IT")

        self.assertTrue(report["available"])
        self.assertEqual(nifty_it["bestStock"]["symbol"], "AAA")
        self.assertEqual(nifty_it["worstStock"]["symbol"], "BBB")
        self.assertIn("AAA outperformed the sector by +2.00 pp", nifty_it["summary"])

    def test_build_nifty500_primary_universe_normalizes_index_constituents(self):
        payload = {
            "data": [
                {"symbol": "NIFTY 500", "meta": {"companyName": "Nifty 500"}},
                {"symbol": "reliance", "meta": {"companyName": "Reliance Industries"}},
                {"symbol": "TCS", "companyName": "Tata Consultancy Services"},
                {"symbol": "RELIANCE", "companyName": "Duplicate row"},
            ]
        }

        with patch("core.services.fetch_nse_stock_index_payload", return_value=payload):
            universe = services.build_nifty500_primary_universe()

        self.assertEqual([stock["symbol"] for stock in universe], ["RELIANCE.NS", "TCS.NS"])
        self.assertEqual(universe[0]["name"], "Reliance Industries")
        self.assertEqual(universe[0]["tags"], ["Nifty 500"])
        self.assertEqual(universe[0]["nsePrice"], None)

    def test_build_nifty500_primary_universe_falls_back_to_constituent_csv(self):
        csv_text = "Company Name,Industry,Symbol\nReliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE\nTata Consultancy Services Ltd.,Information Technology,TCS\n"

        with patch("core.services.fetch_nse_stock_index_payload", side_effect=RuntimeError("NSE India returned 404.")), \
            patch("core.services.fetch_text_once", return_value=csv_text), \
            patch("core.services.get_quotes", return_value={}):
            universe = services.build_nifty500_primary_universe()

        self.assertEqual([stock["symbol"] for stock in universe], ["RELIANCE.NS", "TCS.NS"])
        self.assertEqual(universe[0]["name"], "Reliance Industries Ltd.")
        self.assertIn("Oil Gas & Consumable Fuels", universe[0]["tags"])

    def test_primary_scan_prefilter_limits_to_ranked_nifty500_candidates(self):
        weak_rows = [
            {
                "symbol": f"WEAK{index}.NS",
                "name": f"Weak {index}",
                "tags": ["Nifty 500"],
                "nsePrice": 50,
                "nseYearHigh": 100,
                "nseYearLow": 40,
                "nseChangePercent": -1,
                "nseVolume": 1000,
                "nseValue": 1000,
            }
            for index in range(services.NIFTY_500_PRIMARY_SCAN_LIMIT)
        ]
        leader = {
            "symbol": "LEADER.NS",
            "name": "Leader",
            "tags": ["Nifty 500"],
            "nsePrice": 99,
            "nseYearHigh": 100,
            "nseYearLow": 60,
            "nseChangePercent": 2,
            "nseVolume": 5_000_000,
            "nseValue": 500_000_000,
        }

        selected = services.primary_scan_candidates_from_nifty500([*weak_rows, leader])
        symbols = [stock["symbol"] for stock in selected]

        self.assertEqual(len(selected), services.NIFTY_500_PRIMARY_SCAN_LIMIT)
        self.assertIn("LEADER.NS", symbols)
        self.assertNotIn(f"WEAK{services.NIFTY_500_PRIMARY_SCAN_LIMIT - 1}.NS", symbols)

    def test_fast_market_monitor_returns_ranked_nifty500_candidates(self):
        universe = [{
            "symbol": "LEADER.NS",
            "name": "Leader",
            "tags": ["Nifty 500"],
            "nsePrice": 99,
            "nseYearHigh": 100,
            "nseYearLow": 60,
            "nseChangePercent": 2,
            "nseVolume": 5_000_000,
            "nseValue": 500_000_000,
        }]

        with patch("core.services.safe_nse_market_snapshot", return_value={"available": True}), \
            patch("core.services.safe_nifty500_primary_universe", return_value=universe):
            report = services.build_fast_market_monitor(refreshing=True)

        self.assertTrue(report["refreshing"])
        self.assertTrue(report["fastMode"])
        self.assertEqual(report["breakoutCandidates"][0]["symbol"], "LEADER.NS")
        self.assertIn("chart scan pending", report["breakoutCandidates"][0]["signal"])

    def test_live_market_monitor_preserves_cached_detailed_sections(self):
        cached_detail = {
            "generatedAt": "2026-05-22T10:00:00Z",
            "source": "Detailed scan",
            "nseSnapshot": {"available": True, "timestamp": "old"},
            "orderCatalysts": [{"symbol": "HAL.NS", "headline": "Order win"}],
            "breakoutCandidates": [{"symbol": "LEADER.NS"}],
            "highVolumeCandidates": [{"symbol": "TCS.NS"}],
            "moneycontrolSectorAnalysis": {"available": True, "sectors": [{"sector": "IT"}]},
        }
        live_snapshot = {"available": True, "timestamp": "live", "mostActive": [{"symbol": "IDEA"}]}

        with patch("core.services.safe_nse_market_snapshot", return_value=live_snapshot):
            report = services.build_live_market_monitor(cached_detail, refreshing=True)

        self.assertTrue(report["liveMode"])
        self.assertTrue(report["refreshing"])
        self.assertEqual(report["detailGeneratedAt"], "2026-05-22T10:00:00Z")
        self.assertEqual(report["nseSnapshot"], live_snapshot)
        self.assertEqual(report["orderCatalysts"][0]["symbol"], "HAL.NS")
        self.assertEqual(report["breakoutCandidates"][0]["symbol"], "LEADER.NS")

    def test_market_monitor_primary_scan_uses_nifty500_universe_only(self):
        primary_universe = [{"symbol": "RELIANCE.NS", "name": "Reliance Industries", "tags": ["Nifty 500"]}]
        nse_snapshot = {
            "available": True,
            "topGainers": [{"symbol": "OUTSIDE", "name": "Outside Stock"}],
            "topLosers": [],
            "mostActive": [{"symbol": "ACTIVE", "name": "Active Stock"}],
            "weekHighs": [],
            "priceBands": {"rows": [{"symbol": "BAND", "name": "Band Stock"}]},
        }
        settle_calls = {}

        def fake_settle_map(items, mapper, concurrency=4):
            settle_calls.setdefault(mapper.__name__, []).append(list(items))
            return []

        with patch("core.services.safe_nse_market_snapshot", return_value=nse_snapshot), \
            patch("core.services.safe_moneycontrol_sector_snapshot", return_value={"available": False}), \
            patch("core.services.safe_sector_open_interest", return_value={"stockMoversBySector": {}}), \
            patch("core.services.safe_nifty500_primary_universe", return_value=primary_universe), \
            patch("core.services.safe_usd_inr_snapshot", return_value={"available": False}), \
            patch("core.services.settle_map", side_effect=fake_settle_map):
            report = services.build_market_monitor()

        primary_scan = settle_calls["scan_watchlist_stock"][0]
        self.assertEqual(primary_scan, primary_universe)
        self.assertNotIn("OUTSIDE.NS", [stock["symbol"] for stock in primary_scan])
        self.assertEqual(report["primaryScanUniverse"]["label"], "Nifty 500 only")
        self.assertEqual(report["primaryScanUniverse"]["scanned"], 1)

    def test_moneycontrol_sector_snapshot_maps_stock_sector(self):
        payload = {
            "allSectors": [
                {
                    "sector": "Software & IT Services",
                    "trend": "Neutral",
                    "stockCnt": 261,
                    "industryCnt": 6,
                    "advance": 75,
                    "decline": 168,
                    "currentMcap": "3,396,480",
                    "mCapPerChange": -0.47,
                    "mCapChange": "-16,122",
                    "sectorPe": "34.87",
                    "sectorNpYoy": "37,403",
                    "sectorNpYoyChange": 16.06,
                    "slug": "software-it-services",
                },
                {
                    "sector": "Capital Goods",
                    "trend": "Bullish",
                    "stockCnt": 300,
                    "industryCnt": 9,
                    "advance": 85,
                    "decline": 204,
                    "currentMcap": "2,148,058",
                    "mCapPerChange": -2.03,
                    "sectorPe": "127.03",
                    "sectorNpYoyChange": 42.31,
                    "slug": "capital-goods",
                },
            ],
            "sectorIndices": [{"indexName": "NIFTY IT", "ltp": "29,318.00", "changePer": "-0.26", "advance": 3, "decline": 7}],
        }

        snapshot = services.build_moneycontrol_sector_snapshot_from_payload(payload)
        match = services.match_moneycontrol_sector({"sector": "Technology", "industry": "Software"}, snapshot["sectors"])

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["breadth"]["stocks"], 561)
        self.assertEqual(match["sector"], "Software & IT Services")
        self.assertEqual(snapshot["sectorIndices"][0]["name"], "NIFTY IT")

    def test_moneycontrol_sector_snapshot_filters_movers_and_attaches_top_stocks(self):
        payload = {
            "allSectors": [
                {
                    "sector": f"Sector {index}",
                    "trend": "Neutral",
                    "stockCnt": 10,
                    "industryCnt": 2,
                    "advance": 5,
                    "decline": 5,
                    "mCapPerChange": index - 5,
                    "slug": f"sector-{index}",
                }
                for index in range(9)
            ] + [
                {
                    "sector": "Software & IT Services",
                    "trend": "Bullish",
                    "stockCnt": 20,
                    "industryCnt": 3,
                    "advance": 14,
                    "decline": 6,
                    "mCapPerChange": 8,
                    "slug": "software-it-services",
                }
            ],
            "sectorIndices": [],
        }

        snapshot = services.build_moneycontrol_sector_snapshot_from_payload(payload)
        enriched = services.attach_sector_top_stocks(snapshot, {
            "NIFTY IT": [{"symbol": "TCS", "changePercent": 2.5, "price": 3900}],
        })
        software = next(row for row in enriched["topPerforming"] if row["sector"] == "Software & IT Services")

        self.assertEqual(len(snapshot["topPerforming"]), 5)
        self.assertEqual(len(snapshot["underPerforming"]), 3)
        self.assertEqual(software["nseSector"], "NIFTY IT")
        self.assertEqual(software["topStocks"][0]["symbol"], "TCS")

    def test_sector_open_interest_aggregates_oi_by_nse_sector(self):
        oi_payload = {
            "timestamp": "14-May-2026 11:20:00",
            "data": [
                {"symbol": "TCS", "latestOI": 1000, "prevOI": 900, "changeInOI": 100, "volume": 300},
                {"symbol": "INFY", "latestOI": 800, "prevOI": 1000, "changeInOI": -200, "volume": 250},
                {"symbol": "RELIANCE", "latestOI": 1200, "prevOI": 1100, "changeInOI": 100, "volume": 400},
                {"symbol": "UNMAPPED", "latestOI": 100, "prevOI": 80, "changeInOI": 20, "volume": 10},
            ],
        }
        sector_map = {
            "symbols": {
                "TCS": "NIFTY IT",
                "INFY": "NIFTY IT",
                "RELIANCE": "NIFTY OIL & GAS",
            },
            "sectors": {"NIFTY IT": 2, "NIFTY OIL & GAS": 1},
        }

        report = services.build_sector_open_interest_from_payloads(oi_payload, sector_map)
        by_sector = {row["sector"]: row for row in report["rows"]}

        self.assertTrue(report["available"])
        self.assertEqual(report["coverage"]["mappedStocks"], 3)
        self.assertEqual(report["coverage"]["unmappedStocks"], 1)
        self.assertEqual(by_sector["NIFTY IT"]["latestOi"], 1800)
        self.assertEqual(by_sector["NIFTY IT"]["changeOi"], -100)
        self.assertEqual(by_sector["NIFTY IT"]["volume"], 550)
        self.assertEqual(by_sector["NIFTY OIL & GAS"]["topStocks"][0]["symbol"], "RELIANCE")
        self.assertEqual(report["totals"]["latestOi"], 3000)


class AssetAnalysisTests(SimpleTestCase):
    def test_local_asset_search_prefers_matching_etfs(self):
        results = services.local_asset_search_symbols("nifty", "etf")
        symbols = [item["symbol"] for item in results]

        self.assertIn("NIFTYBEES.NS", symbols)
        self.assertTrue(all(item["type"] == "ETF" for item in results if item["symbol"].endswith(".NS")))

    def test_parse_advisorkhoj_annual_returns_infers_current_year_columns(self):
        html = """
        <table id="tbl_scheme_returns">
          <thead>
            <tr><th>Scheme Name</th><th>AMC Name</th><th>Launch Date</th><th>AUM (Crore)</th><th>TER (%)</th><th>Returns as on - 05-06-2026 in %</th></tr>
            <tr><th class="th_yr_1">2021</th><th class="th_yr_2">2020</th><th class="th_yr_3">2019</th><th class="th_yr_4">2018</th><th class="th_yr_5">2017</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><a>Parag Parikh Flexi Cap Dir Gr</a> | <a>Invest Online</a></td>
              <td><a>PPFASMF</a></td><td>13-05-2013</td><td>90,123.45</td><td>0.63</td>
              <td>-3.21</td><td>10.5</td><td>24.25</td><td>31.1</td><td>-1.8</td>
            </tr>
          </tbody>
          <tfoot>
            <tr><td>Category Average</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-2.0</td><td>8.0</td><td>20.0</td><td>28.0</td><td>-4.0</td></tr>
            <tr><td>NIFTY 500 TRI</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-1.5</td><td>9.0</td><td>21.0</td><td>29.0</td><td>-3.0</td></tr>
          </tfoot>
        </table>
        """

        result = services.parse_advisorkhoj_annual_returns(html, "Equity: Flexi Cap", "Direct")
        matched = services.match_advisorkhoj_fund_row(
            result["funds"],
            "Parag Parikh Flexi Cap Fund Direct Plan Growth",
        )

        self.assertEqual(result["returnsAsOn"], "05-06-2026")
        self.assertEqual(result["years"], [2026, 2025, 2024, 2023, 2022])
        self.assertEqual(result["funds"][0]["label"], "Parag Parikh Flexi Cap Dir Gr")
        self.assertEqual(result["funds"][0]["returns"][0]["return"], -3.21)
        self.assertEqual(result["categoryAverage"]["latestReturn"], -2.0)
        self.assertEqual(matched["amc"], "PPFASMF")

    def test_build_asset_report_includes_profile_risk_and_plan(self):
        candles = [
            {
                "date": f"2025-01-{(index % 28) + 1:02d}",
                "open": 100 + index * 0.1,
                "high": 101 + index * 0.1,
                "low": 99 + index * 0.1,
                "close": 100 + index * 0.1,
                "volume": 100000 + index,
            }
            for index in range(260)
        ]
        summary = {
            "price": {"longName": "Test ETF", "currency": "USD"},
            "summaryDetail": {
                "annualReportExpenseRatio": {"raw": 0.0009},
                "totalAssets": {"raw": 1000000000},
                "yield": {"raw": 0.012},
            },
            "defaultKeyStatistics": {"ytdReturn": {"raw": 0.08}},
            "fundProfile": {"categoryName": "Large Blend", "family": "Test Family", "legalType": "ETF"},
            "topHoldings": {
                "holdings": [{"symbol": "AAA", "holdingName": "AAA Corp", "holdingPercent": {"raw": 0.12}}],
                "sectorWeightings": [{"technology": {"raw": 0.35}}],
            },
        }

        report = services.build_asset_report("TEST", "etf", {"currency": "USD"}, candles, {"quoteType": "ETF"}, summary)

        self.assertEqual(report["assetLabel"], "ETF")
        self.assertEqual(report["profile"]["category"], "Large Blend")
        self.assertEqual(report["holdings"]["top"][0]["symbol"], "AAA")
        self.assertTrue(report["plan"]["items"])
        self.assertGreaterEqual(report["scores"]["confidence"], 75)

    @patch("core.services.get_advisorkhoj_annual_returns")
    def test_build_mutual_fund_report_includes_advisorkhoj_comparison(self, annual_returns):
        annual_returns.return_value = {
            "available": True,
            "source": "AdvisorKhoj annual returns",
            "sourceUrl": "https://example.test/annual",
            "category": "Equity: Flexi Cap",
            "planType": "Direct",
            "returnsAsOn": "05-06-2026",
            "years": [2026, 2025],
            "funds": [
                {
                    "label": "Parag Parikh Flexi Cap Dir Gr",
                    "scheme": "Parag Parikh Flexi Cap Dir Gr",
                    "type": "Fund",
                    "amc": "PPFASMF",
                    "launchDate": "13-05-2013",
                    "aumCrore": 90123.45,
                    "terPercent": 0.63,
                    "returns": [{"year": 2026, "return": -3.21}, {"year": 2025, "return": 10.5}],
                    "latestReturn": -3.21,
                    "averageReturn": 3.65,
                    "rank": 3,
                }
            ],
            "categoryAverage": {
                "label": "Category Average",
                "type": "Comparator",
                "returns": [{"year": 2026, "return": -2.0}, {"year": 2025, "return": 8.0}],
                "latestReturn": -2.0,
                "averageReturn": 3.0,
            },
            "benchmark": None,
        }
        candles = [
            {
                "date": f"2025-01-{(index % 28) + 1:02d}",
                "open": 100 + index * 0.1,
                "high": 101 + index * 0.1,
                "low": 99 + index * 0.1,
                "close": 100 + index * 0.1,
                "volume": 100000 + index,
            }
            for index in range(260)
        ]

        report = services.build_asset_report(
            "TEST",
            "mutual-fund",
            {"currency": "INR"},
            candles,
            {"quoteType": "MUTUALFUND", "longName": "Parag Parikh Flexi Cap Fund Direct Plan Growth"},
            {
                "price": {"longName": "Parag Parikh Flexi Cap Fund Direct Plan Growth", "currency": "INR"},
                "fundProfile": {"categoryName": "Flexi Cap", "family": "PPFAS"},
            },
        )

        self.assertTrue(report["annualReturns"]["available"])
        self.assertTrue(report["annualReturns"]["matched"])
        self.assertEqual(report["annualReturns"]["comparisonRows"][0]["type"], "Selected fund")
        self.assertIn("AdvisorKhoj annual return comparison was available.", report["confidence"]["checks"])


class AssetNameResolutionTests(SimpleTestCase):
    """Yahoo mirrors the scheme id into shortName for Indian funds."""

    def test_symbol_echoed_as_a_name_is_rejected(self):
        name = services.display_name(["0P0000YWL1.BO", "Parag Parikh Flexi Cap Dir Gr"], "0P0000YWL1.BO")

        self.assertEqual(name, "Parag Parikh Flexi Cap Dir Gr")

    def test_symbol_echo_check_ignores_case_and_padding(self):
        self.assertEqual(services.display_name(["  0p0000ywl1.bo  ", "Real Fund"], "0P0000YWL1.BO"), "Real Fund")

    def test_symbol_is_the_last_resort_only(self):
        self.assertEqual(services.display_name([None, "", "   "], "VFIAX"), "VFIAX")

    def test_first_real_name_wins(self):
        self.assertEqual(services.display_name(["Vanguard 500", "Ignored"], "VFIAX"), "Vanguard 500")

    def test_chart_meta_name_is_used_when_quote_endpoints_are_empty(self):
        # Yahoo's quote/quoteSummary endpoints answer 401 anonymously, so the
        # chart meta is the only name source for most funds.
        candles = [
            {
                "date": f"2026-01-{(index % 28) + 1:02d}",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
            }
            for index in range(260)
        ]
        # A fund outside the curated universe, so the chart meta is the only
        # name source available.
        report = services.build_asset_report(
            "0P0001RO8V.BO",
            "mutual-fund",
            {"longName": "Parag Parikh Arbitrage Dir Gr", "shortName": "0P0001RO8V.BO"},
            candles,
            {},
            {},
        )

        self.assertEqual(report["longName"], "Parag Parikh Arbitrage Dir Gr")

    def test_a_curated_name_beats_a_provider_internal_code(self):
        candles = [
            {
                "date": f"2026-01-{(index % 28) + 1:02d}",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
            }
            for index in range(260)
        ]
        report = services.build_asset_report(
            "HDFCNIFTY.NS",
            "etf",
            {"longName": "HDFCAMC - HDFCNIFTY"},
            candles,
            {},
            {},
        )

        self.assertEqual(report["longName"], "HDFC Nifty 50 ETF")


class AssetResolutionTests(SimpleTestCase):
    """A bare word must not be trusted as a ticker on the ETF/MF tabs."""

    def test_qualified_tickers_and_fund_ids_are_treated_as_tickers(self):
        for symbol in ("NIFTYBEES.NS", "0P0000YWL1.BO", "MON100", "^NSEI"):
            self.assertTrue(services.looks_like_asset_ticker(symbol), symbol)

    def test_bare_words_are_not_treated_as_tickers(self):
        for symbol in ("GOLD", "NIFTY", "SILVER", ""):
            self.assertFalse(services.looks_like_asset_ticker(symbol), symbol)

    def test_a_bare_word_is_routed_through_search_instead_of_used_raw(self):
        # "gold" used to resolve to the US equity ticker GOLD on the mutual-fund
        # tab because it matched the ticker regex.
        with patch.object(services, "search_assets", return_value=[]) as search:
            self.assertEqual(services.resolve_asset_input("gold", "mutual-fund"), "")
        search.assert_called_once()

    def test_a_local_universe_hit_short_circuits_search(self):
        with patch.object(services, "search_assets") as search:
            self.assertEqual(services.resolve_asset_input("NIFTYBEES", "etf"), "NIFTYBEES.NS")
        search.assert_not_called()

    def test_wrong_asset_type_results_are_never_chosen(self):
        results = [{"symbol": "GOLD", "name": "Barrick Gold", "exchange": "NYSE", "type": "EQUITY"}]

        self.assertIsNone(services.choose_asset_search_result(results, "gold", "mutual-fund"))

    def test_matching_asset_type_is_chosen(self):
        results = [
            {"symbol": "GOLD", "name": "Barrick Gold", "exchange": "NYSE", "type": "EQUITY"},
            {"symbol": "VFIAX", "name": "Vanguard 500", "exchange": "Nasdaq", "type": "MUTUALFUND"},
        ]

        self.assertEqual(services.choose_asset_search_result(results, "vanguard", "mutual-fund")["symbol"], "VFIAX")


class AssetMatchPlausibilityTests(SimpleTestCase):
    """Resolution must not silently swap in a different fund."""

    def make(self, name, symbol="0P1.BO", tags=None):
        return {"symbol": symbol, "name": name, "exchange": "BSE", "type": "MUTUALFUND", "tags": tags or []}

    def test_a_different_fund_house_is_rejected(self):
        # This exact pair used to resolve, analysing the wrong fund.
        candidate = self.make("Nippon India Small Cap Fund Direct Growth")

        self.assertFalse(services.asset_match_is_plausible(candidate, "ICICI Prudential Value Fund Direct Growth"))

    def test_a_different_category_is_rejected(self):
        candidate = self.make("SBI Small Cap Fund Direct Growth")

        self.assertFalse(services.asset_match_is_plausible(candidate, "Motilal Oswal Midcap Fund Direct Growth"))

    def test_the_right_fund_is_accepted(self):
        candidate = self.make("Parag Parikh Flexi Cap Fund Direct Growth")

        self.assertTrue(services.asset_match_is_plausible(candidate, "Parag Parikh Flexi Cap"))

    def test_spacing_differences_are_tolerated(self):
        candidate = self.make("Parag Parikh Flexi Cap Fund Direct Growth")

        self.assertTrue(services.asset_match_is_plausible(candidate, "parag parikh flexicap"))

    def test_generic_scheme_words_alone_do_not_block_a_match(self):
        candidate = self.make("SBI Small Cap Fund Direct Growth")

        self.assertTrue(services.asset_match_is_plausible(candidate, "fund direct growth"))

    def test_a_tag_can_satisfy_a_query_word(self):
        candidate = self.make("Nippon India ETF Junior BeES", symbol="JUNIORBEES.NS", tags=["Nifty Next 50"])

        self.assertTrue(services.asset_match_is_plausible(candidate, "nifty next 50"))

    def test_an_implausible_result_is_not_chosen_even_when_the_type_matches(self):
        results = [self.make("Nippon India Small Cap Fund Direct Growth")]

        self.assertIsNone(services.choose_asset_search_result(results, "HDFC Balanced Advantage Fund", "mutual-fund"))


class AssetSuggestionFallbackTests(SimpleTestCase):
    """"Do you mean anything from below?" must have something below it."""

    def test_a_shared_word_offers_funds_from_the_same_house(self):
        with patch.object(services, "safe_search_assets", return_value=[]):
            results = services.asset_suggestions("SBI Bluechip Fund", "mutual-fund")

        self.assertTrue(results)
        self.assertTrue(any("SBI" in item["name"] for item in results))

    def test_an_unmatched_query_still_returns_the_curated_list(self):
        with patch.object(services, "safe_search_assets", return_value=[]):
            results = services.asset_suggestions("zzzzz nonexistent", "mutual-fund")

        self.assertTrue(results)

    def test_real_search_results_are_preferred_over_the_fallback(self):
        found = [{"symbol": "VFIAX", "name": "Vanguard 500", "exchange": "Nasdaq", "type": "MUTUALFUND"}]
        with patch.object(services, "safe_search_assets", return_value=found):
            results = services.asset_suggestions("vanguard", "mutual-fund")

        self.assertEqual(results, found)

    def test_etf_suggestions_stay_within_the_etf_universe(self):
        with patch.object(services, "safe_search_assets", return_value=[]):
            results = services.asset_suggestions("zzzzz nonexistent", "etf")

        self.assertTrue(all(item["type"] == "ETF" for item in results))


class AssetUniverseTests(SimpleTestCase):
    def test_tickers_without_usable_history_are_not_offered(self):
        # ICICINIFTY.NS 404s; NIFTYIETF.NS and AXISNIFTY.NS return only a few
        # daily candles, which fails the minimum-history check on analyze.
        symbols = {symbol for symbol, _name, _exchange, _tags in services.ETF_UNIVERSE}

        self.assertNotIn("ICICINIFTY.NS", symbols)
        self.assertNotIn("NIFTYIETF.NS", symbols)
        self.assertNotIn("AXISNIFTY.NS", symbols)
        self.assertIn("NIF100IETF.NS", symbols)

    def test_indian_funds_are_searchable_locally(self):
        for query in ("parag", "quant small", "uti nifty"):
            results = services.local_asset_search_symbols(query, "mutual-fund")
            self.assertTrue(results, query)

    def test_growth_plans_outrank_idcw_variants(self):
        results = [
            {"symbol": "0P1.BO", "name": "SBI Small Cap Fund Reg IDCW-R", "exchange": "BSE", "type": "MUTUALFUND"},
            {"symbol": "0P2.BO", "name": "SBI Small Cap Fund Dir Gr", "exchange": "BSE", "type": "MUTUALFUND"},
        ]

        ordered = services.sort_asset_search_results(results, "sbi small cap", "mutual-fund")

        self.assertEqual(ordered[0]["symbol"], "0P2.BO")

    def test_plan_ranking_does_not_apply_to_etfs(self):
        item = {"symbol": "GOLDBEES.NS", "name": "Nippon India ETF Gold BeES", "exchange": "NSE", "type": "ETF"}

        self.assertEqual(services.fund_plan_rank(item, "etf"), 0)


class InsufficientHistoryTests(SimpleTestCase):
    def test_thin_history_is_recognised(self):
        message = f"{services.INSUFFICIENT_HISTORY_PREFIX}: the data provider returned only 4 daily rows for X.NS"

        self.assertTrue(services.is_insufficient_history_error(message))

    def test_unrelated_errors_are_not_recognised(self):
        self.assertFalse(services.is_insufficient_history_error("Data provider returned 500."))
        self.assertFalse(services.is_insufficient_history_error(""))


def make_price_candles(closes, start_day=1, adjusted=True):
    """Daily candles from a close series, starting 2020-01-01 and skipping weekends."""
    candles = []
    cursor = date(2020, 1, 1) + timedelta(days=start_day - 1)
    for value in closes:
        while cursor.weekday() >= 5:
            cursor += timedelta(days=1)
        candle = {
            "date": cursor.isoformat(),
            "open": value, "high": value, "low": value, "close": value, "volume": 1000,
        }
        if adjusted:
            candle["adjClose"] = value
        candles.append(candle)
        cursor += timedelta(days=1)
    return candles


class AssetReturnSeriesTests(SimpleTestCase):
    """Long-horizon return maths has to run on a corporate-action-safe series."""

    def test_the_adjusted_close_is_preferred_when_it_covers_the_series(self):
        candles = make_price_candles([100.0] * 50)
        for candle in candles:
            candle["adjClose"] = 90.0

        rows, basis = services.build_asset_return_series(candles)

        self.assertTrue(basis["adjusted"])
        self.assertEqual([row["close"] for row in rows], [90.0] * 50)

    def test_a_partial_adjusted_series_falls_back_to_the_raw_close(self):
        candles = make_price_candles([100.0] * 50)
        for candle in candles[:20]:
            candle["adjClose"] = None

        rows, basis = services.build_asset_return_series(candles)

        self.assertFalse(basis["adjusted"])
        self.assertEqual([row["close"] for row in rows], [100.0] * 50)

    def test_an_unadjusted_split_truncates_the_history_before_it(self):
        # A 1:10 split that the provider failed to adjust: 40 sessions at 1000
        # then 40 at 100. Measuring across it would report a 90% "drawdown".
        candles = make_price_candles([1000.0] * 40 + [100.0] * 40, adjusted=False)

        rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["droppedSessions"], 40)
        self.assertEqual(len(rows), 40)
        self.assertTrue(all(row["close"] == 100.0 for row in rows))
        self.assertIsNotNone(basis["discontinuityDate"])
        self.assertIn("corporate action", basis["note"])

    def test_a_clean_series_is_kept_whole(self):
        candles = make_price_candles([100.0 + index for index in range(80)])

        rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["droppedSessions"], 0)
        self.assertIsNone(basis["discontinuityDate"])
        self.assertEqual(len(rows), 80)

    def test_a_real_crash_below_the_threshold_is_not_treated_as_a_split(self):
        candles = make_price_candles([100.0] * 30 + [70.0] * 30, adjusted=False)

        _rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["droppedSessions"], 0)

    def test_a_two_day_bad_divisor_glitch_is_repaired_not_truncated(self):
        # The real NIFTYBEES fault: two December 2019 sessions quoted at a tenth
        # of the surrounding price, then straight back. Truncating at the return
        # leg threw away every earlier session for the sake of two bad rows.
        candles = make_price_candles([100.0] * 40 + [10.0, 10.1] + [100.5] * 40, adjusted=False)

        rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["repairedSessions"], 2)
        self.assertEqual(basis["droppedSessions"], 0)
        self.assertIsNone(basis["discontinuityDate"])
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(row["close"] > 50 for row in rows))

    def test_a_repaired_glitch_no_longer_fakes_a_drawdown(self):
        candles = make_price_candles([100.0] * 40 + [10.0, 10.1] + [100.5] * 40, adjusted=False)
        rows, _basis = services.build_asset_return_series(candles)

        risk = services.build_asset_risk_report([row["close"] for row in rows])

        self.assertGreater(risk["maxDrawdown"], -5.0)

    def test_a_persistent_step_is_still_treated_as_a_split(self):
        # Does not revert, so there is no way to recover the earlier scale.
        candles = make_price_candles([1000.0] * 40 + [100.0] * 40, adjusted=False)

        _rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["repairedSessions"], 0)
        self.assertEqual(basis["droppedSessions"], 40)

    def test_a_spike_longer_than_the_glitch_window_is_treated_as_a_split(self):
        candles = make_price_candles([1000.0] * 30 + [100.0] * 20 + [1000.0] * 30, adjusted=False)

        _rows, basis = services.build_asset_return_series(candles)

        self.assertEqual(basis["repairedSessions"], 0)
        self.assertIsNotNone(basis["discontinuityDate"])

    def test_an_unadjusted_close_is_not_advertised_as_adjusted(self):
        # Yahoo returns adjClose identical to close for many Indian ETFs, which
        # is not an adjustment even though the field is populated.
        candles = make_price_candles([100.0 + index for index in range(60)])

        _rows, basis = services.build_asset_return_series(candles)

        self.assertFalse(basis["adjusted"])
        self.assertIn("no usable adjustment", basis["note"])
        self.assertIn("reinvests", basis["note"])

    def test_a_genuinely_adjusted_series_is_labelled_as_such(self):
        candles = make_price_candles([100.0 + index for index in range(60)])
        for candle in candles:
            candle["adjClose"] = candle["close"] * 0.92

        _rows, basis = services.build_asset_return_series(candles)

        self.assertTrue(basis["adjusted"])
        self.assertIn("dividend-adjusted", basis["note"])


class AssetRollingReturnTests(SimpleTestCase):
    def test_rolling_windows_describe_the_distribution_not_one_sample(self):
        # Steady 20% a year compounding: every rolling one-year window should
        # land near 20% and all of them should be positive.
        daily = 1.20 ** (1 / services.TRADING_SESSIONS_PER_YEAR)
        closes = [100.0 * daily ** index for index in range(services.TRADING_SESSIONS_PER_YEAR * 3)]

        rolling = services.build_asset_rolling_returns(closes)

        self.assertTrue(rolling["available"])
        self.assertEqual(rolling["positiveSharePercent"], 100.0)
        self.assertAlmostEqual(rolling["median"], 20.0, delta=0.5)
        self.assertAlmostEqual(rolling["worst"], 20.0, delta=0.5)

    def test_a_short_history_reports_why_rather_than_guessing(self):
        rolling = services.build_asset_rolling_returns([100.0] * 100)

        self.assertFalse(rolling["available"])
        self.assertIn("Needs more than", rolling["reason"])

    def test_percentiles_are_ordered(self):
        closes = [100.0 + (index % 90) for index in range(services.TRADING_SESSIONS_PER_YEAR * 2)]

        rolling = services.build_asset_rolling_returns(closes)

        self.assertLessEqual(rolling["worst"], rolling["percentile25"])
        self.assertLessEqual(rolling["percentile25"], rolling["median"])
        self.assertLessEqual(rolling["median"], rolling["percentile75"])
        self.assertLessEqual(rolling["percentile75"], rolling["best"])


class AssetRiskAdjustedTests(SimpleTestCase):
    def test_a_flat_return_above_the_risk_free_rate_reports_a_positive_excess(self):
        daily = 1.12 ** (1 / services.TRADING_SESSIONS_PER_YEAR)
        closes = [100.0 * daily ** index for index in range(800)]

        report = services.build_asset_risk_adjusted_report(closes, "INR")

        self.assertTrue(report["available"])
        self.assertEqual(report["riskFreeRatePercent"], services.ASSET_RISK_FREE_RATES["INR"])
        self.assertAlmostEqual(report["annualizedReturn"], 12.0, delta=0.5)
        self.assertGreater(report["excessReturn"], 0)

    def test_the_risk_free_rate_is_currency_aware_and_stated(self):
        self.assertEqual(services.risk_free_rate_for("INR"), services.ASSET_RISK_FREE_RATES["INR"])
        self.assertEqual(services.risk_free_rate_for("usd"), services.ASSET_RISK_FREE_RATES["USD"])
        self.assertEqual(services.risk_free_rate_for("ZZZ"), services.DEFAULT_RISK_FREE_RATE)

    def test_downside_deviation_ignores_upside_moves(self):
        rising = [100.0 * 1.01 ** index for index in range(300)]

        self.assertEqual(services.annualized_downside_deviation(rising), 0.0)
        self.assertGreater(services.annualized_volatility(rising), 0)

    def test_a_thin_series_reports_unavailable_rather_than_a_number(self):
        report = services.build_asset_risk_adjusted_report([100.0] * 30, "INR")

        self.assertFalse(report["available"])
        self.assertIn("Not enough history", report["reason"])


class AssetBenchmarkTests(SimpleTestCase):
    def make_benchmark(self, candles, symbol="^NSEI", name="Nifty 50"):
        return {"symbol": symbol, "name": name, "candles": candles}

    def test_a_perfect_tracker_reports_unit_beta_and_no_tracking_error(self):
        closes = [100.0 * 1.0004 ** index for index in range(600)]
        candles = make_price_candles(closes)
        rows, _basis = services.build_asset_return_series(candles)

        report = services.build_asset_benchmark_report(
            rows, self.make_benchmark(candles), "etf", "Example Nifty 50 ETF", ["Nifty 50"],
        )

        self.assertTrue(report["available"])
        self.assertTrue(report["isTrackingBenchmark"])
        self.assertEqual(report["trackingErrorLabel"], "Tracking error")
        self.assertAlmostEqual(report["beta"], 1.0, delta=0.01)
        self.assertAlmostEqual(report["trackingError"], 0.0, delta=0.01)
        # Matching the price index exactly means the fund handed back none of the
        # index's dividends, which is a shortfall of the index yield, not a draw.
        self.assertAlmostEqual(report["benchmarkPriceReturn"], report["fundReturn"], delta=0.01)
        self.assertAlmostEqual(report["excessReturn"], -services.INDEX_DIVIDEND_YIELDS["^NSEI"], delta=0.01)

    def test_the_reference_is_grossed_up_by_its_dividend_yield(self):
        closes = [100.0 * 1.0004 ** index for index in range(600)]
        candles = make_price_candles(closes)
        rows, _basis = services.build_asset_return_series(candles)

        report = services.build_asset_benchmark_report(
            rows, self.make_benchmark(candles), "etf", "Example Nifty 50 ETF", ["Nifty 50"],
        )

        yield_used = services.INDEX_DIVIDEND_YIELDS["^NSEI"]
        self.assertEqual(report["benchmarkDividendYield"], yield_used)
        self.assertAlmostEqual(
            report["benchmarkReturn"], report["benchmarkPriceReturn"] + yield_used, delta=0.02,
        )
        self.assertIn("price index", report["note"])

    def test_the_dividend_yield_assumption_is_per_index_with_a_fallback(self):
        self.assertEqual(services.index_dividend_yield_for("^GSPC"), services.INDEX_DIVIDEND_YIELDS["^GSPC"])
        self.assertEqual(services.index_dividend_yield_for("^nsei"), services.INDEX_DIVIDEND_YIELDS["^NSEI"])
        self.assertEqual(services.index_dividend_yield_for("^UNKNOWN"), services.DEFAULT_INDEX_DIVIDEND_YIELD)

    def test_an_unrelated_fund_reads_as_active_risk_not_tracking_error(self):
        fund = make_price_candles([100.0 * 1.0008 ** index for index in range(600)])
        index = make_price_candles([100.0 * 1.0002 ** index for index in range(600)])
        rows, _basis = services.build_asset_return_series(fund)

        report = services.build_asset_benchmark_report(
            rows, self.make_benchmark(index), "mutual-fund", "Example Gold Fund", ["Gold"],
        )

        self.assertFalse(report["isTrackingBenchmark"])
        self.assertEqual(report["trackingErrorLabel"], "Active risk")
        self.assertGreater(report["excessReturn"], 0)

    def test_nifty_next_50_is_not_mistaken_for_a_nifty_50_tracker(self):
        self.assertTrue(services.tracks_benchmark("^NSEI", "Example Nifty 50 Index Fund", []))
        self.assertFalse(services.tracks_benchmark("^NSEI", "Nippon India ETF Junior BeES", ["Nifty Next 50"]))
        self.assertFalse(services.tracks_benchmark("^NSEI", "Nippon India ETF Bank BeES", ["Banking"]))

    def test_dates_are_aligned_so_a_missing_nav_day_cannot_offset_the_series(self):
        fund = make_price_candles([100.0, 101.0, 102.0, 103.0, 104.0])
        index = [row for row in make_price_candles([200.0, 202.0, 204.0, 206.0, 208.0]) if row["date"] != fund[2]["date"]]

        paired = services.align_closes_by_date(fund, index)

        self.assertEqual(len(paired), 4)
        self.assertNotIn(fund[2]["date"], [row[0] for row in paired])

    def test_too_little_overlap_reports_unavailable(self):
        fund = make_price_candles([100.0] * 30)

        report = services.build_asset_benchmark_report(
            fund, self.make_benchmark(fund), "etf", "Example ETF", [],
        )

        self.assertFalse(report["available"])
        self.assertIn("matched dates", report["summary"])

    def test_a_missing_benchmark_is_reported_honestly(self):
        report = services.build_asset_benchmark_report(
            make_price_candles([100.0] * 300), None, "etf", "Example ETF", [],
        )

        self.assertFalse(report["available"])
        self.assertFalse(report["expected"])

    def test_capture_ratios_read_above_and_below_one_hundred(self):
        index_returns = [0.01, -0.01] * 40
        # Captures every up move fully and only half of each down move.
        fund_returns = [0.01 if value > 0 else -0.005 for value in index_returns]

        self.assertAlmostEqual(services.capture_ratio(fund_returns, index_returns, "up"), 100.0, places=6)
        self.assertAlmostEqual(services.capture_ratio(fund_returns, index_returns, "down"), 50.0, places=6)


class AssetSuitabilityTests(SimpleTestCase):
    def base_inputs(self):
        return {
            "momentum": {"score": 50, "label": "Neutral"},
            "risk": {"score": 60, "label": "Moderate risk"},
            "confidence": {"score": 80},
            "profile": {"expenseRatio": 0.005},
        }

    def test_data_confidence_no_longer_changes_the_investment_score(self):
        inputs = self.base_inputs()
        well_documented = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], {"score": 95}, inputs["profile"],
        )
        thinly_documented = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], {"score": 20}, inputs["profile"],
        )

        self.assertEqual(well_documented, thinly_documented)

    def test_a_better_sharpe_ratio_raises_the_score(self):
        inputs = self.base_inputs()
        weak = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], inputs["confidence"], inputs["profile"],
            {"sharpe": 0.0}, None, None,
        )
        strong = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], inputs["confidence"], inputs["profile"],
            {"sharpe": 1.5}, None, None,
        )

        self.assertGreater(strong, weak)

    def test_rolling_consistency_raises_the_score(self):
        inputs = self.base_inputs()
        erratic = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], inputs["confidence"], inputs["profile"],
            None, {"available": True, "positiveSharePercent": 40.0}, None,
        )
        consistent = services.score_asset_suitability(
            inputs["momentum"], inputs["risk"], inputs["confidence"], inputs["profile"],
            None, {"available": True, "positiveSharePercent": 95.0}, None,
        )

        self.assertGreater(consistent, erratic)

    def test_the_report_lists_what_it_could_and_could_not_include(self):
        report = services.build_asset_suitability_report(
            64, {"score": 80}, {"stale": False},
            {"sharpe": 0.9}, {"available": True}, {"available": False},
        )

        self.assertIn("Risk-adjusted return", report["included"])
        self.assertIn("Versus market reference", report["missing"])
        self.assertTrue(report["reliable"])

    def test_stale_data_marks_the_score_unreliable_without_changing_it(self):
        stale = services.build_asset_suitability_report(
            64, {"score": 80}, {"stale": True}, {"sharpe": 0.9}, {"available": True}, {"available": True},
        )

        self.assertEqual(stale["score"], 64)
        self.assertFalse(stale["reliable"])
        self.assertIn("indicative only", stale["note"])


class AssetProfileAvailabilityTests(SimpleTestCase):
    """The payload must say when factsheet fields are structurally unavailable."""

    def test_an_empty_summary_is_reported_as_unavailable(self):
        profile = services.extract_asset_profile({}, {}, {}, "etf")

        self.assertFalse(profile["detailsAvailable"])

    def test_a_populated_summary_is_reported_as_available(self):
        profile = services.extract_asset_profile(
            {"summaryDetail": {"annualReportExpenseRatio": {"raw": 0.0005}}}, {}, {}, "etf",
        )

        self.assertTrue(profile["detailsAvailable"])
        self.assertEqual(profile["expenseRatio"], 0.0005)

    def test_the_flag_survives_the_full_report(self):
        candles = make_price_candles([100.0 + index for index in range(400)])

        report = services.build_asset_report("TESTETF.NS", "etf", {}, candles, {}, {})

        self.assertIn("detailsAvailable", report["profile"])
        self.assertFalse(report["profile"]["detailsAvailable"])

    def test_nav_falls_back_to_the_latest_close(self):
        # The NAV is the last close, which always loads, so reporting it as
        # missing alongside the genuinely absent factsheet fields was wrong.
        candles = make_price_candles([100.0 + index for index in range(400)])

        report = services.build_asset_report("TESTFUND.BO", "mutual-fund", {}, candles, {}, {})

        self.assertEqual(report["profile"]["navPrice"], report["quote"]["price"])
        self.assertEqual(report["profile"]["navPrice"], candles[-1]["close"])


class AssetFreshnessTests(SimpleTestCase):
    def make_candles(self, latest_date):
        return [{"date": latest_date, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}]

    def test_a_recent_close_is_not_stale(self):
        today = datetime.now(tz=timezone.utc).date().isoformat()

        freshness = services.build_asset_freshness(self.make_candles(today), "etf")

        self.assertFalse(freshness["stale"])
        self.assertEqual(freshness["ageDays"], 0)

    def test_an_old_nav_is_flagged_as_stale(self):
        old = (datetime.now(tz=timezone.utc).date() - timedelta(days=90)).isoformat()

        freshness = services.build_asset_freshness(self.make_candles(old), "mutual-fund")

        self.assertTrue(freshness["stale"])
        self.assertIn("out of date", freshness["detail"])

    def test_mutual_funds_get_more_slack_than_etfs(self):
        self.assertGreater(
            services.MAX_ASSET_STALE_DAYS["mutual-fund"],
            services.MAX_ASSET_STALE_DAYS["etf"],
        )

    def test_a_stale_report_loses_confidence(self):
        fresh = services.build_asset_confidence_report(
            [{}] * 260, {}, [], {}, None, {"stale": False, "detail": "fresh"}
        )
        stale = services.build_asset_confidence_report(
            [{}] * 260, {}, [], {}, None, {"stale": True, "detail": "stale"}
        )

        self.assertLess(stale["score"], fresh["score"])

    def test_a_missing_date_does_not_raise(self):
        freshness = services.build_asset_freshness([], "etf")

        self.assertFalse(freshness["stale"])
        self.assertIsNone(freshness["ageDays"])

    def test_an_unparseable_date_does_not_raise(self):
        freshness = services.build_asset_freshness(self.make_candles("not-a-date"), "etf")

        self.assertFalse(freshness["stale"])
        self.assertIsNone(freshness["ageDays"])


class SearchSuggestionTests(SimpleTestCase):
    def test_local_suggestions_include_initial_character_matches(self):
        results = services.local_search_symbols("r")
        symbols = [item["symbol"] for item in results]

        self.assertIn("RELIANCE.NS", symbols)
        self.assertIn("RELIANCE.BO", symbols)

    def test_local_suggestions_include_fuzzy_stock_matches(self):
        results = services.local_search_symbols("relaince")
        symbols = [item["symbol"] for item in results]

        self.assertIn("RELIANCE.NS", symbols)

    def test_sort_search_results_prefers_nse_then_bse_then_other_exchanges(self):
        results = services.sort_search_results([
            {"symbol": "RELIANCE", "name": "Reliance", "exchange": "NYQ", "type": "EQUITY"},
            {"symbol": "RELIANCE.BO", "name": "Reliance Industries", "exchange": "BSE", "type": "EQUITY"},
            {"symbol": "RELIANCE.NS", "name": "Reliance Industries", "exchange": "NSE", "type": "EQUITY"},
        ], "rel")

        self.assertEqual([item["symbol"] for item in results], ["RELIANCE.NS", "RELIANCE.BO", "RELIANCE"])

    def test_resolve_symbol_prefers_local_nse_match_when_suffix_is_omitted(self):
        self.assertEqual(services.resolve_symbol_input("RELIANCE"), "RELIANCE.NS")

    def test_resolve_symbol_keeps_unknown_us_style_ticker(self):
        self.assertEqual(services.resolve_symbol_input("AAPL"), "AAPL")

    def test_resolve_asset_prefers_local_exchange_suffix_when_omitted(self):
        self.assertEqual(services.resolve_asset_input("NIFTYBEES", "etf"), "NIFTYBEES.NS")
        self.assertEqual(services.resolve_asset_input("QQQ", "etf"), "QQQ")


class StockHistoryWindowTests(SimpleTestCase):
    def make_candles(self, count):
        start = date(2025, 1, 1)
        candles = []
        for index in range(count):
            close = 100 + index * 0.35
            candles.append({
                "date": (start + timedelta(days=index)).isoformat(),
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100_000 + index,
            })
        return candles

    def test_analyze_symbol_accepts_short_history(self):
        candles = self.make_candles(45)
        with (
            patch("core.services.get_chart", return_value={"meta": {"currency": "USD"}, "candles": candles}),
            patch("core.services.get_quote", return_value={"currency": "USD", "longName": "Short History Inc"}),
            patch("core.services.get_summary", return_value={}),
            patch("core.services.get_sec_fundamentals", return_value={}),
            patch("core.services.get_screener_fundamentals", return_value={}),
            patch("core.services.get_stock_open_interest", return_value=services.open_interest_unavailable("SHORT")),
            patch("core.services.get_benchmark_chart", return_value={"symbol": "^GSPC", "name": "S&P 500", "candles": candles}),
        ):
            report = services._analyze_symbol("SHORT")

        self.assertEqual(len(report["series"]), 45)
        self.assertEqual(report["history"]["chartCandles"], 45)
        self.assertEqual(report["history"]["analysisCandles"], 45)
        self.assertEqual(report["quality"]["checks"][0]["status"], "Partial")
        self.assertIn("available history", " ".join(report["quality"]["warnings"]).lower())

    def test_build_report_caps_chart_to_one_year_with_extra_quarter_buffer(self):
        candles = self.make_candles(330)
        candles[-300]["high"] = 999.0

        report = services.build_report(
            "BUFFER",
            {"currency": "USD"},
            candles,
            {"currency": "USD", "longName": "Buffered Window Inc"},
            {},
            {},
            {},
            benchmark={"symbol": "^GSPC", "name": "S&P 500", "candles": candles},
            open_interest=services.open_interest_unavailable("BUFFER"),
        )

        self.assertEqual(len(report["series"]), services.STOCK_REPORT_MAX_SESSIONS)
        self.assertEqual(report["history"]["analysisCandles"], services.STOCK_ANALYSIS_MAX_SESSIONS)
        self.assertEqual(report["history"]["analysisBufferSessions"], services.STOCK_ANALYSIS_BUFFER_SESSIONS)
        self.assertEqual(report["series"][0]["date"], candles[-services.STOCK_REPORT_MAX_SESSIONS]["date"])
        self.assertNotEqual(report["quote"]["range"]["high52w"], 999.0)
        self.assertIsNotNone(report["series"][-1]["sma200"])


class QualityReportTests(SimpleTestCase):
    def test_quality_report_flags_stale_chart_and_quote_data(self):
        now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
        old_quote_time = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc).timestamp()
        data = {
            "candles": [{"date": "2026-05-20"} for _index in range(210)],
            "quote": {"regularMarketTime": old_quote_time},
            "fundamentals": {
                "marketCap": 1000.0,
                "trailingPE": 20.0,
                "priceToBook": 3.0,
                "profitMargins": 0.1,
                "returnOnEquity": 0.15,
                "returnOnCapitalEmployed": 0.16,
                "revenueGrowth": 0.08,
                "earningsGrowth": 0.09,
                "debtToEquity": 0.4,
                "dividendYield": 0.01,
                "targetMeanPrice": 120.0,
                "dataSource": "Yahoo Finance",
            },
            "technical": {"score": 70},
            "fundamental": {"score": 70},
            "eventRisk": {"score": 20},
            "supportResistance": {
                "supportZones": [{"price": 90.0}, {"price": 85.0}],
                "resistanceZones": [{"price": 110.0}, {"price": 120.0}],
            },
            "source": "Yahoo Finance public endpoints",
            "symbol": "RELIANCE.NS",
            "ownership": {
                "source": "Screener.in shareholding",
                "rows": [
                    {"name": "Promoters", "quarters": [{"period": "Mar 2026", "value": 0.5}]},
                    {"name": "FIIs", "quarters": [{"period": "Mar 2026", "value": 0.18}]},
                    {"name": "DIIs", "quarters": [{"period": "Mar 2026", "value": 0.2}]},
                ],
            },
            "relativeStrength": {
                "available": True,
                "benchmarkName": "Nifty 50",
                "averageSpread": 1.2,
                "label": "Outperforming",
                "summary": "Stock is ahead of benchmark.",
            },
            "now": now,
        }

        quality = services.build_quality_report(data)

        self.assertEqual(quality["freshness"]["chartAgeDays"], 12)
        self.assertTrue(any("stale" in warning.lower() for warning in quality["warnings"]))
        self.assertTrue(any(check["label"] == "Data freshness" and check["status"] == "Verify" for check in quality["checks"]))



class EndpointFamilyTests(SimpleTestCase):
    """The 401 memo has to group by route, not by symbol, or it never fires."""

    def test_a_path_symbol_is_stripped_so_all_symbols_share_one_entry(self):
        first = services.endpoint_family(
            "https://query2.finance.yahoo.com/v10/finance/quoteSummary/INFY.NS?modules=price"
        )
        second = services.endpoint_family(
            "https://query2.finance.yahoo.com/v10/finance/quoteSummary/TCS.NS?modules=price"
        )

        self.assertEqual(first, second)
        self.assertEqual(first, "query2.finance.yahoo.com/v10/finance/quoteSummary")

    def test_a_query_string_symbol_is_ignored(self):
        self.assertEqual(
            services.endpoint_family("https://query1.finance.yahoo.com/v7/finance/quote?symbols=INFY.NS"),
            "query1.finance.yahoo.com/v7/finance/quote",
        )

    def test_different_routes_on_one_host_stay_separate(self):
        # The chart endpoint works anonymously; suppressing it because quote is
        # unauthorized would break every report.
        quote_family = services.endpoint_family("https://query1.finance.yahoo.com/v7/finance/quote?symbols=X.NS")
        chart_family = services.endpoint_family("https://query1.finance.yahoo.com/v8/finance/chart/X.NS?range=2y")

        self.assertNotEqual(quote_family, chart_family)

    def test_an_index_symbol_is_stripped(self):
        self.assertEqual(
            services.endpoint_family("https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"),
            "query1.finance.yahoo.com/v8/finance/chart",
        )


class UnauthorizedEndpointMemoTests(SimpleTestCase):
    def setUp(self):
        services._unauthorized_endpoints.clear()
        self.addCleanup(services._unauthorized_endpoints.clear)

    def test_a_noted_endpoint_is_reported_unauthorized_for_any_symbol(self):
        services.note_unauthorized("https://query1.finance.yahoo.com/v7/finance/quote?symbols=INFY.NS")

        self.assertTrue(
            services.is_unauthorized_endpoint("https://query1.finance.yahoo.com/v7/finance/quote?symbols=SBIN.NS")
        )

    def test_an_unrelated_endpoint_is_unaffected(self):
        services.note_unauthorized("https://query1.finance.yahoo.com/v7/finance/quote?symbols=INFY.NS")

        self.assertFalse(
            services.is_unauthorized_endpoint("https://query1.finance.yahoo.com/v8/finance/chart/INFY.NS")
        )

    def test_the_memo_lapses_so_restored_access_is_picked_up(self):
        endpoint = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=INFY.NS"
        services._unauthorized_endpoints[services.endpoint_family(endpoint)] = time.time() - 1

        self.assertFalse(services.is_unauthorized_endpoint(endpoint))

    def test_a_suppressed_endpoint_makes_no_network_call(self):
        endpoint = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=INFY.NS"
        services.note_unauthorized(endpoint)

        with patch("core.services.urlopen") as urlopen:
            with self.assertRaises(RuntimeError):
                services.fetch_text(endpoint, json_request=True)

        urlopen.assert_not_called()


def chart_payload(symbol, **overrides):
    meta = {
        "symbol": symbol,
        "shortName": f"{symbol} SHORT",
        "longName": f"{symbol} Limited",
        "currency": "INR",
        "exchangeName": "NSI",
        "fullExchangeName": "NSE",
        "exchangeTimezoneName": "Asia/Kolkata",
        "instrumentType": "EQUITY",
        "regularMarketPrice": 1024.9,
        "regularMarketChangePercent": 0.93,
        "regularMarketVolume": 587177,
        "regularMarketTime": 1788238621,
        "regularMarketDayHigh": 1033.9,
        "regularMarketDayLow": 1020.6,
        "chartPreviousClose": 1053.0,
        "fiftyTwoWeekHigh": 1176.0,
        "fiftyTwoWeekLow": 702.4,
    }
    meta.update(overrides)
    return {"chart": {"result": [{"meta": meta}]}}


class QuoteFallbackTests(SimpleTestCase):
    """Yahoo's batch quote answers 401 to anonymous callers; the chart does not."""

    def test_the_chart_meta_supplies_the_fields_a_quote_would_have(self):
        with patch.object(services, "fetch_json", return_value=chart_payload("HINDALCO.NS")):
            result = services.quote_from_chart_meta("HINDALCO.NS")

        self.assertEqual(result["symbol"], "HINDALCO.NS")
        self.assertEqual(result["regularMarketPrice"], 1024.9)
        self.assertEqual(result["regularMarketChangePercent"], 0.93)
        self.assertEqual(result["previousClose"], 1053.0)
        self.assertEqual(result["fullExchangeName"], "NSE")
        self.assertEqual(result["quoteType"], "EQUITY")

    def test_an_exchange_code_without_a_name_is_still_given_one(self):
        payload = chart_payload("AAPL", exchangeName="NMS", fullExchangeName=None)
        with patch.object(services, "fetch_json", return_value=payload):
            result = services.quote_from_chart_meta("AAPL")

        self.assertEqual(result["fullExchangeName"], "NasdaqGS")

    def test_a_batch_that_is_refused_falls_back_per_symbol(self):
        def fetch(endpoint, sec=False):
            if "/v7/finance/quote" in endpoint:
                raise RuntimeError("Data provider returned 401.")
            symbol = endpoint.split("/chart/")[1].split("?")[0]
            return chart_payload(symbol)

        with patch.object(services, "fetch_json", side_effect=fetch):
            quotes = services.get_quotes(["HINDALCO.NS", "TATASTEEL.NS"])

        self.assertEqual(sorted(quotes), ["HINDALCO.NS", "TATASTEEL.NS"])
        self.assertEqual(quotes["TATASTEEL.NS"]["regularMarketPrice"], 1024.9)

    def test_a_batch_that_answers_is_not_second_guessed(self):
        batch = {"quoteResponse": {"result": [{"symbol": "HINDALCO.NS", "regularMarketPrice": 11.0}]}}
        with patch.object(services, "fetch_json", return_value=batch) as fetch:
            quotes = services.get_quotes(["HINDALCO.NS"])

        self.assertEqual(quotes["HINDALCO.NS"]["regularMarketPrice"], 11.0)
        self.assertEqual(fetch.call_count, 1)

    def test_only_the_symbols_the_batch_omitted_are_refetched(self):
        # A batch can answer for some symbols and silently drop others, which is
        # what a renamed or delisted ticker looks like from the outside.
        calls = []

        def fetch(endpoint, sec=False):
            calls.append(endpoint)
            if "/v7/finance/quote" in endpoint:
                return {"quoteResponse": {"result": [{"symbol": "HINDALCO.NS", "regularMarketPrice": 11.0}]}}
            return chart_payload(endpoint.split("/chart/")[1].split("?")[0])

        with patch.object(services, "fetch_json", side_effect=fetch):
            quotes = services.get_quotes(["HINDALCO.NS", "TATASTEEL.NS"])

        self.assertEqual(sorted(quotes), ["HINDALCO.NS", "TATASTEEL.NS"])
        self.assertEqual(len([call for call in calls if "/chart/" in call]), 1)
        self.assertIn("TATASTEEL.NS", calls[-1])

    def test_a_symbol_the_provider_has_retired_is_dropped_not_faked(self):
        def fetch(endpoint, sec=False):
            if "/v7/finance/quote" in endpoint:
                raise RuntimeError("Data provider returned 401.")
            if "TATAMOTORS.NS" in endpoint:
                raise RuntimeError("Data provider returned 404.")
            return chart_payload("HINDALCO.NS")

        with patch.object(services, "fetch_json", side_effect=fetch):
            quotes = services.get_quotes(["HINDALCO.NS", "TATAMOTORS.NS"])

        self.assertEqual(list(quotes), ["HINDALCO.NS"])

    def test_a_priceless_acknowledgement_is_not_treated_as_a_quote(self):
        # HPCL.NS answers 200 with the symbol echoed back and no prices at all.
        payload = chart_payload(
            "HPCL.NS",
            regularMarketPrice=None,
            currency=None,
            instrumentType="MUTUALFUND",
            exchangeName="YHD",
        )
        with patch.object(services, "fetch_json", return_value=payload):
            self.assertEqual(services.quote_from_chart_meta("HPCL.NS"), {})

    def test_a_single_quote_falls_back_the_same_way(self):
        def fetch(endpoint, sec=False):
            if "/v7/finance/quote" in endpoint:
                raise RuntimeError("Data provider returned 401.")
            return chart_payload("RELIANCE.NS")

        with patch.object(services, "fetch_json", side_effect=fetch):
            result = services.get_quote("RELIANCE.NS")

        self.assertEqual(result["regularMarketPrice"], 1024.9)


class WatchlistSymbolTests(SimpleTestCase):
    def test_no_watchlist_entry_still_points_at_the_pre_demerger_tata_motors(self):
        # TATAMOTORS.NS 404s since the demerger, so every report that reached for
        # it got a blank rather than a price.
        symbols = {stock["symbol"] for stock in services.BREAKOUT_WATCHLIST}
        symbols.update(services.unique_impact_symbols())

        self.assertNotIn("TATAMOTORS.NS", symbols)
        self.assertIn("TMCV.NS", symbols)
        self.assertIn("TMPV.NS", symbols)

    def test_hindustan_petroleum_is_referenced_by_its_traded_ticker(self):
        symbols = {stock["symbol"] for stock in services.BREAKOUT_WATCHLIST}
        symbols.update(services.unique_impact_symbols())

        self.assertNotIn("HPCL.NS", symbols)
        self.assertIn("HINDPETRO.NS", symbols)

    def test_every_impact_symbol_has_a_display_name(self):
        for symbol in services.unique_impact_symbols():
            self.assertNotEqual(services.watchlist_name(symbol), symbol, symbol)


class CacheEvictionTests(SimpleTestCase):
    def setUp(self):
        services._cache.clear()
        self.addCleanup(services._cache.clear)

    def test_the_cache_stays_within_its_ceiling(self):
        for index in range(services.MAX_CACHE_ENTRIES + 250):
            services.set_cached(f"evict-test:{index}", index, ttl=300)

        self.assertLessEqual(len(services._cache), services.MAX_CACHE_ENTRIES)

    def test_expired_entries_are_dropped_before_live_ones(self):
        services.set_cached("live-key", "live", ttl=300)
        services._cache["stale-key"] = {"data": "stale", "expiresAt": time.time() - 1}

        services.evict_cache_entries()

        self.assertIn("live-key", services._cache)
        self.assertNotIn("stale-key", services._cache)


class OptionChainExpiryFetchTests(SimpleTestCase):
    """The expiry legs now run concurrently, so order and partial failure matter."""

    def setUp(self):
        services._cache.clear()
        self.addCleanup(services._cache.clear)

    def fake_contract_info(self, expiries):
        return {"expiryDates": expiries}

    def test_strike_rows_follow_provider_expiry_order_not_completion_order(self):
        expiries = ["29-Sep-2026", "27-Oct-2026", "23-Nov-2026"]

        def fetch(path):
            if "contract-info" in path:
                return self.fake_contract_info(expiries)
            # The later expiry returns fastest, so completion order would invert.
            for index, expiry in enumerate(expiries):
                if quote_plus_safe(expiry) in path:
                    time.sleep(0.05 * (len(expiries) - index))
                    return {"records": {"timestamp": expiry, "underlyingValue": 100 + index,
                                        "data": [{"strikePrice": index}]}}
            raise AssertionError(f"unexpected path {path}")

        with patch("core.services.fetch_nse_json_with_session", side_effect=fetch):
            payload = services.get_nse_option_chain_payload("NTPC")

        strikes = [row["strikePrice"] for row in payload["records"]["data"]]
        self.assertEqual(strikes, [0, 1, 2])

    def test_one_failed_expiry_does_not_lose_the_others(self):
        expiries = ["29-Sep-2026", "27-Oct-2026"]

        def fetch(path):
            if "contract-info" in path:
                return self.fake_contract_info(expiries)
            if quote_plus_safe(expiries[0]) in path:
                raise RuntimeError("NSE India returned 503.")
            return {"records": {"timestamp": expiries[1], "underlyingValue": 101,
                                "data": [{"strikePrice": 1}]}}

        with patch("core.services.fetch_nse_json_with_session", side_effect=fetch):
            payload = services.get_nse_option_chain_payload("NTPC")

        self.assertEqual([row["strikePrice"] for row in payload["records"]["data"]], [1])

    def test_every_selected_expiry_is_requested_once(self):
        expiries = ["29-Sep-2026", "27-Oct-2026", "23-Nov-2026"]
        seen = []

        def fetch(path):
            if "contract-info" in path:
                return self.fake_contract_info(expiries)
            seen.append(path)
            return {"records": {"timestamp": "", "underlyingValue": None, "data": []}}

        with patch("core.services.fetch_nse_json_with_session", side_effect=fetch):
            services.get_nse_option_chain_payload("NTPC")

        self.assertEqual(len(seen), len(expiries))
        self.assertEqual(len(set(seen)), len(expiries))


def quote_plus_safe(value):
    """Match how the option-chain URL encodes an expiry label."""
    from urllib.parse import quote as url_quote

    return url_quote(value)


class SettleNamedLoadersTests(SimpleTestCase):
    def test_a_loader_that_raises_yields_an_empty_result(self):
        def boom():
            raise RuntimeError("upstream refused")

        results = services.settle_named_loaders({"ok": lambda: [1], "bad": boom})

        self.assertEqual(results["ok"], [1])
        self.assertEqual(results["bad"], {})

    def test_failure_reasons_are_captured_when_a_dict_is_supplied(self):
        # Without this the caller cannot tell a refused request from an upstream
        # that legitimately returned nothing, which left production outages
        # showing a generic message with no way to diagnose them.
        def boom():
            raise RuntimeError("NSE India returned 403.")

        errors = {}
        services.settle_named_loaders({"feed": boom}, errors=errors)

        self.assertEqual(errors, {"feed": "NSE India returned 403."})

    def test_an_exception_with_no_message_still_names_itself(self):
        def boom():
            raise TimeoutError()

        errors = {}
        services.settle_named_loaders({"feed": boom}, errors=errors)

        self.assertEqual(errors["feed"], "TimeoutError")

    def test_successful_loaders_leave_no_entry_behind(self):
        errors = {}
        services.settle_named_loaders({"feed": lambda: [1]}, errors=errors)

        self.assertEqual(errors, {})


class TemplateRenderingTests(SimpleTestCase):
    """The workspace page is assembled from one partial per tab.

    A stray template tag in any partial is invisible in review but ships a
    developer note to users as page text, so assert on the rendered output.
    """

    def rendered_page(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_no_raw_template_syntax_reaches_the_page(self):
        # Django only parses ``{# ... #}`` on a single line; spread over two it
        # is emitted verbatim. That shipped a developer note to production as
        # visible text, so fail the build rather than trust review to catch it.
        page = self.rendered_page()

        for token in ("{#", "#}", "{%", "%}", "{{", "}}"):
            self.assertNotIn(token, page, f"unrendered template syntax {token!r} reached the page")

    def test_no_developer_commentary_reaches_the_page(self):
        page = self.rendered_page().lower()

        for phrase in ("shown when nse is unreachable", "driven by public/app.js", "included by core/base.html"):
            self.assertNotIn(phrase, page, f"developer comment {phrase!r} reached the page")

    def test_every_tab_partial_is_included(self):
        page = self.rendered_page()

        for view_id in (
            "analysisView",
            "agentView",
            "ipoView",
            "etfView",
            "fundView",
            "monitorView",
            "recommendationsView",
        ):
            self.assertIn(f'id="{view_id}"', page)
