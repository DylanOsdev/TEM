from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db import transaction
from django.db.models import Avg, Q, Count, Prefetch, OuterRef, Subquery, Value, FloatField, F, Max
from django.db.models.functions import Coalesce
from django.utils import timezone
from datetime import timedelta
from ..models import Usuario
from ..decorators import requiere_usuario_activo
from ..utils import get_admin_ids
from proyectos.models import Proyecto, Valoracion
from postulaciones.models import Postulacion
from contrataciones.models import Contratacion
from notificaciones.models import Notificacion
from mensajes.models import Mensaje
from logs.models import LogAuditoria
from ._helpers import get_notificaciones_context


@login_required
@requiere_usuario_activo
def dashboard_admin(request):
    if request.user.rol != 'administrador': return redirect('inicio')
    
    user_stats = Usuario.objects.aggregate(
        total_empresas=Count('id', filter=Q(rol='empresa', is_active=True)),
        total_devs=Count('id', filter=Q(rol='desarrollador', is_active=True)),
    )
    proy_stats = Proyecto.objects.aggregate(
        p_pub=Count('id', filter=Q(estado='publicado')),
        p_des=Count('id', filter=Q(estado='en_desarrollo')),
        p_fin=Count('id', filter=Q(estado='finalizado')),
    )
    post_pen = Postulacion.objects.filter(estado='pendiente').count()
    promedio_global = Valoracion.objects.aggregate(avg=Avg('puntuacion'))['avg'] or 0
    p_ret = Proyecto.objects.filter(
        estado='en_desarrollo', fecha_limite__lt=timezone.now()
    ).count()

    total_empresas = user_stats['total_empresas']
    total_devs = user_stats['total_devs']
    p_pub = proy_stats['p_pub']
    p_des = proy_stats['p_des']
    p_fin = proy_stats['p_fin']
    total_proyectos = p_pub + p_des + p_fin
    total_usuarios = total_empresas + total_devs
    
    usuarios_lista = Usuario.objects.all().order_by('-date_joined')[:50]
    proyectos_lista = Proyecto.objects.all().select_related('empresa').prefetch_related(
        Prefetch(
            'contrataciones',
            queryset=Contratacion.objects.filter(estado='activa').select_related('desarrollador').annotate(
                hitos_completados=Count('proyecto__entregables', filter=Q(proyecto__entregables__estado='completado')),
                total_hitos=Count('proyecto__entregables')
            ),
            to_attr='contratos_activos'
        )
    ).order_by('-fecha_publicacion')[:50]
    
    logs = list(LogAuditoria.objects.select_related('usuario').order_by('-fecha_hora').values_list(
        'id', 'usuario__username', 'accion', 'tabla_afectada', 'fecha_hora'
    )[:20])

    admin_ids = get_admin_ids()
    mensajes_no_leidos_admin = Mensaje.objects.filter(receptor_id__in=admin_ids, leido=False).count()

    mensajes_soporte = Mensaje.objects.filter(Q(remitente_id__in=admin_ids) | Q(receptor_id__in=admin_ids)).values_list('remitente_id', 'receptor_id')
    u_ids = set()
    for r, s in mensajes_soporte:
        if r not in admin_ids: u_ids.add(r)
        if s not in admin_ids: u_ids.add(s)
    
    ultimo_mensaje_sq = Mensaje.objects.filter(
        (Q(remitente=OuterRef('pk'), receptor_id__in=admin_ids) | 
         Q(remitente_id__in=admin_ids, receptor=OuterRef('pk')))
    ).order_by('-fecha_envio').values('id')[:1]

    contactos = Usuario.objects.filter(id__in=u_ids).annotate(
        ultimo_mensaje_id=Subquery(ultimo_mensaje_sq)
    )
    
    conversaciones = []
    mensajes_ids = [c.ultimo_mensaje_id for c in contactos if c.ultimo_mensaje_id]
    ultimos_mensajes = {m.id: m for m in Mensaje.objects.filter(id__in=mensajes_ids).select_related('remitente')}

    for contacto in contactos:
        ultimo_m = ultimos_mensajes.get(contacto.ultimo_mensaje_id)
        if ultimo_m:
            tiene_pendientes = (ultimo_m.remitente_id == contacto.id and not ultimo_m.leido)
            conversaciones.append({
                'usuario': contacto, 
                'ultimo_mensaje': ultimo_m,
                'pendiente': tiene_pendientes
            })
    conversaciones.sort(key=lambda x: x['ultimo_mensaje'].fecha_envio, reverse=True)

    desarrolladores_qs = Usuario.objects.filter(rol='desarrollador').annotate(
        promedio=Coalesce(Avg('valoraciones_como_dev__puntuacion',
            filter=Q(valoraciones_como_dev__rol_evaluador='empresa')),
            Value(0.0), output_field=FloatField()),
        proyectos_comp=Count('contrataciones_dev',
            filter=Q(contrataciones_dev__estado='finalizada')),
    )
    ranking_top = list(desarrolladores_qs.filter(promedio__gte=4.0)
        .order_by('-promedio', '-proyectos_comp')[:5]
        .values_list('nombre', 'proyectos_comp', 'promedio', 'id'))
    ranking_bajo = list(desarrolladores_qs.filter(promedio__lt=4.0)
        .order_by('promedio', 'proyectos_comp')[:5]
        .values_list('nombre', 'proyectos_comp', 'promedio', 'id'))

    salud_global = (promedio_global, p_ret)

    siete_dias_atras = timezone.now() - timedelta(days=7)
    proyectos_alerta = Proyecto.objects.filter(
        estado='en_desarrollo'
    ).annotate(
        ultimo_avance=Max('entregables__fecha_creacion'),
    ).filter(
        Q(ultimo_avance__lt=siete_dias_atras) | Q(ultimo_avance__isnull=True)
    ).select_related('empresa').prefetch_related(
        Prefetch('contrataciones',
            queryset=Contratacion.objects.filter(estado='activa').select_related('desarrollador'),
            to_attr='contratos_activos')
    ).distinct()

    alertas_retraso = []
    for p in proyectos_alerta:
        dev_nombre = p.contratos_activos[0].desarrollador.nombre if p.contratos_activos else ''
        dias = 0
        if p.ultimo_avance:
            dias = (timezone.now() - p.ultimo_avance).days
        elif p.contratos_activos:
            dias = (timezone.now().date() - p.contratos_activos[0].fecha_inicio).days
        alertas_retraso.append((p.titulo, p.empresa.nombre, dev_nombre, dias))

    empresas_qs = Usuario.objects.filter(rol='empresa').annotate(
        promedio=Coalesce(Avg('valoraciones_como_empresa__puntuacion',
            filter=Q(valoraciones_como_empresa__rol_evaluador='desarrollador')),
            Value(0.0), output_field=FloatField()),
        total_eval=Count('valoraciones_como_empresa',
            filter=Q(valoraciones_como_empresa__rol_evaluador='desarrollador')),
        nombre_empresa=Coalesce(F('perfil_empresa__nombre_empresa'), Value('')),
    )
    ranking_empresas_top = list(empresas_qs.filter(promedio__gte=4.0)
        .order_by('-promedio', '-total_eval')[:5]
        .values_list('nombre', 'nombre_empresa', 'promedio', 'total_eval', 'id'))
    ranking_empresas_bajo = list(empresas_qs.filter(promedio__lt=4.0)
        .order_by('promedio', 'total_eval')[:5]
        .values_list('nombre', 'nombre_empresa', 'promedio', 'total_eval', 'id'))

    resenas_auditoria = []
    for v in Valoracion.objects.select_related('empresa', 'desarrollador').order_by('-fecha')[:10]:
        if v.rol_evaluador == 'empresa':
            de = v.empresa.nombre
            para = v.desarrollador.nombre
        else:
            de = v.desarrollador.nombre
            para = v.empresa.nombre
        resenas_auditoria.append((de, para, v.puntuacion, v.comentario, v.rol_evaluador))

    notificaciones_admin, notificaciones_pendientes_count = get_notificaciones_context(request.user.id)

    context = {
        'total_usuarios': total_usuarios, 'total_proyectos': total_proyectos,
        'total_devs': total_devs, 'total_empresas': total_empresas,
        'p_pub': p_pub, 'p_des': p_des, 'p_fin': p_fin,
        'post_pen': post_pen, 'promedio_global': promedio_global, 'p_ret': p_ret,
        'usuarios_todos': usuarios_lista, 'proyectos_todos': proyectos_lista,
        'logs_auditoria': logs, 'conversaciones': conversaciones, 
        'mensajes_no_leidos_admin': mensajes_no_leidos_admin,
        'ranking_talento': ranking_top,
        'ranking_bajo': ranking_bajo,
        'ranking_empresas': ranking_empresas_top,
        'ranking_empresas_bajo': ranking_empresas_bajo,
        'resenas_auditoria': resenas_auditoria,
        'salud_global': salud_global,
        'alertas_retraso': alertas_retraso,
        'notificaciones': notificaciones_admin,
        'notificaciones_pendientes_count': notificaciones_pendientes_count,
        'admin_id': request.user.id,
        'admin_ids': list(admin_ids)
    }
    return render(request, 'administrador/Administrador.html', context)


@login_required
@require_POST
def marcar_notificaciones_leidas(request):
    Notificacion.objects.filter(usuario_id=request.user.id, leida=False).update(leida=True)
    if request.user.rol == 'administrador':
        return redirect('dashboard_admin')
    elif request.user.rol == 'empresa':
        return redirect('dashboard_empresa')
    else:
        return redirect('dashboard_desarrollador')


@login_required
@require_POST
def admin_toggle_usuario(request, usuario_id):
    if request.user.rol != 'administrador': return redirect('inicio')
    user = get_object_or_404(Usuario, id=usuario_id)
    
    if user.estado == 'activo':
        user.estado = 'inactivo'
        user.is_active = False
    else:
        user.estado = 'activo'
        user.is_active = True
        
    user.save()
    LogAuditoria.objects.create(
        usuario=request.user,
        accion=f"Cambió estado del usuario {user.username} a {user.estado}",
        tabla_afectada='usuarios',
        registro_id=user.id,
    )
    messages.info(request, f"Estado del usuario {user.username} actualizado a {user.estado}.")
    return redirect(reverse('dashboard_admin') + '?section=usersSection')


@login_required
@require_POST
def admin_reactivar_proyecto(request, proyecto_id):
    if request.user.rol != 'administrador':
        messages.error(request, "Acceso denegado.")
        return redirect('inicio')
    
    with transaction.atomic():
        proyecto = Proyecto.objects.select_for_update().get(id=proyecto_id)
        
        if proyecto.estado != 'finalizado':
            messages.warning(request, "Solo se pueden reactivar proyectos finalizados.")
            return redirect('ver_avances', proyecto_id=proyecto.id)

        estado_anterior = proyecto.estado
        proyecto.estado = 'en_desarrollo'
        proyecto.save()
        proyecto.registrar_cambio_estado('en_desarrollo', request.user, estado_anterior=estado_anterior)
        
        Contratacion.objects.filter(proyecto=proyecto, estado='finalizada').update(estado='activa')
        
        notificaciones = [
            Notificacion(
                usuario=proyecto.empresa,
                tipo='avance',
                mensaje=f"SOPORTE: Tu proyecto '{proyecto.titulo}' ha sido reactivado exitosamente."
            )
        ]
        
        devs_ids = Contratacion.objects.filter(proyecto=proyecto, estado='activa').values_list('desarrollador_id', flat=True)
        for dev_id in devs_ids:
            notificaciones.append(Notificacion(
                usuario_id=dev_id,
                tipo='avance',
                mensaje=f"ALERTA: El proyecto '{proyecto.titulo}' ha sido reactivado por la empresa."
            ))
        
        Notificacion.objects.bulk_create(notificaciones)

    messages.success(request, f"Proyecto '{proyecto.titulo}' reactivado y contratos restablecidos.")
    return redirect('ver_avances', proyecto_id=proyecto.id)
