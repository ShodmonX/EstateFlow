const tg = window.Telegram?.WebApp;
const apiBase = window.ESTATEFLOW_API_BASE || "/api";
const pageLimit = 20;

let accessToken = sessionStorage.getItem("estateflow_token") || "";
let currentUser = null;
let currentItems = [];
let savedFilters = new Map();
let latestCriteria = null;
let latestNlpExtraction = null;
let latestNlpTags = [];
let latestNlpQuery = "";
let nlpRequestVersion = 0;
let searchRequestVersion = 0;
let formRunVersion = 0;
let searchController = null;

if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor?.("#0d1825");
  tg.setBackgroundColor?.("#0b1420");
}

const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`${apiBase}${path}`, { ...options, headers });
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const validationMessage = Array.isArray(body?.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join(". ")
      : body?.detail;
    const error = new Error(body?.error?.message || validationMessage || `API xatosi: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[char]));
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const parsed = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}

function showMessage(text, type = "error") {
  const element = $("#message");
  element.textContent = text;
  element.classList.toggle("success", type === "success");
  element.classList.remove("hidden");
}

function clearMessage() {
  const element = $("#message");
  element.textContent = "";
  element.classList.remove("success");
  element.classList.add("hidden");
}

function setBusy(button, busy, busyText = "Kutilmoqda...") {
  if (!button) return;
  if (busy) {
    if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = busyText;
  } else {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
}

async function authenticate() {
  if (accessToken) {
    try {
      currentUser = await request("/webapp/me");
      return;
    } catch {
      sessionStorage.removeItem("estateflow_token");
      accessToken = "";
    }
  }
  const initData = tg?.initData || new URLSearchParams(window.location.search).get("initData");
  if (!initData) throw new Error("Mini App Telegram ichidan ochilishi kerak.");
  const session = await request("/webapp/auth", {
    method: "POST",
    body: JSON.stringify({ init_data: initData }),
  });
  accessToken = session.access_token;
  sessionStorage.setItem("estateflow_token", accessToken);
  currentUser = session.user;
}

function parseDistricts(value) {
  const seen = new Set();
  return String(value || "")
    .split(/[,;\n]+|\s+(?:va|yoki)\s+/i)
    .map((item) => item.trim())
    .filter((item) => {
      const key = item.toLocaleLowerCase("uz");
      if (!item || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function formCriteria(nlpCriteria = null, { offset = 0 } = {}) {
  const data = new FormData($("#search-form"));
  const criteria = {
    sort: data.get("sort") || "newest",
    limit: pageLimit,
    offset,
  };
  const supportedKeys = [
    "min_price",
    "max_price",
    "include_per_person",
    "district",
    "districts",
    "rooms",
    "renovation_level",
    "audience_tag",
  ];
  for (const key of supportedKeys) {
    const value = nlpCriteria?.[key];
    if (Array.isArray(value) ? value.length : value !== null && value !== undefined && value !== "") {
      criteria[key] = value;
    }
  }
  const manualDistricts = parseDistricts(data.get("districts_input"));
  if (manualDistricts.length) {
    criteria.districts = manualDistricts;
    criteria.district = manualDistricts[0];
  }
  if (data.get("renovation_level")) criteria.renovation_level = data.get("renovation_level");
  for (const key of ["min_price", "max_price", "rooms"]) {
    if (data.get(key) !== "") criteria[key] = Number(data.get(key));
  }
  criteria.include_per_person = data.get("include_per_person") === "on"
    || Boolean(nlpCriteria?.include_per_person);
  return criteria;
}

function criteriaToTags(criteria = {}) {
  const tags = [];
  const districts = criteria.districts?.length
    ? criteria.districts
    : criteria.district
      ? [criteria.district]
      : [];
  districts.forEach((district) => tags.push({ key: "district", value: district, applied: true }));
  if (criteria.rooms !== null && criteria.rooms !== undefined) {
    tags.push({ key: "rooms", value: `${criteria.rooms} xona`, applied: true });
  }
  if (criteria.min_price !== null && criteria.min_price !== undefined) {
    tags.push({ key: "min_price", value: `${criteria.min_price} dan`, applied: true });
  }
  if (criteria.max_price !== null && criteria.max_price !== undefined) {
    tags.push({ key: "max_price", value: `${criteria.max_price} gacha`, applied: true });
  }
  if (criteria.include_per_person) {
    tags.push({ key: "include_per_person", value: "Bir kishilik ham", applied: true });
  }
  if (criteria.renovation_level) {
    tags.push({ key: "renovation_level", value: renovationLabel(criteria.renovation_level), applied: true });
  }
  return tags;
}

function renderNlpTags(tags = [], status = "Mezonlarni ajratish uchun yozishni boshlang") {
  latestNlpTags = tags;
  $("#nlp-status").textContent = status;
  $("#nlp-tags-list").innerHTML = tags.map((tag, index) => `
    <span class="nlp-tag ${tag.applied ? "" : "unsupported"}"${tag.applied ? "" : " title=\"Qidiruvga qo'shilmagan qo'shimcha shart\""}>
      <span>${escapeHtml(tag.value)}</span>
      <button type="button" class="nlp-tag-remove" data-nlp-tag-index="${index}" aria-label="${escapeHtml(tag.value)} shartini olib tashlash" title="Shartni olib tashlash">&times;</button>
    </span>
  `).join("");
}

async function extractNlp(query) {
  const normalized = query.trim();
  if (!normalized) {
    latestNlpExtraction = null;
    latestNlpQuery = "";
    renderNlpTags();
    return null;
  }
  const requestVersion = ++nlpRequestVersion;
  renderNlpTags([], "Mezonlar ajratilmoqda...");
  const response = await request("/webapp/search/nlp", {
    method: "POST",
    body: JSON.stringify({ query: normalized }),
  });
  if (requestVersion !== nlpRequestVersion) return null;
  latestNlpExtraction = response;
  latestNlpQuery = normalized;
  renderNlpTags(response.tags || [], response.tags?.length ? "Tushunilgan mezonlar" : "Aniq mezon topilmadi");
  return response;
}

function removeNlpTag(index) {
  const tag = latestNlpTags[index];
  if (!tag || !latestNlpExtraction) return Promise.resolve();
  const criteria = { ...latestNlpExtraction.criteria };
  if (tag.applied) {
    if (tag.key === "district") {
      const districts = (Array.isArray(criteria.districts) ? criteria.districts : [criteria.district])
        .filter((district) => district && district.toLocaleLowerCase("uz") !== tag.value.toLocaleLowerCase("uz"));
      criteria.districts = districts;
      criteria.district = districts[0] || null;
    } else if (Object.prototype.hasOwnProperty.call(criteria, tag.key)) {
      criteria[tag.key] = tag.key === "include_per_person" ? false : null;
    }
  }
  latestNlpExtraction = { ...latestNlpExtraction, criteria };
  renderNlpTags(latestNlpTags.filter((_, tagIndex) => tagIndex !== index), "Mezon olib tashlandi");
  return search({ nlpExtraction: latestNlpExtraction });
}

function renderLoading() {
  $("#results").innerHTML = Array.from({ length: 3 }, () => `
    <article class="listing-card skeleton-card" aria-hidden="true">
      <div class="skeleton skeleton-art"></div>
      <div class="skeleton-lines"><i class="skeleton"></i><i class="skeleton"></i><i class="skeleton"></i></div>
    </article>
  `).join("");
}

function formatNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("uz-UZ", { maximumFractionDigits: 0 }).format(numeric)
    : value;
}

function periodLabel(value) {
  return { daily: "kun", monthly: "oy", one_time: "bir martalik" }[value] || value || "oy";
}

function renovationLabel(value) {
  return {
    none: "Remontsız",
    basic: "Oddiy remont",
    good: "Yaxshi remont",
    euro: "Yevro remont",
    luxury: "Lyuks remont",
  }[value] || value || "";
}

function priceMarkup(item) {
  if (item.price === null || item.price === undefined) return "Narx kelishiladi";
  const basis = item.price_basis === "per_person" ? " · kishi boshiga" : "";
  return `${escapeHtml(formatNumber(item.price))} ${escapeHtml(item.currency || "")} / ${escapeHtml(periodLabel(item.price_period))}${basis}`;
}

function listingMarkup(item) {
  const previewUrl = safeUrl(item.preview_url);
  const sourceUrl = safeUrl(item.source_url);
  const art = previewUrl
    ? `<img src="${escapeHtml(previewUrl)}" alt="" loading="lazy" />`
    : `<span class="listing-image-fallback" aria-hidden="true">⌂</span>`;
  return `
    <article class="listing-card" tabindex="0" aria-label="E'lonni batafsil ko'rish" data-announcement-id="${escapeHtml(item.announcement_id)}">
      <div class="listing-art">${art}<span class="media-count">${escapeHtml(item.media_count || 0)} rasm</span></div>
      <div class="listing-body">
        <div class="listing-price">${priceMarkup(item)}</div>
        <div class="listing-meta">
          <span>${escapeHtml(item.district || "Tuman noaniq")}</span>
          <span>${item.rooms ? `${escapeHtml(item.rooms)} xona` : "Xona noaniq"}</span>
          ${item.renovation_level ? `<span>${escapeHtml(renovationLabel(item.renovation_level))}</span>` : ""}
        </div>
        <p class="listing-desc">${escapeHtml(item.description || "Tavsif mavjud emas.")}</p>
        <div class="listing-footer"><span class="listing-open">Batafsil ko'rish →</span>${sourceUrl ? `<a class="listing-link" target="_blank" rel="noopener noreferrer" href="${escapeHtml(sourceUrl)}">Manba ↗</a>` : ""}</div>
      </div>
    </article>
  `;
}

function attachImageFallbacks(container) {
  container.querySelectorAll(".listing-art img").forEach((image) => {
    image.addEventListener("error", () => {
      image.replaceWith(Object.assign(document.createElement("span"), {
        className: "listing-image-fallback",
        textContent: "⌂",
      }));
    }, { once: true });
  });
}

async function search({ nlpExtraction = latestNlpExtraction, append = false, offset = 0 } = {}) {
  clearMessage();
  const requestVersion = ++searchRequestVersion;
  searchController?.abort();
  searchController = new AbortController();
  const searchButton = $("#search-button");
  const loadMoreButton = $("#load-more-button");
  if (!append) {
    setBusy(searchButton, true, "Qidirilmoqda...");
    $("#result-count").textContent = "Qidirilmoqda...";
    renderLoading();
    loadMoreButton.classList.add("hidden");
  } else {
    setBusy(loadMoreButton, true, "Yuklanmoqda...");
  }

  const criteria = formCriteria(nlpExtraction?.criteria || null, { offset });
  latestCriteria = { ...criteria, offset: 0 };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(criteria)) {
    if (value === "" || value === undefined || value === null) continue;
    if (Array.isArray(value)) value.forEach((item) => params.append(key, String(item)));
    else params.append(key, String(value));
  }

  try {
    const response = await request(`/webapp/search/announcements?${params}`, {
      signal: searchController.signal,
    });
    if (requestVersion !== searchRequestVersion) return;
    currentItems = append ? [...currentItems, ...response.items] : response.items;
    const total = response.metadata.total ?? currentItems.length;
    $("#result-count").textContent = `${total} ta variant`;
    const results = $("#results");
    if (!currentItems.length) {
      results.innerHTML = `<div class="message">Bu mezonlar bo'yicha e'lon topilmadi. Qidiruvni biroz kengaytirib ko'ring.</div>`;
    } else {
      results.innerHTML = currentItems.map(listingMarkup).join("");
      attachImageFallbacks(results);
    }
    const loaded = response.metadata.offset + response.metadata.returned;
    const hasMore = response.metadata.total === null
      ? response.metadata.returned === response.metadata.limit
      : loaded < response.metadata.total;
    loadMoreButton.classList.toggle("hidden", !hasMore);
    loadMoreButton.dataset.nextOffset = String(loaded);
  } catch (error) {
    if (error.name === "AbortError") return;
    if (!append) {
      currentItems = [];
      $("#results").innerHTML = "";
      $("#result-count").textContent = "Qidiruv bajarilmadi";
    }
    throw error;
  } finally {
    if (requestVersion === searchRequestVersion) {
      setBusy(searchButton, false);
      setBusy(loadMoreButton, false);
    }
  }
}

function showSearchView() {
  document.querySelectorAll(".view").forEach((item) => item.classList.add("hidden"));
  $("#search-view").classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === "search"));
}

function detailMarkup(item) {
  const media = (item.media || [])
    .map((image) => safeUrl(image.storage_url))
    .filter(Boolean);
  const gallery = media.length
    ? `<div class="detail-gallery">${media.map((url) => `<img src="${escapeHtml(url)}" alt="E'lon rasmi" loading="lazy" />`).join("")}</div>`
    : `<div class="detail-empty-art">Bu e'lon uchun rasm mavjud emas.</div>`;
  const facts = [
    ["Tuman", item.district],
    ["Manzil", item.address],
    ["Xonalar", item.rooms ? `${item.rooms} xona` : null],
    ["Maydon", item.area_sqm ? `${formatNumber(item.area_sqm)} m²` : null],
    ["Qavat", item.floor ? `${item.floor}${item.total_floors ? ` / ${item.total_floors}` : ""}` : null],
    ["Remont", item.renovation_level ? renovationLabel(item.renovation_level) : null],
    ["Mebel", item.furniture === null ? null : item.furniture ? "Bor" : "Yo'q"],
  ].filter(([, value]) => value);
  const sourceUrl = safeUrl(item.source_url);
  return `
    <div class="detail-head"><p class="eyebrow">E'LON TAFSILOTI</p><h2>${priceMarkup(item)}</h2><div class="listing-meta"><span>${escapeHtml(item.district || "Tuman noaniq")}</span>${item.rooms ? `<span>${escapeHtml(item.rooms)} xona</span>` : ""}</div></div>
    ${gallery}
    <div class="detail-panel">
      <p class="detail-description">${escapeHtml(item.description || "Tavsif mavjud emas.")}</p>
      ${facts.length ? `<div class="detail-facts">${facts.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>` : ""}
      ${item.phone_numbers?.length ? `<p class="detail-phones">Telefon: ${item.phone_numbers.map(escapeHtml).join(", ")}</p>` : ""}
      ${sourceUrl ? `<a class="source-button" target="_blank" rel="noopener noreferrer" href="${escapeHtml(sourceUrl)}">Telegram'dagi e'lonni ochish ↗</a>` : `<p class="source-missing">Bu e'lon uchun ochiq Telegram havolasi mavjud emas.</p>`}
    </div>
  `;
}

async function openDetail(announcementId, { updateHash = true } = {}) {
  const detailHash = `#/announcements/${encodeURIComponent(announcementId)}`;
  if (updateHash && location.hash !== detailHash) history.pushState(null, "", detailHash);
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

async function routeHash() {
  const match = location.hash.match(/^#\/announcements\/([^/]+)$/);
  if (match) {
    await openDetail(decodeURIComponent(match[1]), { updateHash: false });
    return;
  }
  showSearchView();
  if (!currentItems.length) await search();
}

function filterSummary(criteria) {
  const districts = criteria.districts?.length ? criteria.districts.join(", ") : criteria.district;
  const parts = [districts || "Barcha tumanlar"];
  if (criteria.rooms !== null && criteria.rooms !== undefined) parts.push(`${criteria.rooms} xona`);
  if (criteria.max_price !== null && criteria.max_price !== undefined) parts.push(`${criteria.max_price} gacha`);
  return parts.join(" · ");
}

async function loadFilters() {
  const container = $("#filters-list");
  container.innerHTML = `<div class="message">Saqlangan filtrlar yuklanmoqda...</div>`;
  const filters = await request("/webapp/filters");
  savedFilters = new Map(filters.map((item) => [item.filter_id, item]));
  if (!filters.length) {
    container.innerHTML = `<div class="message">Hali saqlangan filtr yo'q. Qidiruvdan so'ng filtrni saqlashingiz mumkin.</div>`;
    return;
  }
  container.innerHTML = filters.map((item) => `
    <article class="filter-row">
      <div><p>${escapeHtml(item.name)}</p><small>${escapeHtml(filterSummary(item.criteria))} · ${item.enabled ? "Yoqilgan" : "O'chirilgan"}</small></div>
      <div class="filter-actions">
        <button type="button" data-filter-action="apply" data-filter-id="${escapeHtml(item.filter_id)}">Qidirish</button>
        <button type="button" data-filter-action="toggle" data-filter-id="${escapeHtml(item.filter_id)}">${item.enabled ? "O'chirish" : "Yoqish"}</button>
        <button type="button" class="danger" data-filter-action="delete" data-filter-id="${escapeHtml(item.filter_id)}">O'chirish</button>
      </div>
    </article>
  `).join("");
}

function fillFormFromCriteria(criteria) {
  $("#search-form").reset();
  $("#nlp-query").value = "";
  $("#sort-select").value = criteria.sort || "newest";
  const form = $("#search-form").elements;
  const districts = criteria.districts?.length ? criteria.districts : criteria.district ? [criteria.district] : [];
  form.namedItem("districts_input").value = districts.join(", ");
  for (const key of ["min_price", "max_price", "rooms", "renovation_level"]) {
    form.namedItem(key).value = criteria[key] ?? "";
  }
  form.namedItem("include_per_person").checked = Boolean(criteria.include_per_person);
}

async function applySavedFilter(item) {
  fillFormFromCriteria(item.criteria);
  latestNlpExtraction = { criteria: item.criteria };
  latestNlpQuery = "";
  renderNlpTags(criteriaToTags(item.criteria), "Saqlangan mezonlar");
  activateTab("search", { load: false });
  await search({ nlpExtraction: latestNlpExtraction });
}

function confirmAction(message) {
  if (tg?.showConfirm) {
    return new Promise((resolve) => tg.showConfirm(message, resolve));
  }
  return Promise.resolve(window.confirm(message));
}

function openSaveDialog() {
  if (!latestCriteria) {
    showMessage("Avval qidiruvni ishga tushiring.");
    return;
  }
  const form = $("#save-filter-form");
  form.reset();
  $("#save-dialog").showModal();
  form.elements.name.focus();
}

function activateTab(tab, { load = true } = {}) {
  clearMessage();
  if (location.hash) history.replaceState(null, "", `${location.pathname}${location.search}`);
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((item) => item.classList.add("hidden"));
  const view = $(`#${tab}-view`);
  if (!view) return;
  view.classList.remove("hidden");
  if (tab === "filters" && load) loadFilters().catch((error) => showMessage(error.message));
  if (tab === "search" && load && !currentItems.length) search().catch((error) => showMessage(error.message));
}

async function runSearchFromForm() {
  const runVersion = ++formRunVersion;
  clearMessage();
  setBusy($("#search-button"), true, "Tahlil qilinmoqda...");
  try {
    const query = $("#nlp-query").value;
    const normalizedQuery = query.trim();
    const extraction = normalizedQuery
      ? (latestNlpExtraction && latestNlpQuery === normalizedQuery
        ? latestNlpExtraction
        : await extractNlp(query))
      : null;
    await search({ nlpExtraction: extraction });
  } catch (error) {
    if (error.name !== "AbortError") {
      renderNlpTags([], "Mezonlarni ajratib bo'lmadi");
      showMessage(error.message);
    }
  } finally {
    if (runVersion === formRunVersion) setBusy($("#search-button"), false);
  }
}

async function init() {
  await authenticate();
  $("#profile-name").textContent = currentUser.first_name
    ? `${currentUser.first_name} ${currentUser.last_name || ""}`.trim()
    : `User ${currentUser.user_id}`;
  $("#profile-id").textContent = `Telegram ID: ${currentUser.user_id}`;
  $("#avatar").textContent = (currentUser.first_name || "E").slice(0, 2).toUpperCase();
  await routeHash();
}

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearchFromForm();
});
$("#refresh-button").addEventListener("click", () => search().catch((error) => showMessage(error.message)));
$("#save-filter-button").addEventListener("click", openSaveDialog);
$("#save-dialog-cancel").addEventListener("click", () => $("#save-dialog").close());
$("#sort-select").addEventListener("change", () => search().catch((error) => showMessage(error.message)));
$("#load-more-button").addEventListener("click", () => {
  const offset = Number($("#load-more-button").dataset.nextOffset || currentItems.length);
  search({ append: true, offset }).catch((error) => showMessage(error.message));
});
$("#nlp-query").addEventListener("input", () => {
  latestNlpExtraction = null;
  latestNlpQuery = "";
  nlpRequestVersion += 1;
  renderNlpTags();
});
$("#nlp-tags-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-nlp-tag-index]");
  if (!button) return;
  event.preventDefault();
  removeNlpTag(Number(button.dataset.nlpTagIndex)).catch((error) => showMessage(error.message));
});
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});
$("#results").addEventListener("click", (event) => {
  if (event.target.closest("a")) return;
  const card = event.target.closest("[data-announcement-id]");
  if (card) openDetail(card.dataset.announcementId);
});
$("#results").addEventListener("keydown", (event) => {
  if (event.target.closest("a") || (event.key !== "Enter" && event.key !== " ")) return;
  const card = event.target.closest("[data-announcement-id]");
  if (card) {
    event.preventDefault();
    openDetail(card.dataset.announcementId);
  }
});
$("#filters-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-filter-action]");
  if (!button) return;
  const item = savedFilters.get(button.dataset.filterId);
  if (!item) return;
  setBusy(button, true);
  try {
    if (button.dataset.filterAction === "apply") {
      await applySavedFilter(item);
      return;
    }
    if (button.dataset.filterAction === "toggle") {
      await request(`/webapp/filters/${encodeURIComponent(item.filter_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !item.enabled }),
      });
    }
    if (button.dataset.filterAction === "delete") {
      const confirmed = await confirmAction(`“${item.name}” filtrini o'chirasizmi?`);
      if (!confirmed) return;
      await request(`/webapp/filters/${encodeURIComponent(item.filter_id)}`, { method: "DELETE" });
    }
    await loadFilters();
  } catch (error) {
    showMessage(error.message);
  } finally {
    setBusy(button, false);
  }
});
$("#save-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = event.currentTarget.querySelector('[type="submit"]');
  setBusy(submitButton, true, "Saqlanmoqda...");
  try {
    const name = new FormData(event.currentTarget).get("name").trim();
    await request("/webapp/filters", {
      method: "POST",
      body: JSON.stringify({ name, criteria: latestCriteria }),
    });
    $("#save-dialog").close();
    showMessage("Filtr saqlandi.", "success");
  } catch (error) {
    showMessage(error.message);
  } finally {
    setBusy(submitButton, false);
  }
});
$("#detail-back-button").addEventListener("click", () => {
  history.replaceState(null, "", `${location.pathname}${location.search}`);
  routeHash().catch((error) => showMessage(error.message));
});
window.addEventListener("hashchange", () => {
  if (currentUser) routeHash().catch((error) => showMessage(error.message));
});

init().catch((error) => {
  showMessage(error.message);
  $("#result-count").textContent = "Kirish kutilmoqda";
  $("#results").innerHTML = "";
});
