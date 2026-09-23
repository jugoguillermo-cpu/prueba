import asyncio
import os
import re
from playwright.async_api import async_playwright

# ==========================================
# CONFIGURACIÓN GENERAL
# ==========================================
URL_BASE = "https://tvlibreonline.st/"  # Cambiar por tu sitio base
CARPETA_SALIDA = "m3u_capturados"
ARCHIVO_TXT_RESPALDO = os.path.join(CARPETA_SALIDA, "capturas_m3u8.txt")
ARCHIVO_M3U_FINAL = os.path.join(CARPETA_SALIDA, "lista_canales.m3u")

# Selectores DOM
SELECTOR_BOTONES_OPCIONES = "a.btn-md"

# Palabras clave para descartar iFrames publicitarios o de chat
IGNORAR_IFRAMES = [
    "sharethis.com", "chatbro.com", "facebook.com", 
    "google.com", "analytics", "disqus.com"
]

# Canales a procesar (Nombre: URL de la página del canal)
CANALES_A_PROCESAR = {
    "ESPN PREMIUM": "https://tvlibreonline.st/en-vivo/espn-premium/",
    "TNT SPORTS": "https://tvlibreonline.st/en-vivo/tnt-sports/"
}

os.makedirs(CARPETA_SALIDA, exist_ok=True)

# ==========================================
# FASE 0: EXTRACCIÓN DE IFRAMES VÁLIDOS
# ==========================================
async def buscar_opciones_canal(page, nombre_canal, url_fuente):
    opciones = {}
    enlaces_vistos = set()
    try:
        print(f"\n[*] [{nombre_canal}] Abriendo fuente: {url_fuente}")
        await page.goto(url_fuente, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        botones = await page.locator(SELECTOR_BOTONES_OPCIONES).all()
        cantidad_opciones = len(botones)
        print(f"[*] [{nombre_canal}] Se encontraron {cantidad_opciones} opciones en la fuente.")

        for i in range(cantidad_opciones):
            botones_actuales = await page.locator(SELECTOR_BOTONES_OPCIONES).all()
            if i >= len(botones_actuales):
                break
            boton = botones_actuales[i]

            try:
                print(f"[*] [{nombre_canal}] Clic en opción {i + 1}...")
                await boton.evaluate("el => el.click()")
            except Exception as e:
                print(f"[!] [{nombre_canal}] No se pudo cliquear la opción {i + 1}: {e}")
                continue

            # Esperar a que el JS del sitio actualice el iFrame interno
            await asyncio.sleep(3)

            iframes = await page.locator("iframe").all()
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src and src not in enlaces_vistos:
                    # Filtro de seguridad contra publicidad
                    if any(ignorar in src.lower() for ignorar in IGNORAR_IFRAMES):
                        continue
                    
                    enlaces_vistos.add(src)
                    nombre_opcion = f"{nombre_canal} (OPCION {len(enlaces_vistos)})"
                    opciones[nombre_opcion] = src
                    print(f"[+] [{nombre_canal}] iFrame válido detectado: {src}")

    except Exception as e:
        print(f"[!] [{nombre_canal}] Error buscando opciones: {e}")

    return opciones


# ==========================================
# FASE 1: CAPTURA DE NETWORK Y HEADERS
# ==========================================
async def capturar_stream_con_headers(browser, nombre_opcion, url_iframe):
    captura = None

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    async def interceptar_peticion(request):
        nonlocal captura
        url = request.url
        if (".m3u8" in url or ".mpd" in url) and not captura:
            headers = request.headers
            referer = headers.get("referer", url_iframe)
            user_agent = headers.get("user-agent", "")
            
            captura = {
                "url": url,
                "referer": referer,
                "user_agent": user_agent
            }
            print(f"\n[!!!] ENLACE CAPTURADO [{nombre_opcion}]")
            print(f"      URL: {url}")
            print(f"      Referer: {referer}\n")

    page.on("request", interceptar_peticion)

    try:
        # Pasa el Referer de la página padre al abrir el iFrame
        await page.goto(url_iframe, referer=URL_BASE, wait_until="domcontentloaded", timeout=25000)
        
        # Esperar hasta 12 segundos para que el reproductor ejecute el stream
        for _ in range(12):
            if captura:
                break
            await asyncio.sleep(1)

    except Exception as e:
        print(f"[!] Timeout o error cargando {nombre_opcion}: {e}")

    await context.close()
    return captura


# ==========================================
# GUARDADO DE RESULTADOS (TXT Y M3U)
# ==========================================
def guardar_en_txt(nombre_opcion, captura):
    with open(ARCHIVO_TXT_RESPALDO, "a", encoding="utf-8") as f:
        f.write(f"=== {nombre_opcion} ===\n")
        f.write(f"URL: {captura['url']}\n")
        f.write(f"REFERER: {captura['referer']}\n")
        f.write(f"USER-AGENT: {captura['user_agent']}\n")
        f.write(f"OPCION KODI/IPTV: {captura['url']}|Referer={captura['referer']}&User-Agent={captura['user_agent']}\n")
        f.write("-" * 60 + "\n\n")

def guardar_en_m3u(resultados):
    with open(ARCHIVO_M3U_FINAL, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n\n")
        for res in resultados:
            nombre = res["nombre"]
            cap = res["captura"]
            
            # Formato estándar de directivas KODI / TiviMate / IPTV Smarters
            f.write(f'#EXTINF:-1 tvg-name="{nombre}", {nombre}\n')
            f.write(f'#EXTVLCOPT:http-referrer={cap["referer"]}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={cap["user_agent"]}\n')
            # Formato de tubería inline (pipe format)
            f.write(f'{cap["url"]}|Referer={cap["referer"]}&User-Agent={cap["user_agent"]}\n\n')

# ==========================================
# FLUJO PRINCIPAL
# ==========================================
async def main():
    # Limpiar archivo TXT previo si existe
    if os.path.exists(ARCHIVO_TXT_RESPALDO):
        os.remove(ARCHIVO_TXT_RESPALDO)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page_fase0 = await browser.new_page()

        todas_las_opciones = {}
        
        # 1. Ejecutar Fase 0
        for canal, url in CANALES_A_PROCESAR.items():
            opciones = await buscar_opciones_canal(page_fase0, canal, url)
            todas_las_opciones.update(opciones)

        await page_fase0.close()

        # 2. Ejecutar Fase 1
        print(f"\n[Fase 1] Iniciando escaneo de {len(todas_las_opciones)} iFrames capturados...")
        resultados_finales = []

        for nombre_opcion, url_iframe in todas_las_opciones.items():
            print(f"[*] Analizando stream para: {nombre_opcion}")
            captura = await capturar_stream_con_headers(browser, nombre_opcion, url_iframe)
            
            if captura:
                guardar_en_txt(nombre_opcion, captura)
                resultados_finales.append({
                    "nombre": nombre_opcion,
                    "captura": captura
                })

        await browser.close()

        # 3. Generar M3U final
        if resultados_finales:
            guardar_en_m3u(resultados_finales)
            print(f"\n[✔] Proceso completado exitosamente:")
            print(f"    - TXT Respaldo: {ARCHIVO_TXT_RESPALDO}")
            print(f"    - Lista M3U: {ARCHIVO_M3U_FINAL}")
        else:
            print("\n[!] No se pudieron capturar enlaces válidos.")

if __name__ == "__main__":
    asyncio.run(main())
