from app.api import collections, documents, health

routers = [health.router, collections.router, documents.router]
