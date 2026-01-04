# core/views/kiosk.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Q
import json

from core.models import Product, Order, OrderItem, Category, Station, Ingredient, Allergen
from .utils import get_packaging_price, log_event

@ensure_csrf_cookie
def kiosk_index(request, station_slug):
    station = get_object_or_404(Station, slug=station_slug, station_type='KIOSK')
    products_qs = Product.objects.filter(Q(limit_availability=station) | Q(limit_availability=None), is_active=True)
    categories = Category.objects.filter(products__in=products_qs).distinct()
    current_pkg_price = get_packaging_price()
    return render(request, 'kiosk_home.html', {
        'categories': categories, 'packaging_price': current_pkg_price, 'station': station, 
    })

@ensure_csrf_cookie
def kiosk_category(request, station_slug, category_id):
    station = get_object_or_404(Station, slug=station_slug, station_type='KIOSK')
    category = get_object_or_404(Category, id=category_id)
    products = category.products.filter(Q(limit_availability=station) | Q(limit_availability=None), is_active=True).prefetch_related('possible_ingredients', 'allergens')
    allergens_list = Allergen.objects.all().order_by('code')
    current_pkg_price = get_packaging_price()
    return render(request, 'kiosk_category.html', {
        'category': category, 'products': products, 'packaging_price': current_pkg_price,
        'station': station, 'allergens_list': allergens_list,
    })

@require_POST
def api_create_order(request):
    try:
        data = json.loads(request.body)
        raw_items = data.get('items', [])
        customer_name = data.get('customer_name', 'Klient')
        is_takeaway = data.get('is_takeaway', False)
        payment_choice = data.get('payment_type', 'COUNTER')
        station_slug = data.get('station_slug')
        
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
                initial_status = 'NEW'
                db_payment_method = 'CARD'
            
            order = Order.objects.create(
                total_price=0, status=initial_status, payment_method=db_payment_method, 
                customer_name=customer_name, is_takeaway=is_takeaway, station=station_obj
            )

            total_price = 0
            
            # 1. GRUPOWANIE PRODUKTÓW PO ID
            # Żebyśmy wiedzieli, że klient chce 2x Burger, zanim zaczniemy odejmować
            items_by_id = {}
            
            for item_data in raw_items:
                if isinstance(item_data, int): 
                    pid = item_data
                    extras = []
                else: 
                    pid = item_data.get('id')
                    extras = item_data.get('extras', [])
                
                if pid not in items_by_id:
                    items_by_id[pid] = []
                items_by_id[pid].append(extras)

            # 2. PRZETWARZANIE ZGRUPOWANYCH PRODUKTÓW
            for pid, list_of_extras in items_by_id.items():
                qty_needed = len(list_of_extras) # Ile sztuk tego produktu potrzebujemy
                
                try:
                    # Blokujemy rekord produktu
                    p = Product.objects.select_for_update().get(id=pid)
                    
                    if not p.is_active:
                         raise Exception(f"Produkt '{p.name}' jest obecnie niedostępny.")

                    # --- WALIDACJA STANU ---
                    # Sprawdzamy czy mamy wystarczającą ilość dla CAŁEJ grupy
                    if p.stock < qty_needed:
                        if p.stock == 0:
                            msg = f"Produkt '{p.name}' właśnie się skończył."
                        else:
                            msg = f"Dostępne tylko {p.stock} szt. produktu '{p.name}' (chcesz zamówić {qty_needed})."
                        raise Exception(msg)

                    # Zdejmujemy stan dla całej grupy naraz
                    p.stock = p.stock - qty_needed
                    
                    if p.auto_block_when_zero and p.stock <= 0:
                        p.is_active = False
                    p.save()

                    # Tworzymy OrderItem dla każdej sztuki (bo mogą mieć różne dodatki)
                    base_price = float(p.price)
                    
                    for extras_ids in list_of_extras:
                        order_item = OrderItem.objects.create(order=order, product_name=p.name)
                        item_current_price = base_price
                        
                        if extras_ids:
                            ingredients = Ingredient.objects.filter(id__in=extras_ids)
                            order_item.ingredients.set(ingredients)
                            for ing in ingredients: 
                                item_current_price += float(ing.price)
                        
                        total_price += item_current_price

                        # Opłata za opakowanie (dla każdej sztuki osobno)
                        if is_takeaway and p.has_packaging_fee:
                            OrderItem.objects.create(order=order, product_name="Opakowanie", is_ready=True)
                            total_price += current_pkg_price

                except Product.DoesNotExist:
                    raise Exception("Wybrano nieistniejący produkt.")

            order.total_price = total_price
            order.save()

            if payment_choice == 'KIOSK_CARD':
                 place_name = station_obj.name if station_obj else "Kiosk"
                 log_event(station_obj, 'ORDER_PAID', details=f"Zapłacono kartą. Zamówienie #{order.id}")

        return JsonResponse({'status': 'ok', 'number': order.id})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)