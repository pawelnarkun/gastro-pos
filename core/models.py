from django.db import models
from django.core.validators import RegexValidator
from django.core.files.base import ContentFile

# Importy do obróbki zdjęć
from PIL import Image
from io import BytesIO
import os


class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nazwa kategorii")

    # Kolor kafelka (np. #ffcc00) – opcjonalny
    color = models.CharField(
        max_length=7,
        blank=True,
        verbose_name="Kolor kafelka (HEX)",
        help_text="Np. #ffcc00. Zostaw puste, jeśli używasz grafiki.",
        validators=[
            RegexValidator(
                regex=r'^#(?:[0-9a-fA-F]{3}){1,2}$',
                message="Wpisz poprawny kolor HEX, np. #ffcc00",
            )
        ],
    )

    # Tło kafelka – opcjonalne
    background_image = models.ImageField(
        upload_to="category_backgrounds/",
        blank=True,
        null=True,
        verbose_name="Grafika tła kafelka",
        help_text="Jeśli ustawisz grafikę, będzie użyta zamiast koloru.",
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Kategoria"
        verbose_name_plural = "Kategorie"

class Ingredient(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nazwa dodatku")
    price = models.DecimalField(max_digits=6, decimal_places=2, verbose_name="Cena dodatku")
    is_available = models.BooleanField(default=True, verbose_name="Dostępny?")

    def __str__(self):
        return f"{self.name} (+{self.price} EUR)"


class Station(models.Model):
    STATION_TYPES = [
        ('KIOSK', 'Kiosk samoobsługowy'),
        ('KITCHEN', 'Ekran kuchenny (KDS)'),
        ('CASHIER', 'Stanowisko kasjerskie'),
    ]

    name = models.CharField(max_length=100, verbose_name="Nazwa stanowiska")
    station_type = models.CharField(
        max_length=10, 
        choices=STATION_TYPES, 
        default='KIOSK',
        verbose_name="Typ stanowiska"
    )
    # Slug posłuży nam w URL, np. /kiosk/lobby/
    slug = models.SlugField(unique=True, verbose_name="Identyfikator URL (slug)")

    allow_delivery_before_payment = models.BooleanField(
        default=True,
        verbose_name="Zezwalaj na wydawanie przed zapłatą",
        help_text="Dotyczy tylko stanowisk typu KASA. Jeśli odznaczone, blokuje przyciski wydawania dla nieopłaconych zamówień."
    )

    allow_kiosk_card_payment = models.BooleanField(
        default=False,
        verbose_name="Kiosk: Płatność kartą na miejscu",
        help_text="Zaznacz, jeśli ten kiosk ma terminal płatniczy."
    )

    auto_logout_time = models.TimeField(
        null=True, 
        blank=True, 
        verbose_name="Godzina auto-wylogowania",
        help_text="Godzina, o której system wymusi wylogowanie wszystkich pracowników (raz dziennie)."
    )

    def __str__(self):
        return f"{self.name} ({self.get_station_type_display()})"

    class Meta:
        verbose_name = "Stanowisko (Kiosk/Kuchnia/Kasa)"
        verbose_name_plural = "Stanowiska"

class RawMaterial(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nazwa półproduktu")
    stock = models.DecimalField(max_digits=10, decimal_places=3, default=0.000, verbose_name="Stan magazynowy")
    unit = models.CharField(max_length=10, default="szt", verbose_name="Jednostka (kg, l, szt)")

    auto_logout_time = models.TimeField(
        null=True, 
        blank=True, 
        verbose_name="Godzina auto-wylogowania",
        help_text="Godzina, o której system wymusi wylogowanie wszystkich pracowników."
    )

    def __str__(self):
        return f"{self.name} ({self.stock} {self.unit})"

    class Meta:
        verbose_name = "Półprodukt"
        verbose_name_plural = "Półprodukty"




class Allergen(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nazwa alergenu")
    # Opcjonalnie: ikona lub kod (np. "G" dla Glutenu)
    code = models.CharField(max_length=10, blank=True, verbose_name="Kod/Skrót", help_text="Np. GL, ORZ")

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name

    class Meta:
        verbose_name = "Alergen"
        verbose_name_plural = "Alergeny"

class Product(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    name = models.CharField(max_length=100, verbose_name="Nazwa produktu")
    uses_raw_materials = models.BooleanField(default=False, verbose_name="Używa półproduktów?")

    allergens = models.ManyToManyField(
        Allergen,
        blank=True,
        verbose_name="Alergeny",
        help_text="Zaznacz alergeny obecne w tym produkcie."
    )

    is_customizable = models.BooleanField(default=False, verbose_name="Edytowalny (dodatki)?")
    possible_ingredients = models.ManyToManyField(Ingredient, blank=True, verbose_name="Możliwe dodatki")

    can_cashier_deliver = models.BooleanField(
        default=True,
        verbose_name="Kasjer może wydać?",
        help_text="Jeśli odznaczone, przycisk 'Wydaj' będzie ukryty dla kasjera (produkt musi wydać kuchnia)."
    )
    limit_availability = models.ManyToManyField(
        Station,
        blank=True,
        verbose_name="Ogranicz dostępność do...",
        help_text="Zostaw puste, aby produkt był dostępny WSZĘDZIE. Wybierz konkretne stacje, jeśli chcesz ograniczyć widoczność."
    )
    
    can_kitchen_deliver = models.BooleanField(
        default=True,
        verbose_name="Kuchnia może wydać?",
        help_text="Jeśli odznaczone, produkt nie będzie widoczny na ekranie kuchni (KDS)."
    )

    # CENA SPRZEDAŻY
    price = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name="Cena sprzedaży"
    )
    has_packaging_fee = models.BooleanField(
        default=True,
        verbose_name="Opłata za opakowanie",
        help_text="Zaznacz, jeśli do tego produktu ma być doliczana opłata przy zamówieniu na wynos."
    )

    # KOSZT
    cost_price = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        verbose_name="Koszt jednostkowy",
        help_text="Koszt zakupu / przygotowania jednej sztuki.",
    )

    stock = models.IntegerField(
        default=0,
        verbose_name="Stan magazynu",
        help_text="Liczba sztuk dostępna w magazynie.",
    )

    # automatyczna blokada przy stanie 0
    auto_block_when_zero = models.BooleanField(
        default=False,
        verbose_name="Auto blokada przy stanie 0",
        help_text=(
            "Jeśli zaznaczone: gdy stan spadnie do 0 produkt zostanie "
            "zablokowany w kiosku, a po uzupełnieniu stanu automatycznie odblokowany."
        ),
    )

    # Kolor belki produktu (opcjonalny)
    color = models.CharField(
        max_length=7,
        blank=True,
        verbose_name="Kolor belki (HEX)",
        help_text="Np. #ffcc00. Zostaw puste, aby użyć koloru kategorii lub domyślnego.",
        validators=[
            RegexValidator(
                regex=r'^#(?:[0-9a-fA-F]{3}){1,2}$',
                message="Wpisz poprawny kolor HEX, np. #ffcc00",
            )
        ],
    )

    # Zdjęcie produktu (opcjonalne)
    image = models.ImageField(
        upload_to="product_images/",
        blank=True,
        null=True,
        verbose_name="Zdjęcie produktu",
        help_text="Opcjonalne zdjęcie wyświetlane w kiosku. Zostanie automatycznie zmniejszone do 800px.",
    )

    # flaga dostępności produktu w kiosku
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Dostępny w kiosku",
        help_text="Odznacz, aby zablokować zamawianie tego produktu w kiosku.",
    )

    def __str__(self):
        return f"{self.name} ({self.price} EUR)"

    class Meta:
        verbose_name = "Produkt"
        verbose_name_plural = "Produkty"

    def save(self, *args, **kwargs):
        """
        Nadpisana metoda save:
        1. Obsługuje logikę auto-blokady (stock <= 0).
        2. Automatycznie skaluje i kompresuje zdjęcie produktu.
        """
        
        # --- 1. Logika Stanów Magazynowych ---
        if self.stock is None:
            self.stock = 0

        if self.auto_block_when_zero:
            if self.stock <= 0:
                self.is_active = False
            else:
                self.is_active = True

        # --- 2. Logika Kompresji Obrazka ---
        if self.image:
            # Otwieramy obrazek za pomocą biblioteki Pillow
            try:
                img = Image.open(self.image)
                
                # Ustawiamy maksymalny wymiar (np. 800px szerokości lub wysokości)
                MAX_SIZE = (800, 800)
                
                # Sprawdzamy czy obrazek jest większy niż MAX_SIZE
                if img.height > MAX_SIZE[1] or img.width > MAX_SIZE[0]:
                    
                    # Zachowujemy proporcje (thumbnail)
                    img.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)

                    # Przygotowujemy bufor w pamięci
                    buffer = BytesIO()

                    # Sprawdzamy format pliku
                    filename, ext = os.path.splitext(self.image.name)
                    ext = ext.lower()

                    if ext in ['.png']:
                        # Dla PNG zachowujemy przezroczystość (RGBA)
                        img.save(buffer, format='PNG', optimize=True)
                    else:
                        # Dla JPG konwertujemy na RGB (usuwamy ew. przezroczystość) i kompresujemy
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        img.save(buffer, format='JPEG', quality=85, optimize=True)

                    # Zapisujemy zmieniony plik z powrotem do pola image
                    self.image = ContentFile(buffer.getvalue(), name=self.image.name)
            
            except Exception as e:
                # Jeśli coś pójdzie nie tak z obrazkiem, po prostu ignorujemy kompresję
                # i zapisujemy oryginał, wypisując błąd w konsoli
                print(f"Błąd kompresji obrazka: {e}")

        super().save(*args, **kwargs)

class ProductRecipe(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='recipe_items')
    raw_material = models.ForeignKey(RawMaterial, on_delete=models.CASCADE, verbose_name="Półprodukt")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="Ilość zużywana")

    def __str__(self):
        return f"{self.product.name} -> {self.quantity} {self.raw_material.unit} {self.raw_material.name}"

class Employee(models.Model):
    name = models.CharField(max_length=50, verbose_name="Imię / Nick")
    # PIN opcjonalny (blank=True), jeśli pusty - logowanie bez hasła
    pin = models.CharField(max_length=4, blank=True, null=True, verbose_name="PIN (opcjonalny)", help_text="Zostaw puste dla logowania bez hasła")
    is_active = models.BooleanField(default=True, verbose_name="Aktywny")
    
    # Możemy dodać role, np. czy może być na kuchni, czy na kasie, ale na start uprośćmy:
    can_work_cashier = models.BooleanField(default=True, verbose_name="Może pracować na Kasie")
    can_work_kitchen = models.BooleanField(default=True, verbose_name="Może pracować na Kuchni")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Pracownik"
        verbose_name_plural = "Pracownicy"


class Order(models.Model):
    STATUS_CHOICES = [
        ('UNPAID', 'Nieopłacone'),
        ('NEW', 'Nowe'),
        ('READY', 'Gotowe'),
        ('DONE', 'Wydane'),
    ]

    PAYMENT_METHODS = [
        ('CASH', 'Gotówka'),
        ('CARD', 'Karta'),
        ('ONLINE', 'Online'),
        ('OTHER', 'Inne'),
    ]
    payment_method = models.CharField(
        max_length=10, 
        choices=PAYMENT_METHODS, 
        blank=True, 
        null=True, 
        verbose_name="Metoda płatności"
    )
    payment_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    payment_card = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    created_at = models.DateTimeField(
        auto_now_add=True, 
        db_index=True, 
        verbose_name="Data utworzenia"
    )

    # Pole station przechowuje informację, na której stacji wykonano ostatnią akcję
    station = models.ForeignKey(
        'Station', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='processed_orders',
        verbose_name="Ostatnia stacja"
    )
    
    # --- NOWE POLA: ROZDZIELENIE ODPOWIEDZIALNOŚCI ---
    
    # Liderzy (Główni wydający)
    delivered_by_kitchen = models.ForeignKey(
        Employee, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='kitchen_orders_leader',
        verbose_name="Wydane przez (Lider Kuchni)"
    )
    
    delivered_by_bar = models.ForeignKey(
        Employee, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='bar_orders_leader',
        verbose_name="Wydane przez (Lider Baru/Kasy)"
    )

    # Zespoły pomocnicze (Many-to-Many)
    kitchen_team = models.ManyToManyField(
        Employee,
        blank=True,
        related_name='kitchen_orders_team',
        verbose_name="Zespół Kuchni (pomocnicy)"
    )
    
    bar_team = models.ManyToManyField(
        Employee,
        blank=True,
        related_name='bar_orders_team',
        verbose_name="Zespół Baru (pomocnicy)"
    )
    # -------------------------------------------------
    
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='NEW',
        db_index=True,
        verbose_name="Status",
    )
    cancel_reason = models.TextField(blank=True, null=True, verbose_name="Powód anulowania")
    
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Łączna kwota",
    )
    customer_name = models.CharField(
        max_length=50,
        default="Klient",
        verbose_name="Imię klienta",
    )

    note = models.TextField(
        blank=True,
        null=True,
        verbose_name="Notatka dla kuchni",
    )
    history_note = models.TextField(
        blank=True,
        null=True,
        verbose_name="Notatka do historii",
    )

    is_takeaway = models.BooleanField(
        default=False,
        verbose_name="Na wynos",
    )

    def __str__(self):
        return f"#{self.id} {self.customer_name} - {self.status}"

    # --- METODY POMOCNICZE DO WYŚWIETLANIA ---
    def get_kitchen_display(self):
        """Zwraca string: Lider (+ pomocnik1, pomocnik2)"""
        if not self.delivered_by_kitchen:
            return None
        
        # Pobieramy pomocników, ale WYKLUCZAMY lidera, żeby się nie powtarzał
        helpers = self.kitchen_team.exclude(id=self.delivered_by_kitchen.id)
        
        if helpers.exists():
            names = ", ".join([h.name for h in helpers])
            return f"{self.delivered_by_kitchen.name} (+ {names})"
        
        return self.delivered_by_kitchen.name

    def get_bar_display(self):
        """Zwraca string: Lider (+ pomocnik1, pomocnik2)"""
        if not self.delivered_by_bar:
            return None
            
        helpers = self.bar_team.exclude(id=self.delivered_by_bar.id)
        
        if helpers.exists():
            names = ", ".join([h.name for h in helpers])
            return f"{self.delivered_by_bar.name} (+ {names})"
            
        return self.delivered_by_bar.name

    def get_bar_display(self):
        """Zwraca string: Lider (+ osoba1, osoba2)"""
        if not self.delivered_by_bar:
            return None
            
        helpers = self.bar_team.exclude(id=self.delivered_by_bar.id)
        helpers_names = [h.name for h in helpers]
        
        if helpers_names:
            return f"{self.delivered_by_bar.name} (+ {', '.join(helpers_names)})"
        return self.delivered_by_bar.name

    class Meta:
        verbose_name = "Zamówienie"
        verbose_name_plural = "Zamówienia"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        verbose_name="Zamówienie",
    )
    product_name = models.CharField(
        max_length=100,
        verbose_name="Nazwa produktu",
    )
    is_ready = models.BooleanField(
        default=False,
        verbose_name="Gotowe",
    )

    ingredients = models.ManyToManyField('Ingredient', blank=True, verbose_name="Wybrane dodatki")

    completed_by = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Wykonane przez"
    )

    def __str__(self):
        return f"{self.product_name} ({'Gotowe' if self.is_ready else 'W trakcie'})"

    class Meta:
        verbose_name = "Pozycja zamówienia"
        verbose_name_plural = "Pozycje zamówienia"

class GlobalSettings(models.Model):
    allow_delivery_before_payment = models.BooleanField(
        default=True,
        verbose_name="Zezwalaj na wydawanie przed zapłatą",
        help_text="Jeśli odznaczone, kasjer nie będzie mógł wydać produktu (kliknąć +1/Done) dopóki zamówienie jest NIEOPŁACONE."
    )

    stock_warning_limit = models.IntegerField(
        default=5,
        verbose_name="Próg ostrzegawczy magazynu (pomarańczowy)",
        help_text="Ilość, poniżej której stan magazynowy wyświetli się na pomarańczowo."
    )
    stock_safe_limit = models.IntegerField(
        default=20,
        verbose_name="Próg bezpieczny magazynu (zielony)",
        help_text="Ilość, powyżej której stan magazynowy wyświetli się na zielono."
    )

    def __str__(self):
        return "Ustawienia globalne"

    class Meta:
        verbose_name = "Ustawienia systemu"
        verbose_name_plural = "Ustawienia systemu"

    @classmethod
    def load(cls):
        """Pomocnicza metoda: pobiera ustawienia lub tworzy domyślne."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj
    
class StationLog(models.Model):
    ACTION_TYPES = [
        ('LOGIN', 'Logowanie'),
        ('LOGOUT', 'Wylogowanie'),
        ('LEADER', 'Zmiana Lidera'),
        ('ITEM_DONE', 'Wydanie Produktu'),
        ('ORDER_PAID', 'Opłacenie'),
        ('ORDER_SPLIT', 'Podział Rachunku'),
        ('OTHER', 'Inne'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Czas zdarzenia")
    station = models.ForeignKey('Station', on_delete=models.CASCADE, verbose_name="Stacja")
    employee = models.ForeignKey('Employee', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Pracownik")
    order = models.ForeignKey('Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='logs', verbose_name="Powiązane zamówienie")
    
    action = models.CharField(max_length=20, choices=ACTION_TYPES, verbose_name="Typ akcji")
    details = models.TextField(blank=True, verbose_name="Szczegóły")

    def __str__(self):
        emp_name = self.employee.name if self.employee else "System"
        return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.station.name}: {self.action} ({emp_name})"

    class Meta:
        verbose_name = "Log Systemowy"
        verbose_name_plural = "Logi Systemowe"
        ordering = ['-timestamp']