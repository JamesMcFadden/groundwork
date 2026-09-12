from app.api import collections, documents, health, jobs, questions

routers = [health.router, collections.router, documents.router, jobs.router, questions.router]
