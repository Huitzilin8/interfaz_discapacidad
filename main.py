import threading
import queue
import time
import paho.mqtt.client as mqtt
#import Jetson.GPIO as GPIO
from cajones import CajonThread
from modelo import YoloDockerThread

# --- Variables Globales y Recursos Compartidos ---

# Diccionarios para almacenar información de cajones y estado de los hilos
cajones = {}
sensores = {}
hilos_activos = {} # Clave: cajon_id, Valor: (thread_obj, stop_event), Para cajones solamente
hilo_dokcer = None

# Credenciales e IP de camara
camera_ip = "192.168.100.72"
user = "admin"
password = "Kalilinux364"

# Recursos compartidos

queue_inferencias = queue.Queue(maxsize=1)
queue_resultados = queue.Queue()

detection_lock = threading.Lock()

camara_lock = threading.Lock()

# Para generar IDs de cajones únicos
cajon_key_counter = 0

# --- Configuración MQTT ---
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
# Usamos un topic base. Nos suscribiremos a 'sensores/cajon/+/estado'
# El '+' es un comodín para cualquier ID de cajón.
MQTT_TOPIC_BASE = "sensores/cajon"

# Conexion partes fisicas
# Fan Pin Configuration
FAN_PIN = 33
#GPIO.setmode(GPIO.BOARD)
#GPIO.setup(FAN_PIN, GPIO.OUT)
#fan_pwm = GPIO.PWM(FAN_PIN, 25000)
#fan_pwm.start(0)
duty = 0
# LED Pin Configuration
LED_PIN = 18
#GPIO.setmode(GPIO.BOARD)
#GPIO.setup(LED_PIN, GPIO.OUT)

# --- Clases y Funciones de Simulación (para ignorar hardware real) ---
def on_connect(client, userdata, flags, rc):
    """Callback que se ejecuta cuando nos conectamos al broker."""
    if rc == 0:
        print(f"[MQTT]: Conectado exitosamente al broker {MQTT_BROKER}")
        # Una vez conectados, (re)suscribimos a los topics de los cajones ya registrados
        for cajon_id in cajones.keys():
            topic = f"{MQTT_TOPIC_BASE}/{cajon_id}/estado"
            client.subscribe(topic)
            print(f"[MQTT]: Suscrito a {topic}")
    else:
        print(f"[MQTT]: Fallo en la conexión, código de retorno: {rc}")

def on_message(client, userdata, msg):
    """Callback que se ejecuta cuando llega un mensaje."""
    print(f"[MQTT]: Mensaje recibido! Topic: {msg.topic} | Payload: {msg.payload.decode()}")
    
    try:
        # 1. Extraer el ID del cajón del topic
        # El topic será "sensores/cajon/0/estado"
        parts = msg.topic.split('/')
        cajon_id = int(parts[2]) # Obtenemos el '0'
        
        # 2. Decodificar el mensaje (payload)
        payload = msg.payload.decode().strip().upper()
        
        # 3. Actualizar el estado en nuestro diccionario 'sensores'
        if cajon_id in sensores:
            if payload == "ON":
                sensores[cajon_id] = True
                print(f"[MQTT]: Estado del Cajón {cajon_id} actualizado a: {sensores[cajon_id]}")
            elif payload == "OFF":
                sensores[cajon_id] = False
                print(f"[MQTT]: Estado del Cajón {cajon_id} actualizado a: {sensores[cajon_id]}")
            else:
                print(f"[MQTT]: Payload '{payload}' no reconocido. Usar 'ON' o 'OFF'.")
        else:
            print(f"[MQTT]: ID de cajón {cajon_id} no reconocido.")
            
    except Exception as e:
        print(f"[MQTT]: Error procesando mensaje: {e}")

# --- Funciones Principales ---

def insertar_cajon(preset: int):
    """Registra un nuevo cajón y su sensor asociado."""
    global cajon_key_counter
    cajones[cajon_key_counter] = preset
    # --- Nos suscribimos al topic MQTT para este nuevo cajón ---
    topic = f"{MQTT_TOPIC_BASE}/{cajon_key_counter}/estado"
    client_mqtt.subscribe(topic)
    print(f"[MQTT]: Suscrito a {topic}")
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
            result_queue=queue_resultados
        )
        hilos_activos[cajon_id] = (thread, stop_event)
        thread.start() # Inicia la ejecución del método run() en el nuevo hilo
    else:
        print(f"[MAIN]: Intento de crear hilo para cajón {cajon_id}, pero ya existe uno.")

def crear_hilo_para_docker():
    if hilo_dokcer is not None:
        print(f"[MAIN]: Creando hilo para docker...")
        hilo_dokcer = YoloDockerThread(
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
            if temp > 50: duty = 100
            elif temp > 40: duty = 40
            else: duty = 0
            #fan_pwm.ChangeDutyCycle(duty)
            time.sleep(0.5) # Un pequeño sleep para no consumir 100% de CPU en este ciclo

    except KeyboardInterrupt:
        print("\n--- Deteniendo el programa ---")
        for cajon_id in list(hilos_activos.keys()):
            matar_hilo_para_cajon(cajon_id)
        #GPIO.cleanup()
        
        client_mqtt.loop_stop() # --- NUEVO: Detener el hilo de MQTT ---
        client_mqtt.disconnect()
        #GPIO.cleanup()
        print("Programa finalizado.")

if __name__ == "__main__":

    # --- Configuración e inicio del cliente MQTT ---
    client_mqtt = mqtt.Client()
    client_mqtt.on_connect = on_connect
    client_mqtt.on_message = on_message

    try:
        client_mqtt.connect(MQTT_BROKER, MQTT_PORT, 60)
    except Exception as e:
        print(f"[MQTT]: No se pudo conectar al broker. ¿Hay conexión a internet? Error: {e}")
        exit()

    # client.loop_start() INICIA UN HILO EN SEGUNDO PLANO
    # para manejar los callbacks de MQTT (conexión y mensajes).
    # No bloquea el resto del script.
    client_mqtt.loop_start()


    # 1. Configuración inicial: registrar los cajones y sus sensores simulados
    insertar_cajon(preset= 1)
    insertar_cajon(preset= 2)
   
    # La simulación ahora es externa, desde tu app MQTT.
    print("\n--- Esperando eventos MQTT ---")

    # 3. Iniciar el ciclo principal de monitoreo (que se ejecutará indefinidamente)
    ciclo_main()