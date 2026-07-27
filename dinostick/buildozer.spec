[app]

title = Dino Stick
package.name = dinostick
package.domain = org.dinostick

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,wav,ogg,ttf
source.include_patterns = assets/*,assets/*/*

version = 0.1.0

# pyjnius is needed for the WifiManager.MulticastLock that makes UDP
# discovery work on Android at all (see net/discovery.py).
requirements = python3,kivy==2.3.1,pyjnius

# Both are generated from the game's own SKINS data, so the launcher icon can
# never drift from what the game actually looks like. The presplash colour
# matches COLOR_BG, so the splash does not flash a different background before
# the first frame lands.
icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #F5F5F5

orientation = landscape
fullscreen = 1

# INTERNET                     - TCP game traffic + UDP discovery
# ACCESS_NETWORK_STATE         - detect whether we are on a network
# ACCESS_WIFI_STATE            - read Wi-Fi info
# CHANGE_WIFI_MULTICAST_STATE  - REQUIRED to hold a MulticastLock; without it
#                                Android silently drops broadcast packets and
#                                discovery finds nothing.
android.permissions = INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE, CHANGE_WIFI_MULTICAST_STATE

android.api = 34
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a

android.allow_backup = True

# Raw TCP/UDP sockets on the LAN are NOT affected by Android's cleartext-HTTP
# block (that applies to HttpURLConnection/OkHttp), so no network security
# config is needed. Nothing here ever speaks HTTP.

# Answers the SDK licence prompts, so a first build does not stall waiting for
# a keypress it never shows you.
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
