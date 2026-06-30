const PHOTO_STORAGE_KEY = window.PicFlowStorageKey || "picflow.photos.v1";
const ORDER_STORAGE_KEY = window.PicFlowOrderStorageKey || "picflow.orders.v1";
const EVENT_STORAGE_KEY = window.PicFlowEventStorageKey || "picflow.events.v1";
const EDIT_STORAGE_KEY = "picflow.purchasedEdits.v1";
const DEFAULT_PHOTOS = window.PicFlowDefaults || [];
const DEFAULT_ORDERS = window.PicFlowDefaultOrders || [];
const DEFAULT_EVENTS = window.PicFlowDefaultEvents || [];
const DEMO_IMAGES = window.PicFlowDemoImages || {};

const statusLabels = {
  new: "Новый",
  paid: "Оплачен",
  processing: "В работе",
  completed: "Готов",
  cancelled: "Отменён",
};

const purchasedStatuses = new Set(["paid", "processing", "completed"]);

const $ = (selector) => document.querySelector(selector);

let photos = loadPhotos();
let orders = loadOrders();
let events = loadEvents();
let selectedPhotoId = "";
let selectedOrderId = "";
let focusedOrderId = "";
let editorMode = false;
let lastEditorPhotoId = "";
let editorRotation = 0;
let editorPan = { x: 0, y: 0 };
let dragState = null;
let savedEdits = loadSavedEdits();
let applyingSavedEdit = false;

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
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

function loadOrders() {
  try {
    const saved = JSON.parse(localStorage.getItem(ORDER_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) return saved;
  } catch {
    localStorage.removeItem(ORDER_STORAGE_KEY);
  }
  return DEFAULT_ORDERS;
}

function loadEvents() {
  try {
    const saved = JSON.parse(localStorage.getItem(EVENT_STORAGE_KEY) || "null");
    if (Array.isArray(saved) && saved.length) return saved;
  } catch {
    localStorage.removeItem(EVENT_STORAGE_KEY);
  }
  return DEFAULT_EVENTS;
}

function loadSavedEdits() {
  try {
    const saved = JSON.parse(localStorage.getItem(EDIT_STORAGE_KEY) || "{}");
    return saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
  } catch {
    localStorage.removeItem(EDIT_STORAGE_KEY);
    return {};
  }
}

function saveSavedEdits() {
  localStorage.setItem(EDIT_STORAGE_KEY, JSON.stringify(savedEdits, null, 2));
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(`${value}T12:00:00`));
}

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
}

function prettyDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function showToast(message) {
  const toast = $("#purchasedToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

function getPurchasedPhotos() {
  const paidIds = new Set(
    getVisibleOrders()
      .flatMap((order) => order.items || [])
      .map((item) => item.photoId),
  );
  return photos.filter((photo) => paidIds.has(photo.id));
}

function getPhotoImage(photo) {
  return photo?.imageData || DEMO_IMAGES[photo?.id] || "";
}

function getVisibleOrders() {
  if (focusedOrderId) {
    return orders.filter((order) => order.id === focusedOrderId);
  }

  return orders.filter((order) => purchasedStatuses.has(order.status));
}

function getOrderById(orderId) {
  return orders.find((order) => order.id === orderId);
}

function getOrderEntries(order) {
  if (!order) return [];
  const photoById = new Map(photos.map((photo) => [photo.id, photo]));
  return (order.items || []).map((item) => ({
    item,
    photo: photoById.get(item.photoId),
  }));
}

function getSelectedOrder() {
  const directOrder = getOrderById(selectedOrderId);
  if (directOrder) return directOrder;

  return orders.find((order) => (order.items || []).some((item) => item.photoId === selectedPhotoId));
}

function getPurchasedOrderGroups() {
  const photoById = new Map(photos.map((photo) => [photo.id, photo]));
  const byEvent = new Map();

  getVisibleOrders()
    .forEach((order) => {
      (order.items || []).forEach((item) => {
        const photo = photoById.get(item.photoId);
        const eventName = item.event || photo?.event || "Без события";

        if (!byEvent.has(eventName)) {
          byEvent.set(eventName, {
            name: eventName,
            orders: new Map(),
            photoCount: 0,
            total: 0,
          });
        }

        const eventGroup = byEvent.get(eventName);
        if (!eventGroup.orders.has(order.id)) {
          eventGroup.orders.set(order.id, {
            order,
            items: [],
            total: 0,
          });
        }

        const orderGroup = eventGroup.orders.get(order.id);
        const price = Number(item.price) || 0;
        orderGroup.items.push({ item, photo });
        orderGroup.total += price;
        eventGroup.photoCount += 1;
        eventGroup.total += price;
      });
    });

  return [...byEvent.values()]
    .map((eventGroup) => ({
      ...eventGroup,
      orders: [...eventGroup.orders.values()].sort(
        (a, b) => new Date(b.order.createdAt || 0) - new Date(a.order.createdAt || 0),
      ),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function buildOrderLink(order, eventName) {
  const params = new URLSearchParams({
    event: eventName,
    order: order.id,
  });
  return `/orders/?${params.toString()}`;
}

function makeThumb(photo) {
  const image = getPhotoImage(photo);
  if (image) return `<img src="${escapeHtml(image)}" alt="${escapeHtml(photo.id)}" />`;
  const [bg = "#dce8ed", accent = "#e2795a", dark = "#27323a"] = photo.palette || [];
  return `
    <svg viewBox="0 0 330 240" aria-label="${escapeHtml(photo.id)}">
      <rect width="330" height="240" fill="${bg}" />
      <circle cx="280" cy="44" r="52" fill="${accent}" opacity=".72" />
      <path d="M0 180 C80 138 130 204 218 160 S292 132 330 118 V240 H0 Z" fill="${dark}" opacity=".24" />
      <rect x="126" y="88" width="78" height="58" rx="8" fill="#fff" />
      <text x="165" y="123" text-anchor="middle" font-family="Arial" font-size="22" font-weight="800" fill="${dark}">${escapeHtml((photo.bibs || [""])[0] || "PF")}</text>
    </svg>`;
}

function makeThumbDataUrl(photo) {
  const image = getPhotoImage(photo);
  if (image) return image;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(makeThumb(photo))}`;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Image load failed"));
    image.src = src;
  });
}

function drawCoverCentered(context, image, width, height, zoom = 1) {
  const sourceAspect = image.naturalWidth / image.naturalHeight;
  const targetAspect = width / height;
  let sourceWidth = image.naturalWidth;
  let sourceHeight = image.naturalHeight;

  if (sourceAspect > targetAspect) {
    sourceWidth = image.naturalHeight * targetAspect;
  } else {
    sourceHeight = image.naturalWidth / targetAspect;
  }

  sourceWidth = Math.max(1, sourceWidth / zoom);
  sourceHeight = Math.max(1, sourceHeight / zoom);
  const sourceX = (image.naturalWidth - sourceWidth) / 2;
  const sourceY = (image.naturalHeight - sourceHeight) / 2;
  context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, -width / 2, -height / 2, width, height);
}

function drawCroppedCover(context, image, width, height, zoom, rotation, pan) {
  const normalizedRotation = ((rotation % 360) + 360) % 360;
  const rotated = normalizedRotation === 90 || normalizedRotation === 270;
  context.save();
  context.translate(width / 2 + (pan.x / 100) * width, height / 2 + (pan.y / 100) * height);
  context.rotate((normalizedRotation * Math.PI) / 180);
  drawCoverCentered(context, image, rotated ? height : width, rotated ? width : height, zoom);
  context.restore();
}

function drawFittedText(context, text, x, y, maxWidth, baseSize, align = "left") {
  let fontSize = baseSize;
  context.textAlign = align;
  context.textBaseline = "alphabetic";

  do {
    context.font = `900 ${fontSize}px Arial, sans-serif`;
    if (context.measureText(text).width <= maxWidth || fontSize <= 12) break;
    fontSize -= 1;
  } while (fontSize > 12);

  context.fillText(text, x, y);
}

function clampChannel(value) {
  return Math.max(0, Math.min(255, Math.round(value)));
}

function getAdjustmentValues() {
  return {
    temperature: Number($("#editTemperature").value) || 0,
    tint: Number($("#editTint").value) || 0,
    exposure: Number($("#editExposure").value) || 0,
    contrast: Number($("#editContrast").value) || 0,
    highlights: Number($("#editHighlights").value) || 0,
    shadows: Number($("#editShadows").value) || 0,
    whites: Number($("#editWhites").value) || 0,
    blacks: Number($("#editBlacks").value) || 0,
    texture: Number($("#editTexture").value) || 0,
    clarity: Number($("#editClarity").value) || 0,
    dehaze: Number($("#editDehaze").value) || 0,
    vibrance: Number($("#editVibrance").value) || 0,
    saturation: Number($("#editSaturation").value) || 0,
  };
}

function setAdjustmentValues(values = {}) {
  Object.entries({
    editTemperature: values.temperature,
    editTint: values.tint,
    editExposure: values.exposure,
    editContrast: values.contrast,
    editHighlights: values.highlights,
    editShadows: values.shadows,
    editWhites: values.whites,
    editBlacks: values.blacks,
    editTexture: values.texture,
    editClarity: values.clarity,
    editDehaze: values.dehaze,
    editVibrance: values.vibrance,
    editSaturation: values.saturation,
  }).forEach(([id, value]) => {
    $(`#${id}`).value = Number(value) || 0;
  });
}

function getDefaultEditState(photo) {
  const event = getEventInfo(photo);
  return {
    adjustments: {
      temperature: 0,
      tint: 0,
      exposure: 0,
      contrast: 0,
      highlights: 0,
      shadows: 0,
      whites: 0,
      blacks: 0,
      texture: 0,
      clarity: 0,
      dehaze: 0,
      vibrance: 0,
      saturation: 0,
    },
    crop: {
      aspect: "original",
      zoom: 0,
      rotation: 0,
      pan: { x: 0, y: 0 },
    },
    mark: {
      enabled: Boolean(event.frameData),
      personName: "",
      displayTime: photo?.time || "",
      textScale: 135,
      textX: 0,
      textY: 0,
      logoScale: 100,
      logoX: 0,
      logoY: 0,
    },
  };
}

function getMarkValues() {
  return {
    textScale: Number($("#markTextScale").value) || 135,
    textX: Number($("#markTextX").value) || 0,
    textY: Number($("#markTextY").value) || 0,
    logoScale: Number($("#markLogoScale").value) || 100,
    logoX: Number($("#markLogoX").value) || 0,
    logoY: Number($("#markLogoY").value) || 0,
  };
}

function normalizeEditState(photo, state = {}) {
  const defaults = getDefaultEditState(photo);
  return {
    adjustments: { ...defaults.adjustments, ...(state.adjustments || {}) },
    crop: {
      ...defaults.crop,
      ...(state.crop || {}),
      pan: {
        ...defaults.crop.pan,
        ...(state.crop?.pan || {}),
      },
    },
    mark: {
      ...defaults.mark,
      ...(state.mark || {}),
    },
  };
}

function getSavedEditState(photo) {
  return normalizeEditState(photo, savedEdits[photo.id]);
}

function getCurrentEditState(photo) {
  return normalizeEditState(photo, {
    adjustments: getAdjustmentValues(),
    crop: {
      aspect: document.querySelector('input[name="cropAspect"]:checked')?.value || "original",
      zoom: Number($("#editCropZoom").value) || 0,
      rotation: editorRotation,
      pan: { ...editorPan },
    },
    mark: {
      enabled: $("#frameEnabled").checked,
      personName: $("#framePersonName").value.trim(),
      displayTime: $("#frameDisplayTime").value.trim(),
      ...getMarkValues(),
    },
  });
}

function saveCurrentEditState(photo) {
  if (!photo || applyingSavedEdit) return;
  savedEdits[photo.id] = getCurrentEditState(photo);
  saveSavedEdits();
}

function applyEditState(photo, state) {
  const edit = normalizeEditState(photo, state);
  applyingSavedEdit = true;
  setAdjustmentValues(edit.adjustments);
  $("#frameEnabled").checked = Boolean(edit.mark.enabled);
  $("#framePersonName").value = edit.mark.personName || "";
  $("#frameDisplayTime").value = edit.mark.displayTime || photo.time || "";
  $("#markTextScale").value = Number(edit.mark.textScale) || 135;
  $("#markTextX").value = Number(edit.mark.textX) || 0;
  $("#markTextY").value = Number(edit.mark.textY) || 0;
  $("#markLogoScale").value = Number(edit.mark.logoScale) || 100;
  $("#markLogoX").value = Number(edit.mark.logoX) || 0;
  $("#markLogoY").value = Number(edit.mark.logoY) || 0;
  $("#editCropZoom").value = Number(edit.crop.zoom) || 0;
  const aspectControl = document.querySelector(`input[name="cropAspect"][value="${edit.crop.aspect || "original"}"]`);
  (aspectControl || document.querySelector('input[name="cropAspect"][value="original"]')).checked = true;
  editorRotation = Number(edit.crop.rotation) || 0;
  editorPan = {
    x: Number(edit.crop.pan?.x) || 0,
    y: Number(edit.crop.pan?.y) || 0,
  };
  applyingSavedEdit = false;
}

function getCropSettings(imageAspect = 4 / 3) {
  const selectedAspect = document.querySelector('input[name="cropAspect"]:checked')?.value || "original";
  const zoom = Number($("#editCropZoom").value) || 0;
  let aspect = imageAspect;

  if (selectedAspect === "square") aspect = 1;
  if (selectedAspect === "9x16") aspect = 9 / 16;
  if (selectedAspect === "3x4") aspect = 3 / 4;

  const normalizedRotation = ((editorRotation % 360) + 360) % 360;

  return {
    selectedAspect,
    aspect,
    zoom: 1 + zoom / 80,
    previewZoom: 1 + zoom / 80,
    rotation: normalizedRotation,
  };
}

function getPreviewMediaSize(imageAspect, cropSettings) {
  const rotated = cropSettings.rotation === 90 || cropSettings.rotation === 270;
  const effectiveAspect = rotated ? 1 / imageAspect : imageAspect;
  let width = 100;
  let height = 100;

  if (effectiveAspect > cropSettings.aspect) {
    width = (effectiveAspect / cropSettings.aspect) * 100;
  } else {
    height = (cropSettings.aspect / effectiveAspect) * 100;
  }

  return rotated ? { width: height, height: width } : { width, height };
}

function getPanBounds(imageAspect, cropSettings) {
  const rotatedAspect = cropSettings.rotation === 90 || cropSettings.rotation === 270 ? 1 / imageAspect : imageAspect;
  let imageWidth = 1;
  let imageHeight = 1;

  if (rotatedAspect > cropSettings.aspect) {
    imageWidth = rotatedAspect / cropSettings.aspect;
  } else {
    imageHeight = cropSettings.aspect / rotatedAspect;
  }

  imageWidth *= cropSettings.zoom;
  imageHeight *= cropSettings.zoom;

  return {
    x: Math.max(0, (imageWidth - 1) * 50),
    y: Math.max(0, (imageHeight - 1) * 50),
  };
}

function clampPan(pan, cropSettings, imageAspect = 4 / 3) {
  const bounds = getPanBounds(imageAspect, cropSettings);
  return {
    x: Math.max(-bounds.x, Math.min(bounds.x, pan.x)),
    y: Math.max(-bounds.y, Math.min(bounds.y, pan.y)),
  };
}

function applyEditorTransform(cropSettings) {
  const media = document.querySelector(".edited-image > img, .edited-image > svg");
  const frame = document.querySelector(".edited-frame");
  if (!media || !frame) return;
  const rect = frame.getBoundingClientRect();
  const panX = (editorPan.x / 100) * rect.width;
  const panY = (editorPan.y / 100) * rect.height;
  media.style.transform = `translate(-50%, -50%) translate(${panX.toFixed(1)}px, ${panY.toFixed(1)}px) rotate(${cropSettings.rotation}deg) scale(${cropSettings.previewZoom.toFixed(3)})`;
}

function getOutputSize(aspect) {
  const longSide = 1600;
  if (aspect >= 1) {
    return {
      width: longSide,
      height: Math.round(longSide / aspect),
    };
  }

  return {
    width: Math.round(longSide * aspect),
    height: longSide,
  };
}

function buildPreviewFilter(values) {
  const brightness = Math.max(40, 100 + values.exposure * 0.45 + values.whites * 0.08 + values.shadows * 0.05 - values.dehaze * 0.05);
  const contrast = Math.max(40, 100 + values.contrast * 0.55 + values.clarity * 0.22 + values.dehaze * 0.28 + values.blacks * -0.08 + values.highlights * 0.04);
  const saturation = Math.max(0, 100 + values.saturation * 0.65 + values.vibrance * 0.35);
  return `brightness(${brightness}%) contrast(${contrast}%) saturate(${saturation}%)`;
}

function overlayColor(value, positiveColor, negativeColor, maxOpacity = 0.18) {
  if (value === 0) return "transparent";
  const opacity = Math.min(maxOpacity, Math.abs(value) / 100 * maxOpacity);
  const color = value > 0 ? positiveColor : negativeColor;
  return color.replace("OPACITY", opacity.toFixed(3));
}

function getAdjustmentOverlayMarkup(values) {
  const temperature = overlayColor(
    values.temperature,
    "rgba(255, 154, 68, OPACITY)",
    "rgba(72, 145, 255, OPACITY)",
  );
  const tint = overlayColor(
    values.tint,
    "rgba(225, 80, 210, OPACITY)",
    "rgba(58, 190, 105, OPACITY)",
    0.14,
  );
  const lightOpacity = Math.max(0, values.highlights + values.whites - values.dehaze * 0.25) / 200 * 0.18;
  const darkOpacity = Math.max(0, -(values.shadows + values.blacks) + values.dehaze * 0.35) / 200 * 0.22;

  return `
    <div class="editor-adjust-overlay color-overlay" style="background:${temperature};"></div>
    <div class="editor-adjust-overlay tint-overlay" style="background:${tint};"></div>
    <div class="editor-adjust-overlay light-overlay" style="opacity:${lightOpacity.toFixed(3)};"></div>
    <div class="editor-adjust-overlay dark-overlay" style="opacity:${darkOpacity.toFixed(3)};"></div>`;
}

function applyImageAdjustments(context, width, height, values) {
  const imageData = context.getImageData(0, 0, width, height);
  const data = imageData.data;
  const exposureFactor = Math.pow(2, values.exposure / 100);
  const contrastValue = Math.max(-240, Math.min(240, values.contrast * 1.6 + values.clarity * 0.7 + values.dehaze * 0.9));
  const contrastFactor = (259 * (contrastValue + 255)) / (255 * (259 - contrastValue));
  const saturationFactor = 1 + values.saturation / 100;
  const vibranceFactor = values.vibrance / 100;

  for (let index = 0; index < data.length; index += 4) {
    let red = data[index];
    let green = data[index + 1];
    let blue = data[index + 2];

    red += values.temperature * 0.38 + values.tint * 0.18;
    green += values.tint * -0.28;
    blue += values.temperature * -0.38 + values.tint * 0.18;

    red *= exposureFactor;
    green *= exposureFactor;
    blue *= exposureFactor;

    let luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722;
    const highlightWeight = Math.max(0, (luminance - 128) / 127);
    const shadowWeight = Math.max(0, (128 - luminance) / 128);
    const whiteWeight = Math.max(0, (luminance - 200) / 55);
    const blackWeight = Math.max(0, (56 - luminance) / 56);
    const tonalShift =
      values.highlights * highlightWeight * 0.72 +
      values.shadows * shadowWeight * 0.72 +
      values.whites * whiteWeight * 0.82 +
      values.blacks * blackWeight * 0.82 +
      values.dehaze * (highlightWeight - shadowWeight) * 0.28;

    red += tonalShift;
    green += tonalShift;
    blue += tonalShift;

    red = contrastFactor * (red - 128) + 128;
    green = contrastFactor * (green - 128) + 128;
    blue = contrastFactor * (blue - 128) + 128;

    const maxChannel = Math.max(red, green, blue);
    const minChannel = Math.min(red, green, blue);
    const saturationAmount = maxChannel === 0 ? 0 : (maxChannel - minChannel) / maxChannel;
    const vibranceBoost = vibranceFactor * (1 - saturationAmount) * 0.8;
    const clarityBoost = values.clarity * 0.0025 + values.texture * 0.0018;
    const colorFactor = Math.max(0, saturationFactor + vibranceBoost + values.dehaze * 0.0015);
    luminance = red * 0.2126 + green * 0.7152 + blue * 0.0722;

    red = luminance + (red - luminance) * (colorFactor + clarityBoost);
    green = luminance + (green - luminance) * (colorFactor + clarityBoost);
    blue = luminance + (blue - luminance) * (colorFactor + clarityBoost);

    data[index] = clampChannel(red);
    data[index + 1] = clampChannel(green);
    data[index + 2] = clampChannel(blue);
  }

  context.putImageData(imageData, 0, 0);
}

function getEventInfo(photo) {
  return events.find((event) => event.name === photo.event) || {
    name: photo.event,
    dateFrom: photo.date,
    dateTo: photo.date,
    location: "",
    frameData: "",
    frameName: "",
  };
}

function getEventMarkMarkup(event, personName, displayTime, frameEnabled, markSettings = getMarkValues()) {
  if (!event.frameData || !frameEnabled) return "";
  const settings = {
    textScale: Math.max(0.5, (Number(markSettings.textScale) || 135) / 100),
    textX: Number(markSettings.textX) || 0,
    textY: Number(markSettings.textY) || 0,
    logoScale: Math.max(0.5, (Number(markSettings.logoScale) || 100) / 100),
    logoX: Number(markSettings.logoX) || 0,
    logoY: Number(markSettings.logoY) || 0,
  };

  return `
    <div class="event-mark-layer" style="--mark-text-scale:${settings.textScale.toFixed(3)}; --mark-text-x:${settings.textX}; --mark-text-y:${settings.textY}; --mark-logo-scale:${settings.logoScale.toFixed(3)}; --mark-logo-x:${settings.logoX}; --mark-logo-y:${settings.logoY};" aria-hidden="true">
      <span class="event-mark-logo">
        <img src="${escapeHtml(event.frameData)}" alt="" />
      </span>
      <strong class="event-mark-name">${escapeHtml(personName || "Фамилия Имя")}</strong>
      <span class="event-mark-time">${escapeHtml(displayTime || "Время")}</span>
    </div>`;
}

function renderGallery() {
  const groups = getPurchasedOrderGroups();
  const photoCount = groups.reduce((sum, group) => sum + group.photoCount, 0);
  const orderCount = new Set(groups.flatMap((group) => group.orders.map((orderGroup) => orderGroup.order.id))).size;
  const focusedOrder = focusedOrderId ? getOrderById(focusedOrderId) : null;
  $("#purchasedSummary").textContent = focusedOrder
    ? `Заказ ${focusedOrder.id} · ${photoCount} фото · ${money(focusedOrder.total)}`
    : `${photoCount} купленных фото · ${orderCount} заказов`;
  $("#purchasedGallery").innerHTML = groups.length
    ? groups.map(renderPurchasedEventGroup).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="image-off"></i><strong>Купленных фото пока нет</strong></div>`;
  refreshIcons();
}

function renderPurchasedEventGroup(eventGroup) {
  return `
    <section class="purchased-event-group">
      <header class="purchased-event-head">
        <span>
          <strong>${escapeHtml(eventGroup.name)}</strong>
          <small>${eventGroup.orders.length} заказов · ${eventGroup.photoCount} фото</small>
        </span>
        <strong>${money(eventGroup.total)}</strong>
      </header>
      <div class="purchased-order-stack">
        ${eventGroup.orders.map((orderGroup) => renderPurchasedOrderGroup(orderGroup, eventGroup.name)).join("")}
      </div>
    </section>`;
}

function renderPurchasedOrderGroup(orderGroup, eventName) {
  const order = orderGroup.order;
  const orderLink = buildOrderLink(order, eventName);
  const status = statusLabels[order.status] || order.status || "Заказ";

  return `
    <article class="purchased-order-group">
      <div class="purchased-order-head">
        <span class="purchased-order-cell">
          <small>Заказ</small>
          <a class="order-link" href="${escapeHtml(orderLink)}">${escapeHtml(order.id)}</a>
        </span>
        <span class="purchased-order-cell">
          <small>Клиент</small>
          <strong>${escapeHtml(order.customer || "Гость")}</strong>
          <em>${escapeHtml(order.email || "без email")}</em>
        </span>
        <span class="purchased-order-cell">
          <small>Дата</small>
          <strong>${prettyDate(order.createdAt)}</strong>
        </span>
        <span class="status-pill status-${escapeHtml(order.status || "paid")}">${escapeHtml(status)}</span>
        <strong class="purchased-order-total">${money(order.total || orderGroup.total)}</strong>
        <button class="select-button purchased-open-editor" type="button" data-edit-order="${escapeHtml(order.id)}">
          Открыть покупку
        </button>
      </div>
      <div class="gallery dense purchased-order-photos">
        ${orderGroup.items.map((entry) => renderPurchasedPhotoCard(entry, order, eventName)).join("")}
      </div>
    </article>`;
}

function renderPurchasedPhotoCard(entry, order, eventName) {
  const photo =
    entry.photo ||
    {
      id: entry.item.photoId,
      event: eventName,
      time: "",
      bibs: [],
      palette: ["#eee7dc", "#d8c5a7", "#6d6a62"],
    };
  const orderLink = buildOrderLink(order, eventName);

  return `
    <article class="photo-card ${entry.photo ? "" : "missing-photo"}">
      <div class="photo-thumb">${makeThumb(photo)}</div>
      <div class="photo-info">
        <div class="photo-title">
          <div>
            <strong>${escapeHtml(photo.id)}</strong>
            <small>Заказ: <a class="order-link" href="${escapeHtml(orderLink)}">${escapeHtml(order.id)}</a></small>
          </div>
          <strong>${escapeHtml(photo.time || "")}</strong>
        </div>
        ${
          entry.photo
            ? `<button class="select-button" type="button" data-edit-photo="${escapeHtml(photo.id)}" data-order-id="${escapeHtml(order.id)}">Редактировать</button>`
            : `<span class="missing-photo-note">Файл не найден в каталоге</span>`
        }
      </div>
    </article>`;
}

function setEditorMode(enabled) {
  editorMode = enabled;
  $(".purchased-workspace").classList.toggle("is-editing", editorMode);
  $("#purchasedListView").hidden = editorMode;
  $("#editorStage").hidden = !editorMode;
  $("#editorControls").hidden = !editorMode;
}

function renderFilmstrip(order) {
  const entries = getOrderEntries(order).filter((entry) => entry.photo);
  $("#orderFilmstrip").innerHTML = entries.length
    ? entries
        .map(
          ({ photo }) => `
            <button class="filmstrip-item ${photo.id === selectedPhotoId ? "active" : ""}" type="button" data-edit-photo="${escapeHtml(photo.id)}" data-order-id="${escapeHtml(order.id)}">
              <span>${makeThumb(photo)}</span>
              <strong>${escapeHtml(photo.id)}</strong>
              <small>${escapeHtml(photo.time || "")}</small>
            </button>`,
        )
        .join("")
    : `<div class="selected-empty">В заказе нет доступных файлов</div>`;
}

function openEditor(photoId, orderId = "") {
  const order = getOrderById(orderId) || orders.find((item) => (item.items || []).some((entry) => entry.photoId === photoId));
  const firstPhoto = getOrderEntries(order).find((entry) => entry.photo)?.photo;
  selectedOrderId = order?.id || orderId || "";
  selectedPhotoId = photoId || firstPhoto?.id || "";
  lastEditorPhotoId = "";
  setEditorMode(true);
  renderEditor();
  refreshIcons();
}

function renderEditor() {
  if (!editorMode) return;

  const order = getSelectedOrder();
  const photo = photos.find((item) => item.id === selectedPhotoId) || getOrderEntries(order).find((entry) => entry.photo)?.photo;
  if (!photo) {
    $("#editorPhoto").innerHTML = `<div class="selected-empty">Выберите купленное фото</div>`;
    $("#orderFilmstrip").innerHTML = "";
    return;
  }

  selectedPhotoId = photo.id;
  selectedOrderId = order?.id || selectedOrderId;

  if (lastEditorPhotoId !== photo.id) {
    const event = getEventInfo(photo);
    $("#frameStatus").textContent = event.frameData
      ? `Логотип: ${event.frameName || event.name}`
      : "Логотип не загружен";
    applyEditState(photo, getSavedEditState(photo));
    lastEditorPhotoId = photo.id;
  }

  const event = getEventInfo(photo);
  const adjustments = getAdjustmentValues();
  const cropSettings = getCropSettings(4 / 3);
  const mediaSize = getPreviewMediaSize(4 / 3, cropSettings);
  editorPan = clampPan(editorPan, cropSettings);
  const personName = $("#framePersonName").value.trim();
  const displayTime = $("#frameDisplayTime").value.trim();
  const frameEnabled = $("#frameEnabled").checked;
  const orderLink = order ? buildOrderLink(order, photo.event) : "/orders/";
  $("#editorOrderTitle").textContent = order ? `Заказ ${order.id}` : "Заказ";
  $("#editorOrderMeta").textContent = order
    ? `${order.customer || "Гость"} · ${order.email || "без email"} · ${money(order.total)}`
    : `${photo.event} · ${photo.time || ""}`;
  $("#editorOrderLink").href = orderLink;
  $("#editorPhoto").style.setProperty(
    "--editor-frame-width",
    cropSettings.aspect >= 1 ? "min(100%, 880px)" : `min(100%, ${Math.round(620 * cropSettings.aspect)}px)`,
  );
  $("#editorPhoto").style.setProperty("--media-width", `${mediaSize.width}%`);
  $("#editorPhoto").style.setProperty("--media-height", `${mediaSize.height}%`);
  $("#editorPhoto").innerHTML = `
    <div class="edited-frame" style="--crop-aspect:${cropSettings.aspect};">
      <div class="edited-image" style="filter:${buildPreviewFilter(adjustments)};">${makeThumb(photo)}</div>
      ${getAdjustmentOverlayMarkup(adjustments)}
      ${getEventMarkMarkup(event, personName, displayTime, frameEnabled)}
    </div>`;
  applyEditorTransform(cropSettings);
  if (order) renderFilmstrip(order);
  saveCurrentEditState(photo);
}

function getCropSettingsFromEdit(editState, imageAspect = 4 / 3) {
  let aspect = imageAspect;
  const selectedAspect = editState.crop.aspect || "original";

  if (selectedAspect === "square") aspect = 1;
  if (selectedAspect === "9x16") aspect = 9 / 16;
  if (selectedAspect === "3x4") aspect = 3 / 4;

  const normalizedRotation = ((Number(editState.crop.rotation) || 0) % 360 + 360) % 360;

  return {
    selectedAspect,
    aspect,
    zoom: 1 + (Number(editState.crop.zoom) || 0) / 80,
    previewZoom: 1 + (Number(editState.crop.zoom) || 0) / 80,
    rotation: normalizedRotation,
  };
}

function canvasToBlob(canvas, type = "image/jpeg", quality = 0.92) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Canvas export failed"));
    }, type, quality);
  });
}

async function createEditedPhotoBlob(photo, editState) {
  const event = getEventInfo(photo);
  const normalizedEdit = normalizeEditState(photo, editState);
  const image = await loadImage(makeThumbDataUrl(photo));
  const imageAspect = image.naturalWidth / image.naturalHeight;
  const cropSettings = getCropSettingsFromEdit(normalizedEdit, imageAspect);
  const { width, height } = getOutputSize(cropSettings.aspect);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");

  context.fillStyle = "#111111";
  context.fillRect(0, 0, width, height);
  const outputPan = clampPan(normalizedEdit.crop.pan || { x: 0, y: 0 }, cropSettings, imageAspect);
  drawCroppedCover(context, image, width, height, cropSettings.zoom, cropSettings.rotation, outputPan);
  applyImageAdjustments(context, width, height, normalizedEdit.adjustments);

  const frameEnabled = normalizedEdit.mark.enabled && Boolean(event.frameData);
  if (frameEnabled) {
    const frame = await loadImage(event.frameData);
    const markTextScale = Math.max(0.5, (Number(normalizedEdit.mark.textScale) || 135) / 100);
    const markTextX = Number(normalizedEdit.mark.textX) || 0;
    const markTextY = Number(normalizedEdit.mark.textY) || 0;
    const markLogoScale = Math.max(0.5, (Number(normalizedEdit.mark.logoScale) || 100) / 100);
    const markLogoX = Number(normalizedEdit.mark.logoX) || 0;
    const markLogoY = Number(normalizedEdit.mark.logoY) || 0;
    const scale = Math.min((width * 0.16) / frame.naturalWidth, (height * 0.09) / frame.naturalHeight) * markLogoScale;
    const frameWidth = frame.naturalWidth * scale;
    const frameHeight = frame.naturalHeight * scale;
    const frameX = width * 0.045 + markLogoX * width * 0.0025;
    const frameY = height - frameHeight - height * 0.045 + markLogoY * height * 0.0025;
    context.drawImage(frame, frameX, frameY, frameWidth, frameHeight);

    const personName = normalizedEdit.mark.personName || "Фамилия Имя";
    const displayTime = normalizedEdit.mark.displayTime || photo.time || "Время";
    const textY = frameY + frameHeight * 0.72 + markTextY * height * 0.0025;
    const nameMaxWidth = width * 0.42;
    const timeMaxWidth = width * 0.24;
    const nameX = frameX + frameWidth + width * 0.025 + markTextX * width * 0.0025;
    const timeX = width * 0.7 + markTextX * width * 0.0025;

    context.save();
    context.fillStyle = "#ffffff";
    context.shadowColor = "rgba(0, 0, 0, 0.72)";
    context.shadowBlur = 5;
    context.shadowOffsetY = 2;
    drawFittedText(context, personName, nameX, textY, nameMaxWidth, Math.round(width * 0.017 * markTextScale));
    drawFittedText(context, displayTime, timeX, textY, timeMaxWidth, Math.round(width * 0.014 * markTextScale));
    context.restore();
  }

  return canvasToBlob(canvas);
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function sanitizeFilename(value) {
  return String(value || "photo").replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, "_");
}

function makeCrcTable() {
  return Array.from({ length: 256 }, (_, index) => {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    return value >>> 0;
  });
}

const crcTable = makeCrcTable();

function crc32(data) {
  let crc = 0xffffffff;
  data.forEach((byte) => {
    crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  });
  return (crc ^ 0xffffffff) >>> 0;
}

function writeUint16(view, offset, value) {
  view.setUint16(offset, value, true);
}

function writeUint32(view, offset, value) {
  view.setUint32(offset, value, true);
}

function makeZipDate() {
  const now = new Date();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | Math.floor(now.getSeconds() / 2);
  const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  return { date, time };
}

async function createZip(files) {
  const encoder = new TextEncoder();
  const chunks = [];
  const centralDirectory = [];
  let offset = 0;
  const zipDate = makeZipDate();

  for (const file of files) {
    const data = new Uint8Array(await file.blob.arrayBuffer());
    const name = encoder.encode(file.name);
    const crc = crc32(data);
    const localHeader = new Uint8Array(30 + name.length);
    const localView = new DataView(localHeader.buffer);

    writeUint32(localView, 0, 0x04034b50);
    writeUint16(localView, 4, 20);
    writeUint16(localView, 6, 0);
    writeUint16(localView, 8, 0);
    writeUint16(localView, 10, zipDate.time);
    writeUint16(localView, 12, zipDate.date);
    writeUint32(localView, 14, crc);
    writeUint32(localView, 18, data.length);
    writeUint32(localView, 22, data.length);
    writeUint16(localView, 26, name.length);
    writeUint16(localView, 28, 0);
    localHeader.set(name, 30);

    chunks.push(localHeader, data);

    const centralHeader = new Uint8Array(46 + name.length);
    const centralView = new DataView(centralHeader.buffer);
    writeUint32(centralView, 0, 0x02014b50);
    writeUint16(centralView, 4, 20);
    writeUint16(centralView, 6, 20);
    writeUint16(centralView, 8, 0);
    writeUint16(centralView, 10, 0);
    writeUint16(centralView, 12, zipDate.time);
    writeUint16(centralView, 14, zipDate.date);
    writeUint32(centralView, 16, crc);
    writeUint32(centralView, 20, data.length);
    writeUint32(centralView, 24, data.length);
    writeUint16(centralView, 28, name.length);
    writeUint16(centralView, 30, 0);
    writeUint16(centralView, 32, 0);
    writeUint16(centralView, 34, 0);
    writeUint16(centralView, 36, 0);
    writeUint32(centralView, 38, 0);
    writeUint32(centralView, 42, offset);
    centralHeader.set(name, 46);
    centralDirectory.push(centralHeader);

    offset += localHeader.length + data.length;
  }

  const centralStart = offset;
  centralDirectory.forEach((header) => {
    chunks.push(header);
    offset += header.length;
  });

  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  writeUint32(endView, 0, 0x06054b50);
  writeUint16(endView, 4, 0);
  writeUint16(endView, 6, 0);
  writeUint16(endView, 8, files.length);
  writeUint16(endView, 10, files.length);
  writeUint32(endView, 12, offset - centralStart);
  writeUint32(endView, 16, centralStart);
  writeUint16(endView, 20, 0);
  chunks.push(end);

  return new Blob(chunks, { type: "application/zip" });
}

async function downloadEditedPhoto() {
  const photo = photos.find((item) => item.id === selectedPhotoId) || getPurchasedPhotos()[0];
  if (!photo) {
    showToast("Выберите купленное фото");
    return;
  }

  try {
    saveCurrentEditState(photo);
    const blob = await createEditedPhotoBlob(photo, getCurrentEditState(photo));
    downloadBlob(blob, `${photo.id}-edited.jpg`);
    showToast("Файл скачан");
  } catch {
    showToast("Не удалось скачать фото");
  }
}

async function downloadOrderArchive() {
  const order = getSelectedOrder();
  const entries = getOrderEntries(order).filter((entry) => entry.photo);

  if (!order || !entries.length) {
    showToast("В заказе нет доступных фото");
    return;
  }

  try {
    const currentPhoto = photos.find((item) => item.id === selectedPhotoId);
    if (currentPhoto) saveCurrentEditState(currentPhoto);
    showToast("Готовим архив");
    const files = [];

    for (const [index, entry] of entries.entries()) {
      const editState = entry.photo.id === selectedPhotoId ? getCurrentEditState(entry.photo) : getSavedEditState(entry.photo);
      const blob = await createEditedPhotoBlob(entry.photo, editState);
      files.push({
        name: `${String(index + 1).padStart(2, "0")}-${sanitizeFilename(entry.photo.id)}.jpg`,
        blob,
      });
    }

    const archive = await createZip(files);
    downloadBlob(archive, `${sanitizeFilename(order.id)}-photos.zip`);
    showToast("Архив скачан");
  } catch {
    showToast("Не удалось собрать архив");
  }
}

function openOrderFromButton(orderId) {
  const order = getOrderById(orderId);
  const firstPhoto = getOrderEntries(order).find((entry) => entry.photo)?.photo;
  if (!firstPhoto) {
    showToast("В этом заказе нет доступных файлов");
    return;
  }

  openEditor(firstPhoto.id, order.id);
}

function applyInitialSelection() {
  const params = new URLSearchParams(window.location.search);
  const requestedOrderId = params.get("order") || "";
  const requestedPhotoId = params.get("photo") || "";

  if (!requestedOrderId) {
    setEditorMode(false);
    return;
  }

  const order = getOrderById(requestedOrderId);
  if (!order) {
    setEditorMode(false);
    return;
  }

  focusedOrderId = order.id;
  const entries = getOrderEntries(order);
  const requestedPhoto = entries.find((entry) => entry.photo?.id === requestedPhotoId)?.photo;
  const firstPhoto = requestedPhoto || entries.find((entry) => entry.photo)?.photo;
  if (!firstPhoto) {
    setEditorMode(false);
    return;
  }

  selectedOrderId = order.id;
  selectedPhotoId = firstPhoto.id;
  setEditorMode(true);
}

function resetEdits() {
  const photo = photos.find((item) => item.id === selectedPhotoId);
  [
    "editTemperature",
    "editTint",
    "editExposure",
    "editContrast",
    "editHighlights",
    "editShadows",
    "editWhites",
    "editBlacks",
    "editTexture",
    "editClarity",
    "editDehaze",
    "editVibrance",
    "editSaturation",
  ].forEach((id) => {
    $(`#${id}`).value = 0;
  });
  $("#editCropZoom").value = 0;
  $("#markTextScale").value = 135;
  $("#markTextX").value = 0;
  $("#markTextY").value = 0;
  $("#markLogoScale").value = 100;
  $("#markLogoX").value = 0;
  $("#markLogoY").value = 0;
  document.querySelector('input[name="cropAspect"][value="original"]').checked = true;
  editorRotation = 0;
  editorPan = { x: 0, y: 0 };
  if (photo) {
    delete savedEdits[photo.id];
    saveSavedEdits();
  }
  renderEditor();
  showToast("Оригинал восстановлен");
}

function startCropDrag(event) {
  const frame = event.target.closest(".edited-frame");
  if (!frame) return;
  const cropSettings = getCropSettings(4 / 3);
  const rect = frame.getBoundingClientRect();
  dragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startPan: { ...editorPan },
    width: rect.width || 1,
    height: rect.height || 1,
  };
  frame.classList.add("is-dragging");
  frame.setPointerCapture(event.pointerId);
  event.preventDefault();
  editorPan = clampPan(editorPan, cropSettings);
  applyEditorTransform(cropSettings);
}

function moveCropDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  const cropSettings = getCropSettings(4 / 3);
  const nextPan = {
    x: dragState.startPan.x + ((event.clientX - dragState.startX) / dragState.width) * 100,
    y: dragState.startPan.y + ((event.clientY - dragState.startY) / dragState.height) * 100,
  };
  editorPan = clampPan(nextPan, cropSettings);
  applyEditorTransform(cropSettings);
  const photo = photos.find((item) => item.id === selectedPhotoId);
  if (photo) saveCurrentEditState(photo);
}

function endCropDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  const frame = event.target.closest(".edited-frame") || document.querySelector(".edited-frame");
  if (frame) frame.classList.remove("is-dragging");
  dragState = null;
}

function bindEvents() {
  $("#purchasedGallery").addEventListener("click", (event) => {
    const orderButton = event.target.closest("[data-edit-order]");
    if (orderButton) {
      openOrderFromButton(orderButton.dataset.editOrder);
      return;
    }

    const button = event.target.closest("[data-edit-photo]");
    if (!button) return;
    openEditor(button.dataset.editPhoto, button.dataset.orderId);
  });

  $("#orderFilmstrip").addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-photo]");
    if (!button) return;
    openEditor(button.dataset.editPhoto, button.dataset.orderId || selectedOrderId);
  });

  $("#editorPhoto").addEventListener("pointerdown", startCropDrag);
  $("#editorPhoto").addEventListener("pointermove", moveCropDrag);
  $("#editorPhoto").addEventListener("pointerup", endCropDrag);
  $("#editorPhoto").addEventListener("pointercancel", endCropDrag);

  $("#backToPurchasedButton").addEventListener("click", () => {
    focusedOrderId = "";
    selectedOrderId = "";
    selectedPhotoId = "";
    lastEditorPhotoId = "";
    if (window.location.search) {
      window.history.replaceState({}, "", "/purchased/");
    }
    setEditorMode(false);
    renderGallery();
  });
  [
    "frameEnabled",
    "framePersonName",
    "frameDisplayTime",
    "markTextScale",
    "markTextX",
    "markTextY",
    "markLogoScale",
    "markLogoX",
    "markLogoY",
    "editTemperature",
    "editTint",
    "editExposure",
    "editContrast",
    "editHighlights",
    "editShadows",
    "editWhites",
    "editBlacks",
    "editTexture",
    "editClarity",
    "editDehaze",
    "editVibrance",
    "editSaturation",
    "editCropZoom",
  ].forEach((id) => {
    const control = $(`#${id}`);
    control.addEventListener("input", renderEditor);
    control.addEventListener("change", renderEditor);
  });
  document.querySelectorAll('input[name="cropAspect"]').forEach((control) => {
    control.addEventListener("change", renderEditor);
  });
  $("#rotateLeftButton").addEventListener("click", () => {
    editorRotation -= 90;
    editorPan = clampPan(editorPan, getCropSettings(4 / 3));
    renderEditor();
  });
  $("#rotateRightButton").addEventListener("click", () => {
    editorRotation += 90;
    editorPan = clampPan(editorPan, getCropSettings(4 / 3));
    renderEditor();
  });
  $("#resetEditsButton").addEventListener("click", resetEdits);
  $("#saveEditedButton").addEventListener("click", downloadEditedPhoto);
  $("#downloadArchiveButton").addEventListener("click", downloadOrderArchive);
}

bindEvents();
applyInitialSelection();
renderGallery();
renderEditor();
refreshIcons();
