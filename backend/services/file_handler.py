"""
File upload handler for brochures and documents.
Saves PDFs to a local brochures/ directory and returns the relative path.
"""
import os
import secrets
from pathlib import Path
from werkzeug.utils import secure_filename

BROCHURES_DIR = "brochures"
ALLOWED_EXTENSIONS = {"pdf"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _ensure_brochures_dir():
    """Create brochures directory if it doesn't exist."""
    Path(BROCHURES_DIR).mkdir(parents=True, exist_ok=True)


def save_brochure(file_obj, prefix: str = "event") -> str | None:
    """
    Save an uploaded PDF file to the brochures directory.
    
    Returns the relative path (e.g., 'brochures/event_abc123.pdf') or None if invalid.
    """
    if not file_obj or not file_obj.filename:
        return None
    
    # Validate file extension
    filename = secure_filename(file_obj.filename)
    if not filename or not _is_allowed_file(filename):
        return None
    
    # Check file size
    file_obj.seek(0, 2)  # Seek to end
    file_size = file_obj.tell()
    file_obj.seek(0)  # Reset to start
    
    if file_size > MAX_FILE_SIZE:
        return None
    
    _ensure_brochures_dir()
    
    # Generate unique filename
    random_suffix = secrets.token_hex(8)
    new_filename = f"{prefix}_{random_suffix}.pdf"
    filepath = os.path.join(BROCHURES_DIR, new_filename)
    
    # Save file
    file_obj.save(filepath)
    return filepath


def delete_brochure(filepath: str) -> bool:
    """Delete a brochure file if it exists."""
    if not filepath or not filepath.startswith(BROCHURES_DIR):
        return False
    
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
    except Exception:
        pass
    return False


def _is_allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
