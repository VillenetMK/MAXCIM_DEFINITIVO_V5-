"""Resolución de los gestos heredados, sin ROS, puertos ni efectos al importar."""
import json
import math
from pathlib import Path


class Catalog:
    def __init__(self, directory):
        root = Path(directory)
        self.steps = self._load(root / "pasos_individuales.json")
        self.actions = self._load(root / "acciones_rapidas.json")
        # Los archivos divididos por brazo/acción son la versión mantenida.
        for folder, target in (("pasos", self.steps), ("acciones", self.actions), ("movimientos", self.actions)):
            for path in sorted((root/folder).glob("*.json")):
                target.update(self._load(path))
        self.voice = self._load(root / "comandos_voz.json")
        self.voice["home"] = {"movimiento": "HOME-DOS-BRAZOS"}
        # El controlador original trata este nombre como orden especial de home.
        self.actions["[ IR A HOME COMIENZO ]"] = {"mode":"parallel", "sequence":[f"HOME-CH{ch}" for ch in (0,1,2,4,5,6)]}

    @staticmethod
    def _load(path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Catálogo inválido: " + str(path))
        return data

    def expand(self, name, stack=()):
        if len(stack) > 20 or name in stack:
            raise ValueError("Referencia circular en el catálogo")
        if name in self.steps:
            step = dict(self.steps[name])
            if type(step.get("channel")) is not int or step["channel"] not in (0,1,2,4,5,6):
                raise ValueError("Canal inválido en " + name)
            for key in ("angle", "interval", "delay"):
                value = step.get(key, 0 if key == "delay" else None)
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Paso inválido: " + name)
                step[key] = value
            if not 0 <= step["angle"] <= 270 or not 1 <= step["interval"] <= 100 or not 0 <= step["delay"] <= 20:
                raise ValueError("Paso fuera de límites: " + name)
            return [[step]]
        item = self.actions.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("sequence"), list):
            raise ValueError("Acción no encontrada: " + name)
        result = []
        for entry in item["sequence"]:
            entries = entry if isinstance(entry, list) else [entry]
            parallel = []
            for child in entries:
                if not isinstance(child, str):
                    raise ValueError("Referencia inválida en " + name)
                expanded = self.expand(child, stack+(name,))
                if len(entries) > 1:
                    # Una agrupación paralela exige pasos simples; no interpretar
                    # dos rutinas complejas como si tuvieran el mismo tiempo.
                    if len(expanded) != 1:
                        raise ValueError("Paralelismo complejo pendiente de revisión: " + name)
                    parallel += expanded[0]
                else:
                    result += expanded
            if len(entries) > 1:
                result.append(parallel)
        if item.get("mode", "sequence") == "parallel":
            if any(len(self.expand(child, stack+(name,))) != 1 for child in item["sequence"] if isinstance(child, str)):
                raise ValueError("Acción paralela compleja pendiente de revisión: " + name)
            result = [[step for group in result for step in group]]
        for group in result:
            if len({step["channel"] for step in group}) != len(group):
                raise ValueError("Dos órdenes simultáneas sobre el mismo servo")
        if len(result) > 200:
            raise ValueError("Secuencia demasiado larga")
        if item.get("wait"):
            wait = item["wait"]
            if type(wait) not in (int,float) or not math.isfinite(wait) or not 0 < wait <= 20:
                raise ValueError("Espera de acción inválida")
            result.append([{"wait":wait}])
        return result

    def gesture(self, name):
        item = self.voice.get(name)
        if not isinstance(item, dict):
            raise ValueError("Gesto fuera del catálogo")
        groups = self.expand(item["movimiento"])
        wait = item.get("esperar_segundos", 0)
        if type(wait) not in (int,float) or not math.isfinite(wait) or not 0 <= wait <= 20:
            raise ValueError("Espera inválida")
        if wait:
            groups.append([{"wait": wait}])
        if item.get("volver_a_home"):
            groups += self.expand(item["volver_a_home"])
        return groups


def calibrated_moves(groups, calibration):
    """Validar toda la secuencia antes del primer movimiento; no recortar ángulos."""
    if calibration.get("verified") is not True:
        raise ValueError("Calibración física de brazos pendiente")
    converted = []
    for group in groups:
        moves = []
        for step in group:
            if "wait" in step:
                moves.append(dict(step))
                continue
            servo = calibration["servos"].get(str(step["channel"]))
            if not isinstance(servo, dict):
                raise ValueError("Falta calibración del canal")
            amin, amax = servo["logical_min"], servo["logical_max"]
            low, high = servo["pulse_min"], servo["pulse_max"]
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (amin,amax,low,high)):
                raise ValueError("Calibración no finita")
            if not amin < amax or not 80 <= low < high <= 600:
                raise ValueError("Calibración de pulsos inválida")
            if not amin <= step["angle"] <= amax:
                raise ValueError("Ángulo fuera de la calibración medida")
            ticks = round(low + (step["angle"]-amin)*(high-low)/(amax-amin))
            moves.append(dict(step, ticks=ticks))
        converted.append(moves)
    return converted
