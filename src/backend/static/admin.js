const STORAGE_KEY = window.PicFlowStorageKey || "picflow.photos.v1";
const EVENT_STORAGE_KEY = window.PicFlowEventStorageKey || "picflow.events.v1";
const DEFAULT_PHOTOS = window.PicFlowDefaults || [];
const DEFAULT_EVENTS = window.PicFlowDefaultEvents || [];
const DEMO_IMAGES = window.PicFlowDemoImages || {};
const zoneLabels = window.PicFlowZones || {
  start: "Старт",
  track: "Трасса",
  finish: "Финиш",
  expo: "Expo",
};

const $ = (selector) => document.querySelector(selector);

const fields = {
  id: $("#photoId"),
  imageFile: $("#photoImageFile"),
  imagePreview: $("#photoImagePreview"),
  event: $("#photoEvent"),
  date: $("#photoDate"),
  time: $("#photoTime"),
  zone: $("#photoZone"),
  photographer: $("#photoPhotographer"),
  bibs: $("#photoBibs"),
  manualPacks: $("#photoManualPacks"),
  hidden: $("#photoHidden"),
};

let photos = loadPhotos();
let events = loadEvents();
let selectedEventName = "";
let selectedId = "";
let draftMode = false;
let currentImageData = "";
let currentImageName = "";
let currentQrRaw = "";
let currentPhotoMeta = null;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function normalizePhoto(photo) {
  const palette =
    Array.isArray(photo.palette) && photo.palette.length >= 3
      ? photo.palette.slice(0, 3)
      : ["#dce8ed", "#e2795a", "#27323a"];
  const manualPacks = Array.isArray(photo.manualPacks)
    ? photo.manualPacks.map(String).map((item) => item.trim()).filter(Boolean)
    : String(photo.manualPacks || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

  return {
    id: String(photo.id || "").trim() || uniqueId("PHOTO"),
    event: String(photo.event || "Event").trim(),
    date: photo.date || "2026-06-18",
    time: photo.time || "10:00",
    zone: String(photo.zone || "track").trim(),
    bibs: Array.isArray(photo.bibs)
      ? photo.bibs.map(String).map((item) => item.trim()).filter(Boolean)
      : String(photo.bibs || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
    faces: Boolean(photo.faces),
    match: Math.max(0, Math.min(100, Number(photo.match) || 0)),
    price: Math.max(0, Number(photo.price) || 0),
    palette,
    scene: photo.scene || inferScene(photo.zone),
    imageData: photo.imageData || "",
    imageName: photo.imageName || "",
    qrRaw: photo.qrRaw || "",
    manualPacks,
    manualNote: String(photo.manualNote || "").trim(),
    photographer: String(photo.photographer || "").trim(),
    hidden: Boolean(photo.hidden),
  };
}

function loadPhotos() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) {
      return saved.map(normalizePhoto);
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }

  return DEFAULT_PHOTOS.map(normalizePhoto);
}

function normalizeEvent(event) {
  const legacyActive = event.active !== false;
  const showInSearch = event.showInSearch ?? legacyActive;
  const showInResults = event.showInResults ?? legacyActive;
  const uploadEnabled = event.uploadEnabled ?? legacyActive;
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
    dateFrom: event.dateFrom || "",
    dateTo: event.dateTo || "",
    priceSingle: Math.max(0, Number(event.priceSingle ?? event.singlePrice ?? 0) || 0),
    pricePack: Math.max(0, Number(event.pricePack ?? event.packPrice ?? 0) || 0),
    coverPalette,
    frameData: event.frameData || "",
    frameName: String(event.frameName || "").trim(),
    locations,
    photographers,
    showInSearch,
    showInResults,
    uploadEnabled,
  };
}

function loadEvents() {
  let loadedEvents;

  try {
    const saved = JSON.parse(localStorage.getItem(EVENT_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) {
      loadedEvents = saved.map(normalizeEvent).filter((event) => event.name);
    }
  } catch {
    localStorage.removeItem(EVENT_STORAGE_KEY);
  }

  if (!loadedEvents) {
    loadedEvents = DEFAULT_EVENTS.map(normalizeEvent).filter((event) => event.name);
  }

  const byName = new Map(loadedEvents.map((event) => [event.name, event]));
  photos.forEach((photo) => {
    if (!photo.event || byName.has(photo.event)) return;
    byName.set(photo.event, {
      name: photo.event,
      location: "",
      dateFrom: photo.date || "",
      dateTo: photo.date || "",
      priceSingle: Math.max(0, Number(photo.price) || 0),
      pricePack: 0,
      coverPalette: photo.palette || ["#dce8ed", "#f0dfb7", "#325a64"],
      locations: photo.zone ? [zoneLabel(photo.zone)] : [],
      photographers: photo.photographer ? [photo.photographer] : [],
      showInSearch: true,
      showInResults: true,
      uploadEnabled: true,
    });
  });

  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function savePhotos() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(photos, null, 2));
}

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value);
}

function zoneLabel(value) {
  return zoneLabels[value] || value || "Локация";
}

function inferScene(zone) {
  const value = String(zone || "").toLowerCase();
  if (value.includes("finish") || value.includes("финиш")) return "finish";
  if (value.includes("start") || value.includes("старт")) return "start";
  if (value.includes("expo") || value.includes("экспо")) return "expo";
  return "wide";
}

function getEvent(eventName) {
  return events.find((event) => event.name === eventName) || null;
}

function getEventPrice(eventName) {
  return getEvent(eventName)?.priceSingle || 0;
}

function getEventPalette(eventName) {
  return getEvent(eventName)?.coverPalette || ["#dce8ed", "#e2795a", "#27323a"];
}

function getEventLocations(eventName) {
  const event = getEvent(eventName);
  const eventLocations = event?.locations?.length ? event.locations : [];
  const photoLocations = photos
    .filter((photo) => photo.event === eventName)
    .map((photo) => zoneLabel(photo.zone));
  return [...new Set([...eventLocations, ...photoLocations, "Старт", "Трасса", "Финиш"])].filter(Boolean);
}

function renderEventOptions(selectedEvent = fields.event.value) {
  fields.event.innerHTML = events.length
    ? events
        .map((event) => `<option value="${escapeHtml(event.name)}">${escapeHtml(event.name)}</option>`)
        .join("")
    : `<option value="">Сначала создайте событие</option>`;

  if (selectedEvent && !events.some((event) => event.name === selectedEvent)) {
    fields.event.append(new Option(selectedEvent, selectedEvent));
  }

  fields.event.value = selectedEvent || events[0]?.name || "";
}

function renderLocationOptions(eventName = fields.event.value, selectedZone = fields.zone.value) {
  const locations = getEventLocations(eventName);
  fields.zone.innerHTML = locations
    .map((location) => `<option value="${escapeHtml(location)}">${escapeHtml(location)}</option>`)
    .join("");

  if (selectedZone && !locations.includes(selectedZone)) {
    fields.zone.append(new Option(zoneLabel(selectedZone), selectedZone));
  }

  fields.zone.value = selectedZone || locations[0] || "";
}

function padTimePart(value) {
  return String(value).padStart(2, "0");
}

function captureParts(date) {
  return {
    date: `${date.getFullYear()}-${padTimePart(date.getMonth() + 1)}-${padTimePart(date.getDate())}`,
    time: `${padTimePart(date.getHours())}:${padTimePart(date.getMinutes())}:${padTimePart(date.getSeconds())}`,
  };
}

function parseExifDate(value) {
  if (!value) return null;
  if (value instanceof Date && !Number.isNaN(value.getTime())) return value;

  const match = String(value).match(
    /(\d{4})[:/-](\d{2})[:/-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/,
  );
  if (!match) return null;

  const [, year, month, day, hour, minute, second = "0"] = match;
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

function parseDateFromFileName(fileName) {
  const match = fileName.match(
    /(20\d{2})[-_:.]?(\d{2})[-_:.]?(\d{2})\D?(\d{2})[-_:.]?(\d{2})(?:[-_:.]?(\d{2}))?/,
  );
  if (!match) return null;

  const [, year, month, day, hour, minute, second = "0"] = match;
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

async function readExifCaptureDate(file) {
  if (!window.exifr) return null;

  try {
    const exif = await window.exifr.parse(file);
    return (
      parseExifDate(exif?.DateTimeOriginal) ||
      parseExifDate(exif?.CreateDate) ||
      parseExifDate(exif?.ModifyDate)
    );
  } catch {
    return null;
  }
}

async function readCapture(file) {
  const exifDate = await readExifCaptureDate(file);
  if (exifDate) return { ...captureParts(exifDate), source: "EXIF" };

  const fileNameDate = parseDateFromFileName(file.name);
  if (fileNameDate) return { ...captureParts(fileNameDate), source: "имя файла" };

  const fallbackDate = new Date(file.lastModified || Date.now());
  return { ...captureParts(fallbackDate), source: "свойства файла" };
}

function numbersFromFileName(fileName) {
  const matches = String(fileName || "").match(/\d{2,}/g);
  return matches ? [...new Set(matches)] : [];
}

function idFromFileName(fileName) {
  const cleanName = String(fileName || "")
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 28)
    .toUpperCase();
  return uniqueId(cleanName || "PHOTO");
}

function uniqueId(prefix) {
  const base = String(prefix || "PHOTO").replace(/[^a-z0-9-]/gi, "").toUpperCase();
  let index = photos.length + 1;
  let candidate = `${base}-${String(index).padStart(3, "0")}`;

  while (photos.some((photo) => photo.id === candidate)) {
    index += 1;
    candidate = `${base}-${String(index).padStart(3, "0")}`;
  }

  return candidate;
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showToast(message) {
  const toast = $("#adminToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

function getSearchQuery() {
  return $("#adminSearch").value.trim().toLowerCase();
}

function getFilteredEvents() {
  const query = getSearchQuery();
  if (!query) return events;

  return events.filter((event) => {
    const eventPhotos = photos.filter((photo) => photo.event === event.name);
    return [
      event.name,
      event.location,
      event.dateFrom,
      event.dateTo,
      eventPhotos.map((photo) => photo.bibs.join(" ")).join(" "),
      eventPhotos.map((photo) => photo.manualPacks.join(" ")).join(" "),
    ]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

function getFilteredPhotos() {
  const query = $("#adminSearch").value.trim().toLowerCase();
  const eventPhotos = selectedEventName
    ? photos.filter((photo) => photo.event === selectedEventName)
    : photos;
  if (!query) return eventPhotos;

  return eventPhotos.filter((photo) => {
    return [photo.id, photo.event, photo.zone, photo.bibs.join(" "), photo.manualPacks.join(" "), photo.qrRaw]
      .join(" ")
      .toLowerCase()
      .includes(query);
});
}

function renderList() {
  if (!selectedEventName) {
    renderEventCatalog();
    return;
  }

  renderPhotoCatalog();
}

function renderEventCatalog() {
  const visible = getFilteredEvents();

  $("#adminTitle").textContent = "События";
  $("#adminSummary").textContent = `${events.length} событий · ${photos.length} фото`;
  $("#newPhotoButton").hidden = true;
  $("#adminBackEvents").hidden = true;
  $("#photoForm").hidden = true;
  $("#adminEditorEmpty").hidden = false;
  $("#adminList").innerHTML = visible.length
    ? visible.map(renderAdminEventCard).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="calendar-x"></i><strong>События не найдены</strong></div>`;

  refreshIcons();
}

function renderPhotoCatalog() {
  const visible = getFilteredPhotos();
  const hiddenCount = photos.filter((photo) => photo.event === selectedEventName && photo.hidden).length;

  $("#adminTitle").textContent = selectedEventName;
  $("#adminSummary").textContent = `${visible.length} фото · скрыто ${hiddenCount}`;
  $("#newPhotoButton").hidden = false;
  $("#adminBackEvents").hidden = false;
  $("#adminList").innerHTML = visible.length
    ? visible.map(renderCard).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="image-off"></i><strong>В событии нет фото</strong></div>`;

  refreshIcons();
}

function renderAdminEventCard(event) {
  const eventPhotos = photos.filter((photo) => photo.event === event.name);
  const hiddenCount = eventPhotos.filter((photo) => photo.hidden).length;
  const activeCount = eventPhotos.length - hiddenCount;

  return `
    <button class="event-card admin-event-card" type="button" data-admin-event="${escapeHtml(event.name)}">
      <span class="event-card-main">
        <strong>${escapeHtml(event.name)}</strong>
        <small>${escapeHtml(event.location || "локация не указана")}</small>
        <em>${escapeHtml([event.dateFrom, event.dateTo].filter(Boolean).join(" - ") || "даты не указаны")}</em>
      </span>
      <span class="event-flags">
        <span class="status-pill status-paid">${activeCount} в выдаче</span>
        <span class="status-pill ${hiddenCount ? "status-cancelled" : ""}">${hiddenCount} скрыто</span>
      </span>
    </button>`;
}

function renderCard(photo) {
  const [bg, accent, dark] = getEventPalette(photo.event);
  const active = photo.id === selectedId && !draftMode;
  const image = photo.imageData || DEMO_IMAGES[photo.id] || "";

  return `
    <button class="admin-card ${active ? "active" : ""}" type="button" data-id="${escapeHtml(photo.id)}">
      ${
        image
          ? `<span class="admin-thumb has-image"><img src="${escapeHtml(image)}" alt="" /></span>`
          : `<span class="admin-thumb" style="--bg:${bg}; --accent:${accent}; --dark:${dark};">
              <span>${escapeHtml(photo.bibs[0] || photo.id.slice(-3))}</span>
            </span>`
      }
      <span class="admin-card-main">
        <strong>${escapeHtml(photo.id)}</strong>
        <small>${escapeHtml(photo.event)} · ${escapeHtml(zoneLabel(photo.zone))}</small>
        <em>${escapeHtml(photo.date)} · № ${escapeHtml(photo.bibs.join(", ") || "нет")}${
          photo.manualPacks.length ? ` · добавлено к ${escapeHtml(photo.manualPacks.join(", "))}` : ""
        }${photo.qrRaw ? " · QR" : ""}${photo.hidden ? " · скрыто" : ""}</em>
      </span>
      ${photo.hidden ? `<span class="status-pill status-cancelled">Скрыто</span>` : ""}
    </button>`;
}

function photoFromForm() {
  const existingPhoto = photos.find((photo) => photo.id === selectedId);
  const event = getEvent(fields.event.value);

  return normalizePhoto({
    id: fields.id.value,
    event: fields.event.value,
    date: fields.date.value,
    time: fields.time.value,
    zone: fields.zone.value,
    scene: existingPhoto?.scene || inferScene(fields.zone.value),
    photographer: fields.photographer.value,
    bibs: fields.bibs.value,
    manualPacks: fields.manualPacks.value,
    manualNote: existingPhoto?.manualNote || "",
    match: existingPhoto?.match || 0,
    price: event?.priceSingle || existingPhoto?.price || 0,
    faces: existingPhoto?.faces || false,
    palette: event?.coverPalette || existingPhoto?.palette || ["#dce8ed", "#e2795a", "#27323a"],
    imageData: currentImageData || existingPhoto?.imageData || "",
    imageName: currentImageName || existingPhoto?.imageName || "",
    qrRaw: currentQrRaw || existingPhoto?.qrRaw || "",
    hidden: fields.hidden.checked,
  });
}

function fillForm(photo, mode = "edit") {
  const normalized = normalizePhoto(photo);

  fields.id.value = normalized.id;
  renderEventOptions(normalized.event);
  renderLocationOptions(normalized.event, normalized.zone);
  fields.date.value = normalized.date;
  fields.time.value = normalized.time;
  fields.photographer.value = normalized.photographer;
  fields.bibs.value = normalized.bibs.join(", ");
  fields.manualPacks.value = normalized.manualPacks.join(", ");
  fields.hidden.checked = normalized.hidden;
  currentImageData = normalized.imageData;
  currentImageName = normalized.imageName;
  currentQrRaw = normalized.qrRaw;
  currentPhotoMeta = null;
  renderImagePreview(normalized);

  draftMode = mode === "new";
  selectedId = draftMode ? "" : normalized.id;
  selectedEventName = normalized.event;
  $("#photoForm").hidden = false;
  $("#adminEditorEmpty").hidden = true;
  $("#editorMode").textContent = draftMode ? "New" : "Edit";
  $("#deleteButton").disabled = draftMode;
  renderPreview();
  renderList();
}

function renderImagePreview(photo) {
  const image = photo.imageData || DEMO_IMAGES[photo.id] || "";
  fields.imagePreview.innerHTML = image
    ? `<img src="${escapeHtml(image)}" alt="" />`
    : `<svg viewBox="0 0 240 120" aria-hidden="true">
        <rect width="240" height="120" fill="#dce8ed" />
        <path d="M0 90 C58 62 92 112 142 82 S204 70 240 54 V120 H0 Z" fill="#27323a" opacity=".24" />
        <rect x="86" y="42" width="68" height="38" rx="8" fill="#fff" />
      </svg>`;
}

function renderPreview() {
  const photo = photoFromForm();
  const [bg, accent, dark] = getEventPalette(photo.event);
  const eventPrice = getEventPrice(photo.event);

  $("#editorPreview").innerHTML = `
    <div class="preview-art" style="--bg:${bg}; --accent:${accent}; --dark:${dark};">
      <span class="preview-zone">${escapeHtml(zoneLabel(photo.zone))}</span>
      <span class="preview-runner">
        <i>${escapeHtml(photo.bibs[0] || "000")}</i>
      </span>
    </div>
    <div class="preview-meta">
      <strong>${escapeHtml(photo.id)}</strong>
      <span>${escapeHtml(photo.event)} · ${escapeHtml(photo.date)} ${escapeHtml(photo.time)}</span>
      <span>${escapeHtml(photo.manualPacks.length ? `Добавлено к: ${photo.manualPacks.join(", ")}` : "Без ручной привязки")}</span>
      <span>${escapeHtml(photo.hidden ? "Фото скрыто из выдачи" : "Фото видно в выдаче")}</span>
      <span>${escapeHtml(eventPrice ? `Цена из события: ${money(eventPrice)}` : "Цена события не задана")}</span>
    </div>`;
}

function selectPhoto(id) {
  const photo = photos.find((item) => item.id === id);
  if (!photo) return;
  fillForm(photo);
}

function selectAdminEvent(eventName) {
  selectedEventName = eventName;
  selectedId = "";
  draftMode = false;
  $("#photoForm").hidden = true;
  $("#adminEditorEmpty").hidden = false;
  $("#adminEditorEmpty").innerHTML = `
    <i data-lucide="image"></i>
    <strong>Выберите фото</strong>
    <span>Откройте фотографию внутри события или добавьте новую.</span>`;
  renderList();
}

function showAdminEvents() {
  selectedEventName = "";
  selectedId = "";
  draftMode = false;
  $("#photoForm").hidden = true;
  $("#adminEditorEmpty").hidden = false;
  $("#adminEditorEmpty").innerHTML = `
    <i data-lucide="calendar-search"></i>
    <strong>Выберите событие</strong>
    <span>После выбора события откроется список фотографий.</span>`;
  renderList();
}

function createNewPhoto() {
  const event = getEvent(selectedEventName) || events[0] || null;
  if (!event) {
    showToast("Сначала создайте событие");
    return;
  }

  const firstLocation = event?.locations?.[0] || "Трасса";

  fillForm(
    {
      id: uniqueId("NEW"),
      event: event.name,
      date: event?.dateFrom || new Date().toISOString().slice(0, 10),
      time: "10:00:00",
      zone: firstLocation,
      scene: inferScene(firstLocation),
      photographer: "",
      bibs: [],
      manualPacks: [],
      manualNote: "",
      faces: false,
      match: 0,
      price: event?.priceSingle || 0,
      palette: event?.coverPalette || ["#dce8ed", "#e2795a", "#27323a"],
    },
    "new",
  );
}

function saveCurrentPhoto(event) {
  event.preventDefault();

  const nextPhoto = photoFromForm();
  const duplicate = photos.find(
    (photo) => photo.id === nextPhoto.id && (draftMode || photo.id !== selectedId),
  );

  if (duplicate) {
    showToast("Такой ID уже есть");
    return;
  }

  const index = photos.findIndex((photo) => photo.id === selectedId);
  if (index >= 0 && !draftMode) {
    photos[index] = nextPhoto;
  } else {
    photos.unshift(nextPhoto);
  }

  selectedId = nextPhoto.id;
  selectedEventName = nextPhoto.event;
  draftMode = false;
  savePhotos();
  fillForm(nextPhoto);
  showToast("Сохранено");
}

function duplicateCurrentPhoto() {
  const copy = photoFromForm();
  copy.id = uniqueId(copy.id || "COPY");
  fillForm(copy, "new");
  showToast("Создан дубль");
}

function deleteCurrentPhoto() {
  if (draftMode || !selectedId) return;

  const photo = photos.find((item) => item.id === selectedId);
  if (!photo || !window.confirm(`Удалить ${photo.id}?`)) return;

  photos = photos.filter((item) => item.id !== selectedId);
  savePhotos();
  const nextPhoto = photos.find((item) => item.event === selectedEventName) || null;
  selectedId = nextPhoto?.id || "";

  if (nextPhoto) {
    fillForm(nextPhoto);
  } else {
    $("#photoForm").hidden = true;
    $("#adminEditorEmpty").hidden = false;
    renderList();
  }

  showToast("Удалено");
}

function resetData() {
  if (!window.confirm("Вернуть демо-данные?")) return;

  photos = DEFAULT_PHOTOS.map(normalizePhoto);
  events = loadEvents();
  savePhotos();
  showAdminEvents();
  showToast("Демо-данные восстановлены");
}

function exportData() {
  const blob = new Blob([JSON.stringify(photos, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "picflow-photos.json";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast("Экспорт готов");
}

function importData(file) {
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    try {
      const parsed = JSON.parse(reader.result);
      const imported = Array.isArray(parsed) ? parsed : parsed.photos;
      if (!Array.isArray(imported) || !imported.length) {
        throw new Error("Invalid JSON");
      }

      photos = imported.map(normalizePhoto);
      events = loadEvents();
      savePhotos();
      showAdminEvents();
      showToast("Импортировано");
    } catch {
      showToast("Файл не подходит");
    }
  });
  reader.readAsText(file);
}

async function applyPhotoFile(file) {
  if (!file || !file.type.startsWith("image/")) return;

  const reader = new FileReader();
  reader.addEventListener("load", () => {
    currentImageData = reader.result;
    currentImageName = file.name;
    renderImagePreview({ imageData: currentImageData });
    renderPreview();
  });
  reader.readAsDataURL(file);

  const capture = await readCapture(file);
  fields.date.value = capture.date;
  fields.time.value = capture.time;
  currentPhotoMeta = capture;

  if (draftMode && fields.id.value.startsWith("NEW-")) {
    fields.id.value = idFromFileName(file.name);
  }

  if (!fields.bibs.value.trim()) {
    fields.bibs.value = numbersFromFileName(file.name).join(", ");
  }

  renderPreview();
  showToast(`Дата и время: ${capture.source}`);
}

function bindEvents() {
  $("#adminList").addEventListener("click", (event) => {
    const eventButton = event.target.closest("[data-admin-event]");
    if (eventButton) {
      selectAdminEvent(eventButton.dataset.adminEvent);
      return;
    }

    const button = event.target.closest("[data-id]");
    if (!button) return;
    selectPhoto(button.dataset.id);
  });

  $("#adminSearch").addEventListener("input", renderList);
  $("#adminBackEvents").addEventListener("click", showAdminEvents);
  $("#newPhotoButton").addEventListener("click", createNewPhoto);
  $("#photoForm").addEventListener("submit", saveCurrentPhoto);
  $("#duplicateButton").addEventListener("click", duplicateCurrentPhoto);
  $("#deleteButton").addEventListener("click", deleteCurrentPhoto);
  $("#resetDataButton").addEventListener("click", resetData);
  $("#exportButton").addEventListener("click", exportData);
  $("#importButton").addEventListener("click", () => $("#importFile").click());

  $("#importFile").addEventListener("change", () => {
    const file = $("#importFile").files?.[0];
    if (file) importData(file);
    $("#importFile").value = "";
  });

  fields.imageFile.addEventListener("change", () => {
    const file = fields.imageFile.files?.[0];
    if (file) applyPhotoFile(file);
    fields.imageFile.value = "";
  });

  fields.event.addEventListener("change", () => {
    renderLocationOptions(fields.event.value, "");
    renderPreview();
  });

  Object.values(fields).forEach((field) => {
    field.addEventListener("input", renderPreview);
    field.addEventListener("change", renderPreview);
  });
}

bindEvents();
renderList();
refreshIcons();
