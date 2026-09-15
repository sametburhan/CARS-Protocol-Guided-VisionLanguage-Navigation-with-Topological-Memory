# 🧪 CARS Dayanıklılık Testleri

Bu paket, izole birim testlerinden oluşmaz. Gerçek ROS 2/Gazebo yığınına bağlanan ve bazı durumları kontrollü hata enjeksiyonuyla oluşturan bir **entegrasyon/dayanıklılık test harness'idir**. Sonuçlar; başlangıç pozu, sensör verisinin tazeliği ve simülasyon dünyasındaki engellere bağlı olabilir.

## Ne test ediyor?

| # | Senaryo | Yöntem | Ortama bağımlı mı? |
|---|---|---|---|
| 1 | Sensör/ROS kaybı | `ros_client`, `latest_scan_ranges`, `son_odom_guncelleme` geçici olarak sahteleştirilir | ⚠️ Kısmen — hata enjeksiyonu kontrollü, ancak bazı alt testler çalışan ROS tabanını gerektirir |
| 2 | Timeout (dönüş+mesafe) | `MAX_SURE_DONUS_SANIYE`/`MAX_SURE_MESAFE_SANIYE` geçici olarak 0.05s'e çekilir | ✅ Evet — canlı ve taze sensör verisi ile hareketin engellenmemesi gerekir |
| 3 | İleri engel | Robotun baktığı yönde büyük bir mesafe istenir, güvenlik mekanizmasının tepkisi yorumlanır | ✅ Evet — Gazebo dünyana bağlı |
| 4 | Geri hareket | Küçük bir geri hareket istenir | ✅ Evet — Gazebo dünyana bağlı |
| 5 | Başarılı hareket | Küçük, güvenli bir dönüş + ilerleme | ✅ Evet — başlangıç yönü ve sensör durumuna bağlı |

## Test edilebilir timeout sınırları

Test 2, `modules/autopilot_mcp.py` içindeki aşağıdaki modül sabitlerini test süresince geçici olarak küçültür:

```python
MAX_DONUS = 60.0
MAX_MESAFE = 3.5
GUVENLIK_DURMA_ESIGI_ON = 0.25
GUVENLIK_CEVRE_SINIRI = 0.20
MAX_SURE_DONUS_SANIYE = 10.0
MAX_SURE_MESAFE_SANIYE = 25.0
```

Üretim değerleri test sonunda geri yüklenir. Bu sabitler fonksiyon içine taşınırsa timeout testi çalışmaz ve `ATLANDI` olarak raporlanır. Süre sınırlarının küçültülmesi kontrollüdür; buna rağmen hareket daha önce bir sensör veya engel kontrolüyle kesilirse test beklenen timeout sonucunu üretemez.

## Çalıştırma

```bash
cd /path/to/ros2_ws
./tests/run_tests.sh
```

Bağımsız olarak (Gazebo zaten çalışıyorsa) sadece Python testlerini de çalıştırabilirsin:

```bash
cd /path/to/ros2_ws
PYTHONPATH="$PWD/modules" python3 tests/test_dayaniklilik.py
```

Test harness'i `modules/` yolunu kendisi de `sys.path` içine ekler; yukarıdaki `PYTHONPATH` kullanımı çalıştırma ortamını açık ve yeniden üretilebilir tutmak için gösterilmiştir.

## Ortam değişkenleriyle ayarlama

Test 3/4, senin Gazebo dünyandaki (`turtlebot3_house`) gerçek engel konumlarını bilmediğimiz için **best-effort** çalışır — kesin PASS/FAIL yerine sonucu yorumlar:

```bash
cd /path/to/ros2_ws
CARS_TEST_ILERI_MESAFE=5.0 CARS_TEST_GERI_MESAFE=-1.0 \
PYTHONPATH="$PWD/modules" python3 tests/test_dayaniklilik.py
```

Eğer spawn noktan tamamen açık bir alansa, Test 3 `ATLANDI` (⚠️) döner — bu bir hata değil, yalnızca o yönde engel davranışının doğrulanamadığı anlamına gelir. Benzer şekilde Test 5'te başlangıç yönünde engel bulunursa güvenli duruş gözlenir fakat normal hareket testi `ATLANDI` sayılır. Daha güvenilir entegrasyon testleri için robotu bilinen ve sabit başlangıç pozlarına yerleştir.

## Çıktı

Script sonunda ✅/⚠️/❌ sayılarıyla bir özet basar ve en az bir ❌ varsa exit code `1` ile biter. Yalnızca `ATLANDI` sonuçlarının bulunması exit code'u başarısız yapmaz; dolayısıyla `run_tests.sh` bu durumda "TÜM TESTLER BAŞARILI" yazabilir. Bu ifade, tüm senaryoların kanıtlandığı anlamına gelmez. `ATLANDI` sonuçları deney raporunda ayrıca belirtilmelidir.
