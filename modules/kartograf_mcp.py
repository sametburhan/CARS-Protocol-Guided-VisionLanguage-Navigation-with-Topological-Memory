from experiment_log import logged_tool
from fastmcp import FastMCP
import datetime
import os
import time
import json

_BU_DOSYANIN_KONUMU = os.path.dirname(os.path.abspath(__file__))

# Yeni MCP Sunucumuzu oluşturuyoruz
mcp = FastMCP("Kartograf_Merkezi")

# Harita, LTM ve STM ile aynı çalışma zamanı bellek dizininde tutulur.
# Yol __file__ üzerinden hesaplandığı için sunucunun başlatıldığı dizinden bağımsızdır.
MEMORY_KLASORU = os.path.normpath(os.path.join(_BU_DOSYANIN_KONUMU, "..", "memory"))
os.makedirs(MEMORY_KLASORU, exist_ok=True)
MAP_FILE_PATH = os.path.join(MEMORY_KLASORU, "kognitif_harita.md")


@mcp.tool()
@logged_tool("Kartograf_Merkezi")
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

    return sonuc

if __name__ == "__main__":
    mcp.run()
