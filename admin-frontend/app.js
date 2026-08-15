const apiBase = window.ESTATEFLOW_ADMIN_API_BASE || "/api/admin";
const tokenKey = "estateflow_admin_token";
let adminToken = sessionStorage.getItem(tokenKey) || "";
let currentScreen = "overview";
let reviewItems = [];
let selectedReviewId = null;
let flagsSnapshot = null;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `admin-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("uz-UZ", { dateStyle: "medium", timeStyle: "short" });
}

function stateChip(value) {
  const safe = String(value || "unknown");
  return `<span class="state ${escapeHtml(safe)}">${escapeHtml(safe.replaceAll("_", " "))}</span>`;
}

function showToast(message, kind = "success") {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast ${kind === "error" ? "error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add("hidden"), 4000);
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Admin-Token": adminToken };
  const response = await fetch(`${apiBase}${path}`, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error?.message || body.detail || `API xatosi: ${response.status}`);
  return body;
}

async function verifyToken() {
  await request("/release/flags");
}

function setAuthenticated(value) {
  $("#auth-gate").classList.toggle("hidden", value);
  $("#app").classList.toggle("hidden", !value);
}

function navigate(screen) {
  currentScreen = screen;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.screen === screen));
  $$(".screen").forEach((item) => item.classList.toggle("active-screen", item.id === `${screen}-screen`));
  const titles = { overview: "Tizim holati", reviews: "Review queue", sources: "Manbalar va listenerlar", flags: "Feature flags", telegram: "Telegram sessions", tags: "Audience tags", tools: "Operations tools" };
  $("#current-section").textContent = screen.replaceAll("-", " ").toUpperCase();
  $("#page-title").textContent = titles[screen] || "Control Room";
  if (screen === "overview") loadOverview();
  if (screen === "reviews") loadReviews();
  if (screen === "sources") loadSources();
  if (screen === "flags") loadFlags();
  if (screen === "telegram") loadTelegramSessions();
  if (screen === "tags") loadTags();
  if (screen === "tools") loadTools();
}

async function loadOverview() {
  const [reviews, flags, topology] = await Promise.allSettled([
    request("/manual-review/pending?limit=100"),
    request("/release/flags"),
    request("/listeners/status"),
  ]);
  const reviewData = reviews.status === "fulfilled" ? reviews.value : { items: [], metadata: { total: 0 } };
  const flagsData = flags.status === "fulfilled" ? flags.value : null;
  const topologyData = topology.status === "fulfilled" ? topology.value : null;
  const reviewCount = reviewData.metadata?.total ?? reviewData.items?.length ?? 0;
  const sourceCount = topologyData?.sources?.filter((item) => item.enabled).length ?? 0;
  const enabledFlags = flagsData ? Object.values(flagsData.flags).filter(Boolean).length : 0;
  const worker = topologyData?.worker_snapshot;
  $("#stat-reviews").textContent = reviewCount;
  $("#stat-sources").textContent = sourceCount;
  $("#stat-flags").textContent = enabledFlags;
  $("#stat-listener").textContent = worker?.started ? "Online" : "Check";
  $("#stat-listener-detail").textContent = worker ? `${worker.listener_count} listener / ${worker.telegram_source_count} Telegram source` : "status unavailable";
  $("#review-badge").textContent = reviewCount;
  $("#sidebar-status").textContent = worker?.started ? "Listener online" : "Needs attention";
  $("#overview-flags").innerHTML = flagsData ? Object.entries(flagsData.flags).slice(0, 5).map(([name, enabled]) => `<div class="compact-row"><div><strong>${escapeHtml(name)}</strong><small>version ${escapeHtml(flagsData.versions[name])}</small></div>${stateChip(enabled ? "active" : "offline")}</div>`).join("") : `<div class="empty-state">Flags yuklanmadi.</div>`;
  $("#overview-listener").innerHTML = topologyData ? `<div class="compact-row"><div><strong>${worker?.listener_account_key || "Listener account"}</strong><small>${worker?.reason || "Topology ready"}</small></div>${stateChip(worker?.started ? "online" : "offline")}</div>${topologyData.sources.slice(0, 3).map((source) => `<div class="compact-row"><div><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(source.identifier)}</small></div>${stateChip(source.enabled ? "active" : "offline")}</div>`).join("")}` : `<div class="empty-state">Listener status yuklanmadi.</div>`;
  $("#last-refresh").textContent = `Yangilandi: ${new Date().toLocaleTimeString("uz-UZ")}`;
}

async function loadReviews() {
  const reason = $("#review-filter").value;
  const query = reason ? `&reason=${encodeURIComponent(reason)}` : "";
  try {
    const data = await request(`/manual-review/pending?limit=100${query}`);
    reviewItems = data.items || [];
    $("#review-badge").textContent = data.metadata?.total ?? reviewItems.length;
    renderReviewList();
    if (selectedReviewId && reviewItems.some((item) => item.review_id === selectedReviewId)) await loadReviewDetail(selectedReviewId);
    else $("#review-detail").innerHTML = `<div class="empty-state">Review elementini tanlang.</div>`;
  } catch (error) { $("#review-list").innerHTML = `<div class="panel"><p class="error-text">${escapeHtml(error.message)}</p></div>`; }
}

function renderReviewList() {
  if (!reviewItems.length) { $("#review-list").innerHTML = `<div class="panel empty-state">Hozircha pending review yo'q.</div>`; return; }
  $("#review-list").innerHTML = reviewItems.map((item) => `<article class="review-card ${item.review_id === selectedReviewId ? "selected" : ""}" data-review-id="${escapeHtml(item.review_id)}"><div><h3>${escapeHtml(item.reason.replaceAll("_", " "))}</h3><p>${escapeHtml(item.announcement_id)}</p><div class="review-meta">${stateChip(item.status)}<span class="badge">${escapeHtml(formatDate(item.created_at))}</span></div></div><span class="text-button">Ochish →</span></article>`).join("");
  $$(".review-card").forEach((card) => card.addEventListener("click", () => loadReviewDetail(card.dataset.reviewId)));
}

function renderListingComparisonCard(listing, label, tone) {
  if (!listing) return `<article class="comparison-card ${tone}"><span class="comparison-label">${escapeHtml(label)}</span><div class="empty-state">E'lon ma'lumoti topilmadi.</div></article>`;
  const price = listing.price ? `${escapeHtml(listing.price)} ${escapeHtml(listing.currency || "")}` : "Narx ko'rsatilmagan";
  const location = [listing.district, listing.rooms ? `${listing.rooms} xona` : null, listing.area_sqm ? `${listing.area_sqm} m²` : null].filter(Boolean).map(escapeHtml).join(" · ");
  const floor = listing.floor ? `${escapeHtml(listing.floor)} / ${escapeHtml(listing.total_floors || "-")} qavat` : null;
  const phones = (listing.phone_numbers || []).map(escapeHtml).join(", ");
  return `<article class="comparison-card ${tone}"><span class="comparison-label">${escapeHtml(label)}</span><h4>${price}</h4><p class="comparison-id">${escapeHtml(listing.announcement_id || "-")}</p><div class="comparison-facts"><span>${location || "Joylashuv noma'lum"}</span>${floor ? `<span>${floor}</span>` : ""}${phones ? `<span>☎ ${phones}</span>` : ""}</div><p class="comparison-description">${escapeHtml(listing.description || "Tavsif mavjud emas.")}</p><div class="comparison-footer"><span>${escapeHtml(formatDate(listing.occurred_at))}</span>${listing.source_url ? `<a class="text-button" href="${escapeHtml(listing.source_url)}" target="_blank" rel="noreferrer">Manbani ochish ↗</a>` : ""}</div></article>`;
}

async function loadReviewDetail(reviewId) {
  selectedReviewId = reviewId;
  renderReviewList();
  try {
    const item = await request(`/manual-review/${encodeURIComponent(reviewId)}`);
    const listing = item.listing_summary || {};
    const candidate = item.candidate_listing_summary;
    const breakdown = (item.score_breakdown || []).map((signal) => `<div class="breakdown-row"><span>${escapeHtml(signal.name)} · ${escapeHtml(signal.reason)}</span><strong>${escapeHtml(signal.weight)}</strong></div>`).join("");
    const comparison = item.reason === "possible_duplicate" ? `<div class="comparison-grid">${renderListingComparisonCard(listing, "YANGI E'LON", "incoming")}${renderListingComparisonCard(candidate, "O'XSHASH ESKI E'LON", "candidate")}</div>` : renderListingComparisonCard(listing, "REVIEW E'LONI", "incoming");
    $("#review-detail").innerHTML = `<div class="panel-heading"><div><p class="eyebrow">REVIEW DETAIL</p><h3>${escapeHtml(item.reason.replaceAll("_", " "))}</h3></div>${stateChip(item.status)}</div>${comparison}<div class="breakdown-heading"><strong>Nima uchun o'xshash deb topildi?</strong><span>${escapeHtml(item.related_candidate_id || "")}</span></div><div class="breakdown">${breakdown || `<div class="empty-state">Score breakdown mavjud emas.</div>`}</div><div class="action-row">${item.status === "pending" ? `<button class="primary-button" data-review-action="mark_as_new">Yangi e'lon sifatida qabul qilish</button><button class="secondary-button" data-review-action="merge_duplicate">Duplicate sifatida birlashtirish</button><button class="danger-button" data-review-action="reject">Rad etish</button>` : ""}</div>`;
    $$("[data-review-action]").forEach((button) => button.addEventListener("click", () => actOnReview(reviewId, button.dataset.reviewAction)));
  } catch (error) { $("#review-detail").innerHTML = `<div class="error-text">${escapeHtml(error.message)}</div>`; }
}

async function actOnReview(reviewId, action) {
  const note = window.prompt("Admin izohi (ixtiyoriy):", "");
  if (note === null) return;
  try {
    await request(`/manual-review/${encodeURIComponent(reviewId)}/actions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, idempotency_key: idempotencyKey(), note: note || null, expected_status: "pending" }) });
    showToast("Review qarori saqlandi.");
    selectedReviewId = null;
    await loadReviews();
  } catch (error) { showToast(error.message, "error"); }
}

async function loadSources() {
  const [topology, suggestions] = await Promise.allSettled([request("/listeners/status"), request("/source-suggestions/pending?limit=100")]);
  if (topology.status === "fulfilled") renderListenerStatus(topology.value);
  else $("#listener-status").innerHTML = `<p class="error-text">${escapeHtml(topology.reason.message)}</p>`;
  if (suggestions.status === "fulfilled") renderSourceSuggestions(suggestions.value.items || []);
  else $("#source-suggestions").innerHTML = `<p class="error-text">${escapeHtml(suggestions.reason.message)}</p>`;
}

function renderListenerStatus(data) {
  const worker = data.worker_snapshot;
  $("#listener-status").innerHTML = `<div class="stack-row"><div><strong>${escapeHtml(worker?.listener_account_key || "Listener")}</strong><small>${escapeHtml(worker?.reason || "-")}</small></div>${stateChip(worker?.started ? "online" : "offline")}</div>${(data.sources || []).map((source) => `<div class="stack-row"><div><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(source.identifier)} · ${escapeHtml(source.assigned_account_key || source.listener_account_key || "unassigned")}</small></div>${stateChip(source.enabled ? "active" : "offline")}</div>`).join("") || `<div class="empty-state">Manba topilmadi.</div>`}`;
}

function renderSourceSuggestions(items) {
  $("#source-suggestions").innerHTML = items.length ? `<table class="data-table"><thead><tr><th>Manba</th><th>User</th><th>Status</th><th>Action</th></tr></thead><tbody>${items.map((item) => `<tr><td><strong>${escapeHtml(item.source_identifier)}</strong><br /><small>${escapeHtml(item.source_type)}</small></td><td>${escapeHtml(item.user_id || "-")}</td><td>${stateChip(item.status)}</td><td><button data-suggestion-action="approve" data-suggestion-id="${escapeHtml(item.suggestion_id)}">Approve</button> <button data-suggestion-action="reject" data-suggestion-id="${escapeHtml(item.suggestion_id)}">Reject</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty-state">Pending source taklifi yo'q.</div>`;
  $$("[data-suggestion-action]").forEach((button) => button.addEventListener("click", () => actOnSuggestion(button.dataset.suggestionId, button.dataset.suggestionAction)));
}

async function actOnSuggestion(id, action) {
  try { await request(`/source-suggestions/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idempotency_key: idempotencyKey(), expected_status: "pending", note: null }) }); showToast("Source taklifi yangilandi."); await loadSources(); } catch (error) { showToast(error.message, "error"); }
}

async function loadFlags() {
  try { flagsSnapshot = await request("/release/flags"); renderFlags(); } catch (error) { $("#flags-list").innerHTML = `<div class="panel"><p class="error-text">${escapeHtml(error.message)}</p></div>`; }
}

function renderFlags() {
  const descriptions = { bot_access: "Botga kirish va asosiy bot flow.", listener_sources: "Telegram listener source gate.", ai_processing: "AI extraction va processing pipeline.", notifications: "Mos e'lonlar haqida Telegram bildirishnomalari.", nlp: "Natural language search flow.", content_publishing: "Daily content automation.", ai_vision: "Rasm asosidagi AI tahlili." };
  $("#flags-list").innerHTML = Object.entries(flagsSnapshot.flags).map(([name, enabled]) => `<article class="flag-card"><header><h3>${escapeHtml(name)}</h3><button class="toggle ${enabled ? "on" : ""}" data-flag-name="${escapeHtml(name)}" aria-label="${escapeHtml(name)} toggle"></button></header><p>${escapeHtml(descriptions[name] || "Release control")}</p><div class="flag-meta"><span>v${escapeHtml(flagsSnapshot.versions[name])}</span>${stateChip(enabled ? "active" : "offline")}</div></article>`).join("");
  $("#allowlist-size").textContent = `${flagsSnapshot.beta_allowlist_size} user`;
  $("#flag-audit").innerHTML = flagsSnapshot.audit_events?.length ? `<table class="data-table"><thead><tr><th>Flag</th><th>O'zgarish</th><th>Sabab</th><th>Vaqt</th></tr></thead><tbody>${flagsSnapshot.audit_events.slice(-12).reverse().map((event) => `<tr><td>${escapeHtml(event.flag_name)}</td><td>${event.previous_enabled ? "ON" : "OFF"} → ${event.new_enabled ? "ON" : "OFF"}</td><td>${escapeHtml(event.reason)}</td><td>${escapeHtml(formatDate(event.occurred_at))}</td></tr>`).join("")}</tbody></table>` : `<div class="empty-state">Audit event yo'q.</div>`;
  $$("[data-flag-name]").forEach((button) => button.addEventListener("click", () => toggleFlag(button.dataset.flagName)));
}

async function toggleFlag(name) {
  const enabled = !flagsSnapshot.flags[name];
  const reason = window.prompt(`${name} flag uchun sabab:`, `Admin panel orqali ${enabled ? "yoqildi" : "o'chirildi"}`);
  if (!reason) return;
  try { flagsSnapshot = await request("/release/flags", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flag_name: name, enabled, expected_version: flagsSnapshot.versions[name], reason }) }); renderFlags(); showToast(`${name} flag yangilandi.`); } catch (error) { showToast(error.message, "error"); }
}

async function loadTelegramSessions() {
  try { const sessions = await request("/telegram/sessions"); $("#telegram-sessions").innerHTML = sessions.length ? sessions.map((item) => `<article class="session-card"><header><h3>${escapeHtml(item.session_name)}</h3>${stateChip(item.status)}</header><div class="session-info"><div><span>Authorization</span><strong>${escapeHtml(item.authorized ? "Authorized" : "Not authorized")}</strong></div><div><span>File</span><strong>${escapeHtml(item.file_exists ? `${item.file_size_bytes || 0} bytes` : "Mavjud emas")}</strong></div><div><span>Sources</span><strong>${item.bound_sources.length}</strong></div><div><span>Updated</span><strong>${escapeHtml(formatDate(item.updated_at))}</strong></div></div>${item.bound_sources.length ? `<p class="muted small">${item.bound_sources.map((source) => escapeHtml(source.identifier)).join(", ")}</p>` : ""}</article>`).join("") : `<div class="panel empty-state">Telegram session topilmadi.</div>`; } catch (error) { $("#telegram-sessions").innerHTML = `<div class="panel"><p class="error-text">${escapeHtml(error.message)}</p></div>`; }
}

async function loadTags() {
  try { const data = await request("/audience-tags/pending?limit=100"); $("#tags-list").innerHTML = data.items?.length ? data.items.map((tag) => `<article class="tag-card"><header><h3>${escapeHtml(tag.display_name_uz)}</h3>${stateChip(tag.status)}</header><p><strong>${escapeHtml(tag.tag_key)}</strong><br />${escapeHtml(tag.usage_count)} ta ishlatilgan</p><div class="tag-actions"><button class="primary-button" data-tag-action="approve" data-tag-key="${escapeHtml(tag.tag_key)}">Approve</button><button class="danger-button" data-tag-action="reject" data-tag-key="${escapeHtml(tag.tag_key)}">Reject</button></div></article>`).join("") : `<div class="panel empty-state">Pending audience tag yo'q.</div>`; $$("[data-tag-action]").forEach((button) => button.addEventListener("click", () => actOnTag(button.dataset.tagKey, button.dataset.tagAction))); } catch (error) { $("#tags-list").innerHTML = `<div class="panel"><p class="error-text">${escapeHtml(error.message)}</p></div>`; }
}

async function actOnTag(tagKey, action) {
  try { await request(`/audience-tags/${encodeURIComponent(tagKey)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ idempotency_key: idempotencyKey(), expected_status: "pending", note: null, display_name_uz: null, target_tag_key: null }) }); showToast("Audience tag yangilandi."); await loadTags(); } catch (error) { showToast(error.message, "error"); }
}

async function loadTools() {
  try { const data = await request("/adapters"); $("#adapter-status").innerHTML = `<div class="compact-row"><div><strong>${data.loaded_count} ta adapter yuklangan</strong><small>${data.plugin_directory || "Registry"}</small></div>${stateChip(data.failed_count ? "failed" : "active")}</div>${(data.failures || []).map((failure) => `<div class="compact-row"><strong>${escapeHtml(JSON.stringify(failure))}</strong>${stateChip("failed")}</div>`).join("")}`; } catch (error) { $("#adapter-status").innerHTML = `<p class="error-text">${escapeHtml(error.message)}</p>`; }
}

async function addSource(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  if (data.source_type === "website") delete data.session_name;
  try { const result = await request("/sources/enable", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }); showFormMessage("#source-message", `Manba qo'shildi: ${result.identifier}`, false); event.currentTarget.reset(); await loadSources(); } catch (error) { showFormMessage("#source-message", error.message, true); }
}

async function addAllowlist(event) {
  event.preventDefault();
  const userId = Number(new FormData(event.currentTarget).get("user_id"));
  try { const result = await request("/release/beta-allowlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId }) }); $("#allowlist-size").textContent = `${result.allowlist_size} user`; showFormMessage("#allowlist-message", `${userId} allowlistga qo'shildi.`, false); event.currentTarget.reset(); } catch (error) { showFormMessage("#allowlist-message", error.message, true); }
}

function showFormMessage(selector, text, error) { const element = $(selector); element.textContent = text; element.className = `form-message ${error ? "error" : "success"}`; }

async function generateContent() { try { const result = await request("/content/daily", { method: "POST" }); $("#content-result").innerHTML = result.length ? result.map((item) => `<div class="compact-row"><div><strong>${escapeHtml(item.kind)}</strong><small>${escapeHtml(item.text)}</small></div>${stateChip(item.status)}</div>`).join("") : `<div class="empty-state">Yangi post yaratilmadi.</div>`; showToast("Daily content generatsiya qilindi."); } catch (error) { showToast(error.message, "error"); } }

async function runAiTest(event) { event.preventDefault(); const formData = new FormData(event.currentTarget); $("#ai-result").classList.remove("hidden"); $("#ai-result").textContent = "AI javobi kutilmoqda..."; try { const result = await request("/ai/test-extract", { method: "POST", body: formData, headers: {} }); $("#ai-result").textContent = JSON.stringify(result, null, 2); } catch (error) { $("#ai-result").textContent = error.message; } }

async function reloadAdapters() { try { const result = await request("/adapters/reload", { method: "POST" }); showToast(`${result.loaded_count} ta adapter reload qilindi.`); await loadTools(); } catch (error) { showToast(error.message, "error"); } }

async function refreshCurrent() { navigate(currentScreen); }

$("#auth-form").addEventListener("submit", async (event) => { event.preventDefault(); adminToken = $("#admin-token").value.trim(); try { await verifyToken(); sessionStorage.setItem(tokenKey, adminToken); setAuthenticated(true); navigate("overview"); } catch (error) { $("#auth-error").textContent = error.message; $("#auth-error").classList.remove("hidden"); setAuthenticated(false); } });
$("#logout-button").addEventListener("click", () => { sessionStorage.removeItem(tokenKey); adminToken = ""; setAuthenticated(false); $("#admin-token").value = ""; });
$("#refresh-button").addEventListener("click", () => refreshCurrent().catch((error) => showToast(error.message, "error")));
$("#review-filter").addEventListener("change", () => loadReviews());
$("#source-form").addEventListener("submit", addSource);
$("#refresh-listeners").addEventListener("click", async () => { try { await request("/listeners/refresh", { method: "POST" }); showToast("Listener refresh signal yuborildi."); await loadSources(); } catch (error) { showToast(error.message, "error"); } });
$("#allowlist-form").addEventListener("submit", addAllowlist);
$("#ai-form").addEventListener("submit", runAiTest);
$("#reload-adapters").addEventListener("click", reloadAdapters);
$("#generate-content").addEventListener("click", generateContent);
$$(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.screen)));
$$("[data-go]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.go)));

if (adminToken) { setAuthenticated(true); verifyToken().then(() => navigate("overview")).catch(() => { adminToken = ""; sessionStorage.removeItem(tokenKey); setAuthenticated(false); }); } else { setAuthenticated(false); }
