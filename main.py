import threading
import queue
import time
#import Jetson.GPIO as GPIO
from cajones import CajonThread
from modelo import YoloDockerThread

# --- Variables Globales y Recursos Compartidos ---

# Diccionarios para almacenar información de cajones y estado de los hilos
cajones = {}
sensores = {}
hilos_activos = {} # Clave: cajon_id, Valor: (thread_obj, stop_event), Para cajones solamente
hilo_docker = None

# Credenciales e IP de camara
camera_ip = "192.168.100.189"
user = "admin"
password = "Kalilinux364"

# Recursos compartidos
queue_inferencias = queue.Queue(maxsize=1) # Para inferencias del modelo en el docker
queue_resultados = queue.Queue() # Para los resultados de los cajones

detection_lock = threading.Lock()

camara_lock = threading.Lock()

# Para generar IDs de cajones únicos
cajon_key_counter = 0

# Conexion partes fisicas
# Fan Pin Configuration
FAN_PIN = 33
#GPIO.setmode(GPIO.BOARD)
#GPIO.setup(FAN_PIN, GPIO.OUT)
#fan_pwm = GPIO.PWM(FAN_PIN, 25000)
#fan_pwm.start(0)
duty = 0
# LED Pin Configuration
#LED_PIN = 18
#GPIO.setmode(GPIO.BOARD)
#GPIO.setup(LED_PIN, GPIO.OUT)

# --- Clases y Funciones de Simulación (para ignorar hardware real) ---

class MockSensor:
    """Una clase para simular un sensor de presencia."""
    def __init__(self, initial_state=False):
        self._active = initial_state

    def is_active(self):
        return self._active

    def set_active(self, state):
        self._active = state

# --- Funciones Principales ---

def insertar_cajon(preset: int, sensor: MockSensor):
    """Registra un nuevo cajón y su sensor asociado."""
    global cajon_key_counter
    cajones[cajon_key_counter] = preset
    sensores[cajon_key_counter] = sensor
    print(f"[MAIN]: Cajón {cajon_key_counter} registrado con preset {preset}.")
    cajon_key_counter += 1

    
def crear_hilo_para_cajon(cajon_id):
    """Crea, inicia y registra un nuevo hilo para un cajón específico."""
    if cajon_id not in hilos_activos:
        print(f"[MAIN]: Sensor del cajón {cajon_id} activado. Creando hilo...")
        stop_event = threading.Event()
        thread = CajonThread(
            cajon_id=cajon_id,
            camara_lock=camara_lock,
            stop_event=stop_event,
            preset=cajones[cajon_id],
            result_queue=queue_resultados,
            input_queue=queue_inferencias
        )
        hilos_activos[cajon_id] = (thread, stop_event)
        thread.start() # Inicia la ejecución del método run() en el nuevo hilo
    else:
        print(f"[MAIN]: Intento de crear hilo para cajón {cajon_id}, pero ya existe uno.")

def crear_hilo_para_docker():
    global hilo_docker
    print(hilo_docker)
    if hilo_docker is not None:
        print(f"[MAIN]: Creando hilo para docker...")
        hilo_docker = YoloDockerThread(
            output_queue=queue_inferencias
        )
    else:
        print(f"[MAIN]: Intento de crear hilo para docker pero ya existe uno.")

def matar_hilo_para_cajon(cajon_id):
    """Detiene de forma segura el hilo de un cajón específico."""
    if cajon_id in hilos_activos:
        print(f"[MAIN]: Sensor del cajón {cajon_id} desactivado. Deteniendo hilo...")
        thread, stop_event = hilos_activos[cajon_id]
        
        # 1. Señalar al hilo que debe detenerse
        stop_event.set()
        
        # 2. Esperar a que el hilo termine su ejecución actual (opcional pero recomendado)
        thread.join()
        
        # 3. Eliminar el hilo del registro de hilos activos
        del hilos_activos[cajon_id]
    else:
        print(f"[MAIN]: Intento de detener hilo para cajón {cajon_id}, pero no existe.")
        
def get_cpu_temp():
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return float(f.readline()) / 1000.0
        except Exception:
            return 0

def ciclo_main():
    """
    El ciclo principal que monitorea los sensores, los resultados y gestiona los hilos.
    """
    print("\n--- Iniciando Ciclo Principal de Monitoreo ---")
    start = time.time()
    try:
        while True:
            # 1. Monitorear sensores para crear/destruir hilos
            for cajon_id, sensor in sensores.items():
                
                # Caso 1: Hay un carro y NO hay un hilo activo para ese cajón
                if sensor.is_active() and cajon_id not in hilos_activos:
                    crear_hilo_para_cajon(cajon_id)
                
                # Caso 2: NO hay un carro y SÍ hay un hilo activo para ese cajón
                elif not sensor.is_active() and cajon_id in hilos_activos:
                    # --- LÓGICA CORREGIDA ---
                    print(f"✅ [MAIN]: Carro se fue del cajón {cajon_id}. Torreta APAGADA.")
                    #GPIO.output(LED_PIN, GPIO.HIGH)
                    matar_hilo_para_cajon(cajon_id)
            
            # 2. Procesar resultados de la cola
            try:
                cajon_id, exito = queue_resultados.get_nowait()
                print(f"💡 [MAIN]: Recibido resultado del Cajón {cajon_id}: {'Éxito' if exito else 'Fallo'}")
                if exito:
                    print(f"🚨 [MAIN]: Torreta ENCENDIDA por el cajón {cajon_id}.")
                    #GPIO.output(LED_PIN, GPIO.LOW) # LOW enciende el LED
                # Si no hay éxito, no hacemos nada y la luz permanece apagada
            except queue.Empty:
                pass

            # 3. Monitoreo de temperatura
            temp = get_cpu_temp()
            if temp > 50: 
                duty = 100
            elif temp > 40: 
                duty = 40
            else: 
                duty = 0
#            fan_pwm.ChangeDutyCycle(duty)

    except KeyboardInterrupt:
        print("\n--- Deteniendo el programa ---")
        for cajon_id in list(hilos_activos.keys()):
            matar_hilo_para_cajon(cajon_id)
#        GPIO.cleanup()
        print("Programa finalizado.")   

if __name__ == "__main__":
    # 1. Configuración inicial: registrar los cajones y sus sensores simulados
    crear_hilo_para_docker()
    
    insertar_cajon(preset= 1, sensor=MockSensor())
    insertar_cajon(preset= 2, sensor=MockSensor())
    
    
    # 2. Simulación de eventos
    print("\n--- Simulación de Eventos ---")
    time.sleep(2)
    print("\n>>> SIM: Un carro llega al cajón 0")
    sensores[0].set_active(True)
    
    time.sleep(2)
    print("\n>>> SIM: Otro carro llega al cajón 1")
    sensores[1].set_active(True) # El hilo del cajón 1 esperará a que el 0 libere la cámara
    
    # 3. Iniciar el ciclo principal de monitoreo (que se ejecutará indefinidamente)
    ciclo_main()