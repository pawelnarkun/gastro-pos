# core/views/cashier.py
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.db.models import F
import json

from core.models import Product, Order, OrderItem, Station, Ingredient, Employee, StationLog
from django.utils import timezone
from .utils import (
    log_event, get_station_team, get_current_leader, get_referer,
    get_product_category_map, build_grouped_items, recalculate_order_total,
    find_next_available_sibling
)



@ensure_csrf_cookie
def cashier_index(request, station_slug):
    # 1. Pobieramy stację na samym początku
    station = get_object_or_404(Station, slug=station_slug, station_type='CASHIER')

    # --- LOGIKA AUTO-WYLOGOWANIA ---
    if station.auto_logout_time:
        now = timezone.localtime(timezone.now())
        logout_dt = now.replace(
            hour=station.auto_logout_time.hour, 
            minute=station.auto_logout_time.minute, 
            second=0, 
            microsecond=0
        )
        
        if now > logout_dt:
            last_login = StationLog.objects.filter(
                station=station, action='LOGIN'
            ).order_by('-timestamp').first()

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

    # (Usunięto duplikat station)
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
        "orders": orders, "selected": selected, "q": q,
        "station": station, "leader": leader, "team_members": team_members,
    })

def cashier_api(request, station_slug):
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
                    order.payment_cash = float(cash_amount)
                    order.payment_card = float(card_amount)
                    if order.status == "UNPAID":
                        is_kiosk_order = (order.station and order.station.station_type == 'KIOSK')
                        for item in order.orderitem_set.all():
                            product = Product.objects.filter(name=item.product_name).first()
                            if product:
                                current_stock = product.stock if product.stock is not None else 0
                                product.stock = current_stock - 1
                                if product.auto_block_when_zero and product.stock <= 0:
                                    product.is_active = False
                                product.save()

                        order.status = "NEW"
                        order.payment_method = "MIXED" 
                        order.save()
                        log_event(station, 'ORDER_PAID', employee=leader, order=order,
                            details=f"Płatność Mieszana: Gotówka {order.payment_cash}, Karta {order.payment_card}")
                return JsonResponse({'status': 'ok'})
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    station = get_object_or_404(Station, slug=station_slug)
    q = (request.GET.get("q") or "").strip()
    sel = request.GET.get("sel")
    
    orders_qs = Order.objects.exclude(Q(status="DONE") | Q(status="CANCELLED")).prefetch_related("orderitem_set", "orderitem_set__ingredients").order_by("created_at")

    if q:
        if q.isdigit(): orders_qs = orders_qs.filter(id=int(q))
        else: orders_qs = orders_qs.filter(customer_name__icontains=q)

    def serialize_order(o: Order):
        items = list(o.orderitem_set.all())
        total = len(items)
        ready = sum(1 for i in items if i.is_ready)
        is_ready = (total > 0 and ready == total)
        is_in_progress = (ready > 0 and not is_ready)
        is_new = (ready == 0)
        pay_method_disp = o.get_payment_method_display() if o.payment_method else ""
        bar_info = o.get_bar_display()
        kitchen_info = o.get_kitchen_display()

        return {
            "id": o.id, "status": o.status, "is_takeaway": o.is_takeaway,
            "customer_name": o.customer_name, "created_at": o.created_at.isoformat(), 
            "created_at_display": o.created_at.strftime("%H:%M"), "total_price": float(o.total_price),
            "total_items": total, "ready_items": ready, "is_new": is_new,
            "is_in_progress": is_in_progress, "is_ready": is_ready, "is_paid": (o.status != "UNPAID"),
            "payment_method_display": pay_method_disp,
            "delivered_by_bar_str": bar_info, "delivered_by_kitchen_str": kitchen_info,
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
                    'price': float(p.price), 'category': p.category.name if p.category else "", 
                    'has_packaging_fee': p.has_packaging_fee, 'can_cashier_deliver': p.can_cashier_deliver
                } for p in products_info 
            }
            
            grouped_items = {}
            for item in items:
                name = item.product_name
                ingredients_objects = list(item.ingredients.all())
                extras_names = [ing.name for ing in ingredients_objects]
                extras_names.sort()
                extras_str = ", ".join(extras_names)
                p_info = product_data.get(name, {'price': 0.00, 'category': "", 'has_packaging_fee': False, 'can_cashier_deliver': True})
                base_price = p_info['price']
                extras_cost = sum(float(ing.price) for ing in ingredients_objects)
                final_unit_price = base_price + extras_cost

                key = (name, extras_str, final_unit_price)
                if key not in grouped_items:
                    grouped_items[key] = {
                        "name": name, "category": p_info['category'], "extras": extras_names,
                        "total": 0, "ready": 0, "is_fully_ready": False, "next_id": None, 
                        "all_ids": [], "unit_price": final_unit_price, "total_group_price": 0.00,
                        "has_packaging_fee": p_info['has_packaging_fee'], "can_cashier_deliver": p_info['can_cashier_deliver']
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

    config = { "allow_delivery_before_payment": station.allow_delivery_before_payment }
    return JsonResponse({"orders": orders, "selected": selected, "config": config})

@require_POST
def cashier_create_order(request, station_slug):
    customer_name = request.POST.get("customer_name", "Klient").strip()
    if not customer_name: customer_name = "Klient"
    new_order = Order.objects.create(status='UNPAID', total_price=0, customer_name=customer_name, is_takeaway=False, created_at=timezone.now())
    return redirect(get_referer(request) + f'?sel={new_order.id}')

@require_POST
def cashier_paid(request, order_id, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    with transaction.atomic():
        order = get_object_or_404(Order, id=order_id)
        payment_method = 'CASH'
        try:
            body_data = json.loads(request.body)
            payment_method = body_data.get('payment_method', 'CASH')
        except: pass

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
            order.payment_method = payment_method
            order.save(update_fields=["status", "payment_method"])
            log_event(station, 'ORDER_PAID', employee=leader, order=order, details=f"Opłacono: {payment_method}")

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
            members = Employee.objects.filter(id__in=team_data.get('members_ids', []))
            order.bar_team.set(members)

    bar_products_names = Product.objects.filter(can_cashier_deliver=True).values_list('name', flat=True)
    items_to_update = OrderItem.objects.filter(order=order, is_ready=False, product_name__in=bar_products_names)
    for item in items_to_update:
        item.is_ready = True
        if leader: item.completed_by = leader
        item.save()

    if not OrderItem.objects.filter(order=order, is_ready=False).exists():
        order.status = 'DONE'
        if leader: log_event(station, 'OTHER', employee=leader, order=order, details="Zamówienie zakończone przez Kasjera.")
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
            if sibling: item = sibling
            else:
                accept = request.headers.get("Accept", "")
                if "application/json" in accept or request.headers.get("X-Requested-With") == "fetch":
                    return JsonResponse({"ok": True})
                return redirect(get_referer(request))

        leader = get_current_leader(request, station_slug)
        item.is_ready = True
        if leader: item.completed_by = leader
        item.save()
        log_event(station, 'ITEM_DONE', employee=leader, order=item.order, details=f"Wydano produkt (Bar): {item.product_name}")

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
                if leader: item.completed_by = leader
                item.save()
                count += 1
        if count > 0 and leader:
             log_event(station, 'ITEM_DONE', employee=leader, order=order, details=f"Wydano grupowo (Bar): {ref_item.product_name} ({count} szt.)")
        return JsonResponse({'status': 'ok', 'updated': count})
    except OrderItem.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Item not found'}, status=404)

@require_POST
def cashier_cancel(request, order_id, station_slug):
    station = get_object_or_404(Station, slug=station_slug)
    leader = get_current_leader(request, station_slug)
    
    with transaction.atomic():
        order = get_object_or_404(Order, id=order_id)
        
        # Jeśli już anulowane/wydane - stop
        if order.status in ['CANCELLED', 'DONE']:
            return redirect(get_referer(request))

        # --- LOGIKA ZWROTU TOWARU ---
        # Sprawdzamy, czy zamówienie "zjadło" już towar.
        # Kiosk zdejmuje towar od razu (nawet przy UNPAID).
        # Kasa zdejmuje towar dopiero przy płatności (NEW/PAID).
        
        should_return_stock = False
        
        # 1. Jeśli zamówienie jest już opłacone (NEW/READY/itp), to na pewno zdjęło stan.
        if order.status != 'UNPAID':
            should_return_stock = True
            
        # 2. Jeśli jest UNPAID, ale pochodzi z kiosku (nie ma przypisanego lidera kasy/kuchni przy utworzeniu, 
        #    lub możemy to poznać po braku station - ale kiosk przypisuje stację).
        #    Najpewniej: sprawdzić czy stacja to Kiosk.
        elif order.station and order.station.station_type == 'KIOSK':
            should_return_stock = True

        if should_return_stock:
            # Pobieramy nazwy produktów, żeby znaleźć je w bazie
            # (Uwaga: OrderItem trzyma nazwę, a nie klucz obcy do Product, co utrudnia sprawę przy zmianie nazw,
            # ale przy założeniu, że nazwy są unikalne i stałe, zadziała).
            for item in order.orderitem_set.all():
                # Ignorujemy opakowania i inne dodatki niebędące produktem głównym (chyba że chcesz zwracać też opakowania)
                if item.product_name == "Opakowanie":
                    continue
                    
                product = Product.objects.filter(name=item.product_name).first()
                if product:
                    # Zwrot towaru
                    product.stock = (product.stock or 0) + 1
                    
                    # Odblokowanie produktu w kiosku (jeśli był zablokowany przez brak stanu)
                    if product.auto_block_when_zero and product.stock > 0:
                        product.is_active = True
                        
                    product.save()
                    
                    # LOGIKA PÓŁPRODUKTÓW (Jeśli produkt ma składniki, zwracamy je również)
                    # Zakładamy, że składniki schodzą w momencie zdjęcia produktu głównego.
                    recipes = product.recipe_items.select_related('raw_material').all()
                    for recipe in recipes:
                         # raw_material.stock += recipe.quantity * 1
                         recipe.raw_material.stock = F('stock') + recipe.quantity
                         recipe.raw_material.save()


        reason = request.POST.get('reason', '').strip()
        order.status = 'CANCELLED'
        order.cancel_reason = reason if reason else "Brak powodu"
        order.save()
        
        if leader: 
            log_event(station, 'OTHER', employee=leader, order=order, 
                      details=f"Anulowano zamówienie #{order.id}. Powód: {order.cancel_reason} {'(Zwrócono towar)' if should_return_stock else ''}")
            
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
        if not item_ids: return JsonResponse({'status': 'error', 'message': 'Nie wybrano produktów'}, status=400)
        with transaction.atomic():
            new_order = Order.objects.create(status='UNPAID', customer_name=f"{old_order.customer_name} (cz. 2)", is_takeaway=old_order.is_takeaway, note=old_order.note, created_at=timezone.now())
            items_to_move = OrderItem.objects.filter(id__in=item_ids, order=old_order)
            items_to_move.update(order=new_order)
            recalculate_order_total(old_order)
            recalculate_order_total(new_order)
            log_event(station, 'ORDER_SPLIT', employee=leader, order=old_order, details=f"Wydzielono produkty do nowego zam. #{new_order.id}")
            log_event(station, 'ORDER_SPLIT', employee=leader, order=new_order, details=f"Utworzono z zam. #{old_order.id}")
            if not old_order.orderitem_set.exists(): old_order.delete()
        return JsonResponse({'status': 'ok', 'new_order_id': new_order.id})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@require_POST
def cashier_edit_qty(request, order_id, station_slug):
    order = get_object_or_404(Order, id=order_id)
    if order.status != 'UNPAID': return JsonResponse({'status': 'error', 'message': 'Edycja zablokowana'}, status=403)
    try:
        data = json.loads(request.body)
        product_name = data.get('product_name')
        action = data.get('action')
        extras_names = data.get('extras', [])
        extras_names.sort()

        if action == 'inc':
            product = Product.objects.filter(name=product_name).first()
            if not product: return JsonResponse({'status': 'error', 'message': 'Produkt nie istnieje'}, status=404)
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
    if order.status != 'UNPAID': return JsonResponse({'status': 'error', 'message': 'Zamówienie opłacone'}, status=403)
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
            'id': p.id, 'name': p.name, 'price': float(p.price), 'is_active': p.is_active,
            'is_customizable': p.is_customizable, 'ingredients': ingredients_data
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
    if order.status != 'UNPAID': return JsonResponse({'status': 'error', 'message': 'Zmiana możliwa tylko dla nieopłaconych'}, status=403)
    order.is_takeaway = not order.is_takeaway
    order.orderitem_set.filter(product_name__iexact="Opakowanie").delete()
    if order.is_takeaway:
        current_item_names = list(order.orderitem_set.exclude(product_name__iexact="Opakowanie").values_list('product_name', flat=True))
        if current_item_names:
            products_needing_fee = set(Product.objects.filter(name__in=current_item_names, has_packaging_fee=True).values_list('name', flat=True))
            count_needed = sum(1 for name in current_item_names if name in products_needing_fee)
            if count_needed > 0:
                pkg_product = Product.objects.filter(name__iexact="Opakowanie").first()
                pkg_name = pkg_product.name if pkg_product else "Opakowanie"
                new_items = [OrderItem(order=order, product_name=pkg_name, is_ready=False) for _ in range(count_needed)]
                OrderItem.objects.bulk_create(new_items)
    order.save()
    recalculate_order_total(order)
    return JsonResponse({'status': 'ok', 'is_takeaway': order.is_takeaway})