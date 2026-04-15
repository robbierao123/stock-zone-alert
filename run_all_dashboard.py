import multiprocessing
import os

def run_dashboard():
    os.system("python dashboard.py")

def run_local_ui():
    os.system("python local_dashboard.py")

if __name__ == "__main__":
    p1 = multiprocessing.Process(target=run_dashboard)
    p2 = multiprocessing.Process(target=run_local_ui)

    p1.start()
    p2.start()

    p1.join()
    p2.join()