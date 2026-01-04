from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Order, OrderItem, Product

def send_refresh_signal(source):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "restaurant_updates",  # Nazwa grupy z consumers.py
        {
            "type": "order_refresh", # Nazwa metody w consumers.py
            "source": source
        }
    )

@receiver(post_save, sender=Order)
def order_changed(sender, instance, created, **kwargs):
    # Wywołaj odświeżenie przy każdej zmianie w modelu Order
    send_refresh_signal(source="Order")

@receiver(post_save, sender=OrderItem)
def item_changed(sender, instance, created, **kwargs):
    # Wywołaj odświeżenie przy każdej zmianie w pozycjach
    send_refresh_signal(source="OrderItem")
    
@receiver(post_delete, sender=Order)
def order_deleted(sender, instance, **kwargs):
    send_refresh_signal(source="OrderDeleted")