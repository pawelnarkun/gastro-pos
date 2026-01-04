# core/views/kitchen.py
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.db import transaction
# Dodano import F tutaj:
from django.db.models import Q, F
from django.db.models.functions import TruncDate
import datetime

from core.models import Product, Order, OrderItem, Station, Employee, GlobalSettings, RawMaterial, StationLog
from .utils import (
    log_event, get_station_team, get_current_leader, get_referer,
    get_product_category_map, get_product_map, find_next_available_sibling)

from django.utils import timezone


def kitchen_index(request, station_slug):
    # 1. Pobieramy stację na samym początku
    station = get_object_or_404(Station, slug=station_slug, station_type='KITCHEN')

    # --- LOGIKA AUTO-WYLOGOWANIA ---
    if station.auto_logout_time:
        now = timezone.localtime(timezone.now())
        # Ustawiamy dzisiejszą datę z godziną wylogowania
        logout_dt = now.replace(
            hour=station.auto_logout_time.hour, 
            minute=station.auto_logout_time.minute, 
            second=0, 
            microsecond=0
        )
        
        # Jeśli aktualna godzina jest większa niż godzina wylogowania
        if now > logout_dt:
            # Sprawdzamy ostatnie logowanie
            last_login = StationLog.objects.filter(
                station=station, action='LOGIN'
            ).order_by('-timestamp').first()

            # Jeśli zalogowano się PRZED godziną wylogowania -> Wyloguj
            if last_login and last_login.timestamp < logout_dt:
                request.session.flush()
                return redirect('station_login', station_slug=station_slug)
    # -------------------------------

    leader = get_current_leader(request, station_slug)
    if not leader:
        return redirect('station_login', station_slug=station_slug)
    
    team_data = get_station_team(request, station_slug)
    team_members = []
    if team_data:
        team_members = Employee.objects.filter(id__in=team_data['members_ids'])

    # (Tu usunąłem zduplikowane pobieranie station, bo jest na górze)
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
                if not product or product.can_cashier_deliver: has_bar_pending = True
                if not product or product.can_kitchen_deliver: has_kitchen_pending = True

            ingredients_list = list(item.ingredients.values_list('name', flat=True))
            ingredients_list.sort()
            extras_str = ", ".join(ingredients_list)
            
            category_name = product_categories.get(name, "")
            key = (category_name, name, extras_str) 

            if key not in grouped:
                grouped[key] = {
                    'name': name, 'extras': extras_str, 'category': category_name,
                    'total': 0, 'ready': 0, 'next_id': None, 'is_fully_ready': False,
                    'product_id': product.id if product else None,
                    'is_active': product.is_active if product else True,
                    'can_kitchen_deliver': product.can_kitchen_deliver if product else True,
                }

            grouped[key]['total'] += 1
            if item.is_ready: grouped[key]['ready'] += 1
            elif grouped[key]['next_id'] is None: grouped[key]['next_id'] = item.id

        final_list = []
        for val in grouped.values():
            if val['ready'] == val['total']: val['is_fully_ready'] = True
            final_list.append(val)

        final_list.sort(key=lambda x: ((x['category'] or ''), x['name']))
        order.custom_items = final_list
        order.has_kitchen_pending = has_kitchen_pending
        order.has_bar_pending = has_bar_pending

        if has_kitchen_pending:
            active_kitchen_orders.append(order)

    return render(request, 'kitchen.html', {
        'orders': active_kitchen_orders, 'station': station,
        'leader': leader, 'team_members': team_members,
    })

def kitchen_products(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(Q(limit_availability=station) | Q(limit_availability=None)).select_related("category")
    if query:
        products = products.filter(Q(name__icontains=query) | Q(category__name__icontains=query))
    products = products.order_by("category__name", "name")
    settings = GlobalSettings.load()
    return render(request, "kitchen_products.html", {
        "products": products, "query": query, "station": station, "settings": settings,
    })

def kitchen_history(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug, station_type='KITCHEN')
    dates_qs = Order.objects.filter(Q(status='DONE') | Q(status='CANCELLED')).annotate(just_date=TruncDate('created_at')).values_list('just_date', flat=True).distinct().order_by('-just_date')
    available_dates = list(dates_qs)
    
    selected_date_str = request.GET.get('date')
    selected_date = None
    if selected_date_str:
        try: selected_date = datetime.datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError: pass
        
    if not selected_date and available_dates:
        selected_date = available_dates[0]

    orders = Order.objects.filter(Q(status='DONE') | Q(status='CANCELLED')).prefetch_related('orderitem_set', 'orderitem_set__ingredients', 'orderitem_set__completed_by', 'delivered_by_kitchen', 'delivered_by_bar', 'kitchen_team', 'bar_team').order_by('-created_at')
    
    if selected_date: orders = orders.filter(created_at__date=selected_date)

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
                if item.product_name in bar_products_names: bar_members.add(name)
                else: kitchen_members.add(name)

        if k_leader_name:
            if k_leader_name in kitchen_members: kitchen_members.remove(k_leader_name)
            if kitchen_members: order.kitchen_workers_display = f"{k_leader_name} (+ {', '.join(sorted(kitchen_members))})"
            else: order.kitchen_workers_display = k_leader_name
        else: order.kitchen_workers_display = ", ".join(sorted(kitchen_members))

        if b_leader_name:
            if b_leader_name in bar_members: bar_members.remove(b_leader_name)
            if bar_members: order.bar_workers_display = f"{b_leader_name} (+ {', '.join(sorted(bar_members))})"
            else: order.bar_workers_display = b_leader_name
        else: order.bar_workers_display = ", ".join(sorted(bar_members))

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
            aggregated.append({'name': name, 'category': cat, 'extras': ext, 'count': count, 'worker': worker})
        order.aggregated_items = aggregated

    return render(request, 'kitchen_history.html', {
        'orders': orders, 'available_dates': available_dates, 'current_date': selected_date, 'station': station
    })

def kitchen_item_done(request, station_slug, item_id):
    station = get_object_or_404(Station, slug=station_slug)
    with transaction.atomic():
        try: item = OrderItem.objects.select_for_update().get(id=item_id)
        except OrderItem.DoesNotExist: return redirect(get_referer(request))
        if item.is_ready:
            sibling = find_next_available_sibling(item)
            if sibling: item = sibling
            else: return redirect(get_referer(request))
        leader = get_current_leader(request, station_slug)
        item.is_ready = True
        if leader: item.completed_by = leader
        item.save()
        log_event(station, 'ITEM_DONE', employee=leader, order=item.order, details=f"Wydano produkt: {item.product_name}")
    return redirect(get_referer(request))

def kitchen_group_done(request, station_slug, item_id):
    station = get_object_or_404(Station, slug=station_slug)
    ref_item = get_object_or_404(OrderItem, id=item_id)
    order = ref_item.order
    ref_ingredients = set(ref_item.ingredients.all())
    leader = get_current_leader(request, station_slug)
    siblings = OrderItem.objects.filter(order=order, product_name=ref_item.product_name, is_ready=False).prefetch_related('ingredients')
    count = 0
    for item in siblings:
        if set(item.ingredients.all()) == ref_ingredients:
            item.is_ready = True
            if leader: item.completed_by = leader
            item.save()
            count += 1
    if count > 0 and leader:
        log_event(station, 'ITEM_DONE', employee=leader, order=order, details=f"Wydano grupowo: {ref_item.product_name} ({count} szt.)")
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
            members = Employee.objects.filter(id__in=team_data.get('members_ids', []))
            order.kitchen_team.set(members)
    kitchen_products_names = Product.objects.filter(can_kitchen_deliver=True).values_list('name', flat=True)
    items_to_update = OrderItem.objects.filter(order=order, is_ready=False, product_name__in=kitchen_products_names)
    for item in items_to_update:
        item.is_ready = True
        if leader: item.completed_by = leader
        item.save()
    if not OrderItem.objects.filter(order=order, is_ready=False).exists():
        order.status = 'DONE'
        if leader: log_event(station, 'OTHER', employee=leader, order=order, details="Zamówienie w pełni zrealizowane (DONE).")
    order.save() 
    return redirect(get_referer(request))

def kitchen_add_note(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        order.note = request.POST.get('note')
        order.save()
    return redirect(get_referer(request))

def kitchen_add_history_note(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        order.history_note = request.POST.get('history_note')
        order.save()
    return redirect(get_referer(request))

def kitchen_toggle_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.is_active = not product.is_active
    product.save()
    return redirect(get_referer(request))

@require_POST
def kitchen_update_stock(request, product_id, station_slug=None): 
    product = get_object_or_404(Product, id=product_id)
    action = request.POST.get("action")
    
    old_stock = product.stock if product.stock is not None else 0
    produced_quantity = 0 

    with transaction.atomic():
        # Blokujemy produkt do edycji
        product = Product.objects.select_for_update().get(id=product_id)

        if action == "inc":
            product.stock = (product.stock or 0) + 1
            produced_quantity = 1 
            
        elif action == "dec":
            product.stock = max((product.stock or 0) - 1, 0)
            
        elif action == "set":
            try:
                target_val = int(request.POST.get("stock", product.stock))
            except (TypeError, ValueError):
                target_val = product.stock
            
            if target_val < 0: target_val = 0
            
            if target_val > old_stock:
                produced_quantity = target_val - old_stock
            
            product.stock = target_val

        product.save()

        # --- LOGIKA RECEPTUR (POPRAWIONA) ---
        # Usuwamy warunek 'product.uses_raw_materials'. 
        # System sam sprawdzi, czy istnieją powiązane składniki.
        if produced_quantity > 0:
            # Używamy nazwy relacji z Twojego models.py: related_name='recipe_items'
            recipes = product.recipe_items.select_related('raw_material').all()
            
            if recipes.exists():
                for item in recipes:
                    # item.quantity to decimal, produced_quantity to int
                    total_needed = item.quantity * produced_quantity
                    
                    # Aktualizacja stanu półproduktu (RawMaterial)
                    raw_mat = item.raw_material
                    # F() obsługuje operacje na poziomie bazy danych
                    raw_mat.stock = F('stock') - total_needed
                    raw_mat.save()

    return redirect(get_referer(request))

# ... (istniejące importy)

def kitchen_raw_materials(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    # Pobieramy wszystkie półprodukty
    materials = RawMaterial.objects.all().order_by('name')
    
    # Obsługa wyszukiwarki
    query = request.GET.get("q", "").strip()
    if query:
        materials = materials.filter(name__icontains=query)

    settings = GlobalSettings.load()
    
    return render(request, "kitchen_raw_materials.html", {
        "materials": materials,
        "query": query,
        "station": station,
        "settings": settings,
    })

@require_POST
def kitchen_update_raw_material(request, material_id):
    material = get_object_or_404(RawMaterial, id=material_id)
    action = request.POST.get("action")
    
    with transaction.atomic():
        # Blokada rekordu
        material = RawMaterial.objects.select_for_update().get(id=material_id)
        
        if action == "inc":
            # Przy półproduktach często operujemy na ułamkach (np. kg), 
            # ale tutaj założymy +1 jednostka. Możesz zmienić na 0.1 lub 0.5 wg potrzeb.
            material.stock = F('stock') + 1
            
        elif action == "dec":
            # Zabezpieczenie przed ujemnym stanem (opcjonalne)
            # Używamy Case/When lub po prostu pozwalamy na minus, jeśli tak wolisz.
            # Tu prosta wersja z Pythonowym max, ale dla F() trzeba by użyć Greatest
            # Dla uproszczenia przy F() często pozwala się zejść na minus lub robi reset:
            material.stock = F('stock') - 1
            
        elif action == "set":
            try:
                # Zamiana przecinka na kropkę dla float
                val_str = request.POST.get("stock", "").replace(",", ".")
                target_val = float(val_str)
            except (ValueError, TypeError):
                target_val = material.stock
            
            material.stock = target_val

        material.save()

    return redirect(get_referer(request))

@require_POST
def kitchen_toggle_autoblock(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.auto_block_when_zero = not product.auto_block_when_zero
    product.save()
    return redirect(get_referer(request))