#!/bin/bash
# ==========================================================================
# CARS — Dayanıklılık Test Paketi
# Kapsam: Sensör kaybı, Timeout, İleri/Geri Engel, Başarılı Hareket
#
# Bu script:
#   1) Gazebo + rosbridge'i başlatır (start_robot.sh içeriği buraya gömülü)
#   2) Sistemlerin ayağa kalkmasını bekler
#   3) test_dayaniklilik.py ile 5 senaryoyu çalıştırır
#   4) Sonuçları özetler ve arka plan süreçlerini temizler
# ==========================================================================

set -u  # tanımsız değişken kullanımını hata say (set -e KASITLI KULLANILMADI,
        # çünkü tek bir test başarısız olsa bile diğerlerinin çalışmasını istiyoruz)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJE_KOK="$(dirname "$SCRIPT_DIR")"

GAZEBO_PID=""
ROSBRIDGE_PID=""

# --------------------------------------------------------------------------
# Temizlik: script nasıl biterse bitsin (Ctrl+C, hata, normal bitiş) arka
# planda kalan ROS süreçlerini kapat.
# --------------------------------------------------------------------------
temizlik() {
    echo -e "\n🛑 Test paketi kapatılıyor, arka plan süreçleri sonlandırılıyor..."
    # Yalnızca bu scriptin başlattığı süreçleri hedefle. `kill 0` çağıran
    # terminalin tüm process group'unu öldürebileceği için kullanılmaz.
    for pid in "$ROSBRIDGE_PID" "$GAZEBO_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            pkill -TERM -P "$pid" 2>/dev/null || true
            kill -TERM "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
}
trap temizlik EXIT INT TERM

echo "=========================================================="
echo "🚀 CARS Dayanıklılık Testleri — Ortam Hazırlanıyor"
echo "=========================================================="

# --------------------------------------------------------------------------
# 1) SİMÜLASYON ORTAMINI BAŞLAT (start_robot.sh içeriği buraya gömülü)
# --------------------------------------------------------------------------
source /opt/ros/humble/setup.bash
export TURTLEBOT3_MODEL=waffle

echo "🏠 TurtleBot3 House Simülasyonu başlatılıyor..."
ros2 launch turtlebot3_gazebo turtlebot3_house.launch.py &
GAZEBO_PID=$!
sleep 8

echo "🌉 Rosbridge WebSocket Sunucusu başlatılıyor..."
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &
ROSBRIDGE_PID=$!
sleep 4

# --------------------------------------------------------------------------
# 2) Rosbridge'in gerçekten dinlediğini doğrula (basit port kontrolü)
# --------------------------------------------------------------------------
echo "🔎 Rosbridge (port 9090) erişilebilirliği kontrol ediliyor..."
if command -v nc >/dev/null 2>&1; then
    if nc -z -w 3 127.0.0.1 9090; then
        echo "✅ Rosbridge portu açık."
    else
        echo "❌ Rosbridge portuna 3 saniyede ulaşılamadı. Testler yine de denenecek ama muhtemelen başarısız olacak."
    fi
else
    echo "⚠️ 'nc' komutu bulunamadı, port kontrolü atlanıyor (ncat/netcat kurulumu önerilir)."
fi

# --------------------------------------------------------------------------
# 3) PYTHON TEST HARNESS'İNİ ÇALIŞTIR
# --------------------------------------------------------------------------
echo ""
echo "=========================================================="
echo "🧪 Testler Çalıştırılıyor"
echo "=========================================================="

cd "$PROJE_KOK" || exit 1
PYTHONPATH="$PROJE_KOK/modules${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$SCRIPT_DIR/test_dayaniklilik.py"
TEST_SONUC=$?

echo ""
echo "=========================================================="
if [ "$TEST_SONUC" -eq 0 ]; then
    echo "✅ TÜM TESTLER BAŞARILI"
else
    echo "❌ BAZI TESTLER BAŞARISIZ (yukarıdaki detaylara bak)"
fi
echo "=========================================================="

exit "$TEST_SONUC"
