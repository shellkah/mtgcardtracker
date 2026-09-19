const statusEl = document.getElementById("status");
const storePicker = document.getElementById("store-picker");
const searchButton = document.getElementById("search-button");
const summaryEl = document.getElementById("summary");
const totalEl = document.getElementById("total");
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

const FINISH_ICONS = { foil: "✨", surge: "💎" };

function finishLabel(finish) {
  if (!finish || finish === "nonfoil" || finish === "unknown") return "";
  return FINISH_ICONS[finish] ?? finish;
}

function applyFilters(offers) {
  const inStockOnly = document.getElementById("filter-in-stock").checked;
  const nmOnly = document.getElementById("filter-nm").checked;
  const nonfoilOnly = document.getElementById("filter-nonfoil").checked;
  return offers.filter((m) => {
    if (inStockOnly && !m.offer.in_stock) return false;
    if (nmOnly && m.offer.condition !== "NM") return false;
    if (nonfoilOnly && m.offer.finish !== "nonfoil") return false;
    return true;
  });
}

function render() {
  resultsBody.innerHTML = "";
  let totalCents = 0;
  for (const { card, offers } of cards.values()) {
    const row = document.createElement("tr");
    const sorted = applyFilters(offers).sort((a, b) => Number(a.offer.price_sgd) - Number(b.offer.price_sgd));
    const best = sorted[0];
    if (best) totalCents += Math.round(Number(best.offer.price_sgd) * 100);
    const cardCell = document.createElement("td");
    cardCell.textContent = card.name;
    const bestCell = document.createElement("td");
    if (best) {
      const bestSpan = document.createElement("span");
      bestSpan.className = "offer best";
      bestSpan.textContent = [`$${best.offer.price_sgd}`, best.offer.condition, finishLabel(best.offer.finish)]
        .filter(Boolean)
        .join(" ");
      bestCell.append(bestSpan);
    } else {
      bestCell.textContent = "—";
    }
    const offersCell = document.createElement("td");
    for (const match of sorted) {
      const line = document.createElement("a");
      line.className = "offer";
      if (/^https?:/i.test(match.offer.url)) {
        line.href = match.offer.url;
        line.target = "_blank";
      } else {
        line.setAttribute("aria-disabled", "true");
        line.title = "unsafe link hidden";
      }
      line.textContent = [
        `${match.offer.store_name}:`,
        `$${match.offer.price_sgd}`,
        match.offer.condition,
        finishLabel(match.offer.finish),
        match.offer.in_stock ? "" : "(out of stock)",
      ]
        .filter(Boolean)
        .join(" ");
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
  totalEl.textContent = `Best offers total: $${(totalCents / 100).toFixed(2)}`;
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
  totalEl.textContent = "";
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

async function loadLists() {
  const lists = await (await fetch("/api/lists")).json();
  const ul = document.getElementById("saved-lists");
  ul.innerHTML = "";
  for (const list of lists) {
    const li = document.createElement("li");
    const load = document.createElement("button");
    load.textContent = list.name;
    load.onclick = async () => {
      const saved = await (await fetch(`/api/lists/${list.id}`)).json();
      document.getElementById("card-list").value = saved.raw_text;
    };
    const del = document.createElement("button");
    del.textContent = "✕";
    del.onclick = async () => { await fetch(`/api/lists/${list.id}`, { method: "DELETE" }); loadLists(); };
    li.append(load, del);
    ul.append(li);
  }
}

document.getElementById("save-list").onclick = async () => {
  const name = document.getElementById("list-name").value || "Untitled";
  await fetch("/api/lists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, text: document.getElementById("card-list").value }),
  });
  loadLists();
};

for (const id of ["filter-in-stock", "filter-nm", "filter-nonfoil"]) {
  document.getElementById(id).addEventListener("change", render);
}

searchButton.addEventListener("click", startSearch);
loadStores();
loadLists();
