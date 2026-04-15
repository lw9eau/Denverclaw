import os
import shutil
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger("denver.tools.files")

BASE_DIR = os.getenv("BOT_STORAGE_PATH", "./denver_storage")

# Asegurar que el directorio base exista al iniciar el módulo
os.makedirs(BASE_DIR, exist_ok=True)

def _get_safe_path(filename: str) -> str:
    """
    Obtiene una ruta segura dentro de BASE_DIR previniendo path traversal.
    """
    safe_name = os.path.basename(filename)
    return os.path.join(BASE_DIR, safe_name)

@tool
def write_file(filename: str, content: str) -> str:
    """
    Guarda contenido de texto en un archivo. 
    Añade automáticamente '.txt' si el nombre no tiene extensión.
    """
    try:
        if "." not in filename:
            filename += ".txt"
        
        filepath = _get_safe_path(filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        
        return f"Archivo '{filename}' guardado exitosamente en el sistema de archivos."
    except Exception as e:
        logger.error(f"Error escribiendo archivo {filename}: {e}")
        return f"Error al escribir el archivo: {str(e)}"

@tool
def read_file(filename: str) -> str:
    """
    Lee y retorna el contenido de un archivo del sistema de archivos.
    """
    try:
        filepath = _get_safe_path(filename)
        if not os.path.exists(filepath):
            return f"Error: El archivo '{filename}' no existe."
        
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        return content
    except Exception as e:
        logger.error(f"Error leyendo archivo {filename}: {e}")
        return f"Error al leer el archivo: {str(e)}"

@tool
def delete_file(filename: str) -> str:
    """
    Elimina un archivo del sistema de archivos previa validación de existencia.
    """
    try:
        filepath = _get_safe_path(filename)
        if not os.path.exists(filepath):
            return f"Error: No se pudo borrar '{filename}' porque no existe."
        
        os.remove(filepath)
        return f"Archivo '{filename}' eliminado exitosamente."
    except Exception as e:
        logger.error(f"Error eliminando archivo {filename}: {e}")
        return f"Error al eliminar el archivo: {str(e)}"

@tool
def list_files(pattern: Optional[str] = None) -> str:
    """
    Lista los archivos disponibles en el sistema de archivos.
    Si se provee un pattern, filtra los nombres (búsqueda case-insensitive).
    """
    try:
        files = os.listdir(BASE_DIR)
        
        if pattern:
            pattern_lower = pattern.lower()
            files = [f for f in files if pattern_lower in f.lower()]
        
        if not files:
            return "No se encontraron archivos." if not pattern else f"No hay archivos que coincidan con '{pattern}'."
            
        return "Archivos encontrados:\n" + "\n".join([f"- {f}" for f in files])
    except Exception as e:
        logger.error(f"Error listando archivos: {e}")
        return f"Error al listar archivos: {str(e)}"
