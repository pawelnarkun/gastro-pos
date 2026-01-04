import datetime
from django.contrib import admin
from django.utils.html import mark_safe
from django import forms
from django.db import models
from django.db.models import Sum, Count
from django.utils import timezone
from django.urls import path
from django.template.loader import render_to_string
from django.http import HttpResponse
from xhtml2pdf import pisa
from decimal import Decimal

# Importujemy StationLog oraz inne modele
from .models import GlobalSettings, Station, Employee, StationLog
from .models import Product, Order, OrderItem, Category, Ingredient, Allergen, RawMaterial, ProductRecipe


# ---------- Wspólny widget koloru ----------

class ColorInput(forms.TextInput):
    input_type = "color"


# ---------- Formularze admina ----------

class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = "__all__"
        widgets = {
            "color": ColorInput(),
        }


class CategoryAdminForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = "__all__"
        widgets = {
            "color": ColorInput(),
        }


# ---------- Kategorie ----------

# Rejestracja Półproduktów (żebyś mógł dodać: Bułka, Mięso...)
@admin.register(RawMaterial)
class RawMaterialAdmin(admin.ModelAdmin):
    list_display = ['name', 'stock', 'unit']
    search_fields = ['name']

# Konfiguracja wstawki receptury w produkcie
class RecipeInline(admin.TabularInline):
    model = ProductRecipe
    extra = 1 # Ile pustych wierszy pokazać
    verbose_name = "Składnik receptury"
    verbose_name_plural = "Receptura (Półprodukty)"

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    form = CategoryAdminForm
    list_display = ("name", "color_preview", "has_background_image")
    fields = ("name", "color", "background_image")

    def color_preview(self, obj):
        if not obj.color:
            return "-"
        return mark_safe(
            f'<div style="width: 40px; height: 20px; '
            f'border-radius: 4px; border: 1px solid #ccc; '
            f'background: {obj.color};"></div>'
        )
    color_preview.short_description = "Kolor"

    def has_background_image(self, obj):
        return bool(obj.background_image)
    has_background_image.boolean = True
    has_background_image.short_description = "Grafika?"


# ---------- Podstawowe Modele ----------

@admin.register(Ingredient)
class IngredientAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'is_available')

@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    # Dodałem 'auto_logout_time' do listy wyświetlanej
    list_display = ('name', 'station_type', 'slug', 'auto_logout_time', 'allow_kiosk_card_payment')
    list_filter = ('station_type',)
    prepopulated_fields = {'slug': ('name',)}

    # To sprawia, że w polu godziny pojawia się systemowy zegar
    formfield_overrides = {
        models.TimeField: {'widget': forms.TimeInput(attrs={'type': 'time'})},
    }

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'can_work_cashier', 'can_work_kitchen', 'is_active')
    list_editable = ('is_active',)

@admin.register(Allergen)
class AllergenAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')


# ---------- Produkty ----------

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm

    list_display = (
        "name", "category", "price", "cost_price",
        "margin_abs", "margin_percent", "stock",
        "can_cashier_deliver", "can_kitchen_deliver",
        "available_stations_count", "has_packaging_fee",
        "is_customizable", "auto_block_when_zero",
        "allergens_display", "is_active", "image_thumb", "color",
    )

    list_filter = (
        "category", "is_active", "can_cashier_deliver", 
        "can_kitchen_deliver", "auto_block_when_zero", 
        "has_packaging_fee", "is_customizable"
    )
    
    search_fields = ("name", "category__name")
    inlines = [RecipeInline]
    readonly_fields = ("image_thumb",)
    filter_horizontal = ('possible_ingredients', 'limit_availability', 'allergens')

    fieldsets = (
        ("Dane podstawowe", {
            "fields": (
                "name", "category", "price", "cost_price", "stock",
                "can_cashier_deliver", "can_kitchen_deliver",
                "has_packaging_fee", "is_customizable",
                "possible_ingredients", "auto_block_when_zero",
                "is_active", "allergens",
            )
        }),
        ("Dostępność (Multi-lokalizacja)", {
            "fields": ("limit_availability",),
            "description": "Jeśli nic nie wybierzesz, produkt będzie widoczny na wszystkich stanowiskach."
        }),
        ("Wygląd w kiosku", {
            "fields": ("color", "image", "image_thumb",)
        }),
    )

    def allergens_display(self, obj):
        return ", ".join([a.code for a in obj.allergens.all()]) or "-"
    allergens_display.short_description = "Alergeny"

    def image_thumb(self, obj):
        if not getattr(obj, "image", None): return "—"
        return mark_safe(f'<img src="{obj.image.url}" style="max-width: 80px; max-height: 80px; border-radius: 8px; object-fit: cover;" />')
    image_thumb.short_description = "Podgląd"

    def margin_abs(self, obj):
        if obj.price is None or obj.cost_price is None: return "—"
        return f"{(obj.price - obj.cost_price):.2f}"
    margin_abs.short_description = "Marża [EUR]"

    def margin_percent(self, obj):
        if not obj.price: return "—"
        profit = (obj.price or Decimal("0")) - (obj.cost_price or Decimal("0"))
        return f"{(profit / obj.price * Decimal('100')):.1f}%"
    margin_percent.short_description = "Marża [%]"

    def available_stations_count(self, obj):
        count = obj.limit_availability.count()
        return f"{count} stacji" if count > 0 else "Wszędzie"
    available_stations_count.short_description = "Dostępność"


@admin.register(GlobalSettings)
class GlobalSettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not GlobalSettings.objects.exists()


# ---------- LOGI SYSTEMOWE (NOWOŚĆ) ----------

@admin.register(StationLog)
class StationLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'station', 'action', 'employee', 'order_link', 'details')
    list_filter = ('action', 'station', 'timestamp', 'employee')
    search_fields = ('details', 'order__id', 'employee__name')
    date_hierarchy = 'timestamp'
    
    def order_link(self, obj):
        if obj.order:
            return mark_safe(f'<a href="/admin/core/order/{obj.order.id}/change/">#{obj.order.id}</a>')
        return "-"
    order_link.short_description = "Zamówienie"


# Inline do wyświetlania historii wewnątrz zamówienia
class StationLogInline(admin.TabularInline):
    model = StationLog
    fk_name = 'order'
    extra = 0
    readonly_fields = ('timestamp', 'station', 'action', 'employee', 'details')
    can_delete = False
    verbose_name = "Wpis w historii"
    verbose_name_plural = "Historia zdarzeń (Audit Log)"
    
    def has_add_permission(self, request, obj):
        return False


# ---------- ZAMÓWIENIA ----------

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    # Zaktualizowana lista wyświetlania o nowe pola
    list_display = (
        "id", "created_at", "customer_name", 
        "status", "payment_method", "total_price", 
        "is_takeaway", "delivered_by_kitchen", "delivered_by_bar"
    )
    list_filter = ("status", "payment_method", "is_takeaway", "created_at")
    date_hierarchy = "created_at"
    
    # Dodanie tabeli z logami na dole widoku edycji zamówienia
    inlines = [StationLogInline]

    change_list_template = "admin/core/order/change_list.html"

    # Pola ManyToMany (zespoły) wyświetlamy w ładniejszy sposób
    filter_horizontal = ('kitchen_team', 'bar_team')

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "daily-summary-pdf/",
                self.admin_site.admin_view(self.daily_summary_pdf),
                name="core_order_daily_summary_pdf",
            ),
        ]
        return custom_urls + urls

    def _get_summary_date(self, request):
        date_str = request.GET.get("summary_date")
        if date_str:
            try:
                return datetime.date.fromisoformat(date_str)
            except ValueError:
                pass
        return timezone.localdate()

    def get_daily_summary(self, day):
        orders_qs = Order.objects.filter(created_at__date=day)
        
        new_orders = orders_qs.filter(status="NEW")
        ready_orders = orders_qs.filter(status="READY")
        done_orders = orders_qs.filter(status="DONE")

        total_orders = orders_qs.count()
        total_done = done_orders.count()

        total_value = done_orders.aggregate(sum=Sum("total_price"))["sum"] or Decimal("0")
        avg_value = total_value / total_done if total_done else Decimal("0")

        # --- Statystyki metod płatności ---
        cash_orders = done_orders.filter(payment_method='CASH').aggregate(sum=Sum("total_price"))["sum"] or Decimal("0")
        card_orders = done_orders.filter(payment_method='CARD').aggregate(sum=Sum("total_price"))["sum"] or Decimal("0")
        other_payments = total_value - (cash_orders + card_orders)

        # --- NOWE: ANALIZA OBSADY (Kto pracował?) ---
        # 1. Pobieramy logi z tego dnia
        day_logs = StationLog.objects.filter(timestamp__date=day)
        
        # 2. Zbieramy ID wszystkich, którzy coś klikali (są w logach)
        active_ids = set(day_logs.exclude(employee=None).values_list('employee__id', flat=True))
        
        # 3. Zbieramy ID liderów (z logów 'LEADER' oraz z przypisań w zamówieniach)
        leader_ids = set(day_logs.filter(action='LEADER').exclude(employee=None).values_list('employee__id', flat=True))
        # Dodajemy też tych, którzy są zapisani w zamówieniach jako liderzy (na wszelki wypadek)
        leader_ids.update(orders_qs.exclude(delivered_by_kitchen=None).values_list('delivered_by_kitchen__id', flat=True))
        leader_ids.update(orders_qs.exclude(delivered_by_bar=None).values_list('delivered_by_bar__id', flat=True))
        
        # 4. Pobieramy obiekty pracowników i dzielimy na listy
        all_active = Employee.objects.filter(id__in=active_ids | leader_ids)
        leaders_list = []
        members_list = []
        
        for emp in all_active:
            if emp.id in leader_ids:
                leaders_list.append(emp.name)
            else:
                members_list.append(emp.name)
        # ---------------------------------------------

        # Statystyki produktów
        items_qs = OrderItem.objects.filter(
            order__created_at__date=day,
            order__status="DONE",
        )
        total_items_done = items_qs.count()

        products = Product.objects.all()
        price_map = {p.name: (p.price or Decimal("0")) for p in products}
        cost_map = {p.name: (getattr(p, "cost_price", Decimal("0")) or Decimal("0")) for p in products}

        product_counts = items_qs.values("product_name").annotate(count=Count("id"))
        product_stats = []
        total_cost = Decimal("0")

        for row in product_counts:
            name = row["product_name"]
            count = row["count"] or 0
            unit_price = price_map.get(name, Decimal("0"))
            unit_cost = cost_map.get(name, Decimal("0"))
            
            revenue = unit_price * count
            cost_total = unit_cost * count
            profit = revenue - cost_total
            total_cost += cost_total

            product_stats.append({
                "product_name": name,
                "count": count,
                "unit_price": unit_price,
                "unit_cost": unit_cost,
                "revenue": revenue,
                "cost": cost_total,
                "profit": profit,
            })

        top_products = sorted(product_stats, key=lambda x: x["count"], reverse=True)[:10]
        total_profit = total_value - total_cost
        margin_percent = (total_profit / total_value * Decimal("100")) if total_value else Decimal("0")

        takeaway_count = done_orders.filter(is_takeaway=True).count()
        on_site_count = done_orders.filter(is_takeaway=False).count()

        return {
            "total_orders": total_orders,
            "new_orders": new_orders.count(),
            "ready_orders": ready_orders.count(),
            "done_orders": total_done,
            "total_items_done": total_items_done,
            "total_value": total_value,
            "avg_value": avg_value,
            "total_cost": total_cost,
            "total_profit": total_profit,
            "margin_percent": margin_percent,
            "takeaway_count": takeaway_count,
            "on_site_count": on_site_count,
            "top_products": top_products,
            
            # Nowe dane przekazywane do szablonu:
            "cash_total": cash_orders,
            "card_total": card_orders,
            "other_total": other_payments,
            "leaders_list": sorted(leaders_list),
            "members_list": sorted(members_list),
        }

    def changelist_view(self, request, extra_context=None):
        today = timezone.localdate()
        summary = self.get_daily_summary(today)
        extra_context = extra_context or {}
        extra_context["summary_date"] = today
        extra_context["daily_summary"] = summary
        return super().changelist_view(request, extra_context=extra_context)

    def daily_summary_pdf(self, request):
        day = self._get_summary_date(request)
        summary = self.get_daily_summary(day)

        # Dane dodatkowe dla PDF (Anulowane)
        cancelled_qs = Order.objects.filter(created_at__date=day, status='CANCELLED')
        summary['cancelled_orders'] = cancelled_qs.count()
        
        from django.db.models import Sum
        total_lost = cancelled_qs.aggregate(Sum('total_price'))['total_price__sum']
        summary['total_lost'] = total_lost if total_lost else 0.0
        summary['cancelled_list'] = cancelled_qs.order_by('-created_at')

        # Pobieranie pełnej historii zdarzeń (Logi) do tabeli na końcu
        logs = StationLog.objects.filter(timestamp__date=day).order_by('timestamp')

        html = render_to_string(
            "admin/core/order/daily_summary_pdf.html",
            {
                "summary_date": day,
                "summary": summary,
                "logs": logs,
            },
        )

        response = HttpResponse(content_type="application/pdf")
        filename = f"podsumowanie_{day}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response['X-Content-Type-Options'] = 'nosniff'

        pisa_status = pisa.CreatePDF(html, dest=response, encoding="utf-8")
        if pisa_status.err:
            return HttpResponse("Błąd generowania PDF. Treść HTML:<hr>" + html)

        return response


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product_name', 'is_ready', 'completed_by')
    list_filter = ('is_ready', 'product_name')