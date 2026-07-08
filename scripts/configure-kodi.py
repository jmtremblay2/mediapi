#!/usr/bin/env python3
"""Enable Kodi's JSON-RPC web server by seeding its guisettings.xml.

mediapi controls Kodi over HTTP JSON-RPC, which requires "Allow remote control
via HTTP" -- a setting normally toggled in Kodi's GUI. This seeds it headlessly.

IMPORTANT: Kodi rewrites guisettings.xml when it exits, so this must run while
Kodi is STOPPED (install.sh handles the stop/seed/start ordering). It merges
into any existing file, so re-running is safe.

Reads the desired values from the environment (with sensible defaults matching
.env.example):
  MEDIAPI_KODI_PORT, MEDIAPI_KODI_USER, MEDIAPI_KODI_PASSWORD

Usage: configure-kodi.py [path/to/guisettings.xml]
       (default: ~/.kodi/userdata/guisettings.xml)
"""

import os
import sys
import xml.etree.ElementTree as ET

DEFAULT_PATH = os.path.expanduser("~/.kodi/userdata/guisettings.xml")

WANTED = {
    "services.webserver": os.environ.get("MEDIAPI_KODI_WEBSERVER", "true"),
    "services.webserverport": os.environ.get("MEDIAPI_KODI_PORT", "8090"),
    "services.webserverusername": os.environ.get("MEDIAPI_KODI_USER", "kodi"),
    "services.webserverpassword": os.environ.get("MEDIAPI_KODI_PASSWORD", "kodi"),
    "services.webserverauthentication": "true",
    "services.webserverssl": "false",
}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        tree = ET.parse(path)
        root = tree.getroot()
    else:
        root = ET.Element("settings", {"version": "2"})
        tree = ET.ElementTree(root)

    existing = {el.get("id"): el for el in root.findall("setting")}
    for setting_id, value in WANTED.items():
        el = existing.get(setting_id)
        if el is None:
            el = ET.SubElement(root, "setting", {"id": setting_id})
        el.text = value

    tree.write(path, encoding="utf-8", xml_declaration=True)
    print(f"configure-kodi: web server enabled on port {WANTED['services.webserverport']} ({path})")


if __name__ == "__main__":
    main()
