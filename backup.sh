#!/bin/bash

# Ustalmy foldery
PROJECT_DIR="/home/pawel-n/Pulpit/gastro-new"
BACKUP_DIR="/home/pawel-n/kopie_zapasowe"

# Stwórz folder na kopie, jeśli nie istnieje
mkdir -p $BACKUP_DIR

echo "�� Pakowanie bazy danych i folderu media..."

# Wchodzimy do folderu projektu
cd $PROJECT_DIR

# Pakujemy plik bazy (db.sqlite3) i folder ze zdjęciami (media) do jednego pliku
# Używamy --ignore-failed-read żeby nie krzyczał jeśli np. folder media jest pusty
tar -czvf $BACKUP_DIR/latest.tar.gz db.sqlite3 media

echo "✅ Gotowe! Plik zapisany w: $BACKUP_DIR/latest.tar.gz"
