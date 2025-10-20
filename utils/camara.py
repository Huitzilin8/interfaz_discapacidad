import requests
from requests.auth import HTTPDigestAuth

def camara_ir_a_preset(IP_camara, user, password, preset):
    try:
        auth = HTTPDigestAuth(user, password)
        
        url = f"http://{IP_camara}/ISAPI/PTZCtrl/channels/1/presets/{preset}/goto"
        
        response = requests.put(url, auth=auth, timeout=5)
        response.raise_for_status()
        
        
        print(f"Status Code: {response.status_code}")
        print(f"Finalizado")
    except requests.exceptions.HTTPError as errh:
        print(f"Error HTTP: {errh}")
        # Esto es útil para errores de autenticación (401 Unauthorized)
        print(f"Cuerpo de la respuesta: {errh.response.text}")
    except requests.exceptions.ConnectionError as errc:
        print(f"Error de Conexión: No se pudo conectar a la cámara en {IP_camara}. Verifica la IP y la red.")
    except requests.exceptions.Timeout as errt:
        print(f"Error de Timeout: La cámara no respondió a tiempo.")
    except requests.exceptions.RequestException as err:
        print(f"Ocurrió un error inesperado: {err}")
    
