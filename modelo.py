import threading
import subprocess
import queue
import time
import os
import stat
from typing import Callable, Optional

class YoloDockerThread(threading.Thread):
    def __init__(self,
                 input_queue: queue.Queue, 
                 output_queue: queue.Queue, 
                 error_callback: Optional[Callable] = None,
                 docker_image: str = "ultralytics/onvif-persist:final-20251010",
                 container_name_base: str = "ultralytics_trt_worker"):
        """
        Thread de trabajo que espera trabajos de inferencia en una cola de entrada.
        
        Args:
            input_queue: Cola de donde lee trabajos (ej: (cajon_id, "/path/to/img.jpg"))
            output_queue: Cola donde pone resultados (ej: (cajon_id, True))
            error_callback: Opcional
            docker_image: Imagen de Docker
            container_name_base: Nombre base para el contenedor
        """
        super().__init__(daemon=True)
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.error_callback = error_callback
        self.docker_image = docker_image
        self.container_name = f"{container_name_base}_{os.getpid()}" # Nombre único
        self._stop_event = threading.Event()
        
    def _remove_existing_container(self):
        """Remove existing container if it exists."""
        try:
            subprocess.run(
                ['sudo', 'docker', 'rm', self.container_name],
                capture_output=True,
                timeout=10
            )
        except Exception:
            pass  # Container might not exist, which is fine
    
    def _ensure_venv_permissions(self):
        """Ensure venv directory has proper permissions."""
        venv_path = '/media/nvidia/jetson_sd/ultralytics/venvs/onvif_env'
        if os.path.exists(venv_path):
            try:
                # Make activate script executable
                activate_script = os.path.join(venv_path, 'bin', 'activate')
                if os.path.exists(activate_script):
                    os.chmod(activate_script, 
                            stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR |
                            stat.S_IRGRP | stat.S_IXGRP | 
                            stat.S_IROTH | stat.S_IXOTH)
            except Exception as e:
                print(f"Warning: Could not set permissions on venv: {e}")
    
    def _parse_yolo_output(self, stdout: str) -> bool:
        """
        Revisa el stdout de YOLO para ver si hubo una detección exitosa.
        """
        print(f"[YOLO-WORKER] Salida de YOLO:\n{stdout}")
        # Ejemplo: lo que el modelo detecta
        if "1 carro" in stdout: # O "1 person", "1 object", etc.
            print("[YOLO-WORKER] ¡Detección exitosa encontrada!")
            return True
        
        # Ejemplo 2: si solo te importa que haya detectado ALGO
        # (La línea "image 1/1..." siempre aparece si procesa)
        for line in stdout.splitlines():
            if line.startswith("image 1/1") and "Done" in line:
                if "no objects detected" in line:
                    continue # Esto no es un éxito
                if "1 car" in line or "1 person" in line: # AJUSTAR
                    print("[YOLO-WORKER] ¡Detección exitosa encontrada!")
                    return True

        print("[YOLO-WORKER] No se encontró detección de interés.")
        return False
    
    def run(self):
        """
        Ciclo principal del trabajador: esperar trabajo, ejecutar Docker, poner resultado.
        """
        
        # Ensure proper permissions before starting
        self._ensure_venv_permissions()
        
        while not self._stop_event.is_set():
            try:
                # 1. Esperar un trabajo de la cola de entrada
                # El trabajo es una tupla: (cajon_id, ruta_de_la_imagen)
                cajon_id, image_path = self.input_queue.get(timeout=1)
                
                print(f"[YOLO-WORKER]: Recibido trabajo para Cajón {cajon_id} en imagen {image_path}")

                # 2. Limpiar contenedor anterior (por si acaso)
                self._remove_existing_container()

                # 3. Construir el comando de Docker DINÁMICAMENTE
                bash_cmd = (
                    "bash -lc '"
                    "source /ultralytics/venvs/onvif_env/bin/activate && "
                    "yolo predict "
                    "model=far_signals3_elmejor.engine "
                    f"source={image_path} "  # <-- ¡Usamos la imagen del trabajo!
                    #"source=rtsp://admin:Kalilinux363@192.168.100.72:554/stream "
                    "imgsz=640,640 "
                    "conf=0.30"
                    "save=False " # No necesitamos guardar la imagen
                    "'"
                )
                
                docker_cmd = [
                    'sudo', 'docker', 'run',
                    '--runtime=nvidia',
                    '--ipc=host',
                    '--network', 'host',
                    '--name', self.container_name,
                    '-v', '/media/nvidia/jetson_sd/ultralytics:/ultralytics',
                    # ¡IMPORTANTE! Mapear el directorio donde se guardan las capturas
                    # Asumo que las capturas se guardan en /tmp
                    '-v', '/tmp:/tmp',
                    '--device', '/dev/bus/usb:/dev/bus/usb',
                    '--device-cgroup-rule=c 81:* rmw',
                    '--device-cgroup-rule=c 189:* rmw',
                    self.docker_image,
                    'bash', '-c', bash_cmd
                ]
                
                # 4. Ejecutar el comando y ESPERAR a que termine
                process = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30  # Timeout de 30 segundos
                )

                # 5. Analizar el resultado
                exito = False
                if process.returncode == 0:
                    exito = self._parse_yolo_output(process.stdout)
                else:
                    print(f"[YOLO-WORKER] Error de Docker (stderr): {process.stderr}")

                # 6. Poner el resultado en la cola de salida
                self.output_queue.put((cajon_id, exito))
                self.input_queue.task_done()
        
            except queue.Empty:
                # No hay trabajos, solo volvemos a intertar (comportamiento normal)
                continue
            except Exception as e:
                print(f"[YOLO-WORKER] Excepción en el hilo: {e}")
                if self.error_callback:
                    self.error_callback(str(e))
                # Si un trabajo falla, es mejor notificarlo como fallo
                # (Necesitaríamos guardar cajon_id fuera del try para esto)
            finally:
                # Limpiar el contenedor después de cada ejecución
                self._remove_existing_container()

    
    def stop(self):
        """Stop the thread and kill the Docker process."""
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        # Clean up the container after stopping
        self._remove_existing_container()


# Example usage
if __name__ == "__main__":
    
    print("Este script está diseñado para ser importado, no ejecutado directamente.")
    print("Iniciando prueba de trabajador...")
    
    in_q = queue.Queue()
    out_q = queue.Queue()
    
    worker = YoloDockerThread(in_q, out_q)
    worker.start()
    
    print("Enviando trabajo de prueba (requiere imagen en /tmp/test.jpg)...")
    # Para probar: pon una imagen en /tmp/test.jpg
    # sudo cp tu_imagen.jpg /tmp/test.jpg
    in_q.put((99, "/tmp/test.jpg"))
    
    try:
        result = out_q.get(timeout=45)
        print(f"Resultado recibido: {result}")
    except queue.Empty:
        print("Prueba fallida: No se recibió respuesta del trabajador.")
    
    worker.stop()
    worker.join()
    print("Prueba finalizada.")