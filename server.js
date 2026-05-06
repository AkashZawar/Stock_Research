const http = require("http");
const fs = require("fs/promises");
const path = require("path");

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || "127.0.0.1";
const PUBLIC_DIR = path.join(__dirname, "public");
const CACHE_TTL_MS = 5 * 60 * 1000;
const SEC_CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const SEC_USER_AGENT = process.env.SEC_USER_AGENT || "StockResearchDesk/0.1 contact@example.com";
const cache = new Map();

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
  ".ico": "image/x-icon"
};

const modules = [
  "assetProfile",
  "summaryDetail",
  "defaultKeyStatistics",
  "financialData",
  "price",
  "calendarEvents",
  "earningsTrend",
  "recommendationTrend",
  "upgradeDowngradeHistory"
].join(",");

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname === "/api/search") {
      await handleSearch(url, res);
      return;
    }

    if (url.pathname === "/api/analyze") {
      await handleAnalyze(url, res);
      return;
    }

    await serveStatic(url.pathname, res);
  } catch (error) {
    console.error(error);
    sendJson(res, 500, {
      error: "Something went wrong while handling the request.",
      detail: error.message
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`Stock Research Desk is running at http://${HOST}:${PORT}`);
});

async function handleSearch(url, res) {
  const query = String(url.searchParams.get("q") || "").trim();
  if (query.length < 2) {
    sendJson(res, 200, { results: [] });
    return;
  }

  sendJson(res, 200, { results: await searchSymbols(query) });
}

async function handleAnalyze(url, res) {
  const rawInput = String(url.searchParams.get("symbol") || "").trim();
  const symbol = await resolveSymbolInput(rawInput);
  if (!symbol) {
    sendJson(res, 400, { error: "Enter a valid ticker symbol or stock name." });
    return;
  }

  const data = await cached(`analysis:${symbol}`, async () => {
    const [chartResult, quoteResult, summaryResult, secResult, screenerResult] = await Promise.allSettled([
      getChart(symbol),
      getQuote(symbol),
      getSummary(symbol),
      getSecFundamentals(symbol),
      getScreenerFundamentals(symbol)
    ]);

    if (chartResult.status !== "fulfilled") {
      throw new Error(chartResult.reason.message || "Could not load chart data.");
    }

    const candles = chartResult.value.candles;
    if (candles.length < 80) {
      throw new Error("Not enough one-year daily data was returned for this symbol.");
    }

    const quote = quoteResult.status === "fulfilled" ? quoteResult.value : {};
    const summary = summaryResult.status === "fulfilled" ? summaryResult.value : {};
    const sec = secResult.status === "fulfilled" ? secResult.value : {};
    const screener = screenerResult.status === "fulfilled" ? screenerResult.value : {};
    return buildReport(symbol, chartResult.value.meta, candles, quote, summary, sec, screener);
  });

  sendJson(res, 200, data);
}

async function resolveSymbolInput(input) {
  const normalized = normalizeSymbol(input);
  if (normalized) {
    return normalized;
  }

  const results = await searchSymbols(input);
  const best = chooseSearchResult(results, input);
  return best ? best.symbol : "";
}

async function searchSymbols(query) {
  return cached(`search:${query.toLowerCase()}`, async () => {
    const endpoint = `https://query2.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(query)}&quotesCount=8&newsCount=0`;
    const payload = await fetchJson(endpoint);
    return sortSearchResults(
      (payload.quotes || [])
        .filter((item) => item.symbol && item.quoteType !== "CRYPTOCURRENCY")
        .map((item) => ({
          symbol: item.symbol,
          name: item.shortname || item.longname || item.symbol,
          exchange: item.exchDisp || item.exchange || "",
          type: item.quoteType || ""
        })),
      query
    ).slice(0, 8);
  });
}

function chooseSearchResult(results, query) {
  return sortSearchResults(results, query).find((item) => item.type === "EQUITY") || results[0] || null;
}

function sortSearchResults(results, query) {
  const lowerQuery = query.toLowerCase();
  const preferIndia = lowerQuery.includes("india") || lowerQuery.includes("nse") || lowerQuery.includes("bse");

  return [...results].sort((a, b) => {
    const scoreDiff = searchScore(b, lowerQuery, preferIndia) - searchScore(a, lowerQuery, preferIndia);
    return scoreDiff || a.symbol.localeCompare(b.symbol);
  });
}

function searchScore(item, lowerQuery, preferIndia) {
  let score = item.type === "EQUITY" ? 20 : 0;
  const symbol = item.symbol.toUpperCase();
  const name = item.name.toLowerCase();

  if (name.includes(lowerQuery) || symbol.toLowerCase().includes(lowerQuery.replace(/\s+/g, ""))) {
    score += 10;
  }
  if (preferIndia && symbol.endsWith(".NS")) {
    score += 30;
  }
  if (preferIndia && symbol.endsWith(".BO")) {
    score += 20;
  }
  return score;
}

async function serveStatic(requestPath, res) {
  const pathname = decodeURIComponent(requestPath === "/" ? "/index.html" : requestPath);
  const filePath = path.resolve(PUBLIC_DIR, `.${pathname}`);

  if (filePath !== PUBLIC_DIR && !filePath.startsWith(`${PUBLIC_DIR}${path.sep}`)) {
    sendText(res, 403, "Forbidden");
    return;
  }

  try {
    const content = await fs.readFile(filePath);
    const type = mimeTypes[path.extname(filePath)] || "application/octet-stream";
    res.writeHead(200, { "Content-Type": type });
    res.end(content);
  } catch (error) {
    if (error.code === "ENOENT") {
      sendText(res, 404, "Not found");
      return;
    }
    throw error;
  }
}

async function getChart(symbol) {
  const endpoint = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1y&interval=1d&includePrePost=false&events=div%2Csplits`;
  const payload = await fetchJson(endpoint);
  const result = payload.chart && payload.chart.result && payload.chart.result[0];
  const yahooError = payload.chart && payload.chart.error;

  if (!result) {
    throw new Error((yahooError && yahooError.description) || "Chart data was not available.");
  }

  const quote = result.indicators && result.indicators.quote && result.indicators.quote[0];
  const adjclose = result.indicators && result.indicators.adjclose && result.indicators.adjclose[0];
  const timestamps = result.timestamp || [];
  const candles = timestamps
    .map((timestamp, index) => ({
      date: new Date(timestamp * 1000).toISOString().slice(0, 10),
      open: cleanNumber(quote.open && quote.open[index]),
      high: cleanNumber(quote.high && quote.high[index]),
      low: cleanNumber(quote.low && quote.low[index]),
      close: cleanNumber(quote.close && quote.close[index]),
      adjClose: cleanNumber(adjclose && adjclose.adjclose && adjclose.adjclose[index]),
      volume: cleanNumber(quote.volume && quote.volume[index])
    }))
    .filter((item) => item.open && item.high && item.low && item.close);

  return { meta: result.meta || {}, candles };
}

async function getQuote(symbol) {
  const endpoint = `https://query1.finance.yahoo.com/v7/finance/quote?symbols=${encodeURIComponent(symbol)}`;
  const payload = await fetchJson(endpoint);
  return (payload.quoteResponse && payload.quoteResponse.result && payload.quoteResponse.result[0]) || {};
}

async function getSummary(symbol) {
  const endpoint = `https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(symbol)}?modules=${modules}`;
  const payload = await fetchJson(endpoint);
  const error = payload.quoteSummary && payload.quoteSummary.error;
  if (error) {
    throw new Error(error.description || "Fundamental data was not available.");
  }
  return (payload.quoteSummary && payload.quoteSummary.result && payload.quoteSummary.result[0]) || {};
}

async function getSecFundamentals(symbol) {
  if (!/^[A-Z]{1,5}$/.test(symbol)) {
    return {};
  }

  const tickerMap = await cached(
    "sec:company-tickers",
    async () => fetchSecJson("https://www.sec.gov/files/company_tickers.json"),
    SEC_CACHE_TTL_MS
  );
  const company = Object.values(tickerMap).find((item) => item.ticker && item.ticker.toUpperCase() === symbol);

  if (!company) {
    return {};
  }

  const cik = String(company.cik_str).padStart(10, "0");
  const facts = await cached(
    `sec:companyfacts:${cik}`,
    async () => fetchSecJson(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cik}.json`),
    SEC_CACHE_TTL_MS
  );

  return extractSecMetrics(facts, company.title);
}

async function getScreenerFundamentals(symbol) {
  if (!symbol.endsWith(".NS") && !symbol.endsWith(".BO")) {
    return {};
  }

  const screenerSymbol = symbol.replace(/\.(NS|BO)$/i, "");
  const html = await cached(
    `screener:${screenerSymbol}`,
    async () => fetchText(`https://www.screener.in/company/${encodeURIComponent(screenerSymbol)}/`),
    SEC_CACHE_TTL_MS
  );

  return extractScreenerMetrics(html);
}

function buildReport(symbol, meta, candles, quote, summary, sec, screener) {
  const closes = candles.map((item) => item.close);
  const volumes = candles.map((item) => item.volume || 0);
  const current = closes[closes.length - 1];
  const previous = closes[closes.length - 2];
  const currency = quote.currency || meta.currency || raw(summary.price && summary.price.currency) || "";
  const longName = quote.longName || quote.shortName || summary.price?.longName || summary.price?.shortName || sec.companyName || screener.companyName || symbol;

  const sma20 = sma(closes, 20);
  const sma50 = sma(closes, 50);
  const sma200 = sma(closes, 200);
  const ema12 = ema(closes, 12);
  const ema26 = ema(closes, 26);
  const rsi14 = rsi(closes, 14);
  const macdData = macd(closes);
  const atr14 = atr(candles, 14);
  const bands = bollinger(closes, 20, 2);
  const avgVolume20 = last(sma(volumes, 20));
  const volumeRatio = avgVolume20 ? (volumes[volumes.length - 1] || 0) / avgVolume20 : null;
  const yearHigh = Math.max(...candles.map((item) => item.high));
  const yearLow = Math.min(...candles.map((item) => item.low));
  const supportResistance = findLevels(candles, current, last(atr14));
  const fundamentals = extractFundamentals(summary, quote, meta, sec, screener, current);
  const events = extractEvents(summary, screener);
  const technical = scoreTechnical({
    current,
    yearHigh,
    yearLow,
    sma20: last(sma20),
    sma50: last(sma50),
    sma200: last(sma200),
    rsi14: last(rsi14),
    macdLine: last(macdData.line),
    macdSignal: last(macdData.signal),
    macdHist: last(macdData.histogram),
    volumeRatio,
    atrPercent: safeDivide(last(atr14), current) * 100
  });
  const fundamental = scoreFundamental(fundamentals);
  const eventRisk = scoreEventRisk(events);
  const researchLevels = buildResearchLevels({
    current,
    support: supportResistance.supports[0],
    resistance: supportResistance.resistances[0],
    atrValue: last(atr14),
    yearHigh,
    yearLow,
    technicalScore: technical.score
  });

  const overallScore = weightedAverage([
    [technical.score, 0.45],
    [fundamental.score, 0.4],
    [100 - eventRisk.score, 0.15]
  ]);

  const outlook = buildOutlook(overallScore, technical.score, fundamental.score, eventRisk);
  const series = candles.map((item, index) => ({
    date: item.date,
    close: round(item.close),
    volume: item.volume || 0,
    sma20: roundOrNull(sma20[index]),
    sma50: roundOrNull(sma50[index]),
    sma200: roundOrNull(sma200[index])
  }));

  return {
    symbol,
    longName,
    currency,
    source: buildSourceLabel(sec, screener),
    generatedAt: new Date().toISOString(),
    quote: {
      price: round(current),
      previousClose: round(previous),
      change: round(current - previous),
      changePercent: round(safeDivide(current - previous, previous) * 100),
      marketTime: quote.regularMarketTime ? new Date(quote.regularMarketTime * 1000).toISOString() : null,
      exchange: quote.fullExchangeName || quote.exchange || meta.exchangeName || "",
      range: {
        high52w: round(yearHigh),
        low52w: round(yearLow)
      }
    },
    scores: {
      overall: Math.round(overallScore),
      technical: technical.score,
      fundamental: fundamental.score,
      eventRisk: eventRisk.score
    },
    outlook,
    technical: {
      score: technical.score,
      summary: technical.summary,
      signals: technical.signals,
      indicators: {
        sma20: roundOrNull(last(sma20)),
        sma50: roundOrNull(last(sma50)),
        sma200: roundOrNull(last(sma200)),
        ema12: roundOrNull(last(ema12)),
        ema26: roundOrNull(last(ema26)),
        rsi14: roundOrNull(last(rsi14)),
        macd: roundOrNull(last(macdData.line)),
        macdSignal: roundOrNull(last(macdData.signal)),
        macdHistogram: roundOrNull(last(macdData.histogram)),
        atr14: roundOrNull(last(atr14)),
        atrPercent: roundOrNull(safeDivide(last(atr14), current) * 100),
        bollingerUpper: roundOrNull(last(bands.upper)),
        bollingerMiddle: roundOrNull(last(bands.middle)),
        bollingerLower: roundOrNull(last(bands.lower)),
        avgVolume20: Math.round(avgVolume20 || 0),
        volumeRatio: roundOrNull(volumeRatio)
      },
      performance: {
        oneMonth: roundOrNull(periodReturn(closes, 21)),
        threeMonth: roundOrNull(periodReturn(closes, 63)),
        sixMonth: roundOrNull(periodReturn(closes, 126)),
        oneYear: roundOrNull(periodReturn(closes, closes.length - 1))
      },
      levels: supportResistance
    },
    fundamentals: {
      score: fundamental.score,
      summary: fundamental.summary,
      signals: fundamental.signals,
      metrics: fundamentals
    },
    events: {
      risk: eventRisk,
      items: events
    },
    researchLevels,
    series
  };
}

function extractFundamentals(summary, quote, meta, sec, screener, currentPrice) {
  const price = summary.price || {};
  const detail = summary.summaryDetail || {};
  const key = summary.defaultKeyStatistics || {};
  const financial = summary.financialData || {};
  const profile = summary.assetProfile || {};
  const secMetrics = sec.metrics || {};
  const screenerMetrics = screener.metrics || {};

  return {
    sector: profile.sector || "",
    industry: profile.industry || "",
    website: profile.website || "",
    marketCap: raw(price.marketCap) || raw(detail.marketCap) || quote.marketCap || impliedMarketCap(secMetrics, currentPrice) || screenerMetrics.marketCap,
    beta: raw(detail.beta) || quote.beta || null,
    trailingPE: raw(detail.trailingPE) || quote.trailingPE || impliedTrailingPE(secMetrics, currentPrice) || screenerMetrics.trailingPE,
    forwardPE: raw(detail.forwardPE) || quote.forwardPE || null,
    pegRatio: raw(key.pegRatio),
    priceToBook: raw(key.priceToBook) || quote.priceToBook || impliedPriceToBook(secMetrics, currentPrice) || screenerMetrics.priceToBook,
    profitMargins: raw(financial.profitMargins) ?? secMetrics.profitMargins ?? screenerMetrics.profitMargins ?? null,
    operatingMargins: raw(financial.operatingMargins) ?? secMetrics.operatingMargins ?? null,
    grossMargins: raw(financial.grossMargins) ?? secMetrics.grossMargins ?? null,
    returnOnEquity: raw(financial.returnOnEquity) ?? secMetrics.returnOnEquity ?? screenerMetrics.returnOnEquity ?? null,
    returnOnCapitalEmployed: screenerMetrics.returnOnCapitalEmployed ?? null,
    revenueGrowth: raw(financial.revenueGrowth) ?? secMetrics.revenueGrowth ?? screenerMetrics.revenueGrowth ?? null,
    earningsGrowth: raw(financial.earningsGrowth) ?? secMetrics.earningsGrowth ?? screenerMetrics.earningsGrowth ?? null,
    salesGrowth5y: screenerMetrics.salesGrowth5y ?? null,
    promoterHolding: screenerMetrics.promoterHolding ?? null,
    debtToEquity: raw(financial.debtToEquity) ?? secMetrics.debtToEquity ?? screenerMetrics.debtToEquity ?? null,
    currentRatio: raw(financial.currentRatio) ?? secMetrics.currentRatio ?? null,
    totalCash: raw(financial.totalCash) ?? secMetrics.totalCash ?? null,
    totalDebt: raw(financial.totalDebt) ?? secMetrics.totalDebt ?? null,
    freeCashflow: raw(financial.freeCashflow) ?? secMetrics.freeCashflow ?? null,
    revenue: secMetrics.revenue ?? screenerMetrics.revenue ?? null,
    netIncome: secMetrics.netIncome ?? screenerMetrics.netIncome ?? null,
    bookValuePerShare: screenerMetrics.bookValuePerShare ?? null,
    targetMeanPrice: raw(financial.targetMeanPrice),
    recommendationMean: raw(financial.recommendationMean),
    recommendationKey: financial.recommendationKey || "",
    dividendYield: raw(detail.dividendYield) || quote.dividendYield || screenerMetrics.dividendYield || null,
    currency: raw(price.currency) || quote.currency || meta.currency || "",
    dataSource: [sec.source, screener.source].filter(Boolean).join(" + ") || "Market data provider",
    latestFiling: sec.latestFiling || screener.latestUpdate || null
  };
}

function extractScreenerMetrics(html) {
  const ratios = extractScreenerRatios(html);
  const description = extractMetaDescription(html);
  const companyName = cleanHtml(matchFirst(html, /<h1[^>]*>\s*([^<]+?)\s*<\/h1>/i));
  const currentPrice = ratios["Current Price"];
  const marketCapCrore = ratios["Market Cap"];
  const revenueCrore = parseDescriptionNumber(description, /Revenue:\s*([\d,.]+)\s*Cr/i);
  const profitCrore = parseDescriptionNumber(description, /Profit:\s*([\d,.]+)\s*Cr/i);
  const salesGrowth5y = parseDescriptionPercent(description, /sales growth of\s*([\d.]+)%/i);
  const promoterHolding = parseDescriptionPercent(description, /Promoter Holding:\s*([\d.]+)%/i);
  const earningsGrowth = parseDescriptionPercent(html, /profit growth of\s*([\d.]+)%\s*CAGR/i);
  const bookValuePerShare = ratios["Book Value"];
  const priceToBook = currentPrice && bookValuePerShare ? currentPrice / bookValuePerShare : null;
  const debtToEquity = /almost debt free/i.test(html) ? 0 : null;

  return {
    companyName,
    source: "Screener.in summary",
    latestUpdate: null,
    metrics: {
      marketCap: croreToRupees(marketCapCrore),
      trailingPE: ratios["Stock P/E"] ?? null,
      bookValuePerShare: bookValuePerShare ?? null,
      priceToBook,
      dividendYield: ratioPercent(ratios["Dividend Yield"]),
      returnOnCapitalEmployed: ratioPercent(ratios.ROCE),
      returnOnEquity: ratioPercent(ratios.ROE),
      revenue: croreToRupees(revenueCrore),
      netIncome: croreToRupees(profitCrore),
      profitMargins: revenueCrore && profitCrore ? profitCrore / revenueCrore : null,
      salesGrowth5y,
      revenueGrowth: salesGrowth5y,
      earningsGrowth,
      promoterHolding,
      debtToEquity
    },
    events: extractScreenerEvents(html)
  };
}

function extractSecMetrics(payload, fallbackName) {
  const facts = payload.facts || {};
  const usgaap = facts["us-gaap"] || {};
  const dei = facts.dei || {};
  const revenue = latestAnnualPair(usgaap, [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet"
  ]);
  const netIncome = latestAnnualPair(usgaap, ["NetIncomeLoss", "ProfitLoss"]);
  const operatingIncome = latestAnnualPair(usgaap, ["OperatingIncomeLoss"]);
  const grossProfit = latestAnnualPair(usgaap, ["GrossProfit"]);
  const operatingCashFlow = latestAnnualPair(usgaap, ["NetCashProvidedByUsedInOperatingActivities"]);
  const capex = latestAnnualPair(usgaap, ["PaymentsToAcquirePropertyPlantAndEquipment"]);
  const eps = latestAnnualPair(usgaap, ["EarningsPerShareDiluted"], "USD/shares");
  const assets = latestInstant(usgaap, ["Assets"]);
  const liabilities = latestInstant(usgaap, ["Liabilities"]);
  const equity = latestInstant(usgaap, [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
  ]);
  const currentAssets = latestInstant(usgaap, ["AssetsCurrent"]);
  const currentLiabilities = latestInstant(usgaap, ["LiabilitiesCurrent"]);
  const totalCash = latestInstant(usgaap, [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
  ]);
  const debtCurrent = latestInstant(usgaap, [
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "LongTermDebtCurrent",
    "ShortTermBorrowings"
  ]);
  const debtNonCurrent = latestInstant(usgaap, [
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "LongTermDebtNoncurrent"
  ]);
  const totalDebt = addNumbers(debtCurrent.value, debtNonCurrent.value) || latestInstant(usgaap, ["LongTermDebt"]).value;
  const sharesOutstanding = latestInstant(dei, ["EntityCommonStockSharesOutstanding"], "shares");
  const freeCashflow = Number.isFinite(operatingCashFlow.current) && Number.isFinite(capex.current)
    ? operatingCashFlow.current - Math.abs(capex.current)
    : null;

  return {
    companyName: payload.entityName || fallbackName || "",
    source: "SEC company facts",
    latestFiling: latestFilingDate([
      revenue.currentItem,
      netIncome.currentItem,
      assets.item,
      sharesOutstanding.item
    ]),
    metrics: {
      revenue: revenue.current,
      netIncome: netIncome.current,
      assets: assets.value,
      liabilities: liabilities.value,
      equity: equity.value,
      sharesOutstanding: sharesOutstanding.value,
      epsDiluted: eps.current,
      profitMargins: ratio(netIncome.current, revenue.current),
      operatingMargins: ratio(operatingIncome.current, revenue.current),
      grossMargins: ratio(grossProfit.current, revenue.current),
      returnOnEquity: ratio(netIncome.current, equity.value),
      revenueGrowth: growth(revenue.current, revenue.previous),
      earningsGrowth: growth(netIncome.current, netIncome.previous),
      debtToEquity: ratio(totalDebt, equity.value) !== null ? ratio(totalDebt, equity.value) * 100 : null,
      currentRatio: ratio(currentAssets.value, currentLiabilities.value),
      totalCash: totalCash.value,
      totalDebt,
      freeCashflow
    }
  };
}

function latestAnnualPair(facts, concepts, preferredUnit = "USD") {
  const values = secValues(facts, concepts, preferredUnit)
    .filter((item) => item.start && item.end && item.form && item.form.startsWith("10-K"))
    .sort(secNewestFirst);
  const yearly = [];
  const seen = new Set();

  for (const item of values) {
    const key = item.end;
    if (!seen.has(key) && Number.isFinite(item.val)) {
      yearly.push(item);
      seen.add(key);
    }
    if (yearly.length === 2) {
      break;
    }
  }

  return {
    current: yearly[0]?.val ?? null,
    previous: yearly[1]?.val ?? null,
    currentItem: yearly[0] || null,
    previousItem: yearly[1] || null
  };
}

function latestInstant(facts, concepts, preferredUnit = "USD") {
  const item = secValues(facts, concepts, preferredUnit)
    .filter((value) => value.end && Number.isFinite(value.val))
    .sort(secNewestFirst)[0];

  return {
    value: item?.val ?? null,
    item: item || null
  };
}

function secValues(facts, concepts, preferredUnit) {
  for (const concept of concepts) {
    const fact = facts[concept];
    if (!fact || !fact.units) {
      continue;
    }
    const unitKey = fact.units[preferredUnit] ? preferredUnit : Object.keys(fact.units)[0];
    const values = fact.units[unitKey] || [];
    if (values.length) {
      return values;
    }
  }
  return [];
}

function secNewestFirst(a, b) {
  const endDiff = new Date(b.end || 0) - new Date(a.end || 0);
  if (endDiff) {
    return endDiff;
  }
  return new Date(b.filed || 0) - new Date(a.filed || 0);
}

function latestFilingDate(items) {
  const dates = items
    .filter(Boolean)
    .map((item) => item.filed)
    .filter(Boolean)
    .sort((a, b) => new Date(b) - new Date(a));
  return dates[0] || null;
}

function impliedMarketCap(metrics, currentPrice) {
  if (!Number.isFinite(metrics.sharesOutstanding) || !Number.isFinite(currentPrice)) {
    return null;
  }
  return metrics.sharesOutstanding * currentPrice;
}

function impliedTrailingPE(metrics, currentPrice) {
  if (!Number.isFinite(metrics.epsDiluted) || !Number.isFinite(currentPrice) || metrics.epsDiluted <= 0) {
    return null;
  }
  return currentPrice / metrics.epsDiluted;
}

function impliedPriceToBook(metrics, currentPrice) {
  const marketCap = impliedMarketCap(metrics, currentPrice);
  if (!Number.isFinite(marketCap) || !Number.isFinite(metrics.equity) || metrics.equity <= 0) {
    return null;
  }
  return marketCap / metrics.equity;
}

function ratio(numerator, denominator) {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) {
    return null;
  }
  return numerator / denominator;
}

function growth(current, previous) {
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) {
    return null;
  }
  return (current - previous) / Math.abs(previous);
}

function addNumbers(...values) {
  const usable = values.filter(Number.isFinite);
  return usable.length ? usable.reduce((sum, value) => sum + value, 0) : null;
}

function extractEvents(summary, screener) {
  const calendar = summary.calendarEvents || {};
  const earnings = calendar.earnings || {};
  const trend = summary.earningsTrend || {};
  const upgrades = summary.upgradeDowngradeHistory || {};
  const items = [];

  const earningsDates = arrayify(earnings.earningsDate)
    .map(raw)
    .filter(Boolean)
    .map((date) => ({
      type: "Earnings",
      date: toDate(date),
      detail: "Expected results date"
    }));

  items.push(...earningsDates);

  if (raw(calendar.exDividendDate)) {
    items.push({
      type: "Ex-dividend",
      date: toDate(raw(calendar.exDividendDate)),
      detail: "Dividend eligibility date"
    });
  }

  if (raw(calendar.dividendDate)) {
    items.push({
      type: "Dividend payment",
      date: toDate(raw(calendar.dividendDate)),
      detail: "Expected dividend payment date"
    });
  }

  const nextQuarter = arrayify(trend.trend).find((item) => item.period && item.period.includes("+1q"));
  if (nextQuarter) {
    items.push({
      type: "Earnings trend",
      date: null,
      detail: `Next quarter EPS estimate: ${formatEstimate(raw(nextQuarter.earningsEstimate?.avg))}`
    });
  }

  arrayify(upgrades.history)
    .slice(0, 3)
    .forEach((item) => {
      items.push({
        type: "Analyst action",
        date: item.epochGradeDate ? toDate(item.epochGradeDate) : null,
        detail: [item.firm, item.action, item.toGrade].filter(Boolean).join(" - ")
      });
    });

  items.push(...arrayify(screener.events));

  return items
    .filter((item) => item.date || item.detail)
    .sort((a, b) => {
      if (!a.date) return 1;
      if (!b.date) return -1;
      return new Date(a.date) - new Date(b.date);
    });
}

function extractScreenerRatios(html) {
  const block = matchFirst(html, /<ul id="top-ratios">([\s\S]*?)<\/ul>/i);
  const ratios = {};

  for (const match of block.matchAll(/<li[\s\S]*?<\/li>/gi)) {
    const item = match[0];
    const name = cleanHtml(matchFirst(item, /<span class="name">([\s\S]*?)<\/span>/i));
    const numbers = [...item.matchAll(/<span class="number">([\s\S]*?)<\/span>/gi)]
      .map((numberMatch) => parseLooseNumber(cleanHtml(numberMatch[1])))
      .filter(Number.isFinite);

    if (!name || !numbers.length) {
      continue;
    }
    ratios[name] = numbers.length === 1 ? numbers[0] : numbers;
  }

  return ratios;
}

function extractScreenerEvents(html) {
  const events = [];
  const boardMeetingDate = matchFirst(html, /Board meets ([A-Z][a-z]+ \d{1,2}, \d{4})/i);

  if (boardMeetingDate) {
    events.push({
      type: "Board meeting",
      date: toIsoDate(boardMeetingDate),
      detail: "Expected audited results and final dividend discussion"
    });
  }

  return events;
}

function extractMetaDescription(html) {
  return decodeHtml(matchFirst(html, /<meta name="description" content="([^"]*)"/i));
}

function parseDescriptionNumber(text, pattern) {
  return parseLooseNumber(matchFirst(text, pattern));
}

function parseDescriptionPercent(text, pattern) {
  const value = parseLooseNumber(matchFirst(text, pattern));
  return Number.isFinite(value) ? value / 100 : null;
}

function cleanHtml(value) {
  return decodeHtml(String(value || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
}

function decodeHtml(value) {
  return String(value || "")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#39;", "'")
    .replaceAll("&nbsp;", " ");
}

function matchFirst(value, pattern) {
  const match = String(value || "").match(pattern);
  return match ? match[1] : "";
}

function parseLooseNumber(value) {
  const normalized = String(value || "").replace(/,/g, "").trim();
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function croreToRupees(value) {
  return Number.isFinite(value) ? value * 10_000_000 : null;
}

function ratioPercent(value) {
  return Number.isFinite(value) ? value / 100 : null;
}

function toIsoDate(value) {
  const match = String(value || "").match(/^([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})$/);
  if (match) {
    const months = {
      january: "01",
      february: "02",
      march: "03",
      april: "04",
      may: "05",
      june: "06",
      july: "07",
      august: "08",
      september: "09",
      october: "10",
      november: "11",
      december: "12"
    };
    const month = months[match[1].toLowerCase()];
    if (month) {
      return `${match[3]}-${month}-${String(match[2]).padStart(2, "0")}`;
    }
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function scoreTechnical(input) {
  let score = 50;
  const signals = [];

  if (input.current > input.sma20) {
    score += 7;
    signals.push("Price is above the 20-day average, showing short-term strength.");
  } else {
    score -= 7;
    signals.push("Price is below the 20-day average, showing short-term weakness.");
  }

  if (input.current > input.sma50) {
    score += 9;
    signals.push("Price is above the 50-day average, which supports the medium-term trend.");
  } else {
    score -= 9;
    signals.push("Price is below the 50-day average, so trend confirmation is weaker.");
  }

  if (input.sma200 && input.current > input.sma200) {
    score += 12;
    signals.push("Price is above the 200-day average, which favors the long-term trend.");
  } else if (input.sma200) {
    score -= 12;
    signals.push("Price is below the 200-day average, which keeps long-term risk elevated.");
  }

  if (input.sma50 && input.sma200 && input.sma50 > input.sma200) {
    score += 8;
    signals.push("The 50-day average is above the 200-day average.");
  } else if (input.sma50 && input.sma200) {
    score -= 8;
    signals.push("The 50-day average is below the 200-day average.");
  }

  if (input.rsi14 >= 45 && input.rsi14 <= 65) {
    score += 7;
    signals.push("RSI is in a constructive range, not yet stretched.");
  } else if (input.rsi14 > 70) {
    score -= 8;
    signals.push("RSI is above 70, so the stock may be overextended.");
  } else if (input.rsi14 < 35) {
    score -= 6;
    signals.push("RSI is weak and below 35.");
  }

  if (input.macdLine > input.macdSignal && input.macdHist > 0) {
    score += 9;
    signals.push("MACD is above its signal line.");
  } else {
    score -= 6;
    signals.push("MACD is not confirming upside momentum.");
  }

  if (input.volumeRatio && input.volumeRatio > 1.4 && input.current > input.sma20) {
    score += 5;
    signals.push("Recent volume is above its 20-day average on strength.");
  }

  if (input.atrPercent > 6) {
    score -= 5;
    signals.push("ATR is high, so position sizing and stops need extra care.");
  }

  const distanceFromHigh = safeDivide(input.yearHigh - input.current, input.yearHigh) * 100;
  const distanceFromLow = safeDivide(input.current - input.yearLow, input.yearLow) * 100;
  if (distanceFromHigh < 10) {
    score += 4;
    signals.push("Price is trading near its one-year high.");
  }
  if (distanceFromLow < 12) {
    score -= 4;
    signals.push("Price is close to its one-year low.");
  }

  score = clamp(Math.round(score), 0, 100);
  return {
    score,
    signals,
    summary: score >= 70 ? "Bullish technical structure" : score >= 45 ? "Mixed technical structure" : "Weak technical structure"
  };
}

function scoreFundamental(metrics) {
  let score = 50;
  const signals = [];

  if (metrics.revenueGrowth !== null && metrics.revenueGrowth > 0.08) {
    score += 10;
    signals.push("Revenue growth is positive and above 8%.");
  } else if (metrics.revenueGrowth !== null && metrics.revenueGrowth < 0) {
    score -= 10;
    signals.push("Revenue growth is negative.");
  }

  if (metrics.earningsGrowth !== null && metrics.earningsGrowth > 0.08) {
    score += 10;
    signals.push("Earnings growth is positive and above 8%.");
  } else if (metrics.earningsGrowth !== null && metrics.earningsGrowth < 0) {
    score -= 10;
    signals.push("Earnings growth is negative.");
  }

  if (metrics.profitMargins !== null && metrics.profitMargins > 0.12) {
    score += 8;
    signals.push("Profit margin is healthy.");
  } else if (metrics.profitMargins !== null && metrics.profitMargins < 0.03) {
    score -= 8;
    signals.push("Profit margin is thin.");
  }

  if (metrics.returnOnEquity !== null && metrics.returnOnEquity > 0.15) {
    score += 8;
    signals.push("Return on equity is strong.");
  } else if (metrics.returnOnEquity !== null && metrics.returnOnEquity < 0.05) {
    score -= 6;
    signals.push("Return on equity is weak.");
  }

  if (metrics.debtToEquity !== null && metrics.debtToEquity < 80) {
    score += 6;
    signals.push("Debt-to-equity is manageable.");
  } else if (metrics.debtToEquity !== null && metrics.debtToEquity > 180) {
    score -= 10;
    signals.push("Debt-to-equity is high.");
  }

  if (metrics.currentRatio !== null && metrics.currentRatio >= 1.2) {
    score += 4;
    signals.push("Current ratio suggests acceptable liquidity.");
  } else if (metrics.currentRatio !== null && metrics.currentRatio < 1) {
    score -= 5;
    signals.push("Current ratio is below 1.");
  }

  if (metrics.trailingPE !== null && metrics.trailingPE > 0 && metrics.trailingPE < 30) {
    score += 5;
    signals.push("Trailing P/E is below 30.");
  } else if (metrics.trailingPE !== null && metrics.trailingPE > 60) {
    score -= 7;
    signals.push("Trailing P/E is elevated.");
  }

  if (metrics.recommendationMean !== null && metrics.recommendationMean <= 2.5) {
    score += 4;
    signals.push("Analyst recommendation mean is constructive.");
  } else if (metrics.recommendationMean !== null && metrics.recommendationMean >= 4) {
    score -= 4;
    signals.push("Analyst recommendation mean is weak.");
  }

  if (!signals.length) {
    signals.push("Limited fundamental data was available for this symbol.");
  }

  score = clamp(Math.round(score), 0, 100);
  return {
    score,
    signals,
    summary: score >= 70 ? "Strong fundamental profile" : score >= 45 ? "Mixed fundamental profile" : "Weak fundamental profile"
  };
}

function scoreEventRisk(events) {
  const now = new Date();
  const futureEvents = events
    .filter((event) => event.date)
    .map((event) => ({ ...event, daysAway: Math.ceil((new Date(event.date) - now) / 86400000) }))
    .filter((event) => event.daysAway >= 0);

  const nextResultsEvent = futureEvents.find((event) => event.type === "Earnings" || event.type === "Board meeting");
  let score = 25;
  let label = "Normal";
  let summary = "No near-term results event was found in the available data.";

  if (nextResultsEvent && nextResultsEvent.daysAway <= 7) {
    score = 85;
    label = "High";
    summary = "A results-related event appears to be within one week, so gap risk is high.";
  } else if (nextResultsEvent && nextResultsEvent.daysAway <= 21) {
    score = 65;
    label = "Elevated";
    summary = "A results-related event appears to be within three weeks, so volatility may rise.";
  } else if (nextResultsEvent && nextResultsEvent.daysAway <= 45) {
    score = 45;
    label = "Watch";
    summary = "A results-related event appears to be within 45 days.";
  }

  return { score, label, summary };
}

function buildResearchLevels(input) {
  const atrValue = input.atrValue || input.current * 0.025;
  const support = input.support || input.current - atrValue * 2;
  const resistance = input.resistance || input.current + atrValue * 2;
  const pullbackLow = Math.max(0.01, support);
  const pullbackHigh = Math.max(pullbackLow, Math.min(input.current, support + atrValue * 0.55));
  const breakoutTrigger = resistance + atrValue * 0.25;
  const stop = Math.max(0.01, support - atrValue);
  const risk = Math.max(pullbackHigh - stop, atrValue * 0.5);
  const targetOne = Math.max(resistance, pullbackHigh + risk * 1.8);
  const targetTwo = Math.max(input.yearHigh, pullbackHigh + risk * 2.6);
  const mode = input.technicalScore >= 65 ? "Trend-following" : input.technicalScore >= 45 ? "Confirmation needed" : "Waitlist";

  return {
    mode,
    note: "These are research zones from support, resistance, and ATR. They are not instructions to buy or sell.",
    pullbackEntry: {
      low: round(pullbackLow),
      high: round(pullbackHigh)
    },
    breakoutTrigger: round(breakoutTrigger),
    invalidation: round(stop),
    targets: [round(targetOne), round(targetTwo)],
    support: round(support),
    resistance: round(resistance),
    riskReward: round(safeDivide(targetOne - pullbackHigh, pullbackHigh - stop))
  };
}

function buildOutlook(overallScore, technicalScore, fundamentalScore, eventRisk) {
  let label = "Neutral";
  let summary = "The stock has mixed evidence. Wait for price confirmation near the listed levels.";

  if (overallScore >= 72 && technicalScore >= 65 && fundamentalScore >= 55) {
    label = "Constructive";
    summary = "The setup is constructive if price holds above support and volume confirms strength.";
  } else if (overallScore <= 42 || technicalScore < 40) {
    label = "Cautious";
    summary = "Risk is elevated. The cleaner setup is to wait for a close back above key averages.";
  }

  if (eventRisk.score >= 65) {
    summary += " Upcoming event risk can override the chart, so avoid treating levels as fixed.";
  }

  return { label, summary };
}

function buildSourceLabel(sec, screener) {
  return [
    "Yahoo Finance public endpoints",
    sec.source || "",
    screener.source || ""
  ].filter(Boolean).join(" and ");
}

function findLevels(candles, current, atrValue) {
  const lookback = candles.slice(-252);
  const radius = 4;
  const effectiveAtr = atrValue || current * 0.025;
  const zoneWidth = Math.max(current * 0.006, effectiveAtr * 0.65);
  const clusterThreshold = Math.max(current * 0.01, effectiveAtr * 0.9);
  const candidates = [];

  for (let index = radius; index < lookback.length - radius; index += 1) {
    const window = lookback.slice(index - radius, index + radius + 1);
    const candle = lookback[index];
    const minLow = Math.min(...window.map((item) => item.low));
    const maxHigh = Math.max(...window.map((item) => item.high));

    if (candle.low <= minLow) {
      candidates.push({
        type: "support",
        price: candle.low,
        date: candle.date,
        index,
        source: "swing low"
      });
    }

    if (candle.high >= maxHigh) {
      candidates.push({
        type: "resistance",
        price: candle.high,
        date: candle.date,
        index,
        source: "swing high"
      });
    }
  }

  addRangeLevels(candidates, lookback, [20, 50, 100, lookback.length]);

  let supportZones = rankLevels(candidates, lookback, current, zoneWidth, clusterThreshold, "support")
    .filter((level) => level.price < current);
  let resistanceZones = rankLevels(candidates, lookback, current, zoneWidth, clusterThreshold, "resistance")
    .filter((level) => level.price > current);

  if (!supportZones.length) {
    const fallback = Math.min(...lookback.slice(-60).map((item) => item.low));
    supportZones.push(describeLevel([{ price: fallback, source: "60-day low" }], "support", lookback, current, zoneWidth));
  }

  if (!resistanceZones.length) {
    const fallback = Math.max(...lookback.slice(-60).map((item) => item.high));
    resistanceZones.push(describeLevel([{ price: fallback, source: "60-day high" }], "resistance", lookback, current, zoneWidth));
  }

  supportZones = pickRelevantLevels(supportZones, current, "support");
  resistanceZones = pickRelevantLevels(resistanceZones, current, "resistance");

  return {
    supports: supportZones.map((level) => level.price),
    resistances: resistanceZones.map((level) => level.price),
    supportZones,
    resistanceZones
  };
}

function addRangeLevels(candidates, candles, periods) {
  for (const period of periods) {
    const slice = candles.slice(-period);
    if (!slice.length) {
      continue;
    }

    const lowCandle = slice.reduce((lowest, candle) => (candle.low < lowest.low ? candle : lowest), slice[0]);
    const highCandle = slice.reduce((highest, candle) => (candle.high > highest.high ? candle : highest), slice[0]);

    candidates.push({
      type: "support",
      price: lowCandle.low,
      date: lowCandle.date,
      index: candles.indexOf(lowCandle),
      source: `${period}-session low`
    });
    candidates.push({
      type: "resistance",
      price: highCandle.high,
      date: highCandle.date,
      index: candles.indexOf(highCandle),
      source: `${period}-session high`
    });
  }
}

function rankLevels(candidates, candles, current, zoneWidth, clusterThreshold, type) {
  return clusterLevelCandidates(
    candidates.filter((candidate) => candidate.type === type),
    clusterThreshold
  )
    .map((cluster) => describeLevel(cluster, type, candles, current, zoneWidth))
    .sort((a, b) => b.rank - a.rank);
}

function clusterLevelCandidates(candidates, threshold) {
  const sorted = candidates.filter((candidate) => Number.isFinite(candidate.price)).sort((a, b) => a.price - b.price);
  const clusters = [];

  for (const candidate of sorted) {
    const current = clusters[clusters.length - 1];
    if (!current || Math.abs(current.average - candidate.price) > threshold) {
      clusters.push({ values: [candidate], average: candidate.price });
    } else {
      current.values.push(candidate);
      current.average = current.values.reduce((sum, item) => sum + item.price, 0) / current.values.length;
    }
  }

  return clusters.map((cluster) => cluster.values);
}

function describeLevel(cluster, type, candles, current, zoneWidth) {
  const price = cluster.reduce((sum, item) => sum + item.price, 0) / cluster.length;
  const touches = candles.filter((candle) => touchesLevel(candle, price, zoneWidth, type));
  const lastTouchIndex = touches.length ? candles.indexOf(touches[touches.length - 1]) : 0;
  const recentTouches = touches.filter((candle) => candles.indexOf(candle) >= candles.length - 60).length;
  const averageVolume = average(candles.map((candle) => candle.volume || 0));
  const touchVolume = average(touches.map((candle) => candle.volume || 0));
  const volumeRatio = averageVolume ? touchVolume / averageVolume : 1;
  const rejection = average(touches.map((candle) => rejectionScore(candle, type)));
  const recency = candles.length ? lastTouchIndex / Math.max(1, candles.length - 1) : 0;
  const sources = [...new Set(cluster.map((item) => item.source).filter(Boolean))];
  const distancePercent = safeDivide(price - current, current) * 100;
  const strength = clamp(Math.round(
    touches.length * 6 +
    recentTouches * 5 +
    rejection * 18 +
    Math.min(volumeRatio, 2) * 7 +
    sources.length * 5 +
    recency * 14
  ), 20, 95);
  const proximityPenalty = Math.abs(distancePercent) * 0.7;

  return {
    price: round(price),
    zoneLow: round(Math.max(0.01, price - zoneWidth)),
    zoneHigh: round(price + zoneWidth),
    strength,
    label: strength >= 78 ? "Major" : strength >= 62 ? "Strong" : strength >= 45 ? "Valid" : "Minor",
    touches: touches.length,
    lastTouched: touches[touches.length - 1]?.date || cluster[cluster.length - 1]?.date || null,
    distancePercent: round(distancePercent),
    sources: sources.slice(0, 3),
    rank: strength - proximityPenalty
  };
}

function touchesLevel(candle, price, zoneWidth, type) {
  if (type === "support") {
    return candle.low <= price + zoneWidth && candle.close >= price - zoneWidth;
  }
  return candle.high >= price - zoneWidth && candle.close <= price + zoneWidth;
}

function rejectionScore(candle, type) {
  const range = candle.high - candle.low;
  if (!range) {
    return 0;
  }
  return type === "support" ? (candle.close - candle.low) / range : (candle.high - candle.close) / range;
}

function pickRelevantLevels(levels, current, type) {
  const selected = levels
    .sort((a, b) => b.rank - a.rank)
    .slice(0, 4);

  return selected
    .sort((a, b) => type === "support" ? b.price - a.price : a.price - b.price)
    .map(({ rank, ...level }) => level);
}

function average(values) {
  const usable = values.filter(Number.isFinite);
  return usable.length ? usable.reduce((sum, value) => sum + value, 0) / usable.length : 0;
}

function sma(values, period) {
  const output = Array(values.length).fill(null);
  let sum = 0;
  for (let index = 0; index < values.length; index += 1) {
    sum += values[index];
    if (index >= period) {
      sum -= values[index - period];
    }
    if (index >= period - 1) {
      output[index] = sum / period;
    }
  }
  return output;
}

function ema(values, period) {
  const output = Array(values.length).fill(null);
  const multiplier = 2 / (period + 1);
  let previous = null;

  for (let index = 0; index < values.length; index += 1) {
    if (index === period - 1) {
      previous = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
      output[index] = previous;
    } else if (index >= period) {
      previous = values[index] * multiplier + previous * (1 - multiplier);
      output[index] = previous;
    }
  }

  return output;
}

function rsi(values, period) {
  const output = Array(values.length).fill(null);
  let gain = 0;
  let loss = 0;

  for (let index = 1; index <= period; index += 1) {
    const change = values[index] - values[index - 1];
    gain += Math.max(change, 0);
    loss += Math.max(-change, 0);
  }

  let averageGain = gain / period;
  let averageLoss = loss / period;
  output[period] = averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss);

  for (let index = period + 1; index < values.length; index += 1) {
    const change = values[index] - values[index - 1];
    averageGain = (averageGain * (period - 1) + Math.max(change, 0)) / period;
    averageLoss = (averageLoss * (period - 1) + Math.max(-change, 0)) / period;
    output[index] = averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss);
  }

  return output;
}

function macd(values) {
  const fast = ema(values, 12);
  const slow = ema(values, 26);
  const line = fast.map((value, index) => {
    return value !== null && slow[index] !== null ? value - slow[index] : null;
  });
  const compactLine = line.filter((value) => value !== null);
  const compactSignal = ema(compactLine, 9);
  const signal = Array(values.length).fill(null);
  let signalIndex = 0;

  for (let index = 0; index < line.length; index += 1) {
    if (line[index] !== null) {
      signal[index] = compactSignal[signalIndex];
      signalIndex += 1;
    }
  }

  return {
    line,
    signal,
    histogram: line.map((value, index) => (value !== null && signal[index] !== null ? value - signal[index] : null))
  };
}

function bollinger(values, period, multiplier) {
  const middle = sma(values, period);
  const upper = Array(values.length).fill(null);
  const lower = Array(values.length).fill(null);

  for (let index = period - 1; index < values.length; index += 1) {
    const slice = values.slice(index - period + 1, index + 1);
    const mean = middle[index];
    const variance = slice.reduce((sum, value) => sum + (value - mean) ** 2, 0) / period;
    const deviation = Math.sqrt(variance);
    upper[index] = mean + deviation * multiplier;
    lower[index] = mean - deviation * multiplier;
  }

  return { upper, middle, lower };
}

function atr(candles, period) {
  const trueRanges = candles.map((candle, index) => {
    if (index === 0) {
      return candle.high - candle.low;
    }
    const previousClose = candles[index - 1].close;
    return Math.max(
      candle.high - candle.low,
      Math.abs(candle.high - previousClose),
      Math.abs(candle.low - previousClose)
    );
  });
  return sma(trueRanges, period);
}

function periodReturn(values, sessions) {
  if (!sessions || values.length <= sessions) {
    return null;
  }
  const start = values[values.length - 1 - sessions];
  const end = values[values.length - 1];
  return safeDivide(end - start, start) * 100;
}

async function fetchJson(endpoint) {
  const response = await fetch(endpoint, {
    headers: {
      "Accept": "application/json,text/plain,*/*",
      "User-Agent": "Mozilla/5.0 StockResearchDesk/0.1"
    }
  });

  if (!response.ok) {
    throw new Error(`Data provider returned ${response.status}.`);
  }

  return response.json();
}

async function fetchSecJson(endpoint) {
  const response = await fetch(endpoint, {
    headers: {
      "Accept": "application/json",
      "User-Agent": SEC_USER_AGENT
    }
  });

  if (!response.ok) {
    throw new Error(`SEC data returned ${response.status}.`);
  }

  return response.json();
}

async function fetchText(endpoint) {
  const response = await fetch(endpoint, {
    headers: {
      "Accept": "text/html,application/xhtml+xml",
      "User-Agent": "Mozilla/5.0 StockResearchDesk/0.1"
    }
  });

  if (!response.ok) {
    throw new Error(`Text data provider returned ${response.status}.`);
  }

  return response.text();
}

async function cached(key, loader, ttl = CACHE_TTL_MS) {
  const cachedValue = cache.get(key);
  if (cachedValue && cachedValue.expiresAt > Date.now()) {
    return cachedValue.data;
  }
  const data = await loader();
  cache.set(key, { data, expiresAt: Date.now() + ttl });
  return data;
}

function normalizeSymbol(value) {
  const symbol = String(value || "").trim().toUpperCase();
  return /^[A-Z0-9.^=_-]{1,25}$/.test(symbol) ? symbol : "";
}

function sendJson(res, status, payload) {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function sendText(res, status, text) {
  res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(text);
}

function raw(value) {
  if (value && typeof value === "object" && "raw" in value) {
    return value.raw;
  }
  return value ?? null;
}

function arrayify(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

function toDate(epochSeconds) {
  return new Date(Number(epochSeconds) * 1000).toISOString().slice(0, 10);
}

function cleanNumber(value) {
  return Number.isFinite(value) ? value : null;
}

function last(values) {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null && values[index] !== undefined && Number.isFinite(values[index])) {
      return values[index];
    }
  }
  return null;
}

function safeDivide(numerator, denominator) {
  return denominator ? numerator / denominator : 0;
}

function round(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

function roundOrNull(value) {
  return Number.isFinite(value) ? round(value) : null;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function weightedAverage(items) {
  const totalWeight = items.reduce((sum, item) => sum + item[1], 0);
  return items.reduce((sum, item) => sum + item[0] * item[1], 0) / totalWeight;
}

function formatEstimate(value) {
  return value === null || value === undefined ? "not available" : round(value);
}
