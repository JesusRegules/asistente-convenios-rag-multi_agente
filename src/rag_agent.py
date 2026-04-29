import re
import requests
import os
import edge_tts
import asyncio
import os

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
URL_API_GROQ = "https://api.groq.com/openai/v1/chat/completions"
MODELO_ENRUTADOR = "llama-3.1-8b-instant"  
MODELO_GENERADOR = "llama-3.3-70b-versatile" 

class RagAgent:
    def __init__(self):
        """Inicializa el agente de IA que se comunicará con los LLMs."""
        self.headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

    def respuesta_guardarrail(self, mensaje_usuario):
        """Genera una respuesta restrictiva si el usuario habla antes de cargar un convenio."""
        payload = {
            "model": MODELO_ENRUTADOR,
            "messages": [
                {
                    "role": "system", 
                    "content": "Eres un asistente jurídico estricto especializado en convenios colectivos. "
                    "Tu única misión es pedir al usuario que introduzca el NIF de la empresa en la que trabaja o suba su convenio colectivo en PDF. "
                    "SIEMPRE insistirás en que proporcionen el NIF de la empresa en la que trabajan o que suba el pdf con su convenio colectivo. "
                    "Si el usuario saluda, devuélvele el saludo. "
                    "Si el usuario te pide recetas, chistes, código o cualquier otra cosa, niégate educadamente explicando que solo eres un asistente de derecho laboral."
                },
                {"role": "user", "content": mensaje_usuario}
            ],
            "temperature": 0.2
        }
        try:
            respuesta = requests.post(URL_API_GROQ, headers=self.headers, json=payload)
            respuesta.raise_for_status()
            return respuesta.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Error de conexión con Groq: {str(e)}"

    def clasificar_intencion(self, pregunta):
        """Usa el LLM más rápido para decidir a qué ruta pertenece la pregunta (Few-Shot Prompting)"""
        prompt_clasificador = f"""Eres un enrutador semántico experto en textos legales. Tu ÚNICA tarea es clasificar la pregunta del usuario en UNA de estas 4 categorías:
        1: Caso personal, problema específico o cálculo aplicado a una situación real del usuario.
        2: Resumen temático, enumeración o síntesis general de varios puntos de un tema.
        3: Definición teórica de un concepto jurídico o laboral aislado.
        4: Búsqueda literal, copia, cita o extracción de un artículo/texto exacto.
        
        A continuación, te muestro ejemplos de cómo debes clasificar (Few-Shot Examples):
        Pregunta: "Llevo 5 años en la empresa y quiero pedir una excedencia para cuidar a mi hijo, ¿puedo hacerlo?"
        Respuesta: 1
        Pregunta: "Me caso la semana que viene, ¿cuántos días me corresponden y cómo afectan a mi sueldo?"
        Respuesta: 1
        Pregunta: "Hazme un esquema con los beneficios sociales y ayudas que da la empresa."
        Respuesta: 2
        Pregunta: "Resume los tipos de faltas muy graves y sus posibles sanciones."
        Respuesta: 2
        Pregunta: "¿Qué significa exactamente el plus de nocturnidad?"
        Respuesta: 3
        Pregunta: "Define el concepto de trabajo a turnos según el convenio."
        Respuesta: 3
        Pregunta: "Copia literalmente lo que dice el artículo 14 sobre las horas extras."
        Respuesta: 4
        Pregunta: "Dime el texto exacto del artículo que habla sobre el ámbito territorial."
        Respuesta: 4
        
        AHORA ES TU TURNO. Responde ÚNICAMENTE con el número (1, 2, 3 o 4) para la siguiente pregunta.
        Pregunta: "{pregunta}"
        Respuesta:"""
        
        payload = {
            "model": MODELO_ENRUTADOR,
            "messages": [{"role": "user", "content": prompt_clasificador}],
            "temperature": 0.0 # Máximo determinismo
        }
        try:
            response = requests.post(URL_API_GROQ, headers=self.headers, json=payload).json()
            respuesta = response["choices"][0]["message"]["content"].strip()
            numero = re.search(r'[1-4]', respuesta)
            if numero:
                return int(numero.group(0))
            return 1 
        except Exception as e:
            print(f"Error en el enrutador: {e}")
            return 1

    def construir_prompt_ruta(self, intencion, resultados_bd,es_muy_largo=False):
        """Crea el súper-prompt (System Prompt) dependiendo de la ruta elegida y el contexto de la BD."""
        contexto = ""
        for i, res in enumerate(resultados_bd):
            contexto += f"\n--- {res['seccion_legal']} (Páginas {res['pagina_inicio']}-{res['pagina_fin']}) ---\n{res['texto']}\n"

        if intencion == 1:
            instruccion = """Actúa como un abogado laboralista experto. 
            El usuario te plantea una situación personal o caso práctico. 
            Tu objetivo es analizar la situación paso a paso y darle una respuesta resolutiva basándote ÚNICAMENTE en el convenio proporcionado. 
            Usa un tono profesional, claro y empático. 
            Estructura tu respuesta usando formato Markdown (negritas, saltos de línea). 
            Al final de tu respuesta o en cada punto clave, debes citar obligatoriamente la página del convenio de donde extraes la información (ej. [Pág. X])."""
        
        elif intencion == 2:
            instruccion = """Actúa como un analista de Recursos Humanos. 
            El usuario te pide información general o un resumen sobre un tema específico del convenio. 
            Tu objetivo es sintetizar la información de los fragmentos recuperados. 
            ESTRUCTURA OBLIGATORIA: 
            - Usa un breve párrafo introductorio. 
            - Utiliza una lista con viñetas (bullet points) para enumerar los aspectos clave. 
            - Cita las páginas de referencia al final de cada punto (ej. [Pág. X]).
            No incluyas opiniones personales ni divagues, solo la información del texto."""
        
        elif intencion == 3:
            instruccion = instruccion = """Actúa como un diccionario jurídico laboral. 
            El usuario te pide definir un concepto específico. 
            Tu objetivo es dar una definición directa, concisa y exacta basada ÚNICAMENTE en el texto del convenio. 
            No des explicaciones largas ni ejemplos que no estén en el texto. 
            Formato obligatorio: 
            **[Concepto]**: [Definición exacta]. 
            *Fuente: [Sección/Artículo], Página X.*"""
        
        else:
            instruccion = """Actúa como un asistente documental riguroso. 
            El usuario quiere saber exactamente qué dice un artículo, anexo o sección concreta del convenio. 
            Tu objetivo es extraer y reproducir fielmente la información solicitada basándote ÚNICAMENTE en el contexto proporcionado. 
            Usa comillas para citar textualmente si es pertinente. 
            REGLA CRÍTICA DE CORTE: Si observas que el texto del artículo en los fragmentos recuperados no está completo o se corta abruptamente a mitad de una frase, debes transcribir hasta donde llegue y añadir obligatoriamente al final, en un párrafo nuevo, la siguiente frase:
            "No puedo proporcionar respuestas con más texto que el mostrado."
            Indica siempre la página exacta (ej. [Pág. X]). 
            No des tu opinión ni interpretes la norma, limítate a exponer lo que dice el texto."""

        prompt_final = f"""{instruccion}
        
        REGLA DE ORO DE SEGURIDAD (ANTI-ALUCINACIONES): Si la información necesaria no está en los fragmentos, di ESTRICTAMENTE: "El convenio no especifica información detallada sobre esta consulta concreta."

        DOCUMENTO LEGAL (Contexto recuperado de la Base de Datos):
        {contexto}
        """
        
        return prompt_final

    def generar_respuesta_llm(self, prompt_sistema, pregunta_usuario):
        """Llama al LLM potente para generar la respuesta final al usuario basándose en el contexto."""
        payload = {
            "model": MODELO_GENERADOR,
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": pregunta_usuario}
            ],
            "temperature": 0.2 
        }
        try:
            response = requests.post(URL_API_GROQ, headers=self.headers, json=payload).json()
            return response["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Error de conexión con Groq: {str(e)}"
    
    def transcribir_audio(self, ruta_audio):
        """Envía un archivo de audio a Groq (Whisper) y devuelve el texto."""
        url = "https://api.groq.com/openai/v1/audio/transcriptions"

        headers = {"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"} 
        
        try:
            with open(ruta_audio, "rb") as file:
                files = {"file": ("audio.wav", file, "audio/wav")}
                data = {"model": "whisper-large-v3", "language": "es"}
                respuesta = requests.post(url, headers=headers, files=files, data=data).json()
                return respuesta.get("text", "")
        except Exception as e:
            print(f"Error en transcripción: {e}")
            return ""

    def generar_audio_sincrono(self, texto, ruta_salida):
        """Convierte texto a voz usando edge_tts. 
        Lo envolvemos en asyncio.run() para que funcione de forma síncrona en Streamlit."""
        texto_limpio = texto.replace("*", "").replace("#", "")
        async def _generar():
            communicate = edge_tts.Communicate(texto_limpio, "es-ES-AlvaroNeural")
            await communicate.save(ruta_salida)
            
        asyncio.run(_generar())