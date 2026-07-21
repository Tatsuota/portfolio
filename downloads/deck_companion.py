#!/usr/bin/env python3
"""
Deck Companion - runs ON the Steam Deck (EmuDeck).

Validates games that were synced from the PC and makes sure each one has
everything it needs to launch:
  * checks Emulation/bios for the firmware each system requires
  * verifies every .cue points at files that actually arrived
  * confirms multi-disc .m3u playlists reference real files
  * refreshes the game library so new titles show up

BIOS is only checked, never fetched - console firmware is copyrighted and
must come from your own hardware. This script needs nothing beyond the
Python that already ships on the Deck (stdlib only).

Usage (Desktop Mode > Konsole):
    python3 deck_companion.py            # validate everything, print a report
    python3 deck_companion.py --refresh  # validate, then refresh the library
    python3 deck_companion.py --json     # machine-readable report (for the PC)

You can also drop this in ~/ and run it right after a sync.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

EMU = Path(os.path.expanduser("~/Emulation"))
ROMS = EMU / "roms"
BIOS = EMU / "bios"

SYSTEM_NAMES = {
    "psx": "PlayStation 1", "ps2": "PlayStation 2", "saturn": "Sega Saturn",
    "segacd": "Sega CD", "dreamcast": "Dreamcast", "gba": "Game Boy Advance",
    "psp": "PSP",
}

# Firmware each system needs, expressed as a list of requirement GROUPS.
# Every group must be satisfied (AND across groups); within a group, ANY ONE
# filename satisfies it (OR - these are regional/alternate dumps of the same
# firmware, not separate requirements). "ANY_BIN" is a special marker meaning
# "some .bin file must exist" - PS2 BIOS is identified by PCSX2 from file
# content, not a fixed filename, so we can't check for an exact name.
BIOS_REQUIREMENTS = {
    "psx": [("scph5500.bin", "scph5501.bin", "scph5502.bin")],   # any one region dump
    "ps2": [("ANY_BIN",)],                                       # any PS2 BIOS dump
    "saturn": [("sega_101.bin", "mpr-17933.bin")],                # any one region dump
    "segacd": [("bios_CD_U.bin", "bios_CD_E.bin", "bios_CD_J.bin")],  # any one region dump
    "dreamcast": [("dc_boot.bin",), ("dc_flash.bin",)],           # both required
    "gba": [("gba_bios.bin",)],                                   # optional, checked anyway
}

DISC_IMAGE_EXTS = {".cue", ".chd", ".iso", ".pbp", ".img", ".ccd"}


def bios_present(names_in_bios, group):
    """group is a tuple of alternative filenames; True if any is present."""
    for name in group:
        if name == "ANY_BIN":
            if any(n.endswith(".bin") for n in names_in_bios):
                return True
        elif name.lower() in names_in_bios:
            return True
    return False


def _group_label(group):
    if group == ("ANY_BIN",):
        return "any PS2 BIOS dump (.bin) in Emulation/bios"
    return " or ".join(group)


def cue_refs(cue_path: Path):
    refs = []
    for line in cue_path.read_text(errors="ignore").splitlines():
        s = line.strip()
        if s.upper().startswith("FILE ") and '"' in s:
            a = s.find('"')
            b = s.find('"', a + 1)
            if b != -1:
                refs.append(s[a + 1:b])
    return refs


def validate():
    report = {"systems": {}, "missing_bios": [], "broken": [], "ok": 0}
    if not ROMS.exists():
        report["error"] = f"No roms folder at {ROMS}"
        return report

    bios_names = {p.name.lower() for p in BIOS.glob("*")} if BIOS.exists() else set()

    for sysdir in sorted(ROMS.iterdir()):
        if not sysdir.is_dir():
            continue
        system = sysdir.name
        games = [p for p in sysdir.iterdir() if p.is_file() or p.is_dir()]
        game_files = [p for p in sysdir.iterdir() if p.is_file()]
        if not games:
            continue
        entry = {"count": len([p for p in games if not p.name.startswith(".")])}

        # cue / m3u integrity
        for p in game_files:
            ext = p.suffix.lower()
            if ext == ".cue":
                for ref in cue_refs(p):
                    if not (sysdir / ref).exists():
                        report["broken"].append(f"{system}/{p.name} -> missing {ref}")
            elif ext == ".m3u":
                for line in p.read_text(errors="ignore").splitlines():
                    name = line.strip()
                    if name and not (sysdir / name).exists():
                        report["broken"].append(f"{system}/{p.name} -> missing {name}")

        # bios needs (only if this system actually has disc-based media / is gba)
        has_disc = any(p.suffix.lower() in DISC_IMAGE_EXTS for p in game_files)
        if system in BIOS_REQUIREMENTS and (has_disc or system == "gba"):
            missing_groups = [g for g in BIOS_REQUIREMENTS[system]
                               if not bios_present(bios_names, g)]
            if missing_groups:
                pretty = SYSTEM_NAMES.get(system, system)
                for g in missing_groups:
                    report["missing_bios"].append(f"{pretty}: {_group_label(g)}")

        report["systems"][system] = entry
        report["ok"] += entry["count"]
    return report


def refresh_library():
    """Ask the running frontend to reload games. EmuDeck uses ES-DE."""
    # ES-DE watches its gamelists; a restart is the reliable cross-version way.
    for cmd in (
        ["systemctl", "--user", "restart", "es-de"],
        ["pkill", "-f", "emulationstation"],
    ):
        try:
            subprocess.run(cmd, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            continue
    return ("If you launch games through Steam instead, open EmuDeck > "
            "Steam ROM Manager and click 'Add games' to import the new titles.")


def print_report(report):
    if "error" in report:
        print("ERROR:", report["error"])
        return
    print(f"Validated {report['ok']} games across "
          f"{len(report['systems'])} systems.\n")
    if report["broken"]:
        print("Incomplete games (missing companion files):")
        for b in report["broken"]:
            print("  -", b)
        print()
    if report["missing_bios"]:
        print("Missing BIOS (add to ~/Emulation/bios from your own console):")
        for b in report["missing_bios"]:
            print("  -", b)
        print()
    if not report["broken"] and not report["missing_bios"]:
        print("Everything is complete. All synced games are ready to launch.")


def main():
    ap = argparse.ArgumentParser(description="Validate & refresh synced games on the Deck")
    ap.add_argument("--refresh", action="store_true", help="refresh the game library after validating")
    ap.add_argument("--json", action="store_true", help="output the report as JSON")
    args = ap.parse_args()

    report = validate()

    if args.refresh and "error" not in report:
        report["refresh_note"] = refresh_library()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
        if report.get("refresh_note"):
            print(report["refresh_note"])

    # non-zero exit if anything needs attention (handy for the PC to detect)
    sys.exit(1 if report.get("broken") or report.get("missing_bios") else 0)


if __name__ == "__main__":
    main()
