from app.api import collections, documents, health, jobs, questions

# Health stays open, since probes send no key. Every other router requires the API key.
public_routers = [health.router]
protected_routers = [collections.router, documents.router, jobs.router, questions.router]
