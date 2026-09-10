import os

# Prevent gevent from monkey-patching standard library during pytest collection and execution
os.environ["LOCUST_SKIP_MONKEY_PATCH"] = "1"
