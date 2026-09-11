from app.api import collections, documents, health, jobs

routers = [health.router, collections.router, documents.router, jobs.router]
