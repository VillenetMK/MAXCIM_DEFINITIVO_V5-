from setuptools import find_packages, setup

package_name = 'memory_pkg'

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
    maintainer_email='alexrodas0706@gmail.com',
    description='Memoria personal por usuario con Qdrant y n8n.',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'memory_node = memory_pkg.memory_node:main',
        ],
    },
)
