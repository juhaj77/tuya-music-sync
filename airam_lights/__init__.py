"""Airam Music Lights - local, LAN-based music visualizer for Airam SmartHome
(Tuya-based) Wi-Fi RGB GU10 spotlights.

Package layout (see README.md for the full architecture description):

- config       Configuration schema + JSON persistence
- audio        WASAPI loopback capture
- dsp          FFT / band-energy extraction / attack-release smoothing
- color        Color mapping engines (RGB / HSV / Custom / Beat Sync / Peak Flash)
- lamps        Local Tuya device control, discovery, multi-lamp manager
- diagnostics  Logging + runtime metrics
- engine       Ties audio -> dsp -> color -> lamps together
- ui           PySide6 desktop UI
"""

__version__ = "0.1.0"
