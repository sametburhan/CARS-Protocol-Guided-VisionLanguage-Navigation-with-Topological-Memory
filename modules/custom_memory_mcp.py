from experiment_log import logged_tool
from fastmcp import FastMCP
import json
import os
from datetime import datetime
import time
import json

_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))
mcp = FastMCP("Kognitif_Bellek_Merkezi")

BASE_DIR = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "memory"))
LTM_FILE = os.path.join(BASE_DIR, "long_term_memory.json")
STM_FILE = os.path.join(BASE_DIR, "short_term_memory.json")

os.makedirs(BASE_DIR, exist_ok=True)

# 3 Katmanlı Yeni LTM Başlatma
if not os.path.exists(LTM_FILE):
    with open(LTM_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "ltm_topolojik_iskelet": [], # Katman 2: Sadece Odalar/Milestone'lar arası yollar
            "dag_icerik_agaci": {}       # Katman 3: Odaların içindeki objeler ve robotun pozu
        }, f, indent=4)

if not os.path.exists(STM_FILE):
    with open(STM_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=4)

# ==========================================
# 🛠️ DİNAMİK DOSYA KONTROLÜ VE YARDIMCI FONKSİYONLAR
# ==========================================
def ensure_files_exist():
    """Her işlemden önce klasör ve dosyaların var olduğunu garanti eder."""
    os.makedirs(BASE_DIR, exist_ok=True)

    if not os.path.exists(LTM_FILE):
        with open(LTM_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "ltm_topolojik_iskelet": [],
                "dag_icerik_agaci": {}
            }, f, indent=4)

    if not os.path.exists(STM_FILE):
        with open(STM_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=4)

def load_json(file_path):
    ensure_files_exist() # <-- Okumadan önce her şeyin yerinde olduğundan emin ol
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(file_path, data):
    ensure_files_exist() # <-- Yazmadan önce her şeyin yerinde olduğundan emin ol
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ==========================================
# 🧠 YENİ LTM: İSKELET VE İÇERİK (AĞAÇ) ARACI
# ==========================================
@mcp.tool()
@logged_tool("Kognitif_Bellek_Merkezi")
def ltm_kalici_kayit_yap(
    aktif_milestone_adi: str, baglanacagi_eski_milestone: str, gecis_iliskisi: str,
    obje_adi: str, obje_x: float, obje_y: float, robot_x: float, robot_y: float,
    robot_yaw: float, iliskili_obje: str, iliski_tanimi: str, ozellikler: str
) -> str:
    """
    3 Katmanlı Hafıza Kaydı.
    1. Yeni bir oda/milestone keşfedildiyse 'aktif_milestone_adi' ve 'baglanacagi_eski_milestone' girilir.
    2. Sadece obje eklenecekse, 'baglanacagi_eski_milestone' ve 'gecis_iliskisi' için 'Yok' girilir.
    3. Sadece milestone oluşturulup obje eklenmeyecekse, 'obje_adi' için 'Yok' girilir.
    """
    baslangic = time.time()
    girdi = {"aktif_milestone_adi": aktif_milestone_adi, "obje_adi": obje_adi, "iliskili_obje": iliskili_obje}


    ltm = load_json(LTM_FILE)
    rapor = []

    # --- KATMAN 2: TOPOLOJİK İSKELET (Yol/Bağlantı Kaydı) ---
    if baglanacagi_eski_milestone.lower() != "yok":
        baglanti = {
            "kaynak": baglanacagi_eski_milestone,
            "hedef": aktif_milestone_adi,
            "iliski": gecis_iliskisi
        }
        if baglanti not in ltm["ltm_topolojik_iskelet"]:
            ltm["ltm_topolojik_iskelet"].append(baglanti)
            rapor.append(f"İskelet Güncellendi: [{baglanacagi_eski_milestone}] --{gecis_iliskisi}--> [{aktif_milestone_adi}]")

    # --- KATMAN 3: DAG İÇERİK AĞACI (Dal Oluşturma) ---
    if aktif_milestone_adi not in ltm["dag_icerik_agaci"]:
        ltm["dag_icerik_agaci"][aktif_milestone_adi] = {
            "ilk_kesif_pozu": {"x": robot_x, "y": robot_y, "yaw": robot_yaw},
            "objeler": {}
        }
        rapor.append(f"Yeni Dal Oluşturuldu: {aktif_milestone_adi}")

    # --- DALIN İÇİNE OBJE (Yaprak) EKLEME ---
    if obje_adi.lower() != "yok":
        ltm["dag_icerik_agaci"][aktif_milestone_adi]["objeler"][obje_adi] = {
            "x": obje_x,
            "y": obje_y,
            "gozlem_pozu": {"x": robot_x, "y": robot_y, "yaw": robot_yaw},  # YENİ
            "ozellikler": ozellikler,
            "iliskili_obje": iliskili_obje if iliskili_obje.lower() != "yok" else None,   # YENİ
            "iliski_tanimi": iliski_tanimi if iliski_tanimi.lower() != "yok" else None    # YENİ
            #"zaman": datetime.now().strftime("%H:%M:%S")
        }
        rapor.append(f"Obje Eklendi: '{obje_adi}' -> {aktif_milestone_adi} dalının içine yerleştirildi.")

    save_json(LTM_FILE, ltm)
    sonuc = " | ".join(rapor)
    return sonuc

@mcp.tool()
@logged_tool("Kognitif_Bellek_Merkezi")
def ltm_oku() -> str:
    """Robotun LTM iskeletini ve İçerik Ağacını okuması için kullanılır."""
    ltm = load_json(LTM_FILE)

    # LLM matematik halüsinasyonu görmesin diye X, Y ve Yaw verilerini filtreliyoruz (Kognitif Gizleme)
    if "dag_icerik_agaci" in ltm:
        for oda_adi, icerik in ltm["dag_icerik_agaci"].items():
            if "ilk_kesif_pozu" in icerik:
                icerik["ilk_kesif_pozu"] = "KOORDİNATLAR GİZLENDİ"
            if "objeler" in icerik:
                for obje_adi, detaylar in icerik["objeler"].items():
                    # Koordinatları kopyadan sil, ana JSON'da kalmaya devam edecek
                    if "x" in detaylar: del detaylar["x"]
                    if "y" in detaylar: del detaylar["y"]

                    # YENİ: Gözlem pozu koordinatlarını LLM'den gizle
                    if "gozlem_pozu" in detaylar:
                        detaylar["gozlem_pozu"] = "KOORDİNATLAR GİZLENDİ"

    return json.dumps(ltm, indent=2, ensure_ascii=False)

# ==========================================
# ⚡ SHORT TERM MEMORY (STM) ARAÇLARI
# ==========================================
@mcp.tool()
@logged_tool("Kognitif_Bellek_Merkezi")
def stm_anlik_durum_guncelle(son_aksiyon: str, delta_sonuc: str, lokal_durum: str, niyet: str) -> str:
    """
    HER ATOMİK ADIMDA BURAYI GÜNCELLE. (Geçmişi siler, sadece anlık durumu yazar).
    Global koordinat (x,y,yaw) ve uzun görsel betimlemeler kullanmak KESİNLİKLE YASAKTIR.
    Sadece kinematik durumunu ve bir sonraki mikro-niyetini kaydeder.
    """
    baslangic = time.time()
    from datetime import datetime

    # Liste (append) yerine doğrudan tek bir sözlük (dict) oluşturuyoruz.
    # Bu sayede her çağrıldığında eskisinin üzerine yazacak (Sliding Window).
    stm = {
        "zaman": datetime.now().strftime("%H:%M:%S"),
        "son_aksiyon": son_aksiyon,
        "delta_sonuc": delta_sonuc,
        "lokal_durum": lokal_durum,
        "niyet": niyet
    }
    gecmis = load_json(STM_FILE)
    if not isinstance(gecmis, list):
        gecmis = []
    gecmis.append(stm)
    gecmis = gecmis[-4:]  # SADECE son 4 kayıt tutulur
    save_json(STM_FILE, gecmis)
    sonuc = "STM güncellendi. Son 4 adımlık geçmiş korunuyor."
    return sonuc

@mcp.tool()
@logged_tool("Kognitif_Bellek_Merkezi")
def stm_oku() -> str:
    """Son 4 adımlık kısa süreli bellek geçmişini (en yeni en sonda) döndürür."""
    return json.dumps(load_json(STM_FILE), indent=2, ensure_ascii=False)

@mcp.tool()
@logged_tool("Kognitif_Bellek_Merkezi")
def gecit_otesi_gozlem_kaydet(gecit_adi: str, obje_adi: str, tahmini_ozellikler: str) -> str:
    """
    Bir kapı/geçidin ÖTESİNDE, henüz fiziksel olarak girilmemiş bir odada
    görülen belirgin bir objeyi ön-keşif notu olarak kaydeder.
    Bu kayıt dag_icerik_agaci'nin DIŞINDADIR — hedef_objeye_yonelme_hesapla ve
    gecmis_gozlem_noktasina_yonel tarafından ASLA okunmaz, sadece Aşama 0
    stratejik planlaması için bir ipucu deposudur. Koordinat İÇERMEZ (henüz
    fiziksel olarak doğrulanmadığı için) — sadece isim ve betimleme tutar.
    """
    baslangic = time.time()
    ltm = load_json(LTM_FILE)
    if "gecit_otesi_gozlemler" not in ltm:
        ltm["gecit_otesi_gozlemler"] = {}
    if gecit_adi not in ltm["gecit_otesi_gozlemler"]:
        ltm["gecit_otesi_gozlemler"][gecit_adi] = []
    ltm["gecit_otesi_gozlemler"][gecit_adi].append({
        "obje_adi": obje_adi,
        "tahmini_ozellikler": tahmini_ozellikler
    })
    save_json(LTM_FILE, ltm)
    sonuc = f"📌 Ön keşif notu kaydedildi: '{obje_adi}', '{gecit_adi}' geçidinin ötesinde görülüyor."
    return sonuc

if __name__ == "__main__":
    mcp.run()
