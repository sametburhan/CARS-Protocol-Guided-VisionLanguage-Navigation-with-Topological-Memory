#!/bin/bash

echo "🚀 ROS 2 Sistemleri Başlatılıyor..."

# ROS 2 ortam değişkenlerini yükle
source /opt/ros/humble/setup.bash

# TurtleBot modelini belirle (waffle kameralı modeldir, burger da yapabilirsin)
export TURTLEBOT3_MODEL=waffle 

# Ctrl+C yapıldığında arka planda çalışan tüm ROS süreçlerini temizle
trap "echo -e '\n🛑 Sistem kapatılıyor, tüm düğümler durduruldu.'; kill 0" SIGINT

# 1. Gazebo Simülasyonunu Başlat
echo "🏠 TurtleBot3 House Simülasyonu başlatılıyor..."
ros2 launch turtlebot3_gazebo turtlebot3_house.launch.py &

# Gazebo'nun ayağa kalkması için biraz süre tanı (sorun yaşarsan süreyi artırabilirsin)
sleep 8

# 2. Rosbridge'i Başlat
echo "🌉 Rosbridge WebSocket Sunucusu başlatılıyor..."
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &

echo "✅ Tüm sistemler aktif ve Claude'un bağlantısını bekliyor!"
echo "Çıkış yapmak ve her şeyi kapatmak için Ctrl+C'ye basın."

# Scriptin arka plan işlemlerini beklemesini sağla (kapanmasını engeller)
wait
