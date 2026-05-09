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
