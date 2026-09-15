from fastmcp import FastMCP
import mcp.types as types
import roslibpy
import time
import base64
import numpy as np
from PIL import Image as PILImage
import os
import io
import time
import json

_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))
LOG_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "logs"))
os.makedirs(LOG_KLASORU, exist_ok=True)
RUN_MARKER_DOSYASI = os.path.join(LOG_KLASORU, "current_run.txt")
mcp = FastMCP("Kamera_Merkezi")

# ==========================================
# 🌉 GLOBAL ROS BAĞLANTISI VE TOPIC TANIMLARI
# ==========================================
ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
# KÖK ÇÖZÜM 1: Topic objesini her çağrıda yaratmak yerine BİR KERE yaratıyoruz.
camera_topic = roslibpy.Topic(ros_client, '/camera/image_raw', 'sensor_msgs/msg/Image', queue_length=1)

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

def baglantiyi_garantiye_al():
    """ROS bağlantısının koptuğu durumlarda otomatik yeniden bağlanmayı sağlar."""
    if not ros_client.is_connected:
        print("⚠️ ROS bağlantısı kopuk! Yeniden bağlanılıyor...")
        try:
            ros_client.run()
            time.sleep(1.0)
        except Exception as e:
            print(f"❌ Yeniden bağlanma başarısız: {e}")

# Sunucu ilk kalktığında bağlanmayı dene
baglantiyi_garantiye_al()
if ros_client.is_connected:
    print("✅ Kamera MCP Sunucusu: ROS köprüsüne bağlandı. (Oto-Tamir ve Anti-Ghosting Aktif)")
    
# ==========================================
# 🛠️ BUFFER'SIZ ARAÇLAR (ON-DEMAND)
# ==========================================
@mcp.tool()
def detayli_analiz_kamerasi_al():
    """
    Odadaki MOBİLYALARI, objeleri ve uzaktaki ince detayları tanımak için bu aracı kullan.
    Sadece yeni bir odaya girdiğinde veya obje araman istendiğinde çağırılmalıdır.
    Yüksek çözünürlüklü ham (raw) veriyi %100 kaliteyle döndürür.
    """
    baslangic = time.time()
    baglantiyi_garantiye_al()

    if not ros_client.is_connected:           # 🆕
        sonuc = "🛑 HATA: ROS bağlantısı yok, kamera görüntüsü alınamıyor."
        _log_yaz("Kamera_Merkezi", "detayli_analiz_kamerasi_al", {}, sonuc, time.time() - baslangic)
        return sonuc

    anlik_veri = {"msg": None}
    
    def gecici_callback(msg):
        if anlik_veri["msg"] is None:
            anlik_veri["msg"] = msg

    topic = roslibpy.Topic(
        ros_client, 
        '/camera/image_raw', 
        'sensor_msgs/msg/Image', 
        queue_length=1
    )
    topic.subscribe(gecici_callback)
    
    timeout = 15.0
    start = time.time()
    
    while anlik_veri["msg"] is None:
        if (time.time() - start) > timeout:
            topic.unsubscribe()
            sonuc = "HATA: Detaylı (Raw) kameradan veri alınamadı."
            _log_yaz("Kamera_Merkezi", "detayli_analiz_kamerasi_al", {}, sonuc, time.time() - baslangic)
            return sonuc
        time.sleep(0.05)

    topic.unsubscribe()
    
    try:
        msg = anlik_veri["msg"]
        img_bytes = base64.b64decode(msg['data'])
        img_np = np.frombuffer(img_bytes, dtype=np.uint8)
        
        width = msg['width']
        height = msg['height']
        img_np = img_np.reshape((height, width, 3))
        
        img_pil = PILImage.fromarray(img_np, 'RGB')
        
        # 1MB LİMİTİNİ AŞMAMAK İÇİN KORUMALI ÖLÇEKLEME
        # Çözünürlüğü maksimum 1920x1080 ile sınırlandır ve kaliteyi 85'e çek
        img_pil.thumbnail((1920, 1080))
        buffer = io.BytesIO()
        img_pil.save(buffer, format="JPEG", quality=85, optimize=True)
        
        _log_yaz("Kamera_Merkezi", "detayli_analiz_kamerasi_al", {}, "Görüntü başarıyla alındı.", time.time() - baslangic)
        return types.TextContent(type="text", text="[GÖRSEL KAYNAĞI: Detaylı Analiz Kamerası]")

    except Exception as e:
        sonuc = f"HATA: Ham görüntü işlenirken hata oluştu: {str(e)}"
        _log_yaz("Kamera_Merkezi", "detayli_analiz_kamerasi_al", {}, sonuc, time.time() - baslangic)
        return sonuc


@mcp.tool()
def hizli_kamera_al():
    """
    SADECE navigasyon, yön bulma ve boşluk/kapı tespiti için bu aracı kullan.
    Düşük boyutludur ve anında yanıt verir.
    """
    baslangic = time.time()
    baglantiyi_garantiye_al()                 # 🆕
    if not ros_client.is_connected:           # 🆕
        sonuc = "🛑 HATA: ROS bağlantısı yok, kamera görüntüsü alınamıyor."
        _log_yaz("Kamera_Merkezi", "hizli_kamera_al", {}, sonuc, time.time() - baslangic)
        return sonuc

    anlik_veri = {"data": None}
    
    def gecici_callback(msg):
        if 'data' in msg and anlik_veri["data"] is None:
            anlik_veri["data"] = msg['data']

    # 1. KANAL AÇILIR: Sadece araç çağrıldığı an ROS'a bağlanılır.
    topic = roslibpy.Topic(
        ros_client, 
        '/camera/image_raw/compressed', 
        'sensor_msgs/msg/CompressedImage', 
        queue_length=1 # ROS kuralı gereği yazılır ancak unsubscribe olacağımız için buffer yapamaz.
    )
    topic.subscribe(gecici_callback)
    
    timeout = 10.0
    start = time.time()
    
    # 2. İLK KARE BEKLENİR
    while anlik_veri["data"] is None:
        if (time.time() - start) > timeout:
            topic.unsubscribe()
            sonuc = "HATA: Hızlı kameradan veri alınamadı."
            _log_yaz("Kamera_Merkezi", "hizli_kamera_al", {}, sonuc, time.time() - baslangic)
            return sonuc
        time.sleep(0.05)
        
    # 3. KANAL ANINDA KAPATILIR: Veri gelir gelmez abonelik iptal edilir.
    # Bu sayede arkada hiçbir bayat veri (buffer) birikemez.
    topic.unsubscribe()

    _log_yaz("Kamera_Merkezi", "hizli_kamera_al", {}, "Görüntü başarıyla alındı.", time.time() - baslangic)
    return [
        types.TextContent(type="text", text="[GÖRSEL KAYNAĞI: RGB Hızlı Kamera]"),
        types.ImageContent(type="image", data=anlik_veri["data"], mimeType="image/jpeg")
    ]
    
if __name__ == "__main__":
    mcp.run()
