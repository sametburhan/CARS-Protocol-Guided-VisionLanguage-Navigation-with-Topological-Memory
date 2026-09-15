from fastmcp import FastMCP
import datetime
import os
import time
import json

_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))
LOG_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "logs"))
os.makedirs(LOG_KLASORU, exist_ok=True)
RUN_MARKER_DOSYASI = os.path.join(LOG_KLASORU, "current_run.txt")

# Yeni MCP Sunucumuzu oluşturuyoruz
mcp = FastMCP("Kartograf_Merkezi")

# Harita, LTM ve STM ile aynı çalışma zamanı bellek dizininde tutulur.
# Yol __file__ üzerinden hesaplandığı için sunucunun başlatıldığı dizinden bağımsızdır.
MEMORY_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "memory"))
os.makedirs(MEMORY_KLASORU, exist_ok=True)
MAP_FILE_PATH = os.path.join(MEMORY_KLASORU, "kognitif_harita.md")

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

@mcp.tool()
def harita_guncelle(mermaid_kodu: str) -> str:
    """
    LTM'deki (Uzun Süreli Bellek) anlamsal ilişkileri ve keşifleri bir Mermaid.js topolojik grafiği olarak kaydetmek için kullanılır.
    Bu aracı sadece yeni bir oda, kapı veya kritik obje keşfettiğinde (LTM güncellendiğinde) çağır.
    
    Parametre:
    - mermaid_kodu: Sadece geçerli Mermaid kodunu içermelidir. Markdown işaretleri (```mermaid) KULLANMA.
      Örnek: "graph TD\n A[Başlangıç] -->|İleri| B[Kapı]"
    """
    baslangic = time.time()
    try:
        zaman_damgasi = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Markdown dosyasını oluştur veya üzerine yaz
        with open(MAP_FILE_PATH, "w", encoding="utf-8") as f:
            f.write(f"# 🗺️ CARS Kognitif Topolojik Harita\n\n")
            f.write(f"*Son Güncelleme: {zaman_damgasi}*\n\n")
            f.write("```mermaid\n")
            f.write(mermaid_kodu.strip() + "\n")
            f.write("\n```\n")
        sonuc = f"Harita başarıyla güncellendi. Dosya: {MAP_FILE_PATH}"
    except Exception as e:
        sonuc = f"Harita kaydedilirken hata oluştu: {str(e)}"

    _log_yaz("Kartograf_Merkezi", "harita_guncelle", {"mermaid_uzunluk": len(mermaid_kodu)}, sonuc, time.time() - baslangic)
    return sonuc

if __name__ == "__main__":
    mcp.run()
