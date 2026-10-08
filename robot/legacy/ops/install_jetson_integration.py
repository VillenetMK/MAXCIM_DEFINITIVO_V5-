#!/usr/bin/env python3
"""Install the separate Studio bridge; never modify the original AI workspace."""
import ast
from pathlib import Path
import shutil


def install(repo, destination=None):
    destination = destination or Path.home()/'.local/share/max-studio-jetson'
    files = ('jetson_agent.py', 'jetson_hub.py', 'wire.py',
             'gemini_tools.py', 'mobility_voice.py')
    for name in files:
        ast.parse((repo/'studio'/name).read_text())
    destination.mkdir(parents=True, exist_ok=True)
    for name in files:
        shutil.copy2(repo/'studio'/name, destination/name)
    print('Módulo independiente instalado en '+str(destination))
    print('La IA original no se modifica. El asistente de movilidad no se inicia automáticamente.')


if __name__ == '__main__':
    install(Path(__file__).resolve().parents[1])
