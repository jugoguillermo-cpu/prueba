import asyncio
import os
import re
import requests
import urllib3
import nest_asyncio
from playwright.async_api import async_playwright
from github import Github, Auth
from github import GithubException

# Desactivar advertencias de SSL por certificados autofirmados en fuentes externas
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Habilitar soporte para bucles anidados
nest_asyncio.apply()

# ==========================================
# 1. CONFIGURACIÓN
# ==========================================
EN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = "jugoguillermo-cpu"
GITHUB_REPO_NAME = "prueba"
GITHUB_REPO_COMPLETO = os.environ.get("GITHUB_REPOSITORY", f"{GITHUB_OWNER}/{GITHUB_REPO_NAME}")
NOMBRE_ARCHIVO_GITHUB = "lista.m3u"

CARPETA_LOCAL = "./listas" if EN_GITHUB_ACTIONS else r"C:/Users/gui/Desktop/mis listtas"
ARCHIVO_SCRAPER_TEMPORAL = "canales_extraidos.m3u"
ARCHIVO_FINAL_UNIFICADO = "lista_unificada.m3u"

# CONFIGURACIÓN DE PROXY / VPN (Opcional)
# Si tu VPN/Proxy te da una dirección (ej: "http://127.0.0.1:8080"), ponla aquí. 
# Si dejas en blanco (""), Playwright usará la red predeterminada (si Proton VPN está abierta en tu PC, usará esa IP).
PROXY_SERVER = os.environ.get("PROXY_SERVER", "") 

# Páginas fuente de deportes operativas
FUENTES_DEPORTES = {
    "ESPN PREMIUM": "https://tvlibre.st/en-vivo/espn-premium/",
    "TNT SPORTS": "https://tvlibre.st/en-vivo/tnt-sports/",
    "TYC SPORTS": "https://tvlibre.st/en-vivo/tyc-sports/",
    "FOX SPORTS": "https://tvlibre.st/en-vivo/fox-sports/",
    "ESPN": "https://tvlibre.st/en-vivo/espn/",
}

KEYWORDS_DEPORTES_AR = [
    "ESPN", "TNT SPORTS", "TYC SPORTS", "FOX SPORTS", 
    "DSORTS", "DIRECTV SPORTS", "DXTV", "DEPORTV"
]

SELECTOR_BOTONES_OPCIONES = "a.btn-md"
ESPERA_TRAS_CLICK_SEGUNDOS = 2

URLS_M3U_EXTERNAS = [
    "https://iptv-org.github.io/iptv/regions/southam.m3u",
    "https://telechancho.github.io/telechancho-infinity.m3u",
    "http://45.181.122.46:8090/playlist.m3u8",
    "https://iptv-org.github.io/iptv/countries/ar.m3u",
    "https://www.m3u.cl/lista/AR.m3u",
    "https://radiosargentina.com.ar/TVAR.m3u",
]

PAISES_OBJETIVO = ["ARGENTINA", "CHILE", "BRASIL", "ECUADOR", "URUGUAY"]

STREAM_REGEX = re.compile(r'\.(m3u8|mpd)(\?.*)?$', re.IGNORECASE)

SELECTORES_PLAY = [
    ".vjs-big-play-button",
    ".jw-display-icon-container",
    ".plyr__control--overlaid",
    "button[aria-label='Play']",
    "[class*='play']",
    "[id*='play']",
    "text=Play",
    "text=Reproducir"
]


# ==========================================
# 2. FASE 0: BUSCAR LINKS DE OPCIONES
# ==========================================

async def buscar_opciones_canal(page, nombre_canal, url_fuente):
    opciones = {}
    enlaces_vistos = set()
    try:
        print(f"[*] [{nombre_canal}] Abriendo fuente: {url_fuente}")
        await page.goto(url_fuente, wait_until="load", timeout=30000)
        await asyncio.sleep(2)

        botones_data = await page.evaluate("""() => {
            const botones = document.querySelectorAll("a.btn-md");
            return Array.from(botones).map((btn, index) => {
                return {
                    texto: btn.innerText.trim(),
                    onclick: btn.getAttribute("onclick") || "",
                    href: btn.getAttribute("href") || ""
                };
            });
        }""")

        print(f"[*] [{nombre_canal}] Se encontraron {len(botones_data)} opciones.")

        for i, op in enumerate(botones_data):
            texto_extra = f" ({op['texto']})" if op['texto'] else f" (OPCION {i+1})"
            nombre_opcion = f"{nombre_canal}{texto_extra}"
            
            url_encontrada = None
            match_onclick = re.search(r"['\"](https?://[^'\"]+|/[^'\"]+)['\"]", op["onclick"])
            if match_onclick:
                url_parcial = match_onclick.group(1)
                if url_parcial.startswith("http"):
                    url_encontrada = url_parcial
                else:
                    url_encontrada = f"https://tvlibre.st{url_parcial}"
            elif op["href"] and op["href"] != "#" and not op["href"].startswith("#"):
                url_encontrada = op["href"]

            if url_encontrada and url_encontrada not in enlaces_vistos:
                enlaces_vistos.add(url_encontrada)
                opciones[nombre_opcion] = url_encontrada
                print(f"[+] [{nombre_canal}] Enlace extraído: {url_encontrada}")

    except Exception as e:
        print(f"[!] [{nombre_canal}] Error buscando opciones: {e}")

    return opciones


async def buscar_todas_las_opciones(fuentes):
    if not fuentes:
        return {}

    print(f"--- FASE 0: Buscando links de opciones para {len(fuentes)} canal(es) fuente ---")
    todas_opciones = {}

    async with async_playwright() as p:
        argumentos_lanzamiento = dict(
            headless=EN_GITHUB_ACTIONS,
            args=["--window-position=2000,2000", "--window-size=400,300", "--mute-audio"]
        )
        if PROXY_SERVER:
            argumentos_lanzamiento["proxy"] = {"server": PROXY_SERVER}

        if not EN_GITHUB_ACTIONS:
            argumentos_lanzamiento["channel"] = "chrome"

        browser = await p.chromium.launch(**argumentos_lanzamiento)

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        for nombre_canal, url_fuente in fuentes.items():
            page = await context.new_page()
            try:
                opciones = await buscar_opciones_canal(page, nombre_canal, url_fuente)
                todas_opciones.update(opciones)
            finally:
                await page.close()

        await context.close()
        await browser.close()

    print(f"[***] FASE 0 completa: {len(todas_opciones)} links encontrados. [***]")
    return todas_opciones


# ==========================================
# 3. FASE 1: ESCANEO CON PLAYWRIGHT
# ==========================================

async def intentar_autoclick_play(page, nombre_canal):
    print(f"[*] [{nombre_canal}] Ejecutando secuencia de auto-play...")
    for intento in range(1, 3):
        for selector in SELECTORES_PLAY:
            try:
                elemento = page.locator(selector).first
                if await elemento.is_visible(timeout=1000):
                    print(f"[>] [{nombre_canal}] [Intento {intento}] Botón detectado ({selector}). Clic...")
                    await elemento.click(timeout=2000)
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue


async def interceptar_red(response, nombre_canal, stream_encontrado_event, resultados_m3u):
    url = response.url
    if stream_encontrado_event.is_set():
        return

    if STREAM_REGEX.search(url):
        print(f"\n[+] [¡{nombre_canal} DETECTADO!]: {url}\n")
        stream_encontrado_event.set()

        request_headers = response.request.headers
        referer = request_headers.get('referer', '')
        origin = request_headers.get('origin', '')
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        entrada_m3u = f"#EXTINF:-1 group-title=\"DEPORTES ARGENTINA\",{nombre_canal}\n"
        entrada_m3u += f"#EXTVLCOPT:http-user-agent={user_agent}\n"
        if referer:
            entrada_m3u += f"#EXTVLCOPT:http-referrer={referer}\n"
        if origin:
            entrada_m3u += f"#EXTVLCOPT:http-origin={origin}\n"
        entrada_m3u += f"{url}\n"

        resultados_m3u[nombre_canal] = entrada_m3u


def cargar_m3u_existente(ruta_archivo):
    canales_viejos = {}
    if not os.path.exists(ruta_archivo):
        return canales_viejos

    with open(ruta_archivo, "r", encoding="utf-8", errors='ignore') as f:
        lineas = f.readlines()

    bloque_actual = ""
    nombre_canal_actual = None

    for linea in lineas:
        if linea.startswith("#EXTM3U"):
            continue
        if linea.startswith("#EXTINF"):
            if nombre_canal_actual and bloque_actual:
                canales_viejos[nombre_canal_actual] = bloque_actual
            match = re.search(r',(.+)$', linea)
            nombre_canal_actual = match.group(1).strip() if match else "Canal Desconocido"
            bloque_actual = linea
        elif nombre_canal_actual:
            bloque_actual += linea

    if nombre_canal_actual and bloque_actual:
        canales_viejos[nombre_canal_actual] = bloque_actual

    return canales_viejos


async def escanear_canales_deportes(diccionario_canales, ruta_archivo_salida):
    print(f"--- FASE 1: Escaneando {len(diccionario_canales)} canales con Playwright ---")
    canales_finales = cargar_m3u_existente(ruta_archivo_salida)
    nuevos_resultados = {}

    async with async_playwright() as p:
        argumentos_lanzamiento = dict(
            headless=EN_GITHUB_ACTIONS,
            args=["--window-position=2000,2000", "--window-size=400,300", "--mute-audio"]
        )
        if PROXY_SERVER:
            argumentos_lanzamiento["proxy"] = {"server": PROXY_SERVER}

        if not EN_GITHUB_ACTIONS:
            argumentos_lanzamiento["channel"] = "chrome"

        browser = await p.chromium.launch(**argumentos_lanzamiento)

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        for nombre, url_objetivo in diccionario_canales.items():
            print(f"\n[*] ESCANEANDO: {nombre}")
            page = await context.new_page()
            stream_encontrado_event = asyncio.Event()

            page.on("response", lambda res, n=nombre, ev=stream_encontrado_event: asyncio.create_task(
                interceptar_red(res, n, ev, nuevos_resultados)
            ))

            try:
                await page.goto(url_objetivo, wait_until="load", timeout=45000)
                await asyncio.sleep(2)
                asyncio.create_task(intentar_autoclick_play(page, nombre))

                contador_espera = 0
                while not stream_encontrado_event.is_set() and contador_espera < 20:
                    await asyncio.sleep(1)
                    contador_espera += 1

                if stream_encontrado_event.is_set():
                    canales_finales[nombre] = nuevos_resultados[nombre]
            except Exception as e:
                print(f"[!] Error en {nombre}: {e}")
            finally:
                await page.close()

        await context.close()
        await browser.close()

    if canales_finales:
        with open(ruta_archivo_salida, "w", encoding="utf-8", newline='\n') as f:
            f.write("#EXTM3U\n")
            for bloque_canal in canales_finales.values():
                f.write(bloque_canal)


# ==========================================
# 4. FASE 2: PROCESAR Y UNIFICAR
# ==========================================

def normalizar_texto(texto):
    if not texto:
        return ""
    remplazos = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
                 'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U'}
    for orig, dest in remplazos.items():
        texto = texto.replace(orig, dest)
    return texto.upper()

def es_deporte_argentina(texto):
    texto_norm = normalizar_texto(texto)
    paises_excluidos = ["CHILE", "MEXICO", "PERU", "COLOMBIA", "URUGUAY"]
    if any(p in texto_norm for p in paises_excluidos) and not "ARGENTINA" in texto_norm and not "AR" in texto_norm:
        if "FOX SPORTS MX" in texto_norm or "ESPN CHILE" in texto_norm:
            return False
    return any(kw in texto_norm for kw in KEYWORDS_DEPORTES_AR)

def procesar_y_categorizar(contenido):
    lista_canales = []
    lineas = contenido.splitlines()
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if linea.startswith("#EXTINF:"):
            info_canal = linea
            extras = []
            next_idx = i + 1
            
            while next_idx < len(lineas) and (lineas[next_idx].startswith("#") or not lineas[next_idx].strip()):
                l_sig = lineas[next_idx].strip()
                if l_sig and not l_sig.startswith("#EXTM3U"):
                    extras.append(l_sig)
                next_idx += 1

            url_canal = ""
            if next_idx < len(lineas):
                url_canal = lineas[next_idx].strip()

            texto_busqueda = normalizar_texto(f"{info_canal} {url_canal}")
            
            if es_deporte_argentina(texto_busqueda):
                categoria = "DEPORTES ARGENTINA"
            elif "MAR DEL PLATA" in texto_busqueda or "MARDELPLATA" in texto_busqueda:
                categoria = "MAR DEL PLATA"
            else:
                categoria = "VARIOS"
                for pais in PAISES_OBJETIVO:
                    if pais.upper() in info_canal.upper():
                        categoria = pais
                        break

            if 'group-title="' in info_canal:
                inicio = info_canal.find('group-title="') + 13
                fin = info_canal.find('"', inicio)
                nueva_linea = info_canal[:inicio] + categoria + info_canal[fin:]
            else:
                nueva_linea = info_canal.replace("#EXTINF:-1", f'#EXTINF:-1 group-title="{categoria}"')

            match_nombre = re.search(r',(.+)$', info_canal)
            nombre_canal = match_nombre.group(1).strip() if match_nombre else "Canal Desconocido"

            bloque_partes = [nueva_linea]
            bloque_partes.extend(extras)
            if url_canal:
                bloque_partes.append(url_canal)
            
            bloque_texto = "\n".join(bloque_partes) + "\n"
            lista_canales.append((nombre_canal, bloque_texto, categoria))
            i = next_idx
        i += 1
    return lista_canales


def unificar_todo():
    print("\n--- FASE 2: Unificando, Categorizando y Optimizando ---")
    ruta_final = os.path.join(CARPETA_LOCAL, ARCHIVO_FINAL_UNIFICADO)
    
    deportes_propios = []
    deportes_externos = []
    otros_canales = []

    try:
        ruta_temp_scraper = os.path.join(CARPETA_LOCAL, ARCHIVO_SCRAPER_TEMPORAL)
        if os.path.exists(ruta_temp_scraper):
            with open(ruta_temp_scraper, 'r', encoding='utf-8', errors='ignore') as f:
                resultado_scraper = procesar_y_categorizar(f.read())
                for nombre, bloque, cat in resultado_scraper:
                    deportes_propios.append((nombre, bloque))

        for url in URLS_M3U_EXTERNAS:
            print(f"🌐 Descargando externo: {url}")
            try:
                r = requests.get(url, timeout=15, verify=False)
                if r.status_code == 200:
                    contenido_ext = r.content.decode('utf-8', errors='ignore')
                    canales_externos = procesar_y_categorizar(contenido_ext)
                    for nombre, bloque, cat in canales_externos:
                        if cat == "DEPORTES ARGENTINA":
                            deportes_externos.append((nombre, bloque))
                        elif cat == "MAR DEL PLATA" or cat == "ARGENTINA":
                            otros_canales.append((nombre, bloque))
            except Exception as e:
                print(f"⚠️ Error en URL externa: {e}")

        deportes_propios_ordenados = sorted(deportes_propios, key=lambda x: x[0].upper())
        deportes_externos_ordenados = sorted(deportes_externos, key=lambda x: x[0].upper())
        todos_deportes_arg = deportes_propios_ordenados + deportes_externos_ordenados

        print(f"⚽ Canales en 'DEPORTES ARGENTINA': {len(todos_deportes_arg)}")

        otros_canales.sort(key=lambda x: x[0].upper())

        with open(ruta_final, 'w', encoding='utf-8', newline='\n') as outfile:
            outfile.write("#EXTM3U\n")
            
            for _, bloque in todos_deportes_arg:
                outfile.write(bloque.strip() + "\n\n")

            for _, bloque in otros_canales:
                outfile.write(bloque.strip() + "\n\n")

        print(f"✅ Archivo guardado en: {ruta_final}")
        return True
    except Exception as e:
        print(f"❌ Error en unificación: {e}")
        return False


# ==========================================
# 5. FASE 3: SUBIR A GITHUB
# ==========================================

def subir_a_github(archivo_local_path, repo_nombre_completo, token, ruta_en_repo):
    print(f"\n--- FASE 3: Subiendo a GitHub ---")
    try:
        if not token:
            print("❌ Falta GITHUB_TOKEN.")
            return

        auth = Auth.Token(token)
        g = Github(auth=auth)
        repo = g.get_repo(repo_nombre_completo)

        with open(archivo_local_path, 'r', encoding='utf-8', errors='ignore') as f:
            contenido_nuevo = f.read()

        try:
            contents = repo.get_contents(ruta_en_repo)
            if contents.decoded_content.decode('utf-8') != contenido_nuevo:
                repo.update_file(contents.path, "Update IPTV List (VPN Ready)", contenido_nuevo, contents.sha)
                print("🚀 GitHub actualizado con éxito.")
            else:
                print("ℹ️ El contenido es idéntico.")
        except GithubException as e:
            if e.status == 404:
                repo.create_file(ruta_en_repo, "Initial IPTV List", contenido_nuevo)
                print("🚀 Archivo creado en GitHub.")
    except Exception as e:
        print(f"❌ Error al subir a GitHub: {e}")


# ==========================================
# EJECUCIÓN
# ==========================================

async def main():
    if not os.path.exists(CARPETA_LOCAL):
        os.makedirs(CARPETA_LOCAL)

    canales_a_escanear = await buscar_todas_las_opciones(FUENTES_DEPORTES)
    ruta_temp = os.path.join(CARPETA_LOCAL, ARCHIVO_SCRAPER_TEMPORAL)
    await escanear_canales_deportes(canales_a_escanear, ruta_temp)

    if unificar_todo():
        ruta_final = os.path.join(CARPETA_LOCAL, ARCHIVO_FINAL_UNIFICADO)
        subir_a_github(ruta_final, GITHUB_REPO_COMPLETO, GITHUB_TOKEN, NOMBRE_ARCHIVO_GITHUB)

    print("\n--- ¡PROCESO TERMINADO! ---")


if __name__ == "__main__":
    asyncio.run(main())
