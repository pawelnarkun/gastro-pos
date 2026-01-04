from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static

# ZMIANA: Importujemy poszczególne moduły z pakietu views
from core.views import auth, kiosk, kitchen, cashier, public

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- LANDING PAGE (Auth) ---
    path('', auth.station_selection, name='station_selection'),

    # --- KIOSK ---
    path('kiosk/<slug:station_slug>/', kiosk.kiosk_index, name='kiosk_index'),
    path('kiosk/<slug:station_slug>/category/<int:category_id>/', kiosk.kiosk_category, name='kiosk_category'),
    path('api/order/', kiosk.api_create_order, name='api_order'),

    # --- LOGOWANIE DO STACJI (Auth) ---
    path('station/<slug:station_slug>/login/', auth.station_login, name='station_login'),
    path('station/<slug:station_slug>/logout/', auth.station_logout, name='station_logout'),
    path('station/<slug:station_slug>/leader/<int:employee_id>/', auth.station_switch_leader, name='station_switch_leader'),
    path('station/<slug:station_slug>/logout-user/<int:employee_id>/', auth.station_logout_user, name='station_logout_user'),

    # --- KUCHNIA (Kitchen) ---
    path('kitchen/<slug:station_slug>/', kitchen.kitchen_index, name='kitchen'),
    
    # Akcje kuchni
    path('kitchen/<slug:station_slug>/done/<int:order_id>/', kitchen.kitchen_done, name='kitchen_done'),
    path('kitchen/<slug:station_slug>/item-done/<int:item_id>/', kitchen.kitchen_item_done, name='kitchen_item_done'),
    path('kitchen/<slug:station_slug>/group-done/<int:item_id>/', kitchen.kitchen_group_done, name='kitchen_group_done'),
    path('kitchen/note/<int:order_id>/', kitchen.kitchen_add_note, name='kitchen_add_note'),
    
    # Historia
    path('kitchen/<slug:station_slug>/history/', kitchen.kitchen_history, name='kitchen_history'),
    path('kitchen/history-note/<int:order_id>/', kitchen.kitchen_add_history_note, name='kitchen_add_history_note'),

    # Zarządzanie produktami (Kuchnia)
    path("kitchen/<slug:station_slug>/products/", kitchen.kitchen_products, name="kitchen_products"),
    path("kitchen/toggle-product/<int:product_id>/", kitchen.kitchen_toggle_product, name="kitchen_toggle_product"),
    path("kitchen/product-stock/<int:product_id>/", kitchen.kitchen_update_stock, name="kitchen_update_stock"),
    path("kitchen/product-auto-block/<int:product_id>/", kitchen.kitchen_toggle_autoblock, name="kitchen_toggle_autoblock"),
    path('kitchen/<slug:station_slug>/stock/raw-materials/', kitchen.kitchen_raw_materials, name='kitchen_raw_materials'),
    path('kitchen/stock/raw-material/<int:material_id>/update/', kitchen.kitchen_update_raw_material, name='kitchen_update_raw_material'),

    # --- KASA (Cashier) ---
    path("kasa/<slug:station_slug>/", cashier.cashier_index, name="cashier"),
    path("kasa/<slug:station_slug>/api/", cashier.cashier_api, name="cashier_api"),
    
    path("kasa/<slug:station_slug>/create/", cashier.cashier_create_order, name="cashier_create_order"),
    path("kasa/<slug:station_slug>/search-products/", cashier.cashier_search_products, name="cashier_search_products"),
    
    # Akcje na zamówieniach (Kasa)
    path("kasa/<slug:station_slug>/<int:order_id>/paid/", cashier.cashier_paid, name="cashier_paid"),
    path("kasa/<slug:station_slug>/<int:order_id>/done/", cashier.cashier_done, name="cashier_done"),
    path("kasa/<slug:station_slug>/<int:order_id>/cancel/", cashier.cashier_cancel, name="cashier_cancel"),
    
    path("kasa/<slug:station_slug>/item-done/<int:item_id>/", cashier.cashier_item_done, name="cashier_item_done"),
    path('kasa/<slug:station_slug>/group-done/<int:item_id>/', cashier.cashier_group_done, name='cashier_group_done'),
    
    path("kasa/<slug:station_slug>/<int:order_id>/edit_qty/", cashier.cashier_edit_qty, name="cashier_edit_qty"),
    path("kasa/<slug:station_slug>/add-product/<int:order_id>/", cashier.cashier_add_product, name="cashier_add_product"),
    path("kasa/<slug:station_slug>/toggle-product/<int:product_id>/", cashier.cashier_toggle_product, name="cashier_toggle_product"),
    path("kasa/<slug:station_slug>/toggle-takeaway/<int:order_id>/", cashier.cashier_toggle_takeaway, name="cashier_toggle_takeaway"),
    path("kasa/<slug:station_slug>/split/<int:order_id>/", cashier.cashier_split_order, name="cashier_split_order"),

    # --- STATUS BOARD (Public) ---
    path('status/', public.client_order_board, name='client_order_board'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)