"""Extracción de datos de Biwenger para el notebook analisis_biwenger.ipynb.

- Funciona con la sesión (Authorization: Bearer ...) de cualquier usuario: liga, manager,
  competición y sistema de puntos se detectan a partir de esa sesión.
- La sesión se guarda cifrada fuera del proyecto: DPAPI en Windows y llavero del sistema
  (librería keyring) en macOS/Linux.
- Consultas HTTPS con curl en un proceso aparte, solo a hosts de Biwenger autorizados.
- Cada actualización se guarda completa en capturas/FECHA_UTC/ sin sobrescribir las anteriores.
"""
import getpass
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from time import sleep
from urllib.parse import urlencode, urlsplit

SERVICIO = "BiwengerNotebook"
ES_WINDOWS = os.name == "nt"
HOSTS_PERMITIDOS = {"biwenger.as.com", "cf.biwenger.com"}
# Errores puntuales del servidor observados en Biwenger; 401/403/3xx nunca se reintentan.
ESTADOS_TRANSITORIOS = {"404", "429", "500", "502", "503", "504"}
REINTENTOS = 2
# Versión de la web de Biwenger que se anuncia en las cabeceras; si la API la rechaza, actualízala.
VERSION_WEB = "631"


class SesionNoValida(RuntimeError):
    pass


class SesionCifradaWindows:
    """Archivo en %LOCALAPPDATA%, fuera del proyecto, cifrado con CryptProtectData (DPAPI)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        self.ctypes = ctypes
        self.Blob = Blob
        self.api = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        puntero = ctypes.POINTER(Blob)
        self.api.CryptProtectData.argtypes = [puntero, wintypes.LPCWSTR, puntero, ctypes.c_void_p,
                                              ctypes.c_void_p, wintypes.DWORD, puntero]
        self.api.CryptProtectData.restype = wintypes.BOOL
        self.api.CryptUnprotectData.argtypes = [puntero, ctypes.c_void_p, puntero, ctypes.c_void_p,
                                                ctypes.c_void_p, wintypes.DWORD, puntero]
        self.api.CryptUnprotectData.restype = wintypes.BOOL
        self.kernel = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        self.carpeta = Path(os.environ["LOCALAPPDATA"]) / SERVICIO
        self.ruta = self.carpeta / "sesion.dpapi"
        self.descripcion = str(self.ruta)

    def transformar(self, datos, cifrar):
        ctypes = self.ctypes
        buffer = (ctypes.c_ubyte * len(datos)).from_buffer_copy(datos)
        entrada = self.Blob(len(datos), buffer)
        salida = self.Blob()
        funcion = self.api.CryptProtectData if cifrar else self.api.CryptUnprotectData
        try:
            if not funcion(ctypes.byref(entrada), None, None, None, None, 1, ctypes.byref(salida)):
                codigo = ctypes.get_last_error()
                raise OSError(codigo, "No se pudo cifrar o descifrar la sesión con tu usuario de Windows; "
                              "si cambiaste de equipo o perfil, configura renovar_autorizacion = True")
            return ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.memset(buffer, 0, len(datos))
            if salida.pbData:
                ctypes.memset(salida.pbData, 0, salida.cbData)
                self.kernel.LocalFree(salida.pbData)

    def leer(self):
        ruta = self.ruta
        if not ruta.exists():
            # Versiones anteriores guardaban un archivo por liga: sesion_<liga>_<manager>.dpapi
            anteriores = sorted(self.carpeta.glob("sesion_*.dpapi"), key=lambda r: r.stat().st_mtime)
            if not anteriores:
                return None
            ruta = anteriores[-1]
        return self.transformar(ruta.read_bytes(), False).decode("utf-8")

    def existe(self):
        return self.ruta.exists()

    def guardar(self, valor):
        cifrado = self.transformar(valor.encode("utf-8"), True)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        temporal = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.carpeta, suffix=".tmp", delete=False) as archivo:
                temporal = Path(archivo.name)
                archivo.write(cifrado)
            os.replace(temporal, self.ruta)
        finally:
            if temporal is not None:
                temporal.unlink(missing_ok=True)


class SesionLlavero:
    """macOS (Llavero), Linux (Secret Service/KWallet): almacén cifrado del sistema vía keyring."""

    CUENTA = "authorization"

    def __init__(self):
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError("En macOS/Linux instala la dependencia: pip install keyring") from error
        self.keyring = keyring
        self.descripcion = f"llavero del sistema (servicio {SERVICIO})"

    def leer(self):
        return self.keyring.get_password(SERVICIO, self.CUENTA)

    def existe(self):
        return self.leer() is not None

    def guardar(self, valor):
        self.keyring.set_password(SERVICIO, self.CUENTA, valor)


def almacen_sesion():
    return SesionCifradaWindows() if ES_WINDOWS else SesionLlavero()


def normalizar_autorizacion(valor):
    valor = (valor or "").strip()
    if valor.lower().startswith("authorization:"):
        valor = valor.split(":", 1)[1].strip()
    partes = valor.split(None, 1)
    token = valor
    if partes and partes[0].lower() == "bearer":
        token = partes[1] if len(partes) > 1 else ""
    if not token or any(c.isspace() for c in token):
        raise ValueError("Pega solo el valor de la cabecera Authorization (Bearer eyJ...), sin otras cabeceras")
    return "Bearer " + token


def obtener_autorizacion(renovar=False):
    almacen = almacen_sesion()
    autorizacion = None if renovar else almacen.leer()
    nueva = renovar or not almacen.existe()
    if autorizacion is None:
        print("Configuración inicial: pega el valor de la cabecera Authorization de Biwenger "
              f"(se guardará cifrado en {almacen.descripcion}).")
        autorizacion = getpass.getpass("Authorization (Bearer ...): ")
    return normalizar_autorizacion(autorizacion), almacen, nueva


def consultar_json(url, autorizacion=None, liga_id=None, manager_id=None):
    destino = urlsplit(url)
    if destino.scheme != "https" or destino.hostname not in HOSTS_PERMITIDOS or destino.port not in (None, 443) or destino.username is not None:
        raise ValueError("La consulta debe dirigirse a un endpoint HTTPS de Biwenger autorizado")
    if autorizacion is not None and destino.hostname != "biwenger.as.com":
        raise ValueError("No se envía autorización al catálogo público ni a otros hosts")
    ejecutable = which("curl.exe") or which("curl")
    if ejecutable is None:
        raise RuntimeError("No se encuentra curl; instálalo o añádelo al PATH")
    # La autorización viaja por stdin (--config -), nunca en la línea de comandos.
    configuracion = ""
    if autorizacion is not None:
        if any(c in autorizacion for c in "\r\n"):
            raise ValueError("Introduce solo el valor completo de Authorization")
        configuracion = "header = " + json.dumps("Authorization: " + autorizacion) + "\n"
    argumentos = [ejecutable, "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "30",
                  "--proto", "=https", "--config", "-", "--header", "Accept: application/json",
                  "--header", "Cache-Control: no-cache"]
    if ES_WINDOWS:
        # Schannel: mantiene la validación TLS aunque no se pueda consultar la revocación.
        argumentos.append("--ssl-revoke-best-effort")
    if autorizacion is not None:
        argumentos += ["--header", f"X-Version: {VERSION_WEB}"]
        if liga_id is not None:
            argumentos += ["--header", f"X-League: {liga_id}", "--header", f"X-User: {manager_id}"]
    resultado = estado = None
    for intento in range(REINTENTOS + 1):
        if intento:
            print(f"  HTTP {estado} transitorio en {destino.path}; reintento {intento}/{REINTENTOS}…", flush=True)
            sleep(2 * intento)
        resultado = subprocess.run(argumentos + ["--write-out", "\n%{http_code}", url],
                                   input=configuracion, capture_output=True, text=True,
                                   encoding="utf-8", timeout=40)
        if resultado.returncode != 0:
            raise RuntimeError(f"Error curl {resultado.returncode}; actualización detenida sin usar datos antiguos")
        cuerpo, separador, estado = resultado.stdout.rpartition("\n")
        if not separador or estado not in ESTADOS_TRANSITORIOS:
            break
    if estado == "401" and autorizacion is not None:
        raise SesionNoValida("HTTP 401: la sesión no es válida o ha caducado. Pon renovar_autorizacion = True "
                             "y pega una autorización nueva.")
    if separador and estado in {"301", "302", "303", "307", "308"}:
        raise RuntimeError(f"HTTP {estado}: redirección del endpoint {destino.hostname}{destino.path}; "
                           "no indica por sí sola un fallo de sesión. Se detiene sin reenviar credenciales.")
    if not separador or estado != "200":
        detalle = "el servidor rechaza el acceso; comprueba sesión o restricciones de acceso" if estado == "403" else "fallo del endpoint o del servicio"
        raise RuntimeError(f"HTTP {estado if separador else 'desconocido'} en {destino.hostname}{destino.path}: {detalle}")
    respuesta = json.loads(cuerpo)
    if respuesta["status"] != 200:
        raise ValueError("La API no devuelve status 200")
    return respuesta


def elegir_liga(ligas, liga=None):
    """Elige la liga de la cuenta: la única que haya, o la indicada por ID o por (parte del) nombre."""
    if liga is not None:
        clave = str(liga).strip().lower()
        candidatas = [l for l in ligas if str(l["id"]) == clave] or \
                     [l for l in ligas if clave in str(l["name"]).lower()]
    else:
        candidatas = ligas
    if len(candidatas) == 1:
        return candidatas[0]
    opciones = "; ".join(f'{l["name"]} (id {l["id"]})' for l in ligas)
    if not candidatas:
        raise ValueError(f"No encuentro la liga {liga!r}. Tus ligas: {opciones}")
    raise ValueError(f'Tienes varias ligas: escribe su nombre o ID en la variable liga. Opciones: {opciones}')


def actualizar_captura(liga=None, renovar=False):
    autorizacion, almacen, nueva = obtener_autorizacion(renovar)
    archivos, horas = {}, {}
    inicio = datetime.now(timezone.utc)
    identidad = {}

    def obtener(nombre, url, privada=True):
        nonlocal nueva
        if horas:
            sleep(1)
        print(f"Consultando {nombre}…", flush=True)
        respuesta = consultar_json(url, autorizacion if privada else None,
                                   identidad.get("liga_id"), identidad.get("manager_id"))
        if privada and nueva:
            almacen.guardar(autorizacion)
            nueva = False
            print(f"Sesión validada y guardada cifrada en {almacen.descripcion}.")
        horas[nombre] = datetime.now(timezone.utc).isoformat()
        return respuesta

    try:
        # 1) Cuenta: de aquí salen la liga, el manager, la competición y el sistema de puntos.
        cuenta = obtener("cuenta", "https://biwenger.as.com/api/v2/account")
        liga_cuenta = elegir_liga(cuenta["data"]["leagues"], liga)
        liga_id, manager_id = str(liga_cuenta["id"]), str(liga_cuenta["user"]["id"])
        competicion, puntuacion = liga_cuenta["competition"], liga_cuenta["scoreID"]
        identidad.update(liga_id=liga_id, manager_id=manager_id)
        print(f'Liga detectada: {liga_cuenta["name"]} · competición {competicion} · sistema de puntos {puntuacion}')

        mercado = obtener("mercado.json", "https://biwenger.as.com/api/v2/market")
        if not isinstance(mercado["data"]["sales"], list):
            raise TypeError("El mercado no contiene una lista de ventas")
        mercado["data"]["status"]["balance"]
        mercado["data"]["status"]["maximumBid"]
        archivos["mercado.json"] = {"status": 200, "data": {
            "sales": mercado["data"]["sales"], "status": mercado["data"]["status"]}}

        consulta_catalogo = urlencode({"lang": "en", "score": puntuacion})
        catalogo = obtener("jugadores.json",
            f"https://cf.biwenger.com/api/v2/competitions/{competicion}/data?{consulta_catalogo}", False)
        if not isinstance(catalogo["data"]["players"], dict):
            raise TypeError("El catálogo no contiene un diccionario de jugadores")
        if catalogo["data"].get("scoreID") != puntuacion:
            raise ValueError(f"El catálogo no confirma el sistema de puntos de la liga ({puntuacion})")
        archivos["jugadores.json"] = catalogo

        calendarios = {}
        for equipo_id in catalogo["data"]["teams"]:
            consulta_equipo = urlencode({"lang": "en", "fields": "id,matches"})
            respuesta_equipo = obtener(f"calendario_equipo_{equipo_id}",
                f"https://cf.biwenger.com/api/v2/teams/{competicion}/{equipo_id}?{consulta_equipo}", False)
            datos_equipo = respuesta_equipo["data"]
            if str(datos_equipo["id"]) != str(equipo_id) or not isinstance(datos_equipo["matches"], list):
                raise ValueError(f"Calendario incorrecto del equipo {equipo_id}")
            calendarios[str(equipo_id)] = {"id": datos_equipo["id"], "matches": datos_equipo["matches"]}
        archivos["calendarios.json"] = {"status": 200, "data": calendarios}

        campos_reglas = ("clause", "clauseActivationDelay", "clauseRoundDisabledHours",
                         "clauseExecutedLimit", "clauseReceivedLimit")
        consulta_liga = urlencode({"include": "all,-lastAccess", "fields": "id,scoreID,standings,settings(" + ",".join(campos_reglas) + ")"})
        liga_datos = obtener("liga.json", "https://biwenger.as.com/api/v2/league?" + consulta_liga)["data"]
        if str(liga_datos["id"]) != liga_id or liga_datos["scoreID"] != puntuacion:
            raise ValueError("La liga o la puntuación no coinciden con la cuenta")
        participantes = liga_datos["standings"]
        ids = [str(manager["id"]) for manager in participantes]
        if manager_id not in ids or len(ids) != len(set(ids)):
            raise ValueError("Clasificación sin el manager propio o con IDs duplicados")

        # De la cuenta solo se guarda el bloqueo propio para ejecutar cláusulas.
        archivos["reglas_clausulas.json"] = {"status": 200, "data": {
            "liga_id": liga_id, "manager_id": manager_id,
            "settings": {k: liga_datos["settings"][k] for k in campos_reglas},
            "clauseExecutionDisabledUntil": liga_cuenta["user"].get("clauseExecutionDisabledUntil"),
        }}
        archivos["liga.json"] = {"status": 200, "data": {"id": liga_datos["id"], "standings": [
            {campo: manager[campo] for campo in ("id", "name", "teamSize")} for manager in participantes]}}

        campos = urlencode({"fields": "id,name,players(id,owner),league(id),clauseReceptionLockedUntil"})
        for manager in participantes:
            id_manager = str(manager["id"])
            nombre = f"plantilla_{id_manager}.json"
            plantilla = obtener(nombre, f"https://biwenger.as.com/api/v2/user/{id_manager}?{campos}")["data"]
            if str(plantilla["id"]) != id_manager or str(plantilla["league"]["id"]) != liga_id:
                raise ValueError(f"Identidad incorrecta en {nombre}")
            if not isinstance(plantilla["players"], list) or len(plantilla["players"]) != manager["teamSize"]:
                raise ValueError(f"Plantilla distinta de la clasificación en {nombre}; vuelve a actualizar")
            miembros = []
            for miembro in plantilla["players"]:
                propietario = miembro.get("owner")
                miembros.append({"id": miembro["id"], "owner": {
                    campo: propietario[campo] for campo in ("date", "clause", "clauseLockedUntil") if campo in propietario
                } if propietario is not None else None})
            archivos[nombre] = {"status": 200, "data": {"id": plantilla["id"], "name": plantilla["name"],
                "league": {"id": liga_id}, "players": miembros,
                "clauseReceptionLockedUntil": plantilla.get("clauseReceptionLockedUntil")}}

        destino = Path("capturas") / inicio.strftime("%Y%m%dT%H%M%S%fZ")
        destino.mkdir(parents=True, exist_ok=False)
        for nombre, contenido in archivos.items():
            (destino / nombre).write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")
        metadatos = {"inicio_utc": inicio.isoformat(), "fin_utc": datetime.now(timezone.utc).isoformat(),
                     "liga_id": liga_id, "liga_nombre": liga_cuenta["name"], "manager_id": manager_id,
                     "competicion": competicion, "scoreID": puntuacion,
                     "consultas_utc": horas, "archivos": list(archivos)}
        (destino / "captura.json").write_text(json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8")
        return destino
    finally:
        autorizacion = ""


def identidad_captura(carpeta):
    """(liga_id, manager_id) de una captura, leídos de sus reglas de cláusulas."""
    reglas = json.loads((Path(carpeta) / "reglas_clausulas.json").read_text(encoding="utf-8"))["data"]
    return str(reglas["liga_id"]), str(reglas["manager_id"])


def seleccionar_captura(modo, liga=None, renovar=False):
    """Devuelve la carpeta de datos: una captura nueva ("actualizar") o la última guardada ("local")."""
    if modo == "actualizar":
        carpeta = actualizar_captura(liga, renovar)
    elif modo == "local":
        completas = [r.parent for r in sorted(Path("capturas").glob("*/captura.json"))
                     if (r.parent / "reglas_clausulas.json").exists()]
        if liga is not None:
            completas = [c for c in completas if identidad_captura(c)[0] == str(liga).strip()] or completas
        if not completas:
            raise FileNotFoundError('No hay capturas guardadas: ejecuta primero con modo_datos = "actualizar"')
        carpeta = completas[-1]
        print("MODO LOCAL: datos guardados, no una consulta actual.")
    else:
        raise ValueError('Configura modo_datos como "actualizar" o "local"')
    metadatos = json.loads((carpeta / "captura.json").read_text(encoding="utf-8"))
    if not all((carpeta / nombre).is_file() for nombre in metadatos["archivos"]):
        raise FileNotFoundError("La captura seleccionada ha perdido archivos; no se analizará")
    print("Captura UTC:", metadatos["inicio_utc"], "→", metadatos["fin_utc"])
    print("Carpeta seleccionada:", carpeta)
    return carpeta
