# core/views/utils.py
from core.models import StationLog, OrderItem, Employee, Product, Station
from django.shortcuts import get_object_or_404

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
    siblings = OrderItem.objects.filter(
        order=original_item.order,
        product_name=original_item.product_name,
        is_ready=False
    ).exclude(id=original_item.id).prefetch_related('ingredients')

    original_ingredients = set(original_item.ingredients.values_list('id', flat=True))

    for sibling in siblings:
        sibling_ingredients = set(sibling.ingredients.values_list('id', flat=True))
        if sibling_ingredients == original_ingredients:
            return sibling 
    return None

def get_station_team(request, station_slug):
    session_key = f'station_team_{station_slug}'
    return request.session.get(session_key)

def set_station_team(request, station_slug, leader_id, members_ids):
    session_key = f'station_team_{station_slug}'
    request.session[session_key] = {
        'leader_id': leader_id,
        'members_ids': list(set(members_ids))
    }
    request.session.modified = True

def clear_station_team(request, station_slug):
    session_key = f'station_team_{station_slug}'
    if session_key in request.session:
        del request.session[session_key]
        request.session.modified = True

def get_current_leader(request, station_slug):
    data = get_station_team(request, station_slug)
    if not data or not data.get('leader_id'):
        return None
    return Employee.objects.filter(id=data['leader_id']).first()

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

def build_grouped_items(order, product_categories):
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