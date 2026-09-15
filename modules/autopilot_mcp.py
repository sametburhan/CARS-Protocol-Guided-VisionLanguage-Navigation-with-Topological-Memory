from fastmcp import FastMCP
import roslibpy
import time
import math
import os
import json

mcp = FastMCP("Otopilot_Merkezi")

# Global değişkenler
ros_client = None
latest_scan_ranges = None
cmd_vel_pub = None
front_distance = 3.5
current_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}

# 🆕 Sensör tazelik takibi
son_lidar_guncelleme = None
son_odom_guncelleme = None
MAX_SENSOR_YAS_SANIYE = 1.0  # bu süreden eski veri "donmuş" sayılır

# Hareket sınırları modül düzeyindedir. Üretim davranışı değişmeden kalır;
# dayanıklılık testleri bu değerleri geçici olarak küçültebilir.
MAX_DONUS = 60.0
MAX_MESAFE = 3.5
GUVENLIK_DURMA_ESIGI_ON = 0.25
GUVENLIK_CEVRE_SINIRI = 0.20
MAX_SURE_DONUS_SANIYE = 10.0
MAX_SURE_MESAFE_SANIYE = 25.0

# 🆕 Log dosyası (her oturum/deney için tek dosya)
_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))
LOG_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "logs"))
LTM_FILE = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "memory", "long_term_memory.json"))
os.makedirs(LOG_KLASORU, exist_ok=True)
RUN_MARKER_DOSYASI = os.path.join(LOG_KLASORU, "current_run.txt")

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
# 🛑 YARDIMCI FONKSİYONLAR
# ==========================================
def araci_durdur():
    global cmd_vel_pub
    if cmd_vel_pub is not None:
        msg = roslibpy.Message({'linear': {'x': 0.0, 'y': 0.0, 'z': 0.0}, 'angular': {'x': 0.0, 'y': 0.0, 'z': 0.0}})
        cmd_vel_pub.publish(msg)

def _guvenli_bolge_kontrolu(sinir=0.20):
    """
    360° yarıçapında engel var mı diye dahili kontrol.
    guvenli_bolge_tarama tool'u VE otopilot hareket döngüsü bu fonksiyonu paylaşır
    (kod tekrarını önlemek ve tutarlılığı garantilemek için).
    Döndürür: (engel_var: bool, tehlikeli_engeller: list)
    """
    global latest_scan_ranges
    if latest_scan_ranges is None:
        return False, []
    num_beams = len(latest_scan_ranges)
    if num_beams == 0:
        return False, []

    tehlikeli_engeller = []
    for idx, r in enumerate(latest_scan_ranges):
        if r is not None and 0.05 < r < sinir:
            aci_derece = (idx / num_beams) * 360.0
            lokal_aci = aci_derece - 360.0 if aci_derece > 180 else aci_derece
            tehlikeli_engeller.append({"aci": lokal_aci, "mesafe": r})

    return (len(tehlikeli_engeller) > 0), tehlikeli_engeller

def _hareket_on_guvenlik_kontrolu():
    """
    Fiziksel harekete izin verilip verilmeyeceğini kontrol eder:
    ROS bağlantısı, sensör verisinin VARLIĞI ve TAZELİĞİ.
    Döndürür: (izinli: bool, hata_mesaji: str)
    """
    global ros_client, latest_scan_ranges, son_lidar_guncelleme, son_odom_guncelleme

    if ros_client is None or not ros_client.is_connected:
        return False, "🛑 HAREKET ENGELLENDİ: ROS bağlantısı yok. Motor komutu gönderilmedi."

    if latest_scan_ranges is None:
        return False, "🛑 HAREKET ENGELLENDİ: Lidar verisi hiç alınmadı. Güvenlik nedeniyle hareket iptal edildi."

    if son_odom_guncelleme is None:
        return False, "🛑 HAREKET ENGELLENDİ: Odometri verisi hiç alınmadı. Konum bilinmiyor, hareket iptal edildi."

    simdi = time.time()
    if (simdi - son_lidar_guncelleme) > MAX_SENSOR_YAS_SANIYE:
        return False, f"🛑 HAREKET ENGELLENDİ: Lidar verisi {simdi - son_lidar_guncelleme:.1f}s önce donmuş (bayat). Hareket iptal edildi."

    if (simdi - son_odom_guncelleme) > MAX_SENSOR_YAS_SANIYE:
        return False, f"🛑 HAREKET ENGELLENDİ: Odometri verisi {simdi - son_odom_guncelleme:.1f}s önce donmuş (bayat). Hareket iptal edildi."

    return True, ""

def aciyi_normalize_et(aci):
    """Açıyı her zaman -180 ile 180 derece arasına hapseder."""
    while aci > 180.0:
        aci -= 360.0
    while aci < -180.0:
        aci += 360.0
    return aci

# ==========================================
# 🌉 ROS 2 BAĞLANTILARI (Lidar ve Odom)
# ==========================================
def lidar_callback(message):
    global front_distance, latest_scan_ranges, son_lidar_guncelleme
    if 'ranges' in message:
        son_lidar_guncelleme = time.time()  # 🆕
        ranges = message['ranges']
        latest_scan_ranges = message['ranges']
        num_beams = len(ranges)
        degrees_to_check = 15
        beams_to_check = int(degrees_to_check * (num_beams / 360.0))

        front_ranges = []
        for i in range(0, beams_to_check):
            r = ranges[i]
            if r is not None and not math.isinf(r) and not math.isnan(r) and r > 0.15:
                front_ranges.append(r)
        for i in range(num_beams - beams_to_check, num_beams):
            r = ranges[i]
            if r is not None and not math.isinf(r) and not math.isnan(r) and r > 0.15:
                front_ranges.append(r)

        if front_ranges:
            front_distance = min(front_ranges)
        else:
            front_distance = 3.5

def odom_callback(message):
    """Robotun Gazebo'daki gerçek konumunu ve açısını okur"""
    global current_pose, son_odom_guncelleme
    try:
        pos = message['pose']['pose']['position']
        ori = message['pose']['pose']['orientation']
        current_pose["x"] = pos["x"]
        current_pose["y"] = pos["y"]

        siny_cosp = 2 * (ori["w"] * ori["z"] + ori["x"] * ori["y"])
        cosy_cosp = 1 - 2 * (ori["y"]**2 + ori["z"]**2)
        yaw_rad = math.atan2(siny_cosp, cosy_cosp)
        current_pose["yaw"] = math.degrees(yaw_rad)

        son_odom_guncelleme = time.time()  # 🆕 — sadece başarılı parse'ta güncellenir
    except KeyError:
        pass

try:
    ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
    ros_client.run() 
    
    cmd_vel_pub = roslibpy.Topic(ros_client, '/cmd_vel', 'geometry_msgs/Twist')
    
    scan_topic = roslibpy.Topic(ros_client, '/scan', 'sensor_msgs/LaserScan')
    scan_topic.subscribe(lidar_callback)

    odom_topic = roslibpy.Topic(ros_client, '/odom', 'nav_msgs/Odometry')
    odom_topic.subscribe(odom_callback)
    
    print("✅ Otopilot MCP Sunucusu: /cmd_vel, /scan ve /odom aktif.")
except Exception as e:
    print(f"❌ ROS Bağlantı Hatası: {e}")

# ==========================================
# 🛠️ CLAUDE İÇİN MCP ARAÇLARI
# ==========================================
@mcp.tool()
def hedef_objeye_yonelme_hesapla(oda_adi: str, obje_adi: str) -> str:
    """
    LTM'de kayıtlı bir objenin (senin mevcut konumuna göre) TAHMİNİ olarak hangi yönde 
    ve ne kadar uzakta olduğunu hesaplayan Kognitif Pusula aracıdır.
    Otopilota göndermen gereken dönüş açısını doğrudan verir.
    """
    import os, json
    
    if not os.path.exists(LTM_FILE):
        return "HATA: LTM dosyası bulunamadı."
        
    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)
        
    try:
        obje_veri = ltm["dag_icerik_agaci"][oda_adi]["objeler"][obje_adi]
        hedef_x = obje_veri["x"]
        hedef_y = obje_veri["y"]
    except KeyError:
        return f"HATA: '{oda_adi}' içinde '{obje_adi}' isimli obje bulunamadı."
        
    global current_pose
    dx = hedef_x - current_pose['x']
    dy = hedef_y - current_pose['y']
    
    mesafe = math.sqrt(dx**2 + dy**2)
    global_hedef_aci_rad = math.atan2(dy, dx)
    global_hedef_aci_deg = math.degrees(global_hedef_aci_rad)
    
    # Lokal açı hesabı
    fark_deg = global_hedef_aci_deg - current_pose['yaw']
    
    # Açıyı -180 ile 180 arasına normalize et
    while fark_deg > 180.0: fark_deg -= 360.0
    while fark_deg < -180.0: fark_deg += 360.0
    
    yon_str = "SOL" if fark_deg > 0 else "SAĞ"
    
    return (
        f"📍 KOGNİTİF PUSULA SONUCU:\n"
        f"- Hedef: {obje_adi} ({oda_adi})\n"
        f"- Tahmini Mesafe: {mesafe:.2f} metre\n"
        f"- Yön: Sana göre {yon_str} tarafta.\n"
        f"KULLANIM TALİMATI: Otopilotu çağırırken donus_acisi_derece parametresi olarak {fark_deg:.1f} kullanın."
    )

@mcp.tool()
def anlik_robot_konumu_al() -> str:
    """
    Robotun ortamdaki GERÇEK global koordinatlarını (x, y, yaw) döndürür.
    Özellikle LTM'ye (Uzun Süreli Bellek) yeni bir oda, obje veya doğum noktası kaydederken
    ezbere değerler girmek yerine kendi gerçek konumunu öğrenmek için bu aracı çağır.
    """
    return f"Gerçek Konum -> X: {current_pose['x']:.2f}, Y: {current_pose['y']:.2f}, Yaw: {current_pose['yaw']:.2f} derece"

@mcp.tool()
def gecmis_gozlem_noktasina_yonel(oda_adi: str, obje_adi: str) -> str:
    """
    Bir obje LTM'de kayıtlı konumunda bulunamadığında, o objenin İLK GÖZLEMLENDİĞİ
    noktaya (gozlem_pozu) dönmek için otopilot açısı/mesafesi hesaplar.
    Ham koordinat döndürmez, sadece hareket komutu verir.
    """
    import os, json

    if not os.path.exists(LTM_FILE):
        return "HATA: LTM dosyası bulunamadı."

    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)

    try:
        hedef = ltm["dag_icerik_agaci"][oda_adi]["objeler"][obje_adi]["gozlem_pozu"]
    except KeyError:
        return f"HATA: '{oda_adi}' içinde '{obje_adi}' için gözlem pozu bulunamadı."

    global current_pose
    dx = hedef["x"] - current_pose['x']
    dy = hedef["y"] - current_pose['y']

    mesafe = math.sqrt(dx**2 + dy**2)
    global_hedef_aci_deg = math.degrees(math.atan2(dy, dx))

    # current_pose['yaw'] zaten DERECE cinsinden — ikinci kez math.degrees() UYGULANMAZ
    fark_deg = global_hedef_aci_deg - current_pose['yaw']
    fark_deg = aciyi_normalize_et(fark_deg)  # dosyada zaten var olan yardımcıyı kullan

    return (
        f"📍 GEÇMİŞ GÖZLEM NOKTASI PUSULASI:\n"
        f"- Hedef: '{obje_adi}' objesinin ilk görüldüğü nokta ({oda_adi})\n"
        f"- Tahmini Mesafe: {mesafe:.2f} metre\n"
        f"KULLANIM TALİMATI: Otopilotu çağırırken hedef_aci_derece parametresi olarak {fark_deg:.1f} kullanın."
    )

@mcp.tool()
def kognitif_hedef_analizi(lidar_sari_aci: float, tahmini_mesafe_metre: float) -> str:
    """
    LLM'in Lidar'da tespit ettiği bir hedefin veya boşluğun doğrulaması için kullanılır.
    Lidar'dan okunan sarı açıyı (0-360) alır, otopilot için GÜVENLİ dönüş komutunu hesaplar.
    Ayrıca objenin LTM'ye kaydedilmesi için kesin Global (X, Y) koordinatlarını çıkartır.
    """
    global current_pose
    
    if lidar_sari_aci > 180:
        donus_komutu = lidar_sari_aci - 360.0
    else:
        donus_komutu = lidar_sari_aci
        
    yaw_rad = math.radians(current_pose['yaw'])
    delta_rad = math.radians(donus_komutu)
    mutlak_aci_rad = yaw_rad + delta_rad
    
    hedef_x = current_pose['x'] + (tahmini_mesafe_metre * math.cos(mutlak_aci_rad))
    hedef_y = current_pose['y'] + (tahmini_mesafe_metre * math.sin(mutlak_aci_rad))
    
    rapor = (
        f"✅ Hipotez Doğrulandı ve Hesaplandı:\n"
        f"- Otopilot Dönüş Komutu: {donus_komutu:.1f}°\n"
        f"- Tahmini Global Koordinat (LTM için) -> X: {hedef_x:.2f}, Y: {hedef_y:.2f}\n"
        f"[UYARI]: Bu koordinatlar KESİN değildir, sensör tabanlı tahmindir. "
        f"LTM'ye kayıt yapılacaksa bu bilgiyi 'ozellikler' alanına not et.\n"
        f"KULLANIM: Otopilotu çağırırken donus_acisi_derece parametresi için {donus_komutu:.1f} kullanın."
    )
    return rapor

@mcp.tool()
def guvenli_bolge_tarama() -> str:
    """
    Aracın etrafındaki 360 derecelik 20 cm (0.2 metre) yarıçaplı güvenlik çemberini tarar.
    Hareket etmeden önce veya hedefe giderken yol üstünde ani bir engel olup olmadığını
    kontrol etmek için kullanılır.
    """
    global latest_scan_ranges
    if latest_scan_ranges is None:
        return "⚠️ HATA: Lidar (Radar) verisi okunamıyor."

    engel_var, tehlikeli_engeller = _guvenli_bolge_kontrolu(sinir=0.20)

    if not engel_var:
        return "✅ GÜVENLİ BÖLGE TEMİZ: 20 cm çapında hiçbir engel tespit edilmedi. Güzergah açık."

    rapor = "🚨 GÜVENLİK UYARISI: Yakın Çevrede Engeller Tespit Edildi!\n\n"
    for engel in tehlikeli_engeller:
        aci = engel["aci"]
        mesafe_cm = engel["mesafe"] * 100
        if -30 <= aci <= 30:
            yon = "ÖN BÖLGE (İleri güzergahı kapatıyor)"
        elif 30 < aci <= 150:
            yon = f"SOL BÖLGE (Açı: +{aci:.1f}°)"
        elif -150 <= aci < -30:
            yon = f"SAĞ BÖLGE (Açı: {aci:.1f}°)"
        else:
            yon = "ARKA BÖLGE"
        rapor += f"- Konum: {yon} | Uzaklık: {mesafe_cm:.1f} cm\n"

    rapor += "\n[MANEVRA TALİMATI]: Eğer bu engeller hedefe gidiş yönündeyse, doğrudan ilerleme! Çarpmamak için Otopilot ile engelin TERSİ yönüne en az 30 derece dönüp, 0.3 metre ilerleyerek etrafından dolan (Waypoint oluştur)."
    return rapor

@mcp.tool()
def lazer_mesafe_olc(hedef_aci: float) -> str:
    """
    Belirtilen tahmini açının etrafındaki (+/- 15 derece) 30 derecelik geniş bir koniyi tarar.
    Bu koni içindeki EN YAKIN engelin/objenin milimetrik mesafesini ve KESİN açısını bulur.
    Ajanın radar görselinden mesafe tahmin etmesi yasaktır; daima bu aracı kullanmalıdır.
    """
    global latest_scan_ranges, front_distance

    # OPTİMİZASYON: Hedef açı zaten öne çok yakınsa (kamera ile 0°'ye hizalanmış),
    # lidar_callback'in sürekli güncellediği front_distance'ı tekrar hesaplamadan kullan.
    if abs(hedef_aci) <= 2.0:
        if front_distance >= 3.5:
            return "Hedef açı (0°): +/- 15° koni içinde bir engel tespit edilemedi. Yol tamamen açık (front_distance)."
        return (f"✅ Lazer Ölçümü Başarılı (canlı front_distance)!\n"
                f"- Senin Tahminin: {hedef_aci}°\n"
                f"- Kesin Açı: 0.0° (ön)\n"
                f"- Objenin Kesin Uzaklığı: {front_distance:.2f} metre\n"
                f"(Not: Otopilotu yönlendirirken '0.0°' açısını kullan.)")

    if latest_scan_ranges is None:
        return "⚠️ HATA: Lidar (Radar) verisi okunamıyor."

    num_beams = len(latest_scan_ranges)
    if num_beams == 0:
        return "⚠️ HATA: Lidar verisi boş."

    if hedef_aci < 0:
        hedef_aci_normalize = 360.0 + hedef_aci
    else:
        hedef_aci_normalize = hedef_aci

    target_idx = int((hedef_aci_normalize % 360) / 360.0 * num_beams)
    window_deg = 15
    window_idx = int(window_deg / 360.0 * num_beams)

    gercek_mesafe = float('inf')
    kesin_aci = hedef_aci

    for i in range(-window_idx, window_idx + 1):
        idx = (target_idx + i) % num_beams
        r = latest_scan_ranges[idx]
        if r is not None and 0.1 < r < 12.0:
            if r < gercek_mesafe:
                gercek_mesafe = r
                hesaplanan_aci = (idx / num_beams) * 360.0
                kesin_aci = hesaplanan_aci - 360.0 if hesaplanan_aci > 180 else hesaplanan_aci

    if gercek_mesafe == float('inf'):
        return f"Hedef açı ({hedef_aci}°): Tarama konisi (+/- {window_deg}°) içinde bir engel tespit edilemedi. Yol tamamen açık."

    return (f"✅ Lazer Ölçümü Başarılı!\n"
            f"- Senin Tahminin: {hedef_aci}°\n"
            f"- Lazerin Bulduğu Kesin Açı: {kesin_aci:.1f}°\n"
            f"- Objenin Kesin Uzaklığı: {gercek_mesafe:.2f} metre\n"
            f"(Not: Otopilotu yönlendirirken Lazerin bulduğu '{kesin_aci:.1f}°' açısını kullan.)")

@mcp.tool()
def kognitif_rota_planla(baslangic_milestone: str, hedef_milestone: str) -> str:
    """
    Robot farklı bir odadaki/bölgedeki hedefe gitmek istediğinde, aradaki duvarlara çarpmaması 
    için hangi odalardan ve kapılardan geçmesi gerektiğini (Adım Adım Rota) hesaplar.
    LTM'deki topolojik iskeleti kullanarak en kısa yolu (BFS algoritması ile) bulur.
    """
    import os, json
    from collections import deque
    
    if not os.path.exists(LTM_FILE):
        return "HATA: LTM dosyası bulunamadı."
        
    with open(LTM_FILE, "r", encoding="utf-8") as f:
        ltm = json.load(f)
        
    iskelet = ltm.get("ltm_topolojik_iskelet", [])
    
    # Graf oluşturma (Çift yönlü geçiş varsayımı)
    graf = {}
    for baglanti in iskelet:
        k = baglanti["kaynak"]
        h = baglanti["hedef"]
        if k not in graf: graf[k] = []
        if h not in graf: graf[h] = []
        graf[k].append(h)
        graf[h].append(k) # Geri dönüş yolunu da ekle
        
    if baslangic_milestone not in graf or hedef_milestone not in graf:
        return f"HATA: {baslangic_milestone} veya {hedef_milestone} haritada (LTM İskeletinde) bulunamadı."

    # BFS (Breadth-First Search) ile En Kısa Yolu Bulma
    kuyruk = deque([[baslangic_milestone]])
    ziyaret_edilenler = set([baslangic_milestone])
    
    while kuyruk:
        yol = kuyruk.popleft()
        mevcut_dugum = yol[-1]
        
        if mevcut_dugum == hedef_milestone:
            # Rotayı LLM'in anlayacağı insan diline çevir
            rota_metni = " -> ".join(yol)
            rapor = (
                f"🗺️ ROTA PLANI OLUŞTURULDU:\n"
                f"Nihai hedefine duvarlara çarpmadan ulaşmak için şu sırayı izlemelisin:\n"
                f"[ROTA]: {rota_metni}\n\n"
                f"TALİMAT: Kuş uçuşu hedefe yönelmeden önce, sırasıyla bu duraklara (Milestone) git."
            )
            return rapor
            
        for komsu in graf.get(mevcut_dugum, []):
            if komsu not in ziyaret_edilenler:
                ziyaret_edilenler.add(komsu)
                yeni_yol = list(yol)
                yeni_yol.append(komsu)
                kuyruk.append(yeni_yol)
                
    return "HATA: Bu iki nokta arasında bilinen (keşfedilmiş) bir yol bulunamadı."

@mcp.tool()
def otopilot_kademeli_hareket(hedef_aci_derece: float, hedef_mesafe_metre: float) -> str:
    """
    Robotu hareket ettiren ANA otopilot aracıdır.
    - Maksimum tek seferlik dönüş: 60 derece
    - Maksimum tek seferlik ilerleme: 3.5 metre (LiDAR max güvenilir görme menzili)
    - ROS bağlantısı yoksa veya Lidar/Odometri verisi eksik/bayatsa hareket BAŞLAMAZ.
    - Dönüş ve mesafe döngülerinde timeout vardır (robot fiziksel sıkışırsa sonsuza kalmaz).
    - Hareket sırasında sensör kaybı olursa motor derhal durur.
    Mesafe hareketi sırasında SÜREKLİ canlı güvenlik kontrolü yapılır:
    (1) front_distance ile uzun menzilli ön çarpışma kontrolü,
    (2) 360° güvenli bölge kontrolü ile yalpalama/açılı çarpışma kontrolü.
    """
    cagri_baslangic = time.time()
    girdi_ozet = {"hedef_aci_derece": hedef_aci_derece, "hedef_mesafe_metre": hedef_mesafe_metre}

    # 🛑 ÖN GÜVENLİK KONTROLÜ
    izinli, hata_mesaji = _hareket_on_guvenlik_kontrolu()
    if not izinli:
        _log_yaz("Otopilot_Merkezi", "otopilot_kademeli_hareket", girdi_ozet, hata_mesaji, time.time() - cagri_baslangic)
        return hata_mesaji

    uygulanacak_aci = min(abs(hedef_aci_derece), MAX_DONUS)
    if hedef_aci_derece < 0:
        uygulanacak_aci = -uygulanacak_aci

    uygulanacak_mesafe = min(abs(hedef_mesafe_metre), MAX_MESAFE)
    if hedef_mesafe_metre < 0:
        uygulanacak_mesafe = -uygulanacak_mesafe

    global cmd_vel_pub, current_pose, front_distance

    baslangic_yaw = current_pose['yaw']
    donus_timeout = False
    sensor_kaybi = False

    if abs(uygulanacak_aci) > 0:
        hedef_mutlak_yaw = aciyi_normalize_et(current_pose['yaw'] + uygulanacak_aci)
        tolerans_derece = 1.5
        donus_baslangic_zaman = time.time()
        while True:
            if time.time() - donus_baslangic_zaman > MAX_SURE_DONUS_SANIYE:
                donus_timeout = True
                break

            izinli, hata_mesaji = _hareket_on_guvenlik_kontrolu()
            if not izinli:
                sensor_kaybi = True
                break

            fark = aciyi_normalize_et(hedef_mutlak_yaw - current_pose['yaw'])
            if abs(fark) <= tolerans_derece:
                break
            angular_speed = 0.4 if fark > 0 else -0.4
            if abs(fark) < 15.0:
                angular_speed = 0.15 if fark > 0 else -0.15
            twist_msg = roslibpy.Message({
                'linear': {'x': 0.0, 'y': 0.0, 'z': 0.0},
                'angular': {'x': 0.0, 'y': 0.0, 'z': angular_speed}
            })
            cmd_vel_pub.publish(twist_msg)
            time.sleep(0.05)
        araci_durdur()
        time.sleep(0.5)

    if sensor_kaybi:
        rapor = f"🛑 SENSÖR/BAĞLANTI KAYBI: Dönüş sırasında {hata_mesaji} Motor durduruldu.\n"
        _log_yaz("Otopilot_Merkezi", "otopilot_kademeli_hareket", girdi_ozet, rapor, time.time() - cagri_baslangic)
        return rapor

    gercek_donus = aciyi_normalize_et(current_pose['yaw'] - baslangic_yaw)

    # ==========================================
    # 📏 MESAFE KONTROLÜ
    # ==========================================
    erken_durduruldu = False
    mesafe_timeout = False
    sensor_kaybi_mesafede = False
    gidilen_mesafe = 0.0

    if abs(uygulanacak_mesafe) > 0:
        baslangic_x = current_pose['x']
        baslangic_y = current_pose['y']
        tolerans_metre = 0.02
        mesafe_baslangic_zaman = time.time()

        while True:
            if time.time() - mesafe_baslangic_zaman > MAX_SURE_MESAFE_SANIYE:
                mesafe_timeout = True
                break

            izinli, hata_mesaji = _hareket_on_guvenlik_kontrolu()
            if not izinli:
                sensor_kaybi_mesafede = True
                break

            gidilen_mesafe = math.sqrt((current_pose['x'] - baslangic_x)**2 + (current_pose['y'] - baslangic_y)**2)
            kalan_mesafe = abs(uygulanacak_mesafe) - gidilen_mesafe

            if kalan_mesafe <= tolerans_metre:
                break

            if uygulanacak_mesafe > 0 and front_distance < GUVENLIK_DURMA_ESIGI_ON:
                erken_durduruldu = True
                break

            engel_var, tehlikeli_engeller = _guvenli_bolge_kontrolu(sinir=GUVENLIK_CEVRE_SINIRI)
            if engel_var:
                carpisma_riski = False
                for engel in tehlikeli_engeller:
                    aci = engel["aci"]
                    if uygulanacak_mesafe > 0 and (-90 <= aci <= 90):
                        carpisma_riski = True
                        break
                    elif uygulanacak_mesafe < 0 and (aci < -90 or aci > 90):
                        carpisma_riski = True
                        break
                if carpisma_riski:
                    erken_durduruldu = True
                    break

            linear_speed = 0.2 if uygulanacak_mesafe > 0 else -0.2
            if kalan_mesafe < 0.15:
                linear_speed = 0.08 if uygulanacak_mesafe > 0 else -0.08

            twist_msg = roslibpy.Message({
                'linear': {'x': linear_speed, 'y': 0.0, 'z': 0.0},
                'angular': {'x': 0.0, 'y': 0.0, 'z': 0.0}
            })
            cmd_vel_pub.publish(twist_msg)
            time.sleep(0.05)

        araci_durdur()
        time.sleep(0.5)
        gidilen_mesafe = math.sqrt((current_pose['x'] - baslangic_x)**2 + (current_pose['y'] - baslangic_y)**2)

    # ==========================================
    # 3. KOGNİTİF GERİ BİLDİRİM
    # ==========================================
    rapor = "✅ FİZİKSEL HAREKET TAMAMLANDI VE MOTORLAR DURDURULDU.\n"

    if abs(hedef_aci_derece) > MAX_DONUS:
        kalan_aci = hedef_aci_derece - uygulanacak_aci
        rapor += f"⚠️ DÖNÜŞ LİMİTİ: İstenen {hedef_aci_derece}°, ancak GÜVENLİK için {uygulanacak_aci}° dönüldü. Gerçekleşen: {gercek_donus:.1f}°. (Kalan hedefin: {kalan_aci}°)\n"
    elif donus_timeout:
        rapor += f"⏱️ DÖNÜŞ TIMEOUT: {MAX_SURE_DONUS_SANIYE}s içinde hedef açıya ulaşılamadı. Gerçekleşen dönüş: {gercek_donus:.1f}° (istenen: {uygulanacak_aci}°). Robot fiziksel olarak sıkışmış olabilir.\n"
    else:
        rapor += f"- Dönüş: İstenen {hedef_aci_derece}° (Gerçekleşen: {gercek_donus:.1f}°) başarıyla tamamlandı.\n"

    if sensor_kaybi_mesafede:
        rapor += f"🛑 SENSÖR/BAĞLANTI KAYBI: Mesafe hareketi sırasında sensör/ROS bağlantısı kesildi, motor durduruldu. Gerçekte gidilen: {gidilen_mesafe:.2f}m (istenen: {hedef_mesafe_metre}m).\n"
    elif mesafe_timeout:
        rapor += f"⏱️ MESAFE TIMEOUT: {MAX_SURE_MESAFE_SANIYE}s içinde hedef mesafeye ulaşılamadı. Gerçekte gidilen: {gidilen_mesafe:.2f}m (istenen: {hedef_mesafe_metre}m). Robot fiziksel olarak sıkışmış olabilir.\n"
    elif erken_durduruldu:
        rapor += f"🚨 CANLI ENGEL TESPİTİ: Yalnızca {gidilen_mesafe:.2f}m gidilebildi, hareket sırasında ani bir engel algılandı ve durduruldu. (Hedef: {hedef_mesafe_metre}m)\n"
    elif abs(hedef_mesafe_metre) > MAX_MESAFE:
        kalan_mesafe = hedef_mesafe_metre - uygulanacak_mesafe
        rapor += f"⚠️ MESAFE LİMİTİ: İstenen {hedef_mesafe_metre}m, ancak GÜVENLİK için {uygulanacak_mesafe}m gidildi. Gerçekte gidilen: {gidilen_mesafe:.2f}m. (Kalan hedefin: {kalan_mesafe}m)\n"
    else:
        rapor += f"- İlerleme: İstenen {hedef_mesafe_metre}m (Gerçekleşen: {gidilen_mesafe:.2f}m) başarıyla tamamlandı.\n"

    rapor += "\n[ZORUNLU TALİMAT]: Şu an güvendesin ve duruyorsun. KESİNLİKLE Aşama 5'i uygulayarak STM'yi güncelle. Ardından Aşama 2'ye dönüp yeni açından kamerayı ve radarı kontrol et. Çevre temizse otopilotu kalan değerlerle tekrar çağır."

    _log_yaz("Otopilot_Merkezi", "otopilot_kademeli_hareket", girdi_ozet, rapor, time.time() - cagri_baslangic)
    return rapor


if __name__ == "__main__":
    mcp.run()
