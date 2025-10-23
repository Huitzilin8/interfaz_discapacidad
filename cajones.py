import threading
import queue
import time
import re
from utils import camara

MAX_INTENTOS = 3

class CajonThread(threading.Thread):
    """
    Representa el hilo de ejecución para un único cajón de estacionamiento.
    Gestiona el ciclo de vida de la verificación de un vehículo.
    """
    def __init__(self, cajon_id: int, camara_lock: threading.Lock, stop_event: threading.Event, preset: int, result_queue: queue.Queue, input_queue: queue.Queue):
        """
        Inicializa el hilo del cajón.
        
        Args:
            cajon_id (int): El identificador único del cajón.
            camara_lock (threading.Lock): El lock para controlar el acceso a la cámara.
            stop_event (threading.Event): El evento para señalar la detención del hilo.
            preset (int): El preset de la cámara para este cajón.
            result_queue (threading.Queue): La cola para comunicar resultados al hilo principal.
        """
        super().__init__()
        self.cajon_id = cajon_id
        self.preset = preset
        self.intentos_inferencia = 0
        self.camara_lock = camara_lock
        self.stop_event = stop_event
        self.result_queue = result_queue
        self.input_queue = input_queue
        self.daemon = True

    def solicitar_mover_camara(self):
        print(f"[Cajón {self.cajon_id}]: Solicitando uso de la cámara...")
        self.camara_lock.acquire()
        print(f"[Cajón {self.cajon_id}]: Cámara adquirida. Moviendo a posición...")
        camara.camara_ir_a_preset(IP_camara='192.168.100.189',user='admin', password='Kalilinux363', preset=self.preset)
        
    @staticmethod
    def hay_detecciones(linea):
        match = re.search(r'\d+', linea)
        return match > 0 if match is not None else False
        

    def inferir(self):
        try:
            item = self.input_queue.get_nowait()
            ultima_data = item.get('data', None)
            print(f"[Cajon {self.cajon_id}]: {ultima_data}")
            return self.hay_detecciones(ultima_data)
        except queue.Empty:
            print(f"[ALERTA] Cola vacia")
            return False   
        except Exception as e:
            print(f"[ALERTA] [Cajón {self.cajon_id}]: Excepcion en inferencia")
            print(e)
            return False 
        

    def liberar_uso_camara(self):
        print(f"[Cajón {self.cajon_id}]: Liberando uso de la cámara.")
        self.camara_lock.release()

    def run(self):
        """
        El ciclo de vida principal del hilo. Este método se ejecuta cuando se llama a .start().
        """
        print(f"▶️  [Cajón {self.cajon_id}]: Hilo INICIADO.")
        inference_successful = False
        
        try:
            if not self.stop_event.is_set():
                self.solicitar_mover_camara()
                time.sleep(10) # este tiempo es para esperar a que la camara se ponga en lugar
                
                # Bucle para reintentar la inferencia
                while self.intentos_inferencia < MAX_INTENTOS and not self.stop_event.is_set():
                    if self.inferir():
                        inference_successful = True
                        break # Salir del bucle si la inferencia es exitosa
                    self.intentos_inferencia += 1
                    time.sleep(2)
            if self.stop_event.is_set():
                print(f"🟡  [Cajón {self.cajon_id}]: Hilo cancelado. No se reportará resultado.")
            else:
                # 2. Si no fue cancelado, pon el resultado en la cola.
                print(f"✔️  [Cajón {self.cajon_id}]: Tarea completada. Reportando resultado.")
                self.result_queue.put((self.cajon_id, inference_successful))
            
        finally:
            # Asegurarse de que el lock se libere siempre
            if self.camara_lock.locked():
                self.liberar_uso_camara()

        print(f"⏹️  [Cajón {self.cajon_id}]: Hilo TERMINADO.")