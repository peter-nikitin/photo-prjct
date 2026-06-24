const EVENT_STORAGE_KEY = window.PicFlowEventStorageKey || "picflow.events.v1";
const DEFAULT_EVENTS = window.PicFlowDefaultEvents || [];
const PHOTO_STORAGE_KEY = window.PicFlowStorageKey || "picflow.photos.v1";
const DEFAULT_PHOTOS = window.PicFlowDefaults || [];

const $ = (selector) => document.querySelector(selector);

const fields = {
  name: $("#eventName"),
  location: $("#eventLocation"),
  description: $("#eventDescription"),
  coverFile: $("#eventCoverFile"),
  coverPreview: $("#eventCoverPreview"),
  dateFrom: $("#eventDateFrom"),
  dateTo: $("#eventDateTo"),
  priceSingle: $("#eventPriceSingle"),
  pricePack: $("#eventPricePack"),
  frameFile: $("#eventFrameFile"),
  framePreview: $("#eventFramePreview"),
  locations: $("#eventLocations"),
  photographers: $("#eventPhotographers"),
  showInSearch: $("#eventShowInSearch"),
  showInResults: $("#eventShowInResults"),
  uploadEnabled: $("#eventUploadEnabled"),
};

let events = loadEvents();
let selectedEventName = events[0]?.name || "";
let draftMode = false;
let currentCoverData = "";
let currentFrameData = "";
let currentFrameName = "";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
}

function renderCoverPreview(event) {
  const [bg, accent, dark] = event.coverPalette || ["#dce8ed", "#f0dfb7", "#325a64"];
  fields.coverPreview.innerHTML = event.coverData
    ? `<img src="${escapeHtml(event.coverData)}" alt="" />`
    : `<svg viewBox="0 0 240 120" aria-hidden="true">
        <rect width="240" height="120" fill="${bg}" />
        <circle cx="194" cy="32" r="34" fill="${accent}" />
        <path d="M0 92 C58 62 92 112 142 82 S204 70 240 54 V120 H0 Z" fill="${dark}" opacity=".32" />
      </svg>`;
}

function renderFramePreview(event) {
  fields.framePreview.innerHTML = event.frameData
    ? `<img src="${escapeHtml(event.frameData)}" alt="" />`
    : `<svg viewBox="0 0 240 120" aria-hidden="true">
        <rect width="240" height="120" fill="#f6f4ef" />
        <circle cx="120" cy="56" r="30" fill="#d7d0c4" />
        <text x="120" y="102" text-anchor="middle" font-family="Arial" font-size="13" font-weight="800" fill="#73777f">Логотип не загружен</text>
      </svg>`;
}

function normalizeEvent(event) {
  const legacyActive = event.active !== false;
  const showInSearch = event.showInSearch ?? legacyActive;
  const showInResults = event.showInResults ?? legacyActive;
  const uploadEnabled = event.uploadEnabled ?? legacyActive;
  const priceSingle = Math.max(0, Number(event.priceSingle ?? event.singlePrice ?? 0) || 0);
  const pricePack = Math.max(0, Number(event.pricePack ?? event.packPrice ?? 0) || 0);
  const locations = Array.isArray(event.locations)
    ? event.locations.map(String).map((item) => item.trim()).filter(Boolean)
    : String(event.locations || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
  const photographers = Array.isArray(event.photographers)
    ? event.photographers.map(String).map((item) => item.trim()).filter(Boolean)
    : String(event.photographers || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
  const coverPalette =
    Array.isArray(event.coverPalette) && event.coverPalette.length >= 3
      ? event.coverPalette.slice(0, 3)
      : ["#dce8ed", "#f0dfb7", "#325a64"];

  return {
    name: String(event.name || "").trim(),
    location: String(event.location || "").trim(),
    description: String(event.description || "").trim(),
    coverData: event.coverData || "",
    coverPalette,
    dateFrom: event.dateFrom || "",
    dateTo: event.dateTo || "",
    priceSingle,
    pricePack,
    frameData: event.frameData || "",
    frameName: String(event.frameName || "").trim(),
    locations,
    photographers,
    showInSearch,
    showInResults,
    uploadEnabled,
    active: showInSearch && showInResults && uploadEnabled,
  };
}

function loadPhotos() {
  try {
    const saved = JSON.parse(localStorage.getItem(PHOTO_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) return saved;
  } catch {
    localStorage.removeItem(PHOTO_STORAGE_KEY);
  }

  return DEFAULT_PHOTOS;
}

function loadEvents() {
  try {
    const saved = JSON.parse(localStorage.getItem(EVENT_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) {
      return mergePhotoEvents(saved.map(normalizeEvent));
    }
  } catch {
    localStorage.removeItem(EVENT_STORAGE_KEY);
  }

  return mergePhotoEvents(DEFAULT_EVENTS.map(normalizeEvent));
}

function mergePhotoEvents(baseEvents) {
  const byName = new Map(baseEvents.filter((event) => event.name).map((event) => [event.name, event]));

  loadPhotos().forEach((photo) => {
    if (!photo.event || byName.has(photo.event)) return;
    byName.set(photo.event, {
      name: photo.event,
      location: "",
      dateFrom: photo.date || "",
      dateTo: photo.date || "",
      priceSingle: Math.max(0, Number(photo.price) || 0),
      pricePack: 0,
      description: "",
      coverData: "",
      coverPalette: photo.palette || ["#dce8ed", "#f0dfb7", "#325a64"],
      frameData: "",
      frameName: "",
      locations: [],
      photographers: photo.photographer ? [photo.photographer] : [],
      showInSearch: true,
      showInResults: true,
      uploadEnabled: true,
      active: true,
    });
  });

  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function saveEvents() {
  localStorage.setItem(EVENT_STORAGE_KEY, JSON.stringify(events, null, 2));
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function showToast(message) {
  const toast = $("#eventsToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

function getFilteredEvents() {
  const query = $("#eventsSearch").value.trim().toLowerCase();
  if (!query) return events;

  return events.filter((event) =>
    [event.name, event.location].join(" ").toLowerCase().includes(query),
  );
}

function renderEvents() {
  const visible = getFilteredEvents();
  const searchCount = events.filter((event) => event.showInSearch).length;
  const resultsCount = events.filter((event) => event.showInResults).length;
  const uploadCount = events.filter((event) => event.uploadEnabled).length;

  $("#eventsSummary").textContent = `${events.length} событий · ${searchCount} в поиске · ${resultsCount} в выдаче · ${uploadCount} в загрузчике`;
  $("#eventsList").innerHTML = visible.length
    ? visible.map(renderEventCard).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="calendar-x"></i><strong>События не найдены</strong></div>`;

  refreshIcons();
}

function renderEventCard(event) {
  const active = event.name === selectedEventName && !draftMode;
  const priceLabel = [
    event.priceSingle ? `шт ${money(event.priceSingle)}` : "",
    event.pricePack ? `пак ${money(event.pricePack)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const flags = [
    ["Поиск", event.showInSearch],
    ["Выдача", event.showInResults],
    ["Загрузка", event.uploadEnabled],
  ];

  return `
    <button class="event-card ${active ? "active" : ""}" type="button" data-event-name="${escapeHtml(event.name)}">
      <span class="event-card-main">
        <strong>${escapeHtml(event.name)}</strong>
        <small>${escapeHtml(event.location || "без локации")}</small>
        <em>${escapeHtml([event.dateFrom, event.dateTo].filter(Boolean).join(" - ") || "даты не указаны")}</em>
        <em>${escapeHtml(priceLabel || "цены не заданы")}</em>
      </span>
      <span class="event-flags">
        ${flags
          .map(
            ([label, enabled]) =>
              `<span class="status-pill ${enabled ? "status-paid" : "status-cancelled"}">${label}</span>`,
          )
          .join("")}
      </span>
    </button>`;
}

function eventFromForm() {
  return normalizeEvent({
    name: fields.name.value,
    location: fields.location.value,
    description: fields.description.value,
    coverData: currentCoverData,
    frameData: currentFrameData,
    frameName: currentFrameName,
    dateFrom: fields.dateFrom.value,
    dateTo: fields.dateTo.value,
    priceSingle: fields.priceSingle.value,
    pricePack: fields.pricePack.value,
    locations: fields.locations.value,
    photographers: fields.photographers.value,
    showInSearch: fields.showInSearch.checked,
    showInResults: fields.showInResults.checked,
    uploadEnabled: fields.uploadEnabled.checked,
  });
}

function fillForm(event, mode = "edit") {
  const normalized = normalizeEvent(event);

  fields.name.value = normalized.name;
  fields.location.value = normalized.location;
  fields.description.value = normalized.description;
  currentCoverData = normalized.coverData;
  currentFrameData = normalized.frameData;
  currentFrameName = normalized.frameName;
  fields.dateFrom.value = normalized.dateFrom;
  fields.dateTo.value = normalized.dateTo;
  fields.priceSingle.value = normalized.priceSingle || "";
  fields.pricePack.value = normalized.pricePack || "";
  fields.locations.value = normalized.locations.join(", ");
  fields.photographers.value = normalized.photographers.join(", ");
  fields.showInSearch.checked = normalized.showInSearch;
  fields.showInResults.checked = normalized.showInResults;
  fields.uploadEnabled.checked = normalized.uploadEnabled;

  draftMode = mode === "new";
  selectedEventName = draftMode ? "" : normalized.name;
  $("#eventEditorMode").textContent = draftMode ? "New" : "Edit";
  $("#deleteEventButton").disabled = draftMode;
  renderCoverPreview(normalized);
  renderFramePreview(normalized);
  renderPreview();
  renderEvents();
}

function renderPreview() {
  const event = eventFromForm();
  $("#eventPreviewName").textContent = event.name || "Новое событие";
  $("#eventPreviewMeta").textContent = `${event.location || "без локации"} · поиск: ${
    event.showInSearch ? "да" : "нет"
  } · выдача: ${event.showInResults ? "да" : "нет"} · шт: ${
    event.priceSingle ? money(event.priceSingle) : "не задано"
  } · пак: ${event.pricePack ? money(event.pricePack) : "не задано"} · логотип: ${
    event.frameData ? event.frameName || "загружен" : "не загружен"
  } · загрузчик: ${
    event.uploadEnabled ? "да" : "нет"
  }`;
  renderCoverPreview(event);
  renderFramePreview(event);
}

function createEvent() {
  fillForm(
    {
      name: `New Event ${events.length + 1}`,
      location: "",
      dateFrom: "2026-06-18",
      dateTo: "2026-06-18",
      priceSingle: 350,
      pricePack: 1200,
      frameData: "",
      frameName: "",
      description: "",
      coverData: "",
      locations: ["Старт", "Финиш"],
      photographers: [],
      showInSearch: true,
      showInResults: true,
      uploadEnabled: true,
      active: true,
    },
    "new",
  );
}

function selectEvent(name) {
  const event = events.find((item) => item.name === name);
  if (event) fillForm(event);
}

function saveEvent(submitEvent) {
  submitEvent.preventDefault();
  const nextEvent = eventFromForm();

  if (!nextEvent.name) {
    showToast("Укажите название");
    return;
  }

  const duplicate = events.find(
    (event) => event.name === nextEvent.name && (draftMode || event.name !== selectedEventName),
  );

  if (duplicate) {
    showToast("Такое событие уже есть");
    return;
  }

  const index = events.findIndex((event) => event.name === selectedEventName);
  if (index >= 0 && !draftMode) {
    events[index] = nextEvent;
  } else {
    events.unshift(nextEvent);
  }

  events.sort((a, b) => a.name.localeCompare(b.name));
  selectedEventName = nextEvent.name;
  draftMode = false;
  saveEvents();
  fillForm(nextEvent);
  showToast("Событие сохранено");
}

function deleteEvent() {
  if (draftMode || !selectedEventName) return;
  if (!window.confirm(`Удалить событие ${selectedEventName}?`)) return;

  events = events.filter((event) => event.name !== selectedEventName);
  saveEvents();
  selectedEventName = events[0]?.name || "";

  if (selectedEventName) {
    fillForm(events[0]);
  } else {
    createEvent();
  }

  showToast("Событие удалено");
}

function resetEvents() {
  if (!window.confirm("Вернуть демо-события?")) return;

  events = mergePhotoEvents(DEFAULT_EVENTS.map(normalizeEvent));
  saveEvents();
  fillForm(events[0]);
  showToast("Демо-события восстановлены");
}

function bindEvents() {
  $("#eventsList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-event-name]");
    if (!button) return;
    selectEvent(button.dataset.eventName);
  });

  $("#eventsSearch").addEventListener("input", renderEvents);
  $("#newEventButton").addEventListener("click", createEvent);
  $("#eventForm").addEventListener("submit", saveEvent);
  $("#deleteEventButton").addEventListener("click", deleteEvent);
  $("#resetEventsButton").addEventListener("click", resetEvents);
  fields.coverFile.addEventListener("change", () => {
    const file = fields.coverFile.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.addEventListener("load", () => {
      currentCoverData = reader.result;
      renderPreview();
      showToast("Обложка загружена");
    });
    reader.readAsDataURL(file);
    fields.coverFile.value = "";
  });

  fields.frameFile.addEventListener("change", () => {
    const file = fields.frameFile.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.addEventListener("load", () => {
      currentFrameData = reader.result;
      currentFrameName = file.name;
      renderPreview();
      showToast("Логотип загружен");
    });
    reader.readAsDataURL(file);
    fields.frameFile.value = "";
  });

  $("#clearFrameButton").addEventListener("click", () => {
    currentFrameData = "";
    currentFrameName = "";
    renderPreview();
    showToast("Логотип убран");
  });

  Object.values(fields).forEach((field) => {
    field.addEventListener("input", renderPreview);
    field.addEventListener("change", renderPreview);
  });
}

bindEvents();

if (events.length) {
  fillForm(events[0]);
} else {
  createEvent();
}

refreshIcons();
