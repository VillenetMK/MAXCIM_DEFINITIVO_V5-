from glob import glob
from setuptools import find_packages, setup

package_name = "maxcim_odometry"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="VillenetMK",
    maintainer_email="147531932+VillenetMK@users.noreply.github.com",
    description="Odometry from real physical wheel feedback for MAXCIM.",
    license="Proprietary",
    entry_points={"console_scripts": ["wheel_odometry = maxcim_odometry.node:main"]},
)
