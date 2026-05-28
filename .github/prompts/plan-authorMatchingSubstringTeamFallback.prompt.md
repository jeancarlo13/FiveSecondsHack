# Plan: Author Matching + Substring + Team Fallback

## TL;DR
Tres mejoras independientes al modo `individual` del bot para resolver que 4 de 5 destinatarios no reciban notificaciones:
1. **`data/teams.json`** — jerarquía de equipos como archivo de config.
2. **Substring matching** — `_candidate_matches_allowed_authors` detecta emails noreply de GitHub.
3. **Fallback al líder** — miembro sin issues propios comparte el evento con su líder.

Activación de fallback controlada por nueva env var `TEAM_FALLBACK_ENABLED`.

---

## Jerarquía real del equipo

```
jchavez@tenco.mx            ← nivel 1 (raíz)
├── ccorral@tenco.mx         ← nivel 2
│   ├── cafigueroa@tenco.mx  ← nivel 3
│   └── cfigueroa@tenco.mx   ← nivel 3
└── fgonzalez@tenco.mx       ← nivel 2
    ├── mguerrero@tenco.mx   ← nivel 3
    └── scalderon@tenco.mx   ← nivel 3
```

```json
{
  "jchavez@tenco.mx": ["ccorral@tenco.mx", "fgonzalez@tenco.mx"],
  "fgonzalez@tenco.mx": ["mguerrero@tenco.mx", "scalderon@tenco.mx"],
  "ccorral@tenco.mx": ["cafigueroa@tenco.mx", "cfigueroa@tenco.mx"]
}
```

- `cfigueroa@tenco.mx` — NO se agrega a `ALERT_RECIPIENTS` ahora; se hará en el lanzamiento a producción. Sí aparece en `data/teams.json` para uso futuro.
- `jchavez@tenco.mx` — SÍ se agrega a `ALERT_RECIPIENTS` en `.env`. Es líder raíz y debe recibir notificaciones.

---

## Fases

### Fase 1 — Config & Data (sin dependencias)

**Paso 1.** Crear `data/teams.json` con la jerarquía real.
- Formato: `{ "leader@email": ["member1@email", "member2@email"] }`
- Solo lectura en runtime, no es estado mutable (no usar `save_state`).

**Paso 2.** Agregar en `src/config.py`:
- `TEAMS_FILE = "data/teams.json"` — path al archivo de equipos
- `TEAM_FALLBACK_ENABLED = _env_bool("TEAM_FALLBACK_ENABLED", False)` — igual que `ISSUE_ONLY_FROM_INVITED`

---

### Fase 2 — Substring Matching (depende de Fase 1)

**Paso 3.** Modificar `_candidate_matches_allowed_authors` en `src/sonar.py`.

Lógica actual (3 checks):
1. Lista vacía → True (allow all)
2. Match exacto del email completo
3. `issue_local_part in allowed_authors` — el local-part del issue ES uno de los allowed

Nueva lógica (añadir 4to check al final):
4. Substring inverso: para cualquier `a` en `allowed_authors`, ¿el local-part de `a` está contenido en el local-part del issue?
   - `"ccorral" in "68394537+ccorral1-tenco"` → True ✅
   - Sin modificar los 3 checks existentes, solo agregar este al final.

Código a agregar en `_candidate_matches_allowed_authors`, después del `return issue_local_part in allowed_authors`:
```python
return any(a.split("@")[0] in issue_local_part for a in allowed_authors)
```

**Paso 4.** Agregar tests en `tests/test_sonar.py` — clase `TestFetchAndSelectSonarIssue`:
- `test_matches_github_noreply_email` — `ccorral@tenco.mx` debe matchear `68394537+ccorral1-tenco@users.noreply.github.com`
- `test_no_false_positive_different_name` — `other@tenco.mx` NO debe matchear `68394537+ccorral1-tenco@...`

---

### Fase 3 — Team Loading + Fallback (depende de Fases 1 y 2)

**Paso 5.** Agregar `_load_teams()` en `src/main.py` (función privada, nivel módulo):
- Lee `TEAMS_FILE` desde `src/config.py`
- Retorna `dict` de `{leader: [members]}` o `{}` si el archivo no existe o hay error.
- Usar `json.load`, manejar `FileNotFoundError` y `json.JSONDecodeError` con `log_error`.

**Paso 6.** Extraer helper `_make_attendee(email)` en `src/main.py`:
- Actualmente la creación del dict de attendee está inline en el loop.
- Extraer a función privada para reutilizar en el nuevo código.

**Paso 7.** Refactorizar `_run_individual_mode` en `src/main.py` con enfoque de dos pasadas:

**Primera pasada** — resolver issues por destinatario:
```
for recipient in recipients:
    allowed_authors = [recipient] if ISSUE_ONLY_FROM_INVITED else None
    issue, source_line = _fetch_issue_with_source(...)
    if found: resolved[recipient] = (issue, source_line); used_keys.append(key)
```

**Construcción de grupos** (solo si `TEAM_FALLBACK_ENABLED`):
- Mapa inverso: `member_to_leader = {m: leader for leader, members in teams.items() for m in members}`
- Agregar helper privado `_find_fallback_leader(recipient, member_to_leader, resolved, visited=None)`:
  - Sube el árbol recursivamente hasta encontrar un ancestro con issue resuelto.
  - `visited: set` previene ciclos infinitos.
  - Retorna el email del ancestro con issue o `None` si ningún ancestro lo tiene.
  ```python
  def _find_fallback_leader(recipient, member_to_leader, resolved, visited=None):
      if visited is None:
          visited = set()
      if recipient in visited:
          return None
      visited.add(recipient)
      leader = member_to_leader.get(recipient)
      if not leader:
          return None
      if leader in resolved:
          return leader
      return _find_fallback_leader(leader, member_to_leader, resolved, visited)
  ```
- Escenario cubierto: `fgonzalez` sin issues + `scalderon` sin issues → ambos suben hasta `jchavez` → un solo evento `[jchavez, fgonzalez, scalderon]`.
- Para cada `recipient` sin issue resuelto:
  - Llamar `_find_fallback_leader(recipient, ...)` → si retorna un ancestro, agregarlo como attendee del grupo de ese ancestro.
  - Si retorna `None` → print warning "No unique issue available"
- Para cada `recipient` con issue propio: crear grupo individual `{recipient: [recipient]}`

**Segunda pasada** — dispatch de eventos por grupo:
```
for issue_owner, attendees in event_groups.items():
    payload = _build_alert_payload(resolved[issue_owner])
    success = create_graph_calendar_event(payload["subject"], payload["html"], attendees_override=[_make_attendee(a) for a in attendees])
```

Comportamiento de fallback confirmado:
> "en lugar de enviar un evento individual, envias un evento para el lider y el colaborador"
→ Un solo evento con múltiples attendees [líder + miembro(s) sin issues].

---

### Fase 4 — Tests & Validación (depende de Fase 3)

**Paso 8.** Actualizar `tests/conftest.py`:
- Agregar `"TEAM_FALLBACK_ENABLED": "false"` al `os.environ.update()`
- Agregar `"TEAMS_FILE": "data/teams.json"` si es necesario para tests.

**Paso 9.** Agregar/actualizar tests en `tests/test_main.py`:
- `test_individual_fallback_groups_member_with_leader` — miembro nivel 3 sin issues → evento compartido con líder directo (nivel 2)
- `test_individual_fallback_recursive_two_levels` — **escenario clave**: `fgonzalez` (nivel 2) sin issues + `scalderon` (nivel 3, bajo fgonzalez) sin issues → ambos caen al ancestro `jchavez` (nivel 1) → un evento con attendees `[jchavez, fgonzalez, scalderon]`
- `test_individual_fallback_disabled_skips_member` — `TEAM_FALLBACK_ENABLED=false` → warning normal
- `test_individual_fallback_no_ancestor_with_issues_skips` — ningún ancestro tiene issues → warning
- `test_find_fallback_leader_cycle_protection` — ciclo en el árbol → retorna None sin loop infinito
- `test_load_teams_returns_empty_on_missing_file` — `_load_teams()` con archivo inexistente
- Actualizar `test_individual_sends_one_event_per_recipient` si el comportamiento cambia con fallback

**Paso 10.** Correr suite: `pytest --cov=src --cov-report=term-missing --cov-fail-under=95`

---

## Archivos a modificar

- `data/teams.json` — **nuevo** archivo de configuración de equipos
- `src/config.py` — agregar `TEAMS_FILE` y `TEAM_FALLBACK_ENABLED`
- `src/sonar.py` — 4to check en `_candidate_matches_allowed_authors`
- `src/main.py` — `_load_teams()`, `_make_attendee()`, refactor `_run_individual_mode`
- `tests/conftest.py` — nueva env var `TEAM_FALLBACK_ENABLED`
- `tests/test_sonar.py` — 2 tests nuevos de substring noreply
- `tests/test_main.py` — 4-5 tests nuevos de fallback + actualizar existentes

---

## Decisiones tomadas
- Substring matching: Opción A (genérico), sin parseo estricto del formato noreply
- Fallback: evento compartido [líder + miembro], NO dos eventos separados
- `TEAM_FALLBACK_ENABLED`: env var explícita, default `False`
- Config del equipo: `data/teams.json` (no en `.env`)
- `cfigueroa@tenco.mx` y `cafigueroa@tenco.mx` son personas distintas bajo `ccorral`
- `cfigueroa@tenco.mx` — NO se agrega a `ALERT_RECIPIENTS` ahora; prod launch pendiente
- `jchavez@tenco.mx` — SÍ se agrega a `ALERT_RECIPIENTS` en `.env`
