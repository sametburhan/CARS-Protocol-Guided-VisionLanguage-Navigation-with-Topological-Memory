from experiment_log import logged_tool
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
mcp = FastMCP("Kamera_Merkezi")

# ==========================================
# 🌉 GLOBAL ROS BAĞLANTISI VE TOPIC TANIMLARI
# ==========================================
ros_client = roslibpy.Ros(host='127.0.0.1', port=9090)
# KÖK ÇÖZÜM 1: Topic objesini her çağrıda yaratmak yerine BİR KERE yaratıyoruz.
camera_topic = roslibpy.Topic(ros_client, '/camera/image_raw', 'sensor_msgs/msg/Image', queue_length=1)


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
@logged_tool("Kamera_Merkezi")
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
        
        return types.TextContent(type="text", text="[GÖRSEL KAYNAĞI: Detaylı Analiz Kamerası]")

    except Exception as e:
        sonuc = f"HATA: Ham görüntü işlenirken hata oluştu: {str(e)}"
        return sonuc


@mcp.tool()
@logged_tool("Kamera_Merkezi")
def hizli_kamera_al():
    """
    SADECE navigasyon, yön bulma ve boşluk/kapı tespiti için bu aracı kullan.
    Düşük boyutludur ve anında yanıt verir.
    """
    baslangic = time.time()
    baglantiyi_garantiye_al()                 # 🆕
    if not ros_client.is_connected:           # 🆕
        sonuc = "🛑 HATA: ROS bağlantısı yok, kamera görüntüsü alınamıyor."
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
            return sonuc
        time.sleep(0.05)
        
    # 3. KANAL ANINDA KAPATILIR: Veri gelir gelmez abonelik iptal edilir.
    # Bu sayede arkada hiçbir bayat veri (buffer) birikemez.
    topic.unsubscribe()

    return [
        types.TextContent(type="text", text="[GÖRSEL KAYNAĞI: RGB Hızlı Kamera]"),
        types.ImageContent(type="image", data=anlik_veri["data"], mimeType="image/jpeg")
    ]
    
if __name__ == "__main__":
    mcp.run()
