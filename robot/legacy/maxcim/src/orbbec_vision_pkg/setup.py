from setuptools import find_packages, setup

package_name = 'orbbec_vision_pkg'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='maxcim',
    maintainer_email='maxcim@todo.todo',
    description='Nodos de visión para Orbbec Gemini 2',
    license='TODO: License declaration',
    extras_require={
        'test': ['pytest'],
    },
    entry_points={
        'console_scripts': [
            'orbbec_camera_node = orbbec_vision_pkg.orbbec_camera_node:main',
            'orbbec_face_recognition_node = orbbec_vision_pkg.orbbec_face_recognition_node:main',
            'orbbec_proximity_node = orbbec_vision_pkg.orbbec_proximity_node:main',
        ],
    },
)
