from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from .models import Product, Order, OrderItem, Category, Ingredient, GlobalSettings, Station, Allergen, Employee, StationLog
from django.db.models.functions import TruncDate
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.db import transaction

import datetime
import json


# --- FUNKCJE POMOCNICZE ---

def log_event(station, action, employee=None, order=None, details=""):
    """Tworzy wpis w historii zdarzeń (Audit Log)"""
    try:
        StationLog.objects.create(
            station=station,
            action=action,
            employee=employee,
            order=order,
            details=details
        )
    except Exception as e:
        print(f"Błąd logowania zdarzenia: {e}")

def find_next_available_sibling(original_item):
    """
    Jeśli original_item jest już zajęty/gotowy, szuka innego itemu w tym samym zamówieniu,
    który jest identyczny (ten sam produkt i te same dodatki), ale wciąż 'is_ready=False'.
    """
    siblings = OrderItem.objects.filter(
        order=original_item.order,
        product_name=original_item.product_name,
        is_ready=False
    ).exclude(id=original_item.id).prefetch_related('ingredients')

    # Pobieramy ID składników oryginału do porównania
    original_ingredients = set(original_item.ingredients.values_list('id', flat=True))

    for sibling in siblings:
        sibling_ingredients = set(sibling.ingredients.values_list('id', flat=True))
        if sibling_ingredients == original_ingredients:
            return sibling # Znaleziono wolnego bliźniaka!
    
    return None

# --- HELPERS DO SESJI PRACOWNIKÓW ---

def get_station_team(request, station_slug):
    """Zwraca słownik: {'leader_id': int, 'members_ids': [int, ...]} lub None"""
    session_key = f'station_team_{station_slug}'
    return request.session.get(session_key)

def set_station_team(request, station_slug, leader_id, members_ids):
    session_key = f'station_team_{station_slug}'
    request.session[session_key] = {
        'leader_id': leader_id,
        'members_ids': list(set(members_ids)) # unikalne ID
    }
    request.session.modified = True

def clear_station_team(request, station_slug):
    session_key = f'station_team_{station_slug}'
    if session_key in request.session:
        del request.session[session_key]
        request.session.modified = True

def get_current_leader(request, station_slug):
    """Zwraca obiekt Employee lidera lub None"""
    data = get_station_team(request, station_slug)
    if not data or not data.get('leader_id'):
        return None
    return Employee.objects.filter(id=data['leader_id']).first()

# --- WIDOKI LOGOWANIA ---

def station_login(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    
    # Filtrujemy pracowników w zależności od typu stacji
    employees = Employee.objects.filter(is_active=True)
    if station.station_type == 'CASHIER':
        employees = employees.filter(can_work_cashier=True)
    elif station.station_type == 'KITCHEN':
        employees = employees.filter(can_work_kitchen=True)

    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        pin = request.POST.get('pin', '')
        
        emp = get_object_or_404(Employee, id=employee_id)
        
        # Sprawdzenie PINu (jeśli ustawiony)
        if emp.pin and emp.pin != pin:
            return render(request, 'station_login.html', {
                'station': station, 
                'employees': employees, 
                'error': 'Błędny PIN!'
            })

        # Logika Teamu:
        team_data = get_station_team(request, station_slug)
        
        if not team_data:
            # Pierwsza osoba -> Lider
            set_station_team(request, station_slug, emp.id, [emp.id])
        else:
            # Kolejna osoba -> Dodaj do teamu (nie zmienia lidera)
            members = team_data['members_ids']
            if emp.id not in members:
                members.append(emp.id)
            set_station_team(request, station_slug, team_data['leader_id'], members)
        
        # LOGOWANIE ZDARZENIA (LOGIN)
        log_event(station, 'LOGIN', employee=emp, details="Pracownik zalogował się do stacji.")

        # Przekierowanie do właściwego widoku
        if station.station_type == 'CASHIER':
            return redirect('cashier', station_slug=station.slug)
        elif station.station_type == 'KITCHEN':
            return redirect('kitchen', station_slug=station.slug)
        else:
            return redirect('station_selection')

    return render(request, 'station_login.html', {'station': station, 'employees': employees})

def station_logout(request, station_slug):
    # Wylogowanie czyści cały team
    station = get_object_or_404(Station, slug=station_slug)
    
    # Możemy zalogować wylogowanie wszystkich (bez konkretnego pracownika, lub lidera)
    leader = get_current_leader(request, station_slug)
    log_event(station, 'LOGOUT', employee=leader, details="Wylogowano cały zespół (zamknięcie zmiany).")

    clear_station_team(request, station_slug)
    return redirect('station_login', station_slug=station_slug)

def station_switch_leader(request, station_slug, employee_id):
    """Zmienia lidera na inną osobę z ZALOGOWANEGO zespołu"""
    station = get_object_or_404(Station, slug=station_slug)
    team_data = get_station_team(request, station_slug)
    
    if team_data:
        target_id = int(employee_id)
        members = team_data.get('members_ids', [])
        
        # Zmieniamy lidera TYLKO jeśli ta osoba faktycznie jest w zespole
        if target_id in members:
            set_station_team(request, station_slug, target_id, members)
            
            # LOGOWANIE ZDARZENIA (LEADER)
            new_leader = Employee.objects.filter(id=target_id).first()
            if new_leader:
                log_event(station, 'LEADER', employee=new_leader, details=f"Przejął rolę Lidera.")
    
    # Powrót tam skąd przyszliśmy
    return redirect(get_referer(request))

@require_POST
def station_logout_user(request, station_slug, employee_id):
    station = get_object_or_404(Station, slug=station_slug)
    
    # 1. Pobieramy aktualne dane zespołu
    data = get_station_team(request, station_slug)
    
    # Jeśli sesja wygasła lub jest pusta, przekieruj do logowania
    if not data:
        return redirect('station_login', station_slug=station_slug)

    members = data.get('members_ids', [])
    leader_id = data.get('leader_id')
    target_id = int(employee_id)

    # LOGOWANIE ZDARZENIA (LOGOUT POJEDYNCZY)
    emp_leaving = Employee.objects.filter(id=target_id).first()
    if emp_leaving:
        log_event(station, 'LOGOUT', employee=emp_leaving, details="Wylogowanie pojedynczego pracownika.")

    # 2. Usuwamy pracownika z listy (jeśli tam jest)
    if target_id in members:
        members.remove(target_id)
    
    # 3. Sprawdzamy, czy lista nie jest pusta po usunięciu
    if not members:
        # Jeśli nikogo nie ma, czyścimy sesję całkowicie
        clear_station_team(request, station_slug)
        return redirect('station_login', station_slug=station_slug)

    # 4. Logika zmiany lidera (jeśli wylogował się aktualny lider)
    new_leader_id = leader_id
    if leader_id == target_id:
        # Lider odszedł, więc pierwszy z brzegu zostaje nowym szefem
        new_leader_id = members[0]
        
        # Logujemy automatyczną zmianę lidera
        auto_leader = Employee.objects.filter(id=new_leader_id).first()
        if auto_leader:
             log_event(station, 'LEADER', employee=auto_leader, details="Automatyczne przejęcie lidera po wylogowaniu poprzednika.")
            
    # 5. Zapisujemy nowy stan w sesji
    set_station_team(request, station_slug, new_leader_id, members)

    return redirect(get_referer(request))


def get_packaging_price():
    pkg_product = Product.objects.filter(name__iexact="Opakowanie").first()
    if pkg_product:
        return float(pkg_product.price)
    return 0.00


def get_product_category_map():
    mapping = {}
    for p in Product.objects.select_related('category').all():
        mapping[p.name] = p.category.name if p.category else ""
    return mapping


def get_product_map():
    return {p.name: p for p in Product.objects.all()}


def get_referer(request):
    """Pomocnicza funkcja do powrotu na poprzednią stronę."""
    return request.META.get('HTTP_REFERER', '/')


def recalculate_order_total(order):
    items = order.orderitem_set.prefetch_related('ingredients').all()
    total = 0.0
    price_map = {p.name: float(p.price) for p in Product.objects.all()}
    
    for item in items:
        base_price = price_map.get(item.product_name, 0.0)
        ingredients_price = sum(float(ing.price) for ing in item.ingredients.all())
        total += (base_price + ingredients_price)
        
    order.total_price = total
    order.save(update_fields=['total_price'])


# --- WYBÓR STACJI (LANDING PAGE) ---

def station_selection(request):
    stations = Station.objects.all().order_by('station_type', 'name')
    return render(request, 'station_selection.html', {'stations': stations})


# --- KIOSK ---

@ensure_csrf_cookie
def kiosk_index(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug, station_type='KIOSK')
    products_qs = Product.objects.filter(
        Q(limit_availability=station) | Q(limit_availability=None),
        is_active=True
    )
    categories = Category.objects.filter(products__in=products_qs).distinct()
    current_pkg_price = get_packaging_price()

    return render(request, 'kiosk_home.html', {
        'categories': categories,
        'packaging_price': current_pkg_price,
        'station': station, 
    })


@ensure_csrf_cookie
def kiosk_category(request, station_slug, category_id):
    station = get_object_or_404(Station, slug=station_slug, station_type='KIOSK')
    category = get_object_or_404(Category, id=category_id)
    
    products = category.products.filter(
        Q(limit_availability=station) | Q(limit_availability=None),
        is_active=True
    ).prefetch_related('possible_ingredients', 'allergens')
    allergens_list = Allergen.objects.all().order_by('code')

    current_pkg_price = get_packaging_price()

    return render(request, 'kiosk_category.html', {
        'category': category,
        'products': products,
        'packaging_price': current_pkg_price,
        'station': station,
        'allergens_list': allergens_list,
    })


@require_POST
def api_create_order(request):
    try:
        data = json.loads(request.body)
        raw_items = data.get('items', [])
        customer_name = data.get('customer_name', 'Klient')
        is_takeaway = data.get('is_takeaway', False)
        
        # 1. Pobieramy typ płatności i slug stacji
        payment_choice = data.get('payment_type', 'COUNTER')
        station_slug = data.get('station_slug') # <--- NOWOŚĆ
        
        station_obj = None
        if station_slug:
            station_obj = Station.objects.filter(slug=station_slug).first()

        current_pkg_price = get_packaging_price()

        if not raw_items:
            return JsonResponse({'status': 'error', 'message': 'Pusty koszyk'}, status=400)

        with transaction.atomic():
            initial_status = 'UNPAID'
            db_payment_method = None
            
            if payment_choice == 'KIOSK_CARD':
                initial_status = 'NEW'  # Opłacone -> trafia na kuchnię
                db_payment_method = 'CARD'
            
            # 2. Tworzymy zamówienie z przypisaną stacją
            order = Order.objects.create(
                total_price=0,
                status=initial_status,          
                payment_method=db_payment_method, 
                customer_name=customer_name,
                is_takeaway=is_takeaway,
                station=station_obj # <--- PRZYPISANIE STACJI (Fix dla "zgubionych" zamówień)
            )

            total = 0
            added_any = False

            for item_data in raw_items:
                if isinstance(item_data, int):
                    pid = item_data
                    extra_ids = []
                else:
                    pid = item_data.get('id')
                    extra_ids = item_data.get('extras', [])

                try:
                    # Blokujemy rekord produktu do edycji (dla bezpieczeństwa stanów)
                    p = Product.objects.select_for_update().get(id=pid, is_active=True)

                    if p.auto_block_when_zero and p.stock <= 0:
                        continue

                    # Zdejmowanie stanu magazynowego JEŚLI opłacono w kiosku
                    if initial_status == 'NEW': 
                        current_stock = p.stock if p.stock is not None else 0
                        p.stock = current_stock - 1
                        if p.auto_block_when_zero and p.stock <= 0:
                            p.is_active = False
                        p.save()

                    order_item = OrderItem.objects.create(order=order, product_name=p.name)
                    item_price = float(p.price)
                    
                    if extra_ids:
                        ingredients = Ingredient.objects.filter(id__in=extra_ids)
                        order_item.ingredients.set(ingredients)
                        for ing in ingredients:
                            item_price += float(ing.price)

                    total += item_price
                    added_any = True

                    if is_takeaway and p.has_packaging_fee:
                        OrderItem.objects.create(
                            order=order,                        
                            product_name="Opakowanie",
                            is_ready=False
                        )
                        total += current_pkg_price

                except Product.DoesNotExist:
                    continue

            if not added_any:
                raise Exception("Wybrane produkty nie są dostępne.")

            order.total_price = total
            order.save()

            # 3. Logowanie zdarzenia z poprawną stacją
            if payment_choice == 'KIOSK_CARD':
                 # Pobieramy nazwę stacji, jeśli istnieje, w przeciwnym razie wpisujemy ogólnie "Kiosk"
                 place_name = station_obj.name if station_obj else "Kiosk (nieznany)"
                 
                 log_event(
                     station_obj, 
                     'ORDER_PAID', 
                     details=f"Zapłacono kartą (Miejsce: {place_name}). Zamówienie #{order.id}"
                 )

        return JsonResponse({'status': 'ok', 'number': order.id})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# --- KUCHNIA ---

def kitchen_index(request, station_slug):
    leader = get_current_leader(request, station_slug)
    if not leader:
        return redirect('station_login', station_slug=station_slug)
    
    team_data = get_station_team(request, station_slug)
    team_members = []
    if team_data:
        team_members = Employee.objects.filter(id__in=team_data['members_ids'])

    station = get_object_or_404(Station, slug=station_slug, station_type='KITCHEN')
    orders = Order.objects.filter(status='NEW').prefetch_related('orderitem_set').order_by('created_at')
    
    product_categories = get_product_category_map()
    product_map = get_product_map()

    active_kitchen_orders = [] 

    for order in orders:
        items = order.orderitem_set.all()
        grouped = {}
        has_kitchen_pending = False
        has_bar_pending = False

        for item in items:
            name = item.product_name
            product = product_map.get(name)

            if not item.is_ready:
                if not product or product.can_cashier_deliver:
                    has_bar_pending = True
                if not product or product.can_kitchen_deliver:
                    has_kitchen_pending = True

            ingredients_list = list(item.ingredients.values_list('name', flat=True))
            ingredients_list.sort()
            extras_str = ", ".join(ingredients_list)
            
            category_name = product_categories.get(name, "")
            key = (category_name, name, extras_str) 

            if key not in grouped:
                grouped[key] = {
                    'name': name,
                    'extras': extras_str,
                    'category': category_name,
                    'total': 0,
                    'ready': 0,
                    'next_id': None,
                    'is_fully_ready': False,
                    'product_id': product.id if product else None,
                    'is_active': product.is_active if product else True,
                    'can_kitchen_deliver': product.can_kitchen_deliver if product else True,
                }

            grouped[key]['total'] += 1
            if item.is_ready:
                grouped[key]['ready'] += 1
            elif grouped[key]['next_id'] is None:
                grouped[key]['next_id'] = item.id

        final_list = []
        for val in grouped.values():
            if val['ready'] == val['total']:
                val['is_fully_ready'] = True
            final_list.append(val)

        final_list.sort(key=lambda x: ((x['category'] or ''), x['name']))
        order.custom_items = final_list
        order.has_kitchen_pending = has_kitchen_pending
        order.has_bar_pending = has_bar_pending

        if has_kitchen_pending:
            active_kitchen_orders.append(order)

    return render(request, 'kitchen.html', {
        'orders': active_kitchen_orders,
        'station': station,
        'leader': leader,
        'team_members': team_members,
    })


def kitchen_products(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    query = request.GET.get("q", "").strip()

    products = Product.objects.filter(
        Q(limit_availability=station) | Q(limit_availability=None)
    ).select_related("category")
    
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(category__name__icontains=query)
        )
    products = products.order_by("category__name", "name")

    # --- ZMIANA: Pobieramy ustawienia ---
    settings = GlobalSettings.load()

    return render(request, "kitchen_products.html", {
        "products": products,
        "query": query,
        "station": station,
        "settings": settings,
    })


def kitchen_history(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug, station_type='KITCHEN')
    
    dates_qs = (
        Order.objects.filter(Q(status='DONE') | Q(status='CANCELLED'))
        .annotate(just_date=TruncDate('created_at'))
        .values_list('just_date', flat=True)
        .distinct()
        .order_by('-just_date')
    )
    available_dates = list(dates_qs)
    
    selected_date_str = request.GET.get('date')
    selected_date = None
    if selected_date_str:
        try:
            selected_date = datetime.datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError: pass
        
    if not selected_date and available_dates:
        selected_date = available_dates[0]

    orders = (
        Order.objects
        .filter(Q(status='DONE') | Q(status='CANCELLED'))
        .prefetch_related(
            'orderitem_set', 
            'orderitem_set__ingredients', 
            'orderitem_set__completed_by',
            'delivered_by_kitchen',
            'delivered_by_bar',
            'kitchen_team',
            'bar_team'
        )
        .order_by('-created_at')
    )
    
    if selected_date:
        orders = orders.filter(created_at__date=selected_date)

    product_categories = get_product_category_map()
    bar_products_names = set(Product.objects.filter(can_cashier_deliver=True).values_list('name', flat=True))

    for order in orders:
        items = order.orderitem_set.all()
        
        kitchen_members = set(member.name for member in order.kitchen_team.all())
        bar_members = set(member.name for member in order.bar_team.all())

        k_leader_name = order.delivered_by_kitchen.name if order.delivered_by_kitchen else None
        b_leader_name = order.delivered_by_bar.name if order.delivered_by_bar else None

        for item in items:
            if item.completed_by:
                name = item.completed_by.name
                if item.product_name in bar_products_names:
                    bar_members.add(name)
                else:
                    kitchen_members.add(name)

        if k_leader_name:
            if k_leader_name in kitchen_members:
                kitchen_members.remove(k_leader_name)
            if kitchen_members:
                helpers_str = ", ".join(sorted(kitchen_members))
                order.kitchen_workers_display = f"{k_leader_name} (+ {helpers_str})"
            else:
                order.kitchen_workers_display = k_leader_name
        else:
            order.kitchen_workers_display = ", ".join(sorted(kitchen_members))

        if b_leader_name:
            if b_leader_name in bar_members:
                bar_members.remove(b_leader_name)
            if bar_members:
                helpers_str = ", ".join(sorted(bar_members))
                order.bar_workers_display = f"{b_leader_name} (+ {helpers_str})"
            else:
                order.bar_workers_display = b_leader_name
        else:
            order.bar_workers_display = ", ".join(sorted(bar_members))


        counts = {}
        for item in items:
            name = item.product_name
            extras = list(item.ingredients.values_list('name', flat=True))
            extras.sort()
            extras_str = ", ".join(extras)
            category_name = product_categories.get(name, "")
            worker_name = item.completed_by.name if item.completed_by else None
            key = (category_name, name, extras_str, worker_name)
            counts[key] = counts.get(key, 0) + 1

        aggregated = []
        for (cat, name, ext, worker), count in sorted(counts.items(), key=lambda kv: ((kv[0][0] or ''), kv[0][1])):
            aggregated.append({
                'name': name,
                'category': cat,
                'extras': ext, 
                'count': count,
                'worker': worker,
            })
        order.aggregated_items = aggregated

    return render(request, 'kitchen_history.html', {
        'orders': orders,
        'available_dates': available_dates,
        'current_date': selected_date,
        'station': station
    })


# --- AKCJE KUCHNI (REDIRECTY) ---

def kitchen_item_done(request, station_slug, item_id):
    station = get_object_or_404(Station, slug=station_slug)
    with transaction.atomic():
        try:
            item = OrderItem.objects.select_for_update().get(id=item_id)
        except OrderItem.DoesNotExist:
            return redirect(get_referer(request))

        if item.is_ready:
            sibling = find_next_available_sibling(item)
            if sibling:
                item = sibling
            else:
                return redirect(get_referer(request))

        leader = get_current_leader(request, station_slug)
        item.is_ready = True
        if leader:
            item.completed_by = leader
        item.save()
        
        # LOGOWANIE ZDARZENIA (ITEM DONE - KUCHNIA)
        log_event(
            station, 
            'ITEM_DONE', 
            employee=leader, 
            order=item.order, 
            details=f"Wydano produkt: {item.product_name}"
        )

    return redirect(get_referer(request))

def kitchen_group_done(request, station_slug, item_id):
    station = get_object_or_404(Station, slug=station_slug)
    ref_item = get_object_or_404(OrderItem, id=item_id)
    order = ref_item.order
    ref_ingredients = set(ref_item.ingredients.all())
    
    leader = get_current_leader(request, station_slug)
    
    siblings = OrderItem.objects.filter(
        order=order, 
        product_name=ref_item.product_name, 
        is_ready=False
    ).prefetch_related('ingredients')
    
    count = 0
    for item in siblings:
        if set(item.ingredients.all()) == ref_ingredients:
            item.is_ready = True
            if leader:
                item.completed_by = leader
            item.save()
            count += 1
            
    # LOGOWANIE ZDARZENIA (GROUP DONE)
    if count > 0 and leader:
        log_event(
            station, 
            'ITEM_DONE', 
            employee=leader, 
            order=order, 
            details=f"Wydano grupowo: {ref_item.product_name} ({count} szt.)"
        )
            
    return redirect(get_referer(request))

@require_POST
def kitchen_done(request, station_slug, order_id):
    station = get_object_or_404(Station, slug=station_slug)
    order = get_object_or_404(Order, id=order_id)
    leader = get_current_leader(request, station_slug)

    order.station = station
    
    if leader:
        order.delivered_by_kitchen = leader  
        team_data = get_station_team(request, station_slug)
        if team_data:
            members_ids = team_data.get('members_ids', [])
            members = Employee.objects.filter(id__in=members_ids)
            order.kitchen_team.set(members)

    kitchen_products_names = Product.objects.filter(can_kitchen_deliver=True).values_list('name', flat=True)
    
    items_to_update = OrderItem.objects.filter(
        order=order,
        is_ready=False,
        product_name__in=kitchen_products_names
    )
    
    for item in items_to_update:
        item.is_ready = True
        if leader:
            item.completed_by = leader
        item.save()
    
    if not OrderItem.objects.filter(order=order, is_ready=False).exists():
        order.status = 'DONE'
        # Logujemy, że całe zamówienie zostało wydane
        if leader:
             log_event(station, 'OTHER', employee=leader, order=order, details="Zamówienie w pełni zrealizowane (DONE).")
    
    order.save() 
        
    return redirect(get_referer(request))

def kitchen_add_note(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        note_text = request.POST.get('note')
        order.note = note_text
        order.save()
    return redirect(get_referer(request))

def kitchen_add_history_note(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        note_text = request.POST.get('history_note')
        order.history_note = note_text
        order.save()
    return redirect(get_referer(request))

def kitchen_toggle_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.is_active = not product.is_active
    product.save()
    return redirect(get_referer(request))

@require_POST
def kitchen_update_stock(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    action = request.POST.get("action")
    if action == "inc":
        product.stock = (product.stock or 0) + 1
    elif action == "dec":
        product.stock = max((product.stock or 0) - 1, 0)
    elif action == "set":
        try:
            new_stock = int(request.POST.get("stock", product.stock))
        except (TypeError, ValueError):
            new_stock = product.stock
        if new_stock < 0: new_stock = 0
        product.stock = new_stock
    product.save()
    return redirect(get_referer(request))

@require_POST
def kitchen_toggle_autoblock(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.auto_block_when_zero = not product.auto_block_when_zero
    product.save()
    return redirect(get_referer(request))


# --- KASA ---

@ensure_csrf_cookie
def cashier_index(request, station_slug):
    leader = get_current_leader(request, station_slug)
    if not leader:
        return redirect('station_login', station_slug=station_slug)
    
    team_data = get_station_team(request, station_slug)
    team_members = []
    if team_data:
        team_members = Employee.objects.filter(id__in=team_data['members_ids'])

    station = get_object_or_404(Station, slug=station_slug, station_type='CASHIER')
    
    q = (request.GET.get("q") or "").strip()
    sel = request.GET.get("sel")

    orders_qs = Order.objects.exclude(status='DONE').prefetch_related("orderitem_set").order_by('created_at')
    
    if q:
        if q.isdigit(): orders_qs = orders_qs.filter(id=int(q))
        else: orders_qs = orders_qs.filter(customer_name__icontains=q)

    orders = []
    for o in orders_qs:
        items = list(o.orderitem_set.all())
        total = len(items)
        ready = sum(1 for i in items if i.is_ready)
        o.total_items = total
        o.ready_items = ready
        o.is_ready = (total > 0 and ready == total)
        o.is_in_progress = (ready > 0 and not o.is_ready)
        o.is_new = (ready == 0)
        o.is_paid = (o.status != "UNPAID")
        orders.append(o)
        
    orders.sort(key=lambda x: (not x.is_ready, x.created_at))
    orders.sort(key=lambda x: (x.is_paid, not x.is_ready, x.created_at))

    selected = None
    if sel and sel.isdigit():
        selected = next((x for x in orders if x.id == int(sel)), None)
    
    if selected:
        product_categories = get_product_category_map()
        selected.grouped_items = build_grouped_items(selected, product_categories)

    return render(request, "cashier.html", {
        "orders": orders,
        "selected": selected,
        "q": q,
        "station": station,
        "leader": leader,
        "team_members": team_members,
    })


def cashier_api(request, station_slug):
    # --- NOWY KOD: Obsługa Płatności Mieszanej (POST) ---
    if request.method == "POST":
        action = request.POST.get('action')
        
        if action == 'pay_mixed':
            try:
                station = get_object_or_404(Station, slug=station_slug)
                leader = get_current_leader(request, station_slug)
                
                order_id = request.POST.get('order_id')
                cash_amount = request.POST.get('cash_amount', '0').replace(',', '.')
                card_amount = request.POST.get('card_amount', '0').replace(',', '.')
                
                order = get_object_or_404(Order, id=order_id)
                
                with transaction.atomic():
                    # 1. Zapisujemy kwoty
                    order.payment_cash = float(cash_amount)
                    order.payment_card = float(card_amount)
                    
                    # 2. Jeśli zamówienie było nieopłacone -> Zdejmujemy ze stanu i zmieniamy status
                    if order.status == "UNPAID":
                        # Logika zdejmowania ze stanu (skopiowana z cashier_paid)
                        for item in order.orderitem_set.all():
                            product = Product.objects.filter(name=item.product_name).first()
                            if product:
                                current_stock = product.stock if product.stock is not None else 0
                                product.stock = current_stock - 1
                                if product.auto_block_when_zero and product.stock <= 0:
                                    product.is_active = False
                                product.save()

                        order.status = "NEW" # Trafia do kuchni
                        order.payment_method = "MIXED" 
                        order.save()
                        
                        # Logujemy zdarzenie
                        log_event(
                            station, 'ORDER_PAID', employee=leader, order=order,
                            details=f"Płatność Mieszana: Gotówka {order.payment_cash}, Karta {order.payment_card}"
                        )
                
                return JsonResponse({'status': 'ok'})
                
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    # --- KONIEC NOWEGO KODU ---
    station = get_object_or_404(Station, slug=station_slug)
    q = (request.GET.get("q") or "").strip()
    sel = request.GET.get("sel")
    
    orders_qs = (
        Order.objects
        .exclude(Q(status="DONE") | Q(status="CANCELLED"))
        .prefetch_related("orderitem_set", "orderitem_set__ingredients")
        .order_by("created_at")
    )

    if q:
        if q.isdigit():
            orders_qs = orders_qs.filter(id=int(q))
        else:
            orders_qs = orders_qs.filter(customer_name__icontains=q)

    def serialize_order(o: Order):
        items = list(o.orderitem_set.all())
        total = len(items)
        ready = sum(1 for i in items if i.is_ready)
        
        is_ready = (total > 0 and ready == total)
        is_in_progress = (ready > 0 and not is_ready)
        is_new = (ready == 0)
        
        # --- NOWE: Metoda Płatności do API ---
        # Zakładamy, że masz pole get_payment_method_display w modelu (standard Django dla choices)
        pay_method_disp = o.get_payment_method_display() if o.payment_method else ""

        bar_info = o.get_bar_display()
        kitchen_info = o.get_kitchen_display()

        return {
            "id": o.id,
            "status": o.status,
            "is_takeaway": o.is_takeaway,
            "customer_name": o.customer_name,
            "created_at": o.created_at.isoformat(), 
            "created_at_display": o.created_at.strftime("%H:%M"),
            "total_price": float(o.total_price),
            "total_items": total,
            "ready_items": ready,
            "is_new": is_new,
            "is_in_progress": is_in_progress,
            "is_ready": is_ready,
            "is_paid": (o.status != "UNPAID"),
            
            "payment_method_display": pay_method_disp, # DODANO
            
            "delivered_by_bar_str": bar_info,
            "delivered_by_kitchen_str": kitchen_info,
            "delivered_by_bar": {'name': bar_info} if bar_info else None,
            "delivered_by_kitchen": {'name': kitchen_info} if kitchen_info else None,
        }

    orders = [serialize_order(o) for o in orders_qs]
    orders.sort(key=lambda x: (not x["is_ready"], x["created_at"]))

    selected = None
    if sel and sel.isdigit():
        o = orders_qs.filter(id=int(sel)).first()
        if o:
            selected = serialize_order(o)
            selected['delivered_by_bar'] = {'name': o.delivered_by_bar.name} if o.delivered_by_bar else None
            selected['delivered_by_kitchen'] = {'name': o.delivered_by_kitchen.name} if o.delivered_by_kitchen else None

            items = list(o.orderitem_set.all())
            product_names = list(set([item.product_name for item in items]))
            products_info = Product.objects.filter(name__in=product_names)
            
            product_data = { 
                p.name: {
                    'price': float(p.price), 
                    'category': p.category.name if p.category else "", 
                    'has_packaging_fee': p.has_packaging_fee,
                    'can_cashier_deliver': p.can_cashier_deliver
                } for p in products_info 
            }
            
            grouped_items = {}
            for item in items:
                name = item.product_name
                ingredients_objects = list(item.ingredients.all())
                extras_names = [ing.name for ing in ingredients_objects]
                extras_names.sort()
                extras_str = ", ".join(extras_names)
                
                p_info = product_data.get(name, {
                    'price': 0.00, 
                    'category': "", 
                    'has_packaging_fee': False,
                    'can_cashier_deliver': True
                })
                
                base_price = p_info['price']
                extras_cost = sum(float(ing.price) for ing in ingredients_objects)
                final_unit_price = base_price + extras_cost

                key = (name, extras_str, final_unit_price)
                if key not in grouped_items:
                    grouped_items[key] = {
                        "name": name,
                        "category": p_info['category'],
                        "extras": extras_names,
                        "total": 0,
                        "ready": 0,
                        "is_fully_ready": False,
                        "next_id": None, 
                        "all_ids": [],
                        "unit_price": final_unit_price,
                        "total_group_price": 0.00,
                        "has_packaging_fee": p_info['has_packaging_fee'],
                        "can_cashier_deliver": p_info['can_cashier_deliver']
                    }
                grouped_items[key]["total"] += 1
                grouped_items[key]["all_ids"].append(item.id)
                if item.is_ready:
                    grouped_items[key]["ready"] += 1
                elif grouped_items[key]["next_id"] is None:
                    grouped_items[key]["next_id"] = item.id

            result_groups = []
            for group in grouped_items.values():
                group["is_fully_ready"] = (group["total"] == group["ready"])
                group["total_group_price"] = group["unit_price"] * group["total"]
                result_groups.append(group)
            result_groups.sort(key=lambda x: (x["is_fully_ready"], x["name"]))
            selected["grouped_items"] = result_groups

    config = {
        "allow_delivery_before_payment": station.allow_delivery_before_payment
    }
    return JsonResponse({"orders": orders, "selected": selected, "config": config})


# --- AKCJE KASY (REDIRECTY / AJAX) ---

@require_POST
def cashier_create_order(request, station_slug):
    customer_name = request.POST.get("customer_name", "Klient").strip()
    if not customer_name: customer_name = "Klient"

    new_order = Order.objects.create(
        status='UNPAID',
        total_price=0,
        customer_name=customer_name,
        is_takeaway=False,
        created_at=timezone.now()
    )
    return redirect(get_referer(request) + f'?sel={new_order.id}')

@require_POST
def cashier_paid(request, order_id, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    
    with transaction.atomic():
        order = get_object_or_404(Order, id=order_id)
        
        # ODBIERAMY METODĘ PŁATNOŚCI Z JSON
        payment_method = 'CASH'
        try:
            body_data = json.loads(request.body)
            payment_method = body_data.get('payment_method', 'CASH')
        except:
            pass

        if order.status == "UNPAID":
            for item in order.orderitem_set.all():
                product = Product.objects.filter(name=item.product_name).first()
                if product:
                    current_stock = product.stock if product.stock is not None else 0
                    product.stock = current_stock - 1
                    if product.auto_block_when_zero and product.stock <= 0:
                        product.is_active = False
                    product.save()
            
            order.status = "NEW"
            order.payment_method = payment_method # ZAPIS METODY
            order.save(update_fields=["status", "payment_method"])
            
            # LOGOWANIE ZDARZENIA (ORDER_PAID)
            log_event(
                station, 
                'ORDER_PAID', 
                employee=leader, 
                order=order, 
                details=f"Opłacono: {payment_method}"
            )

    accept = request.headers.get("Accept", "")
    if "application/json" in accept or request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True, "status": order.status})
    return redirect(get_referer(request))

@require_POST
def cashier_done(request, station_slug, order_id):
    station = get_object_or_404(Station, slug=station_slug)
    order = get_object_or_404(Order, id=order_id)
    leader = get_current_leader(request, station_slug)

    if not station.allow_delivery_before_payment and order.status == 'UNPAID':
        return redirect(get_referer(request))

    order.station = station
    
    if leader:
        order.delivered_by_bar = leader
        team_data = get_station_team(request, station_slug)
        if team_data:
            members_ids = team_data.get('members_ids', [])
            members = Employee.objects.filter(id__in=members_ids)
            order.bar_team.set(members)

    bar_products_names = Product.objects.filter(can_cashier_deliver=True).values_list('name', flat=True)
    
    items_to_update = OrderItem.objects.filter(
        order=order, 
        is_ready=False, 
        product_name__in=bar_products_names
    )
    
    for item in items_to_update:
        item.is_ready = True
        if leader:
            item.completed_by = leader
        item.save()

    if not OrderItem.objects.filter(order=order, is_ready=False).exists():
        order.status = 'DONE'
        if leader:
             log_event(station, 'OTHER', employee=leader, order=order, details="Zamówienie zakończone przez Kasjera.")
        order.save() 
    else:
        order.save() 
        
    return redirect(get_referer(request))

@require_POST
def cashier_item_done(request, station_slug, item_id):
    station = get_object_or_404(Station, slug=station_slug)
    
    with transaction.atomic():
        try:
            item = OrderItem.objects.select_for_update().get(id=item_id)
        except OrderItem.DoesNotExist:
            return redirect(get_referer(request))

        if not station.allow_delivery_before_payment and item.order.status == 'UNPAID':
            if "application/json" in request.headers.get("Accept", ""):
                return JsonResponse({'error': 'Wymagana płatność przed wydaniem!'}, status=403)
            return redirect(get_referer(request))

        if item.is_ready:
            sibling = find_next_available_sibling(item)
            if sibling:
                item = sibling
            else:
                accept = request.headers.get("Accept", "")
                if "application/json" in accept or request.headers.get("X-Requested-With") == "fetch":
                    return JsonResponse({"ok": True})
                return redirect(get_referer(request))

        leader = get_current_leader(request, station_slug)
        item.is_ready = True
        if leader:
            item.completed_by = leader
        item.save()
        
        # LOGOWANIE ZDARZENIA (ITEM_DONE - KASA)
        log_event(
            station, 
            'ITEM_DONE', 
            employee=leader, 
            order=item.order, 
            details=f"Wydano produkt (Bar): {item.product_name}"
        )

    accept = request.headers.get("Accept", "")
    if "application/json" in accept or request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
    return redirect(get_referer(request))


@require_POST
def cashier_group_done(request, station_slug, item_id):
    try:
        station = get_object_or_404(Station, slug=station_slug)
        ref_item = OrderItem.objects.get(id=item_id)
        
        if not station.allow_delivery_before_payment and ref_item.order.status == 'UNPAID':
            return JsonResponse({'status': 'error', 'message': 'Wymagana płatność przed wydaniem!'}, status=403)
        
        leader = get_current_leader(request, station_slug)
        
        order = ref_item.order
        ref_ingredients = set(ref_item.ingredients.all())
        siblings = OrderItem.objects.filter(order=order, product_name=ref_item.product_name, is_ready=False).prefetch_related('ingredients')
        count = 0
        for item in siblings:
            if set(item.ingredients.all()) == ref_ingredients:
                item.is_ready = True
                if leader:
                    item.completed_by = leader
                item.save()
                count += 1
        
        # LOGOWANIE (GROUP)
        if count > 0 and leader:
             log_event(
                station, 
                'ITEM_DONE', 
                employee=leader, 
                order=order, 
                details=f"Wydano grupowo (Bar): {ref_item.product_name} ({count} szt.)"
            )
                
        return JsonResponse({'status': 'ok', 'updated': count})
    except OrderItem.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Item not found'}, status=404)

@require_POST
def cashier_cancel(request, order_id, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    order = get_object_or_404(Order, id=order_id)
    
    reason = request.POST.get('reason', '').strip()
    order.status = 'CANCELLED'
    order.cancel_reason = reason if reason else "Brak powodu"
    order.save()
    
    # LOGOWANIE (CANCEL)
    if leader:
        log_event(station, 'OTHER', employee=leader, order=order, details=f"Anulowano zamówienie. Powód: {order.cancel_reason}")

    return redirect(get_referer(request))

@require_POST
def cashier_split_order(request, order_id, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    
    old_order = get_object_or_404(Order, id=order_id)
    if old_order.status != 'UNPAID':
        return JsonResponse({'status': 'error', 'message': 'Można dzielić tylko nieopłacone zamówienia'}, status=403)

    try:
        data = json.loads(request.body)
        item_ids = data.get('item_ids', [])
        if not item_ids:
            return JsonResponse({'status': 'error', 'message': 'Nie wybrano produktów'}, status=400)

        with transaction.atomic():
            new_order = Order.objects.create(
                status='UNPAID',
                customer_name=f"{old_order.customer_name} (cz. 2)",
                is_takeaway=old_order.is_takeaway,
                note=old_order.note,
                created_at=timezone.now()
            )
            items_to_move = OrderItem.objects.filter(id__in=item_ids, order=old_order)
            if not items_to_move.exists():
                raise Exception("Nie znaleziono wybranych pozycji w tym zamówieniu")

            items_to_move.update(order=new_order)
            recalculate_order_total(old_order)
            recalculate_order_total(new_order)

            # LOGOWANIE (SPLIT)
            log_event(station, 'ORDER_SPLIT', employee=leader, order=old_order, details=f"Wydzielono produkty do nowego zam. #{new_order.id}")
            log_event(station, 'ORDER_SPLIT', employee=leader, order=new_order, details=f"Utworzono z zam. #{old_order.id}")

            if not old_order.orderitem_set.exists():
                old_order.delete()
                # Jeśli stare zniknęło, log dla niego też może zniknąć (cascade), 
                # ale to szczegół. W tym flow raczej rzadko kasuje się cały stary.
                return JsonResponse({'status': 'ok', 'new_order_id': new_order.id})

        return JsonResponse({'status': 'ok', 'new_order_id': new_order.id})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@require_POST
def cashier_edit_qty(request, order_id, station_slug):
    order = get_object_or_404(Order, id=order_id)
    if order.status != 'UNPAID':
        return JsonResponse({'status': 'error', 'message': 'Edycja zablokowana'}, status=403)
    try:
        data = json.loads(request.body)
        product_name = data.get('product_name')
        action = data.get('action')
        extras_names = data.get('extras', [])
        extras_names.sort()

        if action == 'inc':
            product = Product.objects.filter(name=product_name).first()
            if not product:
                return JsonResponse({'status': 'error', 'message': 'Produkt nie istnieje'}, status=404)
            item = OrderItem.objects.create(order=order, product_name=product_name, is_ready=False)
            if extras_names:
                ingredients = Ingredient.objects.filter(name__in=extras_names)
                item.ingredients.set(ingredients)
        elif action == 'dec':
            items = OrderItem.objects.filter(order=order, product_name=product_name)
            item_to_remove = None
            for item in items:
                item_ingredients = list(item.ingredients.values_list('name', flat=True))
                item_ingredients.sort()
                if item_ingredients == extras_names:
                    item_to_remove = item
                    break
            if item_to_remove: item_to_remove.delete()
        
        recalculate_order_total(order)
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@require_POST
def cashier_add_product(request, order_id, station_slug):
    order = get_object_or_404(Order, id=order_id)
    if order.status != 'UNPAID':
        return JsonResponse({'status': 'error', 'message': 'Zamówienie opłacone'}, status=403)
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        extras_names = data.get('extras', [])
        product = get_object_or_404(Product, id=product_id)
        item = OrderItem.objects.create(order=order, product_name=product.name, is_ready=False)
        if extras_names:
            ingredients = Ingredient.objects.filter(name__in=extras_names)
            item.ingredients.set(ingredients)
        recalculate_order_total(order)
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def cashier_search_products(request, station_slug):
    q = (request.GET.get('q') or "").strip()
    products = Product.objects.all().prefetch_related('possible_ingredients').order_by('name')
    if q: products = products.filter(name__icontains=q)
    results = []
    for p in products[:50]:
        ingredients_data = []
        if p.is_customizable:
            for ing in p.possible_ingredients.all():
                if ing.is_available:
                    ingredients_data.append({'name': ing.name, 'price': float(ing.price)})
        results.append({
            'id': p.id,
            'name': p.name,
            'price': float(p.price),
            'is_active': p.is_active,
            'is_customizable': p.is_customizable,
            'ingredients': ingredients_data
        })
    return JsonResponse({'results': results})

@require_POST
def cashier_toggle_product(request, product_id, station_slug):
    product = get_object_or_404(Product, id=product_id)
    product.is_active = not product.is_active
    product.save()
    return JsonResponse({'status': 'ok', 'id': product.id, 'is_active': product.is_active})

@require_POST
def cashier_toggle_takeaway(request, order_id, station_slug):
    order = get_object_or_404(Order, id=order_id)
    if order.status != 'UNPAID':
        return JsonResponse({'status': 'error', 'message': 'Zmiana możliwa tylko dla nieopłaconych'}, status=403)
    order.is_takeaway = not order.is_takeaway
    order.orderitem_set.filter(product_name__iexact="Opakowanie").delete()
    if order.is_takeaway:
        current_item_names = list(
            order.orderitem_set.exclude(product_name__iexact="Opakowanie").values_list('product_name', flat=True)
        )
        if current_item_names:
            products_needing_fee = set(
                Product.objects.filter(name__in=current_item_names, has_packaging_fee=True).values_list('name', flat=True)
            )
            count_needed = sum(1 for name in current_item_names if name in products_needing_fee)
            if count_needed > 0:
                pkg_product = Product.objects.filter(name__iexact="Opakowanie").first()
                pkg_name = pkg_product.name if pkg_product else "Opakowanie"
                new_items = [OrderItem(order=order, product_name=pkg_name, is_ready=False) for _ in range(count_needed)]
                OrderItem.objects.bulk_create(new_items)
    order.save()
    recalculate_order_total(order)
    return JsonResponse({'status': 'ok', 'is_takeaway': order.is_takeaway})


def client_order_board(request):
    orders_qs = Order.objects.filter(status='NEW').prefetch_related('orderitem_set').order_by('created_at')
    now = timezone.now()
    orders = []
    for order in orders_qs:
        items = list(order.orderitem_set.all())
        total = len(items)
        ready = sum(1 for i in items if i.is_ready)
        order.total_items = total
        order.ready_items = ready
        order.is_fully_ready = (total > 0 and ready == total)
        delta = now - order.created_at
        order.wait_minutes = int(delta.total_seconds() // 60)
        orders.append(order)
    orders.sort(key=lambda o: (not o.is_fully_ready, o.created_at))
    return render(request, "client_board.html", {"orders": orders})


def build_grouped_items(order: Order, product_categories: dict):
    items = list(order.orderitem_set.all())
    grouped = {}
    for item in items:
        name = item.product_name
        category_name = product_categories.get(name, "")
        key = (category_name, name)
        if key not in grouped:
            grouped[key] = {
                "name": name,
                "category": category_name,
                "total": 0,
                "ready": 0,
                "next_id": None,
                "is_fully_ready": False,
            }
        grouped[key]["total"] += 1
        if item.is_ready:
            grouped[key]["ready"] += 1
        elif grouped[key]["next_id"] is None:
            grouped[key]["next_id"] = item.id
    final_list = []
    for val in grouped.values():
        val["is_fully_ready"] = (val["ready"] == val["total"])
        final_list.append(val)
    final_list.sort(key=lambda x: ((x["category"] or ""), x["name"]))
    return final_list