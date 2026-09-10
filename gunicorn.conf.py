import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
worker_class = "gthread"
threads = 4
timeout = 60
graceful_timeout = 45
keepalive = 5
# Do not log Authorization headers, request bodies, or report IDs in URLs.
accesslog = None
errorlog = "-"
loglevel = "info"
limit_request_line = 2048
limit_request_fields = 40
limit_request_field_size = 8190
