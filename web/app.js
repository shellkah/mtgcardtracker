const statusEl = document.getElementById("status");
const storePicker = document.getElementById("store-picker");
const searchButton = document.getElementById("search-button");
const summaryEl = document.getElementById("summary");
const resultsBody = document.getElementById("results-body");

let stores = [];
let cards = new Map(); // key -> { card, offers: [] }
let source = null;

async function loadStores() {
  const response = await fetch("/api/stores");
  const data = await response.json();
  stores = data.stores.filter((s) => s.enabled);
  for (const store of stores) {
    const label = document.createElement("label");
    label.className = "inline";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = store.id;
    checkbox.checked = true;
    label.append(checkbox, document.createTextNode(` ${store.name}`));
    storePicker.append(label);
  }
}

function selectedStoreIds() {
  return [...storePicker.querySelectorAll("input:checked")].map((c) => c.value);
}

function render() {
  resultsBody.innerHTML = "";
  for (const { card, offers } of cards.values()) {
    const row = document.createElement("tr");
    const sorted = [...offers].sort((a, b) => Number(a.offer.price_sgd) - Number(b.offer.price_sgd));
    const best = sorted[0];
    const cardCell = document.createElement("td");
    cardCell.textContent = card.name;
    const bestCell = document.createElement("td");
    if (best) {
      const bestSpan = document.createElement("span");
      bestSpan.className = "offer best";
      bestSpan.textContent = `$${best.offer.price_sgd} ${best.offer.condition ?? ""} ${best.offer.finish}`;
      bestCell.append(bestSpan);
    } else {
      bestCell.textContent = "—";
    }
    const offersCell = document.createElement("td");
    for (const match of sorted) {
      const line = document.createElement("a");
      line.className = "offer";
      line.href = match.offer.url;
      line.target = "_blank";
      line.textContent = `${match.offer.store_name}: $${match.offer.price_sgd} ${match.offer.condition ?? ""} ${match.offer.finish}${match.offer.in_stock ? "" : " (out of stock)"}`;
      const meta = document.createElement("div");
      meta.className = "confidence";
      meta.textContent = match.confidence;
      const wrap = document.createElement("div");
      wrap.append(line, meta);
      offersCell.append(wrap);
    }
    row.append(cardCell, bestCell, offersCell);
    resultsBody.append(row);
  }
}

function upsertResult(event) {
  const entry = cards.get(event.card.key) || { card: event.card, offers: [] };
  const others = entry.offers.filter((m) => m.offer.store_id !== event.store_id);
  entry.offers = [...others, ...event.offers];
  cards.set(event.card.key, entry);
  render();
}

async function startSearch() {
  if (source) { source.close(); source = null; }
  cards = new Map();
  resultsBody.innerHTML = "";
  summaryEl.textContent = "";
  const text = document.getElementById("card-list").value;
  const force = document.getElementById("force-refresh").checked;
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, store_ids: selectedStoreIds(), force_refresh: force }),
  });
  if (!response.ok) { statusEl.textContent = "Failed to start run"; return; }
  const { run_id, card_count } = await response.json();
  statusEl.textContent = `Searching ${card_count} cards…`;
  source = new EventSource(`/api/runs/${run_id}/stream`);
  source.addEventListener("result", (e) => upsertResult(JSON.parse(e.data)));
  source.addEventListener("error", (e) => { try { console.warn("adapter error", JSON.parse(e.data)); } catch {} });
  source.addEventListener("done", (e) => {
    const data = JSON.parse(e.data);
    summaryEl.textContent = `${data.summary.offers} offers, ${data.summary.errors} errors`;
    statusEl.textContent = "Done";
    source.close();
    source = null;
  });
}

searchButton.addEventListener("click", startSearch);
loadStores();
