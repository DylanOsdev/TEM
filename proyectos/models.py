from django.db import models
from usuarios.models import Usuario
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

class Proyecto(models.Model):
    TIPO_SOLUCION = [
        ('sitio_web', 'Sitio Web'),
        ('aplicacion_movil', 'Aplicación Móvil'),
        ('automatizacion', 'Automatización'),
        ('sistema_escritorio', 'Sistema de Escritorio'),
        ('otro', 'Otro'),
    ]
    PRIORIDAD = [
        ('alta', 'Alta'),
        ('media', 'Media'),
        ('baja', 'Baja'),
    ]
    ESTADO = [
        ('publicado', 'Publicado'),
        ('en_desarrollo', 'En Desarrollo'),
        ('en_revision', 'En Revisión'),
        ('finalizado', 'Finalizado'),
        ('rechazado', 'Rechazado'),
        ('inactivo', 'Inactivo'),
    ]

    empresa = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='proyectos_empresa')
    titulo = models.CharField(max_length=200)
    descripcion = models.TextField()
    tipo_solucion = models.CharField(max_length=30, choices=TIPO_SOLUCION)
    prioridad = models.CharField(max_length=10, choices=PRIORIDAD, default='media')
    vacantes = models.PositiveIntegerField(default=1)
    estado = models.CharField(max_length=25, choices=ESTADO, default='publicado')
    fecha_publicacion = models.DateTimeField(auto_now_add=True)
    fecha_limite = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.titulo} ({self.get_estado_display()})"

    def registrar_cambio_estado(self, nuevo_estado, usuario, estado_anterior=None):
        """Registra una entrada en el historial de estados."""
        HistorialEstadoProyecto.objects.create(
            proyecto=self,
            estado_anterior=estado_anterior or self.estado,
            estado_nuevo=nuevo_estado,
            cambiado_por=usuario
        )

    class Meta:
        db_table = 'proyectos'
        indexes = [
            models.Index(fields=['estado'], name='idx_estado'),
            models.Index(fields=['tipo_solucion'], name='idx_proy_tipo'),
            models.Index(fields=['prioridad'], name='idx_proy_prio'),
            models.Index(fields=['fecha_publicacion'], name='idx_fecha_publicacion'),
        ]

class Valoracion(models.Model):
    ROL_EVALUADOR = [
        ('empresa', 'Empresa'),
        ('desarrollador', 'Desarrollador'),
    ]
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='valoraciones', db_column='proyecto_id')
    empresa = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='valoraciones_como_empresa', db_column='empresa_id')
    desarrollador = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='valoraciones_como_dev', db_column='desarrollador_id')
    rol_evaluador = models.CharField(max_length=20, choices=ROL_EVALUADOR, default='empresa')
    puntuacion = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comentario = models.TextField(blank=True, null=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'valoraciones'
        unique_together = ('proyecto', 'desarrollador', 'rol_evaluador')
        verbose_name_plural = "Valoraciones"

class HistorialEstadoProyecto(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='historial_estados', db_column='proyecto_id')
    estado_anterior = models.CharField(max_length=50, null=True, blank=True)
    estado_nuevo = models.CharField(max_length=50)
    cambiado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, db_column='cambiado_por')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'historial_estado_proyecto'
        verbose_name_plural = "Historiales de Estados"

class Entregable(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('en_revision', 'En Revisión'),
        ('completado', 'Completado'),
    ]
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='entregables', db_column='proyecto_id')
    equipo = models.ForeignKey('Equipo', on_delete=models.SET_NULL, null=True, blank=True, related_name='hitos', db_column='equipo_id')
    titulo = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'entregables'
        indexes = [
            models.Index(fields=['estado'], name='idx_entregables_estado'),
            models.Index(fields=['fecha_creacion'], name='idx_entregables_fecha'),
        ]

    def __str__(self):
        return f"{self.titulo} - {self.proyecto.titulo}"

    def save(self, *args, **kwargs):
        usuario = kwargs.pop('cambiado_por', None)
        
        if self.estado == 'pendiente' and self.proyecto.estado == 'en_revision':
            proyecto = self.proyecto
            estado_anterior = proyecto.estado
            proyecto.estado = 'en_desarrollo'
            proyecto.save()
            # Registrar el cambio de estado para la auditoría
            proyecto.registrar_cambio_estado('en_desarrollo', usuario, estado_anterior=estado_anterior)
            
        super().save(*args, **kwargs)

class Equipo(models.Model):
    nombre = models.CharField(max_length=100)
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='equipos', db_column='proyecto_id')
    miembros = models.ManyToManyField(Usuario, related_name='mis_equipos')
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'equipos'

    def __str__(self):
        return f"{self.nombre} (Proyecto: {self.proyecto.titulo})"
