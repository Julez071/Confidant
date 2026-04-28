"""
Build script for Confidant.
Converts the icon, installs PyInstaller if needed, and packages the app.

Usage: python build.py
"""

import os
import sys
import subprocess
import shutil

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(DIRECTORY, "dist", "Confidant")


def convert_icon():
    """Convert icon.png to icon.ico for the Windows executable."""
    png_path = os.path.join(DIRECTORY, "static", "icon.png")
    ico_path = os.path.join(DIRECTORY, "static", "icon.ico")
    
    if os.path.exists(ico_path):
        print(f"[Build] icon.ico already exists ({os.path.getsize(ico_path)} bytes)")
        return ico_path
    
    if not os.path.exists(png_path):
        print("[Build] WARNING: static/icon.png not found — exe will use default icon")
        return None
    
    try:
        from PIL import Image
        img = Image.open(png_path)
        
        # Ensure square by cropping to center
        w, h = img.size
        if w != h:
            size = min(w, h)
            left = (w - size) // 2
            top = (h - size) // 2
            img = img.crop((left, top, left + size, top + size))
        
        img.save(ico_path, format='ICO',
                 sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        print(f"[Build] Converted icon.png -> icon.ico ({os.path.getsize(ico_path)} bytes)")
        return ico_path
    except ImportError:
        print("[Build] Pillow not installed — run: pip install Pillow")
        return None
    except Exception as e:
        print(f"[Build] Icon conversion failed: {e}")
        return None


def ensure_pyinstaller():
    """Install PyInstaller if not present."""
    try:
        import PyInstaller
        print(f"[Build] PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("[Build] Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("[Build] PyInstaller installed")


def build():
    """Run PyInstaller to build the executable."""
    ico_path = convert_icon()
    ensure_pyinstaller()
    
    # Build the PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "Confidant",
        "--onedir",
        "--windowed",          # No console window
        "--noconfirm",         # Overwrite output without asking
        "--clean",             # Clean cache before building
        
        # Data files: static assets and template files for first-run seeding
        "--add-data", f"static{os.pathsep}static",
        "--add-data", f"character.md.bak{os.pathsep}.",
        "--add-data", f"system_prompt.md.bak{os.pathsep}.",
        
        # Hidden imports that PyInstaller can't auto-detect
        "--hidden-import", "webview",
        "--hidden-import", "bottle",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        "--hidden-import", "keyring.backends",
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "webauthn",
        "--hidden-import", "webauthn.helpers",
        "--hidden-import", "webauthn.helpers.structs",
        
        # Collect all submodules for packages that use dynamic imports
        "--collect-all", "webauthn",
        "--collect-all", "keyring",
        "--collect-all", "webview",
        
        # Entry point
        "app.py",
    ]
    
    # Add icon if available
    if ico_path:
        cmd.insert(cmd.index("--noconfirm"), f"--icon={ico_path}")
    
    print(f"\n[Build] Running PyInstaller...")
    print(f"  Command: {' '.join(cmd)}\n")
    
    result = subprocess.run(cmd, cwd=DIRECTORY)
    
    if result.returncode != 0:
        print("\n[Build] FAILED — check the output above for errors.")
        sys.exit(1)
    
    # Verify output
    exe_path = os.path.join(DIST_DIR, "Confidant.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n[Build] SUCCESS!")
        print(f"  Executable: {exe_path} ({size_mb:.1f} MB)")
        print(f"  Folder:     {DIST_DIR}")
        
        # List the output contents
        total_size = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, dn, filenames in os.walk(DIST_DIR)
            for f in filenames
        )
        print(f"  Total size: {total_size / (1024 * 1024):.1f} MB")
        print(f"\n  To run: {exe_path}")
    else:
        print(f"\n[Build] ERROR: {exe_path} not found after build")
        sys.exit(1)


if __name__ == "__main__":
    build()
