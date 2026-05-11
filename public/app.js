const form = document.querySelector("#analysisForm");
const symbolInput = document.querySelector("#symbolInput");
const suggestions = document.querySelector("#symbolSuggestions");
const emptyState = document.querySelector("#emptyState");
const loadingState = document.querySelector("#loadingState");
const errorState = document.querySelector("#errorState");
const reportEl = document.querySelector("#report");
const canvas = document.querySelector("#priceChart");
const analysisTab = document.querySelector("#analysisTab");
const etfTab = document.querySelector("#etfTab");
const fundTab = document.querySelector("#fundTab");
const monitorTab = document.querySelector("#monitorTab");
const tradeTab = document.querySelector("#tradeTab");
const searchLogTab = document.querySelector("#searchLogTab");
const analysisView = document.querySelector("#analysisView");
const etfView = document.querySelector("#etfView");
const fundView = document.querySelector("#fundView");
const monitorView = document.querySelector("#monitorView");
const tradeView = document.querySelector("#tradeView");
const searchLogView = document.querySelector("#searchLogView");
const refreshMonitor = document.querySelector("#refreshMonitor");
const monitorLoading = document.querySelector("#monitorLoading");
const monitorError = document.querySelector("#monitorError");
const monitorContent = document.querySelector("#monitorContent");
const refreshSearchLogs = document.querySelector("#refreshSearchLogs");
const searchLogLoading = document.querySelector("#searchLogLoading");
const searchLogError = document.querySelector("#searchLogError");
const searchLogContent = document.querySelector("#searchLogContent");
const searchLogRows = document.querySelector("#searchLogRows");
const tradeForm = document.querySelector("#tradeForm");
const tradeRows = document.querySelector("#tradeRows");
const tradeLoading = document.querySelector("#tradeLoading");
const tradeError = document.querySelector("#tradeError");
const useCurrentReport = document.querySelector("#useCurrentReport");
const chartSupportToggle = document.querySelector("#chartSupportToggle");
const chartResistanceToggle = document.querySelector("#chartResistanceToggle");
const assetContexts = {
  etf: {
    tab: etfTab,
    view: etfView,
    form: document.querySelector("#etfForm"),
    input: document.querySelector("#etfInput"),
    suggestions: document.querySelector("#etfSuggestions"),
    type: "etf",
    prefix: "etf"
  },
  fund: {
    tab: fundTab,
    view: fundView,
    form: document.querySelector("#fundForm"),
    input: document.querySelector("#fundInput"),
    suggestions: document.querySelector("#fundSuggestions"),
    type: "mutual-fund",
    prefix: "fund"
  }
};

let latestReport = null;
let latestMonitor = null;
let latestTradeReferences = null;
let latestSearchLogs = null;
let latestAssetReports = { etf: null, fund: null };
let searchTimer = null;
let assetSearchTimers = { etf: null, fund: null };
let suggestionDialogEl = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbol = symbolInput.value.trim();
  if (!symbol) {
    return;
  }
  setActiveTab("analysis");
  await analyze(symbol);
});

analysisTab.addEventListener("click", () => setActiveTab("analysis"));
etfTab.addEventListener("click", () => setActiveTab("etf"));
fundTab.addEventListener("click", () => setActiveTab("fund"));
monitorTab.addEventListener("click", () => {
  setActiveTab("monitor");
  if (!latestMonitor) {
    loadMarketMonitor(false);
  }
});
tradeTab.addEventListener("click", () => {
  setActiveTab("trade");
  if (!latestTradeReferences) {
    loadTradeReferences();
  }
});
searchLogTab.addEventListener("click", () => {
  setActiveTab("logs");
  if (!latestSearchLogs) {
    loadSearchLogs();
  }
});
refreshMonitor.addEventListener("click", () => loadMarketMonitor(true));
refreshSearchLogs.addEventListener("click", loadSearchLogs);
useCurrentReport.addEventListener("click", prefillTradeFromReport);
chartSupportToggle.addEventListener("change", redrawChart);
chartResistanceToggle.addEventListener("change", redrawChart);
canvas.addEventListener("click", confirmTradingViewRedirect);
canvas.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  confirmTradingViewRedirect();
});

tradeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveTradeReference();
});

tradeRows.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-trade]");
  if (!button) {
    return;
  }
  await deleteTradeReference(button.dataset.deleteTrade);
});

for (const context of Object.values(assetContexts)) {
  context.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const symbol = context.input.value.trim();
    if (!symbol) {
      return;
    }
    setActiveTab(context.prefix);
    await analyzeAsset(context, symbol);
  });

  context.input.addEventListener("input", () => {
    clearTimeout(assetSearchTimers[context.prefix]);
    const query = context.input.value.trim();
    if (query.length < 1) {
      context.suggestions.innerHTML = "";
      return;
    }
    assetSearchTimers[context.prefix] = setTimeout(() => searchAssets(context, query), 180);
  });
}

symbolInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = symbolInput.value.trim();
  if (query.length < 1) {
    suggestions.innerHTML = "";
    return;
  }
  searchTimer = setTimeout(() => searchSymbols(query), 180);
});

window.addEventListener("resize", () => {
  if (latestReport && !analysisView.classList.contains("is-hidden")) {
    redrawChart();
  }
});

function setActiveTab(tab) {
  const isEtf = tab === "etf";
  const isFund = tab === "fund";
  const isMonitor = tab === "monitor";
  const isTrade = tab === "trade";
  const isLogs = tab === "logs";
  const isAnalysis = !isEtf && !isFund && !isMonitor && !isTrade && !isLogs;
  analysisTab.classList.toggle("is-active", isAnalysis);
  etfTab.classList.toggle("is-active", isEtf);
  fundTab.classList.toggle("is-active", isFund);
  monitorTab.classList.toggle("is-active", isMonitor);
  tradeTab.classList.toggle("is-active", isTrade);
  searchLogTab.classList.toggle("is-active", isLogs);
  analysisView.classList.toggle("is-hidden", !isAnalysis);
  etfView.classList.toggle("is-hidden", !isEtf);
  fundView.classList.toggle("is-hidden", !isFund);
  monitorView.classList.toggle("is-hidden", !isMonitor);
  tradeView.classList.toggle("is-hidden", !isTrade);
  searchLogView.classList.toggle("is-hidden", !isLogs);
  if (isAnalysis && latestReport) {
    redrawChart();
  }
}

function showAssetState(prefix, state) {
  document.querySelector(`#${prefix}Empty`).classList.toggle("is-hidden", state !== "empty");
  document.querySelector(`#${prefix}Loading`).classList.toggle("is-hidden", state !== "loading");
  document.querySelector(`#${prefix}Error`).classList.toggle("is-hidden", state !== "error");
  document.querySelector(`#${prefix}Report`).classList.toggle("is-hidden", state !== "report");
}

async function analyze(symbol) {
  showState("loading");
  try {
    const response = await fetch(`/api/analyze?symbol=${encodeURIComponent(symbol)}`);
    const payload = await response.json();
    if (!response.ok) {
      if (payload.suggestions) {
        showInvalidSearchPopup(payload, symbol);
        showState(latestReport ? "report" : "empty");
        return;
      }
      throw new Error(payload.error || "Could not build report.");
    }
    latestReport = payload;
    showState("report");
    renderReport(payload);
  } catch (error) {
    errorState.textContent = error.message;
    showState("error");
  } finally {
    latestSearchLogs = null;
  }
}

async function analyzeAsset(context, symbol) {
  showAssetState(context.prefix, "loading");
  try {
    const response = await fetch(`/api/analyze-asset?type=${encodeURIComponent(context.type)}&symbol=${encodeURIComponent(symbol)}`);
    const payload = await response.json();
    if (!response.ok) {
      if (payload.suggestions) {
        showInvalidSearchPopup(payload, symbol);
        showAssetState(context.prefix, latestAssetReports[context.prefix] ? "report" : "empty");
        return;
      }
      throw new Error(payload.error || "Could not build fund report.");
    }
    latestAssetReports[context.prefix] = payload;
    showAssetState(context.prefix, "report");
    renderAssetReport(context.prefix, payload);
  } catch (error) {
    document.querySelector(`#${context.prefix}Error`).textContent = error.message;
    showAssetState(context.prefix, "error");
  } finally {
    latestSearchLogs = null;
  }
}

async function searchSymbols(query) {
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const payload = await response.json();
    suggestions.innerHTML = "";
    for (const item of payload.results || []) {
      const option = document.createElement("option");
      option.value = item.symbol;
      option.label = `${item.name} ${item.exchange ? "- " + item.exchange : ""}`;
      suggestions.appendChild(option);
    }
  } catch {
    suggestions.innerHTML = "";
  }
}

async function searchAssets(context, query) {
  try {
    const response = await fetch(`/api/search-assets?type=${encodeURIComponent(context.type)}&q=${encodeURIComponent(query)}`);
    const payload = await response.json();
    context.suggestions.innerHTML = "";
    for (const item of payload.results || []) {
      const option = document.createElement("option");
      option.value = item.symbol;
      option.label = `${item.name} ${item.exchange ? "- " + item.exchange : ""}`;
      context.suggestions.appendChild(option);
    }
  } catch {
    context.suggestions.innerHTML = "";
  }
}

function showInvalidSearchPopup(payload, query) {
  const dialog = ensureSuggestionDialog();
  const message = dialog.querySelector("#suggestionDialogMessage");
  const typed = dialog.querySelector("#suggestionDialogTyped");
  const list = dialog.querySelector("#suggestionDialogList");
  const suggestionGroups = normalizeSuggestionGroups(payload.suggestions || {});

  message.textContent = payload.error || "Invalid stock/MF name, do you mean anything from below?";
  typed.textContent = query ? `Typed: ${query}` : "";
  list.innerHTML = "";

  if (!suggestionGroups.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No close stock, ETF, or mutual fund matches were found. Try a shorter company, ticker, or scheme name.";
    list.appendChild(empty);
  }

  for (const group of suggestionGroups) {
    const section = document.createElement("section");
    section.className = "suggestion-group";
    const title = document.createElement("h4");
    title.textContent = group.title;
    section.appendChild(title);

    for (const item of group.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggestion-option";
      button.dataset.suggestionKind = item.kind;
      button.dataset.suggestionSymbol = item.symbol;
      button.innerHTML = `
        <strong>${escapeHtml(item.symbol || "n/a")}</strong>
        <span>${escapeHtml(item.name || item.symbol || "n/a")}</span>
        <em>${escapeHtml([item.exchange, item.type || item.label].filter(Boolean).join(" · "))}</em>
      `;
      section.appendChild(button);
    }
    list.appendChild(section);
  }

  dialog.classList.remove("is-hidden");
  document.body.classList.add("dialog-open");
  const firstOption = dialog.querySelector(".suggestion-option");
  (firstOption || dialog.querySelector("#suggestionDialogClose")).focus();
}

function ensureSuggestionDialog() {
  if (suggestionDialogEl) {
    return suggestionDialogEl;
  }

  suggestionDialogEl = document.createElement("section");
  suggestionDialogEl.id = "suggestionDialog";
  suggestionDialogEl.className = "suggestion-dialog is-hidden";
  suggestionDialogEl.setAttribute("role", "dialog");
  suggestionDialogEl.setAttribute("aria-modal", "true");
  suggestionDialogEl.setAttribute("aria-labelledby", "suggestionDialogTitle");
  suggestionDialogEl.innerHTML = `
    <article class="suggestion-modal">
      <div class="suggestion-modal-head">
        <div>
          <p class="eyebrow">Search Suggestions</p>
          <h3 id="suggestionDialogTitle">Invalid stock/MF name</h3>
        </div>
        <button type="button" id="suggestionDialogClose" class="suggestion-close" aria-label="Close suggestions">Close</button>
      </div>
      <p id="suggestionDialogMessage" class="suggestion-message"></p>
      <p id="suggestionDialogTyped" class="suggestion-typed"></p>
      <div id="suggestionDialogList" class="suggestion-list"></div>
    </article>
  `;
  document.body.appendChild(suggestionDialogEl);

  suggestionDialogEl.querySelector("#suggestionDialogClose").addEventListener("click", closeSuggestionDialog);
  suggestionDialogEl.addEventListener("click", (event) => {
    if (event.target === suggestionDialogEl) {
      closeSuggestionDialog();
      return;
    }

    const option = event.target.closest("[data-suggestion-symbol]");
    if (!option) {
      return;
    }
    const symbol = option.dataset.suggestionSymbol || "";
    const kind = option.dataset.suggestionKind || "stock";
    closeSuggestionDialog();
    useSuggestedInstrument(symbol, kind);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !suggestionDialogEl.classList.contains("is-hidden")) {
      closeSuggestionDialog();
    }
  });

  return suggestionDialogEl;
}

function closeSuggestionDialog() {
  if (!suggestionDialogEl) {
    return;
  }
  suggestionDialogEl.classList.add("is-hidden");
  document.body.classList.remove("dialog-open");
}

function useSuggestedInstrument(symbol, kind) {
  if (!symbol) {
    return;
  }
  if (kind === "mutual-fund") {
    const context = assetContexts.fund;
    context.input.value = symbol;
    setActiveTab("fund");
    analyzeAsset(context, symbol);
    return;
  }
  if (kind === "etf") {
    const context = assetContexts.etf;
    context.input.value = symbol;
    setActiveTab("etf");
    analyzeAsset(context, symbol);
    return;
  }
  symbolInput.value = symbol;
  setActiveTab("analysis");
  analyze(symbol);
}

function normalizeSuggestionGroups(suggestionsPayload) {
  const groups = [
    ["stocks", "Stocks"],
    ["etfs", "ETFs"],
    ["mutualFunds", "Mutual Funds"],
  ];
  return groups
    .map(([key, title]) => ({
      title,
      items: Array.isArray(suggestionsPayload[key]) ? suggestionsPayload[key] : []
    }))
    .filter((group) => group.items.length);
}

async function loadMarketMonitor(forceRefresh) {
  monitorLoading.classList.remove("is-hidden");
  monitorError.classList.add("is-hidden");
  monitorContent.classList.add("is-hidden");

  try {
    const suffix = forceRefresh ? "?refresh=1" : "";
    const response = await fetch(`/api/market-monitor${suffix}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load market monitor.");
    }
    latestMonitor = payload;
    renderMarketMonitor(payload);
    monitorContent.classList.remove("is-hidden");
  } catch (error) {
    monitorError.textContent = error.message;
    monitorError.classList.remove("is-hidden");
  } finally {
    monitorLoading.classList.add("is-hidden");
  }
}

async function loadTradeReferences() {
  tradeLoading.classList.remove("is-hidden");
  tradeError.classList.add("is-hidden");

  try {
    const response = await fetch("/api/trade-references");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load trade references.");
    }
    latestTradeReferences = payload.results || [];
    renderTradeReferences(latestTradeReferences);
  } catch (error) {
    showTradeError(error.message);
  } finally {
    tradeLoading.classList.add("is-hidden");
  }
}

async function loadSearchLogs() {
  searchLogLoading.classList.remove("is-hidden");
  searchLogError.classList.add("is-hidden");
  searchLogContent.classList.add("is-hidden");

  try {
    const response = await fetch("/api/search-logs?limit=100");
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load search logs.");
    }
    latestSearchLogs = payload.results || [];
    renderSearchLogs(latestSearchLogs, payload.count || 0);
    searchLogContent.classList.remove("is-hidden");
  } catch (error) {
    searchLogError.textContent = error.message;
    searchLogError.classList.remove("is-hidden");
  } finally {
    searchLogLoading.classList.add("is-hidden");
  }
}

async function saveTradeReference() {
  tradeError.classList.add("is-hidden");

  try {
    const payload = {
      symbol: document.querySelector("#tradeSymbol").value.trim(),
      stockName: document.querySelector("#tradeStockName").value.trim(),
      buyPrice: document.querySelector("#tradeBuyPrice").value,
      sellPrice: document.querySelector("#tradeSellPrice").value,
      stopLoss: document.querySelector("#tradeStopLoss").value || null,
      status: document.querySelector("#tradeStatus").value,
      note: document.querySelector("#tradeNote").value.trim()
    };
    const response = await fetch("/api/trade-references", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not save trade reference.");
    }
    tradeForm.reset();
    await loadTradeReferences();
  } catch (error) {
    showTradeError(error.message);
  }
}

async function deleteTradeReference(id) {
  tradeError.classList.add("is-hidden");

  try {
    const response = await fetch(`/api/trade-references/${encodeURIComponent(id)}`, { method: "DELETE" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not delete trade reference.");
    }
    await loadTradeReferences();
  } catch (error) {
    showTradeError(error.message);
  }
}

function prefillTradeFromReport() {
  if (!latestReport) {
    showTradeError("Load a stock report first, then use current report.");
    return;
  }

  const shortPlan = latestReport.swingTradePlan?.plans?.[0];
  document.querySelector("#tradeSymbol").value = latestReport.symbol || "";
  document.querySelector("#tradeStockName").value = latestReport.longName || "";
  document.querySelector("#tradeBuyPrice").value = shortPlan?.entry?.high || latestReport.researchLevels?.pullbackEntry?.high || latestReport.quote?.price || "";
  document.querySelector("#tradeSellPrice").value = shortPlan?.targets?.[0]?.price || latestReport.researchLevels?.targets?.[0] || "";
  document.querySelector("#tradeStopLoss").value = shortPlan?.stopLoss || latestReport.researchLevels?.invalidation || "";
  document.querySelector("#tradeStatus").value = "watch";
  document.querySelector("#tradeNote").value = [
    shortPlan ? `${shortPlan.horizon} ${shortPlan.timeframe}: ${shortPlan.setup}` : "",
    latestReport.swingTradePlan?.suitability?.label,
    latestReport.researchLevels?.mode,
    latestReport.outlook?.label,
    latestReport.events?.risk?.label ? `Event risk: ${latestReport.events.risk.label}` : ""
  ].filter(Boolean).join(" | ");
  tradeError.classList.add("is-hidden");
}

function renderTradeReferences(items) {
  tradeRows.innerHTML = "";
  document.querySelector("#tradeCount").textContent = `${items.length} saved`;

  if (!items.length) {
    tradeRows.innerHTML = "<tr><td colspan=\"7\">No trade references saved yet.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.stockName || item.symbol)}</strong><span>${escapeHtml(item.symbol)}</span>${item.note ? `<small>${escapeHtml(item.note)}</small>` : ""}</td>
      <td>${formatMoney(item.buyPrice, "INR")}</td>
      <td>${formatMoney(item.sellPrice, "INR")}</td>
      <td><span class="${changeClass(item.expectedReturnPercent)}">${formatPercentValue(item.expectedReturnPercent)}</span></td>
      <td>${formatMoney(item.stopLoss, "INR")}<span>Risk ${formatPercentValue(item.riskPercent)}</span></td>
      <td><span class="status-pill status-${escapeHtml(item.status)}">${escapeHtml(statusText(item.status))}</span></td>
      <td><button class="link-button" type="button" data-delete-trade="${item.id}">Delete</button></td>
    `;
    tradeRows.appendChild(row);
  }
}

function renderSearchLogs(items, totalCount) {
  searchLogRows.innerHTML = "";
  document.querySelector("#searchLogSummary").textContent = `${items.length} recent searches shown${totalCount > items.length ? ` of ${totalCount}` : ""}.`;

  if (!items.length) {
    searchLogRows.innerHTML = "<tr><td colspan=\"5\">No stock searches recorded yet.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    const status = item.success ? "Success" : `Failed ${item.statusCode || ""}`.trim();
    row.innerHTML = `
      <td>${formatDateTime(item.createdAt)}</td>
      <td><strong>${escapeHtml(item.symbol || item.rawInput || "n/a")}</strong><span>Input: ${escapeHtml(item.rawInput || "n/a")}</span></td>
      <td>${escapeHtml(item.ipAddress || "n/a")}</td>
      <td><strong>${escapeHtml(item.deviceLabel || item.deviceType || "Unknown device")}</strong><small>${escapeHtml(shortUserAgent(item.userAgent))}</small></td>
      <td><span class="status-pill ${item.success ? "status-success" : "status-failed"}">${escapeHtml(status)}</span>${item.errorMessage ? `<small>${escapeHtml(item.errorMessage)}</small>` : ""}</td>
    `;
    searchLogRows.appendChild(row);
  }
}

function shortUserAgent(userAgent) {
  if (!userAgent) {
    return "No user agent";
  }
  return userAgent.length > 140 ? `${userAgent.slice(0, 140)}...` : userAgent;
}

function showTradeError(message) {
  tradeError.textContent = message;
  tradeError.classList.remove("is-hidden");
}

function showState(state) {
  emptyState.classList.toggle("is-hidden", state !== "empty");
  loadingState.classList.toggle("is-hidden", state !== "loading");
  errorState.classList.toggle("is-hidden", state !== "error");
  reportEl.classList.toggle("is-hidden", state !== "report");
}

function renderReport(report) {
  const currency = report.currency;
  document.querySelector("#exchangeText").textContent = [report.symbol, report.quote.exchange].filter(Boolean).join(" - ");
  document.querySelector("#stockName").textContent = report.longName;
  document.querySelector("#generatedText").textContent = `Generated ${formatDateTime(report.generatedAt)} from ${report.source}`;
  document.querySelector("#currentPrice").textContent = formatMoney(report.quote.price, currency);

  const change = document.querySelector("#priceChange");
  change.textContent = `${signed(report.quote.change)} (${signed(report.quote.changePercent)}%)`;
  change.className = report.quote.change >= 0 ? "positive" : "negative";

  document.querySelector("#overallScore").textContent = scoreText(report.scores.overall);
  document.querySelector("#technicalScore").textContent = scoreText(report.scores.technical);
  document.querySelector("#fundamentalScore").textContent = scoreText(report.scores.fundamental);
  document.querySelector("#eventRiskScore").textContent = scoreText(report.scores.eventRisk);
  document.querySelector("#confidenceScore").textContent = scoreText(report.scores.confidence);
  document.querySelector("#outlookLabel").textContent = report.outlook.label;
  document.querySelector("#technicalSummary").textContent = report.technical.summary;
  document.querySelector("#fundamentalSummary").textContent = report.fundamentals.summary;
  document.querySelector("#eventRiskSummary").textContent = report.events.risk.label;
  document.querySelector("#confidenceSummary").textContent = report.quality.label;

  renderOwnershipSnapshot(report.growthDrivers, report.fundamentals.metrics);
  renderLevels(report.researchLevels, currency, report.technical.levels);
  renderTechnical(report.technical, currency);
  renderFundamentals(report.fundamentals.metrics, report.fundamentals.signals, currency);
  renderGrowthDrivers(report.growthDrivers, currency);
  renderQuality(report.quality);
  renderScenarios(report.scenarios, currency);
  renderSwingTradePlan(report.swingTradePlan, currency);
  renderReferences(report.references);
  redrawChart();
}

function renderAssetReport(prefix, report) {
  const currency = report.currency;
  document.querySelector(`#${prefix}Exchange`).textContent = [report.symbol, report.quote.exchange || report.assetLabel].filter(Boolean).join(" - ");
  document.querySelector(`#${prefix}Name`).textContent = report.longName;
  document.querySelector(`#${prefix}Generated`).textContent = `Generated ${formatDateTime(report.generatedAt)} from ${report.source}`;
  document.querySelector(`#${prefix}Price`).textContent = formatMoney(report.quote.price, currency);

  const change = document.querySelector(`#${prefix}Change`);
  change.textContent = `${signed(report.quote.change)} (${signed(report.quote.changePercent)}%)`;
  change.className = report.quote.change >= 0 ? "positive" : "negative";

  document.querySelector(`#${prefix}Suitability`).textContent = scoreText(report.scores.suitability);
  document.querySelector(`#${prefix}Summary`).textContent = report.summary;
  document.querySelector(`#${prefix}Momentum`).textContent = scoreText(report.scores.momentum);
  document.querySelector(`#${prefix}MomentumText`).textContent = report.momentum.summary;
  document.querySelector(`#${prefix}Risk`).textContent = scoreText(report.scores.risk);
  document.querySelector(`#${prefix}RiskText`).textContent = report.risk.summary;
  document.querySelector(`#${prefix}Confidence`).textContent = scoreText(report.scores.confidence);
  document.querySelector(`#${prefix}ConfidenceText`).textContent = report.confidence.label;

  renderAssetProfile(prefix, report);
  renderAssetPerformance(prefix, report);
  renderAssetPlan(prefix, report.plan);
  renderAssetHoldings(prefix, report.holdings);
  renderList(`#${prefix}Checks`, [
    ...(report.confidence.checks || []),
    `Quote type: ${report.quote.quoteType || "n/a"}`,
    `Data source: ${report.source || "n/a"}`
  ]);
  renderAssetReferences(`#${prefix}ReferenceLinks`, report.references);
}

function renderAssetProfile(prefix, report) {
  const profile = report.profile || {};
  renderTable(`#${prefix}ProfileMetrics`, [
    ["Category", profile.category || "n/a"],
    ["Family", profile.family || "n/a"],
    ["Type", profile.legalType || report.assetLabel || "n/a"],
    ["Total assets / AUM", formatLarge(profile.totalAssets, report.currency)],
    ["NAV / price", formatMoney(profile.navPrice, report.currency)],
    ["Expense ratio", formatExpenseRatio(profile.expenseRatio)],
    ["Yield", formatRatioPercent(profile.yield)],
    ["YTD return", formatRatioPercent(profile.ytdReturn)],
    ["3Y beta", formatNumber(profile.beta3Year)],
    ["Inception", profile.inceptionDate ? formatDateTime(profile.inceptionDate) : "n/a"]
  ]);
}

function renderAssetPerformance(prefix, report) {
  const rows = (report.performance.rows || []).map((item) => [`${item.label} return`, formatPercentValue(item.return)]);
  rows.push(
    ["Annualized volatility", formatPercentValue(report.risk.annualizedVolatility)],
    ["Max drawdown", formatPercentValue(report.risk.maxDrawdown)],
    ["SMA 50", formatMoney(report.momentum.sma50, report.currency)],
    ["SMA 200", formatMoney(report.momentum.sma200, report.currency)]
  );
  renderTable(`#${prefix}PerformanceMetrics`, rows);
}

function renderAssetPlan(prefix, plan) {
  const container = document.querySelector(`#${prefix}PlanList`);
  container.innerHTML = "";
  if (!plan || !Array.isArray(plan.items) || !plan.items.length) {
    container.innerHTML = "<p class=\"muted\">Allocation plan was not available.</p>";
    return;
  }

  for (const item of plan.items) {
    const row = document.createElement("div");
    row.className = "asset-plan-item";
    row.innerHTML = `
      <span>${escapeHtml(item.label || "Check")}</span>
      <strong>${escapeHtml(item.value || "n/a")}</strong>
      <p>${escapeHtml(item.detail || "")}</p>
    `;
    container.appendChild(row);
  }
}

function renderAssetHoldings(prefix, holdings) {
  const container = document.querySelector(`#${prefix}HoldingList`);
  const top = holdings?.top || [];
  const sectors = holdings?.sectors || [];
  container.innerHTML = "";

  if (!top.length && !sectors.length) {
    container.innerHTML = "<p class=\"muted\">Holdings or sector weightings were not available from the data provider.</p>";
    return;
  }

  if (top.length) {
    const group = document.createElement("div");
    group.className = "asset-holding-group";
    group.innerHTML = "<h4>Top Holdings</h4>";
    for (const item of top) {
      const row = document.createElement("div");
      row.className = "asset-holding-row";
      row.innerHTML = `
        <span>${escapeHtml(item.symbol || "")}</span>
        <strong>${escapeHtml(item.name || "n/a")}</strong>
        <em>${formatRatioPercent(item.percent)}</em>
      `;
      group.appendChild(row);
    }
    container.appendChild(group);
  }

  if (sectors.length) {
    const group = document.createElement("div");
    group.className = "asset-holding-group";
    group.innerHTML = "<h4>Sector Weight</h4>";
    for (const item of sectors) {
      const row = document.createElement("div");
      row.className = "asset-holding-row";
      row.innerHTML = `
        <span></span>
        <strong>${escapeHtml(item.name || "n/a")}</strong>
        <em>${formatRatioPercent(item.percent)}</em>
      `;
      group.appendChild(row);
    }
    container.appendChild(group);
  }
}

function renderAssetReferences(selector, references) {
  const container = document.querySelector(selector);
  container.innerHTML = "";
  if (!references || !Array.isArray(references.links) || !references.links.length) {
    container.innerHTML = "<p class=\"muted\">No external reference links were generated.</p>";
    return;
  }

  for (const link of references.links) {
    const anchor = document.createElement("a");
    anchor.className = "reference-link";
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.innerHTML = `
      <strong>${escapeHtml(link.label || "Reference")}</strong>
      <span>${escapeHtml(link.note || "")}</span>
    `;
    container.appendChild(anchor);
  }
}

function confirmTradingViewRedirect() {
  if (!latestReport) {
    return;
  }

  const chartUrl = tradingViewChartUrl(latestReport);
  if (!chartUrl) {
    window.alert("TradingView chart is not available for this symbol.");
    return;
  }

  const symbol = latestReport.references?.tradingViewSymbol || latestReport.symbol || "this stock";
  const shouldOpen = window.confirm(`Open the candlestick chart for ${symbol} on TradingView?`);
  if (shouldOpen) {
    window.location.href = chartUrl;
  }
}

function tradingViewChartUrl(report) {
  const chartLink = (report.references?.links || []).find((link) => link.label === "TradingView chart");
  if (chartLink?.url) {
    return chartLink.url;
  }
  if (report.references?.tradingViewSymbol) {
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(report.references.tradingViewSymbol)}`;
  }
  return "";
}

function renderMarketMonitor(data) {
  document.querySelector("#monitorGenerated").textContent = `Generated ${formatDateTime(data.generatedAt)} from ${data.source}`;
  document.querySelector("#monitorNote").textContent = data.note;
  document.querySelector("#scanCount").textContent = `${data.scannedCount} scanned`;
  document.querySelector("#highVolumeCount").textContent = `${data.activityScannedCount || 0} scanned`;
  document.querySelector("#catalystCount").textContent = `${(data.orderCatalysts || []).length} found`;
  renderNseSnapshot(data.nseSnapshot || {});
  renderMoneycontrolSectorAnalysis(data.moneycontrolSectorAnalysis || {});
  renderCommodities(data.commodities || []);
  renderBreakoutRows(data.breakoutCandidates || []);
  renderHighVolumeRows(data.highVolumeCandidates || []);
  renderOrderCatalystRows(data.orderCatalysts || []);
  setupMonitorCollapsibles();
}

function setupMonitorCollapsibles() {
  document.querySelectorAll("#monitorContent .analysis-panel").forEach((panel, index) => {
    if (panel.dataset.collapsibleReady === "1") {
      return;
    }
    const header = panel.querySelector(":scope > .section-head");
    if (!header) {
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "collapse-toggle";
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-label", "Collapse section");
    button.title = "Collapse section";
    button.textContent = "−";
    button.addEventListener("click", () => {
      const collapsed = panel.classList.toggle("is-collapsed");
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      button.setAttribute("aria-label", collapsed ? "Expand section" : "Collapse section");
      button.title = collapsed ? "Expand section" : "Collapse section";
      button.textContent = collapsed ? "+" : "−";
    });

    header.appendChild(button);
    panel.dataset.collapsibleReady = "1";
    panel.dataset.collapsibleIndex = String(index);
  });
}

function renderNseSnapshot(snapshot) {
  const statusGrid = document.querySelector("#nseStatusGrid");
  const breadthGrid = document.querySelector("#nseBreadthGrid");
  const indexGrid = document.querySelector("#nseIndexGrid");
  if (!statusGrid || !breadthGrid || !indexGrid) {
    return;
  }

  statusGrid.innerHTML = "";
  breadthGrid.innerHTML = "";
  indexGrid.innerHTML = "";

  if (!snapshot.available) {
    setText("#nseSnapshotStatus", "Unavailable");
    statusGrid.innerHTML = `<article class="market-mini-card"><span>NSE snapshot</span><strong>Unavailable</strong><small>${escapeHtml(snapshot.error || "NSE did not return market data.")}</small></article>`;
    emptyTable("#nseGainerRows", 5, "NSE gainers are unavailable.");
    emptyTable("#nseLoserRows", 5, "NSE losers are unavailable.");
    emptyTable("#nseActiveRows", 5, "NSE most-active data is unavailable.");
    emptyTable("#nseHighRows", 5, "NSE 52-week high and price-band data is unavailable.");
    return;
  }

  const capitalMarket = (snapshot.marketStatus || []).find((item) => item.market === "Capital Market") || {};
  setText("#nseSnapshotStatus", `${capitalMarket.message || capitalMarket.status || "NSE"}${snapshot.timestamp ? " · " + snapshot.timestamp : ""}`);

  for (const item of (snapshot.marketStatus || []).slice(0, 6)) {
    const card = document.createElement("article");
    card.className = "market-mini-card";
    card.innerHTML = `
      <span>${escapeHtml(item.market || "Market")}</span>
      <strong>${escapeHtml(item.status || "n/a")}</strong>
      <small>${escapeHtml(item.message || item.tradeDate || "")}</small>
      ${Number.isFinite(item.last) ? `<small>${escapeHtml(item.index || "")} ${formatNumber(item.last)} <em class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</em></small>` : ""}
    `;
    statusGrid.appendChild(card);
  }

  const breadth = snapshot.breadth || {};
  const marketCap = snapshot.marketCap || {};
  const gift = snapshot.giftNifty || {};
  const breadthCards = [
    ["Advances", formatNumber(breadth.advances), `${formatPercentValue(breadth.advancePercent)} of NSE universe`],
    ["Declines", formatNumber(breadth.declines), `${formatPercentValue(breadth.declinePercent)} of NSE universe`],
    ["A/D Ratio", formatNumber(breadth.advanceDeclineRatio), `${formatNumber(breadth.total)} securities tracked`],
    ["Market Cap", marketCap.lakhCroreRupees ? `${formatNumber(marketCap.lakhCroreRupees)} lakh cr` : "n/a", marketCap.timestamp || ""],
    ["GIFT Nifty", formatNumber(gift.last), `${signed(gift.change)} (${signed(gift.changePercent)}%)`],
  ];
  for (const [label, value, detail] of breadthCards) {
    const card = document.createElement("article");
    card.className = "market-mini-card";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail || "")}</small>`;
    breadthGrid.appendChild(card);
  }

  for (const item of snapshot.indices || []) {
    const card = document.createElement("article");
    card.className = "nse-index-card";
    card.innerHTML = `
      <div>
        <span>${escapeHtml(item.name || "Index")}</span>
        <strong>${formatNumber(item.last)}</strong>
      </div>
      <em class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</em>
      <small>H/L ${formatNumber(item.high)} / ${formatNumber(item.low)} · PE ${formatNumber(item.pe)} · A/D ${formatNumber(item.advances)}/${formatNumber(item.declines)}</small>
    `;
    indexGrid.appendChild(card);
  }

  setText("#nseGainerCount", `${(snapshot.topGainers || []).length} shown`);
  setText("#nseLoserCount", `${(snapshot.topLosers || []).length} shown`);
  setText("#nseActiveCount", `${(snapshot.mostActive || []).length} shown`);
  renderNseMoverRows("#nseGainerRows", snapshot.topGainers || [], "No NSE gainers were returned.");
  renderNseMoverRows("#nseLoserRows", snapshot.topLosers || [], "No NSE losers were returned.");
  renderNseActiveRows(snapshot.mostActive || []);
  renderNseHighAndBandRows(snapshot.weekHighs || [], snapshot.priceBands || {});
}

function renderMoneycontrolSectorAnalysis(snapshot) {
  const summary = document.querySelector("#moneycontrolSectorSummary");
  if (!summary) {
    return;
  }
  summary.innerHTML = "";

  if (!snapshot.available) {
    setText("#moneycontrolSectorStatus", "Unavailable");
    summary.innerHTML = `<article class="market-mini-card"><span>Moneycontrol</span><strong>Unavailable</strong><small>${escapeHtml(snapshot.error || "Sector analysis could not be loaded.")}</small></article>`;
    emptyTable("#moneycontrolSectorRows", 8, "Moneycontrol sector analysis is unavailable.");
    emptyTable("#moneycontrolIndexRows", 4, "Moneycontrol sectoral indices are unavailable.");
    return;
  }

  const sectors = summarizedMoneycontrolSectors(snapshot);
  const indices = snapshot.sectorIndices || [];
  const breadth = snapshot.breadth || {};
  setText("#moneycontrolSectorStatus", `${sectors.length} signals`);
  setText("#moneycontrolIndexStatus", `${indices.length} indices`);

  const summaryCards = [
    ["Stocks", formatNumber(breadth.stocks), "Moneycontrol sector universe"],
    ["Sector Breadth", `${formatNumber(breadth.advance)}/${formatNumber(breadth.decline)}`, `A/D ${formatNumber(breadth.advanceDeclineRatio)}`],
    ["Bullish Sectors", formatNumber(breadth.bullishSectors), `${formatNumber(breadth.bearishSectors)} bearish`],
    ["Source", "Moneycontrol", snapshot.url || ""],
  ];
  for (const [label, value, detail] of summaryCards) {
    const card = document.createElement("article");
    card.className = "market-mini-card";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail || "")}</small>`;
    summary.appendChild(card);
  }

  const sectorRows = document.querySelector("#moneycontrolSectorRows");
  sectorRows.innerHTML = "";
  if (!sectors.length) {
    emptyTable("#moneycontrolSectorRows", 8, "No sector rows were returned by Moneycontrol.");
  } else {
    for (const item of sectors) {
      const row = document.createElement("tr");
      const title = item.url
        ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.sector)}</a>`
        : escapeHtml(item.sector);
      row.innerHTML = `
        <td><span class="sector-signal ${item.signalType === "weak" ? "is-weak" : "is-strong"}">${escapeHtml(item.signal || "Tracked")}</span></td>
        <td><strong>${title}</strong><span>${formatNumber(item.stocks)} stocks · ${formatNumber(item.industries)} industries</span></td>
        <td>${escapeHtml(item.trend || "n/a")}<span>${escapeHtml(item.summary || "")}</span></td>
        <td class="${changeClass(item.marketCapChangePercent)}">${signed(item.marketCapChangePercent)}%<span>${formatLarge(item.marketCapCrore, "")} cr</span></td>
        <td>${formatNumber(item.advance)} / ${formatNumber(item.decline)}<span>${formatPercentValue(item.advancePercent)} advancing</span></td>
        <td>${formatNumber(item.sectorPe)}</td>
        <td class="${changeClass(item.earningsYoyChange)}">${signed(item.earningsYoyChange)}%<span>${formatLarge(item.earningsYoyCrore, "")} cr</span></td>
        <td><strong>${scoreText(item.score || 0)}</strong></td>
      `;
      sectorRows.appendChild(row);
    }
  }

  const indexRows = document.querySelector("#moneycontrolIndexRows");
  indexRows.innerHTML = "";
  if (!indices.length) {
    emptyTable("#moneycontrolIndexRows", 4, "No sectoral index rows were returned by Moneycontrol.");
  } else {
    for (const item of indices) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><strong>${escapeHtml(item.name || "Index")}</strong><span>${escapeHtml(item.lastUpdated || "")}</span></td>
        <td>${formatNumber(item.price)}</td>
        <td class="${changeClass(item.changePercent)}">${signed(item.change)}<span>${signed(item.changePercent)}%</span></td>
        <td>${formatNumber(item.advance)} / ${formatNumber(item.decline)}</td>
      `;
      indexRows.appendChild(row);
    }
  }
}

function summarizedMoneycontrolSectors(snapshot) {
  const rows = [];
  const seen = new Set();
  const addRows = (items, signal, signalType) => {
    for (const item of items || []) {
      if (!item.sector || seen.has(item.sector)) {
        continue;
      }
      seen.add(item.sector);
      rows.push({ ...item, signal, signalType });
    }
  };

  addRows(snapshot.topPerforming || [], "Leader", "strong");
  addRows(snapshot.underPerforming || [], "Weak", "weak");
  if (!rows.length) {
    addRows((snapshot.sectors || []).slice(0, 10), "Tracked", "neutral");
  }
  return rows.slice(0, 12);
}

function renderNseMoverRows(selector, items, emptyMessage) {
  const body = document.querySelector(selector);
  body.innerHTML = "";
  if (!items.length) {
    emptyTable(selector, 5, emptyMessage);
    return;
  }
  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.series || "")}</span></td>
      <td>${formatMoney(item.price, "INR")}</td>
      <td class="${changeClass(item.changePercent)}">${signed(item.change)}<span>${signed(item.changePercent)}%</span></td>
      <td>${formatLarge(item.volume, "")}<span>${formatNumber(item.turnoverLakhs)} lakh turnover</span></td>
      <td>${formatMoney(item.low, "INR")} - ${formatMoney(item.high, "INR")}${item.corporateAction ? `<span>${escapeHtml(item.corporateAction)}</span>` : ""}</td>
    `;
    body.appendChild(row);
  }
}

function renderNseActiveRows(items) {
  const body = document.querySelector("#nseActiveRows");
  body.innerHTML = "";
  if (!items.length) {
    emptyTable("#nseActiveRows", 5, "No NSE most-active rows were returned.");
    return;
  }
  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.lastUpdateTime || "")}</span></td>
      <td>${formatMoney(item.price, "INR")}<span class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</span></td>
      <td>${formatLarge(item.volume, "")}</td>
      <td>${formatLarge(item.value, "INR")}</td>
      <td>${formatMoney(item.yearLow, "INR")} - ${formatMoney(item.yearHigh, "INR")}</td>
    `;
    body.appendChild(row);
  }
}

function renderNseHighAndBandRows(highs, priceBands) {
  const body = document.querySelector("#nseHighRows");
  body.innerHTML = "";
  const bands = (priceBands && priceBands.rows) || [];
  const countText = ((priceBands && priceBands.count) || [])
    .map((item) => `${item.label} ${item.value}`)
    .join(" · ");
  setText("#nsePriceBandCount", countText || `${highs.length + bands.length} shown`);

  const rows = [
    ...highs.map((item) => ({ ...item, type: "52W high" })),
    ...bands.map((item) => ({ ...item, type: "Price band" })),
  ].slice(0, 12);

  if (!rows.length) {
    emptyTable("#nseHighRows", 5, "No NSE 52-week high or price-band rows were returned.");
    return;
  }

  for (const item of rows) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.symbol)}</strong><span>${escapeHtml(item.name || item.type || "")}</span></td>
      <td>${formatMoney(item.price, "INR")}</td>
      <td>${formatMoney(item.newHigh || item.yearHigh, "INR")}<span>${item.previousHighDate ? `Prev ${formatMoney(item.previousHigh, "INR")} on ${escapeHtml(item.previousHighDate)}` : ""}</span></td>
      <td class="${changeClass(item.changePercent)}">${signed(item.change)}<span>${signed(item.changePercent)}%</span></td>
      <td>${Number.isFinite(item.priceBand) ? `${formatNumber(item.priceBand)}%` : escapeHtml(item.priceBucket || "")}<span>${Number.isFinite(item.volume) ? `${formatLarge(item.volume, "")} shares` : ""}</span></td>
    `;
    body.appendChild(row);
  }
}

function emptyTable(selector, colspan, message) {
  const body = document.querySelector(selector);
  if (body) {
    body.innerHTML = `<tr><td colspan="${colspan}">${escapeHtml(message)}</td></tr>`;
  }
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = value;
  }
}

function renderCommodities(items) {
  const container = document.querySelector("#commodityGrid");
  container.innerHTML = "";

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "commodity-card";
    const directionClass = changeClass(item.changePercent);
    card.innerHTML = `
      <span>${escapeHtml(item.category)}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <p>${formatMoney(item.price, "USD")} <em class="${directionClass}">${signed(item.changePercent)}%</em></p>
      <small>1W ${formatPercentValue(item.oneWeek)} · 1M ${formatPercentValue(item.oneMonth)} · ${escapeHtml(item.trend)}</small>
    `;
    container.appendChild(card);
  }
}

function renderBreakoutRows(items) {
  const body = document.querySelector("#breakoutRows");
  body.innerHTML = "";

  if (!items.length) {
    body.innerHTML = "<tr><td colspan=\"7\">No near-high or breakout candidates found in the current scan.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)}</span></td>
      <td>${formatMoney(item.price, "INR")}<span class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</span></td>
      <td>${escapeHtml(item.signal)}</td>
      <td>${formatPercentValue(item.pctBelow52WeekHigh)}<span>52W high ${formatMoney(item.high52Week, "INR")}</span><span>2Y high ${formatMoney(item.availableHigh, "INR")}</span></td>
      <td>${formatMoney(item.prior55High, "INR")}<span>20D ${formatMoney(item.prior20High, "INR")}</span></td>
      <td>${rangeBreakoutText(item, "narrowRange4", "NR4")}<span>${rangeBreakoutText(item, "narrowRange7", "NR7")}</span></td>
      <td>${formatNumber(item.volumeRatio)}x</td>
    `;
    body.appendChild(row);
  }
}

function rangeBreakoutText(item, key, label) {
  const range = item[key] || {};
  if (!range.high) {
    return `${label} n/a`;
  }
  const state = range.breakout ? "breakout" : range.watch ? "watch" : range.tight ? "tight" : "wide";
  return `${label} ${state} ${formatMoney(range.high, "INR")} (${formatPercentValue(range.widthPercent)})`;
}

function renderHighVolumeRows(items) {
  const body = document.querySelector("#highVolumeRows");
  body.innerHTML = "";

  if (!items.length) {
    body.innerHTML = "<tr><td colspan=\"6\">No high-volume upside candidates passed the current filters.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)}</span></td>
      <td>${formatMoney(item.price, "INR")}<span class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</span></td>
      <td>${formatNumber(item.volumeRatio)}x<span>${formatLarge(item.volume, "")} shares</span><span>${formatLarge(item.liquidityValue, "INR")} value</span></td>
      <td>1W ${formatPercentValue(item.oneWeek)}<span>1M ${formatPercentValue(item.oneMonth)}</span></td>
      <td>${escapeHtml(item.signal)}<span>Recent high ${formatMoney(item.recentHigh, "INR")} (${formatPercentValue(item.upsideToRecentHigh)})</span></td>
      <td><strong>${scoreText(item.score)}</strong></td>
    `;
    body.appendChild(row);
  }
}

function renderOrderCatalystRows(items) {
  const body = document.querySelector("#orderCatalystRows");
  body.innerHTML = "";

  if (!items.length) {
    body.innerHTML = "<tr><td colspan=\"5\">No order or contract catalyst headlines were found in the current scan.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    const headline = item.url
      ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.headline)}</a>`
      : escapeHtml(item.headline);
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)}</span></td>
      <td>${headline}<span>${escapeHtml([item.publisher, item.publishedAt ? formatDateTime(item.publishedAt) : ""].filter(Boolean).join(" - "))}</span>${item.orderValue ? `<span>${escapeHtml(item.orderValue)}</span>` : ""}</td>
      <td>${formatMoney(item.price, "INR")}<span class="${changeClass(item.changePercent)}">${formatPercentValue(item.changePercent)}</span><span>Volume ${formatNumber(item.volumeRatio)}x</span></td>
      <td>${escapeHtml(item.signal)}<span>Recent-high gap ${formatPercentValue(item.upsideToRecentHigh)}</span></td>
      <td><strong>${scoreText(item.score)}</strong></td>
    `;
    body.appendChild(row);
  }
}

function renderLevels(levels, currency, technicalLevels) {
  document.querySelector("#levelMode").textContent = levels.mode;
  document.querySelector("#levelsNote").textContent = levels.note;
  document.querySelector("#pullbackEntry").textContent = `${formatMoney(levels.pullbackEntry.low, currency)} - ${formatMoney(levels.pullbackEntry.high, currency)}`;
  document.querySelector("#breakoutTrigger").textContent = formatMoney(levels.breakoutTrigger, currency);
  document.querySelector("#invalidation").textContent = formatMoney(levels.invalidation, currency);
  document.querySelector("#targets").textContent = levels.targets.map((value) => formatMoney(value, currency)).join(" / ");
  document.querySelector("#riskReward").textContent = levels.riskReward ? `${levels.riskReward}:1` : "n/a";
  document.querySelector("#supportZones").textContent = formatZones(technicalLevels && technicalLevels.supportZones, currency, "S");
  document.querySelector("#resistanceZones").textContent = formatZones(technicalLevels && technicalLevels.resistanceZones, currency, "R");
}

function renderTechnical(technical, currency) {
  const indicators = technical.indicators;
  const relativeStrength = technical.relativeStrength || {};
  const rows = [
    ["SMA 20", formatMoney(indicators.sma20, currency)],
    ["SMA 50", formatMoney(indicators.sma50, currency)],
    ["SMA 200", formatMoney(indicators.sma200, currency)],
    ["RSI 14", formatNumber(indicators.rsi14)],
    ["MACD", formatNumber(indicators.macd)],
    ["ATR 14", formatMoney(indicators.atr14, currency)],
    ["ATR %", formatPercentValue(indicators.atrPercent)],
    ["1M return", formatPercentValue(technical.performance.oneMonth)],
    ["3M return", formatPercentValue(technical.performance.threeMonth)],
    ["6M return", formatPercentValue(technical.performance.sixMonth)],
    ["1Y return", formatPercentValue(technical.performance.oneYear)]
  ];

  if (relativeStrength.available) {
    const oneMonth = relativeStrength.rows?.find((row) => row.period === "oneMonth");
    const threeMonth = relativeStrength.rows?.find((row) => row.period === "threeMonth");
    rows.push(
      [`RS vs ${relativeStrength.benchmarkName}`, relativeStrength.label || "n/a"],
      ["1M relative", formatPointChange(oneMonth?.spread)],
      ["3M relative", formatPointChange(threeMonth?.spread)]
    );
  }

  renderTable("#technicalMetrics", rows);
  renderList("#technicalSignals", [
    ...(technical.signals || []),
    ...(relativeStrength.available ? [relativeStrength.summary] : [])
  ]);
}

function renderFundamentals(metrics, signals, currency) {
  const rows = [
    ["Sector", metrics.sector || "n/a"],
    ["Industry", metrics.industry || "n/a"],
    ["Market cap", formatLarge(metrics.marketCap, currency)],
    ["Revenue", formatLarge(metrics.revenue, currency)],
    ["Net profit", formatLarge(metrics.netIncome, currency)],
    ["Trailing P/E", formatNumber(metrics.trailingPE)],
    ["Forward P/E", formatNumber(metrics.forwardPE)],
    ["PEG ratio", formatNumber(metrics.pegRatio)],
    ["Price/book", formatNumber(metrics.priceToBook)],
    ["Revenue growth", formatRatioPercent(metrics.revenueGrowth)],
    ["5Y sales growth", formatRatioPercent(metrics.salesGrowth5y)],
    ["Earnings growth", formatRatioPercent(metrics.earningsGrowth)],
    ["Profit margin", formatRatioPercent(metrics.profitMargins)],
    ["Return on equity", formatRatioPercent(metrics.returnOnEquity)],
    ["ROCE", formatRatioPercent(metrics.returnOnCapitalEmployed)],
    ["Debt/equity", formatNumber(metrics.debtToEquity)],
    ["Promoter holding", formatRatioPercent(metrics.promoterHolding)],
    ["Target mean", formatMoney(metrics.targetMeanPrice, currency)],
    ["Analyst view", metrics.recommendationKey || "n/a"],
    ["Data source", metrics.dataSource || "n/a"]
  ];

  renderTable("#fundamentalMetrics", rows);
  renderList("#fundamentalSignals", signals);
}

function renderOwnershipSnapshot(growthDrivers, fundamentals) {
  const container = document.querySelector("#ownershipSnapshotList");
  const period = document.querySelector("#ownershipSnapshotPeriod");
  const note = document.querySelector("#ownershipSnapshotNote");

  if (!container || !period || !note) {
    return;
  }

  const ownership = growthDrivers?.ownership || {};
  const rows = ownership.rows || [];
  const periods = ownership.periods || [];
  const latestPeriod = periods.length ? periods[periods.length - 1] : "";
  const lookback = growthDrivers?.lookback || "Latest available source data";
  const sourceText = ownership.source ? ` Source: ${ownership.source}.` : "";
  const wanted = [
    { key: "Promoters", label: "Promoters" },
    { key: "FIIs", label: "FII" },
    { key: "DIIs", label: "DII" }
  ];

  container.innerHTML = "";
  period.textContent = latestPeriod || "Latest";

  for (const item of wanted) {
    const row = rows.find((entry) => entry.name === item.key);
    const fallbackLatest = item.key === "Promoters" ? fundamentals?.promoterHolding : null;
    const latest = Number.isFinite(row?.latest) ? row.latest : fallbackLatest;
    const previousQuarter = row?.quarters?.length > 1 ? row.quarters[row.quarters.length - 2] : null;
    const latestQuarter = row?.quarters?.length ? row.quarters[row.quarters.length - 1] : null;
    const latestLabel = latestQuarter?.period || latestPeriod || "Latest";
    const quarterChangePoints = Number.isFinite(row?.quarterChangePoints)
      ? row.quarterChangePoints
      : Number.isFinite(latest) && Number.isFinite(previousQuarter?.value)
      ? (latest - previousQuarter.value) * 100
      : null;

    const card = document.createElement("article");
    card.className = "ownership-snapshot-card";
    card.innerHTML = `
      <span>${escapeHtml(item.label)}</span>
      <div class="ownership-value-row">
        <strong>${formatRatioPercent(latest)}</strong>
        <em class="ownership-change-badge ${holdingChangeClass(quarterChangePoints)}">${escapeHtml(formatHoldingChange(quarterChangePoints))}</em>
      </div>
      <p>Current holding as of ${escapeHtml(latestLabel)}</p>
    `;
    container.appendChild(card);
  }

  note.textContent = `${lookback}.${sourceText} Change badges compare the current holding with the previous available quarter.`;
}

function renderEvents(events) {
  const container = document.querySelector("#eventList");
  container.innerHTML = "";

  if (!events.length) {
    container.innerHTML = "<p class=\"muted\">No upcoming events were found in the available data.</p>";
    return;
  }

  for (const event of events) {
    const row = document.createElement("div");
    row.className = "event-item";
    row.innerHTML = `
      <span>${escapeHtml(event.type)}${event.date ? " - " + escapeHtml(event.date) : ""}</span>
      <strong>${escapeHtml(event.detail || "n/a")}</strong>
    `;
    container.appendChild(row);
  }
}

function renderGrowthDrivers(data, currency) {
  const summary = document.querySelector("#growthDriverText");
  const container = document.querySelector("#growthDriverList");
  container.innerHTML = "";

  if (!data) {
    summary.textContent = "Growth-driver data was not available for this report.";
    return;
  }

  summary.textContent = `${data.summary || "Review ownership, order flow, and policy sensitivity before acting."} ${data.lookback || ""}`.trim();

  const ownershipRows = data.ownership?.rows || [];
  if (ownershipRows.length) {
    const card = document.createElement("article");
    card.className = "growth-card";
    const rows = ownershipRows
      .filter((row) => ["Promoters", "FIIs", "DIIs", "Public"].includes(row.name))
      .map((row) => {
        const history = (row.quarters || [])
          .map((quarter) => `${escapeHtml(quarter.period)} ${formatRatioPercent(quarter.value)}`)
          .join(" / ");
        return `
          <div class="ownership-row">
            <span>${escapeHtml(row.name)}</span>
            <strong>${formatRatioPercent(row.latest)}</strong>
            <em class="${changeClass(row.changePoints)}">${formatPointChange(row.changePoints)}</em>
            <small>${escapeHtml(row.trend || "n/a")}${history ? ` · ${history}` : ""}</small>
          </div>
        `;
      })
      .join("");
    card.innerHTML = `
      <h4>Shareholding Trend</h4>
      <div class="ownership-list">${rows}</div>
    `;
    container.appendChild(card);
  }

  const ownershipFlags = data.ownership?.flags || [];
  if (ownershipFlags.length) {
    const card = document.createElement("article");
    card.className = "growth-card";
    const items = ownershipFlags.map((item) => `
      <div class="ownership-alert is-${escapeHtml(item.type || "neutral")}">
        <strong>${escapeHtml(item.title || "Ownership check")}</strong>
        <small>${escapeHtml(item.detail || "")}</small>
      </div>
    `).join("");
    card.innerHTML = `
      <h4>Ownership Alerts</h4>
      <div class="ownership-alert-list">${items}</div>
    `;
    container.appendChild(card);
  }

  const catalysts = data.catalysts || [];
  if (catalysts.length) {
    const card = document.createElement("article");
    card.className = "growth-card";
    const items = catalysts.map((item) => {
      const title = item.url
        ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>`
        : escapeHtml(item.title);
      return `
        <div class="catalyst-item">
          <span>${escapeHtml(item.type)}${item.publishedAt ? " - " + formatDateTime(item.publishedAt) : ""}</span>
          <strong>${title}</strong>
          <small>${escapeHtml([item.publisher, item.value || item.detail].filter(Boolean).join(" · "))}</small>
        </div>
      `;
    }).join("");
    card.innerHTML = `
      <h4>Orders / Policy Headlines</h4>
      <div class="catalyst-list">${items}</div>
    `;
    container.appendChild(card);
  }

  const sectorAnalysis = data.sectorAnalysis || {};
  if (sectorAnalysis.available && sectorAnalysis.matchedSector) {
    const sector = sectorAnalysis.matchedSector;
    const card = document.createElement("article");
    card.className = "growth-card";
    const title = sectorAnalysis.url
      ? `<a href="${escapeHtml(sectorAnalysis.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sector.sector)}</a>`
      : escapeHtml(sector.sector);
    card.innerHTML = `
      <h4>Moneycontrol Sector Analysis</h4>
      <div class="sector-analysis-card">
        <strong>${title}</strong>
        <p>${escapeHtml(sectorAnalysis.summary || sector.summary || "")}</p>
        <div class="sector-analysis-metrics">
          <span>Trend <em>${escapeHtml(sector.trend || "n/a")}</em></span>
          <span>Mcap <em class="${changeClass(sector.marketCapChangePercent)}">${signed(sector.marketCapChangePercent)}%</em></span>
          <span>A/D <em>${formatNumber(sector.advance)} / ${formatNumber(sector.decline)}</em></span>
          <span>PE <em>${formatNumber(sector.sectorPe)}</em></span>
          <span>NP YoY <em class="${changeClass(sector.earningsYoyChange)}">${signed(sector.earningsYoyChange)}%</em></span>
          <span>Rank <em>${sectorAnalysis.rank ? `#${sectorAnalysis.rank}` : "n/a"}</em></span>
        </div>
      </div>
    `;
    container.appendChild(card);
  }

  const budgetImpacts = data.budgetImpacts || [];
  if (budgetImpacts.length) {
    const card = document.createElement("article");
    card.className = "growth-card";
    const items = budgetImpacts.map((item) => `
      <div class="budget-impact-item">
        <strong>${escapeHtml(item.theme)}</strong>
        <p>${escapeHtml(item.impact)}</p>
        <small>${escapeHtml(item.basis || "")}</small>
      </div>
    `).join("");
    card.innerHTML = `
      <h4>Budget / Sector Impact</h4>
      <div class="budget-impact-list">${items}</div>
    `;
    container.appendChild(card);
  }

  if (!container.children.length) {
    const notes = data.dataNotes || [];
    container.innerHTML = `<p class="muted">${escapeHtml(notes[0] || "No ownership, order, or budget catalyst data was found in the available sources.")}</p>`;
  }
}

function renderQuality(quality) {
  document.querySelector("#qualityText").textContent = quality.summary;
  renderTable("#qualityMetrics", [
    ["Confidence", `${quality.score}/100 (${quality.label})`],
    ["Chart candles", formatNumber(quality.chartPoints)],
    ["Fundamental metrics", formatNumber(quality.availableFundamentalMetrics)],
    ["Data sources", quality.dataSources || "n/a"]
  ]);
  renderAccuracyChecks(quality.checks || []);
  renderList("#qualityWarnings", [
    ...(quality.strengths || []).map((item) => `Strength: ${item}`),
    ...(quality.warnings || []).map((item) => `Warning: ${item}`)
  ]);
}

function renderAccuracyChecks(checks) {
  const container = document.querySelector("#qualityChecks");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  if (!checks.length) {
    return;
  }

  for (const check of checks) {
    const item = document.createElement("div");
    item.className = `accuracy-check is-${check.tone || "neutral"}`;
    item.innerHTML = `
      <div>
        <span>${escapeHtml(check.label || "Check")}</span>
        <strong>${escapeHtml(check.status || "Review")}</strong>
      </div>
      <p>${escapeHtml(check.detail || "")}</p>
    `;
    container.appendChild(item);
  }
}

function renderScenarios(scenarios, currency) {
  const container = document.querySelector("#scenarioList");
  container.innerHTML = "";

  const items = [
    {
      title: scenarios.bull.title,
      lines: [
        `Trigger: ${formatMoney(scenarios.bull.trigger, currency)}`,
        scenarios.bull.confirmation,
        `Targets: ${scenarios.bull.expectedMove.map((value) => formatMoney(value, currency)).join(" / ")}`,
        scenarios.bull.reason
      ]
    },
    {
      title: scenarios.base.title,
      lines: [
        `Range: ${formatMoney(scenarios.base.rangeLow, currency)} - ${formatMoney(scenarios.base.rangeHigh, currency)}`,
        scenarios.base.action,
        scenarios.base.reason
      ]
    },
    {
      title: scenarios.bear.title,
      lines: [
        `Trigger: ${formatMoney(scenarios.bear.trigger, currency)}`,
        scenarios.bear.nextSupport ? `Next support: ${formatMoney(scenarios.bear.nextSupport, currency)}` : "Next support: n/a",
        scenarios.bear.action,
        scenarios.bear.reason
      ]
    }
  ];

  for (const item of items) {
    const panel = document.createElement("div");
    panel.className = "scenario-item";
    const title = document.createElement("strong");
    title.textContent = item.title;
    panel.appendChild(title);
    for (const line of item.lines) {
      const p = document.createElement("p");
      p.textContent = line;
      panel.appendChild(p);
    }
    container.appendChild(panel);
  }
}

function renderSwingTradePlan(plan, currency) {
  const list = document.querySelector("#swingTradeList");
  const badge = document.querySelector("#swingTradeSuitability");
  const summary = document.querySelector("#swingTradeSummary");
  list.innerHTML = "";

  if (!plan || !Array.isArray(plan.plans) || !plan.plans.length) {
    badge.textContent = "Plan unavailable";
    summary.textContent = "A swing trade plan could not be generated from the available data.";
    return;
  }

  const suitability = plan.suitability || {};
  badge.textContent = `${suitability.label || "Swing setup"} ${Number.isFinite(suitability.score) ? scoreText(suitability.score) : ""}`.trim();
  summary.textContent = suitability.summary || plan.note || "";

  for (const item of plan.plans) {
    const article = document.createElement("article");
    article.className = "swing-plan-item";
    const targets = (item.targets || [])
      .map((target) => `${escapeHtml(target.label)} ${formatMoney(target.price, currency)}`)
      .join(" / ");
    const conditions = (item.conditions || [])
      .map((condition) => `<li>${escapeHtml(condition)}</li>`)
      .join("");

    article.innerHTML = `
      <div class="swing-plan-title">
        <div>
          <span>${escapeHtml(item.timeframe)}</span>
          <strong>${escapeHtml(item.horizon)}</strong>
        </div>
        <em>${scoreText(item.score)}</em>
      </div>
      <div class="swing-plan-grid">
        <div>
          <span>Setup</span>
          <strong>${escapeHtml(item.setup)}</strong>
        </div>
        <div>
          <span>Entry zone</span>
          <strong>${formatMoney(item.entry?.low, currency)} - ${formatMoney(item.entry?.high, currency)}</strong>
          <small>Trigger ${formatMoney(item.entry?.trigger, currency)}</small>
        </div>
        <div>
          <span>Exit targets</span>
          <strong>${targets || "n/a"}</strong>
          <small>Risk/reward ${Number.isFinite(item.riskReward) ? `${item.riskReward}:1` : "n/a"}</small>
        </div>
        <div>
          <span>Stop loss</span>
          <strong>${formatMoney(item.stopLoss, currency)}</strong>
          <small>${escapeHtml(item.exitPlan?.time || "")}</small>
        </div>
      </div>
      <ul class="swing-condition-list">${conditions}</ul>
    `;
    list.appendChild(article);
  }
}

function renderReferences(references) {
  const container = document.querySelector("#referenceLinks");
  container.innerHTML = "";

  if (!references || !Array.isArray(references.links) || !references.links.length) {
    container.innerHTML = "<p class=\"muted\">No external reference links were generated for this symbol.</p>";
    return;
  }

  for (const link of references.links) {
    const anchor = document.createElement("a");
    anchor.className = "reference-link";
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";

    const title = document.createElement("strong");
    title.textContent = link.label;
    const note = document.createElement("span");
    note.textContent = link.note;

    anchor.append(title, note);
    container.appendChild(anchor);
  }
}

function renderTable(selector, rows) {
  const container = document.querySelector(selector);
  container.innerHTML = "";
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    const name = document.createElement("span");
    const amount = document.createElement("strong");
    name.textContent = label;
    amount.textContent = value;
    row.append(name, amount);
    container.appendChild(row);
  }
}

function renderList(selector, items) {
  const list = document.querySelector(selector);
  list.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    list.appendChild(li);
  }
}

function redrawChart() {
  if (!latestReport) {
    return;
  }
  requestAnimationFrame(() => drawChart(latestReport.series, latestReport.currency, latestReport.technical.levels));
}

function drawChart(series, currency, levels = {}, retryCount = 0) {
  const context = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  if (rect.width < 20 || rect.height < 20) {
    if (retryCount < 5) {
      requestAnimationFrame(() => drawChart(series, currency, levels, retryCount + 1));
    }
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  context.setTransform(dpr, 0, 0, dpr, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const padding = chartPaddingFor(width);
  const plotWidth = Math.max(1, width - padding.left - padding.right);
  const plotHeight = Math.max(1, height - padding.top - padding.bottom);
  const levelFilters = getChartLevelFilters();
  const levelZones = normalizeLevelZones(levels).filter((level) => levelFilters[level.type]);
  const values = series
    .flatMap((item) => [item.open, item.high, item.low, item.close, item.sma50, item.sma200])
    .concat(levelZones.flatMap((level) => [level.price, level.zoneLow, level.zoneHigh]))
    .filter(Number.isFinite);
  if (!values.length) {
    return;
  }
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const paddingRange = (rawMax - rawMin || rawMax * 0.02 || 1) * 0.06;
  const min = rawMin - paddingRange;
  const max = rawMax + paddingRange;
  const range = max - min || 1;

  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fbfcfa";
  context.fillRect(0, 0, width, height);

  context.strokeStyle = "#e0e6dc";
  context.lineWidth = 1;
  context.fillStyle = "#667078";
  context.font = "12px system-ui, sans-serif";

  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + (plotHeight / 4) * index;
    const value = max - (range / 4) * index;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillText(formatCompactMoney(value, currency), width - padding.right + 10, y + 4);
  }

  drawLevelZones(context, levelZones.filter((level) => level.type === "support"), "S", "#127348", currency, padding, plotWidth, plotHeight, min, range);
  drawLevelZones(context, levelZones.filter((level) => level.type === "resistance"), "R", "#b42318", currency, padding, plotWidth, plotHeight, min, range);

  drawCandlesticks(context, series, padding, plotWidth, plotHeight, min, range);
  plotLine(context, series, "sma50", "#2563eb", 1.6, padding, plotWidth, plotHeight, min, range);
  plotLine(context, series, "sma200", "#b45309", 1.6, padding, plotWidth, plotHeight, min, range);

  drawLevelLabels(context, levelZones.filter((level) => level.type === "support"), "S", "#127348", currency, padding, plotHeight, min, range);
  drawLevelLabels(context, levelZones.filter((level) => level.type === "resistance"), "R", "#b42318", currency, padding, plotHeight, min, range);

  context.fillStyle = "#667078";
  const firstDate = series[0] && series[0].date;
  const lastDate = series[series.length - 1] && series[series.length - 1].date;
  context.fillText(firstDate || "", padding.left, height - 12);
  const lastTextWidth = context.measureText(lastDate || "").width;
  context.fillText(lastDate || "", width - padding.right - lastTextWidth, height - 12);
}

function getChartLevelFilters() {
  return {
    support: chartSupportToggle.checked,
    resistance: chartResistanceToggle.checked
  };
}

function chartPaddingFor(width) {
  if (width <= 420) {
    return { top: 18, right: 72, bottom: 28, left: 8 };
  }
  if (width <= 640) {
    return { top: 20, right: 84, bottom: 30, left: 10 };
  }
  return { top: 24, right: 112, bottom: 34, left: 14 };
}

function normalizeLevelZones(levels) {
  const supports = Array.isArray(levels.supportZones) && levels.supportZones.length
    ? levels.supportZones
    : (levels.supports || []).map((price) => ({ price, zoneLow: price, zoneHigh: price }));
  const resistances = Array.isArray(levels.resistanceZones) && levels.resistanceZones.length
    ? levels.resistanceZones
    : (levels.resistances || []).map((price) => ({ price, zoneLow: price, zoneHigh: price }));

  return [
    ...supports.slice(0, 3).map((level, index) => ({ ...level, type: "support", index: index + 1 })),
    ...resistances.slice(0, 3).map((level, index) => ({ ...level, type: "resistance", index: index + 1 }))
  ].filter((level) => Number.isFinite(level.price));
}

function drawLevelZones(context, levels, prefix, color, currency, padding, plotWidth, plotHeight, min, range) {
  for (const level of levels) {
    const zoneLow = Number.isFinite(level.zoneLow) ? level.zoneLow : level.price;
    const zoneHigh = Number.isFinite(level.zoneHigh) ? level.zoneHigh : level.price;
    const top = yForValue(zoneHigh, padding, plotHeight, min, range);
    const bottom = yForValue(zoneLow, padding, plotHeight, min, range);
    const mid = yForValue(level.price, padding, plotHeight, min, range);

    context.save();
    context.fillStyle = colorToRgba(color, 0.08);
    context.fillRect(padding.left, top, plotWidth, Math.max(2, bottom - top));
    context.strokeStyle = colorToRgba(color, 0.78);
    context.setLineDash([7, 5]);
    context.lineWidth = 1.2;
    context.beginPath();
    context.moveTo(padding.left, mid);
    context.lineTo(padding.left + plotWidth, mid);
    context.stroke();
    context.restore();
  }
}

function drawLevelLabels(context, levels, prefix, color, currency, padding, plotHeight, min, range) {
  const placed = [];
  const labelX = padding.left + (canvas.getBoundingClientRect().width - padding.left - padding.right) + 6;

  levels.forEach((level) => {
    let y = yForValue(level.price, padding, plotHeight, min, range);
    for (const previous of placed) {
      if (Math.abs(y - previous) < 18) {
        y += y >= previous ? 18 : -18;
      }
    }
    placed.push(y);

    const label = `${prefix}${level.index} ${formatCompactMoney(level.price, currency)}`;
    context.save();
    context.font = "700 11px system-ui, sans-serif";
    const labelWidth = context.measureText(label).width + 10;
    context.fillStyle = colorToRgba(color, 0.12);
    context.strokeStyle = colorToRgba(color, 0.35);
    context.lineWidth = 1;
    roundedRect(context, labelX, y - 10, labelWidth, 20, 5);
    context.fill();
    context.stroke();
    context.fillStyle = color;
    context.fillText(label, labelX + 5, y + 4);
    context.restore();
  });
}

function yForValue(value, padding, plotHeight, min, range) {
  return padding.top + (1 - (value - min) / range) * plotHeight;
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.lineTo(x + width - radius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + radius);
  context.lineTo(x + width, y + height - radius);
  context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  context.lineTo(x + radius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - radius);
  context.lineTo(x, y + radius);
  context.quadraticCurveTo(x, y, x + radius, y);
}

function plotLine(context, series, key, color, width, padding, plotWidth, plotHeight, min, range) {
  context.beginPath();
  context.strokeStyle = color;
  context.lineWidth = width;
  let started = false;

  series.forEach((item, index) => {
    const value = item[key];
    if (!Number.isFinite(value)) {
      started = false;
      return;
    }
    const x = padding.left + (index / Math.max(1, series.length - 1)) * plotWidth;
    const y = padding.top + (1 - (value - min) / range) * plotHeight;
    if (!started) {
      context.moveTo(x, y);
      started = true;
    } else {
      context.lineTo(x, y);
    }
  });

  context.stroke();
}

function drawCandlesticks(context, series, padding, plotWidth, plotHeight, min, range) {
  const spacing = plotWidth / Math.max(1, series.length - 1);
  const bodyWidth = Math.max(1, Math.min(9, spacing * 0.62));

  context.save();
  context.lineWidth = Math.max(1, Math.min(1.4, bodyWidth * 0.45));

  series.forEach((item, index) => {
    const close = item.close;
    const open = Number.isFinite(item.open) ? item.open : close;
    const high = Number.isFinite(item.high) ? item.high : Math.max(open, close);
    const low = Number.isFinite(item.low) ? item.low : Math.min(open, close);
    if (![open, high, low, close].every(Number.isFinite)) {
      return;
    }

    const x = padding.left + (index / Math.max(1, series.length - 1)) * plotWidth;
    const yOpen = yForValue(open, padding, plotHeight, min, range);
    const yHigh = yForValue(high, padding, plotHeight, min, range);
    const yLow = yForValue(low, padding, plotHeight, min, range);
    const yClose = yForValue(close, padding, plotHeight, min, range);
    const isUp = close >= open;
    const color = isUp ? "#127348" : "#b42318";
    const top = Math.min(yOpen, yClose);
    const height = Math.max(1, Math.abs(yClose - yOpen));

    context.strokeStyle = colorToRgba(color, 0.88);
    context.fillStyle = colorToRgba(color, isUp ? 0.2 : 0.78);
    context.beginPath();
    context.moveTo(x, yHigh);
    context.lineTo(x, yLow);
    context.stroke();
    context.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
    context.strokeRect(x - bodyWidth / 2, top, bodyWidth, height);
  });

  context.restore();
}

function formatZones(levels, currency, prefix) {
  if (!Array.isArray(levels) || !levels.length) {
    return "n/a";
  }
  return levels
    .slice(0, 3)
    .map((level, index) => {
      const strength = level.label ? ` ${level.label}` : "";
      return `${prefix}${index + 1} ${formatMoney(level.price, currency)}${strength}`;
    })
    .join(" / ");
}

function colorToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const red = Number.parseInt(clean.slice(0, 2), 16);
  const green = Number.parseInt(clean.slice(2, 4), 16);
  const blue = Number.parseInt(clean.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function formatMoney(value, currency) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return safeNumberFormat({
    style: currency ? "currency" : "decimal",
    currency: currency || undefined,
    maximumFractionDigits: value >= 1000 ? 0 : 2
  }).format(value);
}

function formatCompactMoney(value, currency) {
  if (!Number.isFinite(value)) {
    return "";
  }
  return safeNumberFormat({
    style: currency ? "currency" : "decimal",
    currency: currency || undefined,
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value);
}

function formatLarge(value, currency) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return safeNumberFormat({
    style: currency ? "currency" : "decimal",
    currency: currency || undefined,
    notation: "compact",
    maximumFractionDigits: 2
  }).format(value);
}

function formatNumber(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

function formatRatioPercent(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${(value * 100).toFixed(2)}%`;
}

function formatExpenseRatio(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  const percent = value <= 1 ? value * 100 : value;
  return `${percent.toFixed(2)}%`;
}

function formatPercentValue(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(2)}%`;
}

function formatPointChange(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)} pp`;
}

function formatHoldingChange(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  if (Math.abs(value) < 0.005) {
    return "No change";
  }
  return `${value > 0 ? "+" : "-"}${Math.abs(value).toFixed(2)} pp`;
}

function holdingChangeClass(value) {
  if (!Number.isFinite(value) || Math.abs(value) < 0.005) {
    return "is-flat";
  }
  return value > 0 ? "is-up" : "is-down";
}

function scoreText(value) {
  return `${Math.round(value)}/100`;
}

function signed(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

function changeClass(value) {
  if (!Number.isFinite(value)) {
    return "muted";
  }
  return value >= 0 ? "positive" : "negative";
}

function statusText(value) {
  const labels = {
    watch: "Watch",
    active: "Active",
    closed: "Closed"
  };
  return labels[value] || value || "Watch";
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function safeNumberFormat(options) {
  try {
    return new Intl.NumberFormat("en-US", options);
  } catch {
    const { currency, ...fallback } = options;
    return new Intl.NumberFormat("en-US", { ...fallback, style: "decimal" });
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

showState("empty");
