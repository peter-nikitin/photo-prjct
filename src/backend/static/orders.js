const ORDER_STORAGE_KEY = window.PicFlowOrderStorageKey || "picflow.orders.v1";
const DEFAULT_ORDERS = window.PicFlowDefaultOrders || [];

const statusLabels = {
  new: "Новый",
  paid: "Оплачен",
  processing: "В работе",
  completed: "Готов",
  cancelled: "Отменён",
};

const $ = (selector) => document.querySelector(selector);

let orders = loadOrders();
let selectedEventName = "";
let selectedOrderId = "";

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

function prettyDate(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function normalizeOrder(order) {
  const items = Array.isArray(order.items) ? order.items : [];
  const total =
    Number(order.total) ||
    items.reduce((sum, item) => sum + (Number(item.price) || 0), 0);

  return {
    id: String(order.id || `ORD-${Date.now()}`).trim(),
    createdAt: order.createdAt || new Date().toISOString(),
    customer: String(order.customer || "Гость").trim(),
    email: String(order.email || "").trim(),
    status: statusLabels[order.status] ? order.status : "new",
    pricingMode: ["pack", "mixed"].includes(order.pricingMode) ? order.pricingMode : "single",
    subtotal: Math.max(0, Number(order.subtotal) || total),
    packTotal: Math.max(0, Number(order.packTotal) || 0),
    singleSubtotal: Math.max(0, Number(order.singleSubtotal) || 0),
    singleDiscount: Math.max(0, Number(order.singleDiscount) || 0),
    autoDiscount: Math.max(0, Number(order.autoDiscount) || 0),
    discount: Math.max(0, Number(order.discount) || 0),
    promoCode: String(order.promoCode || "").trim(),
    items: items.map((item) => ({
      photoId: String(item.photoId || "").trim(),
      event: String(item.event || "").trim(),
      price: Number(item.price) || 0,
      purchaseType: item.purchaseType === "pack" ? "pack" : "single",
    })),
    total,
  };
}

function loadOrders() {
  try {
    const saved = JSON.parse(localStorage.getItem(ORDER_STORAGE_KEY) || "null");
    if (Array.isArray(saved)) {
      return saved.map(normalizeOrder);
    }
  } catch {
    localStorage.removeItem(ORDER_STORAGE_KEY);
  }

  return DEFAULT_ORDERS.map(normalizeOrder);
}

function saveOrders() {
  localStorage.setItem(ORDER_STORAGE_KEY, JSON.stringify(orders, null, 2));
}

function refreshIcons() {
  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function showToast(message) {
  const toast = $("#ordersToast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2200);
}

function getFilteredOrders() {
  const query = $("#ordersSearch").value.trim().toLowerCase();
  const status = $("#ordersStatus").value;

  return orders.filter((order) => {
    const statusMatch = status === "all" || order.status === status;
    const eventMatch = !selectedEventName || order.items.some((item) => item.event === selectedEventName);
    const queryMatch =
      !query ||
      [
        order.id,
        order.customer,
        order.email,
        order.items.map((item) => `${item.photoId} ${item.event}`).join(" "),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query);

    return statusMatch && eventMatch && queryMatch;
  });
}

function getOrderEvents() {
  const query = $("#ordersSearch").value.trim().toLowerCase();
  const status = $("#ordersStatus").value;
  const byEvent = new Map();

  orders.forEach((order) => {
    if (status !== "all" && order.status !== status) return;
    const queryMatch =
      !query ||
      [order.id, order.customer, order.email, order.items.map((item) => `${item.photoId} ${item.event}`).join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(query);
    if (!queryMatch) return;

    [...new Set(order.items.map((item) => item.event || "Без события"))].forEach((eventName) => {
      if (!byEvent.has(eventName)) {
        byEvent.set(eventName, {
          name: eventName,
          orders: new Set(),
          photos: 0,
          total: 0,
        });
      }

      const bucket = byEvent.get(eventName);
      bucket.orders.add(order.id);
      const eventItems = order.items.filter((item) => (item.event || "Без события") === eventName);
      bucket.photos += eventItems.length;
      bucket.total += eventItems.reduce((sum, item) => sum + item.price, 0);
    });
  });

  return [...byEvent.values()].sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
}

function updateMetrics(visible) {
  const total = visible.reduce((sum, order) => sum + order.total, 0);
  const average = visible.length ? total / visible.length : 0;

  $("#ordersSummary").textContent = `${visible.length} из ${orders.length} заказов`;
  $("#ordersCount").textContent = visible.length;
  $("#ordersRevenue").textContent = money(total);
  $("#ordersAverage").textContent = money(average);
}

function updateEventGroupMetrics(eventGroups) {
  const orderCount = eventGroups.reduce((sum, eventGroup) => sum + eventGroup.orders.size, 0);
  const total = eventGroups.reduce((sum, eventGroup) => sum + eventGroup.total, 0);
  const average = orderCount ? total / orderCount : 0;

  $("#ordersSummary").textContent = `${orderCount} заказов в ${eventGroups.length} событиях`;
  $("#ordersCount").textContent = orderCount;
  $("#ordersRevenue").textContent = money(total);
  $("#ordersAverage").textContent = money(average);
}

function renderOrders() {
  if (!selectedEventName) {
    renderEventGroups();
    return;
  }

  renderEventOrders();
}

function renderEventGroups() {
  const visible = getOrderEvents();

  selectedOrderId = "";
  $("#ordersTitle").textContent = "Заказы по событиям";
  $("#ordersBackEvents").hidden = true;
  $("#ordersTable").innerHTML = visible.length
    ? visible.map(renderEventRow).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="calendar-x"></i><strong>Событий с заказами нет</strong></div>`;
  updateEventGroupMetrics(visible);
  renderEventGroupDetail();
  refreshIcons();
}

function renderEventGroupDetail() {
  $("#detailStatus").textContent = "-";
  $("#orderDetailBody").innerHTML = `
    <div class="selected-empty">
      <i data-lucide="calendar-check"></i>
      <span>Выберите событие</span>
    </div>`;
}

function renderEventOrders() {
  const visible = getFilteredOrders();

  if (selectedOrderId && !visible.some((order) => order.id === selectedOrderId)) {
    selectedOrderId = visible[0]?.id || "";
  }
  if (!selectedOrderId && visible.length) selectedOrderId = visible[0].id;

  $("#ordersTitle").textContent = selectedEventName;
  $("#ordersBackEvents").hidden = false;
  $("#ordersTable").innerHTML = visible.length
    ? visible.map(renderOrderRow).join("")
    : `<div class="empty-state admin-empty"><i data-lucide="receipt"></i><strong>В событии нет заказов</strong></div>`;

  updateMetrics(visible);
  renderDetail();
  refreshIcons();
}

function renderEventRow(eventGroup) {
  return `
    <button class="event-card" type="button" data-order-event="${escapeHtml(eventGroup.name)}">
      <span class="event-card-main">
        <strong>${escapeHtml(eventGroup.name)}</strong>
        <small>${eventGroup.orders.size} заказов · ${eventGroup.photos} фото</small>
        <em>Сумма по событию: ${money(eventGroup.total)}</em>
      </span>
      <span class="status-pill status-paid">${money(eventGroup.total)}</span>
    </button>`;
}

function renderOrderRow(order) {
  const active = order.id === selectedOrderId;
  const itemsLabel =
    order.pricingMode === "mixed"
      ? `${order.items.length} фото · пак + докупка`
      : order.pricingMode === "pack"
        ? `${order.items.length} фото · пак`
        : `${order.items.length} фото`;

  return `
    <button class="order-row ${active ? "active" : ""}" type="button" data-order="${escapeHtml(order.id)}">
      <span class="order-row-id">
        <strong>${escapeHtml(order.id)}</strong>
        <small>${prettyDate(order.createdAt)}</small>
      </span>
      <span class="order-row-customer">
        <strong>${escapeHtml(order.customer)}</strong>
        <small>${escapeHtml(order.email || "без email")}</small>
      </span>
      <span class="status-pill status-${escapeHtml(order.status)}">${statusLabels[order.status]}</span>
      <span class="order-row-items">${itemsLabel}</span>
      <span class="order-row-total">${money(order.total)}</span>
    </button>`;
}

function buildPurchasedLink(order, photoId = "") {
  const eventName = selectedEventName || order.items[0]?.event || "";
  const params = new URLSearchParams({
    order: order.id,
  });
  if (eventName) params.set("event", eventName);
  if (photoId) params.set("photo", photoId);
  return `/purchased/?${params.toString()}`;
}

function getOrderPricingLabel(order) {
  const base =
    order.pricingMode === "mixed"
      ? "Фотопак + докупка"
      : order.pricingMode === "pack"
        ? "Фотопак"
        : "Поштучно";

  if (order.autoDiscount > 0) {
    return `${base} · скидка ${money(order.autoDiscount)}`;
  }

  if (["pack", "mixed"].includes(order.pricingMode) && order.subtotal > order.total) {
    return `${base} · вместо ${money(order.subtotal)}`;
  }

  return base;
}

function renderDetail() {
  const order = orders.find((item) => item.id === selectedOrderId) || orders[0];

  if (!order) {
    $("#detailStatus").textContent = "-";
    $("#orderDetailBody").innerHTML = `
      <div class="selected-empty">
        <i data-lucide="receipt"></i>
        <span>Выберите заказ</span>
      </div>`;
    return;
  }

  selectedOrderId = order.id;
  $("#detailStatus").textContent = statusLabels[order.status];
  $("#orderDetailBody").innerHTML = `
    <div class="detail-head">
      <strong>${escapeHtml(order.id)}</strong>
      <span>${prettyDate(order.createdAt)}</span>
    </div>
    <a class="primary-button detail-action-link" href="${escapeHtml(buildPurchasedLink(order))}">
      <i data-lucide="image"></i>
      Открыть купленные фото
    </a>
    <div class="detail-lines">
      <span>Клиент</span>
      <strong>${escapeHtml(order.customer)}</strong>
      <span>Email</span>
      <strong>${escapeHtml(order.email || "не указан")}</strong>
      <span>Тариф</span>
      <strong>${getOrderPricingLabel(order)}</strong>
      <span>Промокод</span>
      <strong>${escapeHtml(order.promoCode || "не применён")}${order.discount ? ` · -${money(order.discount)}` : ""}</strong>
      <span>Сумма</span>
      <strong>${money(order.total)}</strong>
    </div>
    <label class="field">
      <span>Статус</span>
      <select id="detailStatusSelect">
        ${Object.entries(statusLabels)
          .map(
            ([value, label]) =>
              `<option value="${value}" ${order.status === value ? "selected" : ""}>${label}</option>`,
          )
          .join("")}
      </select>
    </label>
    <div class="order-items">
      ${order.items
        .map(
          (item) => `
            <div class="order-item">
              <span>
                <a class="order-link" href="${escapeHtml(buildPurchasedLink(order, item.photoId))}">${escapeHtml(item.photoId)}</a>
                <small>${escapeHtml(item.event)} · ${item.purchaseType === "pack" ? "фотопак" : "докупка"}</small>
              </span>
              <strong>${money(item.price)}</strong>
            </div>`,
        )
        .join("")}
    </div>
    <button class="danger-button" id="deleteOrderButton" type="button">
      <i data-lucide="trash-2"></i>
      Удалить заказ
    </button>`;

  $("#detailStatusSelect").addEventListener("change", (event) => {
    order.status = event.target.value;
    saveOrders();
    renderOrders();
    showToast("Статус обновлён");
  });

  $("#deleteOrderButton").addEventListener("click", () => {
    if (!window.confirm(`Удалить ${order.id}?`)) return;
    orders = orders.filter((item) => item.id !== order.id);
    selectedOrderId = "";
    saveOrders();
    renderOrders();
    showToast("Заказ удалён");
  });
}

function seedOrders() {
  if (!window.confirm("Вернуть демо-заказы?")) return;
  orders = DEFAULT_ORDERS.map(normalizeOrder);
  selectedEventName = "";
  selectedOrderId = "";
  saveOrders();
  renderOrders();
  showToast("Демо-заказы восстановлены");
}

function exportOrders() {
  const blob = new Blob([JSON.stringify(orders, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "picflow-orders.json";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast("Экспорт готов");
}

function applyInitialSelection() {
  const params = new URLSearchParams(window.location.search);
  const requestedOrderId = params.get("order") || "";
  const requestedEventName = params.get("event") || "";

  if (requestedOrderId) {
    const order = orders.find((item) => item.id === requestedOrderId);
    if (!order) return;

    selectedOrderId = order.id;
    selectedEventName =
      requestedEventName && order.items.some((item) => (item.event || "Без события") === requestedEventName)
        ? requestedEventName
        : order.items[0]?.event || "";
    return;
  }

  if (requestedEventName) {
    selectedEventName = requestedEventName;
  }
}

function bindEvents() {
  $("#ordersSearch").addEventListener("input", renderOrders);
  $("#ordersStatus").addEventListener("change", renderOrders);
  $("#seedOrdersButton").addEventListener("click", seedOrders);
  $("#exportOrdersButton").addEventListener("click", exportOrders);
  $("#ordersBackEvents").addEventListener("click", () => {
    selectedEventName = "";
    selectedOrderId = "";
    renderOrders();
  });

  $("#ordersTable").addEventListener("click", (event) => {
    const eventRow = event.target.closest("[data-order-event]");
    if (eventRow) {
      selectedEventName = eventRow.dataset.orderEvent;
      selectedOrderId = "";
      renderOrders();
      return;
    }

    const row = event.target.closest("[data-order]");
    if (!row) return;
    selectedOrderId = row.dataset.order;
    renderOrders();
  });
}

bindEvents();
applyInitialSelection();
renderOrders();
