const PROMO_STORAGE_KEY = window.PicFlowPromoStorageKey || "picflow.promos.v1";
const DEFAULT_PROMOS = window.PicFlowDefaultPromos || [];

const $ = (selector) => document.querySelector(selector);

const fields = {
  name: $("#promoName"),
  code: $("#promoCodeEdit"),
  type: $("#promoType"),
  value: $("#promoValue"),
  photoLimit: $("#promoPhotoLimit"),
  startsAt: $("#promoStarts"),
  endsAt: $("#promoEnds"),
  usageLimit: $("#promoLimit"),
  events: $("#promoEvents"),
  active: $("#promoActive"),
};

let promos = loadPromos();
let selectedPromoId = promos[0]?.id || "";
let draftMode = false;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function normalizePromo(promo) {
  const events = Array.isArray(promo.events)
    ? promo.events.map(String).map((item) => item.trim()).filter(Boolean)
    : String(promo.events || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

  return {
    id: String(promo.id || `PROMO-${Date.now()}`).trim(),
    name: String(promo.name || "Промокод").trim(),
    code: String(promo.code || "").trim().toUpperCase(),
    type: ["percent", "fixed", "free_photo", "free_pack", "photo_quantity_discount"].includes(promo.type)
      ? promo.type
      : "percent",
    value: Math.max(0, Number(promo.value) || 0),
    photoLimit: Math.max(0, Number(promo.photoLimit) || 0),
    startsAt: promo.startsAt || "",
    endsAt: promo.endsAt || "",
    usageLimit: Math.max(0, Number(promo.usageLimit) || 0),
    used: Math.max(0, Number(promo.used) || 0),
    scope: events.length ? "events" : "all",
    events,
    active: promo.active !== false,
  };
}

function loadPromos() {
  try {
    const saved = JSON.parse(localStorage.getItem(PROMO_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) return saved.map(normalizePromo);
  } catch {
    localStorage.removeItem(PROMO_STORAGE_KEY);
  }
  return DEFAULT_PROMOS.map(normalizePromo);
}

function savePromos() {
  localStorage.setItem(PROMO_STORAGE_KEY, JSON.stringify(promos, null, 2));
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function showToast(message) {
  const toast = $("#promosToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

function getFilteredPromos() {
  const query = $("#promosSearch").value.trim().toLowerCase();
  if (!query) return promos;
  return promos.filter((promo) =>
    [promo.name, promo.code, promo.events.join(" ")].join(" ").toLowerCase().includes(query),
  );
}

function renderPromos() {
  const visible = getFilteredPromos();
  $("#promosSummary").textContent = `${promos.length} промокодов · ${promos.filter((promo) => promo.active).length} активны`;
  $("#promosList").innerHTML = visible.length
    ? visible.map(renderPromoCard).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="badge-x"></i><strong>Промокоды не найдены</strong></div>`;
  refreshIcons();
}

function renderPromoCard(promo) {
  const active = promo.id === selectedPromoId && !draftMode;
  const typeLabel = {
    percent: `${promo.value}%`,
    fixed: `${promo.value} ₽`,
    free_photo: "фото бесплатно",
    free_pack: "пакет бесплатно",
    photo_quantity_discount: `${promo.photoLimit || 1} фото · ${promo.value}%`,
  }[promo.type];

  return `
    <button class="event-card ${active ? "active" : ""}" type="button" data-promo-id="${escapeHtml(promo.id)}">
      <span class="event-card-main">
        <strong>${escapeHtml(promo.code)}</strong>
        <small>${escapeHtml(promo.name)} · ${escapeHtml(typeLabel)}</small>
        <em>${escapeHtml(promo.events.length ? promo.events.join(", ") : "весь сайт")} · ${promo.used}/${promo.usageLimit || "∞"}</em>
      </span>
      <span class="status-pill ${promo.active ? "status-paid" : "status-cancelled"}">
        ${promo.active ? "Активен" : "Отключен"}
      </span>
    </button>`;
}

function promoFromForm() {
  return normalizePromo({
    id: selectedPromoId || `PROMO-${Date.now()}`,
    name: fields.name.value,
    code: fields.code.value,
    type: fields.type.value,
    value: fields.value.value,
    photoLimit: fields.photoLimit.value,
    startsAt: fields.startsAt.value,
    endsAt: fields.endsAt.value,
    usageLimit: fields.usageLimit.value,
    events: fields.events.value,
    active: fields.active.checked,
    used: promos.find((promo) => promo.id === selectedPromoId)?.used || 0,
  });
}

function fillForm(promo, mode = "edit") {
  const normalized = normalizePromo(promo);
  fields.name.value = normalized.name;
  fields.code.value = normalized.code;
  fields.type.value = normalized.type;
  fields.value.value = normalized.value || "";
  fields.photoLimit.value = normalized.photoLimit || "";
  fields.startsAt.value = normalized.startsAt;
  fields.endsAt.value = normalized.endsAt;
  fields.usageLimit.value = normalized.usageLimit || "";
  fields.events.value = normalized.events.join(", ");
  fields.active.checked = normalized.active;
  draftMode = mode === "new";
  selectedPromoId = draftMode ? "" : normalized.id;
  $("#promoMode").textContent = draftMode ? "New" : "Edit";
  $("#deletePromoButton").disabled = draftMode;
  renderPromos();
}

function createPromo() {
  fillForm(
    {
      id: `PROMO-${Date.now()}`,
      name: "Новый промокод",
      code: `CODE${Math.floor(1000 + Math.random() * 9000)}`,
      type: "percent",
      value: 10,
      photoLimit: 0,
      startsAt: "2026-06-18",
      endsAt: "2026-12-31",
      usageLimit: 1,
      events: [],
      active: true,
    },
    "new",
  );
}

function savePromo(event) {
  event.preventDefault();
  const promo = promoFromForm();
  if (!promo.code) {
    showToast("Укажите код");
    return;
  }

  const duplicate = promos.find((item) => item.code === promo.code && (draftMode || item.id !== selectedPromoId));
  if (duplicate) {
    showToast("Такой код уже есть");
    return;
  }

  const index = promos.findIndex((item) => item.id === selectedPromoId);
  if (index >= 0 && !draftMode) promos[index] = promo;
  else promos.unshift(promo);

  selectedPromoId = promo.id;
  draftMode = false;
  savePromos();
  fillForm(promo);
  showToast("Промокод сохранён");
}

function deletePromo() {
  if (draftMode || !selectedPromoId) return;
  promos = promos.filter((promo) => promo.id !== selectedPromoId);
  selectedPromoId = promos[0]?.id || "";
  savePromos();
  if (selectedPromoId) fillForm(promos[0]);
  else createPromo();
  showToast("Промокод удалён");
}

function bindEvents() {
  $("#promosList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-promo-id]");
    if (!button) return;
    const promo = promos.find((item) => item.id === button.dataset.promoId);
    if (promo) fillForm(promo);
  });
  $("#promosSearch").addEventListener("input", renderPromos);
  $("#newPromoButton").addEventListener("click", createPromo);
  $("#promoForm").addEventListener("submit", savePromo);
  $("#deletePromoButton").addEventListener("click", deletePromo);
}

bindEvents();
if (promos.length) fillForm(promos[0]);
else createPromo();
refreshIcons();
