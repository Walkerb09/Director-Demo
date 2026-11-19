"""One lock for every OpenCV DNN model (face finder, cut-out, text finder, identity).
OpenCV nets are not thread-safe: the web app measures takes in a background thread while an upload is
processed in another, and two threads feeding one net at once fail with
"(-215:Assertion failed) buf.shape == m.shape in function 'forwardGraph'". Every forward pass takes this lock."""
import threading
NET_LOCK = threading.RLock()
