"""LAN networking: discovery, framing, host server, client.

Hard rule: nothing in this package may import Kivy. Sockets run on background
threads and hand decoded dict messages to callers through ``queue.Queue``;
the Kivy layer drains those queues on the main thread.
"""
