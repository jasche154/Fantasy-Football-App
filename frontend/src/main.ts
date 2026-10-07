const API_BASE = "http://localhost:8000";

interface RankingRow {
  player: string;
  [key: string]: string | number;
}

interface ApiResponse {
  metric: string;
  season: number;
  results: RankingRow[];
  error?: string;
}

const metricSelect = document.getElementById("metric-select") as HTMLSelectElement;
const seasonInput = document.getElementById("season-input") as HTMLInputElement;
const limitInput = document.getElementById("limit-input") as HTMLInputElement;
const minAttemptsInput = document.getElementById("min-attempts-input") as HTMLInputElement;
const fetchBtn = document.getElementById("fetch-btn") as HTMLButtonElement;
const tableBody = document.getElementById("table-body") as HTMLTableSectionElement;
const errorDiv = document.getElementById("error") as HTMLDivElement;
const loadingDiv = document.getElementById("loading") as HTMLDivElement;
const metricHeader = document.getElementById("metric-header") as HTMLTableHeaderCellElement;

const METRICS = [
  { value: "rush_yards_over_expected", label: "Rush Yards Over Expected / Att" },
  { value: "rush_pct_over_expected", label: "Rush % Over Expected" },
];

function initSelect() {
  METRICS.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m.value;
    opt.textContent = m.label;
    metricSelect.appendChild(opt);
  });
  updateMetricHeader();
}

function updateMetricHeader() {
  const selected = METRICS.find(m => m.value === metricSelect.value);
  if (selected && metricHeader) {
    metricHeader.textContent = selected.label;
  }
}

async function fetchRankings() {
  const metric = metricSelect.value;
  const season = parseInt(seasonInput.value, 10);
  const limit = parseInt(limitInput.value, 10);
  const minAttempts = parseInt(minAttemptsInput.value, 10);

  loadingDiv.style.display = "block";
  errorDiv.style.display = "none";
  tableBody.innerHTML = "";

  try {
    const resp = await fetch(
      `${API_BASE}/api/rankings/rushing?metric=${metric}&season=${season}&limit=${limit}&min_attempts=${minAttempts}`
    );
    const data: ApiResponse = await resp.json();

    if (data.error) {
      showError(data.error);
      return;
    }

    renderTable(data.results, metric);
  } catch (err) {
    showError(`Network error: ${err}`);
  } finally {
    loadingDiv.style.display = "none";
  }
}

function renderTable(rows: RankingRow[], metric: string) {
  if (rows.length === 0) {
    tableBody.innerHTML = `<tr><td colspan="3" style="text-align:center;">No data</td></tr>`;
    return;
  }

  rows.forEach((row, idx) => {
    const tr = document.createElement("tr");
    const value = row[metric];
    const displayValue = typeof value === "number" ? value.toFixed(2) : value;
    tr.innerHTML = `
      <td>${idx + 1}</td>
      <td>${row.player}</td>
      <td>${displayValue}</td>
    `;
    tableBody.appendChild(tr);
  });
}

function showError(msg: string) {
  errorDiv.textContent = msg;
  errorDiv.style.display = "block";
}

fetchBtn.addEventListener("click", fetchRankings);
metricSelect.addEventListener("change", updateMetricHeader);
initSelect();
fetchRankings();