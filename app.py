import streamlit as st
import os
import tempfile
import re
from dotenv import load_dotenv
from audio_recorder_streamlit import audio_recorder
import json
from datetime import datetime

load_dotenv()
from src.rag_agent import RagAgent
from src.bd_opensearch import GestorOpenSearch
from src.procesador_texto import procesar_pdf
from src.rag_agent import RagAgent
from src.scraping_convenios import (
    descargar_convenio_por_cif,
    NoHayResultadosNifError,
    NoHayConvenioEnNaturalezaError
)

# --- MOCK DE TELEMETRÍA (RLHF / LLMOps) ---
def guardar_telemetria_local(pregunta, contexto, respuesta, intencion="N/A"):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "pregunta": pregunta,
        "intencion_detectada": intencion,
        "contexto_usado": contexto,
        "respuesta_llm": respuesta,
        "feedback_usuario": None # Preparado para Fase 2 (Botones UI)
    }
    with open("telemetria.json", "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
# ------------------------------------------

# 1. CONFIGURACIÓN INICIAL DE LA PÁGINA
st.set_page_config(page_title="Asistente Legal RAG", page_icon="⚖️", layout="wide")
st.title("⚖️ Asistente Jurídico de Convenios Colectivos")

# 2. INICIALIZAR SISTEMA Y MEMORIA
@st.cache_resource
def iniciar_servicios():
    agente = RagAgent()
    db = GestorOpenSearch()
    db.inicializar_indice()
    return agente, db

agente_rag, base_datos = iniciar_servicios()

if "convenio_cargado" not in st.session_state:
    st.session_state.convenio_cargado = False

# Memoria para los mensajes del chat
if "mensajes" not in st.session_state:
    st.session_state.mensajes = [
        {
            "rol": "assistant", 
            "contenido": "**¡Hola! Soy tu asistente jurídico experto en derecho laboral.**\n\nPara empezar, necesito saber qué convenio aplicarte. Tienes dos opciones:\n1. Escribe en el chat el **NIF de tu empresa** para que lo busque y descargue automáticamente.\n2. Sube directamente tu convenio en formato **PDF** usando el menú de la izquierda."
        }
    ]

# 3. BARRA LATERAL: SUBIDA MANUAL DE PDF Y AJUSTES
with st.sidebar:
    st.header("📄 Carga Manual")
    archivo_subido = st.file_uploader("Si no sabes el NIF, sube el PDF aquí:", type=["pdf"])
    
    if st.button("Procesar PDF") and archivo_subido:
        with st.spinner("Leyendo y procesando el documento. Por favor, espere..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(archivo_subido.getvalue())
                ruta_temporal = tmp_file.name
            
            chunks = procesar_pdf(ruta_temporal, doc_id="convenio_usuario", nif_id=None)
            base_datos.indexar_chunks(chunks, doc_id="convenio_usuario")
            
            st.session_state.pdf_path_actual = str(ruta_temporal)
            st.session_state.convenio_cargado = True
            
            st.session_state.mensajes.append({
                "rol": "assistant", 
                "contenido": """El convenio está listo. Puedes realizar 4 tipos de consultas..."""
            })
            st.rerun() 
            
    # BOTÓN DE DESCARGA DEL PDF (LÓGICA ROBUSTA)
    if st.session_state.get("convenio_cargado") and st.session_state.get("pdf_path_actual"):
        ruta_archivo = str(st.session_state.get("pdf_path_actual"))
        
        st.markdown("---")
        if os.path.exists(ruta_archivo):
            with open(ruta_archivo, "rb") as f:
                bytes_pdf = f.read()
            
            st.download_button(
                label="📄 Descargar Convenio PDF",
                data=bytes_pdf,
                file_name=os.path.basename(ruta_archivo),
                mime="application/pdf",
                use_container_width=True
            )
        else:
            
            st.error(f"PDF no encontrado en disco: {ruta_archivo}")

    st.markdown("---")
    st.header("Búsqueda por Voz")
    audio_bytes = audio_recorder(
        text="Pulsa el micro para hablar:", 
        recording_color="#e83e8c", 
        neutral_color="#6c757d", 
        key="grabadora_voz"
    )
    
    st.markdown("---")
    st.header("Ajustes")
    if st.button("Limpiar chat y cambiar convenio", use_container_width=True):
        st.session_state.mensajes = [
            {
                "rol": "assistant", 
                "contenido": "**¡Hola! Soy tu asistente jurídico experto en derecho laboral.**\n\nPara empezar, necesito saber qué convenio aplicarte..."
            }
        ]
        st.session_state.convenio_cargado = False
        st.session_state.pdf_path_actual = None 
        st.rerun()

# 4. INTERFAZ PRINCIPAL DE CHAT Y LÓGICA
# 1. Mostrar el historial de mensajes Y los botones
for i, mensaje in enumerate(st.session_state.mensajes):
    with st.chat_message(mensaje["rol"]):
        st.markdown(mensaje["contenido"])
        
        # Si el mensaje es del Bot, añadimos el audio y el feedback
        if mensaje["rol"] == "assistant":
            col1, col2 = st.columns([1, 5])
            
            with col1:
                if st.button("Escuchar", key=f"btn_audio_{i}"):
                    with st.spinner("Generando audio..."):
                        ruta_mp3 = tempfile.mktemp(suffix=".mp3")
                        agente_rag.generar_audio_sincrono(mensaje["contenido"], ruta_mp3)
                    st.audio(ruta_mp3, format="audio/mp3", autoplay=True) 
            
            with col2:
                # MÉTRICAS LLMOPS: Solo a partir del primer mensaje real (no en el saludo)
                if i > 0:
                    feedback = st.feedback("thumbs", key=f"fb_{i}")
                    
                    if feedback is not None:
                        estado_voto = "POSITIVO (1)" if feedback == 1 else "NEGATIVO (0)"
                        # Log simulando la inserción en Base de Datos
                        print("\n" + "="*50)
                        print(f"[LLMOps METRICS] Inyección a DynamoDB (Simulada)")
                        print(f"-> Voto: {estado_voto}")
                        print(f"-> Input Usuario: {st.session_state.mensajes[i-1]['contenido']}")
                        print(f"-> Respuesta RAG: {mensaje['contenido'][:150]}...")
                        print("="*50 + "\n")

# 2. Capturar entrada del usuario (Texto o Voz)
pregunta_texto = st.chat_input("Escribe tu duda o el NIF de tu empresa...")
pregunta_audio = None

# Si el usuario ha grabado un audio
if audio_bytes:
    # Evitamos procesar el mismo audio dos veces si la página se recarga
    if "ultimo_audio" not in st.session_state or st.session_state.ultimo_audio != audio_bytes:
        st.session_state.ultimo_audio = audio_bytes
        
        # Filtro de seguridad: Evitar "clicks" vacíos o grabaciones de 1 segundo
        if len(audio_bytes) > 5000: 
            with st.spinner("Escuchando y transcribiendo audio..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_audio:
                    tmp_audio.write(audio_bytes)
                    ruta_audio = tmp_audio.name
                
                # Llamamos a Groq (Whisper)
                pregunta_audio = agente_rag.transcribir_audio(ruta_audio)
                os.remove(ruta_audio)
                
                # Chivato visual si Groq falla o devuelve vacío
                if not pregunta_audio:
                    st.error("Error en el procesamiento de audio.")
        else:
            st.warning("Error en el procesamiento de audio.")

# Unificamos ambas entradas (prioridad al texto si se usan a la vez)
pregunta_usuario = pregunta_texto or pregunta_audio

if pregunta_usuario:
    # Mostrar y guardar el mensaje del usuario
    st.session_state.mensajes.append({"rol": "user", "contenido": pregunta_usuario})
    with st.chat_message("user"):
        st.markdown(pregunta_usuario)

    # Lógica del Bot
    with st.chat_message("assistant"):
        
        # CASO A: AÚN NO TENEMOS CONVENIO (Buscamos NIF o lanzamos Guardarraíl)
        if not st.session_state.convenio_cargado:
            # Expresión regular para detectar un NIF en el texto del usuario
            patron_cif = re.compile(r'\b[A-HJ-NP-SUVW]\d{7}[0-9A-J]\b', re.IGNORECASE)
            cif_encontrado = patron_cif.search(pregunta_usuario)
            
            if cif_encontrado:
                cif = cif_encontrado.group().upper()
                with st.spinner(f"Detectado NIF {cif}. Buscando en registros oficiales..."):
                    try:
                        # 1. Descargamos usando tu módulo de scraping
                        ruta_pdf = descargar_convenio_por_cif(cif)
                        
                        # 2. Procesamos e indexamos
                        st.info("Convenio encontrado. Leyendo y procesando el documento...")
                        chunks = procesar_pdf(ruta_pdf, doc_id=f"convenio_{cif}")
                        base_datos.indexar_chunks(chunks, doc_id=f"convenio_{cif}")
                        
                        # 3. Éxito
                        st.session_state.convenio_cargado = True
                        st.session_state.pdf_path_actual = str(ruta_pdf)
                        respuesta = """El convenio está listo. Puedes realizar 4 tipos de consultas:
                        * **Caso Personal:** Plantea tu situación particular y te daré una solución guiada.
                        * **Resumen Temático:** Pide información general o los puntos clave de un tema.
                        * **Definición:** Pregunta por el significado exacto de un concepto o término.
                        * **Extracción Literal:** Solicita la transcripción exacta de un artículo concreto."""
                        st.markdown(respuesta)
                        
                    except NoHayResultadosNifError:
                        respuesta = f"**Error:** No he encontrado ninguna empresa en los registros con el NIF {cif}. Asegúrate de que esté bien escrito."
                        st.error(respuesta)
                    except NoHayConvenioEnNaturalezaError:
                        respuesta = f"**Aviso:** He encontrado la empresa ({cif}), pero no tiene un convenio propio publicado en los registros. Por favor, sube tu convenio en PDF en el menú lateral."
                        st.warning(respuesta)
                    except Exception as e:
                        respuesta = f"**Error inesperado** al intentar descargar el convenio: {str(e)}"
                        st.error(respuesta)
            
            else:
                # Si no hay NIF y no hay convenio, salta el LLM guardarraíl
                with st.spinner("Pensando..."):
                    respuesta = agente_rag.respuesta_guardarrail(pregunta_usuario)
                    st.markdown(respuesta)
                    guardar_telemetria_local(pregunta_usuario, "N/A - Bloqueo sin convenio", respuesta, "Guardarraíl")
        # CASO B: YA TENEMOS CONVENIO (Flujo RAG Normal)
        else:
            with st.spinner("Buscando respuesta en el convenio..."):
                intencion = agente_rag.clasificar_intencion(pregunta_usuario)
                
                es_muy_largo = False
                
                if intencion == 4:
                    # Usamos tu función especializada de Regex y extracción secuencial
                    resultados_bd, es_muy_largo = base_datos.buscar_cita_literal(pregunta_usuario, top_k=3)
                else:
                    # Para consultas generales, búsqueda vectorial tradicional
                    top_k = 5 if intencion == 2 else 3
                    resultados_bd = base_datos.buscar_similitud(pregunta_usuario, top_k=top_k)
                
                prompt = agente_rag.construir_prompt_ruta(intencion, resultados_bd, es_muy_largo)
                respuesta = agente_rag.generar_respuesta_llm(prompt, pregunta_usuario)
                
                st.markdown(respuesta)
                guardar_telemetria_local(pregunta_usuario, resultados_bd, respuesta, intencion)
        # Guardamos la respuesta del bot en la memoria
        st.session_state.mensajes.append({"rol": "assistant", "contenido": respuesta})
        # Forzamos la recarga de la interfaz para que el bucle superior dibuje los botones
        st.rerun()