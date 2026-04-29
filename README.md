# Sistema RAG Híbrido Multimodal para Asistencia Jurídica en Convenios Colectivos

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg) ![AWS](https://img.shields.io/badge/AWS-Cloud_Architecture-FF9900.svg) ![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B.svg) ![OpenSearch](https://img.shields.io/badge/OpenSearch-Vector_DB-005EB8.svg) ![Selenium](https://img.shields.io/badge/Selenium-RPA-43B02A.svg)

![Demostración de la aplicación](image/demo.gif)

## Descripción del Proyecto
Este proyecto es el resultado de un Trabajo de Fin de Máster (TFM) especializado en Inteligencia Artificial. Consiste en una arquitectura avanzada de Generación Aumentada por Recuperación (RAG) diseñada para el dominio LegalTech. El sistema actúa como un asistente virtual capaz de procesar, indexar y consultar convenios colectivos españoles, ofreciendo respuestas fundamentadas, extracción literal de articulado y soporte multimodal (voz y texto).

### Flujo General del Sistema 
![Diagrama de Flujo General](image/diagrama_flujo.png)

## Características Principales
* **Extracción automatizada (RPA):** Integración de un proceso headless mediante Selenium que navega por el portal gubernamental REGCON para descargar convenios colectivos en tiempo real a partir del NIF de una empresa.
* **Procesamiento PDF inteligente:** Implementación de algoritmos de chunking semántico que respetan la estructura legal del documento (títulos, capítulos, artículos) para preservar el contexto durante la vectorización.
* **Búsqueda híbrida:** Combinación de búsqueda densa (embeddings multilingües) y búsqueda dispersa (BM25) utilizando OpenSearch.
* **Multimodalidad:** Soporte de interacción por voz integrando modelos Speech-to-Text (Whisper) y Text-to-Speech (Edge-TTS) para la vocalización de respuestas.

## Ingeniería de IA y LLMOps
El sistema está diseñado pensando en el ciclo de vida del modelo de lenguaje (LLMOps) y su mantenimiento a largo plazo:

* **Enrutamiento de modelos (Model Routing):** Se utiliza un modelo rápido y de bajo coste (`Llama-3.1-8b-instant`) como guardarraíl inicial para validar la intención del usuario. Solo para la extracción compleja y razonamiento jurídico se invoca un modelo de gran capacidad (`Llama-3.3-70b-versatile`).
* **Caché cruzada computacional O(1):** El sistema inyecta en OpenSearch el identificador fiscal (`nif_id`) y la huella digital del archivo (`hash_id` vía SHA-256). Esto permite verificar la existencia previa del vectorizado en tiempo constante, saltando la fase de ingesta para convenios recurrentes.
* **Telemetría y preparación para RLHF:** El sistema traza el contexto recuperado, el prompt exacto y la respuesta generada. En la implementación actual (MVP), estos logs se persisten de forma asíncrona mediante un *Mock local* (`telemetria.json`), sentando las bases de la arquitectura objetivo que inyectará estos datos en Amazon DynamoDB para futuros procesos de *Reinforcement Learning from Human Feedback*.
* **Monitorización y Evaluación Continua (Drift):** Dado que en un entorno conversacional no existe un *Ground Truth* estático, la arquitectura contempla el uso de métricas sin referencia (*Reference-free metrics*) como RAGAS. Se utilizará un patrón *LLM-as-a-Judge* sobre los logs de telemetría para monitorizar la Fidelidad (*Faithfulness*), la Relevancia de la Respuesta y la Precisión del Contexto, evitando la degradación del sistema.

## Arquitectura del Software Desacoplada
El código fuente sigue el principio de responsabilidad única, dividiendo el sistema en los siguientes módulos dentro de la carpeta `src/`:
* `app.py`: Controlador principal, interfaz de Streamlit y gestión de estados de sesión.
* `scraping_convenios.py`: Módulo RPA tolerante a fallos para descargas oficiales.
* `procesador_texto.py`: Pipeline de limpieza, extracción de texto (PyMuPDF) y hashing.
* `bd_opensearch.py`: Cliente de conexión y lógica del clúster vectorial.
* `rag_agent.py`: Orquestador de ingeniería de prompts y cliente de la API de Groq.

## Arquitectura Cloud y Decisiones de Diseño (AWS)
El diseño de despliegue sigue los pilares del **AWS Well-Architected Framework**, garantizando el aislamiento de datos sensibles requerido en el sector LegalTech.

### Opción A: Despliegue Híbrido (MVP Cost-Optimized)
![Arquitectura AWS Híbrida](image/diagrama_hibrido.png)
Diseñada para un lanzamiento ágil. La aplicación corre aislada en Amazon ECS (Fargate) en una subred privada. El tráfico sale controladamente por un NAT Gateway exclusivo para ejecutar el RPA y consultar la API de inferencia, abaratando costes iniciales.

### Opción B: Evolución Enterprise (100% AWS Native)
![Arquitectura AWS Nativa](image/diagrama_nativo.png)
Arquitectura objetivo. Se reemplazan las APIs externas por **Amazon Bedrock** conectado mediante AWS PrivateLink (VPC Interface Endpoints). Esto garantiza que el tráfico de Inteligencia Artificial nunca abandone la red interna de AWS, asegurando un *Compliance* absoluto.

### Comparativa de Arquitecturas
| Característica | Opción A (Híbrida) | Opción B (Nativa) |
| :--- | :--- | :--- |
| **Inferencia LLM** | Groq API (Externa) | Amazon Bedrock (Interna) |
| **Privacidad de Datos** | Alta (TLS/SSL) | Extrema (VPC PrivateLink) |
| **Coste Operativo** | Bajo (Pago por uso Groq) | Medio/Alto (Bedrock Provisioned) |
| **Escalabilidad** | Alta (ECS Fargate) | Muy Alta (Serverless Native) |

## Requisitos e Instalación Local

### 1. Preparar el entorno
```bash
git clone [URL_DE_TU_REPOSITORIO]
cd [NOMBRE_DEL_DIRECTORIO]
pip install -r requirements.txt
