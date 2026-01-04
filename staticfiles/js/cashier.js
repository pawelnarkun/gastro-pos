/**
 * static/js/cashier.js
 * Obsługa POS (Point of Sale) dla Kasjera
 */

const CashierConfig = {
    stationSlug: null,
    csrfToken: null,
    apiUrl: null
};

// Zmienne stanu
let selectedId = null;
let currentPaymentMethod = 'CARD';
let currentSelectedData = null;
let currentConfig = null;
let tempProductToAdd = null;

// Modale (inicjowane dynamicznie)
let addProductModal = null;
let customizeProductModal = null;
let splitModal = null;

// Zmienne do Split Bill
let currentOrderTotal = 0.0;
let splitPeopleStates = [];


// --- INICJALIZACJA ---
function initCashier(slug, csrfToken, initialSelectedId) {
    CashierConfig.stationSlug = slug;
    CashierConfig.csrfToken = csrfToken;
    CashierConfig.apiUrl = `/kasa/${slug}/api/`;
    
    if (initialSelectedId && initialSelectedId !== 'null') {
        selectedId = parseInt(initialSelectedId);
    }

    refreshOnce();
    connectCashierWebSocket();
}

function getCsrfToken() {
    return CashierConfig.csrfToken;
}

// --- CORE FUNCTIONS (API & RENDER) ---

async function refreshOnce() {
    const qInput = document.getElementById("qInput");
    const q = (qInput ? qInput.value : "").trim();
    
    const url = new URL(CashierConfig.apiUrl, window.location.origin);
    if (q) url.searchParams.set("q", q);
    if (selectedId) url.searchParams.set("sel", selectedId);

    try {
        const resp = await fetch(url.toString(), { headers: { "Accept": "application/json" } });
        if (!resp.ok) return;
        const data = await resp.json();

        if (selectedId && data.selected === null) {
            selectedId = null;
            // Usuń parametr z URL bez przeładowania
            const u = new URL(window.location.href);
            u.searchParams.delete("sel");
            history.replaceState({}, "", u.toString());
        }

        currentSelectedData = data.selected;
        currentConfig = data.config;

        renderOrdersList(data.orders || []);
        renderSelected(data.selected, data.config);
        
        // Aktualizacja URL
        const u = new URL(window.location.href);
        if (q) u.searchParams.set("q", q);
        if (selectedId) u.searchParams.set("sel", selectedId);
        history.replaceState({}, "", u.toString());

    } catch (e) { console.error(e); }
}

function selectOrder(id) {
    selectedId = id;
    refreshOnce();
}

// --- RENDEROWANIE HTML ---

function escapeHtml(s) {
    return (s ?? "").toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function bannerHtml(o) {
    const isPaid = (o.status !== "UNPAID");
    if (!isPaid) return `<span class="tag tag-unpaid">NIEOPŁACONE</span>`;
    if (o.is_ready) return `<span class="tag tag-ready">GOTOWE</span>`;
    if (o.is_in_progress) return `<span class="tag tag-progress">W TRAKCIE</span>`;
    return `<span class="tag tag-new">NOWE</span>`;
}

function renderOrdersList(orders) {
    const list = document.getElementById("ordersList");
    if (!list) return;
    if (!orders || orders.length === 0) {
        list.innerHTML = `<div class="list-group-item">Brak aktywnych zamówień</div>`;
        return;
    }
    list.innerHTML = orders.map(o => {
        const isSelected = selectedId && String(o.id) === String(selectedId);
return `
        <div class="list-group-item order-row ${isSelected ? "selected-row" : ""} py-2 px-2" onclick="selectOrder(${o.id})">
          
          <div class="d-flex justify-content-between align-items-center mb-1">
            <div class="d-flex align-items-center gap-2" style="overflow: hidden;">
                <span class="badge bg-dark text-warning border border-light" style="font-size: 1rem;">#${o.id}</span>
                <span class="fw-bold text-truncate text-dark" style="font-size: 1.1rem; line-height: 1; max-width: 140px;">
                    ${escapeHtml(o.customer_name)}
                </span>
            </div>
            <div class="text-muted small fw-bold ms-1" style="font-size: 0.8rem;">${escapeHtml(o.created_at_display)}</div>
          </div>

          <div class="d-flex align-items-center gap-1 flex-wrap" style="font-size: 0.85em;">
            ${bannerHtml(o)}
            
            ${o.is_takeaway 
                ? '<span class="badge bg-warning text-dark border border-dark py-1 px-2">🥡 WYNOS</span>' 
                : ''
            }
            
            <span class="badge bg-light text-secondary border py-1">
               ${o.ready_items}/${o.total_items}
            </span>
          </div>
        </div>`;
    }).join("");
}

function renderSelected(selected, config) {
    const panel = document.getElementById("selectedPanel");
    if (!panel) return;
    if (!selected) {
        panel.innerHTML = `<h3 class="p-3 text-muted">Wybierz zamówienie z listy po lewej.</h3>`;
        return;
    }

    const isPaid = (selected.status !== "UNPAID");
    const allowUnpaid = config ? config.allow_delivery_before_payment : true;
    const isDeliveryBlocked = !allowUnpaid && !isPaid;
    
    // Status realizacji
    let deliveryInfoHtml = '';
    if (selected.delivered_by_bar_str || selected.delivered_by_kitchen_str) {
        deliveryInfoHtml = `
            <div class="alert alert-secondary py-2 mb-3 shadow-sm">
                <small class="d-block text-uppercase fw-bold opacity-75 mb-1 border-bottom border-secondary pb-1">Status realizacji:</small>
                ${selected.delivered_by_bar_str ? `<div class="d-flex justify-content-between text-info fw-bold"><span>🍹 Bar:</span><span>${escapeHtml(selected.delivered_by_bar_str)}</span></div>` : ''}
                ${selected.delivered_by_kitchen_str ? `<div class="d-flex justify-content-between text-success fw-bold"><span>👨‍🍳 Kuchnia:</span><span>${escapeHtml(selected.delivered_by_kitchen_str)}</span></div>` : ''}
            </div>`;
    }

    // Przycisk "Na wynos"
    const takeawayBtnClass = selected.is_takeaway ? 'btn-warning border-dark fw-bold' : 'btn-outline-secondary';
    const takeawayText = selected.is_takeaway ? '🥡 NA WYNOS: TAK' : '🍽️ NA WYNOS: NIE (Na miejscu)';
    const takeawayBtn = !isPaid 
        ? `<button class="btn ${takeawayBtnClass} w-100 mb-3 py-2" onclick="toggleTakeaway()"><span class="fs-5">${takeawayText}</span></button>`
        : `<div class="alert ${selected.is_takeaway ? 'alert-warning' : 'alert-secondary'} text-center fw-bold mb-3"><span class="fs-5">${takeawayText}</span></div>`;

    // Przycisk "WYDAJ / DONE"
    let doneBtnHtml = '';
    const groups = selected.grouped_items || [];
    const cashierPending = groups.some(it => !it.is_fully_ready && it.can_cashier_deliver);
    const kitchenPending = groups.some(it => !it.is_fully_ready && !it.can_cashier_deliver);

    if (isDeliveryBlocked) {
        doneBtnHtml = `<button class="btn btn-secondary fw-bold w-100 h-100 border border-dark opacity-50" disabled>⛔ NAJPIERW OZNACZ "ZAPŁACONE"</button>`;
    } else if (cashierPending) {
        doneBtnHtml = `
            <form method="post" action="/kasa/${CashierConfig.stationSlug}/${selected.id}/done/" class="w-100 h-100">
                <input type="hidden" name="csrfmiddlewaretoken" value="${getCsrfToken()}">
                <button type="submit" class="btn btn-warning fw-bold w-100 h-100 border border-dark">WYDAJ BAR 🥤</button>
            </form>`;
    } else if (kitchenPending) {
        doneBtnHtml = `<button class="btn btn-secondary fw-bold w-100 h-100" disabled style="opacity: 0.7;">⏳ CZEKAM NA KUCHNIĘ...</button>`;
    } else {
        doneBtnHtml = `
            <form method="post" action="/kasa/${CashierConfig.stationSlug}/${selected.id}/done/" class="w-100 h-100">
                <input type="hidden" name="csrfmiddlewaretoken" value="${getCsrfToken()}">
                <button type="submit" class="btn btn-success fw-bold w-100 h-100 border border-dark">ZAKOŃCZ (DONE) ✅</button>
            </form>`;
    }

    // Lista pozycji
    const itemsHtml = groups.map(it => {
        const rawExtras = JSON.stringify(it.extras || []);
        const safeExtras = encodeURIComponent(rawExtras);
        const isPkgItem = (it.name.toLowerCase() === 'opakowanie');
        const canEdit = !isPaid;

        // --- NOWOŚĆ: Obliczanie statusu (kolory i ikonki) ---
        const readyCount = it.ready || 0;
        const totalCount = it.total || 0;
        const isAllReady = readyCount >= totalCount;
        // Jeśli gotowe - zielony, jeśli nie - czerwony/pomarańczowy
        const statusColor = isAllReady ? "text-success" : "text-danger"; 
        const statusIcon = isAllReady ? "✓" : "⏳";
        // -----------------------------------------------------

        let actionBtn = '';
        if (it.is_fully_ready) {
            actionBtn = `<span class="badge bg-success">✔ GOTOWE</span>`;
        } else if (isDeliveryBlocked) {
            actionBtn = `<div class="d-flex align-items-center justify-content-end" style="min-width: 90px;"><span class="badge bg-danger border border-danger">⛔ WYMAGANA<br>PŁATNOŚĆ</span></div>`;
        } else if (it.can_cashier_deliver) {
            const showAllBtn = (it.total - it.ready) > 1;
            actionBtn = `
                <div class="d-flex flex-column gap-1 align-items-end">
                    <button class="btn btn-sm btn-outline-danger fw-bold" style="min-width: 90px;" onclick="markItemDone(${it.next_id})">+1 (Wydaj)</button>
                    ${showAllBtn ? `<button class="btn btn-sm btn-success fw-bold" style="min-width: 90px;" onclick="markGroupDone(${it.next_id})">WSZYSTKIE</button>` : ''}
                </div>`;
        } else {
            actionBtn = `<div class="d-flex align-items-center justify-content-end" style="min-width: 90px;"><span class="badge bg-secondary text-white-50 border border-secondary">👨‍🍳 KUCHNIA</span></div>`;
        }

        let editControls = canEdit ? `
            <div class="btn-group btn-group-sm me-2">
                <button class="btn btn-outline-secondary fw-bold" onclick="editQty('${escapeHtml(it.name)}', 'dec', JSON.parse(decodeURIComponent('${safeExtras}')))">-</button>
                <button class="btn btn-outline-secondary fw-bold" onclick="editQty('${escapeHtml(it.name)}', 'inc', JSON.parse(decodeURIComponent('${safeExtras}')))">+</button>
            </div>` : '';

        return `
        <li class="d-flex justify-content-between align-items-center py-2 border-bottom">
            <div class="d-flex align-items-center">
            ${editControls}
            <div>
                <div class="fw-bold fs-5">
                    ${canEdit ? '' : `<span class="text-primary fw-bold me-1">${it.total}x</span>`}
                    ${escapeHtml(it.name)}
                    ${canEdit ? `<span class="badge bg-secondary ms-2">${it.total} szt.</span>` : ''}
                    ${(selected.is_takeaway && it.has_packaging_fee && !isPkgItem) ? '<span class="badge bg-warning text-dark border border-dark ms-2">📦 +OPAK.</span>' : ''}
                </div>
                
                ${(it.extras && it.extras.length) ? `<div class="text-success small fst-italic mt-1">+ ${it.extras.join(', ')}</div>` : ''}

                ${!isPkgItem ? `
                <div class="mt-1 d-inline-block border rounded px-2 py-0 bg-light">
                    <span class="small text-muted fw-bold" style="font-size: 0.7rem;">STAN:</span>
                    <span class="${statusColor} fw-bold ms-1" style="font-size: 0.9rem;">
                        ${statusIcon} ${readyCount} / ${totalCount}
                    </span>
                </div>
                ` : ''}
                <div class="d-flex justify-content-between align-items-center mt-1" style="min-width: 250px;">
                    <div class="text-muted" style="font-size:12px;">Cena jedn.: ${parseFloat(it.unit_price).toFixed(2)} EUR</div>
                    <div class="${isPkgItem ? "text-danger fw-bold" : "text-primary fw-bold"}" style="font-size:14px;">SUMA: ${parseFloat(it.total_group_price).toFixed(2)} EUR</div>
                </div>
            </div>
            </div>
            <div>${actionBtn}</div>
        </li>`;
    }).join("");

    // Sekcja Płatności
    let payBlock = '';
    if (isPaid) {
        payBlock = `
            <button class="btn btn-success fw-bold w-100 mb-2" disabled>ZAPŁACONE (${selected.payment_method_display || 'OK'})</button>
            <div class="text-center text-success fw-bold small">Opłacone — wysłane do kuchni</div>`;
    } else {
        const cardClass = (currentPaymentMethod === 'CARD') ? 'btn-success text-white' : 'btn-outline-success';
        const cashClass = (currentPaymentMethod === 'CASH') ? 'btn-success text-white' : 'btn-outline-success';
        payBlock = `
            <div class="mb-2">
                <div class="d-flex gap-2 mb-2">
                    <button class="btn ${cardClass} fw-bold flex-grow-1 py-2" onclick="setPaymentMethod('CARD')">💳 KARTA</button>
                    <button class="btn ${cashClass} fw-bold flex-grow-1 py-2" onclick="setPaymentMethod('CASH')">💵 GOTÓWKA</button>
                </div>
                <button class="btn btn-primary btn-lg w-100 fw-bold shadow-sm" onclick="markPaid(${selected.id})">
                    ZATWIERDŹ PŁATNOŚĆ (${currentPaymentMethod === 'CARD' ? 'Karta' : 'Gotówka'})
                </button>
            </div>
            <button class="btn btn-outline-dark w-100 mb-2 fw-bold" onclick="openSplitModal()">✂ PODZIEL RACHUNEK</button>
            <div class="text-center text-danger fw-bold small mt-1">Nieopłacone — edycja możliwa</div>`;
    }

    panel.innerHTML = `
        <div class="p-3 mb-3 bg-dark text-white rounded shadow-sm d-flex justify-content-between align-items-center">
            <div>
                <span class="badge bg-warning text-dark fs-4 me-2">#${selected.id}</span>
                <span class="fs-2 fw-bold text-uppercase align-middle">${escapeHtml(selected.customer_name)}</span>
            </div>
            <div class="text-end">
                <div class="fs-5">${selected.created_at_display || ""}</div>
                ${selected.is_takeaway 
                    ? '<span class="badge bg-warning text-dark">🥡 WYNOS</span>' 
                    : '<span class="badge bg-secondary">🍽️ NA MIEJSCU</span>'}
            </div>
        </div>
        <div class="d-flex gap-2 flex-wrap align-items-center mb-3">
            ${bannerHtml(selected)}
            <span class="badge bg-secondary fs-6">Gotowe pozycje: ${selected.ready_items}/${selected.total_items}</span>
        </div>

        ${deliveryInfoHtml}
      <div class="p-3 mb-3 bg-white border border-2 border-primary rounded text-center shadow-sm">
         <div class="text-uppercase text-muted fw-bold" style="font-size: 0.8rem;">Do zapłaty</div>
         <div class="display-3 fw-bold text-dark" style="letter-spacing: -1px;">${parseFloat(selected.total_price).toFixed(2)} EUR</div>
      </div>
      <div class="mb-3">${payBlock}</div>
      <div class="d-flex gap-2 mb-3" style="height: 50px;">
        <div class="flex-grow-1">${doneBtnHtml}</div>
        <button type="button" class="btn btn-outline-danger fw-bold" style="min-width: 150px;" onclick="openCancelModal(${selected.id})">ANULUJ 🗑</button>
      </div>
      <hr>
      <b>Pozycje:</b>
      <ul class="mt-2 list-unstyled mb-3">${itemsHtml}</ul>
      ${!isPaid ? `<button class="btn btn-outline-primary w-100 mb-3 dashed-border" onclick="openAddModal()">➕ Dodaj produkt</button>` : ''}
    `;
}

// --- AKCJE NA ZAMÓWIENIU ---

function setPaymentMethod(method) {
    currentPaymentMethod = method;
    if (currentSelectedData) renderSelected(currentSelectedData, currentConfig);
}

async function markPaid(orderId, methodOverride = null) {
    if (!orderId) return;
    const method = methodOverride || currentPaymentMethod;
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/${orderId}/paid/`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({ payment_method: method })
        });
        if (resp.ok) await refreshOnce();
    } catch (e) { console.error(e); }
}

async function markItemDone(itemId) {
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/item-done/${itemId}/`, {
            method: "POST", headers: { "X-CSRFToken": getCsrfToken() }
        });
        if (resp.ok) await refreshOnce();
    } catch (e) {}
}

async function markGroupDone(itemId) {
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/group-done/${itemId}/`, {
            method: "POST", headers: { "X-CSRFToken": getCsrfToken() }
        });
        if (resp.ok) await refreshOnce();
    } catch (e) {}
}

async function editQty(name, action, extras = []) {
    if (!selectedId) return;
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/${selectedId}/edit_qty/`, {
            method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({ product_name: name, action: action, extras: extras })
        });
        if (resp.ok) await refreshOnce();
    } catch (e) {}
}

async function toggleTakeaway() {
    if (!selectedId) return;
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/toggle-takeaway/${selectedId}/`, {
            method: "POST", headers: { "X-CSRFToken": getCsrfToken() }
        });
        if (resp.ok) await refreshOnce();
    } catch (e) {}
}

function openCancelModal(orderId) {
    const modalEl = document.getElementById('cancelOrderModal');
    const form = document.getElementById('cancelOrderForm');
    if (modalEl && form) {
        form.action = `/kasa/${CashierConfig.stationSlug}/${orderId}/cancel/`;
        new bootstrap.Modal(modalEl).show();
    }
}

// --- DODAWANIE PRODUKTU (WYSZUKIWARKA) ---

function openAddModal() {
    if (!addProductModal) {
        const el = document.getElementById('addProductModal');
        if (el) addProductModal = new bootstrap.Modal(el);
    }
    const input = document.getElementById('productSearchInput');
    const results = document.getElementById('searchResults');
    if (input) input.value = '';
    if (results) results.innerHTML = '';
    addProductModal.show();
    setTimeout(() => input && input.focus(), 500);
    searchProducts();
}

async function searchProducts() {
    const q = document.getElementById('productSearchInput').value;
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/search-products/?q=${encodeURIComponent(q)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        const list = document.getElementById('searchResults');
        
        if (data.results.length === 0) {
            list.innerHTML = '<div class="list-group-item text-muted p-3">Brak wyników</div>';
            return;
        }

        list.innerHTML = data.results.map(p => {
            const isActive = (p.is_active !== undefined) ? p.is_active : true;
            const productJson = encodeURIComponent(JSON.stringify(p));
            
            // Przyciski akcji
            let actionBtn = '';
            if (isActive && p.is_customizable) {
                actionBtn = `<button class="btn btn-outline-primary btn-sm fw-bold" onclick="event.stopPropagation(); openCustomization(JSON.parse(decodeURIComponent('${productJson}')))">⚙ Opcje</button>`;
            }
            const lockIcon = isActive ? '🔓' : '🔒';
            const lockBtn = `<button class="btn btn-sm btn-outline-secondary ms-2" onclick="toggleProduct(${p.id})">${lockIcon}</button>`;

            return `
            <div class="list-group-item d-flex align-items-center justify-content-between ${isActive ? 'list-group-item-action' : 'bg-light text-muted'}" 
                 style="${isActive ? 'cursor:pointer' : 'cursor:not-allowed'}"
                 onclick="${isActive ? `addProductToOrder(${p.id}, [])` : ''}">
                 <div>
                    <div class="fw-bold">${escapeHtml(p.name)} ${!isActive ? '<span class="badge bg-danger">BLOKADA</span>' : ''}</div>
                    <div class="text-success fw-bold">${p.price.toFixed(2)} EUR</div>
                 </div>
                 <div class="d-flex align-items-center">
                    ${actionBtn}
                    ${lockBtn}
                 </div>
            </div>`;
        }).join('');
    } catch (e) { console.error(e); }
}

async function toggleProduct(productId, event) {
    if (event) event.stopPropagation();
    try {
        await fetch(`/kasa/${CashierConfig.stationSlug}/toggle-product/${productId}/`, {
            method: "POST", headers: { "X-CSRFToken": getCsrfToken() }
        });
        searchProducts();
    } catch (e) {}
}

async function addProductToOrder(productId, extras) {
    if (!selectedId) return;
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/add-product/${selectedId}/`, {
            method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({ product_id: productId, extras: extras })
        });
        if (resp.ok) {
            if (addProductModal) addProductModal.hide();
            await refreshOnce();
        }
    } catch (e) {}
}

// --- CUSTOMIZACJA PRODUKTU ---

function openCustomization(productObj) {
    tempProductToAdd = productObj;
    if (!customizeProductModal) {
        const el = document.getElementById('customizeProductModal');
        if (el) customizeProductModal = new bootstrap.Modal(el);
    }
    document.getElementById('custModalTitle').innerText = productObj.name;
    const listDiv = document.getElementById('custModalIngredients');
    listDiv.innerHTML = '';
    document.getElementById('custModalExtraPrice').innerText = '0.00';

    productObj.ingredients.forEach((ing, idx) => {
        listDiv.insertAdjacentHTML('beforeend', `
            <div class="form-check p-2 border rounded bg-white ing-checkbox-wrapper" onclick="toggleIngCheckbox('ingCheck_${idx}')">
                <input class="form-check-input ms-1 me-2" type="checkbox" value="${ing.name}" data-price="${ing.price}" id="ingCheck_${idx}" onchange="calcCustomPrice()">
                <label class="form-check-label fw-bold" for="ingCheck_${idx}">${ing.name} (+${ing.price.toFixed(2)})</label>
            </div>`);
    });
    if (addProductModal) addProductModal.hide();
    customizeProductModal.show();
}

function toggleIngCheckbox(id) {
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'LABEL') return;
    const cb = document.getElementById(id);
    if (cb) { cb.checked = !cb.checked; calcCustomPrice(); }
}

function calcCustomPrice() {
    let total = 0;
    document.querySelectorAll('#custModalIngredients input:checked').forEach(cb => total += parseFloat(cb.dataset.price));
    document.getElementById('custModalExtraPrice').innerText = total.toFixed(2);
}

function confirmAddProduct() {
    if (!tempProductToAdd) return;
    const extras = Array.from(document.querySelectorAll('#custModalIngredients input:checked')).map(cb => cb.value);
    addProductToOrder(tempProductToAdd.id, extras);
    if (customizeProductModal) customizeProductModal.hide();
    tempProductToAdd = null;
}

// --- PODZIAŁ RACHUNKU (SPLIT BILL) ---

async function openSplitModal() {
    if (!selectedId) return;
    const modalEl = document.getElementById('splitBillModal');
    if (modalEl) splitModal = new bootstrap.Modal(modalEl);
    
    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/api/?sel=${selectedId}`);
        const data = await resp.json();
        
        const listDiv = document.getElementById('splitItemsList');
        listDiv.innerHTML = '';
        document.getElementById('splitTotalValue').innerText = '0.00 EUR';

        data.selected.grouped_items.forEach(group => {
            group.all_ids.forEach(id => {
                const html = `
                <div class="list-group-item d-flex gap-2 align-items-center" onclick="toggleSplitCheck('${id}')" style="cursor:pointer;">
                    <input class="form-check-input split-check" type="checkbox" value="${id}" data-price="${group.unit_price}" id="splitCheck_${id}" onchange="calcSplitTotal()">
                    <div class="flex-grow-1">
                        <div class="fw-bold">${escapeHtml(group.name)}</div>
                    </div>
                    <div class="fw-bold text-end">${group.unit_price.toFixed(2)}</div>
                </div>`;
                listDiv.insertAdjacentHTML('beforeend', html);
            });
        });

        currentOrderTotal = parseFloat(data.selected.total_price);
        const totalDisp = document.getElementById('calcOrderTotalDisplay');
        if (totalDisp) totalDisp.innerText = currentOrderTotal.toFixed(2) + ' EUR';

        document.getElementById('splitPeopleInput').value = 2;
        splitPeopleStates = [null, null];
        renderSplitAmountList();
        
        splitModal.show();
    } catch (e) { console.error(e); }
}

function changeSplitPeople(delta) {
    const input = document.getElementById('splitPeopleInput');
    let val = parseInt(input.value) || 1;
    val = Math.max(1, val + delta);
    input.value = val;
    
    // Zachowujemy stany, jeśli ktoś już zapłacił, ale resetujemy jeśli zmieniamy liczbę
    // (uproszczenie: resetujemy stany przy zmianie liczby osób)
    splitPeopleStates = new Array(val).fill(null);
    renderSplitAmountList();
}

function renderSplitAmountList() {
    const container = document.getElementById('splitAmountList');
    container.innerHTML = '';
    const perPerson = currentOrderTotal / splitPeopleStates.length;
    let paidCount = 0; let collectedCash = 0; let collectedCard = 0;

    splitPeopleStates.forEach((method, idx) => {
        if (method) { paidCount++; if(method==='CASH') collectedCash+=perPerson; else collectedCard+=perPerson; }
        
        const controls = method 
            ? `<div class="d-flex align-items-center gap-2"><span class="fw-bold text-success">OK</span><button class="btn btn-sm btn-outline-dark" onclick="setSplitPersonState(${idx}, null)">↩</button></div>`
            : `<div class="btn-group btn-group-sm"><button class="btn btn-outline-primary" onclick="setSplitPersonState(${idx}, 'CARD')">Karta</button><button class="btn btn-outline-success" onclick="setSplitPersonState(${idx}, 'CASH')">Gotówka</button></div>`;

        container.insertAdjacentHTML('beforeend', `
            <div class="list-group-item d-flex justify-content-between align-items-center ${method ? 'list-group-item-success' : ''}">
                <span>Osoba ${idx+1}</span>
                <div class="d-flex align-items-center gap-3"><span class="fw-bold">${perPerson.toFixed(2)}</span>${controls}</div>
            </div>`);
    });

    updateSplitSummary(paidCount, perPerson, collectedCash, collectedCard);
}

function setSplitPersonState(index, method) {
    splitPeopleStates[index] = method;
    renderSplitAmountList();
}

function updateSplitSummary(paidCount, perPerson, cashTotal, cardTotal) {
    const remaining = currentOrderTotal - (paidCount * perPerson);
    document.getElementById('splitRemainingAmount').innerText = Math.max(0, remaining).toFixed(2) + ' EUR';

    const successDiv = document.getElementById('splitAllPaidMsg');
    const boxEl = document.getElementById('splitSummaryBox');

    if (paidCount === splitPeopleStates.length) {
        boxEl.classList.add('d-none');
        successDiv.classList.remove('d-none');
        
        let method = 'OTHER';
        if (cardTotal > 0 && cashTotal === 0) method = 'CARD';
        if (cashTotal > 0 && cardTotal === 0) method = 'CASH';

        successDiv.innerHTML = `
            <div class="alert alert-success mb-2 text-center small">Gotowe! Karta: ${cardTotal.toFixed(2)}, Gotówka: ${cashTotal.toFixed(2)}</div>
            <button class="btn btn-success w-100 fw-bold py-2" onclick="finishSplitPayment('${method}')">ZAMKNIJ RACHUNEK ✅</button>`;
    } else {
        boxEl.classList.remove('d-none');
        successDiv.classList.add('d-none');
    }
}

function finishSplitPayment(method) {
    if (splitModal) splitModal.hide();
    markPaid(selectedId, method);
}

function toggleSplitCheck(id) {
    if (event.target.tagName === 'INPUT') return;
    const cb = document.getElementById(`splitCheck_${id}`);
    if (cb) { cb.checked = !cb.checked; calcSplitTotal(); }
}

function calcSplitTotal() {
    let total = 0;
    document.querySelectorAll('.split-check:checked').forEach(cb => total += parseFloat(cb.dataset.price));
    document.getElementById('splitTotalValue').innerText = total.toFixed(2) + ' EUR';
}

async function confirmSplit() {
    const ids = Array.from(document.querySelectorAll('.split-check:checked')).map(cb => cb.value);
    if (ids.length === 0) { alert("Wybierz produkty!"); return; }
    
    if (!confirm(`Wydzielić ${ids.length} poz. do nowego rachunku?`)) return;

    try {
        const resp = await fetch(`/kasa/${CashierConfig.stationSlug}/split/${selectedId}/`, {
            method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
            body: JSON.stringify({ item_ids: ids })
        });
        if (resp.ok) {
            const data = await resp.json();
            if (splitModal) splitModal.hide();
            selectOrder(data.new_order_id);
        }
    } catch (e) { console.error(e); }
}

// --- WEBSOCKETS ---

function connectCashierWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const socket = new WebSocket(protocol + window.location.host + '/ws/updates/');

    socket.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'refresh') refreshOnce();
    };

    socket.onclose = () => setTimeout(connectCashierWebSocket, 3000);
}