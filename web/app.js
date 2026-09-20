/* ============================================================
   MTG Card Tracker — front-end
   Streams search results over SSE and renders them as a
   sortable, filterable price ledger.
   ============================================================ */
"use strict";

/* ── element handles ────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);

const els = {
  status: $("status"),
  statusText: $("status-text"),
  storePicker: $("store-picker"),
  storeHint: $("store-hint"),
  storesAll: $("stores-all"),
  storesNone: $("stores-none"),
  searchButton: $("search-button"),
  searchLabel: $("search-button-label"),
  cardList: $("card-list"),
  cardCount: $("card-count"),
  formatHint: $("format-hint"),
  clearList: $("clear-list"),
  listName: $("list-name"),
  saveList: $("save-list"),
  savedLists: $("saved-lists"),
  sortMode: $("sort-mode"),
  progress: $("progress"),
  progressFill: $("progress-fill"),
  progressPct: $("progress-pct"),
  summary: $("summary"),
  resultsTitle: $("results-title"),
  results: $("results"),
  emptyState: $("empty-state"),
  resultsList: $("results-list"),
  toasts: $("toasts"),
  cartButton: $("cart-button"),
  cartCount: $("cart-count"),
  cartDrawer: $("cart-drawer"),
  cartClose: $("cart-close"),
  cartAlerts: $("cart-alerts"),
  cartBody: $("cart-body"),
  cartTotal: $("cart-total"),
  cartExcluded: $("cart-excluded"),
  cartRefresh: $("cart-refresh"),
  cartClear: $("cart-clear"),
  cartLive: $("cart-live"),
};

const FILTER_IDS = ["filter-in-stock", "filter-nm", "filter-nonfoil"];
const MATCH_LABEL = {
  exact: "exact printing",
  name_only: "name match",
  ambiguous: "spans printings",
};

/* ── state ──────────────────────────────────────────────────── */
const state = {
  stores: [],
  cards: new Map(), // card.key -> { card, offers: [], cachedStores: Set, errors: [] }
  order: new Map(), // normalised name -> index from the pasted list
  sort: "list",
  run: { active: false, total: 0, done: 0, errors: 0, stores: 0 },
  expanded: new Set(),
  cachedStores: new Set(),
  source: null,
  cart: { items: [] },
};

const cardEls = new Map(); // card.key -> article element

/* ── colours ────────────────────────────────────────────────── */
const STORE_COLORS = {
  greyogre: "#a895b5",
  gameshaven: "#6fb3e8",
  cardscitadel: "#e6d9a8",
  hideout: "#6fce8f",
};
const PALETTE = [
  "#e6d9a8", "#6fb3e8", "#a895b5", "#e87a5c",
  "#6fce8f", "#d9a94a", "#7fd4d4", "#d98ac0",
];
function storeColor(id) {
  if (STORE_COLORS[id]) return STORE_COLORS[id];
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/* ── helpers ────────────────────────────────────────────────── */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalize(name) {
  return String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return "S$" + n.toFixed(2);
}

function isSafeUrl(url) {
  return /^https?:\/\//i.test(String(url || ""));
}

function finishLabel(finish) {
  if (!finish || finish === "nonfoil" || finish === "unknown") return "";
  return finish === "etched" ? "Etched" : finish === "surge" ? "Surge" : "Foil";
}
function finishClass(finish) {
  return "tag--" + (finish === "foil" ? "foil" : finish === "etched" ? "etched" : "surge");
}

const CONDITION_CLASS = { NM: "tag--nm", LP: "tag--lp", MP: "tag--mp", HP: "tag--hp", DMG: "tag--dmg" };
const CONDITION_TITLE = {
  NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played",
  HP: "Heavily Played", DMG: "Damaged",
};

function conditionTag(condition) {
  if (!condition) return "";
  const cls = CONDITION_CLASS[condition] || "";
  const title = CONDITION_TITLE[condition] || condition;
  return `<span class="tag ${cls}" title="${esc(title)}">${esc(condition)}</span>`;
}

function finishTag(finish) {
  const label = finishLabel(finish);
  if (!label) return "";
  return `<span class="tag ${finishClass(finish)}">${esc(label)}</span>`;
}

/* ── card art resolution ────────────────────────────────────── */
/* The preview always shows the store's own listing photo. Store photos are
   the only image that is guaranteed to depict the exact card, printing and
   condition being sold, so nothing else is used as a source (and in
   particular not a Scryfall lookup by printing hint, which is best-effort
   and frequently resolves to the wrong printing). */
function parseHint(hint) {
  const match = /^([^:]+):(.+)$/.exec(String(hint || "").trim());
  return match ? { set: match[1], number: match[2] } : null;
}

function artCandidates(offer) {
  return isSafeUrl(offer?.image_url) ? [offer.image_url] : [];
}

function offerIndexKey(offer) {
  return [offer.store_id, offer.title, offer.condition, offer.finish, offer.url].join("\u0001");
}

/* ── toasts ─────────────────────────────────────────────────── */
function toast({ type = "info", title, msg = "", timeout = 4800 }) {
  const node = document.createElement("div");
  node.className = "toast toast--" + type;
  const icon = type === "error" ? "i-alert" : type === "success" ? "i-check" : "i-zap";
  node.innerHTML = `
    <svg class="ico toast__ico"><use href="#${icon}" /></svg>
    <div class="toast__body">
      <div class="toast__title">${esc(title)}</div>
      ${msg ? `<div class="toast__msg">${esc(msg)}</div>` : ""}
    </div>`;
  els.toasts.append(node);
  const close = () => {
    node.classList.add("is-out");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  };
  if (timeout > 0) setTimeout(close, timeout);
  return close;
}

/* ── status pill ────────────────────────────────────────────── */
function setStatus(text, stateName = "idle") {
  els.statusText.textContent = text;
  els.status.dataset.state = stateName;
}

/* ── input analysis ─────────────────────────────────────────── */
function splitCsvLine(line) {
  const out = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else quoted = false;
      } else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

function analyzeInput(text) {
  const raw = String(text || "").replace(/^\ufeff/, "");
  const lines = raw.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (!lines.length) return { format: "plain", names: [], count: 0 };

  const header = splitCsvLine(lines[0]).map((h) => h.trim().toLowerCase());
  const nameIdx = header.indexOf("name");
  let format = "plain";
  if (header.includes("collector number") && header.includes("edition")) format = "moxfield";
  else if (header.includes("set code") && header.includes("scryfall id")) format = "manabox";
  else if (header.length > 1 && nameIdx >= 0) format = "moxfield";

  const names = [];
  if (format === "plain") {
    for (const line of lines) {
      const m = line.match(/^(\d+)\s*[xX]?\s+(.+)$/);
      names.push((m ? m[2] : line).trim());
    }
  } else {
    const idx = nameIdx >= 0 ? nameIdx : 1;
    for (const line of lines.slice(1)) {
      const cols = splitCsvLine(line);
      const name = (cols[idx] || "").trim();
      if (name) names.push(name);
    }
  }
  const count = new Set(names.map(normalize)).size;
  return { format, names, count };
}

const FORMAT_LABEL = { moxfield: "Moxfield CSV", manabox: "ManaBox CSV", plain: "Plain list" };

function refreshInputMeta() {
  const { format, count } = analyzeInput(els.cardList.value);
  els.clearList.hidden = els.cardList.value.length === 0;
  if (!els.cardList.value.trim()) {
    els.formatHint.textContent = "Paste a list to begin";
    els.formatHint.className = "chip chip--ghost";
    els.cardCount.textContent = "0 cards";
    return;
  }
  els.formatHint.textContent = FORMAT_LABEL[format] || "List";
  els.formatHint.className = "chip";
  els.cardCount.textContent = count === 1 ? "1 card" : count + " cards";
}

/* ── stores ─────────────────────────────────────────────────── */
function renderStores() {
  const zones = {};
  for (const s of state.stores) zones[s.zone] = zones[s.zone] || s.zone;
  els.storePicker.innerHTML = "";
  for (const store of state.stores) {
    const color = storeColor(store.id);
    const label = document.createElement("label");
    label.className = "store is-on";
    label.style.setProperty("--store-color", color);
    label.innerHTML = `
      <input type="checkbox" value="${esc(store.id)}" checked />
      <span class="store__dot" aria-hidden="true"></span>
      <span class="store__body">
        <span class="store__name">${esc(store.name)}</span>
        <span class="store__meta">
          <span>${esc((store.zone || "").toString().replace(/\b\w/g, (c) => c.toUpperCase()))}</span>
          ${store.needs_browser ? '<span class="store__browser">needs browser</span>' : ""}
        </span>
      </span>
      <span class="store__tick" aria-hidden="true"><svg class="ico"><use href="#i-check" /></svg></span>`;
    const input = label.querySelector("input");
    input.addEventListener("change", () => {
      label.classList.toggle("is-on", input.checked);
      updateStoreHint();
    });
    els.storePicker.append(label);
  }
  updateStoreHint();
}

function updateStoreHint() {
  const n = selectedStoreIds().length;
  els.storeHint.textContent = n === 0
    ? "Pick at least one shop"
    : n + (n === 1 ? " shop selected" : " shops selected");
}

function selectedStoreIds() {
  return [...els.storePicker.querySelectorAll("input:checked")].map((c) => c.value);
}

function setAllStores(on) {
  for (const input of els.storePicker.querySelectorAll("input")) {
    input.checked = on;
    input.closest(".store").classList.toggle("is-on", on);
  }
  updateStoreHint();
}

async function loadStores() {
  try {
    const data = await (await fetch("/api/stores")).json();
    state.stores = (data.stores || []).filter((s) => s.enabled);
    renderStores();
  } catch {
    toast({ type: "error", title: "Could not load stores", msg: "Is the server running?" });
  }
}

/* ── filters & sorting ──────────────────────────────────────── */
function filterState() {
  return {
    inStock: $("filter-in-stock").checked,
    nm: $("filter-nm").checked,
    nonfoil: $("filter-nonfoil").checked,
  };
}

function applyFilters(offers) {
  const f = filterState();
  return offers.filter((m) => {
    if (f.inStock && !m.offer.in_stock) return false;
    if (f.nm && m.offer.condition !== "NM") return false;
    if (f.nonfoil && m.offer.finish !== "nonfoil") return false;
    return true;
  });
}

function byPrice(a, b) {
  return Number(a.offer.price_sgd) - Number(b.offer.price_sgd);
}

function bestOffer(entry) {
  const filtered = applyFilters(entry.offers).sort(byPrice);
  return filtered[0] || null;
}

function sortEntries(entries) {
  const mode = state.sort;
  entries.sort((a, b) => {
    if (mode === "name") return a.card.name.localeCompare(b.card.name);
    if (mode === "offers") {
      return applyFilters(b.offers).length - applyFilters(a.offers).length;
    }
    if (mode === "price-asc" || mode === "price-desc") {
      const pa = bestOffer(a);
      const pb = bestOffer(b);
      if (!pa && !pb) return 0;
      if (!pa) return 1;
      if (!pb) return -1;
      const diff = Number(pa.offer.price_sgd) - Number(pb.offer.price_sgd);
      return mode === "price-asc" ? diff : -diff;
    }
    const ia = state.order.has(normalize(a.card.name)) ? state.order.get(normalize(a.card.name)) : 1e9;
    const ib = state.order.has(normalize(b.card.name)) ? state.order.get(normalize(b.card.name)) : 1e9;
    if (ia !== ib) return ia - ib;
    return a.card.name.localeCompare(b.card.name);
  });
}

/* ── card rendering ─────────────────────────────────────────── */
function cardSubHTML(entry) {
  const parts = [];
  if (entry.card.set_code) {
    parts.push(`<span>${esc(String(entry.card.set_code).toUpperCase())}</span>`);
  }
  if (entry.card.collector_number) {
    parts.push(`<span class="dot"></span>`);
    parts.push(`<span>#${esc(entry.card.collector_number)}</span>`);
  }
  if (!parts.length) parts.push(`<span>name-only match</span>`);
  if (entry.errors.length) {
    const msg = entry.errors.map((e) => `${e.store_id}: ${e.message}`).join("\n");
    parts.push(`<span class="dot"></span>`);
    parts.push(`<span class="tag tag--oos" title="${esc(msg)}">store error</span>`);
  }
  if (entry.offers.some((m) => state.cachedStores?.has?.(m.offer.store_id))) {
    parts.push(`<span class="dot"></span>`);
    parts.push(`<span class="tag tag--exact">cached</span>`);
  }
  return parts.join("");
}

function bestHTML(entry) {
  const filtered = applyFilters(entry.offers).sort(byPrice);
  const best = filtered[0];
  if (!best) {
    const reason = entry.offers.length ? "None match filters" : "Not stocked";
    return `<div class="best__row"><span class="best__price best__price--none">${reason}</span></div>`;
  }
  const offer = best.offer;
  const tags = [
    conditionTag(offer.condition),
    finishTag(offer.finish),
    offer.in_stock ? "" : '<span class="tag tag--oos">Out of stock</span>',
    best.confidence === "exact" ? '<span class="tag tag--exact">Exact</span>' : "",
  ].join("");
  const store = isSafeUrl(offer.url)
    ? `<a class="chip chip--gold" href="${esc(offer.url)}" target="_blank" rel="noopener">${esc(offer.store_name)}</a>`
    : `<span class="chip chip--gold">${esc(offer.store_name)}</span>`;
  const saved = cartHasOffer(offer);
  const addBest = saved
    ? '<span class="best__add is-saved" title="In the satchel" aria-hidden="true"><svg class="ico"><use href="#i-check" /></svg></span>'
    : `<button class="best__add" type="button" data-act="add-best" aria-label="Add the best offer for ${esc(entry.card.name)} to the satchel"><svg class="ico"><use href="#i-plus" /></svg><span>Add best</span></button>`;
  return `
    <div class="best__row">
      <span class="best__price">${money(offer.price_sgd)}</span>
      ${store}
      ${addBest}
    </div>
    <div class="best__tags">${tags}</div>`;
}

function offersHTML(entry) {
  const filtered = applyFilters(entry.offers).sort(byPrice);
  if (!filtered.length) {
    return `<p class="offer__meta">${
      entry.offers.length
        ? "Offers were found but all were hidden by your refinements."
        : "No offers for this card at the selected stores."
    }</p>`;
  }
  const prices = filtered.map((m) => Number(m.offer.price_sgd)).filter(Number.isFinite);
  const max = prices.length ? Math.max(...prices) : 0;
  return filtered
    .map((m, i) => {
      const offer = m.offer;
      const price = Number(offer.price_sgd);
      const width = max > 0 && Number.isFinite(price) ? Math.max(6, (price / max) * 100) : 100;
      const color = storeColor(offer.store_id);
      const isBest = i === 0;
      const tags = [
        conditionTag(offer.condition),
        finishTag(offer.finish),
        offer.in_stock ? "" : '<span class="tag tag--oos">Out of stock</span>',
      ].join("");
      const metaBits = [MATCH_LABEL[m.confidence] || m.confidence];
      if (state.cachedStores?.has?.(offer.store_id)) metaBits.push("cached");
      const meta = metaBits.filter(Boolean).map(esc).join(" · ");
      const link = isSafeUrl(offer.url)
        ? `<a class="offer__link" href="${esc(offer.url)}" target="_blank" rel="noopener" title="Open listing at ${esc(offer.store_name)}"><svg class="ico"><use href="#i-external" /></svg></a>`
        : `<span class="offer__link" aria-disabled="true" title="Unsafe link hidden"><svg class="ico"><use href="#i-external" /></svg></span>`;
      const printing = offer.printing_hint ? ` · ${esc(offer.printing_hint)}` : "";
      const saved = cartHasOffer(offer);
      const addBtn = saved
        ? `<span class="offer__add is-saved" title="In the satchel" aria-hidden="true"><svg class="ico"><use href="#i-check" /></svg></span>`
        : `<button class="offer__add" type="button" data-act="add-offer" aria-label="Add ${esc(offer.title)} from ${esc(offer.store_name)} to the satchel"><svg class="ico"><use href="#i-plus" /></svg></button>`;
      return `
        <div class="offer ${isBest ? "is-best" : ""}" style="--store-color:${esc(color)}" data-offer-key="${esc(offerIndexKey(offer))}">
          <span class="offer__fill" style="--w:${width.toFixed(1)}%"></span>
          <span class="offer__dot" aria-hidden="true"></span>
          <div class="offer__body">
            <div class="offer__store">
              ${isBest ? '<span class="tag tag--exact">Best</span>' : ""}
              <span>${esc(offer.store_name)}</span>
              ${tags}
            </div>
            <div class="offer__meta">${meta}${printing}</div>
          </div>
          <span class="offer__price">${money(offer.price_sgd)}</span>
          <button class="offer__preview" type="button" data-act="preview"
            aria-label="Preview ${esc(offer.title)} from ${esc(offer.store_name)}">
            <svg class="ico"><use href="#i-eye" /></svg>
          </button>
          ${addBtn}
          ${link}
        </div>`;
    })
    .join("");
}

function createCardEl(entry) {
  const el = document.createElement("article");
  el.className = "card";
  el.dataset.key = entry.card.key;
  el.innerHTML = `
    <div class="card__main">
      <div class="card__id">
        <span class="qty" title="Quantity">${entry.card.quantity}×</span>
        <div class="card__names">
          <div class="card__name">${esc(entry.card.name)}</div>
          <div class="card__sub" data-slot="sub"></div>
        </div>
      </div>
      <div class="card__best" data-slot="best"></div>
      <div class="card__actions">
        <button class="reveal" type="button" data-act="toggle" aria-expanded="false">
          <span>Offers</span>
          <span class="reveal__count" data-slot="count">0</span>
          <svg class="ico"><use href="#i-chevron" /></svg>
        </button>
      </div>
    </div>
    <div class="offers" data-slot="offers"></div>`;

  // let the entrance animation play once, then pin it so reordering never replays it
  setTimeout(() => { el.style.animation = "none"; }, 700);
  return el;
}

function updateCardEl(el, entry) {
  const offerCount = applyFilters(entry.offers).length;
  el.classList.toggle("is-miss", offerCount === 0);
  el.querySelector('[data-slot="sub"]').innerHTML = cardSubHTML(entry);
  el.querySelector('[data-slot="best"]').innerHTML = bestHTML(entry);
  el.querySelector('[data-slot="count"]').textContent = offerCount;
  el.querySelector('[data-slot="offers"]').innerHTML = offersHTML(entry);
  const reveal = el.querySelector('[data-act="toggle"]');
  const open = state.expanded.has(entry.card.key);
  el.classList.toggle("is-open", open);
  reveal.setAttribute("aria-expanded", String(open));
}

function render() {
  const entries = [...state.cards.values()];
  sortEntries(entries);
  els.emptyState.hidden = entries.length > 0;

  let prev = null;
  for (const entry of entries) {
    let el = cardEls.get(entry.card.key);
    if (!el) {
      el = createCardEl(entry);
      cardEls.set(entry.card.key, el);
      // stagger the arrival of a freshly inserted card
      el.style.setProperty("--i", Math.min(entries.length, 14));
    }
    updateCardEl(el, entry);
    // only move nodes that are actually out of position — moving a node
    // restarts its CSS animation, so avoid it when already correct
    const inPlace = el.parentNode === els.resultsList && el.previousElementSibling === prev;
    if (!inPlace) {
      if (prev && prev.parentNode === els.resultsList) prev.after(el);
      else els.resultsList.prepend(el);
    }
    prev = el;
  }
  renderStats();
}

/* ── stats ──────────────────────────────────────────────────── */
function renderStats() {
  if (!state.cards.size && !state.run.active) {
    els.summary.hidden = true;
    return;
  }
  let total = 0;
  let offers = 0;
  for (const entry of state.cards.values()) {
    const best = bestOffer(entry);
    if (best) total += Math.round(Number(best.offer.price_sgd) * 100);
    offers += applyFilters(entry.offers).length;
  }
  const stats = [
    { label: "Best total", value: money(total / 100), cls: "stat--accent stat--good", icon: "i-coins" },
    { label: "Cards", value: String(state.cards.size), icon: "i-layers" },
    { label: "Offers", value: String(offers), icon: "i-shop" },
    { label: "Stores", value: String(state.run.stores), icon: "i-search" },
    {
      label: "Errors",
      value: String(state.run.errors),
      cls: state.run.errors ? "stat--bad" : "",
      icon: "i-alert",
    },
  ];
  els.summary.hidden = false;
  els.summary.innerHTML = stats
    .map(
      (s, i) => `
      <div class="stat ${s.cls || ""}" style="--i:${i}">
        <div class="stat__label"><svg class="ico"><use href="#${s.icon}" /></svg>${esc(s.label)}</div>
        <div class="stat__value">${esc(s.value)}</div>
      </div>`
    )
    .join("");
}

/* ── progress ───────────────────────────────────────────────── */
function setProgress(done, total) {
  if (!total) { els.progress.hidden = true; return; }
  els.progress.hidden = false;
  const pct = Math.min(100, Math.round((done / total) * 100));
  els.progressFill.style.width = pct + "%";
  els.progressPct.textContent = pct + "%";
  const bar = els.progress.querySelector(".progress__bar");
  if (bar) bar.setAttribute("aria-valuenow", String(pct));
}

/* ── search run ─────────────────────────────────────────────── */
function resetResults() {
  state.cards = new Map();
  state.expanded = new Set();
  state.cachedStores = new Set();
  state.run = { active: false, total: 0, done: 0, errors: 0, stores: selectedStoreIds().length };
  cardEls.clear();
  els.resultsList.innerHTML = "";
  els.progress.hidden = true;
  els.progressFill.style.width = "0%";
  els.summary.hidden = true;
}

async function startSearch() {
  const text = els.cardList.value;
  const { names, count } = analyzeInput(text);
  if (!count) {
    toast({ type: "error", title: "Nothing to search", msg: "Paste a card list first." });
    els.cardList.focus();
    return;
  }
  const storeIds = selectedStoreIds();
  if (!storeIds.length) {
    toast({ type: "error", title: "No stores selected", msg: "Tick at least one shop to sweep." });
    return;
  }

  if (state.source) { state.source.close(); state.source = null; }
  resetResults();
  state.order = new Map();
  names.forEach((name, i) => {
    const key = normalize(name);
    if (!state.order.has(key)) state.order.set(key, i);
  });

  els.searchButton.disabled = true;
  els.searchButton.dataset.busy = "true";
  els.searchLabel.textContent = "Searching…";
  setStatus("Searching…", "searching");
  els.resultsTitle.textContent = `Hunting ${count} card${count === 1 ? "" : "s"}…`;

  let runId;
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        store_ids: storeIds,
        force_refresh: $("force-refresh").checked,
      }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "The server rejected the search.");
    }
    ({ run_id: runId } = await response.json());
  } catch (err) {
    finishRun("error", err.message);
    return;
  }

  state.run.active = true;
  state.run.stores = storeIds.length;
  const source = new EventSource(`/api/runs/${runId}/stream`);
  state.source = source;

  source.addEventListener("started", (e) => {
    const data = JSON.parse(e.data);
    state.run.total = data.total;
    setProgress(0, data.total);
  });

  source.addEventListener("result", (e) => {
    const data = JSON.parse(e.data);
    upsertResult(data);
    state.run.done += 1;
    setProgress(state.run.done, state.run.total);
    render();
  });

  source.addEventListener("error", (e) => {
    if (!e.data) {
      // native EventSource failure, not an adapter error event
      if (source.readyState === EventSource.CLOSED && state.run.active) {
        finishRun("error", "The connection to the server was lost.");
      }
      return;
    }
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.card) {
      const entry = getEntry(data.card);
      entry.errors.push({ store_id: data.store_id, message: data.message });
    }
    state.run.errors += 1;
    state.run.done += 1;
    setProgress(state.run.done, state.run.total);
    render();
  });

  source.addEventListener("done", () => {
    state.run.total = state.run.done || state.run.total;
    setProgress(state.run.done, state.run.total);
    finishRun(state.run.errors ? "warn" : "done");
  });
}

function getEntry(card) {
  let entry = state.cards.get(card.key);
  if (!entry) {
    entry = { card, offers: [], errors: [] };
    state.cards.set(card.key, entry);
  }
  return entry;
}

function upsertResult(event) {
  const entry = getEntry(event.card);
  const others = entry.offers.filter((m) => m.offer.store_id !== event.store_id);
  entry.offers = [...others, ...event.offers];
  if (event.cached) state.cachedStores.add(event.store_id);
}

function finishRun(kind, message) {
  if (state.source) { state.source.close(); state.source = null; }
  state.run.active = false;
  els.searchButton.disabled = false;
  els.searchButton.dataset.busy = "false";
  els.searchLabel.textContent = "Search stores";

  const cards = state.cards.size;
  const stores = state.run.stores;
  els.resultsTitle.textContent = cards
    ? `${cards} card${cards === 1 ? "" : "s"} across ${stores} store${stores === 1 ? "" : "s"}`
    : "Nothing hunted yet";

  if (kind === "error") {
    setStatus("Failed", "error");
    toast({ type: "error", title: "Search failed", msg: message || "" });
    render();
    return;
  }
  if (state.run.errors) {
    setStatus("Done — some errors", "error");
    toast({
      type: "error",
      title: `${state.run.errors} store ${state.run.errors === 1 ? "lookup" : "lookups"} failed`,
      msg: "Those cards were skipped. Try again or check the store is reachable.",
      timeout: 8000,
    });
  } else {
    setStatus("Done", "done");
    const priced = [...state.cards.values()].filter((e) => bestOffer(e)).length;
    toast({
      type: "success",
      title: "Hunt complete",
      msg: `${priced} of ${cards} card${cards === 1 ? "" : "s"} priced across ${stores} store${stores === 1 ? "" : "s"}.`,
    });
  }
  render();
}

/* ── saved lists ────────────────────────────────────────────── */
const FORMAT_BADGE = { moxfield: "Moxfield", manabox: "ManaBox", plain: "Text" };

async function loadLists() {
  let lists = [];
  try {
    lists = await (await fetch("/api/lists")).json();
  } catch {
    return;
  }
  els.savedLists.innerHTML = "";
  if (!lists.length) {
    els.savedLists.innerHTML = `<li class="saved__empty">No saved lists yet.</li>`;
    return;
  }
  for (const list of lists) {
    const li = document.createElement("li");
    li.className = "saved__item";
    li.innerHTML = `
      <button class="saved__load" type="button">
        <svg class="ico"><use href="#i-layers" /></svg>
        <span class="saved__name">${esc(list.name)}</span>
        <span class="chip chip--ghost">${esc(FORMAT_BADGE[list.format] || list.format || "")}</span>
      </button>
      <button class="saved__del" type="button" title="Delete list" aria-label="Delete ${esc(list.name)}">
        <svg class="ico"><use href="#i-trash" /></svg>
      </button>`;
    li.querySelector(".saved__load").addEventListener("click", async () => {
      const saved = await (await fetch(`/api/lists/${list.id}`)).json();
      els.cardList.value = saved.raw_text || "";
      refreshInputMeta();
      toast({ type: "success", title: "List loaded", msg: saved.name });
    });
    li.querySelector(".saved__del").addEventListener("click", async () => {
      if (!confirm(`Delete “${list.name}”?`)) return;
      await fetch(`/api/lists/${list.id}`, { method: "DELETE" });
      loadLists();
    });
    els.savedLists.append(li);
  }
}

async function saveCurrentList() {
  const text = els.cardList.value;
  if (!text.trim()) {
    toast({ type: "error", title: "Empty list", msg: "Paste some cards before saving." });
    return;
  }
  const name = els.listName.value.trim() || "Untitled";
  await fetch("/api/lists", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, text }),
  });
  els.listName.value = "";
  toast({ type: "success", title: "List saved", msg: name });
  loadLists();
}

/* ── card art preview ───────────────────────────────────────── */
const artModal = $("art-modal");
const artEls = artModal
  ? Object.fromEntries(
      [...artModal.querySelectorAll("[data-art]")].map((n) => [n.dataset.art, n])
    )
  : {};

const artCache = new Map();    // offer key -> resolved URL | null
let artAnchor = null;          // the preview button the popover is anchored to
let anchorSeq = 0;             // monotonic id source for per-button anchor names

/* SR-only description: the store photo fallback still needs to say *what*
   the user is looking at, since the picture is the one thing a screen
   reader cannot infer from the surrounding text. */
function artDescription(entry, offer) {
  const bits = [entry.card.name];
  if (offer.title && normalize(offer.title) !== normalize(entry.card.name)) {
    bits.push(`as listed by ${offer.store_name}: ${offer.title}`);
  } else {
    bits.push(`at ${offer.store_name}`);
  }
  if (offer.condition) bits.push(CONDITION_TITLE[offer.condition] || offer.condition);
  const finish = finishLabel(offer.finish);
  if (finish) bits.push(finish);
  return bits.join(", ");
}

function findEntry(offerKey) {
  for (const entry of state.cards.values()) {
    const match = applyFilters(entry.offers).find((m) => offerIndexKey(m.offer) === offerKey);
    if (match) return { entry, match };
  }
  return null;
}

function setArtImage(url) {
  const img = artEls.img;
  if (!url) {
    img.removeAttribute("src");
    img.hidden = true;
    artEls.fallback.hidden = false;
    return;
  }
  artEls.fallback.hidden = true;
  img.hidden = false;
  img.src = url;
}

/* The store photo is used directly: it is not probed with a HEAD request the
   way a cross-origin lookup would need to be, because the only failure mode
   left is a store with no photo at all, which is already known from the data.
   If the image does turn out to be broken the <img> error handler swaps in
   the fallback. */
function resolveArt(offer) {
  return artCandidates(offer)[0] || null;
}

/* Fill the popover with one offer. Shared by hover, keyboard focus and touch
   so every route shows the same content. */
function fillArt(offerKey) {
  const found = findEntry(offerKey);
  if (!found) return false;
  const { entry, match } = found;
  const offer = match.offer;
  const hint = parseHint(offer.printing_hint);
  const printing = hint
    ? `${hint.set.toUpperCase()} ${hint.number.toUpperCase()}`
    : entry.card.set_code
      ? `${String(entry.card.set_code).toUpperCase()}${entry.card.collector_number ? " " + esc(entry.card.collector_number) : ""}`
      : "";

  artEls.store.textContent = offer.store_name;
  artEls.name.textContent = offer.title || entry.card.name;
  artEls.printing.textContent = printing;
  artEls.printing.hidden = !printing;
  artEls.tags.innerHTML = [
    conditionTag(offer.condition),
    finishTag(offer.finish),
    offer.in_stock ? "" : '<span class="tag tag--oos">Out of stock</span>',
    match.confidence === "exact" ? '<span class="tag tag--exact">Exact printing</span>' : "",
  ].join("");
  artEls.price.textContent = money(offer.price_sgd);
  artEls.link.href = isSafeUrl(offer.url) ? offer.url : "#";
  artEls.link.hidden = !isSafeUrl(offer.url);

  artEls.img.alt = artDescription(entry, offer);
  if (!artCache.has(offerKey)) artCache.set(offerKey, resolveArt(offer));
  setArtImage(artCache.get(offerKey));
  return true;
}

function isTouchLike() {
  return !window.matchMedia?.("(hover: hover)").matches;
}

/* Positioning is CSS Anchor Positioning's job, not ours. Each button gets a
   unique anchor name; pointing --art-anchor-name at the hovered one is enough
   for the browser to place the popover, flip it at edges and keep it on
   screen. No measuring, no scroll/resize listeners, no reflow races. */
function anchorNameFor(button) {
  if (!button.dataset.artAnchor) {
    button.dataset.artAnchor = "art-anchor-" + (anchorSeq += 1);
  }
  artModal.style.setProperty("--art-anchor-name", `--${button.dataset.artAnchor}`);
  button.style.setProperty("anchor-name", `--${button.dataset.artAnchor}`);
  return button.dataset.artAnchor;
}

function showArt(button, offerKey) {
  if (!fillArt(offerKey)) return;
  artAnchor = button;
  artModal.dataset.triggerOffer = offerKey;
  anchorNameFor(button);
  if (!artModal.matches(":popover-open")) artModal.showPopover();
}

/* Shown only while the user is still on the same offer, so moving between
   rows swaps the content in place instead of flickering the popover. */
function hideArt() {
  if (artModal.dataset.stuck === "true") return; // a click pinned it open
  artModal.dataset.triggerOffer = "";
  if (artModal.matches(":popover-open")) artModal.hidePopover();
}

function pinArt() {
  artModal.dataset.stuck = "true";
}

function unpinArt() {
  artModal.dataset.stuck = "false";
  hideArt();
}

function wireArtModal() {
  if (!artModal) return;
  // a failed store photo falls back to the placeholder rather than leaving the
  // browser's broken-image icon in the frame
  artEls.img.addEventListener("error", () => {
    if (!artEls.img.getAttribute("src")) return; // cleared on purpose
    setArtImage(null);
  });

  // let the pointer travel from the button into the popover (e.g. to click
  // "Open listing") without it vanishing on the way
  artModal.addEventListener("pointerenter", () => { artModal.dataset.hovering = "true"; });
  artModal.addEventListener("pointerleave", () => {
    artModal.dataset.hovering = "false";
    if (!isTouchLike()) hideArt();
  });
  if (!isTouchLike()) {
    // hovering the popover unlocks a permanently pinned preview
    artModal.addEventListener("mouseenter", () => { artModal.dataset.stuck = "false"; });
  }

  artEls.link.addEventListener("click", unpinArt);
  artModal.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); unpinArt(); }
  });

  /* Scrolling is the one case the browser cannot re-clamp: the popover stays
     glued to its anchor, so if the page moves the row toward an edge the
     preview can end up half off screen. It cannot be re-evaluated without the
     pointer moving, so the honest behaviour is to dismiss it — the pointer is
     about to land on a different row anyway. `scroll` does not bubble, so the
     listener is registered in the capture phase to catch the offer drawers,
     which are created per card as results stream in. */
  const dismissOnScroll = () => { if (artModal.matches(":popover-open")) hideArt(); };
  window.addEventListener("scroll", dismissOnScroll, { passive: true });
  document.addEventListener("scroll", dismissOnScroll, { passive: true, capture: true });
}

/* ── the satchel ────────────────────────────────────────────── */
function cartIdentity(item) {
  return [item.store_id, item.title, item.condition, item.finish, item.url].join("\u0001");
}

function cartHasOffer(offer) {
  const key = [offer.store_id, offer.title, offer.condition, offer.finish, offer.url].join("\u0001");
  return state.cart.items.some((item) => cartIdentity(item) === key);
}

function cartLineTotal(item) {
  return Number(item.price_sgd) * Number(item.quantity);
}

function cartCountable(item) {
  return item.status === "ok" && Boolean(item.in_stock);
}

function cartGroups() {
  const groups = new Map();
  for (const item of state.cart.items) {
    if (!groups.has(item.store_id)) {
      groups.set(item.store_id, { store_id: item.store_id, store_name: item.store_name, items: [] });
    }
    groups.get(item.store_id).items.push(item);
  }
  return [...groups.values()];
}

function duplicateCartCardKeys() {
  const seen = new Set();
  const dupes = new Set();
  for (const item of state.cart.items) {
    if (seen.has(item.card_key)) dupes.add(item.card_key);
    seen.add(item.card_key);
  }
  return dupes;
}

function renderCartBadge() {
  const count = state.cart.items.length;
  els.cartCount.textContent = String(count);
  els.cartCount.hidden = count === 0;
}

function cartLineHTML(item, dupes) {
  const countable = cartCountable(item);
  const tags = [
    conditionTag(item.condition),
    finishTag(item.finish),
    countable ? "" : `<span class="tag tag--oos">${item.status === "unavailable" ? "No longer listed" : "Out of stock"}</span>`,
    dupes.has(item.card_key) ? '<span class="tag tag--warn">Duplicate choice</span>' : "",
  ].join("");
  const thumb = isSafeUrl(item.image_url)
    ? `<img class="satchel__thumb" src="${esc(item.image_url)}" alt="" loading="lazy" />`
    : '<span class="satchel__thumb satchel__thumb--none" aria-hidden="true"><svg class="ico"><use href="#i-stack" /></svg></span>';
  const link = isSafeUrl(item.url)
    ? `<a class="satchel__link" href="${esc(item.url)}" target="_blank" rel="noopener" title="Open listing"><svg class="ico"><use href="#i-external" /></svg></a>`
    : "";
  return `
    <li class="satchel__line${countable ? "" : " is-dim"}" data-item-id="${item.id}">
      ${thumb}
      <div class="satchel__line-body">
        <div class="satchel__line-name">${esc(item.card_name)}</div>
        <div class="satchel__line-sub">${item.printing_hint ? esc(item.printing_hint) : "name match"}${tags ? " " + tags : ""}</div>
      </div>
      <div class="satchel__qty">
        <button class="satchel__step" type="button" data-act="dec" aria-label="Decrease quantity of ${esc(item.card_name)}"><svg class="ico"><use href="#i-minus" /></svg></button>
        <span class="satchel__qty-num">${item.quantity}</span>
        <button class="satchel__step" type="button" data-act="inc" aria-label="Increase quantity of ${esc(item.card_name)}"><svg class="ico"><use href="#i-plus" /></svg></button>
      </div>
      <div class="satchel__line-price">${money(cartLineTotal(item))}</div>
      ${link}
      <button class="satchel__remove" type="button" data-act="remove" aria-label="Remove ${esc(item.card_name)} from the satchel"><svg class="ico"><use href="#i-trash" /></svg></button>
    </li>`;
}

function renderCart() {
  renderCartBadge();
  const items = state.cart.items;
  if (!items.length) {
    els.cartBody.innerHTML = '<div class="satchel__empty"><svg class="ico"><use href="#i-satchel" /></svg><p>Nothing in the satchel yet. Add offers from your hunt results.</p></div>';
    els.cartAlerts.hidden = true;
    els.cartTotal.textContent = money(0);
    els.cartExcluded.hidden = true;
    return;
  }
  const dupes = duplicateCartCardKeys();
  let total = 0;
  let excluded = 0;
  els.cartBody.innerHTML = cartGroups().map((group) => {
    let subtotal = 0;
    const lines = group.items.map((item) => {
      if (cartCountable(item)) {
        subtotal += cartLineTotal(item);
        total += cartLineTotal(item);
      } else {
        excluded += 1;
      }
      return cartLineHTML(item, dupes);
    }).join("");
    return `
      <section class="satchel__store">
        <header class="satchel__store-head">
          <span class="satchel__store-dot" style="--store-color:${esc(storeColor(group.store_id))}" aria-hidden="true"></span>
          <span class="satchel__store-name">${esc(group.store_name)}</span>
          <span class="satchel__store-sub">${money(subtotal)}</span>
        </header>
        <ul class="satchel__lines">${lines}</ul>
      </section>`;
  }).join("");
  els.cartTotal.textContent = money(total);
  els.cartExcluded.hidden = excluded === 0;
  els.cartExcluded.textContent = excluded
    ? `${excluded} line${excluded === 1 ? "" : "s"} excluded (out of stock or unavailable)`
    : "";
  const dupCount = dupes.size;
  const alerts = [
    dupCount ? `<span class="satchel__alert"><svg class="ico"><use href="#i-alert" /></svg> ${dupCount} card${dupCount === 1 ? "" : "s"} appear twice — remove one before buying.</span>` : "",
    excluded ? `<span class="satchel__alert"><svg class="ico"><use href="#i-alert" /></svg> ${excluded} line${excluded === 1 ? "" : "s"} can’t be bought as-is.</span>` : "",
  ].filter(Boolean);
  els.cartAlerts.hidden = alerts.length === 0;
  els.cartAlerts.innerHTML = alerts.join("");
}

async function loadCart() {
  try {
    const data = await (await fetch("/api/cart")).json();
    state.cart.items = data.items || [];
  } catch {
    state.cart.items = [];
  }
  renderCart();
}

function openCart() {
  if (!els.cartDrawer.open) els.cartDrawer.showModal();
  loadCart();
}

function closeCart() {
  if (els.cartDrawer.open) els.cartDrawer.close();
}

els.cartButton.addEventListener("click", openCart);
els.cartClose.addEventListener("click", closeCart);
els.cartDrawer.addEventListener("click", (e) => {
  if (e.target === els.cartDrawer) closeCart(); // backdrop click
});

async function addOfferToCart(entry, offer) {
  const payload = {
    card: {
      key: entry.card.key,
      name: entry.card.name,
      set_code: entry.card.set_code,
      collector_number: entry.card.collector_number,
      quantity: entry.card.quantity,
    },
    offer: {
      store_id: offer.store_id,
      store_name: offer.store_name,
      title: offer.title,
      url: offer.url,
      price_sgd: String(offer.price_sgd),
      currency: offer.currency || "SGD",
      condition: offer.condition,
      finish: offer.finish,
      in_stock: offer.in_stock,
      printing_hint: offer.printing_hint,
      image_url: offer.image_url,
      fetched_at: offer.fetched_at,
    },
  };
  try {
    const res = await fetch("/api/cart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error("rejected");
    const data = await res.json();
    toast({
      type: data.created ? "success" : "info",
      title: data.created ? "Added to the satchel" : "Already in the satchel",
      msg: `${entry.card.name} · ${offer.store_name}`,
    });
    await loadCart();
    render();
  } catch {
    toast({ type: "error", title: "Could not add", msg: "The satchel didn’t save that offer." });
  }
}

/* ── wiring ─────────────────────────────────────────────────── */
els.searchButton.addEventListener("click", startSearch);
els.cardList.addEventListener("input", refreshInputMeta);
els.cardList.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    startSearch();
  }
});
els.clearList.addEventListener("click", () => {
  els.cardList.value = "";
  refreshInputMeta();
  els.cardList.focus();
});
els.saveList.addEventListener("click", saveCurrentList);
els.listName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveCurrentList();
});
els.storesAll.addEventListener("click", () => setAllStores(true));
els.storesNone.addEventListener("click", () => setAllStores(false));
els.sortMode.addEventListener("change", () => {
  state.sort = els.sortMode.value;
  render();
});
for (const id of FILTER_IDS) $(id).addEventListener("change", render);

/* ── preview triggers ───────────────────────────────────────── */
function offerButton(target) {
  const preview = target.closest?.('[data-act="preview"]');
  if (!preview) return null;
  const row = preview.closest(".offer");
  return row ? { button: preview, offerKey: row.dataset.offerKey } : null;
}

// hover (pointer devices): show on enter, and swap content while the pointer
// moves between offers without leaving the list
els.resultsList.addEventListener("pointerover", (e) => {
  if (isTouchLike()) return;
  const hit = offerButton(e.target);
  if (!hit) {
    if (!els.resultsList.contains(e.relatedTarget)) hideArt();
    return;
  }
  const shown = artModal.dataset.triggerOffer;
  if (shown === hit.offerKey) return;
  if (shown && artModal.dataset.hovering === "true") return; // pointer is on the popover
  showArt(hit.button, hit.offerKey);
});
els.resultsList.addEventListener("pointerleave", () => {
  if (!isTouchLike()) hideArt();
});

// keyboard: focusing the button previews it, Tab moves through rows
els.resultsList.addEventListener("focusin", (e) => {
  const hit = offerButton(e.target);
  if (hit) showArt(hit.button, hit.offerKey);
  else hideArt();
});

// click: pin the preview open on touch (where there is no hover), and give
// pointer users a way to keep it on screen
els.resultsList.addEventListener("click", (e) => {
  const hit = offerButton(e.target);
  if (hit) {
    if (artModal.dataset.stuck === "true" && artModal.dataset.triggerOffer === hit.offerKey) {
      unpinArt();
    } else {
      showArt(hit.button, hit.offerKey);
      pinArt();
    }
    return;
  }
  const addOffer = e.target.closest('[data-act="add-offer"]');
  if (addOffer) {
    const row = addOffer.closest(".offer");
    const found = row && findEntry(row.dataset.offerKey);
    if (found) addOfferToCart(found.entry, found.match.offer);
    return;
  }
  const addBest = e.target.closest('[data-act="add-best"]');
  if (addBest) {
    const card = addBest.closest(".card");
    const entry = card && state.cards.get(card.dataset.key);
    const best = entry && bestOffer(entry);
    if (entry && best) addOfferToCart(entry, best.offer);
    return;
  }
  const toggle = e.target.closest('[data-act="toggle"]');
  if (!toggle) return;
  const card = toggle.closest(".card");
  const key = card.dataset.key;
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  const open = state.expanded.has(key);
  card.classList.toggle("is-open", open);
  toggle.setAttribute("aria-expanded", String(open));
});

/* ── boot ───────────────────────────────────────────────────── */
wireArtModal();
refreshInputMeta();
loadStores();
loadLists();
loadCart();
