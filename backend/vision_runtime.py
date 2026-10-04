"""Serialize Apple MPS work across independent detection and recognition queues."""
import threading
mps_lock = threading.RLock()
