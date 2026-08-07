from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib import messages
from django.db import transaction
from django.db.models import Avg, Q, Count, Prefetch, Value, FloatField, F
from django.db.models.functions import Coalesce
from .models import Proyecto, Valoracion, Entregable, Equipo
from usuarios.models import Usuario, PerfilDesarrollador
from contrataciones.models import Contratacion
from favoritos.models import Favorito
from postulaciones.models import Postulacion
from notificaciones.models import Notificacion

@login_required
def listar_proyectos(request):
    tipo = request.GET.get('tipo')
    prioridad = request.GET.get('prioridad')
    
    proyectos_qs = Proyecto.objects.filter(estado='publicado').select_related(
        'empresa__perfil_empresa'
    ).annotate(
        num_postulaciones=Count('postulaciones'),
        empresa_reputacion=Coalesce(Avg('empresa__valoraciones_como_empresa__puntuacion',
            filter=Q(empresa__valoraciones_como_empresa__rol_evaluador='desarrollador')),
            Value(0.0), output_field=FloatField()),
    )
    
    if tipo:
        proyectos_qs = proyectos_qs.filter(tipo_solucion=tipo)
    if prioridad:
        proyectos_qs = proyectos_qs.filter(prioridad=prioridad)
    
    proyectos = []
    for p in proyectos_qs.order_by('-fecha_publicacion'):
        logo = p.empresa.perfil_empresa.logo.url if (
            hasattr(p.empresa, 'perfil_empresa') and p.empresa.perfil_empresa.logo
        ) else None
        proyectos.append({
            'id': p.id,
            'titulo': p.titulo,
            'descripcion': p.descripcion,
            'tipo_solucion': p.tipo_solucion,
            'prioridad': p.prioridad,
            'fecha_publicacion': p.fecha_publicacion,
            'empresa_nombre': p.empresa.nombre,
            'num_postulaciones': p.num_postulaciones,
            'empresa_reputacion': p.empresa_reputacion,
            'empresa_logo': logo,
        })
    
    favoritos_ids = []
    
    if request.user.rol == 'desarrollador':
        # Obtener IDs de proyectos donde el usuario ya tiene una postulación
        postulaciones_ids = Postulacion.objects.filter(desarrollador=request.user).values_list('proyecto_id', flat=True)
        
        # Excluir esos proyectos de la lista
        proyectos = [p for p in proyectos if p['id'] not in postulaciones_ids]
        
        favoritos_ids = list(Favorito.objects.filter(desarrollador=request.user).values_list('proyecto_id', flat=True))
        
    return render(request, 'proyectos/listar.html', {
        'proyectos': proyectos,
        'favoritos_ids': favoritos_ids
    })

@login_required
def crear_proyecto(request):
    if request.user.rol != 'empresa':
        messages.error(request, "Solo las empresas pueden publicar proyectos.")
        return redirect('dashboard_empresa')

    if request.method == 'POST':
        try:
            proyecto = Proyecto(
                empresa=request.user,
                titulo=request.POST.get('titulo'),
                descripcion=request.POST.get('descripcion'),
                tipo_solucion=request.POST.get('tipo_solucion'),
                prioridad=request.POST.get('prioridad', 'media'),
                vacantes=int(request.POST.get('vacantes', 1)),
                fecha_limite=request.POST.get('fecha_limite') or None,
                estado='publicado'
            )
            proyecto.save()
            proyecto.registrar_cambio_estado('publicado', request.user, estado_anterior=None)
            messages.success(request, f"¡Proyecto '{proyecto.titulo}' publicado exitosamente!")
            return redirect('dashboard_empresa')
        except Exception as e:
            messages.error(request, f"Error: {e}")
            
    return redirect('dashboard_empresa')

@login_required
def finalizar_proyecto(request, proyecto_id):
    if request.user.rol != 'empresa':
        messages.error(request, "Acceso denegado.")
        return redirect('inicio')
    
    proyecto = get_object_or_404(Proyecto, id=proyecto_id, empresa=request.user)
    
    # NUEVA VALIDACIÓN ESTRICTA: Todos los hitos deben estar COMPLETADOS
    hitos_totales = proyecto.entregables.count()
    hitos_completados = proyecto.entregables.filter(estado='completado').count()
    
    if hitos_totales == 0:
        messages.error(request, "No puedes finalizar un proyecto que no tiene hitos definidos.")
        return redirect('gestionar_hitos', proyecto_id=proyecto.id)
        
    if hitos_completados < hitos_totales:
        pendientes = hitos_totales - hitos_completados
        messages.error(request, f"No puedes finalizar el proyecto. Aún quedan {pendientes} hitos sin completar o validar.")
        return redirect('ver_avances', proyecto_id=proyecto.id)
    
    # Buscamos quiénes faltan por calificar (Empresa califica a Desarrolladores)
    desarrolladores_pendientes = Usuario.objects.filter(
        id__in=Contratacion.objects.filter(proyecto=proyecto, estado='activa').values_list('desarrollador_id', flat=True)
    ).exclude(
        id__in=Valoracion.objects.filter(proyecto=proyecto, rol_evaluador='empresa').values_list('desarrollador_id', flat=True)
    )
    
    # Si ya no hay nadie más que calificar, cerramos el proyecto oficialmente
    if not desarrolladores_pendientes.exists():
        try:
            with transaction.atomic():
                # Bloqueo pesimista: Bloqueamos la fila para asegurar que el cambio de estado sea único
                proyecto_locked = Proyecto.objects.select_for_update().get(id=proyecto.id)
                
                if proyecto_locked.estado == 'finalizado':
                    messages.info(request, f"El proyecto '{proyecto_locked.titulo}' ya ha sido finalizado anteriormente.")
                    return redirect('dashboard_empresa')

                # 1. Finalizar todas las contrataciones asociadas (Bulk Update)
                contratos = Contratacion.objects.filter(proyecto=proyecto_locked, estado='activa')
                
                # 2. Preparar notificaciones para todo el equipo (Bulk Create)
                notificaciones = [
                    Notificacion(
                        usuario=c.desarrollador,
                        proyecto=proyecto_locked,
                        tipo='aprobacion',
                        mensaje=f"El proyecto '{proyecto_locked.titulo}' ha finalizado. ¡Gracias por tu trabajo!"
                    ) for c in contratos
                ]
                
                if notificaciones:
                    Notificacion.objects.bulk_create(notificaciones)
                
                # 3. Actualizar contratos a 'finalizada'
                # IMPORTANTE: Se hace antes de guardar el proyecto para que el Trigger SQL funcione correctamente.
                contratos.update(estado='finalizada')

                # 4. Finalizar el proyecto oficialmente
                proyecto_locked.estado = 'finalizado'
                proyecto_locked.save()
                
                # Actualización de estadísticas: Eliminada lógica redundante. 
                # El conteo se calcula en tiempo real vía Vistas SQL.

            messages.success(request, f"¡Excelente! Has calificado a todo el equipo y el proyecto '{proyecto.titulo}' ha finalizado.")
            return redirect('dashboard_empresa')
        except Exception as e:
            messages.error(request, f"Error crítico al finalizar: {str(e)}")
            return redirect('finalizar_proyecto', proyecto_id=proyecto.id)

    # Tomamos el primero de la lista para evaluarlo
    desarrollador = desarrolladores_pendientes.first()
    restantes = desarrolladores_pendientes.count() - 1

    if request.method == 'POST':
        try:
            puntuacion = request.POST.get('puntuacion')
            comentario = request.POST.get('comentario', '')
            
            if not puntuacion:
                messages.error(request, "Debes seleccionar una puntuación.")
                return redirect('finalizar_proyecto', proyecto_id=proyecto.id)

            if not 1 <= int(puntuacion) <= 5:
                messages.error(request, "La puntuación debe estar entre 1 y 5.")
                return redirect('finalizar_proyecto', proyecto_id=proyecto.id)

            if Valoracion.objects.filter(proyecto=proyecto, desarrollador=desarrollador, rol_evaluador='empresa').exists():
                messages.error(request, "Ya has calificado a este desarrollador para este proyecto.")
                return redirect('finalizar_proyecto', proyecto_id=proyecto.id)

            Valoracion.objects.create(
                proyecto=proyecto,
                empresa=request.user,
                desarrollador=desarrollador,
                puntuacion=int(puntuacion),
                comentario=comentario,
                rol_evaluador='empresa'
            )

            messages.success(request, f"Calificación registrada para {desarrollador.username}. " + 
                             (f"Quedan {restantes} por calificar." if restantes > 0 else "Era el último colaborador."))
            
            # Recargamos la misma vista para el siguiente desarrollador
            return redirect('finalizar_proyecto', proyecto_id=proyecto.id)
            
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
            return redirect('finalizar_proyecto', proyecto_id=proyecto.id)
    
    return render(request, 'proyectos/finalizar.html', {
        'proyecto': proyecto,
        'desarrollador': desarrollador,
        'restantes': restantes
    })

@login_required
def calificar_empresa(request, proyecto_id):
    """Permite al desarrollador calificar a la empresa tras finalizar un proyecto"""
    if request.user.rol != 'desarrollador':
        return redirect('inicio')
    
    proyecto = get_object_or_404(Proyecto, id=proyecto_id, estado='finalizado')
    contratacion = get_object_or_404(Contratacion, proyecto=proyecto, desarrollador=request.user)
    
    if request.method == 'POST':
        try:
            puntuacion = int(request.POST.get('puntuacion'))
            comentario = request.POST.get('comentario')
            
            if not 1 <= int(puntuacion) <= 5:
                messages.error(request, "La puntuación debe estar entre 1 y 5.")
                return redirect('calificar_empresa', proyecto_id=proyecto.id)

            if Valoracion.objects.filter(proyecto=proyecto, empresa=proyecto.empresa, desarrollador=request.user, rol_evaluador='desarrollador').exists():
                messages.error(request, "Ya has calificado a esta empresa para este proyecto.")
                return redirect('dashboard_desarrollador')

            Valoracion.objects.create(
                proyecto=proyecto,
                empresa=proyecto.empresa,
                desarrollador=request.user,
                puntuacion=int(puntuacion),
                comentario=comentario,
                rol_evaluador='desarrollador'
            )
                
            messages.success(request, "¡Gracias! Tu calificación ha sido registrada.")
            return redirect('dashboard_desarrollador')
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")
            
    return render(request, 'proyectos/calificar_empresa.html', {'proyecto': proyecto})

@login_required
def gestionar_equipos(request, proyecto_id):
    if request.user.rol != 'empresa': return redirect('inicio')
    
    proyecto = get_object_or_404(Proyecto, id=proyecto_id, empresa=request.user)
    desarrolladores = Usuario.objects.filter(
        id__in=Contratacion.objects.filter(proyecto=proyecto, estado='activa').values_list('desarrollador_id', flat=True)
    )

    if desarrolladores.count() < 2:
        messages.warning(request, "Para crear grupos o parejas se requieren al menos 2 desarrolladores contratados.")
        return redirect('gestionar_hitos', proyecto_id=proyecto.id)

    if request.method == 'POST':
        nombre = request.POST.get('nombre')
        miembros_ids = request.POST.getlist('miembros')
        
        if nombre and miembros_ids:
            equipo = Equipo.objects.create(nombre=nombre, proyecto=proyecto)
            equipo.miembros.set(miembros_ids)
            
            # Notificación: Avisar a cada miembro
            for miembro_id in miembros_ids:
                Notificacion.objects.create(
                    usuario_id=miembro_id,
                    tipo='mensaje',
                    mensaje=f"Has sido asignado al equipo '{nombre}' en el proyecto: {proyecto.titulo}"
                )
            
            messages.success(request, f"Equipo '{nombre}' creado y miembros notificados.")
            return redirect('gestionar_equipos', proyecto_id=proyecto.id)

    equipos = proyecto.equipos.all().prefetch_related('miembros')
    
    return render(request, 'proyectos/gestionar_equipos.html', {
        'proyecto': proyecto,
        'desarrolladores': desarrolladores,
        'equipos': equipos
    })

@login_required
@require_POST
def eliminar_equipo(request, equipo_id):
    equipo = get_object_or_404(Equipo, id=equipo_id, proyecto__empresa=request.user)
    proyecto_id = equipo.proyecto.id
    equipo.delete()
    messages.info(request, "Equipo eliminado.")
    return redirect('gestionar_equipos', proyecto_id=proyecto_id)

@login_required
def gestionar_hitos(request, proyecto_id):
    proyecto = get_object_or_404(Proyecto, id=proyecto_id, empresa=request.user)
    hitos = Entregable.objects.filter(proyecto=proyecto).order_by('fecha_creacion')

    if request.method == 'POST':
        titulo = request.POST.get('titulo')
        descripcion = request.POST.get('descripcion')
        equipo_id = request.POST.get('equipo_id')

        if titulo:
            # Validacion de integridad: El equipo debe pertenecer al proyecto
            equipo = None
            if equipo_id:
                equipo = Equipo.objects.filter(id=equipo_id, proyecto=proyecto).first()
                if not equipo:
                    messages.error(request, "Error: El equipo seleccionado no pertenece a este proyecto.")
                    return redirect('gestionar_hitos', proyecto_id=proyecto.id)

            Entregable.objects.create(
                proyecto=proyecto, 
                titulo=titulo, 
                descripcion=descripcion,
                equipo=equipo
            )

            # Notificacion
            if equipo:
                # Avisar solo a los miembros del equipo
                for miembro in equipo.miembros.all():
                    Notificacion.objects.create(
                        usuario=miembro,
                        tipo='avance',
                        mensaje=f"Nueva tarea asignada a tu equipo '{equipo.nombre}': {titulo}"
                    )
            else:
                # Avisar a todos los contratados en el proyecto (Hito General)
                desarrolladores = Usuario.objects.filter(
                    id__in=Contratacion.objects.filter(proyecto=proyecto, estado='activa').values_list('desarrollador_id', flat=True)
                )
                for dev in desarrolladores:
                    Notificacion.objects.create(
                        usuario=dev,
                        tipo='avance',
                        mensaje=f"Nueva tarea general en '{proyecto.titulo}': {titulo}"
                    )

            messages.success(request, f"Hito '{titulo}' creado y desarrolladores notificados.")
            return redirect('gestionar_hitos', proyecto_id=proyecto.id)

    equipos = proyecto.equipos.all()

    return render(request, 'proyectos/gestionar_hitos.html', {
        'proyecto': proyecto, 
        'hitos': hitos,
        'equipos': equipos
    })

@login_required
@require_POST
def eliminar_hito(request, hito_id):
    hito = get_object_or_404(Entregable, id=hito_id, proyecto__empresa=request.user)
    proyecto_id = hito.proyecto.id
    if hito.estado == 'pendiente':
        hito.delete()
        messages.success(request, "Hito eliminado.")
    else:
        messages.error(request, "No se puede eliminar un hito que ya ha sido completado.")
    return redirect('gestionar_hitos', proyecto_id=proyecto_id)

@login_required
@require_http_methods(["GET", "POST"])
def editar_proyecto(request, proyecto_id):
    if request.user.rol != 'empresa':
        messages.error(request, "Solo las empresas pueden editar proyectos.")
        return redirect('dashboard_empresa')

    proyecto = get_object_or_404(Proyecto, id=proyecto_id, empresa=request.user)

    if proyecto.estado != 'publicado':
        messages.error(request, "Solo puedes editar un proyecto en estado publicado.")
        return redirect('dashboard_empresa')

    if Contratacion.objects.filter(proyecto=proyecto, estado='activa').exists():
        messages.error(request, "No puedes modificar un proyecto con contratos activos. Finalízalo o retíralo primero.")
        return redirect('gestionar_hitos', proyecto_id=proyecto.id)

    if request.method == 'POST':
        try:
            vacantes = int(request.POST.get('vacantes', 1))
        except (ValueError, TypeError):
            vacantes = 0

        if vacantes < 1:
            messages.error(request, "Las vacantes deben ser un número mayor o igual a 1.")
            return redirect('editar_proyecto', proyecto_id=proyecto.id)

        try:
            proyecto.titulo = request.POST.get('titulo')
            proyecto.descripcion = request.POST.get('descripcion')
            proyecto.tipo_solucion = request.POST.get('tipo_solucion')
            proyecto.prioridad = request.POST.get('prioridad', 'media')
            proyecto.vacantes = vacantes
            proyecto.fecha_limite = request.POST.get('fecha_limite') or None
            proyecto.save()

            messages.success(request, f"Proyecto '{proyecto.titulo}' actualizado exitosamente.")
            return redirect('dashboard_empresa')
        except Exception as e:
            messages.error(request, f"Error: {e}")
            return redirect('editar_proyecto', proyecto_id=proyecto.id)

    return render(request, 'proyectos/editar_proyecto.html', {'proyecto': proyecto})

@login_required
@require_POST
def desactivar_proyecto(request, proyecto_id):
    if request.user.rol != 'empresa':
        return redirect('inicio')
    
    proyecto = get_object_or_404(Proyecto, id=proyecto_id, empresa=request.user)
    
    if proyecto.estado == 'publicado' or proyecto.estado == 'en_desarrollo':
        with transaction.atomic():
            estado_anterior = proyecto.estado
            proyecto.estado = 'inactivo'
            proyecto.save()
            
            # 1. Registrar Historial
            proyecto.registrar_cambio_estado('inactivo', request.user, estado_anterior=estado_anterior)
            
            # 2. Gestionar Postulaciones y Notificaciones en bloque
            postulaciones = Postulacion.objects.filter(proyecto=proyecto).select_related('desarrollador')
            
            if postulaciones.exists():
                # Notificaciones masivas para postulantes
                notificaciones_lista = [
                    Notificacion(
                        usuario=pos.desarrollador,
                        tipo='alerta',
                        mensaje=f"El proyecto '{proyecto.titulo}' ha sido retirado por la empresa. Tu postulación ya no está vigente."
                    ) for pos in postulaciones
                ]
                
                # Actualización masiva de estados
                postulaciones.update(estado='rechazada')
                # Creación masiva de notificaciones
                Notificacion.objects.bulk_create(notificaciones_lista)

            # 3. Gestionar contrataciones en bloque
            contratos = Contratacion.objects.filter(proyecto=proyecto, estado='activa').select_related('desarrollador')
            
            if contratos.exists():
                # Notificaciones masivas para contratados
                notificaciones_contratos = [
                    Notificacion(
                        usuario=c.desarrollador,
                        tipo='alerta',
                        mensaje=f"Importante: El proyecto '{proyecto.titulo}' en el que trabajabas ha sido CANCELADO por la empresa."
                    ) for c in contratos
                ]
                
                # Actualización masiva de contratos
                contratos.update(estado='cancelada')
                # Creación masiva de notificaciones
                Notificacion.objects.bulk_create(notificaciones_contratos)

        messages.info(request, f"El proyecto '{proyecto.titulo}' ha sido retirado y los desarrolladores han sido notificados.")
    else:
        messages.error(request, "No se puede retirar un proyecto que ya está finalizado o inactivo.")
        
    return redirect('dashboard_empresa')
