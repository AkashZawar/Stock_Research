from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.test import TestCase

from . import services
from .models import StockSearchLog
from .models import WatchlistItem


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


class MarketMonitorEndpointTests(TestCase):
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


class WatchlistApiTests(TestCase):
    def test_watchlist_post_upserts_symbol_and_get_lists_item(self):
        response = self.client.post(
            "/api/watchlist",
            data={
                "symbol": "hal.ns",
                "stockName": "HAL",
                "buyPrice": 4200,
                "sellPrice": 4700,
                "checkPrice": 4300,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertEqual(response.json()["symbol"], "HAL.NS")

        response = self.client.post(
            "/api/watchlist",
            data={"symbol": "HAL.NS", "stockName": "Hindustan Aeronautics", "buyPrice": 4250},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        item = WatchlistItem.objects.get()
        self.assertEqual(item.stock_name, "Hindustan Aeronautics")
        self.assertEqual(float(item.buy_price), 4250.0)

        response = self.client.get("/api/watchlist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["symbol"], "HAL.NS")

    def test_watchlist_rejects_invalid_price(self):
        response = self.client.post(
            "/api/watchlist",
            data={"symbol": "HAL.NS", "buyPrice": -1},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("greater than zero", response.json()["error"])


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


class StockSearchLogTests(TestCase):
    @patch("core.services.resolve_symbol_input")
    @patch("core.services.analyze_symbol")
    def test_analyze_records_search_log_with_ip_and_device(self, analyze_symbol, resolve_symbol):
        resolve_symbol.return_value = "RELIANCE.NS"
        analyze_symbol.return_value = {"symbol": "RELIANCE.NS", "source": "test"}

        response = self.client.get(
            "/api/analyze",
            {"symbol": "Reliance"},
            HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.4",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Mobile/15E148",
        )

        self.assertEqual(response.status_code, 200)
        log = StockSearchLog.objects.get()
        self.assertEqual(log.raw_input, "Reliance")
        self.assertEqual(log.symbol, "RELIANCE.NS")
        self.assertEqual(log.ip_address, "203.0.113.9")
        self.assertEqual(log.device_type, "mobile")
        self.assertTrue(log.success)

    @patch("core.services.instrument_suggestions")
    @patch("core.services.resolve_symbol_input")
    def test_analyze_records_failed_search(self, resolve_symbol, instrument_suggestions):
        resolve_symbol.return_value = ""
        instrument_suggestions.return_value = {"stocks": [], "etfs": [], "mutualFunds": []}

        response = self.client.get(
            "/api/analyze",
            {"symbol": "??"},
            REMOTE_ADDR="198.51.100.7",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], services.INVALID_INSTRUMENT_MESSAGE)
        log = StockSearchLog.objects.get()
        self.assertEqual(log.raw_input, "??")
        self.assertEqual(log.ip_address, "198.51.100.7")
        self.assertEqual(log.device_label, "Windows desktop")
        self.assertFalse(log.success)
        self.assertEqual(log.status_code, 400)

    @patch("core.services.instrument_suggestions")
    @patch("core.services.analyze_symbol")
    @patch("core.services.resolve_symbol_input")
    def test_analyze_404_returns_invalid_name_suggestions(self, resolve_symbol, analyze_symbol, instrument_suggestions):
        resolve_symbol.return_value = "RELANCE.NS"
        analyze_symbol.side_effect = RuntimeError("Data provider returned 404.")
        instrument_suggestions.return_value = {
            "stocks": [{"symbol": "RELIANCE.NS", "name": "Reliance Industries", "kind": "stock"}],
            "etfs": [],
            "mutualFunds": [],
        }

        response = self.client.get("/api/analyze", {"symbol": "relance"})

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"], services.INVALID_INSTRUMENT_MESSAGE)
        self.assertEqual(payload["suggestions"]["stocks"][0]["symbol"], "RELIANCE.NS")

    def test_search_logs_endpoint_returns_recent_logs(self):
        StockSearchLog.objects.create(
            raw_input="TCS",
            symbol="TCS.NS",
            ip_address="192.0.2.10",
            device_type="desktop",
            device_label="Mac desktop",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        )

        response = self.client.get("/api/search-logs")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["symbol"], "TCS.NS")
        self.assertEqual(payload["results"][0]["deviceLabel"], "Mac desktop")
