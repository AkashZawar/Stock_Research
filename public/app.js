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
const watchlistTab = document.querySelector("#watchlistTab");
const monitorTab = document.querySelector("#monitorTab");
const tradeTab = document.querySelector("#tradeTab");
const searchLogTab = document.querySelector("#searchLogTab");
const analysisView = document.querySelector("#analysisView");
const etfView = document.querySelector("#etfView");
const fundView = document.querySelector("#fundView");
const watchlistView = document.querySelector("#watchlistView");
const monitorView = document.querySelector("#monitorView");
const tradeView = document.querySelector("#tradeView");
const searchLogView = document.querySelector("#searchLogView");
const refreshMonitor = document.querySelector("#refreshMonitor");
const monitorLoading = document.querySelector("#monitorLoading");
const monitorError = document.querySelector("#monitorError");
const monitorContent = document.querySelector("#monitorContent");
const monitorPaneButtons = Array.from(document.querySelectorAll("[data-monitor-pane-button]"));
const monitorSections = Array.from(document.querySelectorAll("[data-monitor-section]"));
const stockHeatmapGrid = document.querySelector("#stockHeatmapGrid");
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
const watchlistForm = document.querySelector("#watchlistForm");
const watchlistRows = document.querySelector("#watchlistRows");
const watchlistLoading = document.querySelector("#watchlistLoading");
const watchlistError = document.querySelector("#watchlistError");
const refreshWatchlistButton = document.querySelector("#refreshWatchlist");
const useCurrentWatchlistReport = document.querySelector("#useCurrentWatchlistReport");
const chartSupportToggle = document.querySelector("#chartSupportToggle");
const chartResistanceToggle = document.querySelector("#chartResistanceToggle");
const expandChartButton = document.querySelector("#expandChart");
const marketClockEl = document.querySelector("#marketClock");
const marketClockTime = document.querySelector("#marketClockTime");
const marketClockStatus = document.querySelector("#marketClockStatus");
const marketClockDetail = document.querySelector("#marketClockDetail");
const openInterestPanel = document.querySelector("#openInterestPanel");
const openInterestSource = document.querySelector("#openInterestSource");
const openInterestSummary = document.querySelector("#openInterestSummary");
const openInterestChart = document.querySelector("#openInterestChart");
const openInterestMetrics = document.querySelector("#openInterestMetrics");
const openInterestRows = document.querySelector("#openInterestRows");
const openInterestTabs = Array.from(document.querySelectorAll("[data-oi-period]"));
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
let latestWatchlistItems = null;
let latestSearchLogs = null;
let latestAssetReports = { etf: null, fund: null };
let searchTimer = null;
let assetSearchTimers = { etf: null, fund: null };
let suggestionDialogEl = null;
let chartDialogEl = null;
let expandedChartCanvas = null;
let marketClockTimer = null;
let monitorPollTimer = null;
let selectedOpenInterestPeriod = "day";
let selectedMonitorPane = "primary";
let chartRedrawFrame = null;
let monitorRequestInFlight = false;
const MONITOR_LIVE_POLL_MS = 1000;
const WATCHLIST_REFRESH_CONCURRENCY = 3;
const analysisRequestCache = new Map();
let searchController = null;
let assetSearchControllers = { etf: null, fund: null };

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
watchlistTab?.addEventListener("click", () => {
  setActiveTab("watchlist");
  if (!latestWatchlistItems) {
    loadWatchlist();
  } else {
    renderWatchlist();
  }
});
monitorTab.addEventListener("click", () => {
  setActiveTab("monitor");
  if (!latestMonitor) {
    loadMarketMonitor(false);
  }
});
tradeTab?.addEventListener("click", () => {
  setActiveTab("trade");
  if (!latestTradeReferences) {
    loadTradeReferences();
  }
});
searchLogTab?.addEventListener("click", () => {
  setActiveTab("logs");
  if (!latestSearchLogs) {
    loadSearchLogs();
  }
});
refreshMonitor.addEventListener("click", () => loadMarketMonitor(true));
for (const button of monitorPaneButtons) {
  button.addEventListener("click", () => setMonitorPane(button.dataset.monitorPaneButton || "primary"));
}
refreshSearchLogs?.addEventListener("click", loadSearchLogs);
useCurrentReport?.addEventListener("click", prefillTradeFromReport);
refreshWatchlistButton?.addEventListener("click", () => refreshWatchlist(true));
useCurrentWatchlistReport?.addEventListener("click", () => addCurrentReportToWatchlist());
chartSupportToggle.addEventListener("change", redrawChart);
chartResistanceToggle.addEventListener("change", redrawChart);
expandChartButton.addEventListener("click", openExpandedChart);
for (const tab of openInterestTabs) {
  tab.addEventListener("click", () => {
    selectedOpenInterestPeriod = tab.dataset.oiPeriod || "day";
    renderOpenInterest(latestReport?.openInterest, latestReport?.currency);
  });
}
canvas.addEventListener("click", confirmTradingViewRedirect);
canvas.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") {
    return;
  }
  event.preventDefault();
  confirmTradingViewRedirect();
});

tradeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveTradeReference();
});

tradeRows?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-trade]");
  if (!button) {
    return;
  }
  await deleteTradeReference(button.dataset.deleteTrade);
});

watchlistForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await addWatchlistItemFromForm();
});

watchlistRows?.addEventListener("click", async (event) => {
  const deleteButton = event.target.closest("[data-delete-watchlist]");
  if (deleteButton) {
    await deleteWatchlistItem(deleteButton.dataset.deleteWatchlist);
    return;
  }

  const refreshButton = event.target.closest("[data-refresh-watchlist]");
  if (refreshButton) {
    await refreshWatchlistItem(refreshButton.dataset.refreshWatchlist, true);
    return;
  }

  const checkButton = event.target.closest("[data-check-watchlist]");
  if (checkButton) {
    await checkWatchlistPrice(checkButton.dataset.checkWatchlist);
  }
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
      if (assetSearchControllers[context.prefix]) {
        assetSearchControllers[context.prefix].abort();
        assetSearchControllers[context.prefix] = null;
      }
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
    if (searchController) {
      searchController.abort();
      searchController = null;
    }
    suggestions.innerHTML = "";
    return;
  }
  searchTimer = setTimeout(() => searchSymbols(query), 180);
});

window.addEventListener("resize", () => {
  if (latestReport && !analysisView.classList.contains("is-hidden")) {
    scheduleChartRedraw();
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-asset-shortlist-symbol]");
  if (!button) {
    return;
  }

  const symbol = button.dataset.assetShortlistSymbol || "";
  const type = button.dataset.assetShortlistType || "etf";
  const mode = button.dataset.assetShortlistMode || "analyze";
  const context = type === "mutual-fund" ? assetContexts.fund : assetContexts.etf;
  if (!symbol || !context) {
    return;
  }

  context.input.value = symbol;
  context.suggestions.innerHTML = "";
  setActiveTab(context.prefix);
  if (mode === "search") {
    searchAssets(context, symbol);
    context.input.focus();
    return;
  }
  analyzeAsset(context, symbol);
});

function setActiveTab(tab) {
  const isEtf = tab === "etf";
  const isFund = tab === "fund";
  const isWatchlist = tab === "watchlist" && Boolean(watchlistView);
  const isMonitor = tab === "monitor";
  const isTrade = tab === "trade" && Boolean(tradeView);
  const isLogs = tab === "logs" && Boolean(searchLogView);
  const isAnalysis = !isEtf && !isFund && !isWatchlist && !isMonitor && !isTrade && !isLogs;
  analysisTab.classList.toggle("is-active", isAnalysis);
  etfTab.classList.toggle("is-active", isEtf);
  fundTab.classList.toggle("is-active", isFund);
  watchlistTab?.classList.toggle("is-active", isWatchlist);
  monitorTab.classList.toggle("is-active", isMonitor);
  tradeTab?.classList.toggle("is-active", isTrade);
  searchLogTab?.classList.toggle("is-active", isLogs);
  analysisView.classList.toggle("is-hidden", !isAnalysis);
  etfView.classList.toggle("is-hidden", !isEtf);
  fundView.classList.toggle("is-hidden", !isFund);
  watchlistView?.classList.toggle("is-hidden", !isWatchlist);
  monitorView.classList.toggle("is-hidden", !isMonitor);
  tradeView?.classList.toggle("is-hidden", !isTrade);
  searchLogView?.classList.toggle("is-hidden", !isLogs);
  if (isAnalysis && latestReport) {
    redrawChart();
  }
  if (isMonitor) {
    scheduleMarketMonitorPoll();
  } else {
    clearMarketMonitorPoll();
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
  if (searchController) {
    searchController.abort();
  }
  const controller = new AbortController();
  searchController = controller;
  try {
    const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
    const payload = await response.json();
    if (searchController !== controller) {
      return;
    }
    renderSuggestionOptions(suggestions, payload.results || []);
  } catch (error) {
    if (error.name !== "AbortError" && searchController === controller) {
      suggestions.innerHTML = "";
    }
  } finally {
    if (searchController === controller) {
      searchController = null;
    }
  }
}

async function searchAssets(context, query) {
  if (assetSearchControllers[context.prefix]) {
    assetSearchControllers[context.prefix].abort();
  }
  const controller = new AbortController();
  assetSearchControllers[context.prefix] = controller;
  try {
    const response = await fetch(`/api/search-assets?type=${encodeURIComponent(context.type)}&q=${encodeURIComponent(query)}`, { signal: controller.signal });
    const payload = await response.json();
    if (assetSearchControllers[context.prefix] !== controller) {
      return;
    }
    renderSuggestionOptions(context.suggestions, payload.results || []);
  } catch (error) {
    if (error.name !== "AbortError" && assetSearchControllers[context.prefix] === controller) {
      context.suggestions.innerHTML = "";
    }
  } finally {
    if (assetSearchControllers[context.prefix] === controller) {
      assetSearchControllers[context.prefix] = null;
    }
  }
}

function renderSuggestionOptions(container, results) {
  container.innerHTML = "";
  const fragment = document.createDocumentFragment();
  for (const item of results) {
    const option = document.createElement("option");
    option.value = item.symbol;
    option.label = `${item.name} ${item.exchange ? "- " + item.exchange : ""}`;
    fragment.appendChild(option);
  }
  container.appendChild(fragment);
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

async function loadMarketMonitor(forceRefresh, options = {}) {
  const silent = Boolean(options.silent);
  const live = Boolean(options.live);
  if (monitorRequestInFlight && silent) {
    return;
  }
  monitorRequestInFlight = true;
  if (!silent) {
    monitorLoading.classList.remove("is-hidden");
    monitorError.classList.add("is-hidden");
    if (!latestMonitor) {
      monitorContent.classList.add("is-hidden");
    }
  }

  try {
    const suffix = forceRefresh ? "?refresh=1" : live ? "?live=1" : "";
    const response = await fetch(`/api/market-monitor${suffix}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not load market monitor.");
    }
    latestMonitor = payload;
    renderMarketMonitor(payload);
    setMonitorPane(selectedMonitorPane);
    monitorContent.classList.remove("is-hidden");
    if (!monitorView.classList.contains("is-hidden")) {
      scheduleMarketMonitorPoll();
    } else {
      clearMarketMonitorPoll();
    }
  } catch (error) {
    if (!silent) {
      monitorError.textContent = error.message;
      monitorError.classList.remove("is-hidden");
    } else if (!monitorView.classList.contains("is-hidden")) {
      scheduleMarketMonitorPoll(2500);
    }
  } finally {
    monitorRequestInFlight = false;
    if (!silent) {
      monitorLoading.classList.add("is-hidden");
    }
  }
}

function scheduleMarketMonitorPoll(delay = MONITOR_LIVE_POLL_MS) {
  if (monitorView.classList.contains("is-hidden")) {
    clearMarketMonitorPoll();
    return;
  }
  clearMarketMonitorPoll();
  monitorPollTimer = window.setTimeout(() => {
    loadMarketMonitor(false, { silent: true, live: true });
  }, delay);
}

function clearMarketMonitorPoll() {
  if (monitorPollTimer) {
    window.clearTimeout(monitorPollTimer);
    monitorPollTimer = null;
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

async function loadWatchlist({ refreshAnalysis = true, showErrors = false } = {}) {
  hideWatchlistError();
  setWatchlistLoading(true);
  try {
    const savedItems = await fetchSavedWatchlistItems();
    latestWatchlistItems = savedItems.map(savedWatchlistToDisplayItem);
    renderWatchlist();
    if (refreshAnalysis && savedItems.length) {
      await refreshWatchlist(showErrors);
    }
  } catch (error) {
    latestWatchlistItems = latestWatchlistItems || [];
    renderWatchlist();
    if (showErrors) {
      showWatchlistError(error.message);
    }
  } finally {
    setWatchlistLoading(false);
  }
}

async function addWatchlistItemFromForm() {
  hideWatchlistError();
  const symbol = document.querySelector("#watchlistSymbol").value.trim();
  if (!symbol) {
    showWatchlistError("Enter a stock symbol or name.");
    return;
  }

  setWatchlistLoading(true);
  try {
    const report = await fetchAnalysisForWatchlist(symbol);
    const item = buildWatchlistItem(report, {
      name: document.querySelector("#watchlistName").value.trim(),
      buyPrice: inputNumber("#watchlistBuyPrice"),
      sellPrice: inputNumber("#watchlistSellPrice"),
      checkPrice: inputNumber("#watchlistCheckPrice")
    });
    await persistWatchlistItem(item);
    saveWatchlistItem(item);
    watchlistForm.reset();
    renderWatchlist();
  } catch (error) {
    showWatchlistError(error.message);
  } finally {
    setWatchlistLoading(false);
  }
}

async function addCurrentReportToWatchlist() {
  hideWatchlistError();
  if (!latestReport) {
    showWatchlistError("Load a stock report first, then add it to the watchlist.");
    return;
  }
  setWatchlistLoading(true);
  try {
    const item = buildWatchlistItem(latestReport);
    await persistWatchlistItem(item);
    saveWatchlistItem(item);
    setActiveTab("watchlist");
    renderWatchlist();
  } catch (error) {
    showWatchlistError(error.message);
  } finally {
    setWatchlistLoading(false);
  }
}

async function refreshWatchlist(showErrors = false) {
  hideWatchlistError();
  setWatchlistLoading(true);
  try {
    const baseItems = await fetchSavedWatchlistItems();
    const items = baseItems.length ? baseItems.map(savedWatchlistToDisplayItem) : loadWatchlistItems();
    if (!items.length) {
      latestWatchlistItems = [];
      renderWatchlist();
      return;
    }

    const results = await mapWithConcurrency(items, WATCHLIST_REFRESH_CONCURRENCY, async (item) => {
      try {
        const report = await fetchAnalysisForWatchlist(item.symbol);
        return {
          ok: true,
          item: buildWatchlistItem(report, {
            id: item.id,
            name: item.manualName || "",
            buyPrice: item.manualBuyPrice,
            sellPrice: item.manualSellPrice,
            checkPrice: item.checkPrice
          })
        };
      } catch (error) {
        return {
          ok: false,
          failure: `${item.symbol}: ${error.message}`,
          item: { ...item, rowTone: "watch", indicatorLabel: "Refresh failed", reason: error.message }
        };
      }
    });

    const nextItems = results.map((result) => result.item);
    const failures = results.filter((result) => !result.ok).map((result) => result.failure);
    saveWatchlistItems(nextItems);
    renderWatchlist();
    if (showErrors && failures.length) {
      showWatchlistError(`Some stocks could not refresh. ${failures.slice(0, 2).join(" | ")}`);
    }
  } catch (error) {
    if (showErrors) {
      showWatchlistError(error.message);
    }
  } finally {
    setWatchlistLoading(false);
  }
}

async function mapWithConcurrency(items, concurrency, mapper) {
  const results = new Array(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(1, concurrency), items.length);
  const workers = Array.from({ length: workerCount }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await mapper(items[index], index);
    }
  });
  await Promise.all(workers);
  return results;
}

async function refreshWatchlistItem(id, showErrors = false) {
  hideWatchlistError();
  const items = loadWatchlistItems();
  const item = items.find((entry) => String(entry.id) === String(id));
  if (!item) {
    return;
  }

  setWatchlistLoading(true);
  try {
    const report = await fetchAnalysisForWatchlist(item.symbol);
    saveWatchlistItem(buildWatchlistItem(report, {
      id: item.id,
      name: item.manualName || "",
      buyPrice: item.manualBuyPrice,
      sellPrice: item.manualSellPrice,
      checkPrice: item.checkPrice
    }));
    renderWatchlist();
  } catch (error) {
    if (showErrors) {
      showWatchlistError(error.message);
    }
  } finally {
    setWatchlistLoading(false);
  }
}

async function fetchAnalysisForWatchlist(symbol) {
  const normalizedSymbol = String(symbol || "").trim().toUpperCase();
  if (!normalizedSymbol) {
    throw new Error("Watchlist symbol is missing.");
  }
  if (latestReport?.symbol?.toUpperCase() === normalizedSymbol) {
    return latestReport;
  }
  if (!analysisRequestCache.has(normalizedSymbol)) {
    const request = fetch(`/api/analyze?symbol=${encodeURIComponent(normalizedSymbol)}`)
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) {
          if (payload.suggestions) {
            throw new Error("Choose a specific ticker from the suggestions, then add it to the watchlist.");
          }
          throw new Error(payload.error || "Could not refresh stock analysis.");
        }
        return payload;
      })
      .finally(() => {
        analysisRequestCache.delete(normalizedSymbol);
      });
    analysisRequestCache.set(normalizedSymbol, request);
  }
  return analysisRequestCache.get(normalizedSymbol);
}

function buildWatchlistItem(report, overrides = {}) {
  const snapshot = buildWatchlistSnapshot(report, overrides);
  const decision = evaluateWatchlistPrice(snapshot, snapshot.quotePrice);
  const checkDecision = Number.isFinite(snapshot.checkPrice)
    ? evaluateWatchlistPrice(snapshot, snapshot.checkPrice)
    : null;

  return {
    id: overrides.id || watchlistId(report.symbol),
    symbol: report.symbol,
    name: overrides.name || report.longName || report.symbol,
    manualName: overrides.name || "",
    manualBuyPrice: Number.isFinite(overrides.buyPrice) ? overrides.buyPrice : null,
    manualSellPrice: Number.isFinite(overrides.sellPrice) ? overrides.sellPrice : null,
    currency: report.currency,
    quotePrice: snapshot.quotePrice,
    buyLow: snapshot.buyLow,
    buyHigh: snapshot.buyHigh,
    sellPrice: snapshot.sellPrice,
    targetOne: snapshot.targetOne,
    targetTwo: snapshot.targetTwo,
    stopLoss: snapshot.stopLoss,
    breakoutTrigger: snapshot.breakoutTrigger,
    checkPrice: snapshot.checkPrice,
    oneWeekVolume: oneWeekVolume(report),
    avgDailyVolumeWeek: averageDailyVolume(report, 5),
    analystTarget: snapshot.analystTarget,
    recommendation: snapshot.recommendation,
    planHorizon: snapshot.planHorizon,
    planTimeframe: snapshot.planTimeframe,
    planSetup: snapshot.planSetup,
    planScore: snapshot.planScore,
    riskReward: snapshot.riskReward,
    volumeRatio: snapshot.volumeRatio,
    avgVolume20: snapshot.avgVolume20,
    sourceSummary: snapshot.sourceSummary,
    overallScore: snapshot.overallScore,
    technicalScore: snapshot.technicalScore,
    fundamentalScore: snapshot.fundamentalScore,
    eventRiskScore: snapshot.eventRiskScore,
    qualityScore: snapshot.qualityScore,
    rowTone: decision.tone,
    indicatorLabel: decision.label,
    reason: decision.reason,
    checkResult: checkDecision ? checkDecision.fullText : "",
    updatedAt: new Date().toISOString()
  };
}

function buildWatchlistSnapshot(report, overrides = {}) {
  const levels = report.researchLevels || {};
  const fundamentals = report.fundamentals?.metrics || {};
  const bestPlan = bestWatchlistPlan(report.swingTradePlan);
  const buyLow = Number.isFinite(overrides.buyPrice)
    ? overrides.buyPrice
    : finiteOrClient(bestPlan?.entry?.low, levels.pullbackEntry?.low, report.quote?.price);
  const rawBuyHigh = Number.isFinite(overrides.buyPrice)
    ? overrides.buyPrice
    : finiteOrClient(bestPlan?.entry?.high, levels.pullbackEntry?.high, levels.pullbackEntry?.low, report.quote?.price);
  const buyHigh = Number.isFinite(buyLow) && Number.isFinite(rawBuyHigh)
    ? Math.max(buyLow, rawBuyHigh)
    : rawBuyHigh;
  const targets = Array.isArray(levels.targets) ? levels.targets.filter(Number.isFinite) : [];
  const planTargets = Array.isArray(bestPlan?.targets) ? bestPlan.targets.map((target) => target.price).filter(Number.isFinite) : [];
  const targetOne = Number.isFinite(overrides.sellPrice)
    ? overrides.sellPrice
    : finiteOrClient(planTargets[0], targets[0], fundamentals.targetMeanPrice, report.quote?.price);
  const targetTwo = finiteOrClient(planTargets[1], targets[1], fundamentals.targetMeanPrice, targetOne);
  const sellPrice = Number.isFinite(overrides.sellPrice)
    ? overrides.sellPrice
    : targetOne;
  return {
    currency: report.currency,
    quotePrice: report.quote?.price,
    buyLow,
    buyHigh,
    sellPrice,
    targetOne,
    targetTwo,
    stopLoss: finiteOrClient(bestPlan?.stopLoss, levels.invalidation),
    checkPrice: Number.isFinite(overrides.checkPrice) ? overrides.checkPrice : null,
    breakoutTrigger: finiteOrClient(bestPlan?.entry?.trigger, levels.breakoutTrigger),
    invalidation: finiteOrClient(levels.invalidation, bestPlan?.stopLoss),
    analystTarget: fundamentals.targetMeanPrice,
    recommendation: fundamentals.recommendationKey || "",
    planHorizon: bestPlan?.horizon || "",
    planTimeframe: bestPlan?.timeframe || "",
    planSetup: bestPlan?.setup || "",
    planScore: bestPlan?.score,
    riskReward: bestPlan?.riskReward,
    volumeRatio: report.technical?.indicators?.volumeRatio,
    avgVolume20: report.technical?.indicators?.avgVolume20,
    sourceSummary: watchlistSourceSummary(report),
    overallScore: report.scores?.overall,
    technicalScore: report.scores?.technical,
    fundamentalScore: report.scores?.fundamental,
    eventRiskScore: report.scores?.eventRisk,
    qualityScore: report.scores?.confidence
  };
}

function evaluateWatchlistPrice(snapshot, price) {
  const scoreOk = numericOr(snapshot.overallScore, 0) >= 58
    && numericOr(snapshot.technicalScore, 0) >= 55
    && numericOr(snapshot.fundamentalScore, 0) >= 45
    && numericOr(snapshot.qualityScore, 0) >= 50
    && numericOr(snapshot.eventRiskScore, 100) < 65;
  const planOk = numericOr(snapshot.planScore, snapshot.overallScore || 0) >= 50;
  const riskRewardOk = !Number.isFinite(snapshot.riskReward) || snapshot.riskReward >= 1.3;
  const volumeOk = !Number.isFinite(snapshot.volumeRatio) || snapshot.volumeRatio >= 1.1;
  const analystNote = analystTargetNote(snapshot.analystTarget, price, snapshot.currency);

  if (!Number.isFinite(price)) {
    return {
      tone: scoreOk ? "consider" : "watch",
      label: scoreOk ? "Consider" : "Watchlist",
      reason: scoreOk ? "Analysis is supportive, but price needs to be checked." : "Analysis is not strong enough without price confirmation.",
      fullText: "Enter a price to check buy/sell suitability."
    };
  }

  if (Number.isFinite(snapshot.stopLoss) && price <= snapshot.stopLoss) {
    const reason = `Below stop loss near ${formatMoney(snapshot.stopLoss, snapshot.currency)}; avoid fresh buy.`;
    return { tone: "avoid", label: "Avoid", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (Number.isFinite(snapshot.targetTwo) && price >= snapshot.targetTwo) {
    const reason = `Above final target near ${formatMoney(snapshot.targetTwo, snapshot.currency)}; protect gains instead of buying.`;
    return { tone: "avoid", label: "Sell zone", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (Number.isFinite(snapshot.targetOne) && price >= snapshot.targetOne) {
    const reason = `Good sell/trim zone near target 1 ${formatMoney(snapshot.targetOne, snapshot.currency)}.`;
    return { tone: "avoid", label: "Sell zone", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (!scoreOk || !planOk) {
    const reason = "Watchlist only: scores, quality, or event risk are not aligned enough.";
    return { tone: "watch", label: "Watchlist", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (!riskRewardOk) {
    const reason = `Watchlist only: risk/reward is weak at ${formatNumber(snapshot.riskReward)}:1.`;
    return { tone: "watch", label: "Weak R/R", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (Number.isFinite(snapshot.buyLow) && Number.isFinite(snapshot.buyHigh) && price >= snapshot.buyLow && price <= snapshot.buyHigh) {
    const reason = `Good buy zone: price is inside ${formatMoney(snapshot.buyLow, snapshot.currency)}-${formatMoney(snapshot.buyHigh, snapshot.currency)}.`;
    return { tone: "consider", label: "Consider", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (Number.isFinite(snapshot.buyLow) && price < snapshot.buyLow) {
    const reason = `Below planned entry zone; wait for price to reclaim ${formatMoney(snapshot.buyLow, snapshot.currency)} or form a new setup.`;
    return { tone: "watch", label: "Below entry", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (
    Number.isFinite(snapshot.buyLow)
    && Number.isFinite(snapshot.buyHigh)
    && price >= snapshot.buyLow * 0.98
    && price <= snapshot.buyHigh * 1.025
  ) {
    const reason = `Near buy zone; consider only if price holds above ${formatMoney(snapshot.buyHigh, snapshot.currency)}.`;
    return { tone: "consider", label: "Near buy", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  if (Number.isFinite(snapshot.breakoutTrigger) && price >= snapshot.breakoutTrigger && (!Number.isFinite(snapshot.sellPrice) || price < snapshot.sellPrice)) {
    const reason = volumeOk
      ? `Breakout entry is supported above trigger ${formatMoney(snapshot.breakoutTrigger, snapshot.currency)} with acceptable volume.`
      : `Breakout watch: price is above trigger ${formatMoney(snapshot.breakoutTrigger, snapshot.currency)}, but volume confirmation is weak.`;
    return { tone: volumeOk ? "consider" : "watch", label: volumeOk ? "Breakout" : "Volume watch", reason, fullText: `${reason} ${analystNote}`.trim() };
  }

  const reason = "Watchlist only: wait for pullback to buy zone or a cleaner breakout.";
  return { tone: "watch", label: "Watchlist", reason, fullText: `${reason} ${analystNote}`.trim() };
}

function analystTargetNote(target, price, currency) {
  if (!Number.isFinite(target) || !Number.isFinite(price) || price <= 0) {
    return "Analyst target was not available from the public data provider.";
  }
  const upside = ((target - price) / price) * 100;
  return `Analyst target ${formatMoney(target, currency)} implies ${formatPercentValue(upside)} from this price.`;
}

function bestWatchlistPlan(swingTradePlan) {
  const plans = Array.isArray(swingTradePlan?.plans) ? swingTradePlan.plans : [];
  if (!plans.length) {
    return null;
  }
  const bestHorizon = swingTradePlan?.suitability?.bestHorizon;
  return plans.find((plan) => plan.horizon === bestHorizon)
    || plans.reduce((best, plan) => numericOr(plan.score, 0) > numericOr(best.score, 0) ? plan : best, plans[0]);
}

function watchlistSourceSummary(report) {
  const labels = (report.references?.links || [])
    .map((link) => link.label || "")
    .filter(Boolean);
  const sourceNames = [];
  if ((report.source || "").includes("Screener")) {
    sourceNames.push("Screener");
  }
  if (labels.some((label) => label.includes("TradingView"))) {
    sourceNames.push("TradingView");
  }
  if (labels.some((label) => label.includes("NSE"))) {
    sourceNames.push("NSE");
  }
  if (labels.some((label) => label.includes("BSE"))) {
    sourceNames.push("BSE");
  }
  if (labels.some((label) => label.includes("Moneycontrol"))) {
    sourceNames.push("Moneycontrol");
  }
  return sourceNames.length ? [...new Set(sourceNames)].join(" / ") : report.source || "Public market data";
}

function renderWatchlist() {
  const items = loadWatchlistItems();
  watchlistRows.innerHTML = "";
  setText("#watchlistCount", `${items.length} saved`);

  if (!items.length) {
    watchlistRows.innerHTML = "<tr><td colspan=\"7\">No watchlist stocks saved yet.</td></tr>";
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.className = `watchlist-row is-${item.rowTone || "watch"}`;
    row.innerHTML = `
      <td>
        <span class="watchlist-indicator is-${escapeHtml(item.rowTone || "watch")}"></span>
        <strong>${escapeHtml(item.indicatorLabel || "Watchlist")}</strong>
        <small>${escapeHtml(item.reason || "")}</small>
      </td>
      <td>
        <strong>${escapeHtml(item.name || item.symbol)}</strong>
        <span>${escapeHtml(item.symbol)} · LTP ${formatMoney(item.quotePrice, item.currency)}</span>
        <small>${escapeHtml([item.planHorizon, item.planTimeframe, item.planSetup].filter(Boolean).join(" · ") || "Plan from latest analysis")}</small>
        <small>Sources: ${escapeHtml(item.sourceSummary || "Public market data")}</small>
      </td>
      <td>${formatBuyRange(item)}<span>Trigger ${formatMoney(item.breakoutTrigger, item.currency)}</span><span>Stop ${formatMoney(item.stopLoss, item.currency)} · ${item.manualBuyPrice ? "manual" : "plan"}</span></td>
      <td>${formatTargetRange(item)}<span>R/R ${formatRiskReward(item.riskReward)} · Score ${scoreText(item.planScore || item.overallScore || 0)}</span><span>${escapeHtml(item.recommendation ? `Analyst view: ${item.recommendation}` : "Plan target")}</span></td>
      <td>${formatLarge(item.oneWeekVolume, "")}<span>${formatLarge(item.avgDailyVolumeWeek, "")} avg/day</span><span>${formatVolumeConfirmation(item)}</span></td>
      <td>
        <div class="watchlist-price-check">
          <input data-watchlist-price="${escapeHtml(item.id)}" type="number" step="0.01" min="0" value="${Number.isFinite(item.checkPrice) ? item.checkPrice : ""}" placeholder="Enter price">
          <button type="button" data-check-watchlist="${escapeHtml(item.id)}">Check</button>
        </div>
        <small>${escapeHtml(item.checkResult || "Enter a price to test buy/sell suitability.")}</small>
      </td>
      <td>
        <button class="link-button" type="button" data-refresh-watchlist="${escapeHtml(item.id)}">Refresh</button>
        <button class="link-button" type="button" data-delete-watchlist="${escapeHtml(item.id)}">Delete</button>
      </td>
    `;
    watchlistRows.appendChild(row);
  }
}

async function checkWatchlistPrice(id) {
  hideWatchlistError();
  const items = loadWatchlistItems();
  const item = items.find((entry) => String(entry.id) === String(id));
  const input = watchlistRows.querySelector(`[data-watchlist-price="${id}"]`);
  if (!item || !input) {
    return;
  }
  const price = Number.parseFloat(input.value);
  if (!Number.isFinite(price) || price <= 0) {
    showWatchlistError("Enter a valid price to check.");
    return;
  }
  const decision = evaluateWatchlistPrice(item, price);
  item.checkPrice = price;
  item.checkResult = decision.fullText;
  try {
    await updateSavedWatchlistItem(id, { checkPrice: price });
  } catch (error) {
    showWatchlistError(error.message);
  }
  saveWatchlistItems(items);
  renderWatchlist();
}

async function deleteWatchlistItem(id) {
  hideWatchlistError();
  try {
    await deleteSavedWatchlistItem(id);
  } catch (error) {
    showWatchlistError(error.message);
  }
  saveWatchlistItems(loadWatchlistItems().filter((item) => String(item.id) !== String(id)));
  renderWatchlist();
}

function saveWatchlistItem(item) {
  const items = loadWatchlistItems();
  const existingIndex = items.findIndex((entry) => entry.symbol === item.symbol);
  if (existingIndex >= 0) {
    items[existingIndex] = { ...item, id: items[existingIndex].id };
  } else {
    items.unshift(item);
  }
  saveWatchlistItems(items);
}

function loadWatchlistItems() {
  return Array.isArray(latestWatchlistItems) ? latestWatchlistItems : [];
}

function saveWatchlistItems(items) {
  latestWatchlistItems = items;
}

async function fetchSavedWatchlistItems() {
  const response = await fetch("/api/watchlist");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Could not load watchlist.");
  }
  return payload.results || [];
}

async function persistWatchlistItem(item) {
  const response = await fetch("/api/watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      symbol: item.symbol,
      stockName: item.manualName || item.name || "",
      buyPrice: item.manualBuyPrice,
      sellPrice: item.manualSellPrice,
      checkPrice: item.checkPrice
    })
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Could not save watchlist item.");
  }
  item.id = payload.id;
  return payload;
}

async function updateSavedWatchlistItem(id, fields) {
  const response = await fetch(`/api/watchlist/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields)
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Could not update watchlist item.");
  }
  return payload;
}

async function deleteSavedWatchlistItem(id) {
  const response = await fetch(`/api/watchlist/${encodeURIComponent(id)}`, { method: "DELETE" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Could not delete watchlist item.");
  }
  return payload;
}

function savedWatchlistToDisplayItem(item) {
  return {
    id: item.id,
    symbol: item.symbol,
    name: item.stockName || item.symbol,
    manualName: item.stockName || "",
    manualBuyPrice: item.buyPrice,
    manualSellPrice: item.sellPrice,
    checkPrice: item.checkPrice,
    currency: "",
    quotePrice: null,
    buyLow: item.buyPrice,
    buyHigh: item.buyPrice,
    sellPrice: item.sellPrice,
    targetOne: item.sellPrice,
    targetTwo: item.sellPrice,
    oneWeekVolume: null,
    avgDailyVolumeWeek: null,
    rowTone: "watch",
    indicatorLabel: "Needs refresh",
    reason: "Refresh to calculate live trade levels and volume.",
    checkResult: item.checkPrice ? "Refresh to check this price against live levels." : "",
    updatedAt: item.updatedAt
  };
}

function showWatchlistError(message) {
  watchlistError.textContent = message;
  watchlistError.classList.remove("is-hidden");
}

function hideWatchlistError() {
  watchlistError.classList.add("is-hidden");
}

function setWatchlistLoading(isLoading) {
  watchlistLoading.classList.toggle("is-hidden", !isLoading);
}

function inputNumber(selector) {
  const value = Number.parseFloat(document.querySelector(selector).value);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function oneWeekVolume(report) {
  return (report.series || [])
    .slice(-5)
    .reduce((total, item) => total + (Number.isFinite(item.volume) ? item.volume : 0), 0);
}

function averageDailyVolume(report, days) {
  const rows = (report.series || []).slice(-days);
  if (!rows.length) {
    return 0;
  }
  return rows.reduce((total, item) => total + (Number.isFinite(item.volume) ? item.volume : 0), 0) / rows.length;
}

function formatBuyRange(item) {
  if (!Number.isFinite(item.buyLow) && !Number.isFinite(item.buyHigh)) {
    return "n/a";
  }
  if (!Number.isFinite(item.buyLow) || item.buyLow === item.buyHigh) {
    return formatMoney(item.buyHigh, item.currency);
  }
  return `${formatMoney(item.buyLow, item.currency)} - ${formatMoney(item.buyHigh, item.currency)}`;
}

function formatTargetRange(item) {
  if (Number.isFinite(item.targetOne) && Number.isFinite(item.targetTwo) && item.targetOne !== item.targetTwo) {
    return `${formatMoney(item.targetOne, item.currency)} / ${formatMoney(item.targetTwo, item.currency)}`;
  }
  return formatMoney(item.sellPrice || item.targetOne, item.currency);
}

function formatRiskReward(value) {
  return Number.isFinite(value) ? `${formatNumber(value)}:1` : "n/a";
}

function formatVolumeConfirmation(item) {
  if (!Number.isFinite(item.volumeRatio)) {
    return "Volume confirmation n/a";
  }
  return `Volume ${formatNumber(item.volumeRatio)}x 20D avg`;
}

function finiteOrClient(...values) {
  return values.find((value) => Number.isFinite(value)) ?? null;
}

function watchlistId(symbol) {
  const suffix = window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${symbol}-${suffix}`;
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
  renderMarketClock(report.marketClock);

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
  renderLatestCandle(report.technical.latestCandle, currency);
  renderOpenInterest(report.openInterest, currency);
  renderTechnical(report.technical, currency);
  renderFundamentals(report.fundamentals.metrics, report.fundamentals.signals, currency);
  renderGrowthDrivers(report.growthDrivers, currency);
  renderQuality(report.quality);
  renderScenarios(report.scenarios, currency);
  renderSwingTradePlan(report.swingTradePlan, currency);
  renderReferences(report.references);
  renderSectionSummaries(report, currency);
  redrawChart();
}

function renderSectionSummaries(report, currency) {
  const eventRisk = report.scores?.eventRisk;
  const qualityScore = report.scores?.confidence;
  const overallGate = Math.min(
    numericOr(report.scores?.overall, 0),
    numericOr(qualityScore, 0),
    100 - numericOr(eventRisk, 100)
  );

  setText(
    "#reportVerdict",
    decisionLine(
      overallGate,
      "overall score, data quality, and event risk are aligned.",
      "wait for cleaner confirmation from score, quality, or event risk."
    )
  );
  setText("#ownershipSectionSummary", ownershipDecision(report.growthDrivers, report.scores?.fundamental));
  setText(
    "#chartSectionSummary",
    decisionLine(
      report.scores?.technical,
      "trend and momentum support an entry only near the planned levels.",
      "wait for a breakout, pullback, or moving-average repair."
    )
  );
  setText("#levelsNote", levelsDecision(report.researchLevels, report.scores?.overall, eventRisk, currency));
  setText(
    "#technicalSectionSummary",
    decisionLine(
      report.scores?.technical,
      "technical setup is supportive if price respects support and volume confirms.",
      "technical setup is not clean enough yet; keep it on watchlist."
    )
  );
  setText(
    "#fundamentalSectionSummary",
    decisionLine(
      report.scores?.fundamental,
      "fundamental profile is strong enough to support a position-sized entry.",
      "fundamentals are mixed or incomplete, so keep it on watchlist."
    )
  );
  setText("#growthDriverText", growthDecision(report.growthDrivers, report.scores?.fundamental));
  setText("#qualityText", qualityDecision(report.quality));
  setText("#scenarioSectionSummary", scenarioDecision(report.scenarios, report.scores?.overall, eventRisk));
  setText("#crossCheckSummary", "Watchlist discipline: verify live chart, filings, results, and news before treating this as investable.");
}

function decisionLine(score, positiveReason, watchReason) {
  if (numericOr(score, 0) >= 65) {
    return `Good to consider: ${positiveReason}`;
  }
  return `Watchlist only: ${watchReason}`;
}

function ownershipDecision(growthDrivers, fundamentalScore) {
  const rows = growthDrivers?.ownership?.rows || [];
  const institutionalRows = rows.filter((row) => ["Promoters", "FIIs", "DIIs"].includes(row.name));
  const improvingCount = institutionalRows.filter((row) => numericOr(row.changePoints, 0) > 0).length;
  if (numericOr(fundamentalScore, 0) >= 60 && improvingCount >= 2) {
    return "Good to consider: ownership trend is supportive with improving key holder participation.";
  }
  return "Watchlist only: ownership trend is mixed, incomplete, or needs latest filing verification.";
}

function levelsDecision(levels, overallScore, eventRisk, currency) {
  const buyLow = levels?.pullbackEntry?.low;
  const buyHigh = levels?.pullbackEntry?.high;
  const target = Array.isArray(levels?.targets) ? levels.targets[0] : null;
  if (numericOr(overallScore, 0) >= 65 && numericOr(eventRisk, 100) < 65) {
    return `Good to consider only near ${formatMoney(buyLow, currency)}-${formatMoney(buyHigh, currency)}; first sell zone is ${formatMoney(target, currency)}.`;
  }
  return `Watchlist only: wait for price near ${formatMoney(buyLow, currency)}-${formatMoney(buyHigh, currency)} or a cleaner breakout.`;
}

function growthDecision(growthDrivers, fundamentalScore) {
  const catalysts = growthDrivers?.catalysts || [];
  const budgetImpacts = growthDrivers?.budgetImpacts || [];
  const hasPositiveContext = catalysts.length || budgetImpacts.length || growthDrivers?.sectorAnalysis?.available;
  if (numericOr(fundamentalScore, 0) >= 60 && hasPositiveContext) {
    return "Good to consider: growth/catalyst context supports the fundamental story, but verify latest filings.";
  }
  return "Watchlist only: catalyst support is limited or not strong enough for an investment call.";
}

function qualityDecision(quality) {
  if (numericOr(quality?.score, 0) >= 75) {
    return "Good to consider: data coverage is strong enough for a useful research view.";
  }
  return "Watchlist only: data quality needs manual cross-checking before acting.";
}

function scenarioDecision(scenarios, overallScore, eventRisk) {
  if (numericOr(overallScore, 0) >= 65 && numericOr(eventRisk, 100) < 65 && scenarios?.stance !== "Wait for repair") {
    return "Good to consider: bull/base scenarios are usable if triggers confirm.";
  }
  return "Watchlist only: scenario risk is high until price confirms the trigger.";
}

function renderMarketClock(clock) {
  if (!marketClockEl || !marketClockTime || !marketClockStatus || !marketClockDetail) {
    return;
  }
  if (marketClockTimer) {
    clearInterval(marketClockTimer);
    marketClockTimer = null;
  }
  if (!clock) {
    marketClockTime.textContent = "--:--";
    marketClockStatus.textContent = "Market status";
    marketClockDetail.textContent = "Session timing is not available for this stock.";
    setMarketClockClass("closed");
    return;
  }

  const tick = () => updateMarketClock(clock);
  tick();
  marketClockTimer = setInterval(tick, 1000);
}

function updateMarketClock(clock) {
  const now = new Date();
  const state = resolveMarketClockState(clock, now);
  marketClockTime.textContent = formatZonedClock(now, clock.timezone, true);
  marketClockStatus.textContent = state.label;
  marketClockDetail.textContent = state.detail;
  setMarketClockClass(state.className);
}

function resolveMarketClockState(clock, now) {
  const openAt = parseClockDate(clock.sessionOpenAt);
  const closeAt = parseClockDate(clock.sessionCloseAt);
  const nextOpenAt = parseClockDate(clock.nextOpenAt);
  const exchangeLabel = clock.exchange || "market";
  const holidayName = clock.holidayName || clock.message || "market holiday";

  if (openAt && closeAt && now >= openAt && now < closeAt && !clock.isHoliday) {
    return {
      className: "open",
      label: "Market Open",
      detail: `Closes in ${formatClockDuration(closeAt - now)} at ${formatZonedClock(closeAt, clock.timezone)} (${exchangeLabel})`
    };
  }

  if (clock.status === "open" && !closeAt) {
    return {
      className: "open",
      label: clock.statusLabel || "Market Open",
      detail: clock.message || `${exchangeLabel} reports the market is open; session close time is unavailable.`
    };
  }

  if (clock.isHoliday && (!nextOpenAt || now < nextOpenAt)) {
    const nextDetail = nextOpenAt
      ? `Next open ${formatZonedDateTime(nextOpenAt, clock.timezone)}`
      : "Next open will update when the exchange calendar refreshes.";
    return {
      className: "holiday",
      label: "Market Holiday",
      detail: `${holidayName}. ${nextDetail}`
    };
  }

  if (nextOpenAt && now < nextOpenAt) {
    return {
      className: "closed",
      label: "Market Closed",
      detail: `Opens in ${formatClockDuration(nextOpenAt - now)} at ${formatZonedDateTime(nextOpenAt, clock.timezone)}`
    };
  }

  if (clock.status === "open" && closeAt && now < closeAt) {
    return {
      className: "open",
      label: clock.statusLabel || "Market Open",
      detail: `Closes in ${formatClockDuration(closeAt - now)} at ${formatZonedClock(closeAt, clock.timezone)}`
    };
  }

  return {
    className: "closed",
    label: clock.statusLabel || "Market Closed",
    detail: clock.message || "Regular trading session is closed."
  };
}

function setMarketClockClass(status) {
  marketClockEl.classList.remove("is-open", "is-closed", "is-holiday");
  marketClockEl.classList.add(`is-${status}`);
}

function parseClockDate(value) {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatZonedClock(date, timezone, includeSeconds = false) {
  if (!timezone) {
    return includeSeconds ? date.toLocaleTimeString() : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    second: includeSeconds ? "2-digit" : undefined,
    hour12: true,
    timeZoneName: includeSeconds ? "short" : undefined
  }).format(date);
}

function formatZonedDateTime(date, timezone) {
  if (!timezone) {
    return date.toLocaleString();
  }
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: timezone,
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
    timeZoneName: "short"
  }).format(date);
}

function formatClockDuration(milliseconds) {
  const totalMinutes = Math.max(0, Math.ceil(milliseconds / 60000));
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  const parts = [];
  if (days) {
    parts.push(`${days}d`);
  }
  if (hours || days) {
    parts.push(`${hours}h`);
  }
  parts.push(`${minutes}m`);
  return parts.join(" ");
}

function renderLatestCandle(candle, currency) {
  const container = document.querySelector("#latestCandleReadout");
  if (!container) {
    return;
  }

  if (!candle || !candle.available) {
    container.innerHTML = "<p class=\"muted\">Latest daily candle pattern is not available.</p>";
    return;
  }

  const directionClass = candle.direction === "Bullish" ? "positive" : candle.direction === "Bearish" ? "negative" : "muted";
  container.innerHTML = `
    <article class="candle-pattern-card">
      <div>
        <span>${escapeHtml(candle.timeframe || "Daily")} candle · ${escapeHtml(candle.date || "latest")}</span>
        <strong>${escapeHtml(candle.pattern || "Candle")}</strong>
      </div>
      <em class="${directionClass}">${escapeHtml(candle.bias || candle.direction || "Neutral")}</em>
      <p>${escapeHtml(candle.meaning || candle.summary || "")}</p>
      <div class="candle-next-grid">
        <span>Next candle read <strong>${escapeHtml(candle.nextCandleExpectation || "Wait for confirmation.")}</strong></span>
        <span>Confirmation <strong>${formatMoney(candle.confirmationLevel, currency)}</strong></span>
        <span>Invalidation <strong>${formatMoney(candle.invalidationLevel, currency)}</strong></span>
        <span>Body / wicks <strong>${formatPercentValue(candle.bodyPercent)} body · U ${formatPercentValue(candle.upperWickPercent)} · L ${formatPercentValue(candle.lowerWickPercent)}</strong></span>
      </div>
    </article>
  `;
}

function renderOpenInterest(openInterest, currency) {
  if (!openInterestPanel || !openInterestSummary || !openInterestMetrics || !openInterestRows) {
    return;
  }

  const periods = openInterest?.periods || {};
  let activeKey = selectedOpenInterestPeriod;
  let activePeriod = periods[activeKey]?.available ? periods[activeKey] : null;
  if (!activePeriod) {
    const fallback = Object.entries(periods).find(([, period]) => period?.available);
    if (fallback) {
      activeKey = fallback[0];
      activePeriod = fallback[1];
    }
  }

  for (const tab of openInterestTabs) {
    const key = tab.dataset.oiPeriod;
    tab.classList.toggle("is-active", key === (activePeriod ? activeKey : "day"));
    tab.disabled = !openInterest?.available || !periods[key]?.available;
  }

  if (!openInterest?.available || !activePeriod) {
    openInterestPanel.classList.add("is-unavailable");
    if (openInterestSource) {
      openInterestSource.textContent = openInterest?.source || "NSE India option-chain equities";
    }
    openInterestSummary.textContent = openInterest?.summary || "Open interest is not available for this stock.";
    if (openInterestChart) {
      openInterestChart.innerHTML = "";
    }
    openInterestMetrics.innerHTML = "";
    openInterestRows.innerHTML = `
      <tr>
        <td colspan="5">No option-chain OI data was returned for this symbol.</td>
      </tr>
    `;
    return;
  }

  openInterestPanel.classList.remove("is-unavailable");
  if (openInterestSource) {
    const expiryText = (activePeriod.expiryDates || []).map((item) => item.label).join(", ");
    const timestamp = openInterest.timestamp ? ` · ${escapeHtml(openInterest.timestamp)}` : "";
    openInterestSource.textContent = `${openInterest.source || "NSE option chain"}${timestamp}${expiryText ? ` · Expiry ${expiryText}` : ""}`;
  }
  openInterestSummary.textContent = activePeriod.summary || openInterest.summary || "";

  if (activePeriod.aggregateOnly) {
    if (openInterestChart) {
      openInterestChart.innerHTML = renderAggregateOiChart(activePeriod);
    }
    openInterestMetrics.innerHTML = [
      oiMetric("Latest OI", formatLarge(activePeriod.totalOi, ""), "Contracts", activePeriod.changeOi),
      oiMetric("Previous OI", formatLarge(activePeriod.previousOi, ""), "Previous trade day", 0),
      oiMetric("Change in OI", formatSignedLarge(activePeriod.changeOi), `${formatPercentValue(activePeriod.changePercent)} change`, activePeriod.changeOi),
      oiMetric("Volume", formatLarge(activePeriod.volume, ""), "Contracts traded", activePeriod.volume),
      oiMetric("Underlying", formatMoney(activePeriod.underlyingValue, currency), activePeriod.bias || "OI bias", activePeriod.changeOi),
      oiMetric("Options value", formatLarge(activePeriod.optionsValue, "INR"), "NSE turnover field", activePeriod.changeOi)
    ].join("");
    openInterestRows.innerHTML = `
      <tr>
        <td>Aggregate</td>
        <td colspan="4">
          ${escapeHtml(activePeriod.bias || "OI view")}
          <span>Latest OI ${formatLarge(activePeriod.totalOi, "")} · Previous ${formatLarge(activePeriod.previousOi, "")} · Change ${formatSignedLarge(activePeriod.changeOi)}</span>
          <span>Full call/put strike OI was not returned by NSE for this request; Week, Month, and Quarter views need full option-chain rows.</span>
        </td>
      </tr>
    `;
    return;
  }

  if (openInterestChart) {
    openInterestChart.innerHTML = renderOiChart(activePeriod, currency);
  }

  const netChange = (activePeriod.totalPutChangeOi || 0) - (activePeriod.totalCallChangeOi || 0);
  const pcrTone = Number.isFinite(activePeriod.pcr) ? activePeriod.pcr - 1 : 0;
  openInterestMetrics.innerHTML = [
    oiMetric("Call OI", formatLarge(activePeriod.totalCallOi, ""), `Chg ${formatSignedLarge(activePeriod.totalCallChangeOi)}`, activePeriod.totalCallChangeOi),
    oiMetric("Put OI", formatLarge(activePeriod.totalPutOi, ""), `Chg ${formatSignedLarge(activePeriod.totalPutChangeOi)}`, activePeriod.totalPutChangeOi),
    oiMetric("PCR", formatNumber(activePeriod.pcr), activePeriod.bias || "OI bias", pcrTone),
    oiMetric("Call volume", formatLarge(activePeriod.totalCallVolume, ""), activePeriod.volumeBias || "Volume split", activePeriod.totalCallVolume - activePeriod.totalPutVolume),
    oiMetric("Put volume", formatLarge(activePeriod.totalPutVolume, ""), `Volume PCR ${formatNumber(activePeriod.volumePcr)}`, activePeriod.totalPutVolume - activePeriod.totalCallVolume),
    oiMetric("Max Call OI", formatMoney(activePeriod.maxCallOiStrike, currency), "Likely resistance", -1),
    oiMetric("Max Put OI", formatMoney(activePeriod.maxPutOiStrike, currency), "Likely support", 1),
    oiMetric("Max Pain", formatMoney(activePeriod.maxPain, currency), `Net chg ${formatSignedLarge(netChange)}`, netChange)
  ].join("");

  const rows = activePeriod.rows || [];
  openInterestRows.innerHTML = rows.length ? rows.map((row) => `
    <tr>
      <td>${formatMoney(row.strike, currency)}<span>Total ${formatLarge(row.totalOi, "")}</span></td>
      <td>${formatLarge(row.callOi, "")}<span class="${changeClass(row.callChangeOi)}">${formatSignedLarge(row.callChangeOi)}</span><span>Vol ${formatLarge(row.callVolume, "")}</span></td>
      <td>${formatLarge(row.putOi, "")}<span class="${changeClass(row.putChangeOi)}">${formatSignedLarge(row.putChangeOi)}</span><span>Vol ${formatLarge(row.putVolume, "")}</span></td>
      <td>${formatNumber(row.pcrAtStrike)}<span>Vol PCR ${formatNumber(row.volumePcrAtStrike)}</span><span>Net ${formatSignedLarge(row.netChangeOi)}</span></td>
      <td>${escapeHtml(row.bias || "Balanced")}<span>${escapeHtml(row.volumeBias || "")}</span></td>
    </tr>
  `).join("") : `
    <tr>
      <td colspan="5">No strike-level OI rows were available for this period.</td>
    </tr>
  `;
}

function renderAggregateOiChart(period) {
  const previous = Math.max(0, period.previousOi || 0);
  const latest = Math.max(0, period.totalOi || 0);
  const change = Math.max(0, Math.abs(period.changeOi || 0));
  const maxValue = Math.max(previous, latest, change, 1);
  return `
    <section class="oi-chart-card">
      <div class="oi-chart-head">
        <strong>Aggregate OI movement</strong>
        <span>${escapeHtml(period.volumeSummary || "Call/put volume split unavailable.")}</span>
      </div>
      ${oiSingleBar("Previous OI", previous, maxValue, "neutral")}
      ${oiSingleBar("Latest OI", latest, maxValue, period.changeOi >= 0 ? "put" : "call")}
      ${oiSingleBar("OI change", change, maxValue, period.changeOi >= 0 ? "put" : "call", formatSignedLarge(period.changeOi))}
    </section>
  `;
}

function renderOiChart(period, currency) {
  const oiTotal = Math.max((period.totalCallOi || 0) + (period.totalPutOi || 0), 1);
  const volumeTotal = Math.max((period.totalCallVolume || 0) + (period.totalPutVolume || 0), 1);
  const rows = (period.rows || []).slice(0, 10);
  const maxStrikeValue = Math.max(...rows.map((row) => Math.max(row.callOi || 0, row.putOi || 0, row.callVolume || 0, row.putVolume || 0)), 1);

  return `
    <section class="oi-chart-card">
      <div class="oi-chart-head">
        <strong>Call vs put positioning</strong>
        <span>${escapeHtml(period.volumeSummary || "")}</span>
      </div>
      ${oiSplitBar("Open interest", period.totalCallOi || 0, period.totalPutOi || 0, oiTotal)}
      ${oiSplitBar("Traded volume", period.totalCallVolume || 0, period.totalPutVolume || 0, volumeTotal)}
    </section>
    <section class="oi-chart-card">
      <div class="oi-chart-head">
        <strong>Strike OI and volume</strong>
        <span>Top strikes by open interest</span>
      </div>
      <div class="oi-strike-chart">
        ${rows.map((row) => `
          <article class="oi-strike-row">
            <strong>${formatMoney(row.strike, currency)}</strong>
            <div>
              ${oiDualMiniBar("OI", row.callOi || 0, row.putOi || 0, maxStrikeValue)}
              ${oiDualMiniBar("Vol", row.callVolume || 0, row.putVolume || 0, maxStrikeValue)}
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function oiSplitBar(label, callValue, putValue, total) {
  const callPercent = clampPercent(callValue / total * 100);
  const putPercent = clampPercent(putValue / total * 100);
  return `
    <div class="oi-split-row">
      <div>
        <strong>${escapeHtml(label)}</strong>
        <span>Calls ${formatLarge(callValue, "")} · Puts ${formatLarge(putValue, "")}</span>
      </div>
      <div class="oi-split-bar" aria-label="${escapeHtml(label)} call put split">
        <span class="oi-bar oi-call" style="width:${callPercent}%"></span>
        <span class="oi-bar oi-put" style="width:${putPercent}%"></span>
      </div>
    </div>
  `;
}

function oiSingleBar(label, value, maxValue, tone, displayValue = formatLarge(value, "")) {
  return `
    <div class="oi-single-row">
      <span>${escapeHtml(label)}</span>
      <div class="oi-single-track">
        <i class="oi-bar oi-${tone}" style="width:${clampPercent(value / maxValue * 100)}%"></i>
      </div>
      <strong>${escapeHtml(displayValue)}</strong>
    </div>
  `;
}

function oiDualMiniBar(label, callValue, putValue, maxValue) {
  return `
    <div class="oi-mini-row">
      <span>${escapeHtml(label)}</span>
      <div class="oi-mini-bars">
        <i class="oi-bar oi-call" style="width:${clampPercent(callValue / maxValue * 100)}%"></i>
        <i class="oi-bar oi-put" style="width:${clampPercent(putValue / maxValue * 100)}%"></i>
      </div>
      <small>C ${formatLarge(callValue, "")} / P ${formatLarge(putValue, "")}</small>
    </div>
  `;
}

function clampPercent(value) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, value));
}

function oiMetric(label, value, note, toneValue) {
  const toneClass = changeClass(toneValue);
  return `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small class="${toneClass}">${escapeHtml(note || "")}</small>
    </article>
  `;
}

function openExpandedChart() {
  if (!latestReport) {
    return;
  }
  const dialog = ensureChartDialog();
  dialog.classList.remove("is-hidden");
  document.body.classList.add("dialog-open");
  requestAnimationFrame(() => {
    drawChart(latestReport.series, latestReport.currency, latestReport.technical.levels, 0, expandedChartCanvas);
    expandedChartCanvas.focus();
  });
}

function ensureChartDialog() {
  if (chartDialogEl) {
    return chartDialogEl;
  }

  chartDialogEl = document.createElement("section");
  chartDialogEl.id = "chartDialog";
  chartDialogEl.className = "chart-dialog is-hidden";
  chartDialogEl.setAttribute("role", "dialog");
  chartDialogEl.setAttribute("aria-modal", "true");
  chartDialogEl.setAttribute("aria-labelledby", "chartDialogTitle");
  chartDialogEl.innerHTML = `
    <article class="chart-modal">
      <div class="chart-modal-head">
        <div>
          <p class="eyebrow">One-year candlestick chart</p>
          <h3 id="chartDialogTitle">Expanded Chart</h3>
        </div>
        <button type="button" id="chartDialogClose" class="suggestion-close">Close</button>
      </div>
      <canvas id="expandedPriceChart" aria-label="Expanded one-year candlestick price chart" tabindex="0"></canvas>
    </article>
  `;
  document.body.appendChild(chartDialogEl);
  expandedChartCanvas = chartDialogEl.querySelector("#expandedPriceChart");
  chartDialogEl.querySelector("#chartDialogClose").addEventListener("click", closeExpandedChart);
  chartDialogEl.addEventListener("click", (event) => {
    if (event.target === chartDialogEl) {
      closeExpandedChart();
    }
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !chartDialogEl.classList.contains("is-hidden")) {
      closeExpandedChart();
    }
  });
  window.addEventListener("resize", () => {
    if (latestReport && chartDialogEl && !chartDialogEl.classList.contains("is-hidden")) {
      scheduleChartRedraw();
    }
  });
  return chartDialogEl;
}

function closeExpandedChart() {
  if (!chartDialogEl) {
    return;
  }
  chartDialogEl.classList.add("is-hidden");
  if (!suggestionDialogEl || suggestionDialogEl.classList.contains("is-hidden")) {
    document.body.classList.remove("dialog-open");
  }
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
  const refreshText = data.refreshing ? " · detailed scan refreshing" : "";
  const detailText = data.detailGeneratedAt ? ` · details ${formatDateTime(data.detailGeneratedAt)}` : "";
  document.querySelector("#monitorGenerated").textContent = `Live ${formatDateTime(data.generatedAt)} from ${data.source}${detailText}${refreshText}`;
  document.querySelector("#monitorNote").textContent = data.note;
  const primaryUniverse = data.primaryScanUniverse || {};
  const primaryLabel = primaryUniverse.label || "Nifty 500 only";
  const scannedCount = data.scannedCount || 0;
  const universeCount = primaryUniverse.count || 0;
  const scanText = universeCount > scannedCount ? `${scannedCount}/${universeCount} scanned` : `${scannedCount} scanned`;
  document.querySelector("#scanCount").textContent = `${primaryLabel} · ${scanText}`;
  document.querySelector("#highVolumeCount").textContent = `${data.activityScannedCount || 0} scanned`;
  document.querySelector("#catalystCount").textContent = `${(data.orderCatalysts || []).length} found`;
  renderNseSnapshot(data.nseSnapshot || {});
  renderMoneycontrolSectorAnalysis(data.moneycontrolSectorAnalysis || {});
  renderSectorStockPerformance(data.sectorStockPerformance || {});
  renderCommodities(data.commodities || [], data.usdInr || {});
  renderMentionedStockHeatmap(data || {});
  renderBreakoutRows(data.breakoutCandidates || []);
  renderHighVolumeRows(data.highVolumeCandidates || []);
  renderOrderCatalystRows(data.orderCatalysts || []);
  annotateMonitorTableCells();
  setupMonitorCollapsibles();
}

function monitorRefreshing() {
  return Boolean(latestMonitor?.refreshing);
}

function monitorLoadingText(fallback) {
  return monitorRefreshing()
    ? "Refreshing live data; cached details stay visible until this provider returns fresh rows."
    : fallback;
}

function setMonitorPane(pane) {
  selectedMonitorPane = pane === "details" ? "details" : "primary";
  for (const button of monitorPaneButtons) {
    const isActive = button.dataset.monitorPaneButton === selectedMonitorPane;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  }
  for (const section of monitorSections) {
    section.classList.toggle("is-hidden", section.dataset.monitorSection !== selectedMonitorPane);
  }
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
    setMonitorPanelCollapsed(panel, button, true);
    button.addEventListener("click", () => {
      setMonitorPanelCollapsed(panel, button, !panel.classList.contains("is-collapsed"));
    });

    header.appendChild(button);
    panel.dataset.collapsibleReady = "1";
    panel.dataset.collapsibleIndex = String(index);
  });
}

function setMonitorPanelCollapsed(panel, button, collapsed) {
  panel.classList.toggle("is-collapsed", collapsed);
  button.setAttribute("aria-expanded", collapsed ? "false" : "true");
  button.setAttribute("aria-label", collapsed ? "Expand section" : "Collapse section");
  button.title = collapsed ? "Expand section" : "Collapse section";
  button.textContent = collapsed ? "+" : "−";
}

function annotateMonitorTableCells() {
  document.querySelectorAll("#monitorContent .monitor-table").forEach((table) => {
    const headers = Array.from(table.querySelectorAll("thead th")).map((header) => header.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((row) => {
      const cells = Array.from(row.querySelectorAll("td"));
      if (cells.length === 1 && cells[0].hasAttribute("colspan")) {
        cells[0].dataset.fullRow = "true";
        cells[0].removeAttribute("data-label");
        return;
      }
      cells.forEach((cell, index) => {
        cell.dataset.label = headers[index] || "";
        delete cell.dataset.fullRow;
      });
    });
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
    const status = monitorRefreshing() ? "Refreshing" : "Unavailable";
    setText("#nseSnapshotStatus", status);
    statusGrid.innerHTML = `<article class="market-mini-card"><span>NSE snapshot</span><strong>${status}</strong><small>${escapeHtml(snapshot.error || monitorLoadingText("NSE did not return market data."))}</small></article>`;
    emptyTable("#nseGainerRows", 5, monitorLoadingText("NIFTY 50 gainers are unavailable."));
    emptyTable("#nseLoserRows", 5, monitorLoadingText("NIFTY 50 losers are unavailable."));
    emptyTable("#nseActiveRows", 5, monitorLoadingText("NSE most-active data is unavailable."));
    emptyTable("#nseHighRows", 5, monitorLoadingText("NSE 52-week high and price-band data is unavailable."));
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

  const topGainers = snapshot.topGainers || [];
  const topLosers = snapshot.topLosers || [];
  setText("#nseGainerCount", nseMoverCountLabel(topGainers, "gainers"));
  setText("#nseLoserCount", nseMoverCountLabel(topLosers, "losers"));
  setText("#nseActiveCount", `${(snapshot.mostActive || []).length} shown`);
  renderNseMoverRows(
    "#nseGainerRows",
    topGainers,
    monitorLoadingText("No NIFTY 50 gainers were returned."),
    snapshot.topGainersNote || ""
  );
  renderNseMoverRows(
    "#nseLoserRows",
    topLosers,
    monitorLoadingText("No NIFTY 50 losers were returned."),
    snapshot.topLosersNote || ""
  );
  renderNseActiveRows(snapshot.mostActive || []);
  renderNseHighAndBandRows(snapshot.weekHighs || [], snapshot.priceBands || {});
}

function nseMoverCountLabel(items, label) {
  return items.length ? `NIFTY 50 · ${items.length} shown` : `NIFTY 50 · no ${label}`;
}

function renderSectorStockPerformance(report) {
  const rowsEl = document.querySelector("#sectorStockPerformanceRows");
  const heatmapEl = document.querySelector("#sectorStockPerformanceHeatmap");
  if (!rowsEl) {
    return;
  }

  const rows = report.rows || [];
  setText("#sectorStockPerformanceCount", report.available ? `${rows.length} sectors` : monitorRefreshing() ? "Refreshing" : "Unavailable");
  setText("#sectorStockPerformanceSummary", report.summary || monitorLoadingText("Sector stock performance is not available yet."));
  rowsEl.innerHTML = "";

  if (!report.available || !rows.length) {
    renderSectorStockPerformanceHeatmap(heatmapEl, [], report.summary || monitorLoadingText("Sector stock performance is unavailable."));
    emptyTable("#sectorStockPerformanceRows", 5, report.summary || monitorLoadingText("Sector stock performance is unavailable."));
    return;
  }

  renderSectorStockPerformanceHeatmap(heatmapEl, rows);

  for (const item of rows) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <strong>${escapeHtml(item.sector || "Sector")}</strong>
        <span>${formatNumber(item.stockCount)} stocks · A/D ${formatNumber(item.advance)} / ${formatNumber(item.decline)}</span>
      </td>
      <td>
        <strong class="${changeClass(item.sectorChangePercent)}">${signed(item.sectorChangePercent)}%</strong>
        <span>${formatNumber(item.sectorLast)} · H/L ${formatNumber(item.sectorHigh)} / ${formatNumber(item.sectorLow)}</span>
        <small>1M ${signed(item.oneMonthChange)}% · 1Y ${signed(item.oneYearChange)}% · PE ${formatNumber(item.sectorPe)}</small>
      </td>
      <td>${renderSectorStockMover(item.bestStock, item.sectorChangePercent)}</td>
      <td>${renderSectorStockMover(item.worstStock, item.sectorChangePercent)}</td>
      <td><span>${escapeHtml(item.summary || "")}</span></td>
    `;
    rowsEl.appendChild(row);
  }
}

function renderSectorStockPerformanceHeatmap(container, rows, emptyMessage = "") {
  if (!container) {
    return;
  }
  container.innerHTML = "";

  if (!rows.length) {
    container.innerHTML = `<article class="sector-heatmap-empty">${escapeHtml(emptyMessage || "No sector heatmap data available.")}</article>`;
    return;
  }

  const orderedRows = [...rows].sort((a, b) => {
    const aMove = stockHeatmapNumber(a.sectorChangePercent);
    const bMove = stockHeatmapNumber(b.sectorChangePercent);
    if (Number.isFinite(aMove) && Number.isFinite(bMove) && aMove !== bMove) {
      return bMove - aMove;
    }
    return String(a.sector || "").localeCompare(String(b.sector || ""));
  });
  const moves = orderedRows
    .map((item) => Math.abs(stockHeatmapNumber(item.sectorChangePercent) ?? 0))
    .filter((value) => Number.isFinite(value));
  const maxMove = Math.max(1, ...moves);

  for (const item of orderedRows) {
    const move = stockHeatmapNumber(item.sectorChangePercent);
    const breadth = sectorBreadthPercent(item);
    const tile = document.createElement("article");
    tile.className = `sector-heatmap-tile ${stockHeatmapTone(move)}`;
    const moveOpacity = Number.isFinite(move) ? 0.08 + (Math.min(Math.abs(move) / maxMove, 1) * 0.22) : 0.08;
    tile.style.setProperty("--sector-move-opacity", moveOpacity.toFixed(2));
    tile.style.setProperty("--sector-breadth", Number.isFinite(breadth) ? `${breadth}%` : "0%");
    tile.innerHTML = `
      <div class="sector-heatmap-main">
        <strong>${escapeHtml(item.sector || "Sector")}</strong>
        <em>${Number.isFinite(move) ? `${signed(move)}%` : "n/a"}</em>
      </div>
      <div class="sector-heatmap-meta">
        <span>${formatNumber(item.stockCount)} stocks</span>
        <span>A/D ${formatNumber(item.advance)} / ${formatNumber(item.decline)}</span>
      </div>
      <div class="sector-heatmap-breadth" aria-label="Advance breadth">
        <span></span>
      </div>
      <div class="sector-heatmap-movers">
        ${sectorHeatmapMover("Best", item.bestStock)}
        ${sectorHeatmapMover("Worst", item.worstStock)}
      </div>
    `;
    container.appendChild(tile);
  }
}

function sectorHeatmapMover(label, stock) {
  if (!stock || !stock.symbol) {
    return `<span><small>${label}</small><strong>n/a</strong><em></em></span>`;
  }
  return `
    <span>
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(stock.symbol)}</strong>
      <em class="${changeClass(stock.changePercent)}">${signed(stock.changePercent)}%</em>
    </span>
  `;
}

function sectorBreadthPercent(item) {
  const advances = stockHeatmapNumber(item.advance) || 0;
  const declines = stockHeatmapNumber(item.decline) || 0;
  const unchanged = stockHeatmapNumber(item.unchanged) || 0;
  const total = advances + declines + unchanged;
  return total > 0 ? Math.round((advances / total) * 100) : null;
}

function renderSectorStockMover(stock, sectorChangePercent) {
  if (!stock || !stock.symbol) {
    return "<span>n/a</span>";
  }
  const spread = Number.isFinite(stock.changePercent) && Number.isFinite(sectorChangePercent)
    ? stock.changePercent - sectorChangePercent
    : null;
  return `
    <strong>${escapeHtml(stock.symbol)}</strong>
    <span>${escapeHtml(stock.name || "")}</span>
    <span class="${changeClass(stock.changePercent)}">${signed(stock.changePercent)}% · ${formatMoney(stock.price, "INR")}</span>
    <small>vs sector ${Number.isFinite(spread) ? `${spread >= 0 ? "+" : ""}${spread.toFixed(2)} pp` : "n/a"}</small>
  `;
}

function renderMentionedStockHeatmap(data) {
  const grid = stockHeatmapGrid;
  if (!grid) {
    return;
  }

  const stocks = collectMentionedStocks(data);
  grid.innerHTML = "";
  setText("#stockHeatmapCount", stocks.length ? `${stocks.length} stocks` : "No stocks");

  if (!stocks.length) {
    setText("#stockHeatmapSummary", "No stock mentions are available in the current monitor payload.");
    grid.innerHTML = `<article class="stock-heatmap-empty">No stocks to map.</article>`;
    return;
  }

  const movedStocks = stocks.filter((stock) => Number.isFinite(stock.changePercent));
  const positive = movedStocks.filter((stock) => stock.changePercent > 0.05).length;
  const negative = movedStocks.filter((stock) => stock.changePercent < -0.05).length;
  const neutral = stocks.length - positive - negative;
  const best = movedStocks.reduce((current, stock) => (
    !current || stock.changePercent > current.changePercent ? stock : current
  ), null);
  const weakest = movedStocks.reduce((current, stock) => (
    !current || stock.changePercent < current.changePercent ? stock : current
  ), null);
  const summaryParts = [`${positive} positive`, `${negative} negative`, `${neutral} flat or no live move`];
  if (best) {
    summaryParts.push(`best ${best.symbol} ${signed(best.changePercent)}%`);
  }
  if (weakest && weakest !== best) {
    summaryParts.push(`weakest ${weakest.symbol} ${signed(weakest.changePercent)}%`);
  }
  setText("#stockHeatmapSummary", summaryParts.join(" · "));

  for (const stock of stocks) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = `stock-heatmap-tile ${stockHeatmapTone(stock.changePercent)}`;
    tile.setAttribute("aria-label", `Analyze ${stock.symbol}`);
    tile.addEventListener("click", () => analyzeStockFromHeatmap(stock.analysisSymbol || stock.symbol));
    const sources = stock.sources.slice(0, 3).join(" / ");
    const moreSources = stock.sources.length > 3 ? ` +${stock.sources.length - 3}` : "";
    tile.innerHTML = `
      <div>
        <strong>${escapeHtml(stock.symbol)}</strong>
        <span class="stock-heatmap-name">${escapeHtml(stock.name || "Tracked stock")}</span>
      </div>
      <div class="stock-heatmap-move">
        <span>${formatMoney(stock.price, "INR")}</span>
        <em>${Number.isFinite(stock.changePercent) ? `${signed(stock.changePercent)}%` : "n/a"}</em>
      </div>
      <small>${escapeHtml(sources + moreSources)}</small>
    `;
    grid.appendChild(tile);
  }
}

async function analyzeStockFromHeatmap(symbol) {
  const targetSymbol = String(symbol || "").trim();
  if (!targetSymbol) {
    return;
  }
  symbolInput.value = targetSymbol;
  suggestions.innerHTML = "";
  setActiveTab("analysis");
  window.scrollTo({ top: 0, behavior: "smooth" });
  await analyze(targetSymbol);
}

function collectMentionedStocks(data) {
  const stocks = new Map();
  const snapshot = data.nseSnapshot || {};
  addStockMentions(stocks, snapshot.topGainers, "NIFTY 50 gainers");
  addStockMentions(stocks, snapshot.topLosers, "NIFTY 50 losers");
  addStockMentions(stocks, snapshot.mostActive, "NSE active");
  addStockMentions(stocks, snapshot.weekHighs, "52W highs");
  addStockMentions(stocks, snapshot.priceBands?.rows, "Price bands");

  addStockMentions(stocks, data.breakoutCandidates, "Primary 50");
  addStockMentions(stocks, data.highVolumeCandidates, "High volume");
  addStockMentions(stocks, data.orderCatalysts, "Catalysts");

  for (const group of data.impacted || []) {
    addStockMentions(stocks, group.stocks, `${group.commodity || "Commodity"} impact`);
  }

  for (const row of (data.sectorStockPerformance?.rows || [])) {
    addStockMention(stocks, row.bestStock, `${row.sector || "Sector"} best`);
    addStockMention(stocks, row.worstStock, `${row.sector || "Sector"} worst`);
  }

  const sectorSnapshot = data.moneycontrolSectorAnalysis || {};
  for (const group of ["topPerforming", "underPerforming", "sectors"]) {
    for (const sector of sectorSnapshot[group] || []) {
      addStockMentions(stocks, sector.topStocks, sector.sector || "Moneycontrol sector");
    }
  }
  for (const sector of (sectorSnapshot.sectorOpenInterest?.rows || [])) {
    addStockMentions(stocks, sector.topStocks, `${sector.sector || "Sector"} OI`);
  }

  return Array.from(stocks.values())
    .map((stock) => ({ ...stock, sources: Array.from(stock.sources) }))
    .sort((a, b) => {
      const aMoved = Number.isFinite(a.changePercent);
      const bMoved = Number.isFinite(b.changePercent);
      if (aMoved && bMoved && a.changePercent !== b.changePercent) {
        return b.changePercent - a.changePercent;
      }
      if (aMoved !== bMoved) {
        return aMoved ? -1 : 1;
      }
      return a.symbol.localeCompare(b.symbol);
    });
}

function addStockMentions(map, items, source) {
  for (const item of items || []) {
    addStockMention(map, item, source);
  }
}

function addStockMention(map, item, source) {
  if (!item || !item.symbol) {
    return;
  }
  const key = stockHeatmapKey(item.symbol);
  if (!key || key === "N/A") {
    return;
  }
  const displaySymbol = stockHeatmapSymbol(item.symbol);
  const analysisSymbol = stockHeatmapAnalysisSymbol(item.symbol);
  const price = stockHeatmapNumber(item.price, item.last, item.lastPrice, item.nsePrice, item.regularMarketPrice);
  const changePercent = stockHeatmapNumber(
    item.changePercent,
    item.percentChange,
    item.pChange,
    item.regularMarketChangePercent,
  );
  const volume = stockHeatmapNumber(item.volume, item.totalTradedVolume, item.tradedQuantity);
  const score = stockHeatmapNumber(item.score, item.volumeRatio);
  const existing = map.get(key) || {
    symbol: displaySymbol,
    analysisSymbol: analysisSymbol || displaySymbol,
    name: "",
    price: null,
    changePercent: null,
    volume: null,
    score: null,
    sources: new Set(),
  };

  if (existing.symbol.endsWith(".NS") || displaySymbol.length < existing.symbol.length) {
    existing.symbol = displaySymbol;
  }
  if (analysisSymbol && shouldReplaceHeatmapAnalysisSymbol(existing.analysisSymbol, analysisSymbol)) {
    existing.analysisSymbol = analysisSymbol;
  }
  existing.name = existing.name || item.name || item.companyName || item.securityName || "";
  if (Number.isFinite(price)) {
    existing.price = price;
  }
  if (Number.isFinite(changePercent)) {
    existing.changePercent = changePercent;
  }
  if (Number.isFinite(volume)) {
    existing.volume = Math.max(Number.isFinite(existing.volume) ? existing.volume : 0, volume);
  }
  if (Number.isFinite(score)) {
    existing.score = Math.max(Number.isFinite(existing.score) ? existing.score : 0, score);
  }
  if (source) {
    existing.sources.add(source);
  }
  map.set(key, existing);
}

function stockHeatmapKey(symbol) {
  return String(symbol || "")
    .trim()
    .toUpperCase()
    .replace(/\.(NS|BO)$/u, "");
}

function stockHeatmapSymbol(symbol) {
  return String(symbol || "")
    .trim()
    .toUpperCase()
    .replace(/\.NS$/u, "");
}

function stockHeatmapAnalysisSymbol(symbol) {
  return String(symbol || "")
    .trim()
    .toUpperCase();
}

function shouldReplaceHeatmapAnalysisSymbol(currentSymbol, nextSymbol) {
  const current = String(currentSymbol || "");
  const next = String(nextSymbol || "");
  if (!current) {
    return true;
  }
  const currentHasExchange = /\.(NS|BO)$/u.test(current);
  const nextHasExchange = /\.(NS|BO)$/u.test(next);
  return !currentHasExchange && nextHasExchange;
}

function stockHeatmapNumber(...values) {
  for (const value of values) {
    if (Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string") {
      const parsed = Number.parseFloat(value.replace(/[,％%]/gu, ""));
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return null;
}

function stockHeatmapTone(value) {
  if (!Number.isFinite(value)) {
    return "is-neutral";
  }
  if (value >= 3) {
    return "is-strong-positive";
  }
  if (value > 0.05) {
    return "is-positive";
  }
  if (value <= -3) {
    return "is-strong-negative";
  }
  if (value < -0.05) {
    return "is-negative";
  }
  return "is-flat";
}

function renderMoneycontrolSectorAnalysis(snapshot) {
  const summary = document.querySelector("#moneycontrolSectorSummary");
  if (!summary) {
    return;
  }
  summary.innerHTML = "";
  renderSectorOpenInterest(snapshot.sectorOpenInterest || {});

  if (!snapshot.available) {
    const status = snapshot.loading || monitorRefreshing() ? "Refreshing" : "Unavailable";
    setText("#moneycontrolSectorStatus", status);
    summary.innerHTML = `<article class="market-mini-card"><span>Moneycontrol</span><strong>${status}</strong><small>${escapeHtml(snapshot.error || snapshot.summary || monitorLoadingText("Sector analysis could not be loaded."))}</small></article>`;
    emptyTable("#moneycontrolSectorRows", 9, monitorLoadingText("Moneycontrol sector analysis is unavailable."));
    emptyTable("#moneycontrolIndexRows", 4, monitorLoadingText("Moneycontrol sectoral indices are unavailable."));
    return;
  }

  const sectors = summarizedMoneycontrolSectors(snapshot);
  const indices = snapshot.sectorIndices || [];
  const breadth = snapshot.breadth || {};
  setText("#moneycontrolSectorStatus", `Top 5 / Bottom 3 movers`);
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
    emptyTable("#moneycontrolSectorRows", 9, "No sector rows were returned by Moneycontrol.");
  } else {
    for (const item of sectors) {
      const row = document.createElement("tr");
      const title = item.url
        ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.sector)}</a>`
        : escapeHtml(item.sector);
      row.innerHTML = `
        <td><span class="sector-signal ${item.signalType === "weak" ? "is-weak" : "is-strong"}">${escapeHtml(item.signal || "Tracked")}</span></td>
        <td><strong>${title}</strong><span>${formatNumber(item.stocks)} stocks · ${formatNumber(item.industries)} industries</span></td>
        <td>${renderMoneycontrolSectorStocks(item.topStocks || [], item.nseSector)}</td>
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

function renderMoneycontrolSectorStocks(stocks, nseSector) {
  if (!stocks.length) {
    return `<span>${escapeHtml(monitorLoadingText(nseSector ? "No stock movers returned." : "No NSE sector match."))}</span>`;
  }
  return `
    ${nseSector ? `<span>${escapeHtml(nseSector)}</span>` : ""}
    ${stocks.slice(0, 4).map((stock) => `
      <strong>${escapeHtml(stock.symbol || "n/a")}</strong>
      <span class="${changeClass(stock.changePercent)}">${signed(stock.changePercent)}% · ${formatMoney(stock.price, "INR")}</span>
    `).join("")}
  `;
}

function renderSectorOpenInterest(openInterest) {
  const summary = document.querySelector("#sectorOiSummary");
  const rows = document.querySelector("#sectorOiRows");
  if (!summary || !rows) {
    return;
  }

  summary.innerHTML = "";
  rows.innerHTML = "";

  if (!openInterest.available) {
    const status = openInterest.loading || monitorRefreshing() ? "OI refreshing" : "OI unavailable";
    setText("#sectorOiStatus", status);
    summary.innerHTML = `
      <article class="market-mini-card">
        <span>Sector OI</span>
        <strong>${escapeHtml(status)}</strong>
        <small>${escapeHtml(openInterest.error || openInterest.summary || monitorLoadingText("NSE sector-wise OI could not be loaded."))}</small>
      </article>
    `;
    emptyTable("#sectorOiRows", 6, monitorLoadingText("Sector-wise OI is unavailable."));
    return;
  }

  const totals = openInterest.totals || {};
  const coverage = openInterest.coverage || {};
  const highestOi = openInterest.highestOi || {};
  const topBuildUp = openInterest.topBuildUp || {};
  const sectorRows = openInterest.rows || [];
  setText("#sectorOiStatus", `${sectorRows.length} sectors${openInterest.timestamp ? " · " + openInterest.timestamp : ""}`);

  const cards = [
    ["Mapped F&O", formatNumber(coverage.mappedStocks), `${formatNumber(coverage.sourceRows)} NSE OI rows · ${formatNumber(coverage.unmappedStocks)} unmapped`],
    ["Mapped OI", formatLarge(totals.latestOi, ""), `Change ${formatSignedLarge(totals.changeOi)} (${formatPercentValue(totals.changePercent)})`],
    ["Total Volume", formatLarge(totals.volume, ""), "NSE derivative volume"],
    ["Highest OI", highestOi.sector || "n/a", formatLarge(highestOi.latestOi, "")],
    ["Top Build-up", topBuildUp.sector || "n/a", `${formatSignedLarge(topBuildUp.changeOi)} (${formatPercentValue(topBuildUp.changePercent)})`],
  ];
  for (const [label, value, detail] of cards) {
    const card = document.createElement("article");
    card.className = "market-mini-card";
    card.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail || "")}</small>`;
    summary.appendChild(card);
  }

  if (!sectorRows.length) {
    emptyTable("#sectorOiRows", 6, monitorLoadingText("No sector-wise OI rows were returned."));
    return;
  }

  for (const item of sectorRows) {
    const row = document.createElement("tr");
    const toneClass = item.changeOi > 0 ? "is-strong" : item.changeOi < 0 ? "is-weak" : "";
    row.innerHTML = `
      <td><strong>${escapeHtml(item.sector || "Sector")}</strong><span>${formatNumber(item.stockCount)} mapped F&O stocks</span></td>
      <td>${formatLarge(item.latestOi, "")}<span>Prev ${formatLarge(item.previousOi, "")}</span></td>
      <td class="${changeClass(item.changeOi)}">${formatSignedLarge(item.changeOi)}<span>${formatPercentValue(item.changePercent)}</span></td>
      <td>${formatLarge(item.volume, "")}<span>Vol/OI ${formatNumber(item.volumeToOi)}</span></td>
      <td>${renderSectorOiStocks(item.topStocks || [])}</td>
      <td><span class="sector-signal ${toneClass}">${escapeHtml(item.bias || "Tracked")}</span><small>${escapeHtml(item.summary || "")}</small></td>
    `;
    rows.appendChild(row);
  }
}

function renderSectorOiStocks(stocks) {
  if (!stocks.length) {
    return "<span>n/a</span>";
  }
  return stocks.map((stock) => `
    <strong>${escapeHtml(stock.symbol || "n/a")}</strong>
    <span>${formatLarge(stock.latestOi, "")} OI · ${formatSignedLarge(stock.changeOi)}</span>
  `).join("");
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

  addRows((snapshot.topPerforming || []).slice(0, 5), "Top 5", "strong");
  addRows((snapshot.underPerforming || []).slice(0, 3), "Bottom 3", "weak");
  if (!rows.length) {
    addRows((snapshot.sectors || []).slice(0, 8), "Tracked", "neutral");
  }
  return rows.slice(0, 8);
}

function renderNseMoverRows(selector, items, emptyMessage, sectionNote = "") {
  const body = document.querySelector(selector);
  body.innerHTML = "";
  if (!items.length) {
    emptyTable(selector, 5, sectionNote || emptyMessage);
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
  if (sectionNote) {
    const noteRow = document.createElement("tr");
    noteRow.className = "is-info-row";
    noteRow.innerHTML = `<td colspan="5">${escapeHtml(sectionNote)}</td>`;
    body.appendChild(noteRow);
  }
}

function renderNseActiveRows(items) {
  const body = document.querySelector("#nseActiveRows");
  body.innerHTML = "";
  if (!items.length) {
    emptyTable("#nseActiveRows", 5, monitorLoadingText("No NSE most-active rows were returned."));
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
    emptyTable("#nseHighRows", 5, monitorLoadingText("No NSE 52-week high or price-band rows were returned."));
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
    const rowClass = monitorRefreshing() ? " class=\"is-refreshing-row\"" : "";
    body.innerHTML = `<tr${rowClass}><td colspan="${colspan}">${escapeHtml(message)}</td></tr>`;
  }
}

function setText(selector, value) {
  const element = document.querySelector(selector);
  if (element) {
    element.textContent = value;
  }
}

function renderCommodities(items, usdInr) {
  const container = document.querySelector("#commodityGrid");
  container.innerHTML = "";
  if (usdInr?.available) {
    setText("#usdInrStatus", `USD/INR ${formatNumber(usdInr.price)}`);
    const fxCard = document.createElement("article");
    fxCard.className = "commodity-card currency-card";
    fxCard.innerHTML = `
      <span>Currency</span>
      <strong>USD / INR</strong>
      <p>${formatNumber(usdInr.price)} <em class="${changeClass(usdInr.changePercent)}">${signed(usdInr.changePercent)}%</em></p>
      <small>${escapeHtml(usdInr.lastDate || "Latest provider close")}</small>
    `;
    container.appendChild(fxCard);
  } else {
    setText("#usdInrStatus", monitorRefreshing() ? "USD/INR refreshing" : "USD/INR n/a");
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "commodity-card";
    const directionClass = changeClass(item.changePercent);
    card.innerHTML = `
      <span>${escapeHtml(item.category)}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <p>${formatMoney(item.price, "USD")} <em class="${directionClass}">${signed(item.changePercent)}%</em></p>
      <small>${Number.isFinite(item.inrPrice) ? `${formatMoney(item.inrPrice, "INR")} at USD/INR ${formatNumber(item.usdInr)}` : "INR conversion n/a"}</small>
      <small>1W ${formatPercentValue(item.oneWeek)} · 1M ${formatPercentValue(item.oneMonth)} · ${escapeHtml(item.trend)}</small>
    `;
    container.appendChild(card);
  }

  if (!items.length && !usdInr?.available) {
    const card = document.createElement("article");
    card.className = "commodity-card is-loading";
    card.innerHTML = `
      <span>Live refresh</span>
      <strong>${monitorRefreshing() ? "Refreshing" : "Unavailable"}</strong>
      <p>${escapeHtml(monitorLoadingText("Commodity and USD/INR data is unavailable."))}</p>
    `;
    container.appendChild(card);
  }
}

function renderBreakoutRows(items) {
  const body = document.querySelector("#breakoutRows");
  body.innerHTML = "";

  if (!items.length) {
    emptyTable("#breakoutRows", 7, monitorLoadingText("No high, breakout, or reversal candidates found in the current scan."));
    return;
  }

  for (const item of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.symbol)}</span></td>
      <td>${formatMoney(item.price, "INR")}<span class="${changeClass(item.changePercent)}">${signed(item.changePercent)}%</span></td>
      <td>${escapeHtml(item.signal)}<span>${escapeHtml((item.tags || []).join(" · "))}</span><span>Score ${scoreText(item.score || 0)}</span></td>
      <td>${formatPercentValue(item.pctBelowAvailableHigh)}<span>Scan high ${formatMoney(item.availableHigh, "INR")}</span><span>52W gap ${formatPercentValue(item.pctBelow52WeekHigh)}</span></td>
      <td>Breakout ${formatMoney(item.prior55High, "INR")}<span>SMA20 ${formatMoney(item.sma20, "INR")} · RSI ${formatNumber(item.rsi14)}</span><span>Reversal low ${formatMoney(item.prior20Low, "INR")} (${formatPercentValue(item.pctAbovePrior20Low)})</span></td>
      <td>${rangeBreakoutText(item, "narrowRange4", "NR4")}<span>${rangeBreakoutText(item, "narrowRange7", "NR7")}</span></td>
      <td>${formatNumber(item.volumeRatio)}x<span>1M ${formatPercentValue(item.oneMonth)}</span><span>DD ${formatPercentValue(item.drawdownFromRecentHigh)}</span></td>
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
    emptyTable("#highVolumeRows", 6, monitorLoadingText("No high-volume upside candidates passed the current filters."));
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
    emptyTable("#orderCatalystRows", 5, monitorLoadingText("No order or contract catalyst headlines were found in the current scan."));
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
    ["SMA 50", formatMoney(indicators.sma50, currency)],
    ["SMA 200", formatMoney(indicators.sma200, currency)],
    ["RSI 14", formatNumber(indicators.rsi14)],
    ["MACD", formatNumber(indicators.macd)],
    ["ATR %", formatPercentValue(indicators.atrPercent)],
    ["1M return", formatPercentValue(technical.performance.oneMonth)],
    ["3M return", formatPercentValue(technical.performance.threeMonth)],
    ["1Y return", formatPercentValue(technical.performance.oneYear)]
  ];

  if (relativeStrength.available) {
    rows.push([`RS vs ${relativeStrength.benchmarkName}`, relativeStrength.label || "n/a"]);
  }

  renderTable("#technicalMetrics", rows);
  renderList("#technicalSignals", [
    ...(technical.signals || []),
    ...(relativeStrength.available ? [relativeStrength.summary] : [])
  ].slice(0, 5));
}

function renderFundamentals(metrics, signals, currency) {
  const rows = [
    ["Sector", metrics.sector || "n/a"],
    ["Market cap", formatLarge(metrics.marketCap, currency)],
    ["Revenue", formatLarge(metrics.revenue, currency)],
    ["Net profit", formatLarge(metrics.netIncome, currency)],
    ["Trailing P/E", formatNumber(metrics.trailingPE)],
    ["Revenue growth", formatRatioPercent(metrics.revenueGrowth)],
    ["Profit margin", formatRatioPercent(metrics.profitMargins)],
    ["Return on equity", formatRatioPercent(metrics.returnOnEquity)],
    ["Debt/equity", formatNumber(metrics.debtToEquity)],
    ["Promoter holding", formatRatioPercent(metrics.promoterHolding)],
    ["Target mean", formatMoney(metrics.targetMeanPrice, currency)],
    ["Analyst view", metrics.recommendationKey || "n/a"]
  ];

  renderTable("#fundamentalMetrics", rows);
  renderList("#fundamentalSignals", (signals || []).slice(0, 5));
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
        return `
          <div class="ownership-row">
            <span>${escapeHtml(row.name)}</span>
            <strong>${formatRatioPercent(row.latest)}</strong>
            <em class="${changeClass(row.changePoints)}">${formatPointChange(row.changePoints)}</em>
            <small>${escapeHtml(row.trend || "n/a")}</small>
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

  const catalysts = (data.catalysts || []).slice(0, 3);
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

  const budgetImpacts = (data.budgetImpacts || []).slice(0, 3);
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
    ["Data sources", quality.dataSources || "n/a"]
  ]);
  renderAccuracyChecks(quality.checks || []);
  renderList("#qualityWarnings", [
    ...(quality.strengths || []).map((item) => `Strength: ${item}`),
    ...(quality.warnings || []).map((item) => `Warning: ${item}`)
  ].slice(0, 4));
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
        `Targets: ${scenarios.bull.expectedMove.map((value) => formatMoney(value, currency)).join(" / ")}`,
        scenarios.bull.confirmation
      ]
    },
    {
      title: scenarios.base.title,
      lines: [
        `Range: ${formatMoney(scenarios.base.rangeLow, currency)} - ${formatMoney(scenarios.base.rangeHigh, currency)}`,
        scenarios.base.action
      ]
    },
    {
      title: scenarios.bear.title,
      lines: [
        `Trigger: ${formatMoney(scenarios.bear.trigger, currency)}`,
        scenarios.bear.action
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
  scheduleChartRedraw();
}

function scheduleChartRedraw() {
  if (!latestReport) {
    return;
  }
  if (chartRedrawFrame) {
    window.cancelAnimationFrame(chartRedrawFrame);
  }
  chartRedrawFrame = window.requestAnimationFrame(() => {
    chartRedrawFrame = null;
    drawChart(latestReport.series, latestReport.currency, latestReport.technical.levels, 0, canvas);
    if (expandedChartCanvas && chartDialogEl && !chartDialogEl.classList.contains("is-hidden")) {
      drawChart(latestReport.series, latestReport.currency, latestReport.technical.levels, 0, expandedChartCanvas);
    }
  });
}

function drawChart(series, currency, levels = {}, retryCount = 0, targetCanvas = canvas) {
  const context = targetCanvas.getContext("2d");
  const rect = targetCanvas.getBoundingClientRect();
  if (rect.width < 20 || rect.height < 20) {
    if (retryCount < 5) {
      requestAnimationFrame(() => drawChart(series, currency, levels, retryCount + 1, targetCanvas));
    }
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  targetCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
  targetCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.imageSmoothingEnabled = false;

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

  drawMonthlyMarkers(context, series, padding, plotWidth, plotHeight, width, height);

  drawLevelZones(context, levelZones.filter((level) => level.type === "support"), "S", "#127348", currency, padding, plotWidth, plotHeight, min, range);
  drawLevelZones(context, levelZones.filter((level) => level.type === "resistance"), "R", "#b42318", currency, padding, plotWidth, plotHeight, min, range);

  drawCandlesticks(context, series, padding, plotWidth, plotHeight, min, range);
  plotLine(context, series, "sma50", "#2563eb", 1.6, padding, plotWidth, plotHeight, min, range);
  plotLine(context, series, "sma200", "#b45309", 1.6, padding, plotWidth, plotHeight, min, range);

  drawLevelLabels(context, levelZones.filter((level) => level.type === "support"), "S", "#127348", currency, padding, plotWidth, plotHeight, min, range);
  drawLevelLabels(context, levelZones.filter((level) => level.type === "resistance"), "R", "#b42318", currency, padding, plotWidth, plotHeight, min, range);

  context.fillStyle = "#667078";
  const lastDate = series[series.length - 1] && series[series.length - 1].date;
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
    return { top: 18, right: 88, bottom: 42, left: 8 };
  }
  if (width <= 640) {
    return { top: 20, right: 96, bottom: 44, left: 10 };
  }
  return { top: 24, right: 126, bottom: 48, left: 14 };
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

function drawMonthlyMarkers(context, series, padding, plotWidth, plotHeight, width, height) {
  const markers = [];
  let previousMonth = "";
  series.forEach((item, index) => {
    if (!item.date) {
      return;
    }
    const month = item.date.slice(0, 7);
    if (month && month !== previousMonth) {
      markers.push({ index, date: item.date });
      previousMonth = month;
    }
  });

  context.save();
  context.font = "700 10px system-ui, sans-serif";
  context.strokeStyle = "rgba(100, 116, 139, 0.22)";
  context.fillStyle = "#64748b";
  context.lineWidth = 1;

  markers.forEach((marker, markerIndex) => {
    const x = padding.left + (marker.index / Math.max(1, series.length - 1)) * plotWidth;
    context.beginPath();
    context.moveTo(x, padding.top);
    context.lineTo(x, padding.top + plotHeight);
    context.stroke();

    const date = new Date(`${marker.date}T00:00:00Z`);
    const label = date.toLocaleString("en-US", { month: "short" });
    const labelWidth = context.measureText(label).width;
    const labelX = Math.min(width - padding.right - labelWidth, Math.max(padding.left, x - labelWidth / 2));
    const labelY = height - 25 + (markerIndex % 2) * 12;
    context.fillText(label, labelX, labelY);
  });
  context.restore();
}

function drawLevelZones(context, levels, prefix, color, currency, padding, plotWidth, plotHeight, min, range) {
  for (const level of levels) {
    const zoneLow = Number.isFinite(level.zoneLow) ? level.zoneLow : level.price;
    const zoneHigh = Number.isFinite(level.zoneHigh) ? level.zoneHigh : level.price;
    const top = yForValue(zoneHigh, padding, plotHeight, min, range);
    const bottom = yForValue(zoneLow, padding, plotHeight, min, range);
    const mid = yForValue(level.price, padding, plotHeight, min, range);

    context.save();
    context.fillStyle = colorToRgba(color, 0.045);
    context.fillRect(padding.left, top, plotWidth, Math.max(2, bottom - top));
    context.strokeStyle = colorToRgba(color, 0.94);
    context.setLineDash([]);
    context.lineWidth = 1.4;
    context.beginPath();
    context.moveTo(padding.left, mid);
    context.lineTo(padding.left + plotWidth, mid);
    context.stroke();
    context.strokeStyle = colorToRgba(color, 0.24);
    context.setLineDash([3, 4]);
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(padding.left, top);
    context.lineTo(padding.left + plotWidth, top);
    context.moveTo(padding.left, bottom);
    context.lineTo(padding.left + plotWidth, bottom);
    context.stroke();
    context.restore();
  }
}

function drawLevelLabels(context, levels, prefix, color, currency, padding, plotWidth, plotHeight, min, range) {
  const placed = [];
  const labelX = padding.left + plotWidth + 6;

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
    context.font = "800 11px system-ui, sans-serif";
    const labelWidth = context.measureText(label).width + 10;
    context.fillStyle = colorToRgba(color, 0.14);
    context.strokeStyle = colorToRgba(color, 0.55);
    context.lineWidth = 1;
    roundedRect(context, labelX, y - 10, labelWidth, 20, 5);
    context.fill();
    context.stroke();
    context.fillStyle = color;
    context.fillText(label, labelX + 5, y + 4);
    context.strokeStyle = colorToRgba(color, 0.4);
    context.beginPath();
    context.moveTo(padding.left + plotWidth - 10, y);
    context.lineTo(labelX, y);
    context.stroke();
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

function formatSignedLarge(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  const prefix = value >= 0 ? "+" : "-";
  return `${prefix}${formatLarge(Math.abs(value), "")}`;
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

function numericOr(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
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

if (watchlistRows) {
  renderWatchlist();
}
showState("empty");
