// ============================================================
// CONFIGURATION
// ============================================================

const REFRESH_INTERVAL = 10 * 60 * 1000; // 10 minutes
const TIMEFRAMES = ['1M', '3M', '6M', '1Y', '3Y', '5Y'];
let currentTimeframe = '1Y';
let dashboardData = null;
let nodeData = null;
let btcChartData = null;
let btcChartInstance = null;

// User's NODE watchlist (must match server-side WATCHLIST in fetcher/node_etf/analyze.py)
const NODE_WATCHLIST_TICKERS = ['HODL', 'MSTR', 'ASST', 'STRC'];

// Metric definitions for the flip card backs
const METRIC_INFO = {
  'fear_greed': {
    name: 'Fear & Greed Index',
    description: 'Market sentiment indicator (0-100)',
    btcExplanation: 'The Fear & Greed Index measures overall crypto market sentiment. Extreme fear often presents buying opportunities, while extreme greed may signal overheated markets.',
    whenUp: 'Greed territory \u2014 markets are optimistic, prices may be overextended. Historically, buying in extreme greed leads to lower returns.',
    whenDown: 'Fear territory \u2014 markets are pessimistic, potential buying opportunity. "Be greedy when others are fearful."',
    sources: ['Alternative.me']
  },
  'dxy': {
    name: 'Dollar Index (DXY)',
    description: 'US Dollar strength vs basket of currencies',
    btcExplanation: 'Bitcoin and the DXY typically move inversely. A weakening dollar often boosts Bitcoin as investors seek alternative stores of value.',
    whenUp: 'Dollar strengthening \u2014 typically bearish for Bitcoin. Capital flows into USD-denominated assets.',
    whenDown: 'Dollar weakening \u2014 typically bullish for Bitcoin. Investors seek hard assets as dollar purchasing power declines.',
    sources: ['ICE / Yahoo Finance']
  },
  'fed_funds_rate': {
    name: 'Fed Funds Rate',
    description: 'Federal Reserve target interest rate',
    btcExplanation: 'Interest rates set by the Fed influence all asset prices. Lower rates increase liquidity and risk appetite, benefiting Bitcoin.',
    whenUp: 'Higher rates \u2014 tighter monetary policy. Risk assets including Bitcoin face headwinds as borrowing costs rise.',
    whenDown: 'Lower rates \u2014 easier monetary policy. Increased liquidity and risk appetite tend to boost Bitcoin.',
    sources: ['Federal Reserve / FRED']
  },
  'treasury_10y': {
    name: '10-Year Treasury Yield',
    description: 'US government 10-year bond yield',
    btcExplanation: 'The 10Y yield reflects growth and inflation expectations. Rising yields compete with Bitcoin for investment flows.',
    whenUp: 'Rising yields attract capital to bonds, creating selling pressure on risk assets like Bitcoin.',
    whenDown: 'Falling yields signal economic concerns. Bitcoin may benefit as an alternative store of value.',
    sources: ['US Treasury / FRED']
  },
  'cpi': {
    name: 'CPI (Inflation)',
    description: 'Consumer Price Index year-over-year change',
    btcExplanation: 'Bitcoin is often seen as an inflation hedge. Persistent inflation erodes fiat purchasing power, strengthening the case for scarce assets.',
    whenUp: 'Higher inflation \u2014 erodes dollar purchasing power. Strengthens Bitcoin\'s value proposition as "digital gold."',
    whenDown: 'Lower inflation \u2014 reduces urgency for inflation hedges. But may signal rate cuts ahead, which benefit Bitcoin.',
    sources: ['Bureau of Labor Statistics / FRED']
  },
  'sp500': {
    name: 'S&P 500',
    description: 'Benchmark US equity index',
    btcExplanation: 'Bitcoin shows increasing correlation with the S&P 500, especially in risk-on/risk-off environments.',
    whenUp: 'Risk-on sentiment \u2014 Bitcoin often correlated short term. Equity strength typically supports crypto.',
    whenDown: 'Risk-off sentiment \u2014 Bitcoin may decline with equities short term, but can decouple during monetary crises.',
    sources: ['Yahoo Finance']
  }
};

const DEBT_INFO = {
  'national_debt': {
    name: 'US National Debt',
    description: 'Total US federal government debt'
  },
  'debt_to_gdp': {
    name: 'Debt-to-GDP Ratio',
    description: 'Federal debt as percentage of GDP'
  },
  'deficit': {
    name: 'Federal Deficit',
    description: 'Annual federal budget deficit'
  }
};

// ============================================================
// UTILITY FUNCTIONS
// ============================================================

function formatCurrency(value, decimals) {
  if (decimals === undefined) decimals = 2;
  if (value == null) return '--';
  return '$' + value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function formatLargeNumber(value) {
  if (value == null) return '--';
  var abs = Math.abs(value);
  if (abs >= 1e12) return '$' + (value / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return '$' + (value / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return '$' + (value / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return '$' + (value / 1e3).toFixed(2) + 'K';
  return '$' + value.toFixed(2);
}

function formatPercent(value) {
  if (value == null) return '--';
  var sign = value >= 0 ? '+' : '';
  return sign + value.toFixed(2) + '%';
}

function formatNumber(value, decimals) {
  if (decimals === undefined) decimals = 2;
  if (value == null) return '--';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  });
}

function getChangeClass(value) {
  if (value == null) return '';
  return value >= 0 ? 'text-green' : 'text-red';
}

function getChangeArrow(value) {
  if (value == null) return '';
  return value >= 0 ? '\u2191' : '\u2193';
}

function timeAgo(isoString) {
  var now = new Date();
  var then = new Date(isoString);
  var diffMs = now - then;
  var mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return mins + 'm ago';
  var hours = Math.floor(mins / 60);
  if (hours < 24) return hours + 'h ago';
  return Math.floor(hours / 24) + 'd ago';
}

function escapeHtml(str) {
  if (!str) return '';
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ============================================================
// DATA LOADING
// ============================================================

async function loadData() {
  try {
    var response = await fetch('data.json?t=' + Date.now());
    if (!response.ok) throw new Error('Failed to fetch data');
    dashboardData = await response.json();
  } catch (err) {
    console.error('Data load error:', err);
    var el = document.getElementById('lastUpdated');
    el.textContent = 'Data unavailable';
    el.classList.add('text-red');
    return;
  }
  try {
    renderDashboard();
  } catch (err) {
    console.error('Render error:', err);
  }
}

async function loadNodeData() {
  try {
    var response = await fetch('node/latest.json?t=' + Date.now());
    if (!response.ok) throw new Error('Failed to fetch NODE data');
    nodeData = await response.json();
  } catch (err) {
    console.error('NODE data load error:', err);
    nodeData = null;
  }
  try {
    renderNodeSection();
  } catch (err) {
    console.error('NODE render error:', err);
  }
}

// ============================================================
// RENDER FUNCTIONS
// ============================================================

function renderDashboard() {
  if (!dashboardData) return;

  // Update last updated
  var el = document.getElementById('lastUpdated');
  if (dashboardData.last_updated) {
    el.textContent = 'Updated ' + timeAgo(dashboardData.last_updated);
    el.className = 'last-updated';

    // Check if data is stale (>2 hours)
    var ageMs = Date.now() - new Date(dashboardData.last_updated).getTime();
    if (ageMs > 2 * 60 * 60 * 1000) {
      el.classList.add('text-amber');
    }
  }

  renderBitcoinHero();
  renderTickerCards();
  renderEquities();
  renderTreasuries();
  renderAssetComparison();
}

// ============================================================
// TICKER CARDS (Phase 4): MSTR / ASST / STRC / SATA
// ============================================================

var TICKER_LABELS = {
  'MSTR': 'Strategy Inc',
  'ASST': 'Strive Inc',
  'STRC': 'Strategy Preferred',
  'SATA': 'Strive Preferred'
};
var TICKER_ORDER = ['MSTR', 'ASST', 'STRC', 'SATA'];

function renderTickerCards() {
  var row = document.getElementById('tickerRow');
  if (!row) return;
  var src = dashboardData.tickers || {};

  row.innerHTML = TICKER_ORDER.map(function(sym) {
    var d = src[sym];
    var name = TICKER_LABELS[sym] || sym;

    if (!d || d.value == null) {
      return '<div class="ticker-card">' +
        '<div class="ticker-card-head">' +
          '<span class="ticker-symbol">' + escapeHtml(sym) + '</span>' +
          '<span class="ticker-name">' + escapeHtml(name) + '</span>' +
        '</div>' +
        '<div class="ticker-price text-muted">—</div>' +
        '<div class="ticker-change flat">No data</div>' +
      '</div>';
    }

    var pct = d.change_pct;
    var absVal = d.change_value;
    var cls = 'flat';
    var arrow = '·';
    if (pct != null && pct > 0.001) { cls = 'up'; arrow = '↑'; }
    else if (pct != null && pct < -0.001) { cls = 'down'; arrow = '↓'; }

    var pctStr = pct != null
      ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
      : '--';
    var absStr = '';
    if (absVal != null) {
      var sign = absVal >= 0 ? '+$' : '−$';
      absStr = '<span class="text-muted">' + sign +
        Math.abs(absVal).toLocaleString('en-US', {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        }) + '</span>';
    }

    return '<div class="ticker-card fade-in">' +
      '<div class="ticker-card-head">' +
        '<span class="ticker-symbol">' + escapeHtml(sym) + '</span>' +
        '<span class="ticker-name">' + escapeHtml(name) + '</span>' +
      '</div>' +
      '<div class="ticker-price">$' +
        d.value.toLocaleString('en-US', {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        }) +
      '</div>' +
      '<div class="ticker-change ' + cls + '">' +
        '<span>' + arrow + ' ' + escapeHtml(pctStr) + '</span>' +
        absStr +
      '</div>' +
    '</div>';
  }).join('');
}

// ============================================================
// BTC CHART (Phase 4)
// ============================================================

var CHART_TIMEFRAMES = [
  { key: '24H', source: 'intraday', points: 24 },
  { key: '1W',  source: 'intraday', points: null }, // all intraday
  { key: '1M',  source: 'daily',    points: 30 },
  { key: '3M',  source: 'daily',    points: 90 },
  { key: '1Y',  source: 'daily',    points: 365 },
  { key: '3Y',  source: 'daily',    points: 1095 },
  { key: '5Y',  source: 'daily',    points: 1825 },
  { key: '10Y', source: 'daily',    points: null } // all daily
];
var currentChartTf = '1M';

async function loadBtcChart() {
  try {
    var resp = await fetch('charts/btc.json?t=' + Date.now());
    if (!resp.ok) throw new Error('chart fetch failed: ' + resp.status);
    btcChartData = await resp.json();
  } catch (err) {
    console.error('BTC chart load error:', err);
    btcChartData = null;
  }
  try {
    initBtcChartUI();
  } catch (err) {
    console.error('BTC chart render error:', err);
  }
}

function initBtcChartUI() {
  var tfContainer = document.getElementById('chartTimeframes');
  if (tfContainer && !tfContainer.dataset.wired) {
    tfContainer.innerHTML = CHART_TIMEFRAMES.map(function(tf) {
      var cls = 'chart-tf-btn' + (tf.key === currentChartTf ? ' active' : '');
      return '<button class="' + cls + '" data-tf="' + tf.key + '">' +
        tf.key + '</button>';
    }).join('');
    tfContainer.dataset.wired = '1';
    tfContainer.querySelectorAll('.chart-tf-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        currentChartTf = btn.getAttribute('data-tf');
        renderBtcChart();
      });
    });
  }
  renderBtcChart();
}

function _getChartSeries(tfKey) {
  if (!btcChartData) return null;
  var tf = CHART_TIMEFRAMES.find(function(x) { return x.key === tfKey; });
  if (!tf) return null;
  var src = btcChartData[tf.source] || [];
  if (tf.points && src.length > tf.points) {
    src = src.slice(-tf.points);
  }
  return src;
}

function renderBtcChart() {
  var canvas = document.getElementById('btcChart');
  if (!canvas) return;

  // Toggle active state on buttons
  document.querySelectorAll('.chart-tf-btn').forEach(function(b) {
    if (b.getAttribute('data-tf') === currentChartTf) b.classList.add('active');
    else b.classList.remove('active');
  });

  var series = _getChartSeries(currentChartTf);
  if (!series || series.length < 2) {
    var labelEl = document.getElementById('chartTfLabel');
    var chgEl = document.getElementById('chartTfChange');
    var amtEl = document.getElementById('chartTfAmount');
    if (labelEl) labelEl.textContent = currentChartTf;
    if (chgEl) { chgEl.textContent = 'Loading...'; chgEl.className = 'chart-tf-change'; }
    if (amtEl) amtEl.textContent = '';
    return;
  }

  // Header stats: first vs last price in the window
  var firstPx = series[0][1];
  var lastPx = series[series.length - 1][1];
  var diffAmt = lastPx - firstPx;
  var diffPct = (lastPx - firstPx) / firstPx * 100;
  var up = diffPct >= 0;

  document.getElementById('chartTfLabel').textContent = currentChartTf;
  var chgEl = document.getElementById('chartTfChange');
  chgEl.textContent = (up ? '+' : '') + diffPct.toFixed(2) + '%';
  chgEl.className = 'chart-tf-change ' + (up ? 'up' : 'down');
  var amtEl = document.getElementById('chartTfAmount');
  amtEl.textContent = (up ? '+$' : '−$') + Math.abs(diffAmt).toLocaleString('en-US', {
    maximumFractionDigits: 0
  });

  // Build chart data
  var data = series.map(function(p) { return { x: p[0], y: p[1] }; });
  var lineColor = up ? 'rgba(34, 197, 94, 0.95)' : 'rgba(239, 68, 68, 0.95)';
  var fillRgb = up ? '34, 197, 94' : '239, 68, 68';

  if (typeof Chart === 'undefined') {
    console.warn('Chart.js not loaded yet');
    return;
  }

  if (btcChartInstance) {
    btcChartInstance.destroy();
  }
  btcChartInstance = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [{
        data: data,
        borderColor: lineColor,
        backgroundColor: function(ctx) {
          var chart = ctx.chart;
          var area = chart.chartArea;
          if (!area) return null;
          var grad = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
          grad.addColorStop(0, 'rgba(' + fillRgb + ', 0.22)');
          grad.addColorStop(1, 'rgba(' + fillRgb + ', 0)');
          return grad;
        },
        fill: true,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.08,
        parsing: false
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(0, 0, 0, 0.85)',
          borderColor: 'rgba(255, 255, 255, 0.1)',
          borderWidth: 1,
          titleColor: '#ffffff',
          bodyColor: '#ffffff',
          padding: 10,
          displayColors: false,
          callbacks: {
            title: function(items) {
              var ts = items[0].parsed.x;
              var dt = new Date(ts);
              return dt.toLocaleString('en-US', {
                year: 'numeric', month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit'
              });
            },
            label: function(item) {
              return '$' + item.parsed.y.toLocaleString('en-US', {
                maximumFractionDigits: 2
              });
            }
          }
        },
        decimation: { enabled: true, algorithm: 'lttb', samples: 250 }
      },
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'PPpp',
            displayFormats: {
              hour: 'MMM d HH:mm',
              day: 'MMM d',
              month: 'MMM yy',
              year: 'yyyy'
            }
          },
          ticks: { maxTicksLimit: 6, color: '#71717a', font: { size: 10 } },
          grid: { color: 'rgba(255, 255, 255, 0.04)' }
        },
        y: {
          ticks: {
            color: '#71717a',
            font: { size: 10 },
            callback: function(v) {
              if (Math.abs(v) >= 1000) {
                return '$' + (v / 1000).toFixed(1) + 'k';
              }
              return '$' + Number(v).toFixed(0);
            }
          },
          grid: { color: 'rgba(255, 255, 255, 0.04)' }
        }
      },
      interaction: { intersect: false, mode: 'nearest', axis: 'x' }
    }
  });
}

// ============================================================
// MARKET METRICS (Phase 3): SP500 / NASDAQ100 / GOLD / OIL / DXY / USDINR
// ============================================================

var EQUITY_LABELS = {
  'SP500':     { name: 'S&P 500',      desc: 'Benchmark US large-cap index',                prefix: '' },
  'NASDAQ100': { name: 'NASDAQ 100',   desc: 'Top 100 non-financial US tech-heavy stocks',  prefix: '' },
  'GOLD':      { name: 'Gold',         desc: 'COMEX Gold Futures (per troy ounce)',         prefix: '$' },
  'OIL':       { name: 'Crude Oil',    desc: 'WTI Crude Futures (per barrel)',              prefix: '$' },
  'DXY':       { name: 'Dollar Index', desc: 'US Dollar vs basket of major currencies',     prefix: '' },
  'USDINR':    { name: 'USD / INR',    desc: 'Indian Rupee per US Dollar',                  prefix: '' }
};

var EQUITY_ORDER = ['SP500', 'NASDAQ100', 'GOLD', 'OIL', 'DXY', 'USDINR'];

function renderEquities() {
  var grid = document.getElementById('equitiesGrid');
  if (!grid) return;
  var src = dashboardData.equities || {};

  grid.innerHTML = EQUITY_ORDER.map(function(key) {
    var info = EQUITY_LABELS[key];
    var d = src[key];

    var valueHtml = '<div class="metric-value">--</div>';
    var changeHtml = '';
    if (d && d.value != null) {
      valueHtml = '<div class="metric-value">' +
        escapeHtml(info.prefix + d.value.toLocaleString('en-US', {
          minimumFractionDigits: 2, maximumFractionDigits: 2
        })) +
      '</div>';
    }
    if (d && d.change_pct != null) {
      var cls = getChangeClass(d.change_pct);
      var arrow = getChangeArrow(d.change_pct);
      var absStr = '';
      if (d.change_value != null) {
        var sign = d.change_value >= 0 ? '+' : '−';
        absStr = ' <span class="text-muted">(' + sign +
          escapeHtml(info.prefix + Math.abs(d.change_value).toLocaleString('en-US', {
            minimumFractionDigits: 2, maximumFractionDigits: 2
          })) + ')</span>';
      }
      changeHtml = '<div class="metric-change ' + cls + '">' +
        arrow + ' ' + formatPercent(d.change_pct) + absStr +
      '</div>';
    }

    return '<div class="metric-card fade-in">' +
      '<h4 class="metric-name">' + escapeHtml(info.name) + '</h4>' +
      '<p class="metric-description">' + escapeHtml(info.desc) + '</p>' +
      valueHtml +
      changeHtml +
    '</div>';
  }).join('');
}

// ============================================================
// TREASURIES (Phase 3): 2Y / 10Y / 30Y / 3M T-Bill
// ============================================================

var TREASURY_LABELS = {
  'DGS2':  { name: '2-Year Treasury',  desc: 'Short-end policy expectations' },
  'DGS10': { name: '10-Year Treasury', desc: 'Growth & inflation expectations' },
  'DGS30': { name: '30-Year Treasury', desc: 'Long-duration risk premium' },
  'DTB3':  { name: '3-Month T-Bill',   desc: 'Money-market reference (risk-free rate)' }
};

var TREASURY_ORDER = ['DTB3', 'DGS2', 'DGS10', 'DGS30'];

function renderTreasuries() {
  var grid = document.getElementById('treasuriesGrid');
  if (!grid) return;
  var src = dashboardData.treasuries || {};

  grid.innerHTML = TREASURY_ORDER.map(function(key) {
    var info = TREASURY_LABELS[key];
    var d = src[key];

    var valueHtml = '<div class="metric-value">--</div>';
    var changeHtml = '';
    if (d && d.value != null) {
      valueHtml = '<div class="metric-value">' + d.value.toFixed(2) + '%</div>';
    }
    if (d && d.change != null) {
      // Yield change is in absolute yield points (often ~0.01-0.10).
      // Convert to basis points for the badge (1bp = 0.01%).
      var bps = Math.round(d.change * 100);
      var cls = getChangeClass(bps);
      var arrow = getChangeArrow(bps);
      var sign = bps >= 0 ? '+' : '';
      changeHtml = '<div class="metric-change ' + cls + '">' +
        arrow + ' ' + sign + bps + ' bps' +
      '</div>';
    }

    return '<div class="metric-card fade-in">' +
      '<h4 class="metric-name">' + escapeHtml(info.name) + '</h4>' +
      '<p class="metric-description">' + escapeHtml(info.desc) + '</p>' +
      valueHtml +
      changeHtml +
    '</div>';
  }).join('');
}

// ============================================================
// NODE ETF SECTION
// ============================================================

function fmtPctSigned(v, digits) {
  if (digits === undefined) digits = 2;
  if (v == null) return '--';
  var sign = v >= 0 ? '+' : '';
  return sign + v.toFixed(digits) + '%';
}

function fmtPpSigned(v, digits) {
  if (digits === undefined) digits = 2;
  if (v == null) return '--';
  var sign = v >= 0 ? '+' : '';
  return sign + v.toFixed(digits) + 'pp';
}

function fmtShares(v) {
  if (v == null) return '--';
  return v.toLocaleString('en-US');
}

function fmtSharesSigned(v) {
  if (v == null) return '--';
  var sign = v >= 0 ? '+' : '';
  return sign + v.toLocaleString('en-US');
}

function fmtUsdCompact(v) {
  if (v == null) return '--';
  if (Math.abs(v) >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
  if (Math.abs(v) >= 1e6) return '$' + (v / 1e6).toFixed(2) + 'M';
  if (Math.abs(v) >= 1e3) return '$' + (v / 1e3).toFixed(1) + 'K';
  return '$' + v.toFixed(0);
}

function fmtDate(iso) {
  // 'YYYY-MM-DD' -> 'May 12, 2026'
  if (!iso) return '--';
  var parts = iso.split('-');
  if (parts.length !== 3) return iso;
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var m = parseInt(parts[1], 10);
  var d = parseInt(parts[2], 10);
  return months[m - 1] + ' ' + d + ', ' + parts[0];
}

function renderNodeSection() {
  if (!nodeData) {
    var strip = document.getElementById('nodeFundStrip');
    if (strip) {
      strip.innerHTML = '<div class="node-stat" style="grid-column:1/-1;">' +
        '<div class="node-stat-label">NODE ETF</div>' +
        '<div class="node-stat-value text-muted">Data unavailable — fetch hasn’t run yet.</div>' +
      '</div>';
    }
    return;
  }

  renderNodeAsOf();
  renderNodeFundStrip();
  renderNodeWatchlist();
  renderNodeEvents();
  renderNodePatterns();
  renderNodeHoldings();
}

function renderNodeAsOf() {
  var el = document.getElementById('nodeAsOf');
  if (!el) return;
  var hist = nodeData.history_summary || {};
  var line = 'As of ' + fmtDate(nodeData.as_of);
  if (hist.num_snapshots && hist.num_snapshots > 1) {
    line += ' · ' + hist.num_snapshots + ' days of history';
  }
  el.textContent = line;
}

function renderNodeFundStrip() {
  var strip = document.getElementById('nodeFundStrip');
  if (!strip) return;

  var f = nodeData.fund || {};
  var hist = nodeData.history_summary || {};

  // Build the AUM delta sub-line (only when we have prior-day data)
  var aumDeltaHtml = '';
  if (f.total_net_assets_change_usd != null && f.total_net_assets_change_pct != null) {
    var dUsd = f.total_net_assets_change_usd;
    var dPct = f.total_net_assets_change_pct;
    var cls = 'flat';
    var arrow = '·';
    if (dPct > 0.001) { cls = 'up'; arrow = '↑'; }
    else if (dPct < -0.001) { cls = 'down'; arrow = '↓'; }
    var usdStr = (dUsd >= 0 ? '+' : '−') + fmtUsdCompact(Math.abs(dUsd));
    aumDeltaHtml =
      '<div class="node-stat-delta ' + cls + '">' +
        '<span>' + arrow + '</span>' +
        '<span>' + escapeHtml(fmtPctSigned(dPct, 2)) + '</span>' +
        '<span class="text-muted">(' + escapeHtml(usdStr) + ')</span>' +
      '</div>';
  }

  var stats = [
    {
      label: 'NAV',
      value: f.nav != null ? '$' + f.nav.toFixed(2) : '--'
    },
    {
      label: 'Total Assets',
      value: fmtUsdCompact(f.total_net_assets_usd),
      extraHtml: aumDeltaHtml
    },
    {
      label: 'YTD Return',
      value: f.ytd_return_pct != null ? fmtPctSigned(f.ytd_return_pct, 2) : '--',
      cls: f.ytd_return_pct >= 0 ? 'text-green' : 'text-red'
    },
    {
      label: 'Positions',
      value: (f.num_holdings != null ? f.num_holdings : '--') + '',
      sub: f.num_cash_positions ? f.num_cash_positions + ' cash' : ''
    },
    {
      label: 'History',
      value: hist.num_snapshots ? hist.num_snapshots + 'd' : '--',
      sub: hist.first_snapshot_date
        ? 'since ' + fmtDate(hist.first_snapshot_date).split(',')[0]
        : ''
    }
  ];

  strip.innerHTML = stats.map(function(s) {
    var subHtml = s.sub ? '<div class="node-stat-sub">' + escapeHtml(s.sub) + '</div>' : '';
    var extraHtml = s.extraHtml || '';
    var valCls = s.cls ? ' ' + s.cls : '';
    return '<div class="node-stat">' +
      '<div class="node-stat-label">' + escapeHtml(s.label) + '</div>' +
      '<div class="node-stat-value' + valCls + '">' + escapeHtml(s.value) + '</div>' +
      subHtml +
      extraHtml +
    '</div>';
  }).join('');
}

function renderNodeWatchlist() {
  var grid = document.getElementById('nodeWatchlistGrid');
  if (!grid) return;

  // Show only watchlist tickers that are actually in the portfolio today
  // (or just exited/added today). NOT_HELD is hidden — if VanEck adds the
  // ticker on a future day, its status will flip to ADDED or HELD and the
  // card will auto-resurface here without any code change.
  var allItems = nodeData.watchlist || [];
  var items = allItems.filter(function(item) {
    return item.status !== 'NOT_HELD';
  });

  if (!items.length) {
    grid.innerHTML = '<div class="node-empty-state">' +
      'None of your watchlist tickers are currently held in NODE. ' +
      'Cards will reappear here automatically the day any of them are added.' +
    '</div>';
    return;
  }

  grid.innerHTML = items.map(function(item) {
    var statusKey = (item.status || '').toLowerCase().replace('_', '-');
    var cls = 'node-watchlist-card ' + statusKey;
    var summaryCls = 'node-watchlist-summary';
    var s = (item.summary || '');
    if (/Increased|Added/i.test(s)) summaryCls += ' up';
    else if (/Reduced|Exited/i.test(s)) summaryCls += ' down';
    else summaryCls += ' flat';

    var statusLabel = item.status ? item.status.replace('_', ' ') : '';
    return '<div class="' + cls + '">' +
      '<div class="node-watchlist-head">' +
        '<div>' +
          '<div class="node-watchlist-ticker">' + escapeHtml(item.ticker) + '</div>' +
          '<div class="node-watchlist-label">' + escapeHtml(item.label || '') + '</div>' +
        '</div>' +
        '<span class="status-pill ' + statusKey + '">' + escapeHtml(statusLabel) + '</span>' +
      '</div>' +
      '<div class="' + summaryCls + '">' + escapeHtml(item.summary || '') + '</div>' +
    '</div>';
  }).join('');
}

function renderNodeEvents() {
  var list = document.getElementById('nodeEvents');
  var countEl = document.getElementById('nodeEventsCount');
  if (!list) return;

  var events = nodeData.today_events || [];
  if (countEl) countEl.textContent = events.length + (events.length === 1 ? ' event' : ' events');

  if (!events.length) {
    var hist = nodeData.history_summary || {};
    var msg;
    if ((hist.num_snapshots || 0) < 2) {
      msg = 'No comparison yet — this is the first snapshot. Day-over-day changes will appear starting tomorrow.';
    } else {
      msg = 'No material changes since the prior trading day. Manager is holding steady.';
    }
    list.innerHTML = '<div class="node-empty-state">' + escapeHtml(msg) + '</div>';
    return;
  }

  list.innerHTML = events.map(function(ev) {
    var icon = '•';
    if (ev.type === 'added') icon = '+';
    else if (ev.type === 'exited') icon = '✕';
    else if (ev.type === 'increased') icon = '↑';
    else if (ev.type === 'decreased') icon = '↓';

    var isWatchlist = NODE_WATCHLIST_TICKERS.indexOf((ev.ticker || '').toUpperCase()) !== -1;
    var tickerCls = 'ticker' + (isWatchlist ? ' watchlist' : '');

    var detailParts = [];
    if (ev.delta_shares != null) detailParts.push(fmtSharesSigned(ev.delta_shares) + ' shares');
    if (ev.delta_shares_pct != null) detailParts.push(fmtPctSigned(ev.delta_shares_pct, 1));
    if (ev.flow_adjusted_shares_pct != null) detailParts.push('flow-adj ' + fmtPctSigned(ev.flow_adjusted_shares_pct, 1));
    if (ev.delta_weight_pp != null) detailParts.push(fmtPpSigned(ev.delta_weight_pp, 2) + ' weight');
    if (ev.weight_pct != null && ev.type === 'added') detailParts.push(ev.weight_pct.toFixed(2) + '% of NAV');

    var verb = '';
    if (ev.type === 'added') verb = 'Added';
    else if (ev.type === 'exited') verb = 'Exited';
    else if (ev.type === 'increased') verb = 'Increased';
    else if (ev.type === 'decreased') verb = 'Reduced';

    return '<div class="node-event">' +
      '<div class="node-event-icon ' + escapeHtml(ev.type) + '">' + icon + '</div>' +
      '<div class="node-event-body">' +
        '<div class="node-event-title">' +
          '<span>' + escapeHtml(verb) + '</span>' +
          '<span class="' + tickerCls + '">' + escapeHtml(ev.ticker || '') + '</span>' +
          (ev.name ? '<span class="text-muted text-sm">' + escapeHtml(ev.name) + '</span>' : '') +
        '</div>' +
        '<div class="node-event-detail">' + escapeHtml(detailParts.join(' • ')) + '</div>' +
      '</div>' +
    '</div>';
  }).join('');
}

function renderNodePatterns() {
  var grid = document.getElementById('nodePatternsGrid');
  if (!grid) return;
  var p = nodeData.multi_day_patterns || {};
  var acc = p.accumulating || [];
  var dist = p.distributing || [];

  function renderColumn(items, kind) {
    var headerCls = kind === 'up' ? 'up' : 'down';
    var headerLabel = kind === 'up' ? 'Accumulating' : 'Distributing';
    var headerArrow = kind === 'up' ? '↗' : '↘';

    var body;
    if (!p.ready) {
      var snaps = p.snapshots_in_window || 0;
      var need = (p.min_days || 3) + 1;
      body = '<div class="node-empty-state">' +
        'Need at least ' + need + ' trading days of history (have ' + snaps + '). ' +
        'Patterns will appear as snapshots accumulate.</div>';
    } else if (!items.length) {
      body = '<div class="node-empty-state">No ' + headerLabel.toLowerCase() +
        ' patterns in the trailing ' + (p.window_days || 7) + '-day window.</div>';
    } else {
      body = '<div class="node-pattern-list">' +
        items.slice(0, 8).map(function(r) {
          var isWatchlist = NODE_WATCHLIST_TICKERS.indexOf((r.ticker || '').toUpperCase()) !== -1;
          var tickerCls = 'node-pattern-ticker' + (isWatchlist ? ' watchlist' : '');
          var detail = r.up_days + '/' + r.pair_count + ' days';
          if (r.total_delta_pct != null) {
            detail += ' • ' + fmtPctSigned(r.total_delta_pct, 1);
          }
          return '<div class="node-pattern-row">' +
            '<div>' +
              '<div class="' + tickerCls + '">' + escapeHtml(r.ticker) +
                (isWatchlist ? '<span class="node-event-watchlist-pin">★</span>' : '') +
              '</div>' +
              '<div class="text-muted text-xs">' + escapeHtml(r.name || '') + '</div>' +
            '</div>' +
            '<div class="node-pattern-detail">' + escapeHtml(detail) + '</div>' +
          '</div>';
        }).join('') +
      '</div>';
    }

    return '<div class="node-pattern-column">' +
      '<div class="node-pattern-header ' + headerCls + '">' +
        '<span>' + headerArrow + '</span>' +
        '<span>' + headerLabel + '</span>' +
      '</div>' +
      body +
    '</div>';
  }

  grid.innerHTML = renderColumn(acc, 'up') + renderColumn(dist, 'down');
}

function renderNodeHoldings() {
  var table = document.getElementById('nodeHoldingsTable');
  var countEl = document.getElementById('nodeHoldingsCount');
  if (!table) return;

  // Filter cash positions out of the headline table.
  var all = (nodeData.holdings || []).filter(function(h) { return !h.is_cash; });
  if (countEl) countEl.textContent = all.length + ' positions';

  if (!all.length) {
    table.innerHTML = '<div class="node-empty-state">No holdings data.</div>';
    return;
  }

  var lookback = nodeData.lookback_deltas || {};

  var header = '<div class="node-holding-row header">' +
    '<div>#</div>' +
    '<div>Ticker</div>' +
    '<div class="node-holding-name">Name</div>' +
    '<div class="node-holding-shares">Shares</div>' +
    '<div class="node-holding-weight">Weight</div>' +
    '<div class="node-holding-delta">1d Δsh</div>' +
  '</div>';

  var rows = all.map(function(h, i) {
    var deltas = (lookback[h.ticker] || {}).deltas || {};
    var d1 = deltas['1d'] || {};
    var deltaCls = 'flat', deltaText = '—';
    if (d1.delta_shares_pct != null) {
      if (d1.delta_shares_pct > 0.05) { deltaCls = 'up'; deltaText = fmtPctSigned(d1.delta_shares_pct, 1); }
      else if (d1.delta_shares_pct < -0.05) { deltaCls = 'down'; deltaText = fmtPctSigned(d1.delta_shares_pct, 1); }
      else { deltaText = '0%'; }
    } else if (d1.status === 'new_in_window') {
      deltaCls = 'up'; deltaText = 'NEW';
    }

    var fundTag = h.is_vaneck_fund ? '<span class="tag-fund">FUND</span>' : '';
    var isWatchlist = NODE_WATCHLIST_TICKERS.indexOf((h.ticker || '').toUpperCase()) !== -1;
    var watchlistStar = isWatchlist ? '<span style="color:#fbbf24;font-size:.625rem;">★</span>' : '';

    return '<div class="node-holding-row">' +
      '<div class="node-holding-rank">' + (i + 1) + '</div>' +
      '<div class="node-holding-ticker">' +
        escapeHtml(h.ticker) + watchlistStar + fundTag +
      '</div>' +
      '<div class="node-holding-name">' + escapeHtml(h.name || '') + '</div>' +
      '<div class="node-holding-shares">' + fmtShares(h.shares) + '</div>' +
      '<div class="node-holding-weight">' +
        (h.weight_pct != null ? h.weight_pct.toFixed(2) + '%' : '--') +
      '</div>' +
      '<div class="node-holding-delta ' + deltaCls + '">' + deltaText + '</div>' +
    '</div>';
  }).join('');

  table.innerHTML = header + rows;
}

function renderBitcoinHero() {
  var btc = dashboardData.bitcoin;
  if (!btc) return;

  document.getElementById('btcPrice').textContent = formatCurrency(btc.price_usd, 0);

  var pctEl = document.getElementById('btcChangePct');
  pctEl.textContent = formatPercent(btc.change_24h_pct);
  pctEl.className = 'btc-change-pct ' + getChangeClass(btc.change_24h_pct);

  var amtEl = document.getElementById('btcChangeAmt');
  if (btc.change_24h_pct != null && btc.price_usd != null) {
    var changeAmt = btc.price_usd * (btc.change_24h_pct / 100) / (1 + btc.change_24h_pct / 100);
    var sign = btc.change_24h_pct >= 0 ? '+' : '-';
    amtEl.textContent = sign + formatCurrency(Math.abs(changeAmt), 0);
    amtEl.className = 'btc-change-amt ' + getChangeClass(btc.change_24h_pct);
  }
}

function renderMacroCards() {
  var grid = document.getElementById('macroGrid');
  grid.innerHTML = '';

  var macroMetrics = [
    {
      key: 'dxy',
      data: dashboardData.macro ? dashboardData.macro.dxy : null,
      formatValue: function(d) { return (d && d.value != null) ? formatNumber(d.value) : '--'; },
      getChange: function(d) { return d ? d.change : null; }
    },
    {
      key: 'fed_funds_rate',
      data: dashboardData.macro ? dashboardData.macro.fed_funds_rate : null,
      formatValue: function(d) { return (d && d.value != null) ? d.value + '%' : '--'; },
      getChange: function(d) { return d ? d.change : null; }
    },
    {
      key: 'treasury_10y',
      data: dashboardData.macro ? dashboardData.macro.treasury_10y : null,
      formatValue: function(d) { return (d && d.value != null) ? d.value + '%' : '--'; },
      getChange: function(d) { return d ? d.change : null; }
    },
    {
      key: 'cpi',
      data: dashboardData.macro ? dashboardData.macro.cpi : null,
      formatValue: function(d) { return (d && d.value != null) ? d.value.toFixed(1) + '%' : '--'; },
      getChange: function(d) { return d ? d.change : null; }
    },
    {
      key: 'sp500',
      data: dashboardData.macro ? dashboardData.macro.sp500 : null,
      formatValue: function(d) { return (d && d.value != null) ? formatNumber(d.value, 0) : '--'; },
      getChange: function(d) { return d ? d.change : null; }
    }
  ];

  macroMetrics.forEach(function(metric) {
    var info = METRIC_INFO[metric.key];
    var change = metric.getChange(metric.data);

    var card = document.createElement('div');
    card.className = 'metric-card fade-in';

    var changeHtml = '';
    if (change != null) {
      changeHtml = '<div class="metric-change ' + getChangeClass(change) + '">' +
        getChangeArrow(change) + ' ' + formatPercent(change) + '</div>';
    }

    card.innerHTML =
      '<h3 class="metric-name">' + escapeHtml(info.name) + '</h3>' +
      '<p class="metric-description">' + escapeHtml(info.description) + '</p>' +
      '<div class="metric-value">' + escapeHtml(metric.formatValue(metric.data)) + '</div>' +
      changeHtml;

    grid.appendChild(card);
  });
}

function renderDebtCards() {
  var grid = document.getElementById('debtGrid');
  grid.innerHTML = '';

  var debtMetrics = [
    {
      key: 'national_debt',
      data: dashboardData.debt ? dashboardData.debt.national_debt : null,
      formatValue: function(d) { return (d && d.value != null) ? formatLargeNumber(d.value * 1e6) : '--'; }
    },
    {
      key: 'debt_to_gdp',
      data: dashboardData.debt ? dashboardData.debt.debt_to_gdp : null,
      formatValue: function(d) { return (d && d.value != null) ? d.value.toFixed(1) + '%' : '--'; }
    },
    {
      key: 'deficit',
      data: dashboardData.debt ? dashboardData.debt.deficit : null,
      formatValue: function(d) { return (d && d.value != null) ? formatLargeNumber(Math.abs(d.value) * 1e6) : '--'; }
    }
  ];

  debtMetrics.forEach(function(metric) {
    var info = DEBT_INFO[metric.key];
    var card = document.createElement('div');
    card.className = 'metric-card fade-in';

    var dateHtml = '';
    if (metric.data && metric.data.date) {
      dateHtml = '<div class="metric-date text-muted">As of ' + escapeHtml(metric.data.date) + '</div>';
    }

    card.innerHTML =
      '<h4 class="metric-name">' + escapeHtml(info.name) + '</h4>' +
      '<p class="metric-description">' + escapeHtml(info.description) + '</p>' +
      '<div class="metric-value">' + escapeHtml(metric.formatValue(metric.data)) + '</div>' +
      dateHtml;

    grid.appendChild(card);
  });
}

function renderStablecoins() {
  var grid = document.getElementById('stablecoinGrid');
  grid.innerHTML = '';

  var coins = dashboardData.stablecoins;
  if (!coins) {
    grid.innerHTML = '<p class="data-unavailable">Stablecoin data unavailable</p>';
    return;
  }

  ['usdt', 'usdc'].forEach(function(sym) {
    var coin = coins[sym];
    if (!coin) return;

    var changePct = coin.change_24h_pct || 0;
    var card = document.createElement('div');
    card.className = 'metric-card stablecoin-card fade-in';

    card.innerHTML =
      '<div class="stablecoin-header">' +
        '<div>' +
          '<h4 class="stablecoin-name">' + sym.toUpperCase() + '</h4>' +
          '<p class="text-muted text-sm">Total Supply</p>' +
        '</div>' +
        '<div class="stablecoin-change ' + getChangeClass(changePct) + '">' +
          getChangeArrow(changePct) +
          '<span>' + formatPercent(changePct) + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="stablecoin-value">' + formatLargeNumber(coin.total_supply || coin.market_cap) + '</div>';

    grid.appendChild(card);
  });
}

function renderETFFlows() {
  var grid = document.getElementById('etfGrid');
  grid.innerHTML = '';

  var etf = dashboardData.etf_flows;
  if (!etf || !etf.flows) {
    grid.innerHTML = '<p class="data-unavailable">ETF flow data unavailable</p>';
    return;
  }

  // Sort by absolute flow value
  var flows = Object.entries(etf.flows).sort(function(a, b) {
    return Math.abs(b[1].daily_flow_millions) - Math.abs(a[1].daily_flow_millions);
  });

  flows.forEach(function(entry) {
    var ticker = entry[0];
    var data = entry[1];
    var isPositive = data.daily_flow_millions >= 0;

    var card = document.createElement('div');
    card.className = 'metric-card etf-card fade-in';

    card.innerHTML =
      '<div class="etf-header">' +
        '<div>' +
          '<h4 class="etf-ticker">' + escapeHtml(ticker) + '</h4>' +
          '<p class="text-muted text-sm">' + escapeHtml(data.provider || '') + '</p>' +
        '</div>' +
        '<span class="badge ' + (isPositive ? 'badge-green' : 'badge-red') + '">' +
          (isPositive ? 'Inflow' : 'Outflow') +
        '</span>' +
      '</div>' +
      '<div class="etf-flow font-mono ' + (isPositive ? 'text-green' : 'text-red') + '">' +
        (isPositive ? '+' : '-') + '$' + Math.abs(data.daily_flow_millions).toFixed(1) + 'M' +
      '</div>';

    grid.appendChild(card);
  });

  // Add total row
  if (etf.total_daily_flow != null) {
    var isPositive = etf.total_daily_flow >= 0;
    var totalCard = document.createElement('div');
    totalCard.className = 'metric-card etf-card etf-total fade-in';

    totalCard.innerHTML =
      '<div class="etf-header">' +
        '<h4 class="etf-ticker">TOTAL</h4>' +
        '<span class="text-muted text-sm">' + escapeHtml(etf.date || '') + '</span>' +
      '</div>' +
      '<div class="etf-flow font-mono ' + (isPositive ? 'text-green' : 'text-red') + '" style="font-size:1.5rem">' +
        (isPositive ? '+' : '-') + '$' + Math.abs(etf.total_daily_flow).toFixed(1) + 'M' +
      '</div>';

    grid.appendChild(totalCard);
  }
}

function renderAssetComparison() {
  // Render timeframe buttons
  var btnContainer = document.getElementById('timeframeButtons');
  btnContainer.innerHTML = '';

  TIMEFRAMES.forEach(function(tf) {
    var btn = document.createElement('button');
    btn.className = 'timeframe-btn' + (tf === currentTimeframe ? ' active' : '');
    btn.textContent = tf;
    btn.addEventListener('click', function() {
      currentTimeframe = tf;
      renderAssetComparison();
    });
    btnContainer.appendChild(btn);
  });

  // Get returns for current timeframe
  var returns = dashboardData.asset_returns ? dashboardData.asset_returns[currentTimeframe] : null;
  if (!returns) {
    document.getElementById('assetList').innerHTML = '<p class="data-unavailable">Asset data unavailable for this timeframe</p>';
    document.getElementById('assetsFooter').innerHTML = '';
    return;
  }

  // Sort by return value descending
  var sorted = Object.entries(returns)
    .map(function(entry) {
      return { symbol: entry[0], change: entry[1], name: getAssetName(entry[0]) };
    })
    .sort(function(a, b) { return b.change - a.change; });

  var maxAbsChange = Math.max.apply(null, sorted.map(function(a) { return Math.abs(a.change); }).concat([1]));

  var list = document.getElementById('assetList');
  list.innerHTML = '';

  sorted.forEach(function(asset, index) {
    var isWinner = index === 0;
    var isPositive = asset.change >= 0;
    var barWidth = Math.min(Math.abs(asset.change) / maxAbsChange * 100, 100);

    var card = document.createElement('div');
    card.className = 'asset-card' + (isWinner ? ' winner' : '') + ' fade-in';

    var rankHtml = !isWinner ? '<span class="asset-rank">#' + (index + 1) + '</span>' : '';

    card.innerHTML =
      '<div class="asset-header">' +
        '<div class="asset-info">' +
          '<div class="asset-icon">' + getAssetIcon(asset.symbol) + '</div>' +
          '<div>' +
            '<span class="asset-name">' + escapeHtml(asset.name) + '</span>' +
            '<span class="asset-symbol">' + escapeHtml(asset.symbol) + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="asset-stats">' +
          rankHtml +
          '<div class="asset-change ' + (isPositive ? 'positive' : 'negative') + '">' +
            '<span class="change-icon">' + (isPositive ? '\u2197' : '\u2198') + '</span>' +
            '<span>' + (isPositive ? '+' : '') + asset.change.toFixed(1) + '%</span>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="progress-bar ' + (isWinner ? 'winner' : '') + '">' +
        '<div class="progress-bar-fill ' + (isWinner ? 'winner' : '') + '" style="width: ' + barWidth + '%"></div>' +
      '</div>';

    list.appendChild(card);
  });

  // Footer
  var footer = document.getElementById('assetsFooter');
  var periodMap = {
    '1M': '1 month', '3M': '3 months', '6M': '6 months',
    '1Y': '1 year', '3Y': '3 years', '5Y': '5 years'
  };
  var leader = sorted[0];
  var btcRank = -1;
  for (var i = 0; i < sorted.length; i++) {
    if (sorted[i].symbol === 'BTC') { btcRank = i + 1; break; }
  }

  var leaderText = '';
  if (sorted.length >= 2) {
    var diff = Math.abs(leader.change - sorted[1].change);
    leaderText = escapeHtml(leader.name) + ' leads ' + escapeHtml(sorted[1].name) +
      ' by +' + diff.toFixed(1) + '% this ' + periodMap[currentTimeframe];
  }

  var btcHighlightHtml = btcRank === 1
    ? '<p class="btc-highlight">Bitcoin is #1 this period</p>'
    : '';

  footer.innerHTML =
    '<p class="period-text">Total Return over ' + periodMap[currentTimeframe] + '</p>' +
    '<p class="leader-text">' + leaderText + '</p>' +
    btcHighlightHtml;
}

function getAssetName(symbol) {
  var names = {
    'BTC': 'Bitcoin',
    'GOLD': 'Gold',
    'SP500': 'S&P 500',
    'DXY': 'Dollar Index',
    'QQQ': 'Nasdaq 100',
    'TLT': 'Long Bonds',
    'ETH': 'Ethereum'
  };
  return names[symbol] || symbol;
}

function getAssetIcon(symbol) {
  var icons = {
    'BTC': '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M11.5 11.5v-3h1.25c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5H11.5zm0 1h1.75c.83 0 1.5.67 1.5 1.5s-.67 1.5-1.5 1.5H11.5v-3zM10 6v1H9v1h1v7h-1v1h1v1h1v-1h2v1h1v-1h.5A2.5 2.5 0 0016 13.5c0-.83-.4-1.56-1.02-2 .37-.36.6-.86.6-1.42 0-.59-.26-1.12-.67-1.49A2.5 2.5 0 0013.5 8H13V7h-1V6h-2z"/></svg>',
    'GOLD': '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="6" y="10" width="12" height="8" rx="1"/><rect x="8" y="6" width="8" height="5" rx="1"/></svg>',
    'SP500': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>',
    'DXY': '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>',
    'QQQ': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="7 14 11 10 15 14 19 8"/></svg>',
    'TLT': '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 20h20M5 20V8l7-5 7 5v12"/><rect x="9" y="12" width="6" height="8"/></svg>',
    'ETH': '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M12 1.5l-7 10.17L12 15.72l7-4.05L12 1.5zM5 13.34L12 22.5l7-9.16-7 4.05-7-4.05z"/></svg>'
  };
  return icons[symbol] || '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><circle cx="12" cy="12" r="8"/></svg>';
}

// ============================================================
// NAVIGATION
// ============================================================

// SPA-style tab navigation. Only one section is visible at a time
// (controlled by the .section-active class in styles.css). Switching tabs
// hides the others, updates the URL hash for deep-linking, and scrolls
// back to the top of the page so each tab starts at its header.
function initNavigation() {
  var navLinks = document.querySelectorAll('.nav-link');
  var sections = document.querySelectorAll('.section');
  var validIds = Array.prototype.map.call(sections, function(s) { return s.id; });

  function activateTab(sectionId) {
    if (validIds.indexOf(sectionId) === -1) sectionId = validIds[0] || 'macro';
    sections.forEach(function(s) {
      if (s.id === sectionId) s.classList.add('section-active');
      else s.classList.remove('section-active');
    });
    navLinks.forEach(function(l) {
      if (l.getAttribute('data-section') === sectionId) l.classList.add('active');
      else l.classList.remove('active');
    });
    // Each tab starts at the top — the user shouldn't carry over the
    // scroll position from a previous tab.
    window.scrollTo(0, 0);
  }

  navLinks.forEach(function(link) {
    link.addEventListener('click', function(e) {
      e.preventDefault();
      var targetId = link.getAttribute('data-section');
      activateTab(targetId);
      // Persist the active tab in the URL for refresh / bookmark / share.
      // replaceState (vs pushState) keeps tab switches out of the browser
      // history stack — back button takes the user out of the dashboard
      // rather than between tabs.
      if (history && history.replaceState) {
        history.replaceState(null, '', '#' + targetId);
      }
      // Close mobile menu after selection.
      var navLinksContainer = document.getElementById('navLinks');
      if (navLinksContainer) navLinksContainer.classList.remove('open');
    });
  });

  // On initial load: honour the URL hash, default to first section.
  var initialId = (location.hash || '').replace('#', '');
  activateTab(initialId);

  // Mobile menu toggle (unchanged behaviour).
  var menuBtn = document.getElementById('menuToggle');
  var navLinksContainer = document.getElementById('navLinks');
  if (menuBtn && navLinksContainer) {
    menuBtn.addEventListener('click', function() {
      navLinksContainer.classList.toggle('open');
    });
  }
}

// ============================================================
// INIT
// ============================================================

document.addEventListener('DOMContentLoaded', function() {
  initNavigation();
  loadData();
  loadNodeData();
  loadBtcChart();
  setInterval(loadData, REFRESH_INTERVAL);
  setInterval(loadNodeData, REFRESH_INTERVAL);
  setInterval(loadBtcChart, REFRESH_INTERVAL);
});
