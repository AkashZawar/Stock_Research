const form = document.querySelector("#analysisForm");
const symbolInput = document.querySelector("#symbolInput");
const suggestions = document.querySelector("#symbolSuggestions");
const emptyState = document.querySelector("#emptyState");
const loadingState = document.querySelector("#loadingState");
const errorState = document.querySelector("#errorState");
const reportEl = document.querySelector("#report");
const canvas = document.querySelector("#priceChart");

let latestReport = null;
let searchTimer = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbol = symbolInput.value.trim();
  if (!symbol) {
    return;
  }
  await analyze(symbol);
});

symbolInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const query = symbolInput.value.trim();
  if (query.length < 2) {
    suggestions.innerHTML = "";
    return;
  }
  searchTimer = setTimeout(() => searchSymbols(query), 250);
});

window.addEventListener("resize", () => {
  if (latestReport) {
    drawChart(latestReport.series, latestReport.currency, latestReport.technical.levels);
  }
});

async function analyze(symbol) {
  showState("loading");
  try {
    const response = await fetch(`/api/analyze?symbol=${encodeURIComponent(symbol)}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not build report.");
    }
    latestReport = payload;
    showState("report");
    renderReport(payload);
  } catch (error) {
    errorState.textContent = error.message;
    showState("error");
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
  document.querySelector("#outlookLabel").textContent = report.outlook.label;
  document.querySelector("#technicalSummary").textContent = report.technical.summary;
  document.querySelector("#fundamentalSummary").textContent = report.fundamentals.summary;
  document.querySelector("#eventRiskSummary").textContent = report.events.risk.label;
  document.querySelector("#outlookText").textContent = report.outlook.summary;

  renderLevels(report.researchLevels, currency, report.technical.levels);
  renderTechnical(report.technical, currency);
  renderFundamentals(report.fundamentals.metrics, report.fundamentals.signals, currency);
  renderEvents(report.events.items);
  requestAnimationFrame(() => drawChart(report.series, currency, report.technical.levels));
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

  renderTable("#technicalMetrics", rows);
  renderList("#technicalSignals", technical.signals);
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
  const padding = { top: 24, right: 112, bottom: 34, left: 14 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const levelZones = normalizeLevelZones(levels);
  const values = series
    .flatMap((item) => [item.close, item.sma50, item.sma200])
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

  plotLine(context, series, "close", "#0f766e", 2.4, padding, plotWidth, plotHeight, min, range);
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

function formatPercentValue(value) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(2)}%`;
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
