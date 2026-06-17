# 🧹 repo-hygiene

Sistema autónomo de mantenimiento de repositorios. Ejecuta análisis
periódicos con agentes especializados, genera informes estructurados
y abre issues en GitHub cuando encuentra problemas.

---

## Arquitectura

```
.repo-hygiene/
├── config.yaml              ← Configuración maestra (tareas, budget, GitHub)
├── agents/
│   ├── base.md              ← Contexto compartido por todos los agentes
│   ├── documentation.md     ← Agente: coherencia docs ↔ código
│   ├── refactor.md          ← Agente: código muerto, TODOs, estructura
│   ├── dependency.md        ← Agente: seguridad, unused deps, versiones
│   └── architecture.md      ← Agente: acoplamiento, módulos, deuda
├── templates/
│   └── issue.md             ← Plantilla para issues de GitHub
├── scripts/
│   └── orchestrator.py      ← Runner principal
└── .github/workflows/
    └── repo-hygiene.yml     ← GitHub Actions (daily/weekly/monthly)
```

Cada tarea se ejecuta en **contexto aislado**. Ningún agente hereda
estado de ejecuciones anteriores ni de otras tareas del mismo run.

---

## Quickstart

### 1. Prerrequisitos

```bash
pip install anthropic pyyaml
```

### 2. Configurar secrets en GitHub

```
ANTHROPIC_API_KEY   → tu clave de API de Anthropic
SLACK_HYGIENE_WEBHOOK → (opcional) para notificaciones
```

### 3. Primer run manual

```bash
# Solo informes, sin crear issues
python .repo-hygiene/scripts/orchestrator.py --schedule daily --dry-run

# Run real con issues
python .repo-hygiene/scripts/orchestrator.py --schedule weekly

# Ejecutar una sola tarea
python .repo-hygiene/scripts/orchestrator.py --schedule daily --task dead-code-scan
```

### 4. Activar GitHub Actions

Copia `.repo-hygiene/.github/workflows/repo-hygiene.yml` a
`.github/workflows/` de tu repositorio.

```bash
cp .repo-hygiene/.github/workflows/repo-hygiene.yml .github/workflows/
```

---

## 🌐 Escáner Centralizado (Hub Scanner)

Para usuarios que gestionan múltiples repositorios en un mismo directorio padre, `repo-hygiene` incluye un **Hub Scanner**. Esta herramienta realiza comprobaciones globales ultrarrápidas (sin usar LLMs) sobre todos los repositorios hermanos en busca de deudas técnicas transversales.

```bash
# Ejecutar todas las comprobaciones en todos los repos
python scripts/hub_scanner.py --all

# Ejecutar validaciones específicas:
python scripts/hub_scanner.py --check git       # Detecta clones duplicados del mismo remote
python scripts/hub_scanner.py --check docker    # Detecta conflictos de puertos y versioning obsoleto
python scripts/hub_scanner.py --check paths     # Busca rutas absolutas hardcodeadas
python scripts/hub_scanner.py --check venv      # Revisa higiene de python venvs y pkgs pesados
python scripts/hub_scanner.py --check caddy     # Valida integración con Caddy Hub
python scripts/hub_scanner.py --check garbage   # Detecta archivos residuales (debug.log, wavs, etc.)
python scripts/hub_scanner.py --check activity  # Sugiere archivar repos inactivos (+180 días)
```

La configuración del Hub se encuentra al final de `config.yaml` en la sección `hub:`.

---

## Schedules

| Schedule | Cuándo | Tareas |
|----------|--------|--------|
| `daily`  | 03:00 UTC todos los días | dead-code-scan, dependency-audit |
| `weekly` | 04:00 UTC cada lunes | issue-triage, documentation-health, test-coverage-gaps |
| `monthly`| 05:00 UTC el día 1 | architecture-review, security-posture, docs-full-audit |

---

## Agentes

| Agente | Especialización |
|--------|----------------|
| `documentation` | Coherencia código↔docs, ejemplos rotos, coverage de docs |
| `refactor`      | Código muerto, TODOs/FIXMEs, funciones largas, duplicados |
| `dependency`    | CVEs, deps sin usar, versiones desactualizadas, licencias |
| `architecture`  | Acoplamiento, deps circulares, God Modules, inconsistencias |

---

## Sistema de prioridades

| Prioridad | Criterio | Acción recomendada |
|-----------|----------|--------------------|
| P0 | Fallo en producción, CVE crítico | Resolver este sprint |
| P1 | Riesgo latente, deuda que crece | Planificar en 2 semanas |
| P2 | Mejora significativa de calidad | Backlog priorizado |
| P3 | Nice-to-have | Backlog libre |

---

## Informes

Los informes se guardan en `.reports/hygiene/<fecha>/<task-id>/`:

```
.reports/hygiene/
└── 2025-01-15/
    ├── dead-code-scan/
    │   ├── dead_code.json       ← datos estructurados
    │   ├── dead_code_report.md  ← informe legible
    │   └── raw.txt              ← respuesta completa del agente
    └── dependency-audit/
        └── ...
```

---

## Personalización

### Añadir una tarea nueva

```yaml
# config.yaml
tasks:
  - id: mi-tarea-custom
    schedule: weekly
    agent: refactor              # documentation | refactor | dependency | architecture
    priority: P2
    title: "Mi análisis custom"
    description: |
      Describe aquí qué debe buscar el agente.
    scope:
      include: ["src/**"]
      exclude: ["dist/**"]
    output:
      report: mi_tarea.md
      data: mi_tarea.json
    actions:
      open_issue_if: P1_or_above
```

### Crear un agente nuevo

1. Crea `.repo-hygiene/agents/mi-agente.md` siguiendo la estructura de los existentes
2. Define el schema JSON esperado
3. Define la plantilla Markdown de salida
4. Añade tareas que usen `agent: mi-agente` en `config.yaml`

### Modo solo-informes (sin GitHub)

```yaml
# config.yaml
meta:
  dry_run: true
github:
  create_issues: false
  create_prs: false
```

---

## Control de costes

```yaml
# config.yaml
budget:
  monthly_usd_limit: 50          # detiene runs si se supera
  max_files_per_task: 200        # reduce tokens por tarea
  max_file_size_kb: 100          # trunca archivos grandes
```

---

## Preguntas frecuentes

**¿Los agentes modifican código directamente?**
No. Solo generan informes. Los cambios siempre requieren revisión humana
(o un paso de `--fix` separado que tú apruebas).

**¿Qué pasa si no tengo `core.md`?**
El agente continúa sin él. Los ficheros de contexto son opcionales.

**¿Puedo usar esto sin GitHub Actions?**
Sí. El orquestador es un script Python independiente. Puedes
lanzarlo desde cron, un Makefile, o cualquier CI/CD.

**¿Soporta monorepos?**
Sí. Ajusta los `scope.include` por tarea para apuntar a los
subdirectorios relevantes de tu monorepo.
