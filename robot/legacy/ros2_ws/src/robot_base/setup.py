import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'robot_base'

setup(
    name=package_name,
    version='0.0.1',
    packages=find_packages(exclude=['test']),
    data_files=[
        (
            'share/ament_index/resource_index/packages',
            ['resource/' + package_name],
        ),
        ('share/' + package_name, ['package.xml']),
        (
            os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py'),
        ),
        # AGREGAR ESTA LÍNEA PARA INSTALAR LA CARPETA URDF:
        (
            os.path.join('share', package_name, 'urdf'),
            glob('urdf/*'),
        ),
    ],
    install_requires=['setuptools', 'pyserial'],
    zip_safe=True,
    maintainer='User',
    maintainer_email='user@todo.todo',
    description='Paquete de control de base diferencial y odometria con Arduino',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'base_node = robot_base.base_node:main',
            'scan_relay_node = robot_base.scan_relay_node:main',
        ],
    },
)
