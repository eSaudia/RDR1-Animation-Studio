"""Import a CodeX WFD/WVD through an external SollumzRDR add-on.

The add-on is intentionally external: SollumzRDR is GPL-licensed and owns the
RDR1 shader/material implementation. This bridge only loads the configured
Blender scene, registers the user's SollumzRDR checkout, imports the XML, and
saves a new scene with a machine-readable report.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import bpy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--addon-dir", type=Path, required=True,
                        help="SollumzRDR package directory, e.g. .../SollumzRDR")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = {"blend": str(args.blend), "xml": str(args.xml), "addon": str(args.addon_dir),
              "imported": False, "kind": args.xml.suffix.lower(), "error": None}
    try:
        if not args.blend.is_file():
            raise FileNotFoundError(args.blend)
        if not args.xml.is_file():
            raise FileNotFoundError(args.xml)
        if not args.addon_dir.is_dir():
            raise FileNotFoundError(args.addon_dir)

        sys.path.insert(0, str(args.addon_dir.parent))
        module_name = args.addon_dir.name
        addon = importlib.import_module(module_name)
        if hasattr(addon, "register"):
            addon.register()

        bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))
        if args.xml.suffix.lower() == ".wfd":
            from importlib import import_module
            importer = import_module(f"{module_name}.wfd.wfdimport")
            importer.import_wfd(str(args.xml.resolve()))
        elif args.xml.suffix.lower() == ".wvd":
            from importlib import import_module
            importer = import_module(f"{module_name}.wvd.wvdimport")
            importer.import_wvd(str(args.xml.resolve()))
        else:
            raise ValueError("SollumzRDR bridge accepts .wfd or .wvd XML")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
        report["imported"] = True
    except Exception as exc:  # Blender must write a report even on add-on errors.
        report["error"] = f"{type(exc).__name__}: {exc}"
    report_path = args.output.with_suffix(".sollumz-report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["imported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
