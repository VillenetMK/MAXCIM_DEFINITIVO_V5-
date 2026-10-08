from glob import glob
from setuptools import setup
setup(name="maxcim_control",version="5.0.0",packages=["maxcim_control"],
 data_files=[("share/ament_index/resource_index/packages",["resource/maxcim_control"]),("share/maxcim_control",["package.xml"]),("share/maxcim_control/launch",glob("launch/*.py")),("share/maxcim_control/config",glob("config/*"))],
 install_requires=["setuptools"],zip_safe=True,maintainer="VillenetMK",maintainer_email="147531932+VillenetMK@users.noreply.github.com",description="Autoridad de control MAXCIM",license="Proprietary",
 entry_points={"console_scripts":["gateway = maxcim_control.gateway:main","arms = maxcim_control.arms:main"]})
