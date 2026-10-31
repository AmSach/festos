import os
import zipfile
import shutil

SOURCE_DIR = r"E:\festos"
OUTPUT_ZIP_E = r"E:\festos_submission.zip"
OUTPUT_ZIP_ARTIFACT = r"C:\Users\amans\.gemini\antigravity\brain\e8783692-ea32-4ab3-9a86-25f2f498cf5c\festos_submission.zip"

EXCLUDE_DIRS = {
    ".git",
    ".vercel",
    "__pycache__",
    "snapshots",
    ".pytest_cache",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".mp4",
    ".webm",
    ".png",  # exclude any test screenshots in root
    ".log",
}

EXCLUDE_FILES = {
    ".festos_secret",
    "desktop.ini",
}

def should_exclude(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
        if p.startswith(".git"):
            return True
    filename = os.path.basename(rel_path)
    if filename in EXCLUDE_FILES:
        return True
    name, ext = os.path.splitext(filename)
    if ext.lower() in EXCLUDE_EXTS:
        return True
    return False

def build_zip():
    print(f"Building submission ZIP from {SOURCE_DIR}...")
    file_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(OUTPUT_ZIP_E, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(SOURCE_DIR):
            # Modify dirs in-place to avoid traversing excluded dirs
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".git")]
            
            for file in sorted(files):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, SOURCE_DIR)
                
                if should_exclude(rel_path):
                    continue
                
                # We store files relative to root so judges can double click START_FESTOS.bat right away
                arcname = rel_path.replace("\\", "/")
                zf.write(full_path, arcname=arcname)
                file_count += 1
                total_uncompressed_bytes += os.path.getsize(full_path)

    zip_size_mb = os.path.getsize(OUTPUT_ZIP_E) / (1024 * 1024)
    print(f"Created {OUTPUT_ZIP_E} with {file_count} files ({zip_size_mb:.2f} MB, uncompressed: {total_uncompressed_bytes / (1024 * 1024):.2f} MB)")
    
    # Copy to artifact directory
    shutil.copy2(OUTPUT_ZIP_E, OUTPUT_ZIP_ARTIFACT)
    print(f"Copied to artifact directory: {OUTPUT_ZIP_ARTIFACT}")

if __name__ == "__main__":
    build_zip()
