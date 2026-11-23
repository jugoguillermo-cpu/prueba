import asyncio
import re
import os
import requests
import nest_asyncio
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from github import Github, Auth
from github import GithubException

# Habilitar soporte para bucles anidados en Jupyter/Anaconda
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

# Páginas "fuente" -> NOMBRE FIJO del canal
FUENTES_DEPORTES = {
    "https://tvlibreonline.st/en-vivo/espn-premium/": "ESPN PREMIUM",
    "https://tvlibreonline.st/en-vivo/tyc-sports/": "TYC SPORTS",
    "https://tvlibreonline.st/en-vivo/dsports/": "DSPORTS",
    "https://tvlibreonline.st/en-vivo/fox-sports/": "FOX SPORTS",
    "https://tvlibreonline.st/en-vivo/directv-sports/": "DIRECTV SPORTS",
}

SELECTOR_BOTONES_OPCIONES = "a.btn-md"
ESPERA_TRAS_CLICK_SEGUNDOS = 2

# NUEVO: búsquedas internas de la fuente
PAT_BUSQUEDA = re.compile(r"(buscar|search|/buscar/|/search/|[?&]s=|[?&]q=)", re.I)
PAT_IGNORAR = re.compile(r"^(#|javascript:|mailto:|tel:|data:)", re.I)
PAT_PC = re.compile(r"\bpc\b|extensi", re.I)
MAX_BUSQUEDAS = 25
PROFUNDIDAD_BUSQUEDA = 1

URLS_M3U_EXTERNAS = [
    "https://iptv-org.github.io/iptv/regions/southam.m3u?fbclid=IwdGRjcAUdOZNjbGNrBR05eXBkb2YFZXh0bgNhZW0CMTEAc3J0YwZhcHBfaWQMMzUwNjg1NTMxNzI4AAEe8N84jKI0NhBNdeY1BGjzh_9cCP8VR4R_tJ59KrgjdVaMzJ9DZ642tSPADz8_aem_hNReBY1EZ79qC6NeFVID6g",
    "https://telechancho.github.io/telechancho-iptv/telechancho-infinity.m3u",
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
# 1.b NOMBRES
# ==========================================
def slug_nombre(url):
    path = urlparse(url).path.rstrip("/")
    if not path:
        return ""
    last = re.sub(r"\.(html?|php|aspx?)$", "", path.split("/")[-1], flags=re.I)
    return re.sub(r"[-_]+", " ", last).strip().title()[:60]


def nombre_opcion(texto, n):
    texto = re.sub(r"\s+", " ", (texto or "")).strip()
    if not texto or len(texto) < 2:
        return f"Opción {n}"
    if not re.search(r"opci[oó]n", texto, re.I) and len(texto) > 40:
        return f"Opción {n} - {texto[:40]}"
    return texto[:70]


async def recolectar_busquedas(page, base, vistos):
    """Links de búsqueda de la página, ya nombrados."""
    out, host = [], urlparse(base).netloc
    try:
        els = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: (e.innerText||'').trim()}))")
    except Exception:
        return out
    for a in els:
        href = (a.get("href") or "").split("#")[0]
        if not href or PAT_IGNORAR.search(href) or href in vistos:
            continue
        texto = re.sub(r"\s+", " ", a.get("text", "")).strip()
        es_busqueda = (PAT_BUSQUEDA.search(href)
                       or (urlparse(href).netloc == host and PAT_BUSQUEDA.search(texto)))
        if not es_busqueda:
            continue
        vistos.add(href)
        out.append({"nombre": f"Buscar: {texto or slug_nombre(href) or 'resultado'}"[:70],
                    "href": href})
        if len(out) >= MAX_BUSQUEDAS:
            break
    return out


# ==========================================
# 2. FASE 0: BUSCAR LINKS DE OPCIONES POR CANAL
# ==========================================

async def buscar_opciones_canal(page, nombre_canal, url_fuente,
                                profundidad=PROFUNDIDAD_BUSQUEDA, vistos=None):
    opciones = {}
    vistos = vistos if vistos is not None else {url_fuente.split("#")[0]}
    enlaces_vistos = set()

    try:
        print(f"[*] [{nombre_canal}] Abriendo fuente: {url_fuente}")
        await page.goto(url_fuente, wait_until="load", timeout=30000)

        botones = await page.locator(SELECTOR_BOTONES_OPCIONES).all()
        cantidad_opciones = len(botones)
        textos = [await b.inner_text() for b in botones]
        print(f"[*] [{nombre_canal}] Se encontraron {cantidad_opciones} opciones en la fuente.")

        # NUEVO: links de búsqueda de esta página
        busquedas = await recolectar_busquedas(page, url_fuente, vistos) if profundidad > 0 else []
        if busquedas:
            print(f"[*] [{nombre_canal}] {len(busquedas)} link(s) de búsqueda: "
                  f"{[b['nombre'] for b in busquedas]}")

        for i in range(cantidad_opciones):
            nombre = nombre_opcion(textos[i] if i < len(textos) else "", i + 1)
            if PAT_PC.search(nombre):
                print(f"[-] [{nombre_canal}] {nombre}: la salteo (requiere extensión)")
                continue

            botones_actuales = await page.locator(SELECTOR_BOTONES_OPCIONES).all()
            if i >= len(botones_actuales):
                break
            boton = botones_actuales[i]

            try:
                print(f"[*] [{nombre_canal}] Cliqueando {nombre}...")
                await boton.evaluate("el => el.click()")
            except Exception as e:
                print(f"[!] [{nombre_canal}] No se pudo cliquear {nombre}: {e}")
                continue

            await asyncio.sleep(ESPERA_TRAS_CLICK_SEGUNDOS)

            # si el click navegó, ese es el reproductor
            if page.url.split("#")[0] != url_fuente.split("#")[0]:
                src = page.url
                if src not in enlaces_vistos:
                    enlaces_vistos.add(src)
                    clave = f"{nombre_canal} {nombre}"
                    opciones[clave] = src
                    print(f"[+] [{nombre_canal}] Enlace encontrado: {src}")
                await page.goto(url_fuente, wait_until="load", timeout=30000)
                await asyncio.sleep(1)
                continue

            iframes = await page.locator("iframe").all()
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src and src not in enlaces_vistos and "chatbro" not in src:
                    enlaces_vistos.add(src)
                    clave = f"{nombre_canal} {nombre}"
                    opciones[clave] = src
                    print(f"[+] [{nombre_canal}] Enlace encontrado: {src}")

    except Exception as e:
        print(f"[!] [{nombre_canal}] Error buscando opciones en la fuente: {e}")

    # NUEVO: profundizar en búsquedas
    for b in busquedas:
        print(f"[*] [{nombre_canal}] -> entrando a {b['nombre']}: {b['href']}")
        try:
            sub = await buscar_opciones_canal(page, nombre_canal, b["href"],
                                              profundidad - 1, vistos)
        except Exception as e:
            print(f"[!] [{nombre_canal}] {b['nombre']}: {e}")
            continue
        for sub_nombre, src in sub.items():
            etiqueta = sub_nombre.replace(f"{nombre_canal} ", f"{nombre_canal} {b['nombre']} / ", 1)
            opciones[etiqueta] = src

    return opciones


async def buscar_todas_las_opciones(fuentes):
    if not fuentes:
        return {}
    if isinstance(fuentes, (list, tuple)):
        fuentes = {u: (slug_nombre(u) or "CANAL").upper() for u in fuentes}

    print(f"--- FASE 0: Buscando links de opciones para {len(fuentes)} canal(es) fuente ---")
    todas_opciones = {}
    vistos = set()

    async with async_playwright() as p:
        argumentos_lanzamiento = dict(
            headless=EN_GITHUB_ACTIONS,
            args=[
                "--window-position=2000,2000",
                "--window-size=400,300",
                "--mute-audio"
            ]
        )
        if not EN_GITHUB_ACTIONS:
            argumentos_lanzamiento["channel"] = "chrome"
        browser = await p.chromium.launch(**argumentos_lanzamiento)

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        for nombre_canal, url_fuente in fuentes.items():
            page = await context.new_page()
            try:
                opciones = await buscar_opciones_canal(page, nombre_canal, url_fuente,
                                                       PROFUNDIDAD_BUSQUEDA, vistos)
                for clave, src in opciones.items():
                    n = 2
                    k = clave
                    while k in todas_opciones:
                        k = f"{clave} {n}"
                        n += 1
                    todas_opciones[k] = src
            finally:
                await page.close()

        await context.close()
        await browser.close()

    print(f"[***] FASE 0 completa: {len(todas_opciones)} links de opciones encontrados en total. [***]")
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
                    print(f"[>] [{nombre_canal}] [Intento {intento}] Botón detectado ({selector}). Haciendo clic...")
                    await elemento.click(timeout=2000)
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue


async def interceptar_red(response, nombre_canal, stream_encontrado_event,
                          resultados_m3u, vistos_streams):
    url = response.url
    if stream_encontrado_event.is_set() or url in vistos_streams:
        return

    if STREAM_REGEX.search(url):
        print(f"\n[+] [¡{nombre_canal} DETECTADO!]: {url}\n")
        stream_encontrado_event.set()
        vistos_streams.add(url)

        request_headers = response.request.headers
        referer = request_headers.get('referer', '')
        origin = request_headers.get('origin', '')
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

        headers_pipe = []
        if referer:
            headers_pipe.append(f"Referer={referer}")
        headers_pipe.append(f"User-Agent={user_agent}")
        if origin:
            headers_pipe.append(f"Origin={origin}")
        url_con_headers = f"{url}|{'&'.join(headers_pipe)}" if headers_pipe else url

        entrada_m3u = f"#EXTINF:-1,{nombre_canal}\n"
        entrada_m3u += f"#EXTVLCOPT:http-user-agent={user_agent}\n"
        if referer:
            entrada_m3u += f"#EXTVLCOPT:http-referrer={referer}\n"
        if origin:
            entrada_m3u += f"#EXTVLCOPT:http-origin={origin}\n"
        entrada_m3u += f"{url_con_headers}\n"

        resultados_m3u[nombre_canal] = entrada_m3u


def cargar_m3u_existente(ruta_archivo):
    canales_viejos = {}
    if not os.path.exists(ruta_archivo):
        return canales_viejos

    print(f"[*] Cargando lista existente desde {ruta_archivo} para preservar canales...")
    with open(ruta_archivo, "r", encoding="utf-8") as f:
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
    print(f"--- FASE 1: Escaneando {len(diccionario_canales)} canales de deportes con Playwright ---")
    canales_finales = cargar_m3u_existente(ruta_archivo_salida)
    nuevos_resultados = {}
    vistos_streams = set()

    async with async_playwright() as p:
        argumentos_lanzamiento = dict(
            headless=EN_GITHUB_ACTIONS,
            args=[
                "--window-position=2000,2000",
                "--window-size=400,300",
                "--mute-audio"
            ]
        )
        if not EN_GITHUB_ACTIONS:
            argumentos_lanzamiento["channel"] = "chrome"
        browser = await p.chromium.launch(**argumentos_lanzamiento)

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        for nombre, url_objetivo in diccionario_canales.items():
            print("\n" + "=" * 60)
            print(f"[*] ESCANEANDO EN CHROME OCULTO: {nombre}")
            print(f"[*] Origen: {url_objetivo}")
            print("=" * 60 + "\n")

            page = await context.new_page()
            stream_encontrado_event = asyncio.Event()

            page.on("response", lambda res, n=nombre, ev=stream_encontrado_event: asyncio.create_task(
                interceptar_red(res, n, ev, nuevos_resultados, vistos_streams)
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
                    print(f"[+] Éxito con {nombre}. Enlace actualizado.")
                    canales_finales[nombre] = nuevos_resultados[nombre]
                    await asyncio.sleep(1)
                else:
                    print(f"[!] No se capturó flujo nuevo para '{nombre}'.")
                    if nombre in canales_finales:
                        print(f"[->] RESPALDO: Se conserva el stream anterior de '{nombre}'.")

            except Exception as e:
                print(f"[!] Error en canal {nombre}: {e}")
                if nombre in canales_finales:
                    print(f"[->] RESPALDO por Error: Conservando versión anterior de '{nombre}'.")
            finally:
                await page.close()

        await context.close()
        await browser.close()

    if canales_finales:
        with open(ruta_archivo_salida, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for bloque_canal in canales_finales.values():
                f.write(bloque_canal)
        print(f"\n[***] M3U ACTUALIZADO: {ruta_archivo_salida} (Total: {len(canales_finales)} canales) [***]")
    else:
        print("\n[!] No hay canales disponibles para escribir.")


# ==========================================
# 4. FASE 2: PROCESAR, CATEGORIZAR Y UNIFICAR
# ==========================================

def normalizar_texto(texto):
    if not texto:
        return ""
    remplazos = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
                 'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U'}
    for orig, dest in remplazos.items():
        texto = texto.replace(orig, dest)
    return texto.upper()


def procesar_y_categorizar(contenido):
    lista_canales = []
    lineas = contenido.splitlines()
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if linea.startswith("#EXTINF:"):
            info_canal = linea
            url_canal = ""

            extras = []
            next_idx = i + 1
            while next_idx < len(lineas) and lineas[next_idx].startswith("#"):
                if not lineas[next_idx].startswith("#EXTM3U"):
                    extras.append(lineas[next_idx].strip())
                next_idx += 1

            if next_idx < len(lineas):
                url_canal = lineas[next_idx].strip()

            texto_busqueda = normalizar_texto(f"{info_canal} {url_canal}")

            if "MAR DEL PLATA" in texto_busqueda or "MARDELPLATA" in texto_busqueda:
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

            bloque = [nueva_linea]
            bloque.extend(extras)
            if url_canal:
                bloque.append(url_canal)
            bloque_texto = "\n".join(bloque) + "\n\n"

            lista_canales.append((nombre_canal, bloque_texto))
            i = next_idx
        i += 1
    return lista_canales


def unificar_todo():
    print("\n--- FASE 2: Unificando, Categorizando y Ordenando A-Z ---")
    ruta_final = os.path.join(CARPETA_LOCAL, ARCHIVO_FINAL_UNIFICADO)
    canales_totales = []

    try:
        ruta_temp_scraper = os.path.join(CARPETA_LOCAL, ARCHIVO_SCRAPER_TEMPORAL)
        if os.path.exists(ruta_temp_scraper):
            print("📦 Procesando canales de deportes...")
            with open(ruta_temp_scraper, 'r', encoding='utf-8') as f:
                canales_totales.extend(procesar_y_categorizar(f.read()))

        for url in URLS_M3U_EXTERNAS:
            print(f"🌐 Descargando externo: {url}")
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    canales_totales.extend(procesar_y_categorizar(r.text))
            except Exception as e:
                print(f"⚠️ Error en URL {url}: {e}")

        print("🔤 Ordenando canales alfabéticamente (A-Z)...")
        canales_totales.sort(key=lambda x: x[0].upper())

        with open(ruta_final, 'w', encoding='utf-8') as outfile:
            outfile.write("#EXTM3U\n\n")
            for _, bloque_canal in canales_totales:
                outfile.write(bloque_canal)

        print(f"✅ Unificación y ordenamiento completados. Total canales: {len(canales_totales)}")
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
            print("❌ Error GitHub: falta GITHUB_TOKEN (no está seteado en el entorno/Secret).")
            return

        auth = Auth.Token(token)
        g = Github(auth=auth)

        try:
            repo = g.get_repo(repo_nombre_completo)
        except GithubException as e:
            print(f"❌ Error GitHub: no se pudo acceder al repo '{repo_nombre_completo}' (status {e.status}): {e.data}")
            return

        with open(archivo_local_path, 'r', encoding='utf-8') as f:
            contenido_nuevo = f.read()

        try:
            contents = repo.get_contents(ruta_en_repo)
            if contents.decoded_content.decode('utf-8') != contenido_nuevo:
                repo.update_file(contents.path, "Update IPTV List", contenido_nuevo, contents.sha)
                print("🚀 GitHub actualizado con éxito.")
            else:
                print("ℹ️ El contenido es idéntico, no se requiere subida.")
        except GithubException as e:
            if e.status == 404:
                repo.create_file(ruta_en_repo, "Initial IPTV List", contenido_nuevo)
                print("🚀 Archivo creado en GitHub por primera vez.")
            else:
                print(f"❌ Error GitHub al leer/crear el archivo (status {e.status}): {e.data}")
    except Exception as e:
        print(f"❌ Error GitHub inesperado: {type(e).__name__}: {e}")


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
