#!/bin/sh
# ==========================================
# TAOUFIK GAMES E2 - Installer Script
# ==========================================

PLUGIN_PATH="/usr/lib/enigma2/python/Plugins/Extensions/TaoufikPlay"
TEMP_DIR="/tmp/taoufik_games_install"

echo "=========================================="
echo "    [+] Installing TAOUFIK GAMES E2...    "
echo "=========================================="

# 1. إيقاف الواجهة مؤقتاً
init 4 2>/dev/null

# 2. تحديث وتثبيت RetroArch تلقائياً
echo "[*] Updating package lists and installing RetroArch..."
opkg update
opkg install enigma2-plugin-systemplugins-retroarch

# 3. إنشاء المجلدات الضرورية
mkdir -p $PLUGIN_PATH/assets
mkdir -p $PLUGIN_PATH/covers
mkdir -p /media/hdd/roms/neogeo
mkdir -p /media/hdd/roms/arcade
mkdir -p /media/hdd/roms/ps2
mkdir -p /media/hdd/roms/dreamcast
mkdir -p /media/hdd/roms/covers

# 4. تحميل ملفات البلوغين
echo "[*] Downloading plugin files..."
rm -rf $TEMP_DIR
mkdir -p $TEMP_DIR
cd $TEMP_DIR

wget -q --no-check-certificate https://raw.githubusercontent.com/TAOUFIK-SAT/TAOUFIK-GAMES-E2/main/plugin.py -O plugin.py

if [ -f "plugin.py" ]; then
    cp plugin.py $PLUGIN_PATH/
    chmod 755 $PLUGIN_PATH/plugin.py
    echo "[✓] Plugin installed successfully!"
else
    echo "[✗] Error: Failed to download plugin.py"
fi

cd /
rm -rf $TEMP_DIR

# 5. إعادة تشغيل واجهة الرسيفر
echo "[*] Restarting Enigma2 GUI..."
init 3
exit 0
