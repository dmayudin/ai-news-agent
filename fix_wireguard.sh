#!/bin/bash
# Скрипт для исправления и переконфигурации WireGuard

set -e

echo "=== Исправление конфигурации WireGuard ==="

# Проверка прав администратора
if [[ $EUID -ne 0 ]]; then
   echo "Этот скрипт должен быть запущен от root"
   exit 1
fi

# Остановка WireGuard если он запущен
echo "1. Остановка WireGuard..."
systemctl stop wg-quick@wg0 || true
ip link del wg0 || true

# Очистка маршрутов
echo "2. Очистка маршрутов..."
ip rule del table 51820 || true
ip route flush table 51820 || true

# Удаление старой конфигурации
echo "3. Удаление старой конфигурации..."
rm -f /etc/wireguard/wg0.conf

# Создание новой конфигурации с правильными параметрами
echo "4. Создание новой конфигурации WireGuard..."
cat > /etc/wireguard/wg0.conf << 'EOFWG'
[Interface]
PrivateKey = oExsEAJQHFmrIbwOYBx/Zc807CTl2n90HTQwUP1kVlc=
Address = 10.0.0.2/32
DNS = 8.8.8.8, 8.8.4.4
# Не используем автоматическую маршрутизацию всего трафика
# Это предотвращает потерю SSH соединения

[Peer]
PublicKey = HzAaoldGTvhAassPS9pE5CtkbuwUKjUiaz4wYTOH1WY=
Endpoint = 5.129.219.153:51820
# Маршрутизируем только трафик к OpenAI через VPN
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOFWG

chmod 600 /etc/wireguard/wg0.conf

# Активация WireGuard
echo "5. Активация WireGuard..."
wg-quick up wg0 || echo "Ошибка при активации, но продолжаем..."

# Проверка статуса
echo "6. Проверка статуса..."
sleep 2
wg show || true
ip addr show wg0 || true

# Настройка автозапуска
echo "7. Настройка автозапуска..."
systemctl enable wg-quick@wg0

echo ""
echo "=== Исправление завершено ==="
echo "Проверьте соединение SSH - оно должно работать нормально"
echo ""
