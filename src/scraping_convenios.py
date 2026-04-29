from __future__ import annotations

import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options

from webdriver_manager.chrome import ChromeDriverManager

 
# URL de extraccion por nif
URL_AYUDA_POR_NIF = "https://expinterweb.mites.gob.es/regcon/pub/ayudaPorNif"

#RUTA ABSOLUTA
RUTA_DESCARGAS_ABSOLUTA = r"C:\Users\USUARIO\Desktop\TFM\TFM Convenios\convenios\descargas_rpa"

#Pudiera ser que la empresa no tenga convenio propio.

class NoHayResultadosNifError(Exception):
    """No hay filas en la tabla de resultados para ese CIF/NIF."""
    pass

class NoHayConvenioEnNaturalezaError(Exception):
    """Hay filas, pero ninguna corresponde a 'convenio colectivo' (suele ser convenio sectorial)."""
    pass

# XPATHS 

# Xpath: input del NIF.
XP_INPUT_NIF = "//*[@id='nif']"

# Xpath: botón “Siguiente”
XP_BOTON_SIGUIENTE = (
    "//button[contains(normalize-space(.),'Siguiente')]"
    " | //input[contains(@value,'Siguiente')]"
    " | //a[contains(normalize-space(.),'Siguiente')]"
)

# Xpath: tabla de resultados por NIF
XP_TABLA_RESULTADOS = "//table[.//th[contains(.,'Naturaleza')] and .//th[contains(.,'Acciones')]]"

# Xpath: botón “Ver Trámites” dentro de una fila.
XP_VER_TRAMITES_EN_FILA = ".//input[starts-with(@name,'_verTramites')]"

# Fallback: primer elemento clicable dentro de la última celda (Acciones).
XP_FALLBACK_ACCIONES_PRIMER_CLIC = "./td[last()]//input[1] | ./td[last()]//a[1] | ./td[last()]//button[1]"

# Xpath: tabla de trámites (cualquier tabla que tenga un “Ver”).
XP_TABLA_TRAMITES = "//table[.//a[normalize-space()='Ver'] or .//button[normalize-space()='Ver'] or .//input[@value='Ver']]"

# Xpath: botón/enlace “Ver” dentro de una fila de trámites.
XP_VER_EN_FILA = ".//a[normalize-space()='Ver'] | .//button[normalize-space()='Ver'] | .//input[@value='Ver']"

# Xpath: botón real de descarga (según tu captura):
# <button type="submit" name="_descargarDocPublicacion..." ...> <img title="Descargar Documento de Publicación"> ...
XP_DESCARGAR = (
    "//button[starts-with(@name,'_descargarDocPublicacion') "
    "or contains(@title,'Descargar') "
    "or .//img[contains(@title,'Descargar') or contains(@alt,'Descargar')]]"
    " | //input[starts-with(@name,'_descargarDocPublicacion')]"
)

# LOG
def log(mensaje: str) -> None:
    """Imprime logs con flush para ver el progreso en tiempo real."""
    print(f"[SCRAPER] {mensaje}", flush=True)


# NORMALIZACIÓN / MATCH
def normalizar_texto(s: str) -> str:
    """Minúsculas, quita tildes/diacríticos, colapsa espacios."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s

def contiene_convenio_colectivo(s: str) -> bool:
    """True si el texto contiene 'convenio colectivo' (no igualdad exacta)."""
    return "convenio colectivo" in normalizar_texto(s)

def fecha_a_num(fecha: str) -> int:
    """dd/mm/yyyy o dd-mm-yyyy -> yyyymmdd para comparar (si falla, -1)."""
    m = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})", (fecha or ""))
    if not m:
        return -1
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return yyyy * 10000 + mm * 100 + dd


# SELENIUM: DRIVER / PAUSAS
def crear_driver_chrome_visible(carpeta_descargas: str, dejar_abierto_en_error: bool = False):
    """
    Crea la instancia de Chrome. Ahora configurado en modo Headless (invisible)
    con permisos especiales para descargar PDFs sin preguntar.
    """
    # 1. Preparamos la carpeta de descargas
    from pathlib import Path
    carpeta = Path(carpeta_descargas).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    
    # 2. Configuramos el modo invisible (Headless)
    opciones = Options()
    opciones.add_argument("--headless=new") # Esto hace la magia de ocultarlo
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")

    # 3. Forzamos la descarga automática sin cuadros de diálogo
    prefs = {
        "download.default_directory": str(carpeta), 
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True 
    }
    opciones.add_experimental_option("prefs", prefs)

    # 4. Lanzamos Chrome con todas las opciones
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()), 
        options=opciones
    )
    
    return driver, carpeta

def pausar_si_hay_verificacion(driver: webdriver.Chrome) -> None:
    """Pausa si detecta verificación humana/captcha."""
    html = (driver.page_source or "").lower()
    if ("what code is in the image" in html) or ("captcha" in html) or ("recaptcha" in html) or ("hcaptcha" in html):
        log("⚠️ Verificación/CAPTCHA detectada. Resuélvela manualmente en Chrome.")
        input("Pulsa ENTER cuando la hayas completado para continuar... ")

# TABLAS: UTILIDADES
def obtener_indice_columna(tabla, posibles_cabeceras: List[str]) -> Optional[int]:
    """Encuentra índice de columna buscando coincidencias en el texto de cabecera (<th>)."""
    ths = tabla.find_elements(By.XPATH, ".//th")
    if not ths:
        return None

    posibles = [normalizar_texto(x) for x in posibles_cabeceras]
    for i, th in enumerate(ths):
        txt = normalizar_texto(th.text)
        for p in posibles:
            if p and (p in txt or txt in p):
                return i
    return None

def seleccionar_fila_mas_reciente_con_convenio(filas, idx_texto: Optional[int], idx_fecha: Optional[int]):
    """Elige fila cuya columna (o texto de fila) contenga 'convenio colectivo' y fecha más reciente."""
    mejor_fila = None
    mejor_fecha = -1

    for f in filas:
        tds = f.find_elements(By.XPATH, "./td")
        texto = (tds[idx_texto].text if idx_texto is not None and idx_texto < len(tds) else f.text) or ""
        if not contiene_convenio_colectivo(texto):
            continue

        fecha_txt = (tds[idx_fecha].text if idx_fecha is not None and idx_fecha < len(tds) else "") or ""
        n = fecha_a_num(fecha_txt)

        if n > mejor_fecha:
            mejor_fecha = n
            mejor_fila = f

    return mejor_fila


# DESCARGA
def esperar_descarga_nueva(carpeta: Path, antes: set[Path], timeout: int = 90) -> Path:
    """Espera a que aparezca un archivo nuevo (no .crdownload) en la carpeta de descargas."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        ahora = set(carpeta.glob("*"))
        nuevos = [p for p in (ahora - antes) if p.is_file() and not p.name.endswith(".crdownload")]
        if nuevos:
            return max(nuevos, key=lambda p: p.stat().st_mtime)
        time.sleep(0.3)
    raise TimeoutException("No se detectó descarga finalizada a tiempo.")

def fallback_descargar_por_url_actual(driver: webdriver.Chrome, carpeta: Path) -> Path:
    """Descarga por requests usando cookies del navegador (si el botón abre visor/pestaña)."""
    url = driver.current_url
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    r = requests.get(url, cookies=cookies, stream=True, timeout=60)
    r.raise_for_status()

    ruta = carpeta / "convenio_fallback.pdf"
    with open(ruta, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    return ruta

def descargar_pdf_por_enlace_directo(driver: webdriver.Chrome, carpeta: Path) -> Optional[Path]:
    """
    En la pantalla de detalle, a veces existe un <a href="...pdf"> (BOCM directo).
    Si existe, lo descargamos directamente (más fiable que clicar botones).
    """
    enlaces = driver.find_elements(By.XPATH, "//a[contains(translate(@href,'PDF','pdf'),'.pdf')]")
    if not enlaces:
        return None

    href = enlaces[0].get_attribute("href")
    if not href:
        return None

    nombre = os.path.basename(urlparse(href).path) or "convenio.pdf"
    ruta = carpeta / nombre

    r = requests.get(href, stream=True, timeout=60)
    r.raise_for_status()
    with open(ruta, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
    return ruta


# CLICK ROBUSTO: “VER TRÁMITES”
def click_ver_tramites_en_fila(fila) -> None:
    """
    Click en el botón de 'Ver Trámites' dentro de la fila:
    - En tu HTML real es <input type='submit' name='_verTramites...'>
    """
    candidatos = fila.find_elements(By.XPATH, XP_VER_TRAMITES_EN_FILA)
    if candidatos:
        candidatos[0].click()
        return

    # Fallback: primer elemento clicable en Acciones
    fila.find_element(By.XPATH, XP_FALLBACK_ACCIONES_PRIMER_CLIC).click()


# FUNCIÓN PRINCIPAL
def scrapear_convenio_por_nif(nif_cif: str,
                              carpeta_descargas: str = RUTA_DESCARGAS_ABSOLUTA,
                              cerrar_chrome_al_final: bool = True) -> Path:
    """
    Ejecuta TODO el flujo NIF:
    resultados -> ver trámites -> ver -> descargar PDF
    y guarda SIEMPRE en carpeta_descargas (ruta absoluta del proyecto).
    """
    driver, carpeta = crear_driver_chrome_visible(
        carpeta_descargas=carpeta_descargas,
        dejar_abierto_en_error=not cerrar_chrome_al_final
    )
    wait = WebDriverWait(driver, 25)

    try:
        # 1) Abrir y meter NIF
        log("1) Abrir ayudaPorNif")
        driver.get(URL_AYUDA_POR_NIF)
        time.sleep(0.6)
        pausar_si_hay_verificacion(driver)

        log("2) Escribir NIF y pulsar Siguiente")
        inp = wait.until(EC.presence_of_element_located((By.ID, "nif")))
        inp.clear()
        inp.send_keys(nif_cif)

        wait.until(EC.element_to_be_clickable((By.XPATH, XP_BOTON_SIGUIENTE))).click()
        time.sleep(1.0)
        pausar_si_hay_verificacion(driver)

        # 2) Tabla resultados: convenio colectivo más reciente
        log("3) Tabla resultados: seleccionar NATURALEZA contiene 'convenio colectivo' + FECHA más reciente")
        tabla_res = wait.until(EC.presence_of_element_located((By.XPATH, XP_TABLA_RESULTADOS)))
        filas_res = tabla_res.find_elements(By.XPATH, ".//tr[td]")
        if not filas_res:
            raise NoHayResultadosNifError(
                f"No hay filas en la tabla de resultados por NIF/CIF: {nif_cif}"
            )

        idx_nat = obtener_indice_columna(tabla_res, ["Naturaleza"])
        idx_fecha = obtener_indice_columna(tabla_res, ["Fecha", "Inscripción", "Publicación", "Inscripcion", "Publicacion"])

        fila_convenio = seleccionar_fila_mas_reciente_con_convenio(filas_res, idx_nat, idx_fecha)
        if fila_convenio is None:
            raise NoHayConvenioEnNaturalezaError(
                f"No hay 'convenio colectivo' en Naturaleza para NIF/CIF: {nif_cif}"
            )

        # 3) Click en “Ver Trámites”
        log("4) Click 'Ver Trámites' en la fila seleccionada")
        click_ver_tramites_en_fila(fila_convenio)

        time.sleep(1.0)
        pausar_si_hay_verificacion(driver)

        # 4) Tabla trámites: convenio colectivo más reciente
        log("5) Tabla trámites: seleccionar fila que contiene 'convenio colectivo' + FECHA más reciente")
        tabla_tram = wait.until(EC.presence_of_element_located((By.XPATH, XP_TABLA_TRAMITES)))
        filas_tram = tabla_tram.find_elements(By.XPATH, ".//tr[td]")
        if not filas_tram:
            raise RuntimeError("No hay filas en la tabla de trámites.")

        idx_tipo = obtener_indice_columna(tabla_tram, ["Tipo de Trámite", "Tipo de Tramite", "Tipo"])
        idx_fecha2 = obtener_indice_columna(tabla_tram, ["Fecha", "Inscripción", "Publicación", "Inscripcion", "Publicacion"])

        fila_tramite = seleccionar_fila_mas_reciente_con_convenio(filas_tram, idx_tipo, idx_fecha2)
        if fila_tramite is None:
            # fallback: buscar por texto completo
            for f in filas_tram:
                if contiene_convenio_colectivo(f.text or ""):
                    fila_tramite = f
                    break

        if fila_tramite is None:
            raise RuntimeError("No se encontró ningún trámite que contenga 'convenio colectivo'.")

        # 5) Click “Ver”
        log("6) Click 'Ver' en el trámite seleccionado")
        fila_tramite.find_element(By.XPATH, XP_VER_EN_FILA).click()

        time.sleep(1.0)
        pausar_si_hay_verificacion(driver)

        # 6) Detalle: Descargar
        log("7) Detalle: descargar PDF en la ruta del proyecto")

        # 7.1) Mejor opción: enlace directo a PDF (BOCM)
        pdf = descargar_pdf_por_enlace_directo(driver, carpeta)
        if pdf:
            log(f"PDF descargado por enlace directo: {pdf}")
            return pdf

        # 7.2) Si no hay enlace PDF, intentamos clicar el botón de descargar
        antes = set(carpeta.glob("*"))
        try:
            wait.until(EC.element_to_be_clickable((By.XPATH, XP_DESCARGAR))).click()
        except Exception:
            log("⚠️ No pude clicar 'Descargar' con XP_DESCARGAR. Fallback por URL actual con cookies.")
            pdf = fallback_descargar_por_url_actual(driver, carpeta)
            log(f"✅ PDF descargado (fallback): {pdf}")
            return pdf

        try:
            pdf = esperar_descarga_nueva(carpeta, antes, timeout=90)
            log(f"✅ PDF descargado: {pdf}")
            return pdf
        except TimeoutException:
            log("⚠️ No apareció descarga en carpeta. Fallback por URL actual con cookies.")
            pdf = fallback_descargar_por_url_actual(driver, carpeta)
            log(f"✅ PDF descargado (fallback): {pdf}")
            return pdf

    finally:
        if cerrar_chrome_al_final:
            log("Cerrando navegador...")
            try:
                driver.quit()
            except Exception:
                pass
        else:
            log("Dejando Chrome abierto (modo depuración).")


def descargar_convenio_por_cif(cif: str, cerrar_chrome_al_final: bool = True):
    """
    Es la función “oficial” que usará el chatbot.
    Llama a scrapear_convenio_por_nif() con:
          * nif_cif = cif (viene del chatbot)
          * carpeta_descargas = RUTA_DESCARGAS_ABSOLUTA
          * cerrar_chrome_al_final = True (por defecto)
    Qué devuelve:
      - La ruta del PDF (Path) que devuelve tu scraper.
    """
    cif = (cif or "").strip()
    return scrapear_convenio_por_nif(
        nif_cif=cif,
        carpeta_descargas=RUTA_DESCARGAS_ABSOLUTA,
        cerrar_chrome_al_final=cerrar_chrome_al_final
    )

