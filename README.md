# Biwenger · ¿A quién fichar hoy?

Notebook de Jupyter que analiza **tu liga de Biwenger** y te dice qué jugadores merece la pena fichar, del mercado o de las plantillas rivales mediante cláusula. Busca los que pueden **sumar más puntos que los tuyos** o **revalorizarse** (ganar dinero), sin tener que revisar uno a uno los cientos de jugadores de la competición.

Proyecto del módulo de APIs de la EOI: **conexión a una API** (no oficial) con un proceso **ETL** completo.

| Fase | Qué hace |
|---|---|
| **Extracción** | Descarga en directo mercado, catálogo de jugadores, calendarios, reglas de cláusulas y plantillas de todos los managers. Guarda una captura fechada en `capturas/` |
| **Transformación** | Valida y une todo por ID; calcula precio, variación diaria, media de puntos en casa/fuera y cuándo se puede clausular cada jugador (hora de Madrid, reglas de tu liga) |
| **Modelo** | Estima los puntos de los próximos 3 partidos (localía y dificultad del rival) y da un rating 0–100 que combina puntos, revalorización y coste |
| **Resultados** | Resumen y tablas: mejores compras del mercado de hoy, jugadores que mejoran tu plantilla, jugadores para hacer dinero y cláusulas que se desbloquean pronto |

## Requisitos

- Python 3 (probado con 3.14) y [VS Code](https://code.visualstudio.com/) con la extensión **Jupyter** (o Jupyter Lab/Notebook).
- `curl` en el PATH (viene incluido en Windows 10/11, macOS y casi todas las distribuciones Linux).
- Una cuenta de Biwenger con al menos una liga.

## Instalación

```bash
git clone https://github.com/guensovt/biwenger-fichajes.git
cd biwenger-fichajes
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt
```

Abre la carpeta en VS Code, abre `analisis_biwenger.ipynb` y elige el kernel de `.venv`.

## Obtener tu `Authorization` (solo la primera vez)

La API de Biwenger no tiene registro de aplicaciones: se usa la misma sesión que la web. Para copiarla:

1. Entra en [biwenger.as.com](https://biwenger.as.com) con tu cuenta.
2. Abre las herramientas de desarrollador (**F12**) → pestaña **Red / Network**.
3. Recarga la página (**F5**) y escribe `api` en el filtro.
4. Pulsa cualquier petición a `biwenger.as.com/api/v2/...` (por ejemplo `account`).
5. En **Encabezados / Headers → Request Headers**, busca `Authorization`. Haz clic derecho y elige **Copiar valor**. Empieza por `Bearer eyJ...`.

> ⚠️ Ese valor equivale a tu contraseña mientras la sesión esté activa: **no lo compartas, no lo pegues en el código ni lo subas a GitHub**.

## Ejecutar

1. Pulsa **Ejecutar todo / Run All**.
2. La primera vez aparece una casilla oculta: pega ahí el `Authorization`. Tras la primera consulta correcta se guarda **cifrado y fuera del proyecto**:
   - Windows: `%LOCALAPPDATA%\BiwengerNotebook\sesion.dpapi`, cifrado con DPAPI (solo tu usuario de Windows puede descifrarlo).
   - macOS / Linux: llavero del sistema (Llavero, Secret Service o KWallet) mediante `keyring`.
3. Las siguientes veces no pide nada: descarga los datos de ese día y muestra los resultados al final, en **4. Resultados**.

Tu **liga**, tu **manager**, la **competición** y el **sistema de puntos** se detectan solos a partir de la sesión.

### Opciones de la celda «Actualizar datos»

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `liga` | `None` | Si tienes varias ligas, pon su nombre (o parte) o su ID |
| `modo_datos` | `"actualizar"` | `"local"` reutiliza la última captura sin conexión, útil para demostraciones |
| `renovar_autorizacion` | `False` | Ponlo a `True` si aparece **HTTP 401** (sesión caducada), pega un `Authorization` nuevo y vuelve a `False` |

### Parámetros del modelo (sección 3)

| Parámetro | Por defecto | Significado |
|---|---|---|
| `pesos_rating` | 40 % deportivo, 50 % económico, 10 % coste | Peso de cada componente en el rating 0–100 |
| `horizonte_rating` | 3 | Próximos partidos que se tienen en cuenta |
| `margen_mejora_puntos` | 0,5 | Puntos/partido que debe superar a tu peor jugador de su posición |
| `caida_maxima_deportiva` | −50.000 €/día | Por debajo no se propone como mejora deportiva |
| `subida_minima_dinero` | 50.000 €/día | Subida mínima para aparecer como oportunidad de dinero |
| `maximo_por_lista` | 10 | Jugadores por tabla |

## Estructura

```
analisis_biwenger.ipynb   Notebook principal (ETL + resultados)
biwenger_datos.py         Extracción: sesión cifrada, consultas con curl, reintentos y capturas
requirements.txt          Dependencias
docs/memoria.pdf          Documento explicativo del proyecto
capturas/                 Datos descargados (se crea al ejecutar; excluido de Git)
```

## Privacidad y seguridad

- El `Authorization` nunca se escribe en el proyecto, en las salidas ni en la línea de comandos: se pasa a `curl` por la entrada estándar y solo se envía a `biwenger.as.com`.
- `capturas/` contiene datos de tu liga (saldo, plantillas y nombres de otros managers) y está en `.gitignore`. El notebook se publica **sin salidas**.
- Para borrar la sesión guardada, elimina `sesion.dpapi` (Windows) o la entrada `BiwengerNotebook` del llavero.

## Limitaciones

- La API **no es oficial** y puede cambiar sin aviso. Si deja de funcionar, puede que haya que actualizar `VERSION_WEB` en `biwenger_datos.py`.
- El rating es una **heurística sin validar**: no tiene en cuenta minutos jugados, titularidades ni lesiones más allá del estado que devuelve la API.
- Antes de pujar o clausular, revisa en la app el saldo, los cupos de cláusulas y la jornada.

## Aviso

Proyecto educativo y personal, **sin relación con Biwenger ni con AS**. Úsalo con tu propia cuenta y de forma responsable: hace unas pocas decenas de consultas por ejecución, con pausas entre ellas. Referencia de endpoints: [pablopb3/biwenger-api](https://github.com/pablopb3/biwenger-api).

## Licencia

[MIT](LICENSE) © 2026 Gonzalo Velasco Toronjo ([@guensovt](https://github.com/guensovt))
