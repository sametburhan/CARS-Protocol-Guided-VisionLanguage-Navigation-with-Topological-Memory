from fastmcp import FastMCP
import mcp.types as types # MCP standart tipleri
import base64             # Resmi kodlamak için
import roslibpy
import cv2
import numpy as np
from PIL import Image as PILImage
import io
import math
import time
import os
import json

_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))
LOG_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "logs"))
os.makedirs(LOG_KLASORU, exist_ok=True)
RUN_MARKER_DOSYASI = os.path.join(LOG_KLASORU, "current_run.txt")

# 1. MCP Sunucusunu Başlat
mcp = FastMCP("Lidar_Radar_Merkezi")

# Global değişken
latest_scan_ranges = None
son_lidar_guncelleme = None      
MAX_SENSOR_YAS_SANIYE = 1.0      

def _run_log_dosyasini_al():
    """
    Tüm MCP sunucularının (otopilot, kamera, lidar, kartograf, bellek) AYNI
    deney/oturum log dosyasına yazmasını sağlar. İlk açılan sunucu bir run ID
    oluşturur ve marker dosyasına yazar; sonrakiler bunu okuyup kullanır.
    """
    if os.path.exists(RUN_MARKER_DOSYASI):
        with open(RUN_MARKER_DOSYASI, "r") as f:
            run_id = f.read().strip()
    else:
        run_id = time.strftime('%Y%m%d_%H%M%S')
        with open(RUN_MARKER_DOSYASI, "w") as f:
            f.write(run_id)
    return os.path.join(LOG_KLASORU, f"run_{run_id}.jsonl")

RUN_LOG_DOSYASI = _run_log_dosyasini_al()

def _log_yaz(sunucu_adi, tool_adi, girdi: dict, cikti_ozet, sure_saniye: float = None):
    try:
        kayit = {
            "zaman": time.strftime('%Y-%m-%d %H:%M:%S'),
            "sunucu": sunucu_adi,
            "tool": tool_adi,
            "girdi": girdi,
            "cikti_ozet": (cikti_ozet[:300] if isinstance(cikti_ozet, str) else str(cikti_ozet)),
            "sure_s": round(sure_saniye, 3) if sure_saniye is not None else None,
        }
        with open(RUN_LOG_DOSYASI, "a", encoding="utf-8") as f:
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Log yazılamadı: {e}")

# ==========================================
# 🌉 ROS 2 BAĞLANTISI (Rosbridge Üzerinden)
# ==========================================
def lidar_callback(message):
    global latest_scan_ranges, son_lidar_guncelleme
    if 'ranges' in message:
        latest_scan_ranges = message['ranges']
        son_lidar_guncelleme = time.time()

def _ros_baglantisini_kontrol_et():          # 🆕
    """ROS bağlantısı kopmuşsa yeniden bağlanmayı dener."""
    global ros_client
    if not ros_client.is_connected:
        print("⚠️ Lidar MCP: ROS bağlantısı kopuk! Yeniden bağlanılıyor...")
        try:
            ros_client.run()
            time.sleep(1.0)
        except Exception as e:
            print(f"❌ Yeniden bağlanma başarısız: {e}")

def _lidar_verisi_taze_mi():                 # 🆕
    global son_lidar_guncelleme
    if son_lidar_guncelleme is None:
        return False
    return (time.time() - son_lidar_guncelleme) <= MAX_SENSOR_YAS_SANIYE

# Rosbridge 9090 portuna bağlanıyoruz
try:
    ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
    scan_topic = roslibpy.Topic(ros_client, '/scan', 'sensor_msgs/LaserScan')
    scan_topic.subscribe(lidar_callback)
    ros_client.run()
    print("✅ Lidar MCP Sunucusu: ROS köprüsüne bağlandı ve /scan dinleniyor.")
except Exception as e:
    print(f"❌ ROS Bağlantı Hatası: {e}")

# ==========================================
# 🖼️ GÖRÜNTÜ İŞLEME FONKSİYONLARI
# ==========================================
def _encode_image_to_imagecontent(image):
    # 1. Çözünürlüğü ve kaliteyi düşür (Hız için)
    img_copy = image.copy()
    max_size = (320, 240)
    img_copy.thumbnail(max_size)
    buffer = io.BytesIO()
    img_copy.save(buffer, format="JPEG", quality=40, optimize=True)
    
    # 2. Resmi metin formuna (Base64) çevir
    base64_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
    
    # 3. Claude'un yerleşik, standart resim formatında geri döndür!
    return types.ImageContent(
        type="image",
        data=base64_data,
        mimeType="image/jpeg"
    )

def generate_lidar_radar_visual_pil(scan_data, max_range=3.5, img_size=320):
    img = np.zeros((img_size, img_size, 3), dtype=np.uint8)
    center = img_size // 2
    scale = (center - 25) / max_range

    num_beams = len(scan_data)
    angle_min = 0.0
    angle_increment = (2.0 * 3.14159265359) / num_beams

    font = cv2.FONT_HERSHEY_SIMPLEX
    
    # 1. Hangi açılara referans çizgisi (Grid) çekeceğiz? (Her 30 derecede bir)
    grid_angles_deg = range(0, 360, 30)
    
    # Arka plandaki soluk referans çizgilerini çiz
    for angle_deg in grid_angles_deg:
        rad = math.radians(angle_deg)
        x_end = int(center - (max_range * scale * math.sin(rad)))
        y_end = int(center - (max_range * scale * math.cos(rad)))
        cv2.line(img, (center, center), (x_end, y_end), (40, 40, 40), 1) # Koyu gri

    # Grid açılarına denk gelen en yakın engelleri kaydetmek için sözlük
    # Başlangıçta hepsini menzil dışı (max_range + 1) kabul ediyoruz
    closest_obstacles = {deg: max_range + 1 for deg in grid_angles_deg}
    tolerance_deg = 2.0 # O açıya tam denk gelmese bile +/- 2 dereceyi kabul et

    # 2. Lidar Engellerini Çiz ve Grid Çakışmalarını Yakala
    for i, r in enumerate(scan_data):
        if r is None or math.isinf(r) or math.isnan(r) or r < 0.1:
            continue
            
        dist = min(r, max_range)
        angle_rad = angle_min + (i * angle_increment)
        angle_deg_current = math.degrees(angle_rad) % 360
        
        # ROS'ta X öne, Y sola doğrudur. Görüntüde düzeltiyoruz.
        x_m = dist * math.cos(angle_rad)
        y_m = dist * math.sin(angle_rad)
        
        px = int(center - (y_m * scale))
        py = int(center - (x_m * scale))
        
        # Engeli beyaz nokta olarak çiz
        cv2.circle(img, (px, py), 2, (255, 255, 255), -1)

        # Bu engel bizim grid açılarımızdan birine yakın mı?
        for grid_deg in grid_angles_deg:
            # Açı farkını hesapla (359 ile 1 derece arası fark 2'dir mantığıyla)
            diff = abs(angle_deg_current - grid_deg)
            if diff > 180:
                diff = 360 - diff
                
            # Eğer referans çizgisine çok yakınsa ve daha yakın bir engelse kaydet
            if diff <= tolerance_deg:
                if dist < closest_obstacles[grid_deg]:
                    closest_obstacles[grid_deg] = dist

    # 3. Akıllı Açı Yazıları ("Akıllı İletki")
    for grid_deg, dist in closest_obstacles.items():
        if dist <= max_range: # Eğer o açıda gerçekten bir engel yakaladıysak
            rad = math.radians(grid_deg)
            # Yazıyı engelin hafif iç tarafına yazalım ki dışarı taşıp kesilmesin
            text_dist = dist - 0.3 if dist > 0.6 else dist + 0.3
            tx = int(center - (text_dist * scale * math.sin(rad)))
            ty = int(center - (text_dist * scale * math.cos(rad)))
            
            # Sarı renkle sadece sayıyı yaz (Örn: 30, 60, 330)
            cv2.putText(img, str(grid_deg), (tx - 10, ty + 5), font, 0.4, (0, 255, 255), 1)

    # 4. Yön Okunu Çiz (Ön tarafa doğru kırmızı ok)
    ok_uzunlugu = 40
    cv2.arrowedLine(img, (center, center), (center, center - ok_uzunlugu), (0, 0, 255), 2, tipLength=0.3)

    # 5. Robotu Çiz (Merkeze belirgin yeşil daire)
    cv2.circle(img, (center, center), 5, (0, 255, 0), -1)

    # 6. Yön Etiketlerini Ekle (ON, ARKA, SOL, SAG)
    cv2.putText(img, "ON", (center - 15, 15), font, 0.5, (0, 255, 255), 1)
    cv2.putText(img, "ARKA", (center - 25, img_size - 5), font, 0.5, (0, 255, 255), 1)
    cv2.putText(img, "SOL", (5, center + 5), font, 0.5, (0, 255, 255), 1)
    cv2.putText(img, "SAG", (img_size - 35, center + 5), font, 0.5, (0, 255, 255), 1)

    rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return PILImage.fromarray(rgb_img)

# ==========================================
# 🛠️ CLAUDE İÇİN MCP ARACI
# ==========================================
@mcp.tool()
def anlik_lidar_radar_haritasi_al():
    """
    Robotun 360 derece Lidar 'Radar' görüntüsünü alır.
    Kameranın kör noktalarını (arka, yanlar) ve net uzamsal mesafeleri anlamak için kullan.
    (Siyah arkaplan üzerinde beyaz noktalar engelleri, ortadaki yeşil üçgen robotu temsil eder).
    """
    baslangic = time.time()
    global latest_scan_ranges
    _ros_baglantisini_kontrol_et() 

    if not ros_client.is_connected:          # 🆕
        sonuc = "🛑 HATA: ROS bağlantısı yok, radar verisi alınamıyor."
        _log_yaz("Lidar_Radar_Merkezi", "anlik_lidar_radar_haritasi_al", {}, sonuc, time.time() - baslangic)
        return sonuc

    # KÖKLÜ ÇÖZÜM: Her istekte yepyeni bir Lidar taraması garantilemek için eskiyi sil
    latest_scan_ranges = None
    
    timeout = 10.0
    start = time.time()
    
    # İlk ROS mesajı (taze veri) gelene kadar toleransla bekle
    while latest_scan_ranges is None:
        if (time.time() - start) > timeout:
            sonuc = "HATA: Lidar verisi 10 saniye boyunca alınamadı. Rosbridge'in çalıştığından ve Gazebo'nun aktif olduğundan emin ol."
            _log_yaz("Lidar_Radar_Merkezi", "anlik_lidar_radar_haritasi_al", {}, sonuc, time.time() - baslangic)
            return sonuc
        time.sleep(0.1) # Sistemi yormadan bekle

    pil_img = generate_lidar_radar_visual_pil(latest_scan_ranges)
    
    _log_yaz("Lidar_Radar_Merkezi", "anlik_lidar_radar_haritasi_al", {}, "Görsel başarıyla üretildi.", time.time() - baslangic)
    return [
        types.TextContent(type="text", text="[GÖRSEL KAYNAĞI: 360° Lidar / Radar Haritası]"),
        _encode_image_to_imagecontent(pil_img)
    ]

@mcp.tool()
def gecit_genisligi_hesapla(hedef_aci_derece: float) -> str:
    baslangic = time.time()
    global latest_scan_ranges
    _ros_baglantisini_kontrol_et() 

    if not ros_client.is_connected:                     # 🆕
        sonuc = "🛑 HATA: ROS bağlantısı yok, geçit analizi yapılamıyor."
        _log_yaz("Lidar_Radar_Merkezi", "gecit_genisligi_hesapla", {"hedef_aci_derece": hedef_aci_derece}, sonuc, time.time() - baslangic)
        return sonuc

    if latest_scan_ranges is None:
        sonuc = "HATA: Lidar verisi bulunamadı."
        _log_yaz("Lidar_Radar_Merkezi", "gecit_genisligi_hesapla", {"hedef_aci_derece": hedef_aci_derece}, sonuc, time.time() - baslangic)
        return sonuc

    if not _lidar_verisi_taze_mi():                      # 🆕
        yas = time.time() - son_lidar_guncelleme
        sonuc = f"⚠️ UYARI: Lidar verisi {yas:.1f}s önce alınmış (bayat olabilir). Önce anlik_lidar_radar_haritasi_al ile taze veri al, sonra tekrar dene."
        _log_yaz("Lidar_Radar_Merkezi", "gecit_genisligi_hesapla", {"hedef_aci_derece": hedef_aci_derece}, sonuc, time.time() - baslangic)
        return sonuc
        
    num_beams = len(latest_scan_ranges)
    if num_beams == 0:
        sonuc = "HATA: Lidar verisi boş."
        _log_yaz("Lidar_Radar_Merkezi", "gecit_genisligi_hesapla", {"hedef_aci_derece": hedef_aci_derece}, sonuc, time.time() - baslangic)
        return sonuc

    GUVENLI_GENISLIK_M = 0.45
    MAX_GUVENLI_YAKLASMA_ACISI = 35.0  # derece — bu açıdan fazla çaprazlık riskli sayılır

    target_idx = int((hedef_aci_derece % 360) / 360.0 * num_beams)
    window_deg = 30
    window_idx = int(window_deg / 360.0 * num_beams)
    
    min_sol_dist = float('inf'); sol_aci_deg = 0.0
    for i in range(window_idx):
        idx = (target_idx + i) % num_beams
        r = latest_scan_ranges[idx]
        if r is not None and 0.1 < r < 4.0 and r < min_sol_dist:
            min_sol_dist = r
            sol_aci_deg = (idx / float(num_beams)) * 360.0
                
    min_sag_dist = float('inf'); sag_aci_deg = 0.0
    for i in range(window_idx):
        idx = (target_idx - i) % num_beams
        r = latest_scan_ranges[idx]
        if r is not None and 0.1 < r < 4.0 and r < min_sag_dist:
            min_sag_dist = r
            sag_aci_deg = (idx / float(num_beams)) * 360.0

    if min_sol_dist == float('inf') or min_sag_dist == float('inf'):
        return f"✅ {hedef_aci_derece:.1f}° yönünde belirgin bir kenar/engel bulunamadı. Alan tamamen açık ve GÜVENLİ."
        
    aci_farki_rad = math.radians(abs(sol_aci_deg - sag_aci_deg))
    genislik_kare = (min_sol_dist**2) + (min_sag_dist**2) - (2 * min_sol_dist * min_sag_dist * math.cos(aci_farki_rad))
    genislik_m = math.sqrt(max(0, genislik_kare))

    # ============================================
    # 🆕 YAKLAŞMA AÇISI (ÇAPRAZLIK) HESABI
    # ============================================
    
    # İki kenar noktasını Kartezyen'e çevir (robot merkezli)
    sol_rad = math.radians(sol_aci_deg)
    sag_rad = math.radians(sag_aci_deg)
    x1, y1 = min_sol_dist * math.cos(sol_rad), min_sol_dist * math.sin(sol_rad)
    x2, y2 = min_sag_dist * math.cos(sag_rad), min_sag_dist * math.sin(sag_rad)

    # Geçit çizgisinin yönü ve normali (dik yönü)
    gate_dx, gate_dy = (x2 - x1), (y2 - y1)
    gate_line_aci = math.degrees(math.atan2(gate_dy, gate_dx))
    normal_aci = gate_line_aci + 90.0  # geçide dik yaklaşma açısı (iki çözümden biri)

    # Robotun hedefe (hedef_aci_derece) gitme yönü ile geçit normali arasındaki fark
    fark = abs((hedef_aci_derece - normal_aci + 180) % 360 - 180)
    yaklasma_acisi = min(fark, 180 - fark)  # 0°=tam dik, 90°=tam paralel/çapraz

    efektif_genislik = genislik_m * math.cos(math.radians(yaklasma_acisi))

    durum = "GÜVENLİ (Geçilebilir)" if efektif_genislik >= GUVENLI_GENISLIK_M else \
            ("RİSKLİ (ÇAPRAZ YAKLAŞIM — Sıkışma Tehlikesi)" if genislik_m >= GUVENLI_GENISLIK_M else "RİSKLİ (Çok Dar, Sıkışma Tehlikesi)")

    rapor = (
        f"🔍 GEÇİT ANALİZİ SONUCU ({hedef_aci_derece}° Yönü):\n"
        f"- Sol Kenar Referansı: {min_sol_dist:.2f}m uzakta ({sol_aci_deg:.1f}°)\n"
        f"- Sağ Kenar Referansı: {min_sag_dist:.2f}m uzakta ({sag_aci_deg:.1f}°)\n"
        f"- 📐 Ham Geçit Genişliği: {genislik_m:.2f} metre\n"
        f"- 📐 Yaklaşma Açısı (Çaprazlık): {yaklasma_acisi:.1f}° (0°=tam dik, riski yok)\n"
        f"- 📐 EFEKTİF Genişlik (yaklaşma açısı dahil): {efektif_genislik:.2f} metre\n"
        f"- 🤖 Robot Minimum Gereksinimi: {GUVENLI_GENISLIK_M} metre\n"
        f"KARAR: {durum}"
    )
    if "ÇAPRAZ" in durum:
        rapor += (
            f"\n[ÖNERİ]: Doğrudan bu açıyla geçme! Önce {normal_aci:.1f}° civarına daha dik bir "
            f"açıyla hizalanmak için waypoint stratejisi uygula (bkz. protokol: Çapraz Yaklaşım Waypoint Kuralı)."
        )

    _log_yaz("Lidar_Radar_Merkezi", "gecit_genisligi_hesapla", {"hedef_aci_derece": hedef_aci_derece}, rapor, time.time() - baslangic)
    return rapor

if __name__ == "__main__":
    time.sleep(1) # ROS bağlantısının oturması için 1 saniye bekle
    mcp.run()
