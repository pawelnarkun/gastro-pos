/**
 * static/js/kiosk_core.js
 * Obsługa logiki Kiosku: Koszyk, Inactivity Timer, WebSocket
 */

const KioskConfig = {
    stationSlug: null,
    packagingPrice: 0.0,
    csrfToken: null
};

// Stan aplikacji
let cart = JSON.parse(localStorage.getItem('kiosk_cart')) || [];
let isTakeaway = JSON.parse(localStorage.getItem('kiosk_is_takeaway')) || false;
let inactivityTimerId = null;
let warningIntervalId = null;
const INACTIVITY_LIMIT_MS = 60_000;
const WARNING_BEFORE_MS = 10_000;

// --- INICJALIZACJA ---
function initKiosk(slug, pkgPrice, csrfToken) {
    KioskConfig.stationSlug = slug;
    KioskConfig.packagingPrice = parseFloat(pkgPrice.replace(',', '.')); // fix dla locale
    KioskConfig.csrfToken = csrfToken;

    updateCartUI();
    resetInactivityTimerFromStorage();
    
    // Śledzenie aktywności
    ['click', 'keydown', 'touchstart', 'mousemove'].forEach(evt => {
        document.addEventListener(evt, registerActivity, { passive: true });
    });

    // WebSocket
    connectKioskWebSocket();
}

// --- LOGIKA KOSZYKA ---

function saveState() {
    localStorage.setItem('kiosk_cart', JSON.stringify(cart));
    localStorage.setItem('kiosk_is_takeaway', JSON.stringify(isTakeaway));
    updateCartUI();
    registerActivity();
}

function addToCart(id, name, price, hasFee, extras, extraIds = []) {
    price = parseFloat(price);
    const extrasSignature = extraIds.slice().sort().join(',');

    let existingItem = cart.find(item => {
        const itemSig = (item.extraIds || []).slice().sort().join(',');
        return item.id === id && itemSig === extrasSignature;
    });

    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            id: id, name: name, price: price, quantity: 1,
            hasFee: hasFee, extras: extras || [], extraIds: extraIds || []
        });
    }
    saveState();
}

function removeFromCart(index) {
    if (!cart[index]) return;
    cart.splice(index, 1);
    saveState();
}

function changeQuantity(index, delta) {
    if (!cart[index]) return;
    cart[index].quantity += delta;
    if (cart[index].quantity <= 0) {
        cart.splice(index, 1);
    }
    saveState();
}

function clearCart() {
    cart = [];
    isTakeaway = false;
    localStorage.removeItem('kiosk_cart');
    localStorage.removeItem('kiosk_is_takeaway');
    localStorage.removeItem('kiosk_last_activity');

    const checkbox = document.getElementById('takeawaySwitch');
    if (checkbox) checkbox.checked = false;

    hideInactivityWarning();
    updateCartUI();
}

function toggleTakeaway() {
    isTakeaway = !isTakeaway;
    saveState();
}

// --- UI UPDATE ---

function updateCartUI() {
    const list = document.getElementById('cart-list');
    const totalEl = document.getElementById('total-price');
    const checkoutBtns = document.querySelectorAll('#checkout-buttons button'); // Obsługa przycisków

    updateTakeawayButtonUI();

    if (!list || !totalEl) return;

    list.innerHTML = '';
    let total = 0;

    if (cart.length === 0) {
        list.innerHTML = '<li class="list-group-item text-center text-muted">Koszyk jest pusty</li>';
        totalEl.innerText = '0.00 EUR';
        checkoutBtns.forEach(btn => btn.disabled = true);
    } else {
        checkoutBtns.forEach(btn => btn.disabled = false);
        
        cart.forEach((item, index) => {
            let itemPrice = item.price;
            if (isTakeaway && item.hasFee) {
                itemPrice += KioskConfig.packagingPrice;
            }
            const itemTotal = itemPrice * item.quantity;
            total += itemTotal;

            const extraInfo = (isTakeaway && item.hasFee)
                ? `<br><span class="badge bg-secondary" style="font-size:0.7em">+ Opakowanie ${KioskConfig.packagingPrice.toFixed(2)}</span>`
                : '';

            let extrasHtml = '';
            if (item.extras && item.extras.length > 0) {
                extrasHtml = '<div class="text-success small fst-italic">';
                item.extras.forEach(ex => { extrasHtml += `+ ${ex.name}<br>`; });
                extrasHtml += '</div>';
            }

            // Używamy insertAdjacentHTML dla wydajności
            list.insertAdjacentHTML('beforeend', `
                <li class="list-group-item d-flex justify-content-between align-items-center">
                    <div class="me-2">
                        <span class="fw-bold d-block">
                            <span class="text-primary">${item.quantity}x</span> ${item.name}
                        </span>
                        ${extrasHtml}
                        <small class="text-muted">${item.price.toFixed(2)} EUR ${extraInfo}</small>
                    </div>
                    <div class="d-flex align-items-center">
                        <span class="fw-bold me-3">${itemTotal.toFixed(2)}</span>
                        <div class="btn-group btn-group-sm">
                            <button class="btn btn-outline-secondary" onclick="changeQuantity(${index}, -1)">-</button>
                            <button class="btn btn-outline-secondary" onclick="changeQuantity(${index}, 1)">+</button>
                            <button class="btn btn-outline-danger" onclick="removeFromCart(${index})">🗑</button>
                        </div>
                    </div>
                </li>
            `);
        });
        totalEl.innerText = total.toFixed(2) + ' EUR';
    }
}

function updateTakeawayButtonUI() {
    const dineInDiv = document.getElementById('ui-dinein');
    const takeawayDiv = document.getElementById('ui-takeaway');
    const checkbox = document.getElementById('takeawaySwitch');

    if (!dineInDiv || !takeawayDiv) return;
    if (checkbox) checkbox.checked = isTakeaway;

    if (isTakeaway) {
        dineInDiv.className = "w-50 d-flex flex-column align-items-center justify-content-center transition-bg border-end border-secondary bg-light text-muted opacity-50";
        takeawayDiv.className = "w-50 d-flex flex-column align-items-center justify-content-center transition-bg bg-warning text-dark border-warning";
    } else {
        dineInDiv.className = "w-50 d-flex flex-column align-items-center justify-content-center transition-bg border-end border-secondary bg-white text-dark fw-bold";
        takeawayDiv.className = "w-50 d-flex flex-column align-items-center justify-content-center transition-bg bg-light text-muted opacity-50";
    }
}

// --- CHECKOUT ---

function checkout(paymentType) {
    if (cart.length === 0) { alert("Koszyk jest pusty!"); return; }

    let name = prompt("Podaj imię do zamówienia:");
    if (!name) return;

    if (paymentType === 'KIOSK_CARD') {
        if (!confirm("Proszę zbliżyć kartę do terminala... (Kliknij OK, aby zapłacić)")) return;
    }

    let apiItems = [];
    cart.forEach(item => {
        for (let i = 0; i < item.quantity; i++) {
            apiItems.push({ id: item.id, extras: item.extraIds || [] });
        }
    });

    fetch('/api/order/', {
        method: 'POST',
        body: JSON.stringify({
            items: apiItems,
            customer_name: name,
            is_takeaway: isTakeaway,
            payment_type: paymentType,
            station_slug: KioskConfig.stationSlug
        }),
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': KioskConfig.csrfToken }
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'ok') {
            clearCart(); 
            // Komunikat
            let msg = '✅ Zamówienie przyjęte! Nr: ' + data.number;
            if (paymentType === 'COUNTER') msg += '\nZapraszamy do kasy, aby opłacić.';
            else msg += '\nOpłacono pomyślnie. Odbiór wkrótce!';
            
            showOrderMessage(msg);
        } else {
            alert("Błąd: " + data.message);
        }
    });
}

function showOrderMessage(text) {
    const msgDiv = document.getElementById('order-message');
    if (!msgDiv) return;
    msgDiv.innerText = text;
    msgDiv.classList.remove('d-none');
    setTimeout(function() {
        msgDiv.classList.add('d-none');
        msgDiv.innerText = '';
        // Przekierowanie na stronę główną kiosku
        window.location.href = `/kiosk/${KioskConfig.stationSlug}/`;
    }, 4000);
}

// --- INACTIVITY TIMER ---

function hideInactivityWarning() {
    const bar = document.getElementById('inactivity-warning');
    if (!bar) return;
    bar.classList.add('d-none');
    bar.textContent = '';
}

function updateInactivityWarning() {
    const bar = document.getElementById('inactivity-warning');
    if (!bar) return;
    if (!cart || cart.length === 0) { hideInactivityWarning(); return; }
    
    const lastStr = localStorage.getItem('kiosk_last_activity');
    if (!lastStr) { hideInactivityWarning(); return; }
    
    const diff = Date.now() - parseInt(lastStr, 10);
    const remaining = INACTIVITY_LIMIT_MS - diff;
    
    if (remaining <= 0) { hideInactivityWarning(); return; }
    
    if (remaining <= WARNING_BEFORE_MS) {
        const secs = Math.ceil(remaining / 1000);
        bar.textContent = 'Brak aktywności – zamówienie zostanie anulowane za ' + secs + ' s';
        bar.classList.remove('d-none');
    } else { 
        hideInactivityWarning(); 
    }
}

function cancelOrderForInactivity() {
    if (warningIntervalId) { clearInterval(warningIntervalId); warningIntervalId = null; }
    hideInactivityWarning();
    if (!cart || cart.length === 0) return;
    
    clearCart();
    window.location.href = `/kiosk/${KioskConfig.stationSlug}/`;
}

function resetInactivityTimerFromStorage() {
    if (inactivityTimerId) clearTimeout(inactivityTimerId);
    
    const now = Date.now();
    const lastStr = localStorage.getItem('kiosk_last_activity');
    let last = lastStr ? parseInt(lastStr, 10) : 0;
    
    if (!last) { 
        last = now; 
        localStorage.setItem('kiosk_last_activity', String(now)); 
    }
    
    const diff = now - last;
    if (diff >= INACTIVITY_LIMIT_MS) { 
        cancelOrderForInactivity(); 
    } else {
        const remaining = INACTIVITY_LIMIT_MS - diff;
        inactivityTimerId = setTimeout(cancelOrderForInactivity, remaining);
        
        if (warningIntervalId) clearInterval(warningIntervalId);
        warningIntervalId = setInterval(updateInactivityWarning, 1000);
        updateInactivityWarning();
    }
}

function registerActivity() {
    localStorage.setItem('kiosk_last_activity', String(Date.now()));
    hideInactivityWarning();
    resetInactivityTimerFromStorage();
}

// --- WEBSOCKETS ---

function connectKioskWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const socketUrl = protocol + window.location.host + '/ws/updates/';
    const socket = new WebSocket(socketUrl);

    socket.onopen = () => console.log("WS: Kiosk Connected 🤖");
    
    socket.onmessage = async (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'refresh') {
            // Unikamy odświeżania gdy modal jest otwarty
            if (document.querySelector('.modal.show')) return;
            
            // Pobieramy nową treść
            try {
                const resp = await fetch(window.location.href);
                if (!resp.ok) return;
                const html = await resp.text();
                const doc = new DOMParser().parseFromString(html, "text/html");

                // Odświeżamy kontener produktów lub kategorii
                const newContent = doc.querySelector("#productsContainer") || doc.querySelector("#categoriesContainer");
                const oldContent = document.querySelector("#productsContainer") || document.querySelector("#categoriesContainer");
                
                if (newContent && oldContent) {
                    oldContent.innerHTML = newContent.innerHTML;
                    // Przywróć filtrowanie jeśli jesteśmy w kategorii
                    if (typeof filterProducts === 'function') filterProducts();
                }
            } catch (err) { console.error(err); }
        }
    };

    socket.onclose = () => setTimeout(connectKioskWebSocket, 3000);
}