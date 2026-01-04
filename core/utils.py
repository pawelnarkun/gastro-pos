from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa
from io import BytesIO
from django.db.models import Sum
from .models import StationLog, Order

def generate_station_report_pdf(station):
    """Generuje PDF z raportem dla KONKRETNEJ stacji"""
    today = timezone.now().date()
    
    # 1. Pobierz logi tylko dla tej stacji z dzisiaj
    logs = StationLog.objects.filter(station=station, timestamp__date=today).order_by('timestamp')

    # 2. Analiza pracowników (Kto pracował?)
    # Szukamy unikalnych pracowników, którzy się zalogowali lub byli aktywni
    employee_ids = logs.values_list('employee', flat=True).distinct()
    employees_names = []
    # Pobieramy imiona (zakładając, że masz model Employee, zróbmy to bezpiecznie)
    from .models import Employee
    employees = Employee.objects.filter(id__in=employee_ids)
    
    # 3. Analiza Danych (Zależnie od typu stacji)
    context = {
        'station': station,
        'date': today,
        'employees': employees,
        'logs': logs, # Pełna historia zdarzeń
    }

    if station.station_type == 'CASHIER':
        # --- RAPORT KASOWY (Finanse) ---
        # Szukamy logów "ORDER_PAID" na tej stacji
        paid_logs = logs.filter(action='ORDER_PAID')
        
        cash_total = 0.0
        card_total = 0.0
        online_total = 0.0
        
        processed_orders_count = 0
        
        for log in paid_logs:
            if log.order:
                processed_orders_count += 1
                # Sprawdzamy metodę płatności w zamówieniu
                # UWAGA: Zakładamy, że order.total_price to kwota zapłacona
                amount = float(log.order.total_price)
                method = log.order.payment_method # CARD, CASH, ONLINE
                
                if method == 'CASH': cash_total += amount
                elif method == 'CARD': card_total += amount
                elif method == 'ONLINE': online_total += amount
                else: card_total += amount # Domyślnie np. mieszane do karty lub osobna kategoria
        
        context['stats'] = {
            'type': 'FINANCIAL',
            'cash_total': cash_total,
            'card_total': card_total,
            'online_total': online_total,
            'grand_total': cash_total + card_total + online_total,
            'orders_count': processed_orders_count
        }

    elif station.station_type == 'KITCHEN':
        # --- RAPORT KUCHENNY (Wydajność) ---
        # Szukamy logów "ITEM_DONE" lub "ORDER_DONE" (zależy jak logujesz w views.py)
        # Załóżmy, że logujesz 'ITEM_DONE' przy wydawaniu pozycji i 'ORDER_DONE' przy całości
        
        items_done_logs = logs.filter(action='ITEM_DONE')
        orders_done_logs = logs.filter(action__in=['ORDER_DONE', 'KITCHEN_DONE']) # Sprawdź jak nazywasz akcję w views
        
        context['stats'] = {
            'type': 'PERFORMANCE',
            'items_completed': items_done_logs.count(),
            'orders_completed': orders_done_logs.count(),
        }
    
    else:
        # Kioski i inne
        context['stats'] = {'type': 'GENERAL', 'actions_count': logs.count()}

    # 4. Renderowanie
    # Stworzymy prosty szablon HTML wewnątrz kodu lub w pliku, tutaj dla uproszczenia w pliku:
    html = render_to_string("reports/station_report.html", context)

    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    
    if pisa_status.err:
        return None
    return buffer.getvalue()