import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from proyectos.models import Proyecto

User = get_user_model()


class TestListarProyectos:
    """Tests for listar_proyectos view."""

    def test_listar_publicados(self, auth_client, empresa_user):
        """Public listing shows only 'publicado' projects."""
        Proyecto.objects.create(
            empresa=empresa_user, titulo='Public', descripcion='P',
            tipo_solucion='sitio_web', estado='publicado',
        )
        Proyecto.objects.create(
            empresa=empresa_user, titulo='Hidden', descripcion='H',
            tipo_solucion='sitio_web', estado='en_desarrollo',
        )
        response = auth_client.get(reverse('listar_proyectos'))
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Public' in content
        assert 'Hidden' not in content

    def test_filtrar_por_tipo(self, auth_client, empresa_user):
        """Filter by tipo_solucion works."""
        Proyecto.objects.create(
            empresa=empresa_user, titulo='Web App', descripcion='W',
            tipo_solucion='sitio_web', estado='publicado',
        )
        Proyecto.objects.create(
            empresa=empresa_user, titulo='Mobile App', descripcion='M',
            tipo_solucion='aplicacion_movil', estado='publicado',
        )
        response = auth_client.get(reverse('listar_proyectos') + '?tipo=sitio_web')
        content = response.content.decode()
        assert 'Web App' in content
        assert 'Mobile App' not in content
        assert response.status_code == 200


class TestCrearProyecto:
    """Tests for crear_proyecto view."""

    def test_empresa_puede_crear(self, auth_client, empresa_user):
        """Empresa user can create a project."""
        response = auth_client.post(reverse('crear_proyecto'), {
            'titulo': 'Nuevo Proyecto',
            'descripcion': 'Descripción del proyecto',
            'tipo_solucion': 'sitio_web',
            'prioridad': 'alta',
            'vacantes': '2',
        })
        assert response.status_code == 302
        assert Proyecto.objects.filter(titulo='Nuevo Proyecto').exists()

    def test_dev_no_puede_crear(self, dev_client):
        """Dev user cannot create a project."""
        response = dev_client.post(reverse('crear_proyecto'), {
            'titulo': 'Proyecto Dev',
            'descripcion': 'No debería crearse',
            'tipo_solucion': 'sitio_web',
        })
        assert response.status_code == 302
        assert not Proyecto.objects.filter(titulo='Proyecto Dev').exists()

    def test_anonimo_no_puede_crear(self, client):
        """Anonymous cannot create projects."""
        response = client.post(reverse('crear_proyecto'), {
            'titulo': 'Anon',
            'descripcion': 'Nope',
            'tipo_solucion': 'sitio_web',
        })
        assert response.status_code == 302
        assert not Proyecto.objects.filter(titulo='Anon').exists()

    def test_crear_registra_historial(self, auth_client, empresa_user):
        """Creating a project registers initial estado in historial."""
        auth_client.post(reverse('crear_proyecto'), {
            'titulo': 'Con Historial',
            'descripcion': 'Test',
            'tipo_solucion': 'sitio_web',
        })
        proyecto = Proyecto.objects.get(titulo='Con Historial')
        historial = proyecto.historial_estados.first()
        assert historial is not None
        assert historial.estado_nuevo == 'publicado'


class TestEditarProyecto:
    """Tests for editar_proyecto view (SCRUM-103 / RF 2.2.2)."""

    def _proyecto_publicado(self, empresa):
        return Proyecto.objects.create(
            empresa=empresa, titulo='Editable', descripcion='E',
            tipo_solucion='sitio_web', estado='publicado', vacantes=2,
        )

    def test_empresa_duena_ve_formulario(self, auth_client, empresa_user):
        """Empresa owner can GET the edit form prefilled."""
        proyecto = self._proyecto_publicado(empresa_user)
        response = auth_client.get(
            reverse('editar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'Editable' in content
        assert 'value="2"' in content  # vacantes pre-llenado

    def test_empresa_puede_editar(self, auth_client, empresa_user):
        """Empresa owner can POST valid edits."""
        proyecto = self._proyecto_publicado(empresa_user)
        response = auth_client.post(
            reverse('editar_proyecto', args=[proyecto.id]),
            {
                'titulo': 'Titulo Nuevo',
                'descripcion': 'Desc nueva',
                'tipo_solucion': 'aplicacion_movil',
                'prioridad': 'alta',
                'vacantes': '5',
                'fecha_limite': '',
            },
        )
        assert response.status_code == 302
        proyecto.refresh_from_db()
        assert proyecto.titulo == 'Titulo Nuevo'
        assert proyecto.tipo_solucion == 'aplicacion_movil'
        assert proyecto.vacantes == 5

    def test_otra_empresa_no_puede(self, db, empresa_user):
        """Another empresa cannot edit (404 via get_object_or_404)."""
        otra = User.objects.create_user(
            username='otra_edit', email='otra2@test.teir',
            nombre='Otra', rol='empresa', estado='activo',
            password='TestPass123!',
        )
        proyecto = self._proyecto_publicado(otra)
        from django.test import Client
        client = Client()
        client.login(username='empresa_test', password='TestPass123!')
        response = client.get(
            reverse('editar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 404

    def test_dev_no_puede_editar(self, dev_client, empresa_user):
        """Dev user cannot edit projects."""
        proyecto = self._proyecto_publicado(empresa_user)
        response = dev_client.get(
            reverse('editar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 302  # redirect a dashboard_empresa

    def test_no_editable_si_estado_no_publicado(self, auth_client, empresa_user):
        """Project in en_desarrollo cannot be edited."""
        proyecto = Proyecto.objects.create(
            empresa=empresa_user, titulo='En Desarrollo',
            descripcion='D', tipo_solucion='sitio_web',
            estado='en_desarrollo',
        )
        response = auth_client.get(
            reverse('editar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 302  # blocked

    def test_no_editable_con_contrato_activo(self, auth_client, empresa_user):
        """Project with an active contratacion cannot be edited."""
        proyecto = self._proyecto_publicado(empresa_user)
        from contrataciones.models import Contratacion
        dev = User.objects.create_user(
            username='dev_contratado', email='devc@test.teir',
            nombre='Dev', rol='desarrollador', estado='activo',
            password='TestPass123!',
        )
        Contratacion.objects.create(
            proyecto=proyecto, desarrollador=dev, empresa=empresa_user,
            estado='activa',
        )
        response = auth_client.get(
            reverse('editar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 302  # blocked by active contract

    def test_vacantes_invalidas_no_guardan(self, auth_client, empresa_user):
        """vacantes < 1 is rejected and nothing is saved."""
        proyecto = self._proyecto_publicado(empresa_user)
        response = auth_client.post(
            reverse('editar_proyecto', args=[proyecto.id]),
            {
                'titulo': 'No Debe Guardar',
                'descripcion': 'X',
                'tipo_solucion': 'sitio_web',
                'prioridad': 'media',
                'vacantes': '0',
            },
        )
        assert response.status_code == 302
        proyecto.refresh_from_db()
        assert proyecto.titulo == 'Editable'  # unchanged


class TestFinalizarProyecto:
    """Tests for finalizar_proyecto view."""

    def test_empresa_puede_acceder(self, auth_client, empresa_user):
        """Empresa can access finalizar page (redirects if no hitos)."""
        proyecto = Proyecto.objects.create(
            empresa=empresa_user, titulo='Para Finalizar',
            descripcion='T', tipo_solucion='sitio_web',
            estado='en_desarrollo',
        )
        response = auth_client.get(
            reverse('finalizar_proyecto', args=[proyecto.id])
        )
        # Redirects because no hitos defined — but page is accessible (not 404/403)
        assert response.status_code == 302

    def test_otra_empresa_no_puede(self, db, empresa_user):
        """Empresa cannot finalize another empresa's project."""
        otra = User.objects.create_user(
            username='otra_emp', email='otra@test.teir',
            nombre='Otra', rol='empresa', estado='activo',
            password='TestPass123!',
        )
        proyecto = Proyecto.objects.create(
            empresa=otra, titulo='Ajeno', descripcion='A',
            tipo_solucion='sitio_web', estado='en_desarrollo',
        )
        from django.test import Client
        client = Client()
        client.login(username='empresa_test', password='TestPass123!')
        response = client.get(
            reverse('finalizar_proyecto', args=[proyecto.id])
        )
        assert response.status_code == 404
