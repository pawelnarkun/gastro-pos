import json
from channels.generic.websocket import AsyncWebsocketConsumer

class OrderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Dołączamy każdego klienta do grupy "restaurant_updates"
        self.group_name = "restaurant_updates"

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Odłączamy od grupy
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Odbiór wiadomości z grupy (wysłanej przez signals.py)
    async def order_refresh(self, event):
        # Wysyłamy wiadomość do WebSocket w przeglądarce
        await self.send(text_data=json.dumps({
            'type': 'refresh',
            'source': event.get('source', 'unknown')
        }))