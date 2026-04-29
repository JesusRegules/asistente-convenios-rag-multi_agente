import warnings
from opensearchpy import OpenSearch
from sentence_transformers import SentenceTransformer
import os
warnings.filterwarnings("ignore")

class GestorOpenSearch:
    def __init__(self):
        # 1. Conexión a OpenSearch
        self.host = "localhost"
        self.port = 9200
        self.user = "admin"
        self.password = os.getenv("OPENSEARCH_PASSWORD") 
        self.index_name = "convenios_chunks"
        self.client = OpenSearch(
            hosts=[{"host": self.host, "port": self.port}],
            http_auth=(self.user, self.password),
            use_ssl=True,                
            verify_certs=False,         
            ssl_assert_hostname=False,
            ssl_show_warn=False,
        )
        # 2. Cargar el modelo de Embeddings
        print("Cargando modelo de embeddings (multilingual-e5-large)...")
        self.embedding_model = SentenceTransformer("intfloat/multilingual-e5-large")
        print("Modelo cargado.")

    def inicializar_indice(self):
        """Crea el índice preparado para Búsqueda Híbrida (Vectores + BM25)"""
        if self.client.indices.exists(index=self.index_name):
            self.client.indices.delete(index=self.index_name)
            print(f"🔄 Índice '{self.index_name}' reseteado.")

        index_body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 100
                }
            },
            "mappings": {
                "properties": {
                    "doc_id": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "texto_chunk": {"type": "text"}, 
                    "pagina_inicio": {"type": "keyword"},
                    "pagina_fin": {"type": "keyword"},
                    "seccion_legal": {"type": "keyword"}, 
                    "orden_chunk": {"type": "integer"},   
                    "hash_id": {
                    "type": "keyword"
                    },
                    "nif_id": {
                        "type": "keyword"
                    },
                    "embedding": { 
                        "type": "knn_vector",
                        "dimension": 1024, 
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "faiss"
                        }
                    }
                }
            }
        }
        self.client.indices.create(index=self.index_name, body=index_body)
        print(f"Índice '{self.index_name}' creado.")

    def indexar_chunks(self, lista_chunks, doc_id):
        """Vectoriza y sube los chunks a OpenSearch"""
        print(f"Iniciando indexación de {len(lista_chunks)} chunks...")
        
        for i, chunk in enumerate(lista_chunks):
            texto = chunk["texto_chunk"]
            texto_para_embedding = f"passage: {texto}"
            # Generamos el vector
            vector = self.embedding_model.encode(texto_para_embedding).tolist()
            # Preparamos el documento para OpenSearch
            documento = {
                "doc_id": doc_id,
                "chunk_id": chunk["chunk_id"],
                "texto_chunk": texto, # Guardamos el texto original (sin "passage:")
                "pagina_inicio": chunk["pagina_inicio"],
                "pagina_fin": chunk["pagina_fin"],
                "seccion_legal": chunk["seccion_legal"],
                "orden_chunk": chunk.get("orden_chunk", 1),
                "embedding": vector
            }
            # Insertamos en OpenSearch
            self.client.index(index=self.index_name, body=documento, id=chunk["chunk_id"])
            
            if (i + 1) % 50 == 0:
                print(f"   -> Indexados {i + 1}/{len(lista_chunks)}...")
                
        print("¡Indexación completada con éxito!")

    def buscar_similitud(self, pregunta_usuario, top_k=4):
        """Busca en OpenSearch los chunks más relevantes para la pregunta del usuario"""
        print(f"Buscando en BD: '{pregunta_usuario}'")
        texto_busqueda = f"query: {pregunta_usuario}"
        # 1. Convertimos la pregunta del usuario en un vector matemático
        vector_pregunta = self.embedding_model.encode(texto_busqueda).tolist()
        # 2. Preparamos la consulta k-NN para OpenSearch
        consulta_knn = {
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": vector_pregunta,
                        "k": top_k
                    }
                }
            },
            "_source": ["texto_chunk", "pagina_inicio", "pagina_fin", "seccion_legal"] # Solo pedimos estos datos
        }
        
        # 3. Ejecutar la búsqueda
        respuesta = self.client.search(index=self.index_name, body=consulta_knn)
        # 4. Formatear los resultados para pasárselos al LLM
        resultados = []
        for hit in respuesta["hits"]["hits"]:
            score = hit["_score"]
            fuente = hit["_source"]
            
            resultados.append({
                "texto": fuente["texto_chunk"],
                "pagina_inicio": fuente["pagina_inicio"],
                "pagina_fin": fuente["pagina_fin"],
                "seccion_legal": fuente.get("seccion_legal", "Sección Desconocida"), # <--- ¡NUEVO!
                "similitud": score
            })
            
        return resultados
    
    def buscar_cita_literal(self, pregunta_usuario, top_k=3):
        """Busca el artículo exacto filtrando directamente por el metadato 'seccion_legal'"""
        import re
        print(f"\n[RUTA 4] Analizando petición: '{pregunta_usuario}'")
        
        # 1. Extraer el número que pide el usuario
        match_numero = re.search(r"(?:art[íi]culo|art\.)\s*(\d+)", pregunta_usuario.lower())
        num_objetivo = match_numero.group(1) if match_numero else None
        
        # Si el usuario no ha puesto un número claro, hacemos fallback a la búsqueda semántica
        if not num_objetivo:
            print("[RUTA 4] No se detectó número, usando búsqueda semántica...")
            resultados_semanticos = self.buscar_similitud(pregunta_usuario, top_k=top_k)
            return resultados_semanticos, False
            
        print(f"[RUTA 4] El usuario quiere el Artículo {num_objetivo}. Filtrando directamente en metadatos...")
        
        # 2. PARA BÚSQUEDA DIRECTA POR METADATO
        consulta_exacta = {
            "size": top_k,
            "query": {
                "bool": {
                    "should": [
                        { "match_phrase": { "seccion_legal": f"Artículo {num_objetivo}" } },
                        { "match_phrase": { "seccion_legal": f"Articulo {num_objetivo}" } },
                        { "match_phrase": { "seccion_legal": f"Art. {num_objetivo}" } },
                        { "match_phrase": { "seccion_legal": f"Art {num_objetivo}" } }
                    ],
                    "minimum_should_match": 1
                }
            },
            "sort": [
                {"orden_chunk": {"order": "asc"}}
            ],
            "_source": ["texto_chunk", "pagina_inicio", "pagina_fin", "seccion_legal", "orden_chunk"]
        }
        
        respuesta = self.client.search(index=self.index_name, body=consulta_exacta)
        
        # 3. Lógica de detección de artículo largo
        total_fragmentos_articulo = respuesta["hits"]["total"]["value"]
        
        if total_fragmentos_articulo == 0:
            print("[RUTA 4] Error: No se encontró el artículo en la base de datos.")
            return [], False
            
        es_muy_largo = total_fragmentos_articulo > top_k
        
        resultados = []
        for hit in respuesta["hits"]["hits"]:
            fuente = hit["_source"]
            resultados.append({
                "texto": fuente["texto_chunk"],
                "pagina_inicio": fuente["pagina_inicio"],
                "pagina_fin": fuente["pagina_fin"],
                "seccion_legal": fuente.get("seccion_legal", "Sección Desconocida"),
                "orden_chunk": fuente.get("orden_chunk", 0)
            })
            
        return resultados, es_muy_largo
# Bloque de prueba
# Bloque de prueba
if __name__ == "__main__":
    gestor = GestorOpenSearch()
    
    # Suponiendo que ya indexaste un convenio antes a través de tu chatbot...
    pregunta = "¿Cuántos días de vacaciones me corresponden?"
    
    resultados = gestor.buscar_similitud(pregunta, top_k=3)
    
    print("\n" + "="*50)
    print(f"RESULTADOS PARA: {pregunta}")
    print("="*50)
    
    for i, res in enumerate(resultados):
        print(f"\n--- RESULTADO {i+1} (Similitud: {res['similitud']:.4f}) ---")
        print(f"Páginas: {res['pagina_inicio']} - {res['pagina_fin']}")
        print(f"Texto: {res['texto'][:200]}...") # Imprimimos solo los primeros 200 caracteres
'''if __name__ == "__main__":
    gestor = GestorOpenSearch()
    gestor.inicializar_indice()
    print("El gestor de OpenSearch está listo para ser importado.")'''