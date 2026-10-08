from setuptools import find_packages, setup

package_name = 'reasoning_pkg'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/maxcim.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='maxcim',
    maintainer_email='maxcim@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'gemini_live_node = reasoning_pkg.gemini_live_node:main',
        ],
    },
)
