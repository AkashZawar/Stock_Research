from unittest.mock import patch

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

    @patch("market.views.services.resolve_symbol_input")
    def test_analyze_records_failed_search(self, resolve_symbol):
        resolve_symbol.return_value = ""

        response = self.client.get(
            "/api/analyze",
            {"symbol": "??"},
            REMOTE_ADDR="198.51.100.7",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        )

        self.assertEqual(response.status_code, 400)
        log = StockSearchLog.objects.get()
        self.assertEqual(log.raw_input, "??")
        self.assertEqual(log.ip_address, "198.51.100.7")
        self.assertEqual(log.device_label, "Windows desktop")
        self.assertFalse(log.success)
        self.assertEqual(log.status_code, 400)

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
