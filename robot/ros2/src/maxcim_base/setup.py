from glob import glob
from setuptools import setup

setup(name='maxcim_base', version='0.1.0', packages=['maxcim_base'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/maxcim_base']),
                  ('share/maxcim_base', ['package.xml']),
                  ('share/maxcim_base/launch', glob('launch/*.launch.py')),
                  ('share/maxcim_base/config', glob('config/*.yaml'))],
      install_requires=['setuptools', 'pyserial'], zip_safe=True,
      maintainer='VillenetMK',
      maintainer_email='147531932+VillenetMK@users.noreply.github.com',
      description='Control físico diferencial Nano/FG', license='Proprietary',
      entry_points={'console_scripts': ['nano_base = maxcim_base.node:main']})
