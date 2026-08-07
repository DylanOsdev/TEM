from django.urls import path
from . import views

urlpatterns = [
    path('', views.listar_proyectos, name='listar_proyectos'),
    path('crear/', views.crear_proyecto, name='crear_proyecto'),
    path('editar/<int:proyecto_id>/', views.editar_proyecto, name='editar_proyecto'),
    path('desactivar/<int:proyecto_id>/', views.desactivar_proyecto, name='desactivar_proyecto'),
    path('finalizar/<int:proyecto_id>/', views.finalizar_proyecto, name='finalizar_proyecto'),
    path('calificar-empresa/<int:proyecto_id>/', views.calificar_empresa, name='calificar_empresa'),
    path('hitos/<int:proyecto_id>/', views.gestionar_hitos, name='gestionar_hitos'),
    path('hitos/eliminar/<int:hito_id>/', views.eliminar_hito, name='eliminar_hito'),
    path('equipos/<int:proyecto_id>/', views.gestionar_equipos, name='gestionar_equipos'),
    path('equipos/eliminar/<int:equipo_id>/', views.eliminar_equipo, name='eliminar_equipo'),
]
