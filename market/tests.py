from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.test import TestCase

from . import services
from .models import StockSearchLog


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
            "gainers": {"allSec": {"data": [{"symbol": "TEST", "ltp": 100, "net_price": 5, "perChange": 5, "trade_quantity": 1000}]}},
            "losers": {"allSec": {"data": [{"symbol": "FAIL", "ltp": 50, "net_price": -2, "perChange": -4, "trade_quantity": 900}]}},
            "mostActive": {"data": [{"symbol": "ACTIVE", "lastPrice": 99, "pChange": 1.2, "totalTradedVolume": 5000}]},
            "weekHighs": {"dataLtpGreater20": [{"symbol": "HIGH", "new52WHL": 120, "ltp": 119, "pChange": 2.1}]},
            "priceBands": {"AllSec": {"count": [{"key": "TOTAL", "value": 3}], "data": [{"symbol": "BAND", "ltp": 10, "priceBand": 5}]}},
        }

        snapshot = services.build_nse_market_snapshot_from_payloads(payloads)

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["breadth"]["advanceDeclineRatio"], 2.0)
        self.assertEqual(snapshot["indices"][0]["name"], "NIFTY 50")
        self.assertEqual(snapshot["sectorIndices"][0]["name"], "NIFTY IT")
        self.assertEqual(snapshot["topGainers"][0]["symbol"], "TEST")
        self.assertEqual(snapshot["priceBands"]["count"][0]["label"], "Total")

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


class AssetAnalysisTests(SimpleTestCase):
    def test_local_asset_search_prefers_matching_etfs(self):
        results = services.local_asset_search_symbols("nifty", "etf")
        symbols = [item["symbol"] for item in results]

        self.assertIn("NIFTYBEES.NS", symbols)
        self.assertTrue(all(item["type"] == "ETF" for item in results if item["symbol"].endswith(".NS")))

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


class StockSearchLogTests(TestCase):
    @patch("market.views.services.resolve_symbol_input")
    @patch("market.views.services.analyze_symbol")
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

    @patch("market.views.services.instrument_suggestions")
    @patch("market.views.services.resolve_symbol_input")
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

    @patch("market.views.services.instrument_suggestions")
    @patch("market.views.services.analyze_symbol")
    @patch("market.views.services.resolve_symbol_input")
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
