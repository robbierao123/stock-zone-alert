import multiprocessing
import sys
import subprocess

def run_dashboard():
    # subprocess.run([sys.executable, "ZONE-ALERT/dashboard.py"])
    subprocess.run([sys.executable, "ZONE-ALERT/dashboard_modified.py"])

def run_local_ui():
    subprocess.run([sys.executable, "ZONE-ALERT/local_dashboard.py"])

if __name__ == "__main__":
    p1 = multiprocessing.Process(target=run_dashboard)
    p2 = multiprocessing.Process(target=run_local_ui)

    p1.start()
    p2.start()

    p1.join()
    p2.join()