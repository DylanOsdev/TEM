# Manual Técnico — TEIR

> Documentación técnica para desarrolladores y operadores del sistema.
> Última actualización: 30 de junio de 2026.

---

## Índice

1. [Stack tecnológico](#1-stack-tecnológico)
2. [Arquitectura](#2-arquitectura)
3. [Modelo de datos](#3-modelo-de-datos)
4. [Instalación y desarrollo local](#4-instalación-y-desarrollo-local)
5. [Docker y despliegue](#5-docker-y-despliegue)
6. [API REST administrativa](#6-api-rest-administrativa)
7. [Sistema de testing](#7-sistema-de-testing)
8. [Triggers MySQL](#8-triggers-mysql)
9. [Vistas Django](#9-vistas-django)
10. [Seguridad](#10-seguridad)
11. [Variables de entorno](#11-variables-de-entorno)
12. [Estructura del proyecto](#12-estructura-del-proyecto)
13. [Flujos principales](#13-flujos-principales)

---

## 1. Stack tecnológico

| Capa | Tecnología | Versión |
|------|-----------|---------|
| Backend | Django | 5.0+ |
| Lenguaje | Python | 3.14+ |
| Base de datos | MySQL | 8.0 |
| Driver MySQL | PyMySQL | 1.2+ |
| Servidor WSGI | Gunicorn | 22.0+ |
| Proxy reverso | Nginx | Alpine |
| Contenedores | Docker + Docker Compose | — |
| Frontend | Django Templates + Bootstrap 5 + Vanilla JS | — |
| Testing | pytest + pytest-django + pytest-cov | — |
| E2E | Playwright | — |

---

## 2. Arquitectura

### 2.1 Patrón general

TEIR sigue una arquitectura **Django MTV (Model-Template-View)** modularizada en 9 aplicaciones independientes:

```
config/          → settings, urls raíz, wsgi/asgi
usuarios/        → auth, perfiles, dashboards, API admin
proyectos/       → CRUD proyectos, hitos, equipos, valoraciones
postulaciones/   → postulación, aceptación/rechazo
contrataciones/  → contratos, cancelación
avances/         → registro y revisión de avances
mensajes/        → chat grupal, privado, inbox
notificaciones/  → sistema de alertas
favoritos/       → toggle proyectos favoritos
logs/            → auditoría y backups
```

### 2.2 Patrones de diseño

- **Transacciones atómicas** (`transaction.atomic`) en operaciones críticas: finalizar proyecto, aceptar postulación, revisar avance, cancelar contrato
- **Bloqueo pesimista** (`select_for_update`) para evitar race conditions en estados compartidos
- **Bulk operations** (`bulk_create`, `update`) para notificaciones masivas
- **Custom QuerySet** para sincronizar `is_superuser` con `rol`
- **Custom User model** heredando de `AbstractUser` con campos `first_name` y `last_name` desactivados
- **Decoradores** para control de acceso: `@login_required`, `@require_POST`, `@requiere_usuario_activo`
- **Django checks framework** para validar existencia de triggers MySQL al iniciar

### 2.3 Diagrama de despliegue

```
[Cliente] → [Nginx :80] → [Gunicorn :8000] → [Django] → [MySQL :3306]
                |                  |                 |
           /static/           /media/          triggers SQL
           (cache 30d)       (cache 7d)       (integridad)
```

---

## 3. Modelo de datos

### 3.1 Usuario (`usuarios`)

```python
Usuario(AbstractUser):
    first_name = None           # desactivado
    last_name = None            # desactivado
    nombre: CharField(150)
    identificacion: CharField(20, unique, nullable)
    fecha_nacimiento: DateField(nullable)
    email: EmailField(150, unique)
    rol: CharField(choices=[empresa, desarrollador, administrador])
    estado: CharField(choices=[activo, inactivo, suspendido])
    token_recuperacion: CharField(255, nullable)  # token criptográfico
    token_expiracion: DateTimeField(nullable)
    intentos_fallidos: PositiveSmallIntegerField(default=0)

PerfilEmpresa:
    usuario: OneToOneField(Usuario)
    nombre_empresa, sector, telefono, ciudad, descripcion
    logo: ImageField
    calificacion_promedio: DecimalField

PerfilDesarrollador:
    usuario: OneToOneField(Usuario)
    programa_formacion, ficha, habilidades
    foto_perfil: ImageField
    portafolio_url: URLField
    calificacion_promedio: DecimalField
    num_proyectos_completados: IntegerField
```

### 3.2 Proyecto (`proyectos`)

```python
Proyecto:
    empresa: FK(Usuario)
    titulo, descripcion, tipo_solucion(choices), prioridad(choices)
    vacantes: PositiveIntegerField
    estado: CharField(choices=[
        publicado, en_desarrollo,
        en_revision, finalizado, rechazado, inactivo
    ])
    fecha_publicacion: auto_now_add
    fecha_limite: DateField(nullable)

Entregable (Hito):
    proyecto: FK(Proyecto)
    equipo: FK(Equipo, nullable)
    titulo, descripcion
    estado: CharField(choices=[pendiente, en_revision, completado])

Equipo:
    nombre, proyecto: FK(Proyecto)
    miembros: M2M(Usuario)

Valoracion:
    proyecto, empresa, desarrollador: FK
    rol_evaluador: CharField(choices=[empresa, desarrollador])
    puntuacion: PositiveSmallIntegerField(1-5)
    comentario: TextField(nullable)

HistorialEstadoProyecto:
    proyecto: FK(Proyecto)
    estado_anterior, estado_nuevo: CharField
    cambiado_por: FK(Usuario, SET_NULL)
    fecha: auto_now_add
```

### 3.3 Postulación y Contratación

```python
Postulacion:
    proyecto: FK(Proyecto), desarrollador: FK(Usuario)
    mensaje: TextField(nullable)
    estado: CharField(choices=[pendiente, aceptada, rechazada])
    unique_together: (proyecto, desarrollador)

Contratacion:
    proyecto: FK(Proyecto), desarrollador: FK(Usuario), empresa: FK(Usuario)
    fecha_inicio: DateField(auto_now_add)
    estado: CharField(choices=[activa, finalizada, cancelada])
    unique_together: (proyecto, desarrollador)
```

### 3.4 Avance

```python
Avance:
    proyecto: FK(Proyecto), desarrollador: FK(Usuario)
    entregable: FK(Entregable)
    descripcion: TextField
    archivo_url: CharField(500)  # obligatorio
    estado: CharField(choices=[pendiente, aceptado, rechazado])
    comentario_revision: TextField(nullable)
```

### 3.5 Mensajería y Notificaciones

```python
Mensaje:
    remitente, receptor: FK(Usuario) (receptor nullable = grupal)
    proyecto: FK(Proyecto, SET_NULL)
    titulo, contenido, leido: BooleanField

Notificacion:
    usuario, proyecto: FK
    tipo: CharField(choices)
    mensaje: TextField, leida: BooleanField
```

### 3.6 Auditoría

```python
LogAuditoria:
    usuario: FK(Usuario, SET_NULL)
    accion: CharField(300)
    tabla_afectada: CharField(nullable)
    registro_id: BigIntegerField(nullable)

CopiaSeguridad:
    ejecutado_por: FK(Usuario, SET_NULL)
    archivo_url, tamano_mb, estado(choices)
```

---

## 4. Instalación y desarrollo local

### 4.1 Requisitos

- Python 3.14+
- MySQL 8.0+
- pip

### 4.2 Setup

```bash
git clone https://github.com/MrDylanDev/TEIR.git
cd TEIR
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configurar base de datos
cp docker/.env.docker.example .env
# Editar .env con credenciales reales de MySQL:
#   DB_NAME=tem_dbv2
#   DB_USER=root
#   DB_PASSWORD=tu_password
#   DB_HOST=127.0.0.1
#   DJANGO_SECRET_KEY=generar_una_key_segura
#   DJANGO_DEBUG=True

# Inicializar BD
python manage.py migrate
python manage.py runserver
```

### 4.3 Crear superusuario

```bash
python manage.py createsuperuser
# Rol: administrador
```

### 4.4 Cargar schema MySQL (triggers)

El archivo `database/init.sql` contiene el DDL completo con triggers. Para desarrollo local, ejecutar solo los triggers:

```bash
mysql -u root -p tem_dbv2 < database/init.sql
```

El sistema verificará la existencia de triggers al iniciar mediante `usuarios/checks.py`.

---

## 5. Docker y despliegue
### 5.1 Requisitos
- Docker


### 5.2 Servicios

```yaml
services:
  db:      # MySQL 8.0, puerto 3307:3306
  web:     # Django + Gunicorn, puerto 8000 (interno)
  nginx:   # Nginx Alpine, puerto 80
```

### 5.3 Inicio rápido

```bash
cp docker/.env.docker.example .env
# Editar .env con secretos reales
docker compose up -d
```

La aplicación en `http://localhost`.

### 5.4 Entrypoint

`docker/entrypoint.sh` ejecuta en orden:
1. Espera conexión MySQL (30 reintentos, 2s cada uno)
2. `python manage.py migrate --noinput`
3. `python manage.py collectstatic --noinput`
4. `gunicorn config.wsgi:application` con 3 workers, 2 threads c/u

### 5.5 Gunicorn

```bash
gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --worker-class gthread \
    --threads 2 \
    --timeout 120 \
    --max-requests 1000 \
    --max-requests-jitter 50
```

### 5.6 Nginx

- Sirve `/static/` con cache 30 días
- Sirve `/media/` con cache 7 días
- Proxy reverso a Gunicorn en `web:8000`
- Health check en `/health/`
- Headers de seguridad: `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`
- Gzip compresión nivel 6
- Bloqueo de archivos ocultos (`.env`, `.git`)

### 5.7 Health checks

```bash
# MySQL
docker exec teir_db mysqladmin ping -h localhost

# Web
docker exec teir_web curl -f http://localhost:8000/

# Nginx
curl -f http://localhost/health/
```

---

## 6. API REST administrativa

### 6.1 Autenticación

Todas las rutas requieren:
- Sesión activa con rol `administrador`
- Token CSRF en header `X-CSRFToken`

### 6.2 Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/` | Listar todos los usuarios |
| GET | `/api/<id>/` | Obtener usuario por ID |
| POST | `/api/crear/` | Crear usuario |
| PUT | `/api/<id>/actualizar/` | Actualizar usuario |
| DELETE | `/api/<id>/eliminar/` | Eliminar usuario |
| POST | `/api/bulk-toggle/` | Activar/suspender en lote |

### 6.3 Crear usuario

```json
POST /api/crear/
{
  "username": "nuevo_dev",
  "email": "dev@teir.edu.co",
  "password": "SecurePass123!",
  "rol": "desarrollador"
}
```

Respuesta `201`:
```json
{"id": 5, "status": "created"}
```

### 6.4 Bulk toggle

```json
POST /api/bulk-toggle/
{
  "ids": [1, 2, 3],
  "action": "suspend"  // o "activate"
}
```

Validaciones:
- No se puede suspender al admin que ejecuta la acción
- `activate`: `estado='activo'`, `is_active=True`
- `suspend`: `estado='suspendido'`, `is_active=False`

### 6.5 Códigos de error

| Código | Significado |
|--------|------------|
| 200 | Éxito |
| 201 | Creado |
| 400 | Datos inválidos |
| 403 | No es administrador |
| 404 | No encontrado |
| 405 | Método no permitido |
| 409 | Conflicto (username/email duplicado) |
| 500 | Error interno |

---

## 7. Sistema de testing

### 7.1 Configuración

```ini
# pytest.ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings_test
python_files = tests.py test_*.py *_tests.py
addopts = -v --tb=short --strict-markers -p no:warnings
markers =
    slow: tests lentos
    e2e: tests end-to-end con Playwright
```

### 7.2 Settings de test

`config/settings_test.py`:
- SQLite en memoria (`:memory:`)
- Validadores de password desactivados
- MD5 hasher (rápido)
- Email backend: `locmem`
- Monkey-patch para rename de columna `cedula` → `identificacion`

### 7.3 Fixtures globales (`conftest.py`)

```python
client()              # Django test client sin auth
admin_user(db)        # Usuario admin activo
empresa_user(db)      # Usuario empresa activo
desarrollador_user(db) # Usuario desarrollador activo
suspended_user(db)    # Usuario suspendido
auth_client()         # Client autenticado como empresa
admin_client()        # Client autenticado como admin
dev_client()          # Client autenticado como desarrollador
```

### 7.4 Ejecutar tests

```bash
# Unitarios e integración (sin E2E)
pytest . --ignore=tests/e2e -v

# Solo E2E (requiere Playwright y navegador)
pytest tests/e2e/ -v -m e2e

# Todos
pytest -v
```

### 7.5 Cobertura

```
apps con tests:
  usuarios/       → test_admin.py, test_auth.py, test_dashboard.py, test_registro.py
  proyectos/      → test_models.py, test_views.py
  postulaciones/  → test_postulaciones.py
  contrataciones/ → test_contrataciones.py
  avances/        → test_avances.py
  mensajes/       → test_mensajes.py
  favoritos/      → test_favoritos.py
  logs/           → test_logs.py
  notificaciones/ → test_notificaciones.py
  tests/e2e/      → test_full_workflow.py, test_auth_flows.py, test_avance_workflow.py

Total: 117 tests recolectados
```

---

## 8. Triggers MySQL

### 8.1 Lista de triggers requeridos

Verificados al iniciar Django por `usuarios/checks.py`:

| Trigger | Función |
|---------|---------|
| `trg_validar_vacantes_antes_de_contratar` | Evita exceder vacantes al contratar |
| `trg_notificacion_mensaje` | Crea notificación al enviar mensaje |
| `trg_nueva_postulacion` | Notifica a la empresa cuando alguien se postula |
| `trg_actualizar_proyectos_completados` | Actualiza contador de proyectos del dev |
| `trg_log_nuevo_usuario` | Registra creación de usuarios en LogAuditoria |
| `trg_registro_sesion` | Registra inicio de sesión |
| `trg_log_usuario_modificado` | Registra modificación de usuarios |

### 8.2 Verificación

```bash
mysql -u root -p -e "SHOW TRIGGERS" tem_dbv2
```

Si falta algún trigger, Django emite un Warning al iniciar. Ejecutar `database/init.sql` para restaurarlos.

---

## 9. Vistas Django

### 9.1 Estructura de vistas

El paquete `usuarios/views/` está modularizado:

```
usuarios/views/
├── __init__.py              # re-exporta todas las vistas
├── _helpers.py              # get_notificaciones_context()
├── auth_views.py            # login, logout, recuperar, restablecer
├── registration_views.py    # registro
├── dashboard_views.py       # inicio (landing page)
├── profile_views.py         # editar_perfil
├── empresa_dashboard.py     # dashboard_empresa
├── dev_dashboard.py         # dashboard_desarrollador
├── admin_dashboard.py       # dashboard_admin + admin_toggle + admin_reactivar
└── admin_api.py             # api_usuarios + CRUD + bulk_toggle
```

### 9.2 Decoradores de acceso

```python
@login_required              # requiere sesión activa
@require_POST                # solo acepta POST
@requiere_usuario_activo     # bloquea usuarios suspendidos/inactivos
```

### 9.3 URLs principales

```
/                           → landing page (inicio)
/login/                     → login
/logout/                    → logout
/registro/                  → registro
/recuperar/                 → recuperar contraseña
/restablecer/<token>/       → restablecer contraseña

/empresa/dashboard/         → dashboard empresa
/desarrollador/dashboard/   → dashboard desarrollador
/admin/dashboard/           → dashboard administrador
/perfil/editar/             → editar perfil

/proyectos/listar/          → listar proyectos
/proyectos/crear/           → publicar proyecto
/proyectos/<id>/finalizar/  → finalizar proyecto
/proyectos/<id>/calificar/  → calificar empresa (dev)
/proyectos/<id>/hitos/      → gestionar hitos
/proyectos/<id>/equipos/    → gestionar equipos

/postulaciones/<id>/        → ver postulaciones (empresa)
/postulaciones/postularse/<id>/ → postularse (dev)
/postulaciones/aceptar/<id>/    → aceptar postulación

/contrataciones/            → lista contrataciones (empresa)
/contrataciones/<id>/cancelar/  → cancelar contrato

/avances/<proyecto_id>/     → registrar avance (dev)
/avances/revisar/<id>/      → revisar avance (empresa)
/avances/ver/<proyecto_id>/ → ver avances

/mensajes/sala/<proyecto_id>/     → chat grupal
/mensajes/chat/<receptor_id>/<proyecto_id>/ → chat privado
/mensajes/inbox/                  → bandeja de entrada
/mensajes/sent/                   → enviados
/mensajes/redactar/               → redactar mensaje

/notificaciones/                  → lista notificaciones
/notificaciones/marcar-leidas/    → marcar leídas

/favoritos/toggle/<proyecto_id>/  → toggle favorito
/favoritos/                       → listar favoritos

/logs/reporte/                    → reporte de logs

/api/                             → API REST (admin only)
/api/crear/                       → crear usuario
/api/bulk-toggle/                 → acciones en lote
```

---

## 10. Seguridad

### 10.1 Contraseñas

- Validación custom: 8+ caracteres, mayúscula, minúscula, número
- Hashing: PBKDF2 (principal), Argon2 y BCrypt (fallback)
- Recuperación: token `secrets.token_urlsafe(32)`, expira en 1 hora
- Sin fuga de información: mismo mensaje para email existente y no existente

### 10.2 Sesiones

- `SESSION_COOKIE_AGE = 3600` (1 hora)
- `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`
- `SESSION_SAVE_EVERY_REQUEST = True`
- Bloqueo por fuerza bruta: 5 intentos fallidos → cuenta suspendida

### 10.3 CSRF

- `CSRF_TRUSTED_ORIGINS` configurado para localhost
- Token CSRF requerido en API

### 10.4 Docker

- Usuario no-root (`appuser`)
- `.env` en `.gitignore`
- Health checks en los 3 servicios
- Sin puertos expuestos excepto 80 (Nginx) y 3307 (MySQL dev)

### 10.5 Consideraciones

- `SECRET_KEY` obligatoria en producción (levanta `ValueError` si falta)
- Sin rate limiting en API admin (issue SCRUM-141)
- `ALLOWED_HOSTS` sin `.strip()` — usar sin espacios (issue SCRUM-137)
- Contraseñas con defaults en `docker-compose.yml` (issue SCRUM-147)

---

## 11. Variables de entorno

| Variable | Descripción | Default |
|----------|------------|---------|
| `DJANGO_SECRET_KEY` | Clave secreta Django | Obligatorio en prod |
| `DJANGO_DEBUG` | Modo debug | `False` |
| `DJANGO_ALLOWED_HOSTS` | Hosts permitidos (separados por coma) | `localhost,127.0.0.1` |
| `DB_NAME` | Nombre BD | `tem_dbv2` |
| `DB_USER` | Usuario MySQL | `root` |
| `DB_PASSWORD` | Contraseña MySQL | — |
| `DB_HOST` | Host MySQL | `127.0.0.1` |
| `DB_PORT` | Puerto MySQL | `3306` |
| `EMAIL_BACKEND` | Backend de email | `console.EmailBackend` |
| `EMAIL_HOST` | Servidor SMTP | `smtp.gmail.com` |
| `EMAIL_PORT` | Puerto SMTP | `587` |
| `EMAIL_USE_TLS` | Usar TLS | `True` |
| `EMAIL_HOST_USER` | Usuario SMTP | — |
| `EMAIL_HOST_PASSWORD` | Contraseña SMTP | — |
| `DEFAULT_FROM_EMAIL` | Remitente default | `TEIR <noreply@teir.edu.co>` |
| `MYSQL_ROOT_PASSWORD` | Root MySQL (Docker) | — |

---

## 12. Estructura del proyecto

```
TEIR/
├── avances/              # App: registro y revisión de avances
│   ├── models.py         #   Avance
│   ├── views.py          #   registrar_avance, revisar_avance, ver_avances
│   ├── urls.py
│   └── tests/
├── config/               # Configuración Django
│   ├── settings.py       #   Producción (MySQL)
│   ├── settings_test.py  #   Testing (SQLite en memoria)
│   ├── urls.py           #   URLconf raíz
│   ├── wsgi.py
│   └── asgi.py
├── contrataciones/       # App: gestión de contratos
│   ├── models.py         #   Contratacion
│   └── views.py          #   listar, detalle, cancelar
├── database/
│   └── init.sql          # Schema DDL + triggers MySQL
├── docker/
│   ├── entrypoint.sh     # Script de inicio del contenedor
│   ├── .env.docker.example
│   └── nginx/nginx.conf  # Configuración Nginx
├── docs/
│   └── API.md            # Documentación de API REST
├── favoritos/            # App: proyectos guardados
│   ├── models.py         #   Favorito
│   └── views.py          #   toggle_favorito, listar_favoritos
├── logs/                 # App: auditoría
│   ├── models.py         #   LogAuditoria, CopiaSeguridad
│   └── views.py
├── mensajes/             # App: chat y mensajería
│   ├── models.py         #   Mensaje
│   └── views.py          #   sala_chat, sala_chat_grupal, inbox, sent, redactar
├── notificaciones/       # App: sistema de alertas
│   ├── models.py         #   Notificacion
│   └── views.py
├── postulaciones/        # App: postulaciones
│   ├── models.py         #   Postulacion
│   └── views.py          #   ver_postulaciones, postularse, aceptar
├── proyectos/            # App: core del negocio
│   ├── models.py         #   Proyecto, Entregable, Equipo, Valoracion, HistorialEstado
│   └── views.py          #   listar, crear, finalizar, calificar, hitos, equipos
├── static/               # CSS, JS, imágenes
│   ├── publico/          #   Landing page
│   ├── dashboard/        #   Dashboards
│   ├── Desarrollador/    #   Perfil desarrollador
│   ├── empresa/          #   Panel empresa
│   └── administrador/    #   Panel admin
├── templates/            # Django templates
├── tests/
│   └── e2e/              # Tests end-to-end con Playwright
├── usuarios/             # App: auth y perfiles
│   ├── models.py         #   Usuario, PerfilEmpresa, PerfilDesarrollador
│   ├── forms.py          #   RegistroUsuarioForm, PerfilEmpresaForm, PerfilDesarrolladorForm
│   ├── validators.py     #   ComplexPasswordValidator
│   ├── decorators.py     #   requiere_usuario_activo
│   ├── checks.py         #   Verificación de triggers MySQL
│   ├── utils.py          #   get_admin_id, get_admin_ids (con caché)
│   └── views/            #   Paquete modular de vistas
├── conftest.py           # Fixtures globales de pytest
├── pytest.ini            # Configuración de pytest
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 13. Flujos principales

### 13.1 Ciclo de vida del proyecto

```
publicado → (primera contratación) → en_desarrollo
                                     ↓
                          (todos los hitos enviados)
                                     ↓
                                en_revision
                                     ↓
                          (empresa acepta todos, califica)
                                     ↓
                                finalizado
```

**Estados intermedios:**
- `rechazado`: proyecto rechazado por admin (no usado actualmente)
- `inactivo`: proyecto desactivado por la empresa

**Cambios de estado por eventos:**
- Aceptar postulación: `publicado` → `en_desarrollo`
- Rechazar un avance: `en_revision` → `en_desarrollo`
- Último hito enviado: `en_desarrollo` → `en_revision`
- Finalizar proyecto: `en_revision` → `finalizado`
- Desactivar proyecto: `publicado` o `en_desarrollo` → `inactivo`

### 13.2 Flujo de postulación

```
1. Empresa publica proyecto (estado: publicado)
2. Desarrollador se postula (estado postulación: pendiente)
3. Empresa acepta postulación
   ├── Crea Contratacion (estado: activa)
   ├── Cambia estado proyecto → en_desarrollo
   ├── Agrega dev al equipo del proyecto
   └── Registra en LogAuditoria
4. Desarrollador trabaja en hitos
5. Empresa revisa avances
6. Empresa finaliza y califica
7. Desarrollador califica a la empresa
```

### 13.3 Flujo de hitos y avances

```
1. Empresa crea Entregable (estado: pendiente)
   ├── Asignado a equipo → notifica miembros del equipo
   └── Sin equipo → notifica todos los contratados
2. Desarrollador registra Avance
   ├── archivo_url obligatorio
   ├── Cambia estado hito → en_revision
   └── Notifica a la empresa
3. Empresa revisa avance:
   ├── Aceptar:
   │   ├── Cambia estado hito → completado
   │   └── Notifica al desarrollador
   └── Rechazar:
       ├── Requiere comentario obligatorio
       ├── Cambia estado hito → pendiente
       ├── Cambia estado proyecto → en_desarrollo
       └── Notifica al desarrollador
4. Si es el último hito → proyecto → en_revision
```

### 13.4 Transacciones atómicas

Las siguientes operaciones usan `transaction.atomic()` + `select_for_update()`:

| Operación | Archivo | Bloqueo sobre |
|-----------|---------|---------------|
| Finalizar proyecto | `proyectos/views.py` | `Proyecto` |
| Aceptar postulación | `postulaciones/views.py` | `Postulacion` |
| Cancelar contrato | `contrataciones/views.py` | `Contratacion` |
| Registrar avance | `avances/views.py` | `Proyecto` |
| Revisar avance | `avances/views.py` | (implícito por `Avance`) |
| Desactivar proyecto | `proyectos/views.py` | (implícito por `Proyecto`) |

### 13.5 Sincronización de caché admin

Cuando se crea, modifica o elimina un administrador:
```python
cache.delete('admin_id')
cache.delete('admin_ids_list')
```

---

## 14. Base de datos

### 14.1 Motor y configuración

| Parámetro | Desarrollo | Testing | Producción (Docker) |
|-----------|-----------|---------|---------------------|
| Motor | MySQL 8.0 | SQLite :memory: | MySQL 8.0 |
| Driver | PyMySQL | sqlite3 | PyMySQL |
| Host | 127.0.0.1 | — | db (nombre del servicio) |
| Puerto | 3306 | — | 3306 |
| charset | utf8mb4 | — | utf8mb4 |

### 14.2 Índices definidos

| Tabla | Índice | Columnas |
|-------|--------|----------|
| proyectos | idx_estado | estado |
| proyectos | idx_proy_tipo | tipo_solucion |
| proyectos | idx_proy_prio | prioridad |
| proyectos | idx_fecha_publicacion | fecha_publicacion |
| contrataciones | idx_proy_dev_est | proyecto, desarrollador, estado |
| contrataciones | idx_empresa_contratos | empresa |
| entregables | idx_entregables_estado | estado |
| entregables | idx_entregables_fecha | fecha_creacion |

### 14.3 Constraints UNIQUE

| Tabla | Columnas |
|-------|----------|
| postulaciones | (proyecto_id, desarrollador_id) |
| contrataciones | (proyecto_id, desarrollador_id) |
| favoritos | (desarrollador_id, proyecto_id) |
| valoraciones | (proyecto_id, desarrollador_id, rol_evaluador) |

### 14.4 Migraciones

```bash
# Generar migraciones después de cambiar modelos
python manage.py makemigrations

# Aplicar migraciones pendientes
python manage.py migrate

# Ver estado de migraciones
python manage.py showmigrations

# Revertir última migración (development only)
python manage.py migrate <app_name> <numero_anterior>
```

**Nota:** El proyecto usa `settings_test.py` con SQLite en memoria para tests. La migración 0005 de usuarios usa `SeparateDatabaseAndState` porque la columna `cedula` se renombró manualmente en MySQL. El `conftest.py` aplica un monkey-patch para que SQLite maneje este caso correctamente.

### 14.5 Relación modelos Django ↔ tablas MySQL

| Modelo Django | Tabla MySQL | Tipo |
|---------------|-------------|------|
| Usuario | usuarios | Custom User (AbstractUser) |
| PerfilEmpresa | perfil_empresa | OneToOneField |
| PerfilDesarrollador | perfil_desarrollador | OneToOneField |
| Proyecto | proyectos | Model |
| Entregable | entregables | Model |
| Equipo | equipos | Model |
| Equipo.miembros (M2M) | equipos_miembros | Through table |
| Postulacion | postulaciones | Model |
| Contratacion | contrataciones | Model |
| Valoracion | valoraciones | Model |
| Avance | avances | Model |
| Mensaje | mensajes | Model |
| Notificacion | notificaciones | Model |
| Favorito | favoritos | Model |
| LogAuditoria | logs_auditoria | Model |
| CopiaSeguridad | copias_seguridad | Model |
| HistorialEstadoProyecto | historial_estado_proyecto | Model |

---

## 15. Troubleshooting técnico

**Error: `No module named 'pymysql'`**
```bash
pip install pymysql
```

**Error: `DJANGO_SECRET_KEY es obligatorio en producción`**
El archivo `.env` no existe o no tiene `DJANGO_SECRET_KEY`. Crearlo desde el template:
```bash
cp docker/.env.docker.example .env
```

**Error: MySQL no conecta desde Django**
1. Verificar que MySQL está corriendo: `systemctl status mysql`
2. Verificar credenciales en `.env` (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
3. Verificar que la BD existe: `mysql -u root -p -e "SHOW DATABASES"`
4. Si usás Docker: `docker exec teir_db mysqladmin ping`

**Warning: "Falta el trigger requerido: 'trg_...'"**
Los triggers no se crearon en la BD. Ejecutar:
```bash
mysql -u root -p tem_dbv2 < database/init.sql
```
Verificar: `mysql -u root -p -e "SHOW TRIGGERS" tem_dbv2`

**Error: `Table 'tem_dbv2.X' doesn't exist`**
Las migraciones no se aplicaron. Ejecutar:
```bash
python manage.py migrate
```

**Error: `port 3306 already in use`**
MySQL local está usando el puerto. Para Docker, el compose mapea `3307:3306`. Conectate por el 3307 o detené MySQL local.

**Error: CSRF token missing en API**
Obtener el token de la cookie de sesión:
```bash
curl -c cookies.txt -d "username=admin&password=...&rol_seleccionado=administrador" http://localhost/login/
CSRF=$(grep csrftoken cookies.txt | awk '{print $7}')
```

**Error en tests: `ValueError: DJANGO_SECRET_KEY...`**
Pasar la variable de entorno:
```bash
DJANGO_SECRET_KEY=test-key DJANGO_DEBUG=True pytest -v
```

**Gunicorn workers se reinician constantemente**
Ajustar `--max-requests` y `--max-requests-jitter` en `entrypoint.sh`. Por defecto: 1000 requests por worker con jitter de 50.

---

## 16. Guía de contribución

### 16.1 Agregar una nueva app

```bash
python manage.py startapp nueva_app
```

Luego:
1. Agregar `'nueva_app'` a `INSTALLED_APPS` en `config/settings.py`
2. Crear `models.py` con los modelos
3. Crear `views.py` con las vistas
4. Crear `urls.py` con las rutas
5. Incluir las rutas en `config/urls.py`
6. Crear tests en `nueva_app/tests/`
7. Generar migraciones: `python manage.py makemigrations`

### 16.2 Convenciones de código

- **Idioma:** Código, comentarios y mensajes de commit en español
- **Modelos:** `db_table` explícito en Meta, `related_name` en FKs
- **Vistas:** `@login_required` en toda vista que requiera sesión, `@require_POST` en mutaciones
- **Transacciones:** Usar `transaction.atomic()` en operaciones que modifican múltiples tablas
- **Queries:** Preferir ORM sobre SQL crudo. Usar `select_related()` y `prefetch_related()` para evitar N+1
- **Tests:** Un archivo `test_*.py` por app. Usar fixtures de `conftest.py`

### 16.3 Flujo de trabajo con Git

```bash
git checkout -b feat/nombre-feature
# ... desarrollar ...
git add .
git commit -m "feat(app): descripción breve"
pytest . --ignore=tests/e2e -v  # verificar que todo pase
git push origin feat/nombre-feature
# Crear PR en GitHub
```

### 16.4 Agregar nuevas variables de entorno

1. Agregar la variable en `config/settings.py` con `os.environ.get('NOMBRE', 'default')`
2. Documentarla en `docker/.env.docker.example`
3. Agregarla a la tabla en la sección 11 de este manual
