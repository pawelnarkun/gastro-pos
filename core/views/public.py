# core/views/public.py
from django.shortcuts import render
from django.utils import timezone
from core.models import Order

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