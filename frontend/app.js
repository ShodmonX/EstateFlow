const tg = window.Telegram?.WebApp;
const apiBase = window.ESTATEFLOW_API_BASE || "/api";
let accessToken = sessionStorage.getItem("estateflow_token") || "";
let currentUser = null;
let latestCriteria = null;

if (tg) {
  tg.ready();
  tg.expand();
  document.documentElement.style.setProperty("--accent", tg.themeParams.button_color || "#e85d3f");
}

const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`${apiBase}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || `API xatosi: ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function showMessage(text) { const element = $("#message"); element.textContent = text; element.classList.remove("hidden"); }
function clearMessage() { $("#message").classList.add("hidden"); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char])); }

async function authenticate() {
  if (accessToken) {
    try { currentUser = await request("/webapp/me"); return; } catch { sessionStorage.removeItem("estateflow_token"); accessToken = ""; }
  }
  const initData = tg?.initData || new URLSearchParams(window.location.search).get("initData");
  if (!initData) throw new Error("Mini App Telegram ichidan ochilishi kerak.");
  const session = await request("/webapp/auth", { method: "POST", body: JSON.stringify({ init_data: initData }) });
  accessToken = session.access_token;
  sessionStorage.setItem("estateflow_token", accessToken);
  currentUser = session.user;
}

function formCriteria() {
  const data = new FormData($("#search-form"));
  const criteria = { sort: data.get("sort") || "newest", limit: 20, offset: 0, include_per_person: data.get("include_per_person") === "on" };
  for (const key of ["district", "renovation_level"]) if (data.get(key)) criteria[key] = data.get(key);
  for (const key of ["min_price", "max_price", "rooms"]) if (data.get(key)) criteria[key] = Number(data.get(key));
  return criteria;
}

async function search() {
  clearMessage();
  const criteria = formCriteria();
  latestCriteria = criteria;
  const params = new URLSearchParams(Object.entries(criteria).filter(([, value]) => value !== "" && value !== undefined).map(([key, value]) => [key, String(value)]));
  const response = await request(`/webapp/search/announcements?${params}`);
  $("#result-count").textContent = `${response.metadata.total ?? response.items.length} ta variant`;
  const results = $("#results");
  if (!response.items.length) { results.innerHTML = `<div class="message">Bu filter bo'yicha e'lon topilmadi. Boshqa mezonlarni sinab ko'ring.</div>`; return; }
  results.innerHTML = response.items.map((item) => `<article class="listing-card" tabindex="0" role="button" data-announcement-id="${escapeHtml(item.announcement_id)}"><div class="listing-art"${item.preview_url ? ` style="background-image: linear-gradient(180deg, transparent 45%, rgba(23,33,29,.48)), url('${escapeHtml(item.preview_url)}')"` : ""}><span>${escapeHtml(item.media_count)} ta rasm</span></div><div class="listing-body"><div class="listing-price">${escapeHtml(item.price ?? "Narx yo'q")} ${escapeHtml(item.currency || "")} / ${escapeHtml(item.price_period)}</div><div class="listing-meta"><span>${escapeHtml(item.district || "Tuman ko'rsatilmagan")}</span><span>${item.rooms ? `${escapeHtml(item.rooms)} xona` : "Xona noaniq"}</span><span>${escapeHtml(item.renovation_level || "Remont noaniq")}</span></div><p class="listing-desc">${escapeHtml(item.description)}</p><span class="listing-open">Batafsil ko'rish &#8594;</span>${item.source_url ? `<a class="listing-link" target="_blank" rel="noreferrer" href="${escapeHtml(item.source_url)}">Manbani ochish &#8599;</a>` : ""}</div></article>`).join("");
}

function showSearchView() {
  document.querySelectorAll(".view").forEach((item) => item.classList.add("hidden"));
  $("#search-view").classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === "search"));
}

function detailMarkup(item) {
  const media = item.media.length
    ? `<div class="detail-gallery">${item.media.map((image) => `<img src="${escapeHtml(image.storage_url)}" alt="E'lon rasmi" loading="lazy" />`).join("")}</div>`
    : `<div class="detail-empty-art">Bu e'lon uchun rasm mavjud emas.</div>`;
  const facts = [
    ["Tuman", item.district],
    ["Manzil", item.address],
    ["Xonalar", item.rooms ? `${item.rooms} xona` : null],
    ["Maydon", item.area_sqm ? `${item.area_sqm} m2` : null],
    ["Remont", item.renovation_level],
    ["Mebel", item.furniture === null ? null : item.furniture ? "Bor" : "Yo'q"],
  ].filter(([, value]) => value);
  return `<div class="detail-head"><p class="eyebrow">E'LON TAFSILOTI</p><h2>${escapeHtml(item.price ?? "Narx yo'q")} ${escapeHtml(item.currency || "")} / ${escapeHtml(item.price_period)}</h2><div class="listing-meta"><span>${escapeHtml(item.district || "Tuman ko'rsatilmagan")}</span><span>${item.rooms ? `${escapeHtml(item.rooms)} xona` : "Xona noaniq"}</span></div></div>${media}<div class="detail-panel"><p class="detail-description">${escapeHtml(item.description)}</p>${facts.length ? `<div class="detail-facts">${facts.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>` : ""}${item.phone_numbers.length ? `<p class="detail-phones">Telefon: ${item.phone_numbers.map(escapeHtml).join(", ")}</p>` : ""}${item.source_url ? `<a class="source-button" target="_blank" rel="noreferrer" href="${escapeHtml(item.source_url)}">Telegram kanalidagi e'lonni ochish &#8599;</a>` : `<p class="source-missing">Bu kanal uchun ochiq Telegram linki mavjud emas.</p>`}</div>`;
}

async function openDetail(announcementId, { updateHash = true } = {}) {
  const detailHash = `#/announcements/${encodeURIComponent(announcementId)}`;
  if (updateHash && location.hash !== detailHash) location.hash = detailHash;
  document.querySelectorAll(".view").forEach((item) => item.classList.add("hidden"));
  $("#detail-view").classList.remove("hidden");
  $("#detail-content").innerHTML = `<div class="message">E'lon yuklanmoqda...</div>`;
  try {
    const item = await request(`/webapp/announcements/${encodeURIComponent(announcementId)}`);
    $("#detail-content").innerHTML = detailMarkup(item);
  } catch (error) {
    $("#detail-content").innerHTML = `<div class="message">${escapeHtml(error.message)}</div>`;
  }
}

function routeHash() {
  const match = location.hash.match(/^#\/announcements\/([^/]+)$/);
  if (match) return openDetail(decodeURIComponent(match[1]), { updateHash: false });
  showSearchView();
  return Promise.resolve();
}

async function loadFilters() {
  const filters = await request("/webapp/filters");
  const container = $("#filters-list");
  if (!filters.length) { container.innerHTML = `<div class="message">Hali saqlangan filter yo'q.</div>`; return; }
  container.innerHTML = filters.map((item) => `<div class="filter-row"><div><p>${escapeHtml(item.name)}</p><small>${escapeHtml(item.criteria.district || "Barcha tumanlar")} · ${item.enabled ? "Yoqilgan" : "O'chirilgan"}</small></div><button data-filter-id="${escapeHtml(item.filter_id)}">${item.enabled ? "O'chirish" : "Yoqish"}</button></div>`).join("");
  container.querySelectorAll("button").forEach((button) => button.addEventListener("click", async () => {
    const item = filters.find((filter) => filter.filter_id === button.dataset.filterId);
    await request(`/webapp/filters/${item.filter_id}`, { method: "PATCH", body: JSON.stringify({ enabled: !item.enabled }) });
    await loadFilters();
  }));
}

async function saveFilter() {
  if (!latestCriteria) { showMessage("Avval qidiruvni ishga tushiring."); return; }
  const dialog = $("#save-dialog");
  dialog.showModal();
  $("#save-filter-form").onsubmit = async (event) => {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get("name");
    await request("/webapp/filters", { method: "POST", body: JSON.stringify({ name, criteria: latestCriteria }) });
    dialog.close();
    showMessage("Filter saqlandi.");
    await loadFilters();
  };
}

function activateTab(tab) {
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((item) => item.classList.add("hidden"));
  $(`#${tab}-view`).classList.remove("hidden");
  if (tab === "filters") loadFilters().catch((error) => showMessage(error.message));
}

async function init() {
  await authenticate();
  $("#profile-name").textContent = currentUser.first_name ? `${currentUser.first_name} ${currentUser.last_name || ""}`.trim() : `User ${currentUser.user_id}`;
  $("#profile-id").textContent = `Telegram ID: ${currentUser.user_id}`;
  $("#avatar").textContent = (currentUser.first_name || "E").slice(0, 2).toUpperCase();
  await routeHash();
  if (!location.hash) await search();
}

$("#search-form").addEventListener("submit", (event) => { event.preventDefault(); search().catch((error) => showMessage(error.message)); });
$("#refresh-button").addEventListener("click", () => search().catch((error) => showMessage(error.message)));
$("#save-filter-button").addEventListener("click", () => saveFilter().catch((error) => showMessage(error.message)));
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
$("#results").addEventListener("click", (event) => {
  if (event.target.closest("a")) return;
  const card = event.target.closest("[data-announcement-id]");
  if (card) openDetail(card.dataset.announcementId);
});
$("#results").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const card = event.target.closest("[data-announcement-id]");
  if (card) { event.preventDefault(); openDetail(card.dataset.announcementId); }
});
$("#detail-back-button").addEventListener("click", () => { location.hash = ""; showSearchView(); });
window.addEventListener("hashchange", () => { if (currentUser) routeHash().catch((error) => showMessage(error.message)); });
init().catch((error) => { showMessage(error.message); $("#result-count").textContent = "Kirish kutilmoqda"; });
