#!/usr/bin/env python3
"""Build and import the "Transfer via sound" Quick Action for macOS Shortcuts.

Select a 32-hex destination hash in any app, right-click -> Services ->
Transfer via sound, and the Mac plays it as DTMF (tools/dtmf_send.py) for a
T-Deck listening in Find (m, then 0).

    python3 tools/mac/make_shortcut.py

Writes an unsigned shortcut plist, signs it with `shortcuts sign`, and opens
it -- click "Add Shortcut" once. The shortcut runs dtmf_send.py from this
checkout, so keep the repo where it is (or re-run this after moving it).

One-time prerequisite: Shortcuts -> Settings -> Advanced -> Allow Running
Scripts. Optional: bind a key in System Settings -> Keyboard -> Keyboard
Shortcuts -> Services -> Text -> Transfer via sound.
"""

import os
import plistlib
import subprocess
import sys
import tempfile
import uuid

NAME = "Transfer via sound"
SENDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dtmf_send.py"))


def _script():
    # Services and Quick Actions run with a minimal PATH: find a python3.
    # One copy (~4.5 s): the T-Deck is listening already when you pick the
    # menu entry, so the second copy mostly just made you wait.
    return (
        'PY=/opt/homebrew/bin/python3\n'
        '[ -x "$PY" ] || PY=/usr/local/bin/python3\n'
        '[ -x "$PY" ] || PY=/usr/bin/python3\n'
        'exec "$PY" "%s" --once\n' % SENDER
    )


def build():
    """The shortcut as a plist dict: Quick Action on text -> shell script
    with the selected text on stdin."""
    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 4282601983,
            "WFWorkflowIconGlyphNumber": 59772,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["QuickActions", "ActionExtension"],
        "WFQuickActionSurfaces": ["Services"],
        "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
                "WFWorkflowActionParameters": {
                    "UUID": str(uuid.uuid4()).upper(),
                    "Script": _script(),
                    "Shell": "/bin/zsh",
                    "InputMode": "to stdin",
                    "Input": {
                        "Value": {"Type": "ExtensionInput"},
                        "WFSerializationType": "WFTextTokenAttachment",
                    },
                },
            }
        ],
    }


def main():
    if not os.path.exists(SENDER):
        sys.exit("dtmf_send.py not found at " + SENDER)
    out_dir = tempfile.mkdtemp()
    unsigned = os.path.join(out_dir, "unsigned.shortcut")
    signed = os.path.join(out_dir, NAME + ".shortcut")
    with open(unsigned, "wb") as f:
        plistlib.dump(build(), f, fmt=plistlib.FMT_BINARY)
    r = subprocess.run(["shortcuts", "sign", "--mode", "anyone",
                        "--input", unsigned, "--output", signed],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(signed):
        sys.exit("shortcuts sign failed: " + (r.stderr or r.stdout).strip())
    print("Signed:", signed)
    print('Opening it -- click "Add Shortcut".')
    print("Then enable Shortcuts -> Settings -> Advanced -> Allow Running Scripts.")
    subprocess.run(["open", signed], check=False)


if __name__ == "__main__":
    main()
