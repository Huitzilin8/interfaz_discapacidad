import threading
import subprocess
import queue
import time
import os
import stat
from typing import Callable, Optional

class YoloDockerThread(threading.Thread):
    def __init__(self, output_queue: queue.Queue, 
                 error_callback: Optional[Callable] = None,
                 docker_image: str = "ultralytics/onvif-persist:final-20251010",
                 container_name: str = "ultralytics_trt_v2"):
        """
        Thread to run YOLO prediction in Docker and stream output.
        
        Args:
            output_queue: Queue to send output lines to other threads
            error_callback: Optional callback function for errors
            docker_image: Docker image to use
            container_name: Name for the Docker container
        """
        super().__init__(daemon=True)
        self.output_queue = output_queue
        self.error_callback = error_callback
        self.docker_image = docker_image
        self.container_name = container_name
        self.process = None
        self._stop_event = threading.Event()
        
    def _remove_existing_container(self):
        """Remove existing container if it exists."""
        try:
            subprocess.run(
                ['sudo', 'docker', 'rm', self.container_name],
                timeout=10
            )
            print("[DOCKER] Docker removido con exito")
        except Exception as e:
            print("[Docker] Docker no fue removido")
            print(e)# Container might not exist, which is fine
    
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
                
    @staticmethod
    def safe_put(q: queue.Queue, item):
        if q.full():
            try:
                q.get_nowait()
            except queue.Empty:
                pass
        q.put_nowait(item)
    
    def run(self):
        """Run the Docker container and stream output."""
        # Clean up any existing container first
        self._remove_existing_container()
        
        # Ensure proper permissions before starting
        self._ensure_venv_permissions()
        
        # Command that activates venv and runs YOLO
        # Using bash -l to load login shell environment
        bash_cmd = (
            "bash -lc '"
            "source /ultralytics/venvs/onvif_env/bin/activate && "
            "cd /models"
            "yolo predict "
            "model=far_signals3_elmejor.engine "
            "source=rtsp://admin:Kalilinux363@192.168.100.72:554/stream "
            "imgsz=640,640 "
            "conf=0.30"
            "'"
        )
        
        docker_cmd = [
            'sudo', 'docker', 'run',
            '--runtime=nvidia',
            '--ipc=host',
            '--network', 'host',
            '--name', self.container_name,
            '-v', '/media/nvidia/jetson_sd/ultralytics:/ultralytics',
            '--device', '/dev/bus/usb:/dev/bus/usb',
            '--device-cgroup-rule=c 81:* rmw',
            '--device-cgroup-rule=c 189:* rmw',
            self.docker_image,
            'bash', '-c', bash_cmd
        ]
        
        try:
            self.process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )
            
            # Read stdout in real-time
            for line in iter(self.process.stdout.readline, ''):
                if self._stop_event.is_set():
                    break
                    
                line = line.rstrip()
                if line:
                    # Send output to queue for other threads
                    self.safe_put(self.output_queue,{
                        'type': 'stdout',
                        'data': line,
                        'timestamp': time.time()
                    })
            
            # Wait for process to complete
            self.process.wait()
            
            # Check for errors
            if self.process.returncode != 0:
                stderr = self.process.stderr.read()
                error_msg = f"Process exited with code {self.process.returncode}: {stderr}"
                self.safe_put(self.output_queue,{
                    'type': 'error',
                    'data': error_msg,
                    'timestamp': time.time()
                })
                if self.error_callback:
                    self.error_callback(error_msg)
                    
        except Exception as e:
            error_msg = f"Exception in YOLO thread: {str(e)}"
            self.safe_put(self.output_queue,{
                'type': 'error',
                'data': error_msg,
                'timestamp': time.time()
            })
            if self.error_callback:
                self.error_callback(error_msg)
        finally:
            self.safe_put(self.output_queue,{
                'type': 'stopped',
                'data': 'Thread stopped',
                'timestamp': time.time()
            })
    
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


if __name__ == "__main__":
    
    # Create a queue for inter-thread communication
    output_queue = queue.Queue()
    
    def handle_error(error_msg):
        print(f"ERROR: {error_msg}")
    
    # Create and start the YOLO thread
    yolo_thread = YoloDockerThread(
        output_queue=output_queue,
        error_callback=handle_error
    )
    yolo_thread.start()
    
    # Consumer thread example - reads from queue
    def consumer_thread():
        while True:
            try:
                msg = output_queue.get(timeout=1)
                if msg['type'] == 'stdout':
                    print(f"[YOLO OUTPUT] {msg['data']}")
                elif msg['type'] == 'error':
                    print(f"[ERROR] {msg['data']}")
                elif msg['type'] == 'stopped':
                    print(f"[INFO] {msg['data']}")
                    break
                output_queue.task_done()
            except queue.Empty:
                continue
    
    # Start consumer thread
    consumer = threading.Thread(target=consumer_thread, daemon=True)
    consumer.start()
    
    # Run for some time or until interrupted
    try:
        yolo_thread.join()  # Wait for YOLO thread to finish
    except KeyboardInterrupt:
        print("\nStopping...")
        yolo_thread.stop()
        yolo_thread.join(timeout=10)