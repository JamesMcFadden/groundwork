from app.api import collections, health

routers = [health.router, collections.router]
