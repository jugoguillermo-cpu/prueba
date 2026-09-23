import asyncio
import os
from urllib.parse import urljoin
from playwright.async_api import async_playwright

# ==========================================
# CONFIGURACIÓN GENERAL
# ==========================================
URL_BASE = "https://tvlibreonline.st/"
CARPETA_SALIDA = "m3u_capturados"
ARCHIVO_TXT_RESPALDO = os.path.join(CARPETA_SALIDA, "capturas_m3u8.txt")
ARCHIVO_M3U_FINAL = os.path.join(CARPETA_SALIDA, "lista_canales.m3u")

# Selectores DOM
SELECTOR_BOTONES_OPCIONES = "a.btn-md"

# Dominios a ignorar (filtros anti-publicidad / chats)
IGNORAR_IFRAMES = [
    "sharethis.com", "chatbro.com", "facebook.com", 
    "google.com", "analytics", "disqus.com"
]

# Canales a procesar
CANALES_A_PROCESAR = {
    "ESPN PREMIUM": "https://tvlibreonline.st/en-vivo/espn-premium/",
    "TNT SPORTS": "https://tvlibreonline.st/en-vivo/tnt-sports/"
}

os.makedirs(CARPETA_SALIDA, exist_ok=True)

# ==========================================
# FASE 0: EXTRACCIÓN Y NORMALIZACIÓN DE IFRAMES
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
                print(f"[!] [{nombre_canal}] Error al cliquear opción {i + 1}: {e}")
                continue

            await asyncio.sleep(3)

            iframes = await page.locator("iframe").all()
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src:
                    # Corrección de URLs relativas (/html/fl/...) a absolutas (https://...)
                    src_absoluta = urljoin(page.url, src)

                    if src_absoluta and src_absoluta not in enlaces_vistos:
                        if any(ignorar in src_absoluta.lower() for ignorar in IGNORAR_IFRAMES):
                            continue
                        
                        enlaces_vistos.add(src_absoluta)
                        nombre_opcion = f"{nombre_canal} (OPCION {len(enlaces_vistos)})"
                        opciones[nombre_opcion] = src_absoluta
                        print(f"[+] [{nombre_canal}] iFrame válido: {src_absoluta}")

    except Exception as e:
        print(f"[!] [{nombre_canal}] Error en Fase 0: {e}")

    return opciones

# ==========================================
# FASE 1: INTERCEPTACIÓN DE RED Y CABECERAS
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
        await page.goto(url_iframe, referer=URL_BASE, wait_until="domcontentloaded", timeout=25000)
        
        for _ in range(12):
            if captura:
                break
            await asyncio.sleep(1)

    except Exception as e:
        print(f"[!] Error procesando {nombre_opcion}: {e}")

    await context.close()
    return captura

# ==========================================
# ESCRITURA DE ARCHIVOS
# ==========================================
def guardar_en_txt(nombre_opcion, captura):
    with open(ARCHIVO_TXT_RESPALDO, "a", encoding="utf-8") as f:
        f.write(f"=== {nombre_opcion} ===\n")
        f.write(f"URL: {captura['url']}\n")
        f.write(f"REFERER: {captura['referer']}\n")
        f.write(f"USER-AGENT: {captura['user_agent']}\n")
        f.write(f"PIPE FORMAT: {captura['url']}|Referer={captura['referer']}&User-Agent={captura['user_agent']}\n")
        f.write("-" * 60 + "\n\n")

def guardar_en_m3u(resultados):
    with open(ARCHIVO_M3U_FINAL, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n\n")
        for res in resultados:
            nombre = res["nombre"]
            cap = res["captura"]
            
            f.write(f'#EXTINF:-1 tvg-name="{nombre}", {nombre}\n')
            f.write(f'#EXTVLCOPT:http-referrer={cap["referer"]}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={cap["user_agent"]}\n')
            f.write(f'{cap["url"]}|Referer={cap["referer"]}&User-Agent={cap["user_agent"]}\n\n')

# ==========================================
# MAIN
# ==========================================
async def main():
    if os.path.exists(ARCHIVO_TXT_RESPALDO):
        os.remove(ARCHIVO_TXT_RESPALDO)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page_fase0 = await browser.new_page()

        todas_las_opciones = {}
        
        for canal, url in CANALES_A_PROCESAR.items():
            opciones = await buscar_opciones_canal(page_fase0, canal, url)
            todas_las_opciones.update(opciones)

        await page_fase0.close()

        print(f"\n[Fase 1] Escaneando {len(todas_las_opciones)} iFrames...")
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

        if resultados_finales:
            guardar_en_m3u(resultados_finales)
            print(f"\n[✔] Proceso finalizado exitosamente.")

if __name__ == "__main__":
    asyncio.run(main())
