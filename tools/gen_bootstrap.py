"""
Generate _bootstrap.py — a frozen module that writes natmod .mpy files
to the filesystem on first boot.

Run this before building firmware:
    python3 gen_bootstrap.py

It reads the natmod .mpy files from lib/ and generates _bootstrap.py
containing them as byte literals. When frozen into firmware, _bootstrap.py
runs once on first boot, writes the files, then deletes its marker.

Add to tdeck_manifest.py:
    freeze(_root + "/tools", "_bootstrap.py")

Add to tdeck_node.py (very first lines after gc.collect):
    try:
        import _bootstrap
    except:
        pass
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TDECK_ROOT = os.path.dirname(SCRIPT_DIR)
LIB_DIR = os.path.join(TDECK_ROOT, "lib")

# Files to embed — path relative to TDECK_ROOT, destination on device
FILES = [
    ("lib/ed25519_fast_xtensawin.mpy", "/lib/ed25519_fast_xtensawin.mpy"),
    ("lib/bz2_fast_xtensawin.mpy",     "/lib/bz2_fast_xtensawin.mpy"),
    ("lib/codec2_fast_xtensawin.mpy",  "/lib/codec2_fast_xtensawin.mpy"),
    ("lib/tjpgd_fast_xtensawin.mpy",   "/lib/tjpgd_fast_xtensawin.mpy"),
    ("lib/webp_fast_xtensawin.mpy",    "/lib/webp_fast_xtensawin.mpy"),
]

# Optional data files
OPTIONAL = [
    ("logo.jpg", "/logo.jpg"),
]

out_lines = []
out_lines.append('"""Auto-extract natmod files on first boot."""')
out_lines.append("import os")
out_lines.append("")
out_lines.append("_MARKER = '/.natmods_installed'")
out_lines.append("")
out_lines.append("def _exists(path):")
out_lines.append("    try:")
out_lines.append("        os.stat(path)")
out_lines.append("        return True")
out_lines.append("    except:")
out_lines.append("        return False")
out_lines.append("")
out_lines.append("if not _exists(_MARKER):")
out_lines.append("    print('[bootstrap] First boot — extracting natmod files...')")
out_lines.append("    try:")
out_lines.append("        os.mkdir('/lib')")
out_lines.append("    except:")
out_lines.append("        pass")
out_lines.append("")

all_files = FILES + OPTIONAL
embedded_count = 0

for src_rel, dest in all_files:
    src_abs = os.path.join(TDECK_ROOT, src_rel)
    if not os.path.exists(src_abs):
        print(f"  Skipping {src_rel} (not found)")
        continue

    with open(src_abs, "rb") as f:
        data = f.read()

    size_kb = len(data) / 1024
    print(f"  Embedding {src_rel} ({size_kb:.1f} KB)")

    # Write as hex literal for readability
    hex_str = data.hex()
    out_lines.append(f"    # {os.path.basename(src_rel)} ({len(data)} bytes)")
    out_lines.append(f"    with open('{dest}', 'wb') as _f:")
    out_lines.append(f"        _f.write(bytes.fromhex(")

    # Split hex string into 120-char chunks
    chunk_size = 120
    for i in range(0, len(hex_str), chunk_size):
        chunk = hex_str[i:i + chunk_size]
        if i == 0:
            out_lines.append(f"            '{chunk}'")
        else:
            out_lines.append(f"            '{chunk}'")

    out_lines.append(f"        ))")
    out_lines.append(f"    print('[bootstrap]   {os.path.basename(dest)}')")
    out_lines.append("")
    embedded_count += 1

out_lines.append("    # Mark as done")
out_lines.append("    with open(_MARKER, 'w') as _f:")
out_lines.append("        _f.write('1')")
out_lines.append("    print('[bootstrap] Done. Files extracted to /lib/')")
out_lines.append("")

output_path = os.path.join(SCRIPT_DIR, "_bootstrap.py")
with open(output_path, "w") as f:
    f.write("\n".join(out_lines) + "\n")

total_size = os.path.getsize(output_path) / 1024
print(f"\nGenerated: {output_path}")
print(f"Embedded {embedded_count} files, _bootstrap.py is {total_size:.1f} KB")
print(f"\nAdd to tdeck_manifest.py:")
print(f'    freeze(_root + "/tools", "_bootstrap.py")')
