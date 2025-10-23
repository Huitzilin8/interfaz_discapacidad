import threading
import queue
import time
from utils import camara  
import cv2     # <-- NUEVO: Necesitamos OpenCV para capturar
import os      # <-- NUEVO: Para manejar rutas de archivos

class CajonThread(threading.Thread):
    """
    Representa el hilo de ejecución para un único cajón.
    Su trabajo es:
    1. Mover la cámara al preset.
    2. Capturar una imagen.
    3. Enviar el trabajo (cajon_id, ruta_imagen) a la cola de inferencia.
    4. Terminar.
    """
    def __init__(self, 
                 cajon_id: int, 
                 camara_lock: threading.Lock, 
                 stop_event: threading.Event, 
                 preset: int,
                 inference_queue: queue.Queue, # <-- NUEVO: Para enviar trabajos
                 camera_ip: str,               # <-- NUEVO: Credenciales
                 user: str,
                 password: str,
                 rtsp_stream: str):            # <-- NUEVO: RTSP path
        """
        Inicializa el hilo del cajón.
        """
        super().__init__()
        self.cajon_id = cajon_id
        self.preset = preset
        self.camara_lock = camara_lock
        self.stop_event = stop_event
        self.inference_queue = inference_queue # La cola de trabajos para YOLO
        self.daemon = True
        
        # Credenciales e info de la cámara
        self.camera_ip = camera_ip
        self.user = user
        self.password = password
        self.rtsp_url = f"rtsp://{user}:{password}@{camera_ip}:554/{rtsp_stream}"

        # Directorio para guardar capturas
        # Asegúrate que este dir exista y sea escribible por tu script
        self.capture_dir = "/tmp/capturas" 
        os.makedirs(self.capture_dir, exist_ok=True)

    def solicitar_mover_camara(self):
        """Adquiere el lock y mueve la cámara usando el módulo camara.py"""
        print(f"[Cajón {self.cajon_id}]: Solicitando uso de la cámara...")
        self.camara_lock.acquire()
        print(f"[Cajón {self.cajon_id}]: Cámara adquirida. Moviendo a preset {self.preset}...")
        
        # Usamos las credenciales pasadas en el constructor
        camara.camara_ir_a_preset(
            IP_camara=self.camera_ip,
            user=self.user,
            password=self.password, 
            preset=self.preset
        )

    def _capture_and_send_job(self) -> bool:
        """
        NUEVA FUNCIÓN: Captura un frame de la cámara, lo guarda, 
        y pone el trabajo en la cola de inferencia.
        """
        print(f"[Cajón {self.cajon_id}]: Conectando a RTSP ({self.rtsp_url}) para captura...")
        cap = cv2.VideoCapture(self.rtsp_url)
        
        if not cap.isOpened():
            print(f"[Cajón {self.cajon_id}]: ERROR, no se pudo abrir el stream RTSP.")
            cap.release()
            return False
            
        ret, frame = cap.read()
        cap.release()
        
        if ret:
            # Usar un nombre de archivo único
            timestamp = int(time.time() * 1000)
            image_path = os.path.join(self.capture_dir, f"cajon_{self.cajon_id}_{timestamp}.jpg")
            
            # ¡IMPORTANTE! Asegúrate que la ruta que guardas aquí (ej: /tmp/capturas/...)
            # sea la MISMA ruta que mapeaste en el volumen de Docker en modelo.py
            # (ej: -v /tmp/capturas:/tmp/capturas)
            cv2.imwrite(image_path, frame)
            
            print(f"[Cajón {self.cajon_id}]: Imagen guardada en {image_path}")
            
            # Enviar el trabajo a la cola de inferencia
            # El trabajo es una tupla: (id_cajon, ruta_de_la_imagen)
            self.inference_queue.put((self.cajon_id, image_path))
            print(f"[Cajón {self.cajon_id}]: Trabajo enviado a la cola de inferencia.")
            return True
        else:
            print(f"[Cajón {self.cajon_id}]: ERROR, no se pudo capturar el frame.")
            return False

    def liberar_uso_camara(self):
        """Libera el lock de la cámara."""
        print(f"[Cajón {self.cajon_id}]: Liberando uso de la cámara.")
        self.camara_lock.release()

    def run(self):
        """
        El ciclo de vida principal del hilo.
        Este método se ejecuta UNA VEZ y termina.
        """
        print(f"▶️  [Cajón {self.cajon_id}]: Hilo INICIADO.")
        
        try:
            # 1. Mover la cámara (esto ya adquiere el lock)
            if self.stop_event.is_set(): return # Salir si nos cancelaron
            self.solicitar_mover_camara()
            
            print(f"[Cajón {self.cajon_id}]: Esperando 10s a que la cámara llegue...")
            # Esperar a que la cámara termine de moverse
            # time.sleep(10) es una forma.
            # self.stop_event.wait(10) es mejor, porque se interrumpe si nos cancelan
            tiempo_espera_camara = 10 
            if self.stop_event.wait(timeout=tiempo_espera_camara):
                print(f"[Cajón {self.cajon_id}]: Hilo detenido durante espera de cámara.")
                return # Salir si nos cancelaron
                
            # 2. Capturar imagen y enviar trabajo
            print(f"[Cajón {self.cajon_id}]: Capturando imagen...")
            if not self.stop_event.is_set():
                self._capture_and_send_job()
            
            # Este hilo NO pone nada en result_queue.
            # El YoloDockerThread lo hará.
        
        except Exception as e:
            print(f"💥 [Cajón {self.cajon_id}]: ERROR INESPERADO en run(): {e}")
        
        finally:
            # 3. Asegurarse de que el lock se libere siempre
            if self.camara_lock.locked():
                self.liberar_uso_camara()

        print(f"⏹️  [Cajón {self.cajon_id}]: Hilo TERMINADO.")