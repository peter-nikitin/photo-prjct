const PHOTO_STORAGE_KEY = window.PicFlowStorageKey || "picflow.photos.v1";
const EVENT_STORAGE_KEY = window.PicFlowEventStorageKey || "picflow.events.v1";
const DEFAULT_PHOTOS = window.PicFlowDefaults || [];
const DEFAULT_EVENTS = window.PicFlowDefaultEvents || [];
const zoneLabels = window.PicFlowZones || {
  start: "Старт",
  track: "Трасса",
  finish: "Финиш",
  expo: "Expo",
};

const $ = (selector) => document.querySelector(selector);

let uploadQueue = [];
let photos = loadPhotos();
let events = loadEvents();

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
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

function savePhotos() {
  localStorage.setItem(PHOTO_STORAGE_KEY, JSON.stringify(photos, null, 2));
}

function normalizeEvent(event) {
  const legacyActive = event.active !== false;
  const uploadEnabled = event.uploadEnabled ?? legacyActive;
  const locations = Array.isArray(event.locations)
    ? event.locations.map(String).filter(Boolean)
    : String(event.locations || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
  const photographers = Array.isArray(event.photographers)
    ? event.photographers.map(String).filter(Boolean)
    : String(event.photographers || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

  return {
    name: String(event.name || "").trim(),
    priceSingle: Math.max(0, Number(event.priceSingle ?? event.singlePrice ?? 0) || 0),
    pricePack: Math.max(0, Number(event.pricePack ?? event.packPrice ?? 0) || 0),
    locations,
    photographers,
    uploadEnabled,
    active: uploadEnabled,
  };
}

function loadEvents() {
  try {
    const saved = JSON.parse(localStorage.getItem(EVENT_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) {
      return saved.map(normalizeEvent).filter((event) => event.name);
    }
  } catch {
    localStorage.removeItem(EVENT_STORAGE_KEY);
  }

  return DEFAULT_EVENTS.map(normalizeEvent).filter((event) => event.name);
}

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function showToast(message) {
  const toast = $("#uploadToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2400);
}

function logLine(message) {
  const log = $("#uploadLog");
  const time = new Date().toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  log.value = `${log.value}${log.value ? "\n" : ""}[${time}] ${message}`;
  log.scrollTop = log.scrollHeight;
}

function renderEventSelect() {
  const uploadEvents = events.filter((event) => event.uploadEnabled);

  $("#uploadEvent").innerHTML = uploadEvents.length
    ? uploadEvents
        .map((event) => `<option value="${escapeHtml(event.name)}">${escapeHtml(event.name)}</option>`)
        .join("")
    : `<option value="">Нет доступных событий</option>`;
  renderEventMetaSelects();
}

function renderEventMetaSelects() {
  const event = events.find((item) => item.name === $("#uploadEvent").value);
  const locations = event?.locations?.length ? event.locations : ["Старт", "Трасса", "Финиш", "Expo"];
  const photographers = event?.photographers?.length ? event.photographers : ["Фотограф не указан"];

  $("#uploadZone").innerHTML = locations
    .map((location) => `<option value="${escapeHtml(location)}">${escapeHtml(location)}</option>`)
    .join("");
  $("#uploadPhotographer").innerHTML = photographers
    .map((photographer) => `<option value="${escapeHtml(photographer)}">${escapeHtml(photographer)}</option>`)
    .join("");
}

function updateQrCapability() {
  const hasNative = "BarcodeDetector" in window;
  const hasJsQr = "jsQR" in window;
  $("#qrCapability").textContent = hasNative || hasJsQr ? "Доступно" : "Библиотека не загрузилась";
}

function fileNumberFallback(fileName) {
  const matches = fileName.match(/\d{2,}/g);
  return matches ? [...new Set(matches)] : [];
}

function uniqueUploadId(fileName) {
  const cleanName = fileName
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 28)
    .toUpperCase();
  return `UPL-${cleanName || Date.now()}`;
}

function imageFromFile(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Image load failed"));
    };
    image.src = url;
  });
}

function makeCanvas(image, maxWidth = 1300) {
  const scale = Math.min(1, maxWidth / image.naturalWidth);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));

  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas;
}

async function detectQr(canvas) {
  if ("BarcodeDetector" in window) {
    try {
      const detector = new BarcodeDetector({ formats: ["qr_code"] });
      const codes = await detector.detect(canvas);
      if (codes[0]?.rawValue) return codes[0].rawValue;
    } catch {
      // Fall through to jsQR.
    }
  }

  if ("jsQR" in window) {
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
    const result = window.jsQR(imageData.data, imageData.width, imageData.height);
    if (result?.data) return result.data;
  }

  return "";
}

function numbersFromQr(rawValue) {
  if (!rawValue) return [];
  const matches = rawValue.match(/\d{2,}/g);
  return matches ? [...new Set(matches)] : [];
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

function applyWatermark(canvas, eventName) {
  const context = canvas.getContext("2d");
  const text = "PICFLOW";
  const fontSize = Math.max(18, Math.round(canvas.width / 34));

  context.save();
  context.globalAlpha = 0.23;
  context.fillStyle = "#ffffff";
  context.strokeStyle = "rgba(0,0,0,.3)";
  context.lineWidth = Math.max(1, Math.round(fontSize / 14));
  context.font = `800 ${fontSize}px Arial, sans-serif`;
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate(-Math.PI / 7);

  const stepX = Math.max(170, canvas.width / 4.4);
  const stepY = Math.max(90, canvas.height / 5.2);

  for (let y = -canvas.height; y <= canvas.height; y += stepY) {
    for (let x = -canvas.width; x <= canvas.width; x += stepX) {
      context.strokeText(text, x, y);
      context.fillText(text, x, y);
      context.globalAlpha = 0.16;
      context.beginPath();
      context.arc(x - fontSize * 0.9, y - fontSize * 0.28, fontSize * 0.5, 0, Math.PI * 2);
      context.stroke();
      context.globalAlpha = 0.23;
    }
  }

  context.restore();

  const badgeHeight = Math.max(44, Math.round(canvas.height * 0.075));
  context.fillStyle = "rgba(20,20,20,.72)";
  context.fillRect(0, canvas.height - badgeHeight, canvas.width, badgeHeight);
  context.fillStyle = "#ffffff";
  context.font = `800 ${Math.round(badgeHeight * 0.42)}px Arial, sans-serif`;
  context.fillText(`PICFLOW PREVIEW · ${eventName}`, 18, canvas.height - badgeHeight * 0.34);

  return canvas.toDataURL("image/jpeg", 0.82);
}

async function prepareFile(file) {
  logLine(`Подготовка ${file.name}`);
  const image = await imageFromFile(file);
  const sourceCanvas = makeCanvas(image, 1300);
  const qrRaw = await detectQr(sourceCanvas);
  const qrNumbers = numbersFromQr(qrRaw);
  const fallbackNumbers = fileNumberFallback(file.name);
  const bibs = qrNumbers.length ? qrNumbers : fallbackNumbers;
  const capture = await readCapture(file);

  return {
    file,
    id: uniqueUploadId(file.name),
    bibs,
    qrRaw,
    image,
    capturedDate: capture.date,
    capturedTime: capture.time,
    captureSource: capture.source,
    status: qrRaw ? "QR найден" : bibs.length ? "Номер из файла" : "Номер не найден",
  };
}

function renderUploadList() {
  $("#processUploadButton").disabled = uploadQueue.length === 0 || !$("#uploadEvent").value;
  const event = events.find((item) => item.name === $("#uploadEvent").value);
  const eventPrice = event?.priceSingle || 0;
  $("#uploadSummary").textContent = uploadQueue.length
    ? `${uploadQueue.length} фото готово к обработке`
    : "Фото будут добавлены в фотобанк";

  $("#uploadList").innerHTML = uploadQueue.length
    ? uploadQueue
        .map(
          (item) => {
            const exists = photos.some((photo) => photo.id === item.id);
            return `
            <div class="upload-row">
              <span class="upload-row-icon"><i data-lucide="${item.qrRaw ? "qr-code" : "image"}"></i></span>
              <span class="upload-row-main">
                <strong>${escapeHtml(item.file.name)}</strong>
                <small>${escapeHtml(item.status)} · ${escapeHtml(item.capturedDate)} ${escapeHtml(
                  item.capturedTime,
                )} · ${escapeHtml(item.captureSource)} · № ${escapeHtml(item.bibs.join(", ") || "не найден")}${
                  exists ? " · уже есть" : ""
                }</small>
                ${item.qrRaw ? `<em>QR: ${escapeHtml(item.qrRaw)}</em>` : ""}
              </span>
              <span class="admin-price">${money(eventPrice)}</span>
            </div>`;
          },
        )
        .join("")
    : `<div class="empty-state admin-empty"><i data-lucide="image-plus"></i><strong>Файлы не выбраны</strong></div>`;

  refreshIcons();
}

async function addFiles(files) {
  const imageFiles = [...files].filter((file) => file.type.startsWith("image/"));
  if (!imageFiles.length) return;

  $("#uploadSummary").textContent = "Читаю QR и готовлю фото";
  const prepared = [];
  logLine(`Выбрано файлов: ${imageFiles.length}`);

  for (const file of imageFiles) {
    try {
      prepared.push(await prepareFile(file));
    } catch {
      logLine(`Ошибка чтения ${file.name}`);
      showToast(`Не удалось прочитать ${file.name}`);
    }
  }

  uploadQueue = [...uploadQueue, ...prepared];
  logLine(`Готово к загрузке: ${prepared.length}`);
  renderUploadList();
}

async function processUpload() {
  if (!uploadQueue.length) return;

  const eventName = $("#uploadEvent").value;
  if (!eventName) {
    showToast("Откройте событие для загрузки");
    return;
  }

  const zone = $("#uploadZone").value;
  const photographer = $("#uploadPhotographer").value;
  if (!zone || !photographer) {
    showToast("Выберите локацию и фотографа");
    return;
  }

  const event = events.find((item) => item.name === eventName);
  const price = event?.priceSingle || 0;
  const duplicateMode =
    document.querySelector('input[name="existingPhotos"]:checked')?.value || "skip";
  const created = [];
  let skipped = 0;
  let overwritten = 0;

  for (const item of uploadQueue) {
    const existingIndex = photos.findIndex((photo) => photo.id === item.id);
    if (existingIndex >= 0 && duplicateMode === "skip") {
      skipped += 1;
      logLine(`Пропущено: ${item.file.name}`);
      continue;
    }

    const canvas = makeCanvas(item.image, 1200);
    const imageData = applyWatermark(canvas, eventName);
    const nextPhoto = {
      id: item.id,
      event: eventName,
      date: item.capturedDate,
      time: item.capturedTime,
      zone,
      photographer,
      bibs: item.bibs,
      faces: false,
      match: item.qrRaw ? 95 : 60,
      price,
      palette: ["#dce8ed", "#e2795a", "#27323a"],
      scene: "wide",
      imageData,
      imageName: item.file.name,
      qrRaw: item.qrRaw,
      manualPacks: [],
      manualNote: "",
      hidden: false,
    };

    if (existingIndex >= 0 && duplicateMode === "overwrite") {
      photos[existingIndex] = nextPhoto;
      overwritten += 1;
      logLine(`Перезаписано: ${item.file.name}`);
    } else {
      created.push(nextPhoto);
      logLine(`Добавлено: ${item.file.name}`);
    }
  }

  photos = [...created, ...photos];
  savePhotos();
  uploadQueue = [];
  renderUploadList();
  showToast(`Добавлено ${created.length}, перезаписано ${overwritten}, пропущено ${skipped}`);
  logLine(`Итог: добавлено ${created.length}, перезаписано ${overwritten}, пропущено ${skipped}`);
}

function bindEvents() {
  $("#uploadFiles").addEventListener("change", () => {
    addFiles($("#uploadFiles").files || []);
    $("#uploadFiles").value = "";
  });

  $("#dropzone").addEventListener("dragover", (event) => {
    event.preventDefault();
    $("#dropzone").classList.add("drag-over");
  });

  $("#dropzone").addEventListener("dragleave", () => {
    $("#dropzone").classList.remove("drag-over");
  });

  $("#dropzone").addEventListener("drop", (event) => {
    event.preventDefault();
    $("#dropzone").classList.remove("drag-over");
    addFiles(event.dataTransfer.files || []);
  });

  $("#processUploadButton").addEventListener("click", processUpload);
  $("#uploadEvent").addEventListener("change", () => {
    renderEventMetaSelects();
    renderUploadList();
  });
  $("#clearUploadButton").addEventListener("click", () => {
    uploadQueue = [];
    renderUploadList();
    logLine("Очередь очищена");
  });
}

renderEventSelect();
updateQrCapability();
renderUploadList();
bindEvents();
refreshIcons();
