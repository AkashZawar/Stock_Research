"""Tests for the IPO Radar tab.

Covers the parsing layer (which absorbs several inconsistent upstream formats),
cross-source company-name matching, GMP consensus and its agreement grading,
the recommendation flag, section assembly, and the ``/api/ipo`` endpoint.

Run with ``python manage.py test ipo``.
"""
import time
from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from . import services


class ParsingTests(SimpleTestCase):
    def test_to_number_handles_the_formats_upstream_actually_sends(self):
        self.assertEqual(services.to_number("Rs.546"), 546.0)
        self.assertEqual(services.to_number("+\u20b954 (+66%)"), 54.0)
        self.assertEqual(services.to_number("   788"), 788.0)
        self.assertEqual(services.to_number("1.555635E7"), 15556350.0)
        self.assertEqual(services.to_number("1,23,456"), 123456.0)
        self.assertEqual(services.to_number(42), 42.0)

    def test_to_number_keeps_a_negative_premium_negative(self):
        # A grey market premium can be a discount, and losing the sign would
        # turn one into a positive premium.
        self.assertEqual(services.to_number("-\u20b95"), -5.0)
        self.assertEqual(services.to_number("-12"), -12.0)

    def test_to_number_reads_a_bare_leading_decimal(self):
        # NSE's OFS feed right-aligns subscription without a leading zero, so
        # "       .08" has to parse as 0.08 rather than as no value at all.
        self.assertEqual(services.to_number("       .08"), 0.08)
        self.assertEqual(services.to_number(".5"), 0.5)
        self.assertEqual(services.to_number("-.25"), -0.25)

    def test_to_number_treats_placeholder_dashes_as_missing(self):
        for value in ["-", "--", "", None, "n/a", "\u2014"]:
            self.assertIsNone(services.to_number(value), value)

    def test_parse_price_band_reads_both_ends(self):
        self.assertEqual(services.parse_price_band("Rs.546 to Rs.575"), (546.0, 575.0))
        self.assertEqual(services.parse_price_band("\u20b978-82"), (78.0, 82.0))
        self.assertEqual(services.parse_price_band("168&ndash;177"), (168.0, 177.0))

    def test_parse_price_band_returns_a_single_value_for_a_fixed_price_issue(self):
        self.assertEqual(services.parse_price_band("59-59"), (59.0, 59.0))
        self.assertEqual(services.parse_price_band("Rs.100"), (100.0, 100.0))

    def test_parse_price_band_survives_a_band_with_no_numbers(self):
        self.assertEqual(services.parse_price_band("-"), (None, None))

    def test_parse_date_accepts_every_spelling_the_feeds_mix(self):
        expected = date(2026, 8, 31)
        for value in ["31-Aug-2026", "31-AUG-2026", "Aug 31, 2026", "2026-08-31"]:
            self.assertEqual(services.parse_date(value), expected, value)

    def test_parse_date_returns_none_for_an_unlisted_issue(self):
        self.assertIsNone(services.parse_date("-"))
        self.assertIsNone(services.parse_date(""))

    def test_clean_text_strips_markup_so_scraped_html_cannot_reach_the_payload(self):
        self.assertEqual(services.clean_text("<b>Acme</b> Ltd"), "Acme Ltd")
        self.assertEqual(services.clean_text("<script>alert(1)</script>Acme"), "alert(1) Acme")

    def test_clean_text_caps_length(self):
        self.assertEqual(len(services.clean_text("x" * 500)), services.MAX_TEXT_LENGTH)

    def test_parse_html_tables_extracts_rows_and_cells(self):
        html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        self.assertEqual(services.parse_html_tables(html), [[["A", "B"], ["1", "2"]]])


class NameMatchingTests(SimpleTestCase):
    def test_the_same_company_matches_across_all_three_spellings(self):
        nse = "ESDS Software Solution Limited"
        self.assertTrue(services.names_match(nse, "ESDS Software Solution IPO Mainboard Open"))
        self.assertTrue(services.names_match(nse, "ESDS Software Solution Ltd (MAINBOARD)"))
        self.assertTrue(services.names_match(nse, "ESDS Software"))

    def test_different_companies_sharing_a_first_word_do_not_match(self):
        self.assertFalse(services.names_match("Priority Jewels Limited", "Priority Technologies Ltd"))

    def test_matching_ignores_board_and_suffix_noise(self):
        self.assertTrue(services.names_match("Ashutosh Fibre Limited", "Ashutosh Fibre IPO NSE SME Open"))


class GmpConsensusTests(SimpleTestCase):
    def build_quotes(self, values):
        return {
            f"src{index}": [{"company": "ESDS Software Solution", "gmp": value, "bandHigh": 429.0, "expectedListing": None}]
            for index, value in enumerate(values)
        }

    def test_consensus_averages_every_matching_source(self):
        consensus = services.gmp_consensus("ESDS Software Solution Limited", self.build_quotes([300.0, 330.0, 360.0]), 429.0)
        self.assertEqual(consensus["value"], 330.0)
        self.assertEqual(consensus["sourceCount"], 3)
        self.assertEqual(consensus["low"], 300.0)
        self.assertEqual(consensus["high"], 360.0)

    def test_expected_listing_price_is_cap_price_plus_average_premium(self):
        consensus = services.gmp_consensus("ESDS Software Solution Limited", self.build_quotes([300.0, 330.0, 360.0]), 429.0)
        self.assertEqual(consensus["expectedListingPrice"], 759.0)
        self.assertAlmostEqual(consensus["percent"], 76.92, places=1)

    def test_tightly_clustered_sources_grade_as_high_agreement(self):
        consensus = services.gmp_consensus("ESDS Software Solution Limited", self.build_quotes([100.0, 105.0, 110.0]), 429.0)
        self.assertEqual(consensus["agreement"], "high")

    def test_widely_scattered_sources_grade_as_low_agreement(self):
        consensus = services.gmp_consensus("ESDS Software Solution Limited", self.build_quotes([10.0, 60.0, 110.0]), 429.0)
        self.assertEqual(consensus["agreement"], "low")

    def test_a_lone_source_is_labelled_rather_than_presented_as_a_consensus(self):
        consensus = services.gmp_consensus("ESDS Software Solution Limited", self.build_quotes([120.0]), 429.0)
        self.assertEqual(consensus["agreement"], "single source")
        self.assertEqual(consensus["sourceCount"], 1)

    def test_no_matching_source_yields_no_consensus(self):
        self.assertIsNone(services.gmp_consensus("Unlisted Co", self.build_quotes([100.0]), 200.0))

    def test_band_high_is_borrowed_from_aggregators_when_nse_omits_it(self):
        quotes = {"src": [{"company": "Ashutosh Fibre", "gmp": 37.0, "bandHigh": 92.0, "expectedListing": None}]}
        self.assertEqual(services.gmp_band_high("Ashutosh Fibre Limited", quotes), 92.0)


class RecommendationFlagTests(SimpleTestCase):
    def test_strong_premium_and_heavy_subscription_flag_green(self):
        result = services.upcoming_flag(
            {"percent": 70.0, "sourceCount": 3, "agreement": "high"},
            {"total": 11.0, "qib": 5.0},
            True,
        )
        self.assertEqual(result["flag"], "green")

    def test_thin_premium_and_undersubscribed_book_flag_red(self):
        result = services.upcoming_flag(
            {"percent": 4.0, "sourceCount": 3, "agreement": "moderate"},
            {"total": 0.02, "qib": 0.0},
            True,
        )
        self.assertEqual(result["flag"], "red")

    def test_a_forthcoming_issue_is_scored_on_premium_alone_not_penalised_for_absent_bids(self):
        # Subscription is unknowable before bidding opens. Scoring it as zero
        # would flag every forthcoming issue red regardless of its premium.
        result = services.upcoming_flag({"percent": 40.0, "sourceCount": 3, "agreement": "high"}, None, False)
        self.assertEqual(result["flag"], "green")
        self.assertIn("bidding not open yet", result["reason"])

    def test_no_data_at_all_yields_grey_rather_than_a_verdict(self):
        result = services.upcoming_flag(None, None, False)
        self.assertEqual(result["flag"], "grey")
        self.assertIsNone(result["score"])

    def test_disagreeing_sources_reduce_the_score_and_are_called_out(self):
        agreed = services.upcoming_flag({"percent": 30.0, "sourceCount": 3, "agreement": "high"}, None, False)
        scattered = services.upcoming_flag({"percent": 30.0, "sourceCount": 3, "agreement": "low"}, None, False)
        self.assertLess(scattered["score"], agreed["score"])
        self.assertIn("sources disagree widely", scattered["reason"])

    def test_a_listed_issue_trading_below_its_issue_price_flags_red(self):
        self.assertEqual(services.listed_flag(15.6, -18.6, -5.9)["flag"], "red")

    def test_a_listed_issue_holding_its_gain_flags_green(self):
        self.assertEqual(services.listed_flag(21.9, -1.7, 19.8)["flag"], "green")

    def test_a_listed_issue_with_no_price_data_yields_grey(self):
        self.assertEqual(services.listed_flag(None, None, None)["flag"], "grey")


class RecentlyListedTests(SimpleTestCase):
    def past_issue(self, **overrides):
        row = {
            "company": "Tempsens Instruments (India) Limited",
            "symbol": "TEMPSENS",
            "securityType": "EQ",
            "issuePrice": "   300",
            "priceRange": "Rs.285 to Rs.300",
            "listingDate": "28-AUG-2026",
        }
        row.update(overrides)
        return row

    def test_listing_and_current_prices_drive_the_computed_percentages(self):
        with patch.object(
            services,
            "listing_and_current_price",
            return_value={"listingPrice": 634.0, "currentPrice": 568.9, "analysisSymbol": "TEMPSENS.NS"},
        ):
            rows = services.build_recently_listed([self.past_issue()], date(2026, 8, 31))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["issuePrice"], 300.0)
        self.assertEqual(rows[0]["listingPrice"], 634.0)
        self.assertAlmostEqual(rows[0]["listingGainPercent"], 111.33, places=1)
        self.assertAlmostEqual(rows[0]["vsIssuePercent"], 89.63, places=1)

    def test_issues_outside_the_seven_day_window_are_excluded(self):
        with patch.object(services, "listing_and_current_price", return_value={}):
            rows = services.build_recently_listed(
                [self.past_issue(listingDate="01-JAN-2026")], date(2026, 8, 31)
            )
        self.assertEqual(rows, [])

    def test_issues_that_have_not_listed_yet_are_excluded(self):
        with patch.object(services, "listing_and_current_price", return_value={}):
            rows = services.build_recently_listed([self.past_issue(listingDate="-")], date(2026, 8, 31))
        self.assertEqual(rows, [])

    def test_bonds_and_ncds_are_excluded_from_the_equity_listing_table(self):
        # NSE's past-issues feed mixes debt in with equity. Those rows carry a
        # 1000-rupee face value and no equity chart, so they would otherwise
        # render as a listing with a price and no outcome.
        debt = self.past_issue(symbol="PFC2026", securityType="N2", issuePrice="1000")
        with patch.object(services, "listing_and_current_price", return_value={}):
            rows = services.build_recently_listed([debt], date(2026, 8, 31))
        self.assertEqual(rows, [])

    def test_sme_issues_are_kept_and_tagged(self):
        sme = self.past_issue(symbol="FASCINATE", securityType="SME")
        with patch.object(services, "listing_and_current_price", return_value={}):
            rows = services.build_recently_listed([sme], date(2026, 8, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["board"], "SME")

    def test_rows_are_ordered_newest_listing_first(self):
        older = self.past_issue(symbol="OLDER", listingDate="25-AUG-2026")
        newer = self.past_issue(symbol="NEWER", listingDate="30-AUG-2026")
        with patch.object(services, "listing_and_current_price", return_value={}):
            rows = services.build_recently_listed([older, newer], date(2026, 8, 31))
        self.assertEqual([row["symbol"] for row in rows], ["NEWER", "OLDER"])


class PipelineTests(SimpleTestCase):
    def upcoming(self, **overrides):
        row = {
            "companyName": "ESDS Software Solution Limited",
            "symbol": "ESDS",
            "series": "EQ",
            "status": "Active",
            "issuePrice": "Rs.408 to Rs.429",
            "issueSize": "12352942",
            "issueStartDate": "28-Aug-2026",
            "issueEndDate": "01-Sep-2026",
        }
        row.update(overrides)
        return row

    def test_an_open_issue_is_merged_with_its_live_subscription(self):
        current = [dict(self.upcoming(), category="Total", noOfTime="6.13")]
        with patch.object(services, "fetch_bid_details", return_value={"qib": 0.03, "nii": 18.17, "retail": 4.45, "total": 6.13}):
            rows = services.build_pipeline([self.upcoming()], current, {}, date(2026, 8, 31))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Open")
        self.assertEqual(rows[0]["subscription"]["total"], 6.13)
        self.assertEqual(rows[0]["subscription"]["qib"], 0.03)
        self.assertEqual(rows[0]["priceBandHigh"], 429.0)

    def test_an_issue_opening_inside_the_window_is_kept_as_upcoming(self):
        row = self.upcoming(symbol="DEEPA", status="Forthcoming", issueStartDate="01-Sep-2026", issueEndDate="03-Sep-2026")
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([row], [], {}, date(2026, 8, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Upcoming")
        self.assertIsNone(rows[0]["subscription"])

    def test_an_issue_opening_beyond_the_window_is_dropped(self):
        row = self.upcoming(status="Forthcoming", issueStartDate="30-Sep-2026", issueEndDate="03-Oct-2026")
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([row], [], {}, date(2026, 8, 31))
        self.assertEqual(rows, [])

    def test_a_closed_issue_is_dropped_from_the_pipeline(self):
        row = self.upcoming(status="Closed", issueStartDate="18-Aug-2026", issueEndDate="20-Aug-2026")
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([row], [], {}, date(2026, 8, 31))
        self.assertEqual(rows, [])

    def test_sme_issues_present_only_in_the_current_feed_still_appear(self):
        # NSE's upcoming feed omits SME entirely; the current-issue feed is the
        # only place an SME issue shows up, so the merge has to add it.
        sme = {
            "companyName": "Ashutosh Fibre Limited",
            "symbol": "ASHUTOSH",
            "series": "SME",
            "status": "Active",
            "issueStartDate": "31-Aug-2026",
            "issueEndDate": "02-Sep-2026",
            "noOfTime": "0.13",
        }
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([], [sme], {}, date(2026, 8, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["board"], "SME")
        self.assertEqual(rows[0]["subscription"]["total"], 0.13)

    def test_a_missing_sme_band_is_recovered_from_the_gmp_sources(self):
        sme = {
            "companyName": "Ashutosh Fibre Limited",
            "symbol": "ASHUTOSH",
            "series": "SME",
            "status": "Active",
            "issueStartDate": "31-Aug-2026",
            "issueEndDate": "02-Sep-2026",
        }
        quotes = {"src": [{"company": "Ashutosh Fibre", "gmp": 37.0, "bandHigh": 92.0, "expectedListing": None}]}
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([], [sme], quotes, date(2026, 8, 31))
        self.assertEqual(rows[0]["priceBandHigh"], 92.0)
        self.assertEqual(rows[0]["gmp"]["expectedListingPrice"], 129.0)

    def test_open_issues_sort_ahead_of_upcoming_ones(self):
        upcoming = self.upcoming(symbol="DEEPA", status="Forthcoming", issueStartDate="01-Sep-2026", issueEndDate="03-Sep-2026")
        with patch.object(services, "fetch_bid_details", return_value={}):
            rows = services.build_pipeline([upcoming, self.upcoming()], [], {}, date(2026, 8, 31))
        self.assertEqual([row["status"] for row in rows], ["Open", "Upcoming"])


class OfsTests(SimpleTestCase):
    # Shape taken from a live /api/live-ofs-active-issues response: the same
    # company arrives as two groups, one keyed "data" and one keyed "rows".
    ACTIVE = [
        {
            "symbol": "KRT",
            "company": "Knowledge Realty Trust",
            "data": [
                {
                    "series": "IS",
                    "offerDate": "01-Sep-2026",
                    "ltp": "111.3",
                    "status": "Active",
                    "floorPrice": "108",
                    "cutOffPrice": "-#",
                    "issueSize": "-",
                    "noOfTimes": "-",
                },
                {
                    "series": "RS",
                    "offerDate": "01-Sep-2026",
                    "ltp": "111.3",
                    "status": "Active",
                    "floorPrice": "108",
                    "issueSize": None,
                    "noOfTimes": "-",
                },
            ],
        },
        {
            "symbol": "KRT",
            "company": "Knowledge Realty Trust",
            "rows": [
                {
                    "series": "IS",
                    "offerDate": "31-Aug-2026",
                    "ltp": "111.3",
                    "status": "Active",
                    "floorPrice": "108",
                    "issueSize": "666000000",
                    "noOfTimes": "       .08",
                }
            ],
        },
    ]

    def test_active_ofs_groups_collapse_into_one_row_per_company(self):
        rows = services.build_active_ofs(self.ACTIVE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["symbol"], "KRT")
        self.assertEqual(row["status"], "Active")
        self.assertEqual(row["floorPrice"], 108.0)
        self.assertEqual(row["currentPrice"], 111.3)
        # The window spans both sessions, not just the one that carried bids.
        self.assertEqual(row["openDate"], "2026-08-31")
        self.assertEqual(row["closeDate"], "2026-09-01")
        self.assertEqual(row["subscription"], {"nonRetail": 0.08})
        self.assertEqual(row["discountPercent"], 3.06)

    def test_active_ofs_reads_rows_key_as_well_as_data_key(self):
        # Dropping the "rows" group would silently lose the only subscription
        # number in the response.
        rows = services.build_active_ofs([self.ACTIVE[0]])
        self.assertEqual(rows[0]["subscription"], {})
        rows = services.build_active_ofs(self.ACTIVE)
        self.assertEqual(rows[0]["subscription"], {"nonRetail": 0.08})

    def test_recent_ofs_merges_both_sessions_of_one_offer(self):
        past = [
            {
                "companyName": "Hindustan Copper Limited",
                "symbol": "HINDCOPPERCUMU",
                "offerDate": "26-Aug-2026",
                "floorPrice": "514",
                "allocatePrice": "-",
                "noOfshareOffered": "52219296",
                "noOfTimes": "       .00",
            },
            {
                "companyName": "Hindustan Copper Limited",
                "symbol": "HINDCOPPERCUMU",
                "offerDate": "25-Aug-2026",
                "floorPrice": "514",
                "allocatePrice": "520",
                "noOfshareOffered": "52219296",
                "noOfTimes": "      7.88",
            },
        ]
        rows = services.build_recent_ofs(past, date(2026, 8, 31), set())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["openDate"], "2026-08-25")
        self.assertEqual(row["closeDate"], "2026-08-26")
        self.assertEqual(row["cutOffPrice"], 520.0)
        self.assertEqual(row["subscription"]["total"], 7.88)
        # The suffixed archive symbol is not tradable, so it must not reach the
        # analysis button.
        self.assertEqual(row["symbol"], "HINDCOPPER")
        self.assertEqual(row["analysisSymbol"], "HINDCOPPER.NS")

    def test_recent_ofs_drops_offers_outside_the_window(self):
        past = [
            {
                "companyName": "Old Co",
                "symbol": "OLD",
                "offerDate": "01-Jan-2026",
                "floorPrice": "100",
            }
        ]
        self.assertEqual(services.build_recent_ofs(past, date(2026, 8, 31), set()), [])

    def test_recent_ofs_skips_companies_already_shown_as_live(self):
        past = [
            {
                "companyName": "Knowledge Realty Trust",
                "symbol": "KRT",
                "offerDate": "30-Aug-2026",
                "floorPrice": "108",
            }
        ]
        self.assertEqual(services.build_recent_ofs(past, date(2026, 8, 31), {"KRT"}), [])

    def test_forthcoming_ofs_accepts_both_date_spellings(self):
        issues = [
            {"company": "A Ltd", "symbol": "AAA", "ofsStartDate": "02-Sep-2026", "ofsEndDate": "03-Sep-2026", "floorPrice": "50"},
            {"company": "B Ltd", "symbol": "BBB", "startDate": "03-Sep-2026", "endDate": "04-Sep-2026", "floorPrice": "60"},
        ]
        rows = services.build_forthcoming_ofs(issues, date(2026, 8, 31))
        self.assertEqual([row["openDate"] for row in rows], ["2026-09-02", "2026-09-03"])
        self.assertTrue(all(row["status"] == "Upcoming" for row in rows))

    def test_forthcoming_ofs_ignores_offers_beyond_the_next_week(self):
        issues = [{"company": "Far Ltd", "symbol": "FAR", "ofsStartDate": "30-Sep-2026"}]
        self.assertEqual(services.build_forthcoming_ofs(issues, date(2026, 8, 31)), [])

    def test_ofs_flag_rewards_a_wide_discount(self):
        wide = services.ofs_flag(12.0, {"nonRetail": 2.0})
        thin = services.ofs_flag(-1.0, {"nonRetail": 0.1})
        self.assertEqual(wide["flag"], "green")
        self.assertEqual(thin["flag"], "red")

    def test_ofs_flag_is_grey_without_any_signal(self):
        self.assertEqual(services.ofs_flag(None, {})["flag"], "grey")

    def test_ofs_flag_scores_on_discount_alone_before_bidding_opens(self):
        flag = services.ofs_flag(11.0, {})
        self.assertEqual(flag["flag"], "green")
        self.assertEqual(flag["score"], 100)


class GmpSourceResilienceTests(SimpleTestCase):
    """A dead grey-market tracker must not slow down or break the dashboard."""

    def setUp(self):
        services.clear_ipo_cache()
        services._gmp_cooldowns.clear()
        services._gmp_failures.clear()
        self.addCleanup(services.clear_ipo_cache)
        self.addCleanup(services._gmp_cooldowns.clear)
        self.addCleanup(services._gmp_failures.clear)

    def test_cooldown_grows_with_consecutive_failures_then_caps(self):
        # A flapping source must be retried soon; a dead one must not be retried
        # every minute forever.
        self.assertEqual(services.gmp_cooldown_for(1), 60)
        self.assertEqual(services.gmp_cooldown_for(2), 120)
        self.assertEqual(services.gmp_cooldown_for(3), 240)
        self.assertEqual(services.gmp_cooldown_for(99), services.GMP_SOURCE_COOLDOWN_MAX_SECONDS)

    def test_first_failure_only_costs_a_short_cooldown(self):
        def boom():
            raise RuntimeError("522")

        with patch.dict(services.GMP_SCRAPERS, {"IPO Watch": boom}):
            with self.assertRaises(RuntimeError):
                services.scrape_gmp_source("IPO Watch")
        remaining = services.gmp_source_cooldowns()["IPO Watch"] - time.time()
        self.assertLessEqual(remaining, services.GMP_SOURCE_COOLDOWN_SECONDS + 1)

    def test_a_recovery_resets_the_backoff(self):
        def boom():
            raise RuntimeError("522")

        with patch.dict(services.GMP_SCRAPERS, {"IPO Watch": boom}):
            with self.assertRaises(RuntimeError):
                services.scrape_gmp_source("IPO Watch")
        self.assertEqual(services._gmp_failures.get("IPO Watch"), 1)

        services._gmp_cooldowns.clear()
        with patch.dict(services.GMP_SCRAPERS, {"IPO Watch": lambda: [{"company": "A", "gmp": 1.0}]}):
            services.scrape_gmp_source("IPO Watch")
        self.assertNotIn("IPO Watch", services._gmp_failures)

    def test_a_failing_source_is_not_retried_until_its_cooldown_expires(self):
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("HTTP Error 522")

        with patch.dict(services.GMP_SCRAPERS, {"IPO Ji": boom}):
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    services.scrape_gmp_source("IPO Ji")

        # Three requests, one actual network attempt. Without this the tab pays
        # the full timeout on every rebuild while the site stays down.
        self.assertEqual(len(calls), 1)
        self.assertIn("IPO Ji", services.gmp_source_cooldowns())

    def test_cooldown_lapses_so_a_recovered_source_comes_back(self):
        services._gmp_cooldowns["IPO Ji"] = time.time() - 1
        with patch.dict(services.GMP_SCRAPERS, {"IPO Ji": lambda: [{"company": "A", "gmp": 1.0}]}):
            self.assertEqual(services.scrape_gmp_source("IPO Ji"), [{"company": "A", "gmp": 1.0}])
        self.assertNotIn("IPO Ji", services.gmp_source_cooldowns())

    def test_a_successful_scrape_is_reused_without_refetching(self):
        calls = []

        def once():
            calls.append(1)
            return [{"company": "A", "gmp": 1.0}]

        with patch.dict(services.GMP_SCRAPERS, {"IPO Ji": once}):
            services.scrape_gmp_source("IPO Ji")
            services.scrape_gmp_source("IPO Ji")
        self.assertEqual(len(calls), 1)

    def test_one_dead_source_still_yields_a_consensus_from_the_others(self):
        def boom():
            raise RuntimeError("down")

        with patch.dict(
            services.GMP_SCRAPERS,
            {
                "IPO Ji": lambda: [{"company": "Acme Limited", "gmp": 10.0, "bandHigh": 100.0}],
                "IPO Watch": boom,
                "IPO Premium": lambda: [{"company": "Acme Limited", "gmp": 20.0, "bandHigh": 100.0}],
            },
        ):
            quotes, failed = services.collect_gmp_quotes()

        self.assertEqual(failed, ["IPO Watch"])
        consensus = services.gmp_consensus("Acme Limited", quotes, 100.0)
        self.assertEqual(consensus["value"], 15.0)
        self.assertEqual(consensus["sourceCount"], 2)

    def test_dashboard_survives_every_gmp_source_being_down(self):
        def boom():
            raise RuntimeError("down")

        with patch.dict(
            services.GMP_SCRAPERS,
            {name: boom for name in services.GMP_SCRAPERS},
        ):
            quotes, failed = services.collect_gmp_quotes()
        self.assertEqual(quotes, {})
        self.assertEqual(sorted(failed), sorted(services.GMP_SCRAPERS))
        # No GMP means no premium to score on, not a crash.
        self.assertEqual(services.gmp_consensus("Acme", quotes, 100.0), None)


class DashboardAssemblyTests(SimpleTestCase):
    def test_dashboard_reports_every_gmp_source_down_without_failing(self):
        with patch.object(services, "fetch_upcoming_issues", lambda: []), \
             patch.object(services, "fetch_current_issues", lambda: []), \
             patch.object(services, "fetch_past_issues", lambda: [{"securityType": "EQ"}]), \
             patch.object(services, "collect_gmp_quotes", lambda: ({}, ["IPO Ji", "IPO Watch", "IPO Premium"])), \
             patch.object(services, "fetch_ofs_active", lambda: []), \
             patch.object(services, "fetch_ofs_forthcoming", lambda: []), \
             patch.object(services, "fetch_ofs_past", lambda: []):
            payload = services.build_ipo_dashboard()

        self.assertEqual(payload["pipeline"], [])
        self.assertTrue(any("not responding" in note for note in payload["notes"]))
        self.assertTrue(all(source["ok"] is False for source in payload["gmpSources"]))

    def test_dashboard_fails_loudly_when_every_nse_feed_is_empty(self):
        # An empty NSE response is indistinguishable from a broken one here, and
        # rendering an empty dashboard would read as "no IPOs" rather than "no data".
        with patch.object(services, "fetch_upcoming_issues", lambda: []), \
             patch.object(services, "fetch_current_issues", lambda: []), \
             patch.object(services, "fetch_past_issues", lambda: []), \
             patch.object(services, "collect_gmp_quotes", lambda: ({}, [])), \
             patch.object(services, "fetch_ofs_active", lambda: []), \
             patch.object(services, "fetch_ofs_forthcoming", lambda: []), \
             patch.object(services, "fetch_ofs_past", lambda: []):
            with self.assertRaises(RuntimeError):
                services.build_ipo_dashboard()

    def test_a_crashed_gmp_loader_does_not_take_down_the_dashboard(self):
        # settle_named_loaders reports a crashed loader as {}, which must not be
        # unpacked as if it were the (quotes, failed) tuple.
        def boom():
            raise RuntimeError("thread died")

        with patch.object(services, "fetch_upcoming_issues", lambda: []), \
             patch.object(services, "fetch_current_issues", lambda: []), \
             patch.object(services, "fetch_past_issues", lambda: [{"securityType": "EQ"}]), \
             patch.object(services, "collect_gmp_quotes", boom), \
             patch.object(services, "fetch_ofs_active", lambda: []), \
             patch.object(services, "fetch_ofs_forthcoming", lambda: []), \
             patch.object(services, "fetch_ofs_past", lambda: []):
            payload = services.build_ipo_dashboard()

        self.assertTrue(all(source["ok"] is False for source in payload["gmpSources"]))

    def test_ofs_feeds_that_fail_leave_the_section_empty_not_broken(self):
        with patch.object(services, "fetch_upcoming_issues", lambda: []), \
             patch.object(services, "fetch_current_issues", lambda: []), \
             patch.object(services, "fetch_past_issues", lambda: [{"securityType": "EQ"}]), \
             patch.object(services, "collect_gmp_quotes", lambda: ({}, [])), \
             patch.object(services, "fetch_ofs_active", lambda: (_ for _ in ()).throw(RuntimeError("down"))), \
             patch.object(services, "fetch_ofs_forthcoming", lambda: []), \
             patch.object(services, "fetch_ofs_past", lambda: []):
            payload = services.build_ipo_dashboard()

        self.assertEqual(payload["ofs"]["rows"], [])
        self.assertTrue(payload["ofs"]["note"])


class IpoEndpointTests(SimpleTestCase):
    def test_endpoint_returns_the_dashboard_payload(self):
        payload = {
            "generatedAt": "2026-08-31T00:00:00Z",
            "asOfDate": "2026-08-31",
            "recentlyListed": [],
            "pipeline": [],
            "ofs": {"available": False, "rows": [], "note": "n"},
            "gmpSources": [],
            "counts": {"recentlyListed": 0, "open": 0, "upcoming": 0},
            "notes": [],
            "source": "test",
        }
        with patch.object(services, "get_ipo_dashboard", return_value=payload):
            response = self.client.get("/api/ipo")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["asOfDate"], "2026-08-31")

    def test_refresh_clears_the_cache_before_rebuilding(self):
        with patch.object(services, "clear_ipo_cache") as clear, patch.object(
            services, "get_ipo_dashboard", return_value={"ok": True}
        ):
            response = self.client.get("/api/ipo?refresh=1")

        clear.assert_called_once()
        self.assertEqual(response.status_code, 200)

    def test_upstream_failure_surfaces_as_a_500_with_a_message(self):
        with patch.object(services, "get_ipo_dashboard", side_effect=RuntimeError("NSE is down")):
            response = self.client.get("/api/ipo")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "NSE is down")


class GmpMetadataParsingTests(SimpleTestCase):
    """Status, board, dates and names recovered from the aggregators.

    These fields are what let the tab survive an NSE outage without presenting
    already-listed issues as investable, so each is pinned separately.
    """

    def test_status_words_collapse_to_the_three_states_shown(self):
        self.assertEqual(services.gmp_status("Open"), "Open")
        self.assertEqual(services.gmp_status("Live Now"), "Open")
        self.assertEqual(services.gmp_status("Upcoming"), "Upcoming")
        self.assertEqual(services.gmp_status("Coming Soon"), "Upcoming")
        self.assertEqual(services.gmp_status("Closed"), "Closed")
        self.assertEqual(services.gmp_status("Listed"), "Closed")

    def test_an_unrecognised_status_is_none_rather_than_a_guess(self):
        # None keeps "not published" distinct from "closed"; guessing "closed"
        # here would silently drop live issues from the fallback pipeline.
        self.assertIsNone(services.gmp_status(""))
        self.assertIsNone(services.gmp_status("-"))
        self.assertIsNone(services.gmp_status("qwerty"))

    def test_board_is_read_from_a_type_column_or_a_tag_on_the_name(self):
        self.assertEqual(services.gmp_board("SME"), "SME")
        self.assertEqual(services.gmp_board("", "Ashutosh Fibre Ltd. (NSE SME)"), "SME")
        self.assertEqual(services.gmp_board("Mainboard"), "Mainboard")
        self.assertEqual(services.gmp_board("", "Deepa Jewellers IPO MB"), "Mainboard")
        self.assertIsNone(services.gmp_board("", "Some Company Ltd"))

    def test_board_abbreviations_match_on_word_boundaries(self):
        # "MB" as a substring appears inside ordinary words; only the standalone
        # tag means mainboard.
        self.assertIsNone(services.gmp_board("Combustion Ltd"))
        self.assertIsNone(services.gmp_board("Smerch Industries"))

    def test_a_spelled_out_date_range_splits_into_two_dates(self):
        self.assertEqual(
            services.parse_gmp_dates("Aug 27, 2026 \u2013 Aug 31, 2026"),
            (date(2026, 8, 27), date(2026, 8, 31)),
        )

    def test_a_date_range_missing_its_year_is_declined_rather_than_guessed(self):
        # "1-3 September" needs both a year and a month inferred backwards; a
        # wrong window would filter a live issue out of the table.
        self.assertEqual(services.parse_gmp_dates("1-3 September"), (None, None))

    def test_company_names_lose_the_tags_aggregators_append(self):
        self.assertEqual(services.gmp_company_name("Lumino Industries IPO Mainboard Open"), "Lumino Industries")
        self.assertEqual(services.gmp_company_name("Kwick Forensic Solutions IPO BSE SME Open"), "Kwick Forensic Solutions")
        self.assertEqual(services.gmp_company_name("Ashutosh Fibre Ltd. (NSE SME)"), "Ashutosh Fibre Ltd")
        self.assertEqual(services.gmp_company_name("Phychem IPO SME \u20b951 - \u20b954 31 Aug-2 Sep"), "Phychem")

    def test_a_name_that_is_all_tags_or_starts_with_a_digit_is_left_alone(self):
        # Trimming these would leave nothing to match other sources against.
        self.assertEqual(services.gmp_company_name("3M India Limited"), "3M India Limited")
        self.assertEqual(services.gmp_company_name("IPO"), "IPO")

    def test_trimming_lets_two_spellings_of_one_issue_match(self):
        left = services.gmp_company_name("Phychem IPO SME \u20b951 - \u20b954 31 Aug-2 Sep")
        right = services.gmp_company_name("Phychem Technologies Ltd. (BSE SME)")
        self.assertTrue(services.names_match(left, right))


class GmpOnlyPipelineTests(SimpleTestCase):
    """The pipeline built when NSE is unreachable."""

    def quotes(self, **overrides):
        row = {
            "company": "Lumino Industries",
            "gmp": 50.0,
            "bandHigh": 82.0,
            "expectedListing": 132.0,
            "status": "Open",
            "board": "Mainboard",
            "openDate": date(2026, 8, 27),
            "closeDate": date(2026, 8, 31),
        }
        row.update(overrides)
        return {"IPO Ji": [row]}

    def test_a_row_carries_its_source_so_the_table_can_explain_blank_columns(self):
        rows = services.build_pipeline_from_gmp(self.quotes(), date(2026, 8, 29))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "gmp")
        self.assertIsNone(rows[0]["analysisSymbol"])
        self.assertIsNone(rows[0]["subscription"])

    def test_status_board_and_dates_survive_from_the_aggregator(self):
        rows = services.build_pipeline_from_gmp(self.quotes(), date(2026, 8, 29))
        self.assertEqual(rows[0]["status"], "Open")
        self.assertEqual(rows[0]["board"], "Mainboard")
        self.assertEqual(rows[0]["openDate"], "2026-08-27")
        self.assertEqual(rows[0]["closeDate"], "2026-08-31")

    def test_an_issue_marked_closed_is_dropped(self):
        # Aggregators keep listing an issue after it lists, carrying the stale
        # premium it had on listing day. Showing that as pipeline invites a bet
        # on something that can no longer be applied for.
        rows = services.build_pipeline_from_gmp(self.quotes(status="Closed"), date(2026, 8, 29))
        self.assertEqual(rows, [])

    def test_an_issue_whose_window_has_passed_is_dropped_without_a_status(self):
        rows = services.build_pipeline_from_gmp(self.quotes(status=None), date(2026, 9, 15))
        self.assertEqual(rows, [])

    def test_status_is_inferred_from_the_window_when_no_source_states_it(self):
        upcoming = services.build_pipeline_from_gmp(self.quotes(status=None), date(2026, 8, 20))
        self.assertEqual(upcoming[0]["status"], "Upcoming")
        live = services.build_pipeline_from_gmp(self.quotes(status=None), date(2026, 8, 29))
        self.assertEqual(live[0]["status"], "Open")

    def test_a_row_with_no_status_and_no_dates_is_kept(self):
        # Most sources publish neither; dropping them would empty the table.
        rows = services.build_pipeline_from_gmp(
            self.quotes(status=None, openDate=None, closeDate=None), date(2026, 8, 29)
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["status"])

    def test_one_issue_listed_by_two_sources_produces_one_row(self):
        quotes = {
            "IPO Ji": [dict(self.quotes()["IPO Ji"][0])],
            "IPO Watch": [dict(self.quotes()["IPO Ji"][0], company="Lumino Industries Ltd", gmp=60.0)],
        }
        rows = services.build_pipeline_from_gmp(quotes, date(2026, 8, 29))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gmp"]["sourceCount"], 2)

    def test_disagreeing_sources_settle_on_the_majority_status(self):
        base = self.quotes()["IPO Ji"][0]
        quotes = {
            "IPO Ji": [dict(base, status="Open")],
            "IPO Watch": [dict(base, status="Open")],
            "IPO Premium": [dict(base, status="Closed")],
        }
        rows = services.build_pipeline_from_gmp(quotes, date(2026, 8, 29))
        self.assertEqual(rows[0]["status"], "Open")

    def test_open_issues_sort_ahead_of_upcoming_ones(self):
        base = self.quotes()["IPO Ji"][0]
        quotes = {
            "IPO Ji": [
                dict(base, company="Later Co", status="Upcoming"),
                dict(base, company="Live Co", status="Open"),
            ]
        }
        rows = services.build_pipeline_from_gmp(quotes, date(2026, 8, 29))
        self.assertEqual([row["company"] for row in rows], ["Live Co", "Later Co"])


class NseOutageTests(SimpleTestCase):
    """What the tab does when NSE answers nothing at all."""

    def gmp_rows(self):
        return (
            {
                "IPO Ji": [
                    {
                        "company": "Lumino Industries",
                        "gmp": 50.0,
                        "bandHigh": 82.0,
                        "expectedListing": 132.0,
                        "status": "Open",
                        "board": "Mainboard",
                        "openDate": date(2026, 8, 27),
                        "closeDate": date(2026, 8, 31),
                    }
                ]
            },
            ["IPO Watch"],
        )

    def build_with_nse_down(self):
        def blocked():
            raise RuntimeError("NSE India returned 403.")

        with patch.object(services, "fetch_upcoming_issues", blocked), \
             patch.object(services, "fetch_current_issues", blocked), \
             patch.object(services, "fetch_past_issues", blocked), \
             patch.object(services, "collect_gmp_quotes", self.gmp_rows), \
             patch.object(services, "fetch_ofs_active", blocked), \
             patch.object(services, "fetch_ofs_forthcoming", blocked), \
             patch.object(services, "fetch_ofs_past", blocked):
            return services.build_ipo_dashboard()

    def test_the_tab_still_returns_issues_from_the_grey_market_sources(self):
        payload = self.build_with_nse_down()
        self.assertFalse(payload["nseAvailable"])
        self.assertEqual(len(payload["pipeline"]), 1)
        self.assertEqual(payload["pipeline"][0]["company"], "Lumino Industries")

    def test_the_sections_only_nse_can_fill_are_empty_rather_than_stale(self):
        payload = self.build_with_nse_down()
        self.assertEqual(payload["recentlyListed"], [])
        self.assertFalse(payload["ofs"]["available"])
        self.assertEqual(payload["counts"]["recentlyListed"], 0)

    def test_what_nse_actually_said_reaches_the_payload(self):
        # settle_named_loaders used to swallow the exception, leaving a generic
        # message that made a production outage impossible to diagnose.
        payload = self.build_with_nse_down()
        self.assertTrue(any("403" in note for note in payload["notes"]))

    def test_the_source_line_stops_crediting_nse_when_nse_gave_nothing(self):
        payload = self.build_with_nse_down()
        self.assertNotIn("NSE India", payload["source"])

    def test_it_still_fails_loudly_when_no_source_of_any_kind_responds(self):
        def blocked():
            raise RuntimeError("NSE India returned 403.")

        with patch.object(services, "fetch_upcoming_issues", blocked), \
             patch.object(services, "fetch_current_issues", blocked), \
             patch.object(services, "fetch_past_issues", blocked), \
             patch.object(services, "collect_gmp_quotes", lambda: ({}, ["IPO Ji"])), \
             patch.object(services, "fetch_ofs_active", blocked), \
             patch.object(services, "fetch_ofs_forthcoming", blocked), \
             patch.object(services, "fetch_ofs_past", blocked):
            with self.assertRaises(RuntimeError) as caught:
                services.build_ipo_dashboard()

        self.assertIn("403", str(caught.exception))

    def test_an_empty_feed_reads_differently_from_a_refused_one(self):
        self.assertEqual(services.nse_failure_reason({}), "the feeds returned no issues")
        self.assertIn("403", services.nse_failure_reason({"upcoming": "NSE India returned 403."}))

    def test_identical_failures_across_feeds_are_reported_once(self):
        reason = services.nse_failure_reason(
            {"upcoming": "NSE India returned 403.", "current": "NSE India returned 403."}
        )
        self.assertEqual(reason, "NSE India returned 403.")
