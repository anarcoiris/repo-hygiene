#!/usr/bin/env python3
import os
import sys
import json
import fnmatch
import argparse
import ast
from pathlib import Path
import requests
import yaml
from datetime import datetime, timezone

HYGIENE_DIR = Path(__file__).parent.parent
CONFIG_FILE = HYGIENE_DIR / "config.yaml"

# Colores para consola
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No se encontró {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)

def is_excluded(path: Path, repo_path: Path, exclude_patterns: list) -> bool:
    try:
        rel_path = path.relative_to(repo_path)
    except ValueError:
        return True
    rel_str = str(rel_path).replace('\\', '/')
    
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(rel_str, pattern):
            return True
        # Comprobar si alguna de las partes de la ruta coincide con el patrón limpio
        clean_pat = pattern.rstrip('/*').strip('/')
        for part in rel_path.parts:
            if fnmatch.fnmatch(part, pattern) or fnmatch.fnmatch(part, clean_pat):
                return True
    return False

def get_project_tree(repo_path: Path, exclude_patterns: list) -> list[str]:
    """Genera un listado en árbol de la estructura de directorios y archivos del proyecto."""
    lines = []
    
    def _walk(directory: Path, prefix: str = ""):
        try:
            items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return
            
        items = [item for item in items if not is_excluded(item, repo_path, exclude_patterns)]
        
        for i, item in enumerate(items):
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            
            if item.is_dir():
                new_prefix = prefix + ("    " if is_last else "│   ")
                # Limitar profundidad del árbol a 4 niveles para evitar saturación de tokens
                if len(new_prefix) // 4 <= 4:
                    _walk(item, new_prefix)
                    
    _walk(repo_path)
    return lines

def extract_python_skeleton(file_path: Path) -> str:
    """Extrae el esqueleto estructural de un archivo Python (importaciones, clases, funciones y docstrings) usando AST."""
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content)
    except Exception as e:
        return f"# [Error parsing AST for {file_path.name}: {e}]\n"

    class SkeletonTransformer:
        def visit_module(self, node: ast.Module):
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            for child in node.body:
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    new_body.append(child)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    new_body.append(self.visit_function(child))
                elif isinstance(child, ast.ClassDef):
                    new_body.append(self.visit_class(child))
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    new_body.append(child)
            return ast.Module(body=new_body, type_ignores=[])

        def visit_class(self, node: ast.ClassDef) -> ast.ClassDef:
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    new_body.append(self.visit_function(child))
                elif isinstance(child, ast.ClassDef):
                    new_body.append(self.visit_class(child))
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    new_body.append(child)
            if not new_body:
                new_body.append(ast.Pass())
            return ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                decorator_list=node.decorator_list,
                body=new_body
            )

        def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.FunctionDef | ast.AsyncFunctionDef:
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            new_body.append(ast.Pass())
            cls = ast.AsyncFunctionDef if isinstance(node, ast.AsyncFunctionDef) else ast.FunctionDef
            return cls(
                name=node.name,
                args=node.args,
                decorator_list=node.decorator_list,
                returns=node.returns,
                body=new_body
            )

    transformer = SkeletonTransformer()
    new_tree = transformer.visit_module(tree)
    try:
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree)
    except Exception as e:
        return f"# [Error unparsing AST for {file_path.name}: {e}]\n"

def collect_code_files(repo_path: Path, exclude_patterns: list, max_size_kb: int, target_file_rel: str = None) -> dict[str, str]:
    """Carga el contenido o esqueleto de los archivos de código clave para enviarlos como contexto."""
    code_files = {}
    valid_extensions = {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.cs', '.yaml', '.yml', 'Dockerfile', 'Caddyfile'}
    
    for root, dirs, files in os.walk(repo_path):
        # Filtrar directorios in situ
        dirs[:] = [d for d in dirs if not is_excluded(Path(root) / d, repo_path, exclude_patterns)]
        
        for file in files:
            file_path = Path(root) / file
            if is_excluded(file_path, repo_path, exclude_patterns):
                continue
                
            # Verificar extensión o nombres especiales
            if file_path.suffix.lower() not in valid_extensions and file_path.name not in ('Dockerfile', 'Caddyfile', 'services.json'):
                continue
                
            try:
                rel_path = file_path.relative_to(repo_path)
                rel_path_str = str(rel_path).replace('\\', '/')
                
                # Si es el archivo objetivo de auditoría, se carga completo
                if target_file_rel and rel_path_str == target_file_rel.replace('\\', '/'):
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    code_files[rel_path_str] = content
                    continue
                
                # Si es un archivo Python, extraemos el esqueleto con AST
                if file_path.suffix.lower() == '.py':
                    content = extract_python_skeleton(file_path)
                    code_files[rel_path_str] = content
                else:
                    # Para otros lenguajes (JS, TS, Go, Rust, etc.), leemos las primeras 100 líneas
                    # y agregamos una nota indicando que es un fragmento.
                    # Excepción para archivos de configuración pequeños (yaml, Dockerfile, Caddyfile, services.json) que se leen enteros si son pequeños.
                    size_kb = file_path.stat().st_size / 1024
                    if size_kb > max_size_kb:
                        continue
                        
                    if file_path.suffix.lower() in ('.yaml', '.yml') or file_path.name in ('Dockerfile', 'Caddyfile', 'services.json'):
                        content = file_path.read_text(encoding='utf-8', errors='ignore')
                    else:
                        # Leer primeras 100 líneas
                        lines = []
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for _ in range(100):
                                line = f.readline()
                                if not line:
                                    break
                                lines.append(line)
                        content = "".join(lines)
                        if len(lines) == 100:
                            content += "\n# [... Contenido restante omitido para optimizar contexto ...]\n"
                    
                    code_files[rel_path_str] = content
            except Exception:
                pass
    return code_files

def filter_and_limit_files(code_files: dict[str, str], target_file_rel: str = None, max_chars: int = 80000) -> dict[str, str]:
    """Filtra y limita los archivos recolectados para no exceder la ventana de contexto, priorizando archivos estructurales clave."""
    if not code_files:
        return {}
        
    prioritized_files = []
    for filepath, content in code_files.items():
        path_obj = Path(filepath)
        parts = path_obj.parts
        
        if target_file_rel and filepath.replace('\\', '/') == target_file_rel.replace('\\', '/'):
            priority = 0
        elif path_obj.name in ('requirements.txt', 'Dockerfile', 'Caddyfile', 'services.json', 'package.json') or (path_obj.suffix in ('.yaml', '.yml') and len(parts) == 1):
            priority = 1
        else:
            first_dir = parts[0] if parts else ""
            if first_dir in ('tests', 'scripts', 'notebooks', 'heuristics', 'docs', 'tests_fixtures', 'test'):
                priority = 4
            elif first_dir in ('src', 'core', 'services', 'models', 'controllers', 'app'):
                priority = 2
            else:
                priority = 3
                
        prioritized_files.append((filepath, content, priority, len(parts), len(content)))
        
    prioritized_files.sort(key=lambda x: (x[2], x[3], x[0]))
    
    result = {}
    current_chars = 0
    
    if target_file_rel:
        target_norm = target_file_rel.replace('\\', '/')
        if target_norm in code_files:
            result[target_norm] = code_files[target_norm]
            current_chars += len(code_files[target_norm])
            
    for filepath, content, priority, depth, size in prioritized_files:
        if target_file_rel and filepath.replace('\\', '/') == target_file_rel.replace('\\', '/'):
            continue
            
        if current_chars + size > max_chars:
            continue
            
        result[filepath] = content
        current_chars += size
        
    return result

def run_ai_review(repo_path: Path, target_file_rel: str = None) -> str:
    config = load_config()
    ai_config = config.get("ai_review", {})
    
    ollama_url = ai_config.get("ollama_url", "http://localhost:11435").rstrip('/')
    model = ai_config.get("model", "qwen2.5-coder:7b-instruct")
    num_ctx = ai_config.get("num_ctx", 32768)
    max_size_kb = ai_config.get("max_file_size_kb", 100)
    exclude_patterns = ai_config.get("exclude_patterns", [])
    
    print(f"{Colors.OKCYAN}>> Generando mapa holístico de la estructura de {repo_path.name}...{Colors.ENDC}")
    tree_lines = get_project_tree(repo_path, exclude_patterns)
    tree_str = "\n".join(tree_lines)
    
    print(f"{Colors.OKCYAN}>> Recopilando archivos del código fuente...{Colors.ENDC}")
    code_files = collect_code_files(repo_path, exclude_patterns, max_size_kb, target_file_rel)
    print(f"   Se cargaron {len(code_files)} archivos de código en bruto.")
    
    max_context_chars = ai_config.get("max_context_chars", 80000)
    code_files = filter_and_limit_files(code_files, target_file_rel, max_context_chars)
    print(f"   Se seleccionaron {len(code_files)} archivos clave (~ {sum(len(c) for c in code_files.values())} caracteres) para el contexto del LLM.")
    
    # Preparar el prompt del sistema
    system_prompt = f"""
Eres un Arquitecto de Software Maestro y Cartógrafo Estructural (Hygiene Agent).
Tu objetivo NO es dar unas simples pinceladas ejecutivas, sino actuar como un "artista del diseño y la arquitectura". Necesitas mapear y entender el conjunto a cualquier escala, de manera ordenada, eficiente y holística (todas a la vez).

Para los archivos Python, se te provee de "Esqueletos Arquitectónicos" (clases, funciones, docstrings e importaciones, con los cuerpos vacíos). Si se especifica un "Foco de Auditoría", ese archivo se carga íntegramente.

REGLAS ESTRICTAS PARA PYTHON:
1. Virtual Environments: Las sugerencias de entorno virtual DEBEN usar `py -3.xx -m venv .venv` (ej. py -3.10 -m venv .venv). Evita `python -m venv`.
2. Dependencias: Diferencia los `requirements.txt` de desarrollo local de las dependencias para Docker.

REGLAS ARQUITECTÓNICAS Y DE ANÁLISIS APLICABLES:
1. **Regla de Capas y Dominios:** Las capas superiores dependen de las inferiores, NUNCA al revés. Dominios separados aislados mediante interfaces. Identifica acoplamientos estrechos.
2. **Regla de Responsabilidad Única (SRP):** Flaggea clases "dios" y funciones gigantescas.
3. **Hardcoding:** Se deben flaggear rutas locales absolutas y credenciales quemadas.

ESTRUCTURA DE TU RESPUESTA (OBLIGATORIA):
En lugar de un simple resumen ejecutivo, DEBES producir una salida profunda y profusa estructurada de la siguiente manera:

1. **Visión General Holística:**
   - Una apreciación del "todo" a la vez basada en el mapa de directorios. 
   - Descripción de la estructura base detectada.

2. **Censo y Taxonomía Completa (Mapeo Estructural Detallado):**
   - Extrae y ennumera de los archivos priorizados de forma exhaustiva:
     - Clases identificadas.
     - Enumeración de funciones y métodos.
     - Variables clave, constantes y argumentos de las firmas de funciones/clases.
   - NO resumas esta sección. Necesitamos el mapa completo del estado actual.

3. **Auditoría de Desviaciones:**
   - Lista detallada y numerada marcando TODO aquello que se esté saliendo de un esquema fijo de lógica de directorios, funciones, clases y capas/dominios (Archivos, líneas, y motivos).

4. **Sugerencias de Refactorización Estructural:**
   - Consejos concretos como un experto Arquitecto de Software para reorganizar y optimizar el diseño global y la interconexión.
"""

    # Construir el contenido del usuario
    user_message = f"=== MAPA HOLÍSTICO DEL PROYECTO ({repo_path.name}) ===\n"
    user_message += f"Estructura del directorio:\n```\n{tree_str}\n```\n\n"
    
    if target_file_rel:
        if target_file_rel not in code_files:
            print(f"{Colors.FAIL}[ERROR] El archivo objetivo no existe o fue excluido: {target_file_rel}{Colors.ENDC}", file=sys.stderr)
            sys.exit(1)
        user_message += f"=== FOCO DE AUDITORÍA: ARCHIVO ESPECÍFICO ===\n"
        user_message += f"Analiza detalladamente las dependencias y la lógica del siguiente archivo en relación con el mapa del proyecto:\n"
        user_message += f"Ruta: `{target_file_rel}`\n"
        user_message += f"Contenido:\n```python\n{code_files[target_file_rel]}\n```\n"
    else:
        user_message += f"=== ARCHIVOS CLAVE DEL PROYECTO ===\n"
        for filepath, content in code_files.items():
            user_message += f"--- Archivo: `{filepath}` ---\n{content}\n\n"
            
    user_message += "\nPor favor, realiza tu revisión arquitectónica y de higiene estructurando tu respuesta según el sistema de reglas secuenciales y comprobación holística especificado."

    # Enviar a Ollama
    print(f"\n{Colors.OKBLUE}=== Invocando Ollama ({model}, num_ctx={num_ctx}) ==={Colors.ENDC}")
    url = f"{ollama_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": num_ctx
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=2400)
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]
    except requests.exceptions.RequestException as e:
        print(f"{Colors.FAIL}[ERROR] Falló la comunicación con Ollama: {e}{Colors.ENDC}", file=sys.stderr)
        print(f"¿Está Ollama encendido en el puerto {ollama_url}?", file=sys.stderr)
        sys.exit(1)

def main():
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="AI Architecture & Hygiene Reviewer (Ollama)")
    parser.add_argument("--repo", required=True, help="Ruta del repositorio a analizar")
    parser.add_argument("--file", help="Ruta relativa de un archivo específico a auditar con prioridad")
    parser.add_argument("--output", help="Ruta del archivo markdown de salida para el reporte")
    args = parser.parse_args()
    
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"{Colors.FAIL}[ERROR] El repositorio especificado no existe o no es una carpeta: {args.repo}{Colors.ENDC}", file=sys.stderr)
        sys.exit(1)
        
    print(f"{Colors.HEADER}==================================================")
    print(f"      AI ARCHITECTURE & HYGIENE REVIEWER")
    print(f"=================================================={Colors.ENDC}")
    print(f"Repositorio: {repo_path}")
    if args.file:
        print(f"Archivo objetivo: {args.file}")
        
    start_time = datetime.now()
    review_content = run_ai_review(repo_path, args.file)
    end_time = datetime.now()
    
    # Determinar ruta de salida
    reports_dir = HYGIENE_DIR / ".reports" / "ai_review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    if args.output:
        output_file = Path(args.output).resolve()
    else:
        safe_name = repo_path.name.replace(" ", "_").replace("(", "").replace(")", "")
        output_file = reports_dir / f"{safe_name}_ai_review.md"
        
    # Escribir el informe
    report_md = f"# 🤖 AI Architecture & Hygiene Review - {repo_path.name}\n\n"
    report_md += f"**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report_md += f"**Modelo:** `{load_config().get('ai_review', {}).get('model', 'qwen2.5-coder:7b-instruct')}`\n"
    report_md += f"**Tiempo de análisis:** {((end_time - start_time).total_seconds()):.2f} segundos\n"
    if args.file:
        report_md += f"**Foco de archivo:** `{args.file}`\n"
    report_md += "\n---\n\n"
    report_md += review_content
    
    output_file.write_text(report_md, encoding='utf-8')
    print(f"\n{Colors.OKGREEN}✓ Revisión de arquitectura completada exitosamente!{Colors.ENDC}")
    print(f"Informe guardado en: {output_file.relative_to(HYGIENE_DIR)}")

if __name__ == "__main__":
    main()
