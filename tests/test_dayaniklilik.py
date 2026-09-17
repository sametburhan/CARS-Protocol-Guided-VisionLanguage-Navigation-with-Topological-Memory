#!/usr/bin/env python3
# ==========================================================================
# CARS — Dayanıklılık Test Harness
#
# Bu script MCP protokolünü DEĞİL, autopilot_mcp.py modülünü DOĞRUDAN import
# ederek tool fonksiyonlarını çağırır (fastmcp'nin @mcp.tool() dekoratörü
# genelde orijinal fonksiyonu değiştirmeden bırakır; farklı davranırsa
# _cagir() yardımcı fonksiyonu .fn erişimini de dener).
#
# ÖN KOŞUL: autopilot_mcp.py'deki MAX_DONUS/MAX_MESAFE/GUVENLIK_*/MAX_SURE_*
# sabitleri fonksiyon içinden modül (global) düzeyine taşınmış olmalı —
# aksi halde Test 2 (Timeout) çalışmaz (bkz. testability_patch_aciklama.md).
#
# Test 3 ve 4 (İleri/Geri Engel) GERÇEK simülasyon ortamına bağımlıdır —
# robotun spawn noktasındaki gerçek engel konumlarını bilmiyoruz, bu yüzden
# best-effort çalışır ve sonucu YORUMLAYARAK raporlar (kesin PASS/FAIL yerine).
# ==========================================================================

import sys
import os
import time

PROJE_KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODUL_KLASORU = os.path.join(PROJE_KOK, "modules")
sys.path.insert(0, MODUL_KLASORU)

from experiment_log import start_run, write_event
from test_summary import summarize, motion_completed

RUN_ID = start_run(metadata={"kind": "durability_tests"})
print(f"Deney run-id: {RUN_ID}")
SONUCLAR = []
BASARILI = []
BASARISIZ = []
ATLANAN = []


def baslik(ad):
    print(f"\n{'='*60}\n▶ {ad}\n{'='*60}")


def sonuc_yaz(ad, gecti, detay=""):
    record = {"name": ad, "status": "passed" if gecti is True else "failed" if gecti is False else "skipped", "detail": detay}
    SONUCLAR.append(record)
    write_event(dict(record, event="test_result"), RUN_ID)
    if gecti is True:
        print(f"  ✅ BAŞARILI — {detay}")
        BASARILI.append(ad)
    elif gecti is False:
        print(f"  ❌ BAŞARISIZ — {detay}")
        BASARISIZ.append(ad)
    else:  # None => atlandı
        print(f"  ⚠️  ATLANDI — {detay}")
        ATLANAN.append(ad)


def _cagir(tool_obj, *args, **kwargs):
    """fastmcp sürüm farkına karşı: fonksiyonu doğrudan ya da .fn üzerinden çağırmayı dener."""
    return getattr(tool_obj, "fn", tool_obj)(*args, **kwargs)


# --------------------------------------------------------------------------
# Modülü import et (bu satır gerçek ROS bağlantısını da başlatır)
# --------------------------------------------------------------------------
print("📦 autopilot_mcp modülü import ediliyor (gerçek ROS bağlantısı denenecek)...")
try:
    import autopilot_mcp as ap
except Exception as e:
    print(f"❌ KRİTİK: autopilot_mcp.py import edilemedi: {e}")
    write_event({"event": "test_summary", "status": "error", "exit_code": 2, "detail": str(e)}, RUN_ID)
    sys.exit(2)

print("⏳ ROS bağlantısının ve ilk sensör verilerinin oturması için 3 saniye bekleniyor...")
time.sleep(3.0)


# ==========================================================================
# TEST 1 — SENSÖR / ROS BAĞLANTI KAYBI
# ==========================================================================
baslik("TEST 1: Sensör / ROS Bağlantı Kaybında Hareketin Engellenmesi")


class _SahteBaglanti:
    is_connected = False


try:
    # --- 1a: ROS bağlantısı yokmuş gibi davran ---
    orijinal_ros_client = ap.ros_client
    ap.ros_client = _SahteBaglanti()
    sonuc = _cagir(ap.otopilot_kademeli_hareket, 0.0, 0.3)
    ap.ros_client = orijinal_ros_client  # HER DURUMDA geri yükle

    gecti = "HAREKET ENGELLENDİ" in sonuc and "ROS bağlantısı yok" in sonuc
    sonuc_yaz("1a-ROS_baglanti_yok", gecti, sonuc.splitlines()[0])
except Exception as e:
    ap.ros_client = orijinal_ros_client
    sonuc_yaz("1a-ROS_baglanti_yok", False, f"Beklenmeyen hata: {e}")

try:
    # --- 1b: Lidar verisi hiç yokmuş gibi davran ---
    orijinal_scan = ap.latest_scan_ranges
    ap.latest_scan_ranges = None
    sonuc = _cagir(ap.otopilot_kademeli_hareket, 0.0, 0.3)
    ap.latest_scan_ranges = orijinal_scan

    gecti = "HAREKET ENGELLENDİ" in sonuc and "Lidar verisi hiç alınmadı" in sonuc
    sonuc_yaz("1b-Lidar_verisi_yok", gecti, sonuc.splitlines()[0])
except Exception as e:
    ap.latest_scan_ranges = orijinal_scan
    sonuc_yaz("1b-Lidar_verisi_yok", False, f"Beklenmeyen hata: {e}")

try:
    # --- 1c: Odometri verisi bayat (2 saniye önce, MAX_SENSOR_YAS_SANIYE=1.0 aşılıyor) ---
    orijinal_odom_zaman = ap.son_odom_guncelleme
    ap.son_odom_guncelleme = time.time() - 2.0
    sonuc = _cagir(ap.otopilot_kademeli_hareket, 0.0, 0.3)
    ap.son_odom_guncelleme = orijinal_odom_zaman

    gecti = "HAREKET ENGELLENDİ" in sonuc and "Odometri verisi" in sonuc and "bayat" in sonuc
    sonuc_yaz("1c-Odometri_bayat", gecti, sonuc.splitlines()[0])
except Exception as e:
    ap.son_odom_guncelleme = orijinal_odom_zaman
    sonuc_yaz("1c-Odometri_bayat", False, f"Beklenmeyen hata: {e}")


# ==========================================================================
# TEST 2 — TIMEOUT (dönüş ve mesafe)
# ==========================================================================
baslik("TEST 2: Dönüş/Mesafe Timeout")

if not hasattr(ap, "MAX_SURE_DONUS_SANIYE") or not hasattr(ap, "MAX_SURE_MESAFE_SANIYE"):
    sonuc_yaz(
        "2-Timeout",
        None,
        "MAX_SURE_DONUS_SANIYE / MAX_SURE_MESAFE_SANIYE modül düzeyinde değil "
        "(bkz. testability_patch_aciklama.md) — bu test için otopilot_mcp.py'de "
        "bu sabitleri fonksiyon dışına, modül düzeyine taşımalısın."
    )
else:
    try:
        orijinal_donus_sure = ap.MAX_SURE_DONUS_SANIYE
        orijinal_mesafe_sure = ap.MAX_SURE_MESAFE_SANIYE

        # Süreleri gülünç derecede kısa yap ki gerçek robot hareketi tamamlanamadan zaman aşımına uğrasın
        ap.MAX_SURE_DONUS_SANIYE = 0.05
        ap.MAX_SURE_MESAFE_SANIYE = 0.05

        sonuc_donus = _cagir(ap.otopilot_kademeli_hareket, 45.0, 0.0)
        sonuc_mesafe = _cagir(ap.otopilot_kademeli_hareket, 0.0, 1.0)

        ap.MAX_SURE_DONUS_SANIYE = orijinal_donus_sure
        ap.MAX_SURE_MESAFE_SANIYE = orijinal_mesafe_sure

        gecti_donus = "TIMEOUT" in sonuc_donus and "⏱️" in sonuc_donus
        gecti_mesafe = "TIMEOUT" in sonuc_mesafe and "⏱️" in sonuc_mesafe

        sonuc_yaz("2a-Donus_timeout", gecti_donus, sonuc_donus.splitlines()[-2] if gecti_donus else sonuc_donus[:120])
        sonuc_yaz("2b-Mesafe_timeout", gecti_mesafe, sonuc_mesafe.splitlines()[-2] if gecti_mesafe else sonuc_mesafe[:120])
    except Exception as e:
        ap.MAX_SURE_DONUS_SANIYE = orijinal_donus_sure
        ap.MAX_SURE_MESAFE_SANIYE = orijinal_mesafe_sure
        sonuc_yaz("2-Timeout", False, f"Beklenmeyen hata: {e}")


# ==========================================================================
# TEST 3 — İLERİ ENGEL (best-effort, ortama bağımlı)
# ==========================================================================
baslik("TEST 3: İleri Yönde Engel Tespiti (ortama bağımlı, best-effort)")
print("  ℹ️  Bu test, robotun mevcut baktığı yönde gerçek bir engel olup olmadığına göre yorumlanır.")
print("  ℹ️  Kendi Gazebo dünyanda bu senaryoyu garanti etmek için robotu bilerek bir duvara")
print("      yakın bir konuma spawn edebilir veya İLERI_MESAFE değerini büyütebilirsin.")

ILERI_MESAFE = float(os.environ.get("CARS_TEST_ILERI_MESAFE", "3.0"))

try:
    sonuc = _cagir(ap.otopilot_kademeli_hareket, 0.0, ILERI_MESAFE)
    if "CANLI ENGEL TESPİTİ" in sonuc:
        sonuc_yaz("3-Ileri_engel", True, "Hareket sırasında engel algılandı ve robot durduruldu.")
    elif "LİMİT" in sonuc:
        sonuc_yaz("3-Ileri_engel", None, "Komut sınırlandı; engel algılama doğrulanamadı.")
    elif motion_completed(sonuc, "İlerleme"):
        sonuc_yaz("3-Ileri_engel", None, f"{ILERI_MESAFE}m boyunca hiç engel yok — bu yönde test edilemedi, alan açık.")
    else:
        sonuc_yaz("3-Ileri_engel", False, f"Beklenmeyen rapor formatı: {sonuc[:150]}")
except Exception as e:
    sonuc_yaz("3-Ileri_engel", False, f"Beklenmeyen hata: {e}")


# ==========================================================================
# TEST 4 — GERİ HAREKET (180° kontrolü / açık alan)
# ==========================================================================
baslik("TEST 4: Geri Yönde Hareket (180° güvenlik kontrolü, best-effort)")

GERI_MESAFE = float(os.environ.get("CARS_TEST_GERI_MESAFE", "-0.5"))

try:
    sonuc = _cagir(ap.otopilot_kademeli_hareket, 0.0, GERI_MESAFE)
    if "CANLI ENGEL TESPİTİ" in sonuc:
        sonuc_yaz("4-Geri_engel", True, "Arkada engel doğru şekilde tespit edilip hareket durduruldu.")
    elif motion_completed(sonuc, "İlerleme"):
        sonuc_yaz("4-Geri_engel", None, "Geri hareket tamamlandı; arkada engel bulunmadığından engel algılama doğrulanamadı.")
    else:
        sonuc_yaz("4-Geri_engel", False, f"Beklenmeyen rapor formatı: {sonuc[:150]}")
except Exception as e:
    sonuc_yaz("4-Geri_engel", False, f"Beklenmeyen hata: {e}")


# ==========================================================================
# TEST 5 — BAŞARILI HAREKET (kontrol/sağlık testi)
# ==========================================================================
baslik("TEST 5: Normal Koşulda Başarılı Hareket ve Gerçek Mesafe Raporu")

try:
    # Önce küçük bir dönüş (limitin altında, hızlıca tamamlanmalı)
    sonuc_donus = _cagir(ap.otopilot_kademeli_hareket, 10.0, 0.0)
    donus_ok = motion_completed(sonuc_donus, "Dönüş")

    # Küçük, güvenli bir ileri hareket. Engel nedeniyle erken durmak güvenli
    # davranıştır ama "normal hareket başarıyla tamamlandı" kanıtı değildir.
    sonuc_mesafe = _cagir(ap.otopilot_kademeli_hareket, 0.0, 0.2)
    mesafe_ok = motion_completed(sonuc_mesafe, "İlerleme")

    sonuc_yaz("5a-Basarili_donus", donus_ok, sonuc_donus.splitlines()[1] if donus_ok else sonuc_donus[:150])
    if "CANLI ENGEL TESPİTİ" in sonuc_mesafe:
        sonuc_yaz(
            "5b-Basarili_hareket",
            None,
            "Başlangıç yönünde engel bulundu; güvenlik katmanı doğru şekilde durdu ancak normal hareket doğrulanamadı."
        )
    else:
        sonuc_yaz("5b-Basarili_hareket", mesafe_ok, sonuc_mesafe.splitlines()[1] if mesafe_ok else sonuc_mesafe[:150])
except Exception as e:
    sonuc_yaz("5-Basarili_hareket", False, f"Beklenmeyen hata: {e}")


# ==========================================================================
# ÖZET
# ==========================================================================
baslik("ÖZET")
print(f"✅ Başarılı : {len(BASARILI)}  {BASARILI}")
print(f"⚠️  Atlanan  : {len(ATLANAN)}  {ATLANAN}")
print(f"❌ Başarısız: {len(BASARISIZ)}  {BASARISIZ}")

ozet = summarize(SONUCLAR)
write_event(dict(ozet, event="test_summary"), RUN_ID)
print(f"Sonuç: {ozet['status']} | run-id: {RUN_ID}")
sys.exit(ozet['exit_code'])
