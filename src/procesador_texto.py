import fitz
import re
from transformers import AutoTokenizer
import hashlib

MODELO_EMBEDDINGS = "intfloat/multilingual-e5-large"
tokenizer = AutoTokenizer.from_pretrained(MODELO_EMBEDDINGS)
MAX_TOKENS = 500 

def calcular_hash_pdf(ruta_pdf: str) -> str:
    """Calcula el hash SHA-256 de un archivo físico."""
    hasher = hashlib.sha256()
    with open(ruta_pdf, 'rb') as f:
        # Leemos el archivo en bloques por si es muy grande
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def limpiar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def extraer_bloques_pdf(ruta_pdf: str) -> list:
    """Lee el PDF línea a línea guardando la página y la sección legal a la que pertenece."""
    bloques = []
    seccion_actual = "Introducción / Preámbulo"
    
    patron_seccion = re.compile(
        r"^\s*(Pre[áa]mbulo|Art[íi]culo\s+\d+|Art\.\s*\d+|Disposici[óo]n\s+(?:adicional|transitoria|final|derogatoria|general)\s*(?:primera|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava|novena|d[eé]cima|und[eé]cima|[a-z]+)?|Anexo\s+(?:[IVX]+|\d+|[A-Z]))", 
        re.IGNORECASE
    )

    doc = fitz.open(ruta_pdf)
    for num_pag in range(len(doc)):
        pagina = doc[num_pag]
        texto_pag = pagina.get_text("text")
        lineas = texto_pag.split('\n')
        
        for linea in lineas:
            linea_limpia = limpiar_texto(linea)
            if not linea_limpia:
                continue
                
            match = patron_seccion.match(linea_limpia)
            if match:
                seccion_actual = match.group(1).title().strip() 
                
            bloques.append({
                "texto": linea_limpia,
                "seccion": seccion_actual,
                "pagina": num_pag + 1 
            })
            
    doc.close()
    return bloques

def agrupar_bloques_en_chunks(bloques: list, doc_id: str = "convenio_actual", hash_id: str = None, nif_id: str = None) -> list:
    """Agrupa los bloques aislando cada artículo para mantener la pureza semántica. 
       Aplica Smart Overlap SOLO si el mismo artículo supera el límite de tokens."""
    chunks_finales = []
    chunk_texto = ""
    chunk_tokens = 0
    pag_inicio = None
    pag_fin = None
    seccion_actual_chunk = None  
    lineas_chunk_actual = []
    conteo_secciones = {}

    for bloque in bloques:
        tokens_bloque = len(tokenizer.tokenize(bloque["texto"]))
        
        # Inicializamos la sección si es el primer bloque del chunk
        if not seccion_actual_chunk:
            seccion_actual_chunk = bloque["seccion"]
            
        # CONDICIONES PARA CERRAR EL CHUNK ACTUAL:
        # 1. Nos pasamos del límite de tokens
        exceso_tokens = (chunk_tokens + tokens_bloque > MAX_TOKENS)
        # 2. Entramos en un artículo nuevo
        cambio_de_seccion = (bloque["seccion"] != seccion_actual_chunk)
        if (exceso_tokens or cambio_de_seccion) and chunk_tokens > 0:
            # Actualizamos el contador de la sección que vamos a guardar
            seccion = seccion_actual_chunk
            conteo_secciones[seccion] = conteo_secciones.get(seccion, 0) + 1
            
            # 1. Guardamos el chunk actual
            chunks_finales.append({
                "chunk_id": f"{doc_id}__c{len(chunks_finales):03d}",
                "texto_chunk": chunk_texto.strip(),
                "pagina_inicio": pag_inicio,
                "pagina_fin": pag_fin,
                "seccion_legal": seccion_actual_chunk, 
                "n_tokens": chunk_tokens,
                "orden_chunk": conteo_secciones[seccion],
                "hash_id": hash_id, 
                "nif_id": nif_id    
            })
            
            # 2. Inicializamos el siguiente chunk
            if exceso_tokens and not cambio_de_seccion:
                # Cogemos las dos últimas líneas para que haya contexto.
                lineas_solape = lineas_chunk_actual[-2:] if len(lineas_chunk_actual) > 1 else lineas_chunk_actual[-1:]
                texto_solape = " ".join(lineas_solape)
                chunk_texto = texto_solape + " "
                chunk_tokens = len(tokenizer.tokenize(texto_solape))
                # La página inicio es la página fin del chunk anterior
                pag_inicio = pag_fin
                seccion_actual_chunk = bloque["seccion"]
                lineas_chunk_actual = lineas_solape.copy()
            else:
                # NO HAY SOLAPAMIENTO. (Cambiamos de artículo, empezamos 100% limpios)
                chunk_texto = ""
                chunk_tokens = 0
                pag_inicio = bloque["pagina"]
                seccion_actual_chunk = bloque["seccion"]
                lineas_chunk_actual = []
        
        # 3. Añadimos el bloque actual
        if not pag_inicio:
            pag_inicio = bloque["pagina"]
        pag_fin = bloque["pagina"]
        
        chunk_texto += bloque["texto"] + " "
        chunk_tokens += tokens_bloque
        lineas_chunk_actual.append(bloque["texto"])
            
    # Guardar el último chunk al salir del bucle
    if chunk_texto.strip():
        seccion = seccion_actual_chunk
        conteo_secciones[seccion] = conteo_secciones.get(seccion, 0) + 1
        
        chunks_finales.append({
            "chunk_id": f"{doc_id}__c{len(chunks_finales):03d}",
            "texto_chunk": chunk_texto.strip(),
            "pagina_inicio": pag_inicio,
            "pagina_fin": pag_fin,
            "seccion_legal": seccion_actual_chunk,
            "n_tokens": chunk_tokens,
            "orden_chunk": conteo_secciones[seccion],
            "hash_id": hash_id, 
            "nif_id": nif_id  
        })
        
    return chunks_finales

def procesar_pdf(ruta_pdf: str, doc_id: str = "convenio_actual", nif_id: str = None) -> list:
    print(f"[Procesador] 1. Extrayendo la estructura legal y calculando Hash...")
    hash_documento = calcular_hash_pdf(ruta_pdf)
    bloques = extraer_bloques_pdf(ruta_pdf)
    
    print(f"[Procesador] 2. Segmentando con pureza semántica y Smart Overlap...")
    chunks = agrupar_bloques_en_chunks(bloques, doc_id, hash_id=hash_documento, nif_id=nif_id)
    
    print(f"[Procesador] 3. Proceso terminado. Se han generado {len(chunks)} chunks.")
    return chunks