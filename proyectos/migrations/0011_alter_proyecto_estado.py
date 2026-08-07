
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('proyectos', '0010_alter_valoracion_puntuacion'),
    ]

    operations = [
        migrations.AlterField(
            model_name='proyecto',
            name='estado',
            field=models.CharField(choices=[('publicado', 'Publicado'), ('en_desarrollo', 'En Desarrollo'), ('en_revision', 'En Revisión'), ('finalizado', 'Finalizado'), ('rechazado', 'Rechazado'), ('inactivo', 'Inactivo')], default='publicado', max_length=25),
        ),
    ]
