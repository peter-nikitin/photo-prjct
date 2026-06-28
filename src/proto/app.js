const defaultPhotos = window.PicFlowDefaults || [
  {
    id: "LDN-1048",
    event: "London 10K",
    date: "2026-06-08",
    time: "09:18",
    zone: "start",
    bibs: ["1842", "921"],
    faces: true,
    match: 86,
    price: 350,
    palette: ["#c9d7c5", "#f0dfb7", "#325a64"],
    scene: "start",
  },
  {
    id: "LDN-1190",
    event: "London 10K",
    date: "2026-06-08",
    time: "10:07",
    zone: "track",
    bibs: ["1842", "2407"],
    faces: true,
    match: 93,
    price: 350,
    palette: ["#b8d7e9", "#f2c464", "#245167"],
    scene: "run",
  },
  {
    id: "LDN-1316",
    event: "London 10K",
    date: "2026-06-08",
    time: "10:43",
    zone: "finish",
    bibs: ["516", "1842"],
    faces: true,
    match: 78,
    price: 350,
    palette: ["#e7d5c2", "#e87052", "#163b3e"],
    scene: "finish",
  },
  {
    id: "LDN-1432",
    event: "London 10K",
    date: "2026-06-09",
    time: "11:12",
    zone: "finish",
    bibs: ["3371"],
    faces: false,
    match: 42,
    price: 350,
    palette: ["#d5e8dc", "#f4c843", "#283b45"],
    scene: "finish",
  },
  {
    id: "BRI-2044",
    event: "Brighton Ride",
    date: "2026-06-13",
    time: "08:52",
    zone: "start",
    bibs: ["778", "1204"],
    faces: true,
    match: 74,
    price: 400,
    palette: ["#c6d6ed", "#f1b768", "#2f3d62"],
    scene: "cycle",
  },
  {
    id: "BRI-2148",
    event: "Brighton Ride",
    date: "2026-06-13",
    time: "09:34",
    zone: "track",
    bibs: ["1204"],
    faces: true,
    match: 91,
    price: 400,
    palette: ["#dae8c2", "#dd6956", "#244342"],
    scene: "cycle",
  },
  {
    id: "BRI-2291",
    event: "Brighton Ride",
    date: "2026-06-14",
    time: "12:05",
    zone: "track",
    bibs: ["606", "778"],
    faces: false,
    match: 48,
    price: 400,
    palette: ["#e2d4bf", "#5aa4a0", "#3b3330"],
    scene: "wide",
  },
  {
    id: "BRI-2366",
    event: "Brighton Ride",
    date: "2026-06-14",
    time: "13:21",
    zone: "finish",
    bibs: ["1204", "882"],
    faces: true,
    match: 82,
    price: 400,
    palette: ["#d6e1ed", "#f2c94c", "#143f4a"],
    scene: "finish",
  },
  {
    id: "EXP-3011",
    event: "Expo Run",
    date: "2026-06-16",
    time: "15:40",
    zone: "expo",
    bibs: ["44"],
    faces: true,
    match: 67,
    price: 250,
    palette: ["#ece4db", "#82b29a", "#2b2e34"],
    scene: "expo",
  },
  {
    id: "EXP-3125",
    event: "Expo Run",
    date: "2026-06-16",
    time: "16:26",
    zone: "expo",
    bibs: ["1842", "44"],
    faces: true,
    match: 88,
    price: 250,
    palette: ["#dce8ed", "#e2795a", "#27323a"],
    scene: "portrait",
  },
  {
    id: "EXP-3270",
    event: "Expo Run",
    date: "2026-06-17",
    time: "09:58",
    zone: "track",
    bibs: ["237", "815"],
    faces: false,
    match: 50,
    price: 250,
    palette: ["#d5decb", "#f4c843", "#2b3e4b"],
    scene: "run",
  },
  {
    id: "EXP-3338",
    event: "Expo Run",
    date: "2026-06-17",
    time: "10:19",
    zone: "finish",
    bibs: ["815"],
    faces: true,
    match: 79,
    price: 250,
    palette: ["#e7d4c8", "#6aa6ad", "#342d35"],
    scene: "finish",
  },
];

const zoneLabels = window.PicFlowZones || {
  start: "Старт",
  track: "Трасса",
  finish: "Финиш",
  expo: "Expo",
};

const STORAGE_KEY = window.PicFlowStorageKey || "picflow.photos.v1";
const ORDER_STORAGE_KEY = window.PicFlowOrderStorageKey || "picflow.orders.v1";
const EVENT_STORAGE_KEY = window.PicFlowEventStorageKey || "picflow.events.v1";
const PROMO_STORAGE_KEY = window.PicFlowPromoStorageKey || "picflow.promos.v1";
const defaultOrders = window.PicFlowDefaultOrders || [];
const defaultEvents = window.PicFlowDefaultEvents || [];
const defaultPromos = window.PicFlowDefaultPromos || [];
const demoImages = window.PicFlowDemoImages || {};
const PHOTO_PAGE_STEP = 3;

function normalizePhoto(photo) {
  const normalizedTime = normalizeTime(photo.time || "10:00");
  const manualPacks = Array.isArray(photo.manualPacks)
    ? photo.manualPacks.map(String).map((item) => item.trim()).filter(Boolean)
    : String(photo.manualPacks || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);

  return {
    id: String(photo.id || "").trim() || `PHOTO-${Date.now()}`,
    event: String(photo.event || "Event").trim(),
    date: photo.date || "2026-06-18",
    time: normalizedTime,
    zone: String(photo.zone || "track").trim(),
    bibs: Array.isArray(photo.bibs)
      ? photo.bibs.map(String).filter(Boolean)
      : String(photo.bibs || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
    faces: Boolean(photo.faces),
    match: Math.max(0, Math.min(100, Number(photo.match) || 0)),
    price: Math.max(0, Number(photo.price) || 0),
    palette:
      Array.isArray(photo.palette) && photo.palette.length >= 3
        ? photo.palette.slice(0, 3)
        : ["#dce8ed", "#e2795a", "#27323a"],
    scene: photo.scene || "run",
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

  return defaultPhotos.map(normalizePhoto);
}

let photos = loadPhotos();

function normalizeEvent(event) {
  const legacyActive = event.active !== false;
  const showInSearch = event.showInSearch ?? legacyActive;
  const showInResults = event.showInResults ?? legacyActive;
  const uploadEnabled = event.uploadEnabled ?? legacyActive;
  const priceSingle = Math.max(0, Number(event.priceSingle ?? event.singlePrice ?? 0) || 0);
  const pricePack = Math.max(0, Number(event.pricePack ?? event.packPrice ?? 0) || 0);
  const coverPalette =
    Array.isArray(event.coverPalette) && event.coverPalette.length >= 3
      ? event.coverPalette.slice(0, 3)
      : ["#dce8ed", "#f0dfb7", "#325a64"];
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

  return {
    name: String(event.name || "").trim(),
    dateFrom: event.dateFrom || "",
    dateTo: event.dateTo || "",
    description: String(event.description || "").trim(),
    coverData: event.coverData || "",
    coverPalette,
    locations,
    photographers,
    priceSingle,
    pricePack,
    showInSearch,
    showInResults,
    uploadEnabled,
    active: showInSearch && showInResults && uploadEnabled,
    location: String(event.location || "").trim(),
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
    loadedEvents = defaultEvents.map(normalizeEvent).filter((event) => event.name);
  }

  const byName = new Map(loadedEvents.map((event) => [event.name, event]));
  photos.forEach((photo) => {
    if (!photo.event || byName.has(photo.event)) return;
    byName.set(photo.event, {
      name: photo.event,
      dateFrom: photo.date || "",
      dateTo: photo.date || "",
      priceSingle: Math.max(0, Number(photo.price) || 0),
      pricePack: 0,
      description: "",
      coverPalette: photo.palette || ["#dce8ed", "#f0dfb7", "#325a64"],
      locations: [],
      photographers: photo.photographer ? [photo.photographer] : [],
      showInSearch: true,
      showInResults: true,
      uploadEnabled: true,
      active: true,
      location: "",
    });
  });

  return [...byName.values()];
}

let events = loadEvents();

const state = {
  event: "",
  zone: "all",
  faceLoaded: false,
  dense: false,
  photoSort: "asc",
  eventSort: "date-desc",
  eventPageSize: 6,
  photoPageSize: PHOTO_PAGE_STEP,
  searchStarted: false,
  wideSearchActive: false,
  wideSearchSnapshot: null,
  appliedPromo: null,
  selected: new Set(),
  selectionMeta: new Map(),
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const eventHome = $("#eventHome");
const searchWorkspace = $("#searchWorkspace");
const eventGrid = $("#eventGrid");
const eventHomeSearch = $("#eventHomeSearch");
const eventHomeSort = $("#eventHomeSort");
const photoLoadMore = $("#photoLoadMore");
const gallery = $("#gallery");
const emptyState = $("#emptyState");
const bibInput = $("#bibNumber");
const dateFrom = $("#dateFrom");
const timeFrom = $("#timeFrom");
const timeTo = $("#timeTo");
const eventSelect = $("#eventSelect");
const minMatch = $("#minMatch");
const minMatchValue = $("#minMatchValue");
const faceFile = $("#faceFile");
const facePreview = $("#facePreview");
const faceStatus = $("#faceStatus");
const faceName = $("#faceName");
const uploadBox = document.querySelector(".upload-box");
const possibleMatchBlock = $("#possibleMatchBlock");
const possibleMatchList = $("#possibleMatchList");
const otherEventsBlock = $("#otherEventsBlock");
const otherEventsList = $("#otherEventsList");
const packOffer = $("#packOffer");

function money(value) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(value);
}

function getEventPricing(eventName) {
  return events.find((event) => event.name === eventName) || null;
}

function getPhotoPrice(photo) {
  const event = getEventPricing(photo.event);
  return event?.priceSingle ? event.priceSingle : photo.price;
}

function getSelectionMeta(photoId) {
  return state.selectionMeta.get(photoId) || { packEligible: false };
}

function setPhotoSelected(photo, packEligible) {
  if (state.selected.has(photo.id)) {
    state.selected.delete(photo.id);
    state.selectionMeta.delete(photo.id);
    return false;
  }

  state.selected.add(photo.id);
  state.selectionMeta.set(photo.id, { packEligible: Boolean(packEligible) });
  return true;
}

function getSinglePhotoDiscountRate(count) {
  if (count >= 20) return 40;
  if (count >= 15) return 35;
  if (count >= 10) return 30;
  if (count >= 5) return 25;
  return 0;
}

function getSelectionPricing(selectedPhotos) {
  const byEvent = new Map();
  const packPhotoIds = new Set();
  const singlePhotoIds = new Set();
  const eventBreakdowns = [];
  let subtotal = 0;
  let total = 0;
  let packSubtotal = 0;
  let packTotal = 0;
  let singleSubtotal = 0;
  let singleTotal = 0;
  let singleDiscount = 0;
  let packCount = 0;
  let singleCount = 0;

  selectedPhotos.forEach((photo) => {
    if (!byEvent.has(photo.event)) {
      byEvent.set(photo.event, {
        eventName: photo.event,
        packPhotos: [],
        singlePhotos: [],
      });
    }

    const eventGroup = byEvent.get(photo.event);
    if (getSelectionMeta(photo.id).packEligible) {
      eventGroup.packPhotos.push(photo);
    } else {
      eventGroup.singlePhotos.push(photo);
    }
  });

  byEvent.forEach((eventGroup, eventName) => {
    const event = getEventPricing(eventName);
    const eventPackSubtotal = eventGroup.packPhotos.reduce((sum, photo) => sum + getPhotoPrice(photo), 0);
    const eventPackTotal = eventGroup.packPhotos.length ? event?.pricePack > 0 ? event.pricePack : eventPackSubtotal : 0;
    const eventSingleSubtotal = eventGroup.singlePhotos.reduce((sum, photo) => sum + getPhotoPrice(photo), 0);
    const eventSingleDiscountRate = getSinglePhotoDiscountRate(eventGroup.singlePhotos.length);
    const eventSingleDiscount = Math.round((eventSingleSubtotal * eventSingleDiscountRate) / 100);
    const eventSingleTotal = eventSingleSubtotal - eventSingleDiscount;
    const eventSubtotal = eventPackSubtotal + eventSingleSubtotal;
    const eventTotal = eventPackTotal + eventSingleTotal;
    const eventPackSaving = Math.max(0, eventPackSubtotal - eventPackTotal);

    subtotal += eventSubtotal;
    total += eventTotal;
    packSubtotal += eventPackSubtotal;
    packTotal += eventPackTotal;
    singleSubtotal += eventSingleSubtotal;
    singleTotal += eventSingleTotal;
    singleDiscount += eventSingleDiscount;
    packCount += eventGroup.packPhotos.length;
    singleCount += eventGroup.singlePhotos.length;

    eventGroup.packPhotos.forEach((photo) => packPhotoIds.add(photo.id));
    eventGroup.singlePhotos.forEach((photo) => singlePhotoIds.add(photo.id));

    eventBreakdowns.push({
      eventName,
      photoCount: eventGroup.packPhotos.length + eventGroup.singlePhotos.length,
      packCount: eventGroup.packPhotos.length,
      singleCount: eventGroup.singlePhotos.length,
      subtotal: eventSubtotal,
      total: eventTotal,
      packSubtotal: eventPackSubtotal,
      packTotal: eventPackTotal,
      packSaving: eventPackSaving,
      singleSubtotal: eventSingleSubtotal,
      singleTotal: eventSingleTotal,
      singleDiscount: eventSingleDiscount,
      singleDiscountRate: eventSingleDiscountRate,
      autoDiscount: eventPackSaving + eventSingleDiscount,
    });
  });

  const packSaving = Math.max(0, packSubtotal - packTotal);
  const autoDiscount = packSaving + singleDiscount;

  const mode = packCount && singleCount ? "mixed" : packCount ? "pack" : "single";
  const eventPrefix = eventBreakdowns.length > 1 ? `${eventBreakdowns.length} события · ` : "";
  const hasEventDiscount = eventBreakdowns.some((event) => event.singleDiscountRate > 0);
  const note =
    mode === "mixed"
      ? `${eventPrefix}фотопак (${packCount}) + отдельно (${singleCount})${hasEventDiscount ? " · скидка по событиям" : ""}`
      : mode === "pack"
        ? `${eventPrefix}фотопак ${packCount} фото`
        : hasEventDiscount
          ? `${eventPrefix}отдельные фото · скидка по событиям`
          : "Цена за фото";

  return {
    total,
    subtotal,
    mode,
    note,
    packPhotoIds,
    singlePhotoIds,
    packCount,
    singleCount,
    packSubtotal,
    packTotal,
    singleSubtotal,
    singleTotal,
    singleDiscount,
    singleDiscountRate: eventBreakdowns.length === 1 ? eventBreakdowns[0].singleDiscountRate : 0,
    autoDiscount,
    eventBreakdowns,
  };
}

function promoAppliesToSelection(promo, selectedPhotos) {
  if (!promo || !selectedPhotos.length || !promo.active) return false;

  const today = new Date().toISOString().slice(0, 10);
  const dateMatch = (!promo.startsAt || promo.startsAt <= today) && (!promo.endsAt || promo.endsAt >= today);
  const limitMatch = !promo.usageLimit || promo.used < promo.usageLimit;
  const eventMatch =
    promo.scope === "all" || selectedPhotos.some((photo) => promo.events.includes(photo.event));

  return dateMatch && limitMatch && eventMatch;
}

function getPromoDiscount(basePricing, selectedPhotos) {
  const promo = state.appliedPromo;
  if (!promo || !promoAppliesToSelection(promo, selectedPhotos)) {
    return { discount: 0, label: "", promo: null };
  }

  if (promo.type === "percent") {
    return {
      discount: Math.min(basePricing.total, Math.round((basePricing.total * promo.value) / 100)),
      label: `${promo.value}%`,
      promo,
    };
  }

  if (promo.type === "fixed") {
    return { discount: Math.min(basePricing.total, promo.value), label: money(promo.value), promo };
  }

  if (promo.type === "free_photo") {
    const cheapest = Math.min(...selectedPhotos.map(getPhotoPrice));
    return { discount: Math.min(basePricing.total, cheapest), label: "1 фото бесплатно", promo };
  }

  if (promo.type === "free_pack") {
    return {
      discount: Math.min(basePricing.total, basePricing.packTotal || 0),
      label: "фотопак бесплатно",
      promo,
    };
  }

  if (promo.type === "photo_quantity_discount") {
    const limit = promo.photoLimit || 1;
    const discountable = selectedPhotos
      .map(getPhotoPrice)
      .sort((a, b) => b - a)
      .slice(0, limit);
    const discountBase = discountable.reduce((sum, price) => sum + price, 0);
    return {
      discount: Math.min(basePricing.total, Math.round((discountBase * promo.value) / 100)),
      label: `${limit} фото со скидкой ${promo.value}%`,
      promo,
    };
  }

  return { discount: 0, label: "", promo: null };
}

function getCheckoutPricing(selectedPhotos) {
  const base = getSelectionPricing(selectedPhotos);
  const promo = getPromoDiscount(base, selectedPhotos);

  return {
    ...base,
    discount: promo.discount,
    promoLabel: promo.label,
    promo: promo.promo,
    finalTotal: Math.max(0, base.total - promo.discount),
  };
}

function getPackOffer(visiblePhotos) {
  if (!state.searchStarted || visiblePhotos.length < 2) return null;

  const byEvent = new Map();
  const filters = getFilters();
  visiblePhotos.filter((photo) => isPhotoPackEligible(photo, filters)).forEach((photo) => {
    if (!byEvent.has(photo.event)) byEvent.set(photo.event, []);
    byEvent.get(photo.event).push(photo);
  });

  return [...byEvent.entries()]
    .map(([eventName, eventPhotos]) => {
      const event = getEventPricing(eventName);
      const subtotal = eventPhotos.reduce((sum, photo) => sum + getPhotoPrice(photo), 0);
      const saving = subtotal - (event?.pricePack || 0);

      return {
        eventName,
        photos: eventPhotos,
        subtotal,
        packPrice: event?.pricePack || 0,
        saving,
      };
    })
    .filter((offer) => offer.photos.length > 1 && offer.packPrice > 0)
    .sort((a, b) => b.saving - a.saving)[0];
}

function loadOrders() {
  try {
    const saved = JSON.parse(localStorage.getItem(ORDER_STORAGE_KEY) || "null");
    if (Array.isArray(saved)) {
      return saved;
    }
  } catch {
    localStorage.removeItem(ORDER_STORAGE_KEY);
  }

  return defaultOrders;
}

function saveOrders(orders) {
  localStorage.setItem(ORDER_STORAGE_KEY, JSON.stringify(orders, null, 2));
}

function normalizePromo(promo) {
  return {
    id: String(promo.id || `PROMO-${Date.now()}`).trim(),
    name: String(promo.name || "").trim() || "Промокод",
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
    scope: promo.scope === "events" ? "events" : "all",
    events: Array.isArray(promo.events) ? promo.events.map(String).filter(Boolean) : [],
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

  return defaultPromos.map(normalizePromo);
}

function savePromos(promos) {
  localStorage.setItem(PROMO_STORAGE_KEY, JSON.stringify(promos, null, 2));
}

function prettyDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
  }).format(new Date(`${value}T12:00:00`));
}

function normalizeTime(value) {
  const [hours = "0", minutes = "0", seconds = "0"] = String(value || "")
    .split(":")
    .map((part) => part.padStart(2, "0"));

  return `${hours.slice(-2)}:${minutes.slice(-2)}:${seconds.slice(-2)}`;
}

function timeToSeconds(value) {
  const parts = normalizeTime(value).split(":").map(Number);
  if (parts.some((part) => Number.isNaN(part))) return null;
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

function secondsToTime(value) {
  const safeValue = Math.max(0, Math.min(86399, Number(value) || 0));
  const hours = Math.floor(safeValue / 3600);
  const minutes = Math.floor((safeValue % 3600) / 60);
  const seconds = safeValue % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function getEventNames() {
  const searchableEvents = events.filter((event) => event.showInSearch).map((event) => event.name);
  return [...new Set(searchableEvents)].sort((a, b) => a.localeCompare(b));
}

function eventPhotoCount(eventName) {
  return photos.filter((photo) => photo.event === eventName).length;
}

function zoneLabel(value) {
  return zoneLabels[value] || value || "Точка не указана";
}

function photoLocationLabel(photo) {
  const label = zoneLabel(photo.zone);
  if (label && label !== "Точка не указана") return label;
  const event = getEventPricing(photo.event);
  return event?.locations?.[0] || "Точка не указана";
}

function getEventLocations(eventName) {
  const event = events.find((item) => item.name === eventName);
  const eventLocations = event?.locations?.length ? event.locations : [];
  const photoLocations = photos
    .filter((photo) => photo.event === eventName)
    .map((photo) => photoLocationLabel(photo));
  return [...new Set([...eventLocations, ...photoLocations])].filter(Boolean);
}

function eventDateLabel(event) {
  return [event.dateFrom, event.dateTo].filter(Boolean).join(" - ") || "даты не указаны";
}

function eventCover(event) {
  if (event.coverData) {
    return `<img src="${escapeHtml(event.coverData)}" alt="" loading="lazy" />`;
  }

  const [bg, accent, dark] = event.coverPalette || ["#dce8ed", "#f0dfb7", "#325a64"];
  return `
    <svg viewBox="0 0 420 220" aria-hidden="true">
      <rect width="420" height="220" fill="${bg}" />
      <circle cx="344" cy="54" r="62" fill="${accent}" opacity=".8" />
      <path d="M0 166 C92 122 146 196 236 144 S352 124 420 98 V220 H0 Z" fill="${dark}" opacity=".26" />
      <path d="M70 42 H220" stroke="#fff" stroke-width="12" opacity=".75" />
      <path d="M70 70 H168" stroke="#fff" stroke-width="8" opacity=".55" />
    </svg>`;
}

function getFilteredEventsForHome() {
  const query = eventHomeSearch.value.trim().toLowerCase();

  return events
    .filter((event) => event.showInSearch)
    .filter((event) => {
      if (!query) return true;
      return [event.name, event.location, event.description, event.dateFrom, event.dateTo]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((a, b) => {
      if (state.eventSort === "date-asc") return a.dateFrom.localeCompare(b.dateFrom);
      if (state.eventSort === "name") return a.name.localeCompare(b.name);
      if (state.eventSort === "photos") return eventPhotoCount(b.name) - eventPhotoCount(a.name);
      return b.dateFrom.localeCompare(a.dateFrom);
    });
}

function renderEventHome() {
  const filtered = getFilteredEventsForHome();
  const visible = filtered;

  $("#eventHomeSummary").textContent = `${filtered.length} мероприятий`;
  eventGrid.innerHTML = visible.length
    ? visible
        .map((event) => {
          const count = eventPhotoCount(event.name);
          return `
            <article class="event-home-card">
              <div class="event-home-cover">${eventCover(event)}</div>
              <div class="event-home-body">
                <span class="event-home-date">${escapeHtml(eventDateLabel(event))}</span>
                <h2>${escapeHtml(event.name)}</h2>
                <p>${escapeHtml(event.description || event.location || "Фотографии мероприятия")}</p>
                <div class="event-home-meta">
                  <span>${escapeHtml(event.location || "локация не указана")}</span>
                  <span>${count} фото</span>
                  <span>${event.pricePack ? `пак ${money(event.pricePack)}` : `шт ${money(event.priceSingle)}`}</span>
                </div>
                <button class="primary-button" type="button" data-open-event="${escapeHtml(event.name)}">
                  <i data-lucide="search"></i>
                  Поиск
                </button>
              </div>
            </article>`;
        })
        .join("")
    : `<div class="empty-state admin-empty"><i data-lucide="calendar-x"></i><strong>Мероприятия не найдены</strong></div>`;

  refreshIcons();
}

function showEventHome() {
  state.event = "";
  document.body.classList.remove("event-search-mode");
  searchWorkspace.hidden = true;
  eventHome.hidden = false;
  renderEventHome();
  renderEventControls();
}

function isActivePhoto(photo) {
  const event = events.find((item) => item.name === photo.event);
  return !photo.hidden && (!event || event.showInResults !== false);
}

function renderEventControls() {
  const eventNames = getEventNames();

  if (state.event && !eventNames.includes(state.event)) {
    state.event = eventNames[0] || "";
  }

  document.querySelector(".event-tabs").innerHTML = [
    `<button class="event-tab ${!state.event ? "active" : ""}" type="button" data-home-events="true">Мероприятия</button>`,
    ...eventNames.map(
      (eventName) =>
        `<button class="event-tab ${state.event === eventName ? "active" : ""}" type="button" data-event="${escapeHtml(eventName)}">${escapeHtml(eventName)}</button>`,
    ),
  ].join("");

  eventSelect.innerHTML = [
    ...eventNames.map(
      (eventName) =>
        `<option value="${escapeHtml(eventName)}">${escapeHtml(eventName)}</option>`,
    ),
  ].join("");
  eventSelect.value = state.event;
}

function openEvent(eventName) {
  const event = events.find((item) => item.name === eventName);
  if (!event) return;

  state.event = event.name;
  state.zone = "all";
  state.searchStarted = false;
  state.photoPageSize = PHOTO_PAGE_STEP;
  document.body.classList.add("event-search-mode");
  eventHome.hidden = true;
  searchWorkspace.hidden = false;
  dateFrom.value = event.dateFrom || "";
  timeFrom.value = "";
  timeTo.value = "";
  $("#currentEventTitle").textContent = event.name;
  renderEventControls();
  renderLocationFilters(event);
  renderGallery();
}

function renderLocationFilters(event) {
  const locations = getEventLocations(event.name);
  $("#zoneFilters").innerHTML = [
    `<button class="chip active" type="button" data-zone="all">Все</button>`,
    ...locations.map(
      (location) =>
        `<button class="chip" type="button" data-zone="${escapeHtml(location)}">${escapeHtml(location)}</button>`,
    ),
  ].join("");
}

function getFilters() {
  return {
    bib: bibInput.value.trim(),
    date: dateFrom.value,
    timeFrom: timeFrom.value,
    timeTo: timeTo.value,
    min: Number(minMatch.value),
    event: state.event,
    zone: state.zone,
  };
}

function getSearchSnapshot() {
  return {
    event: state.event,
    zone: state.zone,
    bib: bibInput.value,
    date: dateFrom.value,
    timeFrom: timeFrom.value,
    timeTo: timeTo.value,
    min: minMatch.value,
    faceLoaded: state.faceLoaded,
    faceName: faceName.textContent,
    faceStatus: faceStatus.textContent,
    facePreview: facePreview.getAttribute("src") || "",
    searchStarted: state.searchStarted,
  };
}

function applySearchSnapshot(snapshot) {
  if (!snapshot) return;

  state.zone = snapshot.zone || "all";
  state.searchStarted = snapshot.searchStarted;
  bibInput.value = snapshot.bib || "";
  dateFrom.value = snapshot.date || "";
  timeFrom.value = snapshot.timeFrom || "";
  timeTo.value = snapshot.timeTo || "";
  minMatch.value = snapshot.min || "70";
  minMatchValue.textContent = `${minMatch.value}%`;
  state.faceLoaded = Boolean(snapshot.faceLoaded);
  faceStatus.textContent = snapshot.faceStatus || "Загрузить фото";
  faceName.textContent = snapshot.faceName || "JPG, PNG, HEIC";
  if (snapshot.facePreview) {
    facePreview.src = snapshot.facePreview;
    uploadBox.classList.add("has-image");
  } else {
    facePreview.removeAttribute("src");
    uploadBox.classList.remove("has-image");
  }
  $$("#zoneFilters .chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.zone === state.zone);
  });
}

function updateWideSearchControl() {
  $("#resetWideSearchButton").hidden = !state.wideSearchActive;
}

function numbersFromText(value) {
  const matches = String(value || "").match(/\d{2,}/g);
  return matches ? [...new Set(matches)] : [];
}

function isBibMatch(photo, filters) {
  const query = String(filters.bib || "").trim().toLowerCase();
  return Boolean(query && photo.bibs.some((bib) => String(bib).trim().toLowerCase() === query));
}

function isQrMatch(photo, filters) {
  if (!filters.bib || !photo.qrRaw) return false;
  const query = filters.bib.toLowerCase();
  const raw = String(photo.qrRaw).toLowerCase();
  return raw === query || numbersFromText(photo.qrRaw).some((number) => number === filters.bib);
}

function isManualPackMatch(photo, filters) {
  if (!filters.bib) return false;
  const query = filters.bib.toLowerCase();
  return photo.manualPacks.some((pack) => {
    const normalizedPack = pack.toLowerCase().trim();
    return normalizedPack === query || numbersFromText(pack).some((number) => number === filters.bib);
  });
}

function isFaceMatch(photo, filters) {
  return Boolean(state.faceLoaded && photo.faces && photo.match >= filters.min);
}

function getRecognitionReasons(photo, filters) {
  const reasons = [];

  if (isFaceMatch(photo, filters)) reasons.push("лицо");
  if (isBibMatch(photo, filters) || isQrMatch(photo, filters) || isManualPackMatch(photo, filters)) {
    reasons.push("номер");
  }

  return [...new Set(reasons)];
}

function hasRecognitionSignal(filters) {
  return Boolean(state.faceLoaded || filters.bib);
}

function isPhotoPackEligible(photo, filters = getFilters()) {
  return getRecognitionReasons(photo, filters).length > 0;
}

function isContextMatch(photo, filters) {
  const eventMatch = Boolean(filters.event) && photo.event === filters.event;
  const zoneMatch =
    filters.zone === "all" || photo.zone === filters.zone || zoneLabel(photo.zone) === filters.zone;
  const dateMatch = !filters.date || photo.date === filters.date;
  const photoSeconds = timeToSeconds(photo.time);
  const fromSeconds = filters.timeFrom ? timeToSeconds(filters.timeFrom) : null;
  const toSeconds = filters.timeTo ? timeToSeconds(filters.timeTo) : null;
  const timeMatch =
    photoSeconds === null ||
    ((fromSeconds === null || toSeconds === null || fromSeconds <= toSeconds) &&
      (fromSeconds === null || photoSeconds >= fromSeconds) &&
      (toSeconds === null || photoSeconds <= toSeconds)) ||
    (fromSeconds !== null &&
      toSeconds !== null &&
      fromSeconds > toSeconds &&
      (photoSeconds >= fromSeconds || photoSeconds <= toSeconds));

  return isActivePhoto(photo) && eventMatch && zoneMatch && dateMatch && timeMatch;
}

function getFilteredPhotos() {
  if (!state.searchStarted) return [];

  const filters = getFilters();

  return photos
    .filter((photo) => {
      const recognitionMatch =
        !hasRecognitionSignal(filters) || getRecognitionReasons(photo, filters).length > 0;

      return isContextMatch(photo, filters) && recognitionMatch;
    })
    .sort((a, b) => {
      const timeOrder = sortByCaptureTime(a, b);
      return state.photoSort === "asc" ? timeOrder : -timeOrder;
    });
}

function getPackKeys(photo) {
  return [
    ...photo.bibs,
    ...photo.manualPacks,
    ...numbersFromText(photo.qrRaw),
    photo.qrRaw,
  ]
    .map((key) => String(key || "").trim().toLowerCase())
    .filter(Boolean);
}

function makeThumbnail(photo, compact = false) {
  const image = photo.imageData || demoImages[photo.id] || "";
  if (image) {
    return `<img src="${escapeHtml(image)}" alt="${escapeHtml(photo.id)}" loading="lazy" />`;
  }

  const [bg, accent, dark] = photo.palette;
  const bib = escapeHtml(photo.bibs[0]);
  const bibTwo = escapeHtml(photo.bibs[1] || photo.bibs[0]);
  const horizon = compact ? 74 : 88;
  const crowd = Array.from({ length: 8 }, (_, index) => {
    const x = 18 + index * 34;
    const y = 42 + (index % 3) * 7;
    return `<circle cx="${x}" cy="${y}" r="8" fill="${index % 2 ? accent : dark}" opacity=".42" />`;
  }).join("");

  const runner = `
    <g transform="translate(132 84)">
      <circle cx="0" cy="-34" r="15" fill="#f2b98c"/>
      <path d="M-13 -16 Q0 -28 15 -16 L20 34 L-20 34 Z" fill="${dark}"/>
      <rect x="-14" y="-8" width="28" height="18" rx="3" fill="#fff"/>
      <text x="0" y="5" text-anchor="middle" font-family="Arial" font-size="11" font-weight="700" fill="${dark}">${bib}</text>
      <path d="M-15 4 L-54 32" stroke="${dark}" stroke-width="10" stroke-linecap="round"/>
      <path d="M14 5 L52 28" stroke="${dark}" stroke-width="10" stroke-linecap="round"/>
      <path d="M-8 34 L-38 82" stroke="${accent}" stroke-width="11" stroke-linecap="round"/>
      <path d="M13 33 L50 73" stroke="${accent}" stroke-width="11" stroke-linecap="round"/>
    </g>`;

  const cyclist = `
    <g transform="translate(116 102)">
      <circle cx="-50" cy="54" r="33" fill="none" stroke="${dark}" stroke-width="8"/>
      <circle cx="58" cy="54" r="33" fill="none" stroke="${dark}" stroke-width="8"/>
      <path d="M-50 54 L-12 12 L18 54 L58 54 L18 54 L-8 54 L-50 54" fill="none" stroke="${accent}" stroke-width="8" stroke-linejoin="round"/>
      <circle cx="-5" cy="-18" r="14" fill="#f2b98c"/>
      <path d="M-10 -3 L-38 28 L6 24 L22 0 Z" fill="${dark}"/>
      <rect x="-20" y="8" width="31" height="17" rx="3" fill="#fff"/>
      <text x="-4" y="21" text-anchor="middle" font-family="Arial" font-size="10" font-weight="700" fill="${dark}">${bib}</text>
    </g>`;

  const finish = `
    <path d="M28 32 H304" stroke="#fff" stroke-width="8" opacity=".85"/>
    <path d="M28 52 H304" stroke="${dark}" stroke-width="5" opacity=".55"/>
    ${runner}
    <g transform="translate(215 82)">
      <circle cx="0" cy="-26" r="13" fill="#e7ad84"/>
      <path d="M-16 -10 H18 L22 36 H-22 Z" fill="${accent}"/>
      <rect x="-14" y="0" width="28" height="16" rx="3" fill="#fff"/>
      <text x="0" y="12" text-anchor="middle" font-family="Arial" font-size="10" font-weight="700" fill="${dark}">${bibTwo}</text>
    </g>`;

  const expo = `
    <rect x="22" y="28" width="116" height="82" rx="6" fill="#fff" opacity=".7"/>
    <rect x="156" y="36" width="136" height="60" rx="6" fill="${accent}" opacity=".75"/>
    <g transform="translate(98 116)">
      <circle cx="0" cy="-32" r="17" fill="#f0b48a"/>
      <path d="M-22 -12 H22 L28 48 H-28 Z" fill="${dark}"/>
      <rect x="-17" y="1" width="34" height="18" rx="3" fill="#fff"/>
      <text x="0" y="14" text-anchor="middle" font-family="Arial" font-size="11" font-weight="700" fill="${dark}">${bib}</text>
    </g>
    <g transform="translate(210 118)">
      <circle cx="0" cy="-30" r="15" fill="#d99e77"/>
      <path d="M-20 -10 H20 L25 46 H-25 Z" fill="${dark}" opacity=".82"/>
    </g>`;

  const wide = `
    <path d="M0 ${horizon + 46} C72 ${horizon + 8} 126 ${horizon + 66} 210 ${horizon + 28} S310 ${horizon + 48} 340 ${horizon + 18} V240 H0 Z" fill="${accent}" opacity=".72"/>
    <g transform="translate(118 118) scale(.72)">${runner}</g>
    <g transform="translate(214 120) scale(.55)">${runner}</g>`;

  const portrait = `
    <ellipse cx="166" cy="118" rx="86" ry="96" fill="#ffffff" opacity=".24"/>
    <g transform="translate(165 116) scale(1.25)">
      <circle cx="0" cy="-34" r="17" fill="#f1b78c"/>
      <path d="M-28 -13 H28 L35 54 H-35 Z" fill="${dark}"/>
      <rect x="-20" y="4" width="40" height="21" rx="3" fill="#fff"/>
      <text x="0" y="19" text-anchor="middle" font-family="Arial" font-size="13" font-weight="700" fill="${dark}">${bib}</text>
    </g>`;

  const sceneMap = {
    start: `${crowd}${runner}`,
    run: `${crowd}${runner}`,
    finish,
    cycle: `${crowd}${cyclist}`,
    wide,
    expo,
    portrait,
  };

  return `
    <svg viewBox="0 0 330 240" role="img" aria-label="${photo.event}, ${zoneLabel(photo.zone)}">
      <defs>
        <linearGradient id="g-${photo.id}" x1="0" x2="1" y1="0" y2="1">
          <stop offset="0" stop-color="${bg}" />
          <stop offset=".58" stop-color="#f7f1e7" />
          <stop offset="1" stop-color="${accent}" stop-opacity=".82" />
        </linearGradient>
      </defs>
      <rect width="330" height="240" fill="url(#g-${photo.id})"/>
      <circle cx="292" cy="38" r="42" fill="${accent}" opacity=".4"/>
      <rect x="0" y="${horizon}" width="330" height="${240 - horizon}" fill="${dark}" opacity=".13"/>
      ${sceneMap[photo.scene] || runner}
    </svg>`;
}

function sortByCaptureTime(a, b) {
  return a.date.localeCompare(b.date) || timeToSeconds(a.time) - timeToSeconds(b.time);
}

function getAnchorCandidate(contextPhotos, filters) {
  return contextPhotos
    .map((photo) => ({
      photo,
      reasons: getRecognitionReasons(photo, filters),
    }))
    .filter((item) => item.reasons.length > 0)
    .sort((a, b) => {
      const signalScore = b.reasons.length - a.reasons.length;
      return signalScore || b.photo.match - a.photo.match || sortByCaptureTime(a.photo, b.photo);
    })[0];
}

function renderPossibleMatches() {
  if (!state.searchStarted) {
    possibleMatchBlock.hidden = true;
    possibleMatchList.innerHTML = "";
    return;
  }

  const filters = getFilters();
  const contextPhotos = photos.filter((photo) => isContextMatch(photo, filters));
  const anchor = hasRecognitionSignal(filters) ? getAnchorCandidate(contextPhotos, filters) : null;

  if (!anchor) {
    possibleMatchBlock.hidden = true;
    possibleMatchList.innerHTML = "";
    return;
  }

  const anchorSeconds = timeToSeconds(anchor.photo.time);
  if (anchorSeconds === null) {
    possibleMatchBlock.hidden = true;
    possibleMatchList.innerHTML = "";
    return;
  }

  const rangeStart = Math.max(0, anchorSeconds - 30);
  const rangeEnd = Math.min(86399, anchorSeconds + 30);
  const anchorPackKeys = getPackKeys(anchor.photo);
  const nearby = contextPhotos
    .filter((photo) => {
      const recognized = getRecognitionReasons(photo, filters).length > 0;
      const photoSeconds = timeToSeconds(photo.time);
      const timeMatch =
        photo.event === anchor.photo.event &&
        photo.date === anchor.photo.date &&
        photoSeconds !== null &&
        photoSeconds >= rangeStart &&
        photoSeconds <= rangeEnd;
      const manualPackMatch =
        photo.manualPacks.length > 0 &&
        getPackKeys(photo).some((key) => anchorPackKeys.includes(key));

      return photo.id !== anchor.photo.id && !recognized && (timeMatch || manualPackMatch);
    })
    .sort(sortByCaptureTime)
    .slice(0, 8);

  if (!nearby.length) {
    possibleMatchBlock.hidden = true;
    possibleMatchList.innerHTML = "";
    return;
  }

  const hasManualPack = nearby.some((photo) => photo.manualPacks.length > 0);
  $("#possibleMatchMeta").textContent = `${anchor.photo.event} · ${secondsToTime(rangeStart)} - ${secondsToTime(
    rangeEnd,
  )}${hasManualPack ? " · плюс добавленные вручную" : ""} · сигнал: ${anchor.reasons.join(", ")}`;
  $("#possibleMatchCount").textContent = nearby.length;
  possibleMatchList.innerHTML = nearby
    .map((photo) => {
      const selected = state.selected.has(photo.id);
      const reasons = getRecognitionReasons(photo, filters);
      const selectedLabel = "В корзине";
      const delta = timeToSeconds(photo.time) - anchorSeconds;
      const deltaLabel = delta === 0 ? "точное время" : `${delta > 0 ? "+" : ""}${delta} сек`;
      const manualPack = photo.manualPacks.some((pack) => anchorPackKeys.includes(pack.toLowerCase()));
      const reasonLabel = reasons.join(", ") || (manualPack ? "добавлено вручную" : "рядом по времени");

      return `
        <article class="possible-card">
          <div class="possible-thumb">${makeThumbnail(photo, true)}</div>
          <div class="possible-main">
            <strong>${escapeHtml(photo.id)}</strong>
            <small>${escapeHtml(photo.time)} · ${escapeHtml(photoLocationLabel(photo))} · ${escapeHtml(deltaLabel)} · № ${escapeHtml(photo.bibs.join(", ") || "нет")}</small>
            <span>${escapeHtml(reasonLabel)}</span>
          </div>
          <button class="select-button ${selected ? "selected" : ""}" type="button" data-id="${escapeHtml(photo.id)}" data-pack-eligible="false">
            ${selected ? selectedLabel : "Докупить"}
          </button>
        </article>`;
    })
    .join("");

  possibleMatchBlock.hidden = false;
}

function renderOtherEventMatches() {
  const filters = getFilters();
  if (!state.searchStarted || !hasRecognitionSignal(filters)) {
    otherEventsBlock.hidden = true;
    otherEventsList.innerHTML = "";
    return;
  }

  const matches = photos
    .filter((photo) => photo.event !== state.event && isActivePhoto(photo))
    .map((photo) => ({ photo, reasons: getRecognitionReasons(photo, filters) }))
    .filter((item) => item.reasons.length > 0)
    .sort((a, b) => b.photo.match - a.photo.match || sortByCaptureTime(a.photo, b.photo))
    .slice(0, 6);

  if (!matches.length) {
    otherEventsBlock.hidden = true;
    otherEventsList.innerHTML = "";
    return;
  }

  $("#otherEventsCount").textContent = matches.length;
  otherEventsList.innerHTML = matches
    .map(
      ({ photo, reasons }) => `
        <article class="possible-card">
          <div class="possible-thumb">${makeThumbnail(photo, true)}</div>
          <div class="possible-main">
            <strong>${escapeHtml(photo.event)}</strong>
            <small>${escapeHtml(photo.date)} ${escapeHtml(photo.time)} · ${escapeHtml(photoLocationLabel(photo))} · № ${escapeHtml(photo.bibs.join(", ") || "нет")}</small>
            <span>${escapeHtml(reasons.join(", "))}</span>
          </div>
          <button class="select-button" type="button" data-jump-event="${escapeHtml(photo.event)}">
            Открыть событие
          </button>
        </article>`,
    )
    .join("");
  otherEventsBlock.hidden = false;
}

function renderPackOffer(visiblePhotos) {
  const offer = getPackOffer(visiblePhotos);

  if (!offer) {
    packOffer.hidden = true;
    packOffer.removeAttribute("data-event");
    return;
  }

  $("#packOfferTitle").textContent = `Купить фотопак за ${money(offer.packPrice)}`;
  $("#packOfferText").textContent = `${offer.eventName}: ${offer.photos.length} фото за ${money(
    offer.packPrice,
  )}${offer.saving > 0 ? ` вместо ${money(offer.subtotal)}` : ""} · только распознанные`;
  $("#packOfferButton").textContent = `Выбрать все ${offer.photos.length}`;
  packOffer.dataset.event = offer.eventName;
  packOffer.hidden = false;
}

function renderGallery() {
  const allVisible = getFilteredPhotos();
  const visible = allVisible.slice(0, state.photoPageSize);

  updateWideSearchControl();
  gallery.classList.toggle("dense", state.dense);
  gallery.innerHTML = visible
    .map((photo) => {
      const selected = state.selected.has(photo.id);
      return `
        <article class="photo-card">
          <div class="photo-thumb">
            ${makeThumbnail(photo, state.dense)}
            ${
              state.faceLoaded
                ? `<span class="match-badge">${photo.match}%</span>`
                : ""
            }
            <span class="zone-badge">${escapeHtml(photoLocationLabel(photo))}</span>
          </div>
          <div class="photo-info">
            <div class="photo-title">
              <div>
                <strong>${photo.id}</strong>
                <small>${escapeHtml(photo.event)} · ${escapeHtml(photoLocationLabel(photo))}</small>
              </div>
              <strong>${photo.time}</strong>
            </div>
            <div class="photo-meta">
              <span>${prettyDate(photo.date)}</span>
              <span>${escapeHtml(photoLocationLabel(photo))}</span>
              <span>${photo.time}</span>
              <span>№ ${photo.bibs.join(", ")}</span>
              <span>${
                photo.manualPacks.length
                  ? "добавлено вручную"
                  : photo.bibs.length
                    ? "номер"
                    : photo.faces
                      ? "лицо"
                      : "общий план"
              }</span>
            </div>
            <div class="photo-actions">
              <button class="select-button ${selected ? "selected" : ""}" type="button" data-id="${photo.id}" data-pack-eligible="false">
                ${selected ? "В корзине" : "В корзину"}
              </button>
              <button class="time-search-button" type="button" data-time-search="${escapeHtml(photo.id)}">
                Попробовать поискать внимательнее
              </button>
              <span class="price">${money(getPhotoPrice(photo))}</span>
            </div>
          </div>
        </article>`;
    })
    .join("");

  emptyState.hidden = !state.searchStarted || allVisible.length > 0;
  photoLoadMore.hidden = !state.searchStarted || visible.length >= allVisible.length;
  renderPackOffer(allVisible);
  renderPossibleMatches();
  renderOtherEventMatches();
  updateSummary(allVisible, visible.length);
  updateSelection();
  refreshIcons();
}

function updateSummary(visible, shownCount = visible.length) {
  if (!state.searchStarted) {
    $("#resultSummary").textContent = "Готово к поиску";
    $("#metricPhotos").textContent = "0";
    $("#metricFaces").textContent = "0";
    $("#metricBibs").textContent = "0";
    return;
  }

  const uniqueBibs = new Set(visible.flatMap((photo) => photo.bibs));
  const faceCount = visible.filter((photo) => photo.faces).length;
  const filters = getFilters();
  const parts = [`${visible.length} фото`];

  if (shownCount < visible.length) parts.push(`показано ${shownCount}`);
  if (filters.bib) parts.push(`ключ ${filters.bib}`);
  if (state.faceLoaded) parts.push(`лицо от ${filters.min}%`);
  if (filters.event) parts.push(filters.event);
  if (filters.date) parts.push(filters.date);
  if (filters.timeFrom || filters.timeTo) {
    parts.push(`${filters.timeFrom || "00:00:00"} - ${filters.timeTo || "23:59:59"}`);
  }

  $("#resultSummary").textContent = parts.join(" · ");
  $("#metricPhotos").textContent = visible.length;
  $("#metricFaces").textContent = faceCount;
  $("#metricBibs").textContent = uniqueBibs.size;
}

function eventSelectionSummary(eventPricing) {
  const parts = [`${eventPricing.photoCount} фото`];
  if (eventPricing.subtotal > eventPricing.total) parts.push(`вместо ${money(eventPricing.subtotal)}`);
  return parts.join(" · ");
}

function renderSelectedPhotoRows(eventPhotos, pricing) {
  return eventPhotos
    .map(
      (photo) => `
        <div class="selected-row">
          <div class="selected-mini">${makeThumbnail(photo, true)}</div>
          <div>
            <strong>${escapeHtml(photo.id)}</strong>
            <span>${pricing.packPhotoIds.has(photo.id) ? "в фотопаке" : "отдельно"} · ${escapeHtml(photoLocationLabel(photo))} · № ${escapeHtml(photo.bibs.join(", ") || "нет")}</span>
          </div>
          <button class="remove-selected" type="button" title="Убрать" data-remove="${escapeHtml(photo.id)}">
            <i data-lucide="x"></i>
          </button>
        </div>`,
    )
    .join("");
}

function renderSelectedCartSection(title, count, total, eventPhotos, note, pricing) {
  if (!count) return "";

  return `
    <div class="selected-cart-section">
      <div class="selected-section-head">
        <span>
          <strong>${title}</strong>
          <small>${count} шт.${note ? ` · ${note}` : ""}</small>
        </span>
        <em>${money(total)}</em>
      </div>
      ${renderSelectedPhotoRows(eventPhotos, pricing)}
    </div>`;
}

function updateSelection() {
  const selectedPhotos = photos.filter((photo) => state.selected.has(photo.id));
  const pricing = getCheckoutPricing(selectedPhotos);
  const pricingNote = pricing.subtotal > pricing.total ? `${pricing.note} · вместо ${money(pricing.subtotal)}` : pricing.note;
  const photosByEvent = new Map();

  selectedPhotos.forEach((photo) => {
    if (!photosByEvent.has(photo.event)) photosByEvent.set(photo.event, []);
    photosByEvent.get(photo.event).push(photo);
  });

  $("#selectionCount").textContent = selectedPhotos.length;
  $("#orderTotal").textContent = money(pricing.finalTotal);
  $("#orderPricingNote").textContent =
    pricing.discount > 0
      ? `${pricingNote} · промокод ${pricing.promoLabel} −${money(pricing.discount)}`
      : pricingNote;
  $("#checkoutButton").disabled = selectedPhotos.length === 0;

  if (!selectedPhotos.length) {
    $("#selectedPreview").innerHTML = `
      <div class="selected-empty">
        <i data-lucide="images"></i>
        <span>Фото появятся здесь</span>
      </div>`;
    return;
  }

  $("#selectedPreview").innerHTML = pricing.eventBreakdowns
    .map((eventPricing) => {
      const eventPhotos = photosByEvent.get(eventPricing.eventName) || [];
      const packPhotos = eventPhotos.filter((photo) => pricing.packPhotoIds.has(photo.id));
      const singlePhotos = eventPhotos.filter((photo) => pricing.singlePhotoIds.has(photo.id));
      const singleNote = eventPricing.singleDiscountRate ? `скидка ${eventPricing.singleDiscountRate}%` : "";
      return `
        <section class="selected-event-group">
          <div class="selected-event-head">
            <span>
              <strong>${escapeHtml(eventPricing.eventName)}</strong>
              <small>${escapeHtml(eventSelectionSummary(eventPricing))}</small>
            </span>
            <em>${money(eventPricing.total)}</em>
          </div>
          ${renderSelectedCartSection("Фотопак", eventPricing.packCount, eventPricing.packTotal, packPhotos, "", pricing)}
          ${renderSelectedCartSection("Штучные", eventPricing.singleCount, eventPricing.singleTotal, singlePhotos, singleNote, pricing)}
        </section>`;
    })
    .join("");
}

function clearFaceSearch() {
  state.faceLoaded = false;
  faceFile.value = "";
  facePreview.removeAttribute("src");
  uploadBox.classList.remove("has-image");
  faceStatus.textContent = "Загрузить фото";
  faceName.textContent = "JPG, PNG, HEIC";
}

function syncEventControls(value) {
  state.event = value;
  eventSelect.value = value;
  $$(".event-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.event === value);
  });
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showStoreToast(message) {
  const toast = $("#storeToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showStoreToast.timer);
  showStoreToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function initCookieBanner() {
  const accepted = localStorage.getItem("picflow.cookiesAccepted") === "true";
  $("#cookieBanner").hidden = accepted;
  $("#acceptCookiesButton").addEventListener("click", () => {
    localStorage.setItem("picflow.cookiesAccepted", "true");
    $("#cookieBanner").hidden = true;
  });
}

function createOrder() {
  const selectedPhotos = photos.filter((photo) => state.selected.has(photo.id));
  if (!selectedPhotos.length) return;

  const emailInput = $("#customerEmail");
  const email = emailInput.value.trim();
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  if (!emailValid) {
    emailInput.focus();
    emailInput.classList.add("input-error");
    showStoreToast("Укажите email для получения фото");
    return;
  }
  emailInput.classList.remove("input-error");

  const orders = loadOrders();
  const pricing = getCheckoutPricing(selectedPhotos);
  const orderNumber = String(orders.length + 1).padStart(3, "0");
  const order = {
    id: `ORD-${Date.now().toString().slice(-6)}-${orderNumber}`,
    createdAt: new Date().toISOString(),
    customer: email,
    email,
    status: "new",
    pricingMode: pricing.mode,
    subtotal: pricing.subtotal,
    packTotal: pricing.packTotal,
    singleSubtotal: pricing.singleSubtotal,
    singleDiscount: pricing.singleDiscount,
    autoDiscount: pricing.autoDiscount,
    discount: pricing.discount,
    promoCode: pricing.promo?.code || "",
    eventBreakdowns: pricing.eventBreakdowns.map((eventPricing) => ({
      eventName: eventPricing.eventName,
      photoCount: eventPricing.photoCount,
      packCount: eventPricing.packCount,
      singleCount: eventPricing.singleCount,
      subtotal: eventPricing.subtotal,
      total: eventPricing.total,
      packTotal: eventPricing.packTotal,
      singleTotal: eventPricing.singleTotal,
      singleDiscount: eventPricing.singleDiscount,
      singleDiscountRate: eventPricing.singleDiscountRate,
    })),
    items: selectedPhotos.map((photo) => ({
      photoId: photo.id,
      event: photo.event,
      price: getPhotoPrice(photo),
      purchaseType: pricing.packPhotoIds.has(photo.id) ? "pack" : "single",
    })),
    total: pricing.finalTotal,
  };

  if (pricing.promo) {
    const promos = loadPromos();
    const promoIndex = promos.findIndex((promo) => promo.code === pricing.promo.code);
    if (promoIndex >= 0) {
      promos[promoIndex].used += 1;
      savePromos(promos);
    }
    state.appliedPromo = null;
    $("#promoCode").value = "";
    $("#promoStatus").textContent = "Промокод не применён";
  }

  saveOrders([order, ...orders]);
  state.selected.clear();
  state.selectionMeta.clear();
  emailInput.value = "";
  renderGallery();
  showStoreToast(`Заказ ${order.id} создан на ${money(pricing.finalTotal)}`);
}

function expandTimeSearch() {
  const filters = getFilters();
  const visible = getFilteredPhotos();
  let fromSeconds = filters.timeFrom ? timeToSeconds(filters.timeFrom) : null;
  let toSeconds = filters.timeTo ? timeToSeconds(filters.timeTo) : null;

  if ((fromSeconds === null || toSeconds === null) && visible.length) {
    const seconds = visible.map((photo) => timeToSeconds(photo.time)).filter((value) => value !== null);
    fromSeconds = Math.min(...seconds);
    toSeconds = Math.max(...seconds);
  }

  if (fromSeconds === null || toSeconds === null) {
    showStoreToast("Сначала задайте время или выполните поиск");
    return;
  }

  state.searchStarted = true;
  state.photoPageSize = PHOTO_PAGE_STEP;
  timeFrom.value = secondsToTime(fromSeconds - 30);
  timeTo.value = secondsToTime(toSeconds + 30);
  renderGallery();
  showStoreToast(`Диапазон расширен: ${timeFrom.value} - ${timeTo.value}`);
}

function applyPromo() {
  const code = $("#promoCode").value.trim().toUpperCase();
  const selectedPhotos = photos.filter((photo) => state.selected.has(photo.id));
  const promo = loadPromos().find((item) => item.code === code);

  if (!code) {
    state.appliedPromo = null;
    $("#promoStatus").textContent = "Промокод не применён";
    updateSelection();
    return;
  }

  if (!promo || !promoAppliesToSelection(promo, selectedPhotos)) {
    state.appliedPromo = null;
    $("#promoStatus").textContent = "Промокод не подходит";
    updateSelection();
    return;
  }

  state.appliedPromo = promo;
  $("#promoStatus").textContent = `Применён: ${promo.name}`;
  updateSelection();
}

function searchAroundPhoto(photoId) {
  const photo = photos.find((item) => item.id === photoId);
  const photoSeconds = photo ? timeToSeconds(photo.time) : null;
  if (!photo || photoSeconds === null) return;

  state.wideSearchSnapshot = getSearchSnapshot();
  state.wideSearchActive = true;
  if (photo.event !== state.event) {
    openEvent(photo.event);
  }

  state.searchStarted = true;
  state.photoPageSize = PHOTO_PAGE_STEP;
  bibInput.value = "";
  dateFrom.value = photo.date;
  timeFrom.value = secondsToTime(photoSeconds - 30);
  timeTo.value = secondsToTime(photoSeconds + 30);
  state.zone = "all";
  clearFaceSearch();
  syncEventControls(photo.event);
  $$(".chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.zone === "all");
  });
  renderGallery();
  showStoreToast(`Показан интервал ${timeFrom.value} - ${timeTo.value}`);
}

function resetWideSearch() {
  applySearchSnapshot(state.wideSearchSnapshot);
  state.wideSearchActive = false;
  state.wideSearchSnapshot = null;
  state.photoPageSize = PHOTO_PAGE_STEP;
  renderGallery();
  showStoreToast("Поиск внимательнее сброшен");
}

function showAllEventPhotos() {
  state.wideSearchActive = false;
  state.wideSearchSnapshot = null;
  state.searchStarted = true;
  state.photoPageSize = PHOTO_PAGE_STEP;
  state.zone = "all";
  bibInput.value = "";
  dateFrom.value = "";
  timeFrom.value = "";
  timeTo.value = "";
  clearFaceSearch();
  $$("#zoneFilters .chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.zone === "all");
  });
  renderGallery();
  showStoreToast("Показаны все фотографии мероприятия");
}

function resetFilters() {
  const event = events.find((item) => item.name === state.event);
  bibInput.value = "";
  dateFrom.value = event?.dateFrom || "";
  timeFrom.value = "";
  timeTo.value = "";
  minMatch.value = "70";
  minMatchValue.textContent = "70%";
  state.zone = "all";
  state.searchStarted = false;
  state.photoPageSize = PHOTO_PAGE_STEP;
  state.wideSearchActive = false;
  state.wideSearchSnapshot = null;
  clearFaceSearch();
  syncEventControls(state.event);
  $$(".chip").forEach((chip) => {
    chip.classList.toggle("active", chip.dataset.zone === "all");
  });
  renderGallery();
}

function markSearchChanged() {
  state.searchStarted = false;
  state.photoPageSize = PHOTO_PAGE_STEP;
  state.wideSearchActive = false;
  state.wideSearchSnapshot = null;
}

function applySearch() {
  state.searchStarted = true;
  state.photoPageSize = PHOTO_PAGE_STEP;
  state.wideSearchActive = false;
  state.wideSearchSnapshot = null;
}

function bindEvents() {
  eventGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-event]");
    if (!button) return;
    openEvent(button.dataset.openEvent);
  });

  eventHomeSearch.addEventListener("input", () => {
    renderEventHome();
  });

  eventHomeSort.addEventListener("change", () => {
    state.eventSort = eventHomeSort.value;
    renderEventHome();
  });

  document.querySelector(".event-tabs").addEventListener("click", (event) => {
    const homeButton = event.target.closest("[data-home-events]");
    if (homeButton) {
      showEventHome();
      return;
    }

    const tab = event.target.closest("[data-event]");
    if (!tab) return;
    openEvent(tab.dataset.event);
  });

  [bibInput, dateFrom, timeFrom, timeTo].forEach((control) => {
    control.addEventListener("input", () => {
      markSearchChanged();
      renderGallery();
    });
  });

  minMatch.addEventListener("input", () => {
    markSearchChanged();
    minMatchValue.textContent = `${minMatch.value}%`;
    renderGallery();
  });

  eventSelect.addEventListener("change", (event) => {
    openEvent(event.target.value);
  });

  $("#zoneFilters").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-zone]");
    if (!chip) return;
    markSearchChanged();
    state.zone = chip.dataset.zone;
    $$("#zoneFilters .chip").forEach((item) => {
      item.classList.toggle("active", item === chip);
    });
    renderGallery();
  });

  faceFile.addEventListener("change", () => {
    const file = faceFile.files?.[0];
    if (!file) return;

    state.faceLoaded = true;
    markSearchChanged();
    faceStatus.textContent = "Лицо загружено";
    faceName.textContent = file.name;
    uploadBox.classList.add("has-image");

    const reader = new FileReader();
    reader.addEventListener("load", () => {
      facePreview.src = reader.result;
    });
    reader.readAsDataURL(file);
    renderGallery();
  });

  $("#resetFilters").addEventListener("click", resetFilters);
  $("#backToEvents").addEventListener("click", showEventHome);
  $("#applySearch").addEventListener("click", () => {
    applySearch();
    renderGallery();
  });
  $("#showAllEventPhotos").addEventListener("click", showAllEventPhotos);
  $("#resetWideSearchButton").addEventListener("click", resetWideSearch);

  photoLoadMore.addEventListener("click", () => {
    state.photoPageSize += PHOTO_PAGE_STEP;
    renderGallery();
  });

  $("#gridView").addEventListener("click", () => {
    state.dense = false;
    $("#gridView").classList.add("active");
    $("#denseView").classList.remove("active");
    renderGallery();
  });

  $("#denseView").addEventListener("click", () => {
    state.dense = true;
    $("#denseView").classList.add("active");
    $("#gridView").classList.remove("active");
    renderGallery();
  });

  $("#sortToggle").addEventListener("click", () => {
    state.photoSort = state.photoSort === "asc" ? "desc" : "asc";
    $("#sortToggle").lastChild.textContent = state.photoSort === "asc" ? " Ранние" : " Поздние";
    renderGallery();
  });

  gallery.addEventListener("click", (event) => {
    const timeButton = event.target.closest("[data-time-search]");
    if (timeButton) {
      searchAroundPhoto(timeButton.dataset.timeSearch);
      return;
    }

    const button = event.target.closest("[data-id]");
    if (!button) return;

    const { id } = button.dataset;
    const photo = photos.find((item) => item.id === id);
    if (!photo) return;
    const added = setPhotoSelected(photo, button.dataset.packEligible === "true");
    renderGallery();
    if (added && button.dataset.packEligible !== "true") {
      showStoreToast("Фото добавлено как отдельная покупка");
    }
  });

  $("#selectedPreview").addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;

    state.selected.delete(button.dataset.remove);
    state.selectionMeta.delete(button.dataset.remove);
    renderGallery();
  });

  possibleMatchList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-id]");
    if (!button) return;

    const { id } = button.dataset;
    const photo = photos.find((item) => item.id === id);
    if (!photo) return;
    const added = setPhotoSelected(photo, button.dataset.packEligible === "true");
    renderGallery();
    if (added && button.dataset.packEligible !== "true") {
      showStoreToast("Фото добавлено как отдельная покупка");
    }
  });

  otherEventsList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-jump-event]");
    if (!button) return;
    openEvent(button.dataset.jumpEvent);
  });

  $("#applyPromoButton").addEventListener("click", applyPromo);
  $("#promoCode").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyPromo();
    }
  });

  $("#packOfferButton").addEventListener("click", () => {
    const offer = getPackOffer(getFilteredPhotos());
    if (!offer) return;

    offer.photos.forEach((photo) => {
      state.selected.add(photo.id);
      state.selectionMeta.set(photo.id, { packEligible: true });
    });
    renderGallery();
    showStoreToast(`Фотопак добавлен: ${offer.photos.length} фото`);
  });

  $("#checkoutButton").addEventListener("click", createOrder);
}

bindEvents();
renderEventControls();
initCookieBanner();
const initialEvent = new URLSearchParams(window.location.search).get("event");
if (initialEvent && events.some((event) => event.name === initialEvent)) {
  openEvent(initialEvent);
} else {
  showEventHome();
}
refreshIcons();
